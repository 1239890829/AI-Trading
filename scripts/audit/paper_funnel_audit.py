#!/usr/bin/env python3
"""Offline funnel diagnostics; never imports the app or changes business facts.

Counts describe saved records, not data authenticity, investment quality or
counterfactual fills. A recorded pass with no linked consumer needs review; it
does not prove that an order should have filled. Output is a local diagnostic,
not a second task, selection, permission or trading ledger.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import time

BJ = timezone(timedelta(hours=8))
MAX_JSON_BYTES = 2_500_000
COLUMNS = {
    "daily_pick_set": "date items meta created_at",
    "opportunity_decision_run": "run_id trade_date scenario",
    "opportunity_decision_snapshot": "id snapshot_id run_id trade_date as_of created_at scenario stage symbol decision strategy_version feature_version rank data_state evidence",
    "alert_event": "id symbol triggered_at snapshot",
    "buy_point_consumption": "event_key event_id consumer payload state reason attempts expires_at_ms updated_at_ms",
    "notification_outbox": "id event_id channel state reason created_at_ms",
    "paper_order": "id scope side status created_at updated_at",
    "paper_action_receipt": "scope request_id result created_at",
    "paper_reset_backup": "scope restored created_at",
}


def _text(value, limit=300):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    value = str(value)[:limit]
    value = re.sub(r"https?://\S+|/(?:Users|home)/\S+", "[redacted]", value)
    value = re.sub(r"\b(?:sk-|ghp_|hf_|apikey_)[A-Za-z0-9_-]{12,}", "[redacted]", value)
    return re.sub(r"(?i)(?:token|api[_-]?key|password|secret)\s*[:=]\s*\S+", "[redacted]", value)


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _clock(value, naive_zone=timezone.utc):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            return result.replace(tzinfo=naive_zone) if naive_zone is not None else None
        return result
    except (ValueError, TypeError):
        return None


def _count(values):
    return dict(sorted(Counter(str(value) if value is not None else "unknown" for value in values).items()))


def _unique(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def audit(database, *, start_date=None, end_date=None, now=None, position_plans=None):
    """Read one SQLite snapshot with explicit missing/old-contract boundaries."""
    if ((start_date is not None and not _day(start_date)) or
            (end_date is not None and not _day(end_date)) or
            (start_date and end_date and start_date > end_date)):
        raise ValueError("invalid date window")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("audit clock requires timezone")
    report = {"version": 1, "status": "complete", "captured_at": now.isoformat(),
        "window": {"start_date": start_date, "end_date": end_date},
        "side_effects": {"model_calls": 0, "provider_calls": 0, "business_writes": 0},
        "coverage": {}, "unknowns": [], "denominators": {}, "daily": {}, "intraday": {},
        "notification": {}, "consumers": {}, "orders": {"main_filled_count": None},
        "reset": {}, "messages": {}, "outbox": {}, "plans": {"state": "not_requested"},
        "conclusion": {"state": "unknown_records_prevent_global_claim"},
        "limits": ["Saved records do not establish source authenticity or a strategy win rate.",
            "Daily saved rows do not contain every historical generation.",
            "First blockers are short-circuit observations; later conditions were not necessarily evaluated.",
            "Independent gaps use recorded inputs only, not reconstructed execution or hindsight.",
            "A missing consumer link is not proof of a tradable or missed fill."]}

    def unknown(code, table, reference=None):
        report["unknowns"].append({"code": code, "table": table, "reference": _text(reference, 128)})

    def in_window(day):
        return bool(_day(day) and (not start_date or day >= start_date) and (not end_date or day <= end_date))

    def parse(raw, table, reference, kind=dict):
        try:
            if not isinstance(raw, str) or len(raw.encode()) > MAX_JSON_BYTES:
                raise ValueError
            value = json.loads(raw, object_pairs_hook=_unique,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
            if not isinstance(value, kind):
                raise ValueError
            return value
        except (ValueError, TypeError, UnicodeError):
            unknown("invalid_json", table, reference)
            return None

    path = Path(database).resolve()
    if not path.is_file():
        unknown("database_unavailable", "database")
        report["status"] = "unknown"
        return report
    connection = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=1000")
        connection.execute("BEGIN")
        connection.row_factory = sqlite3.Row
    except sqlite3.Error:
        if connection is not None:
            connection.close()
        unknown("database_unreadable", "database")
        report["status"] = "unknown"
        return report

    def rows(table, where="", params=()):
        names = COLUMNS[table].split()
        try:
            actual = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if not actual or not set(names) <= actual:
                code = "table_missing" if not actual else "schema_unknown"
                report["coverage"][table] = {"state": "unknown", "reason": code}
                unknown(code, table)
                return None
            deadline = time.monotonic() + 10
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            result = [dict(row) for row in connection.execute(
                f'SELECT {",".join(names)} FROM "{table}"' + where, params)]
            report["coverage"][table] = {"state": "read", "rows_read": len(result)}
            return result
        except sqlite3.Error:
            report["coverage"][table] = {"state": "unknown", "reason": "read_failed"}
            unknown("read_failed", table)
            return None
        finally:
            connection.set_progress_handler(None, 0)

    try:
        data = {table: rows(table, " WHERE stage IN ('rank','notification')"
                            if table == "opportunity_decision_snapshot" else "") for table in COLUMNS}
    finally:
        connection.close()

    def time_consistent(row, table):
        created = _clock(row.get("created_at"))
        as_of = _clock(row.get("as_of"), BJ)
        code = None
        if created is None or as_of is None or not _day(row.get("trade_date")):
            code = "original_clock_unknown"
        elif created > now + timedelta(seconds=5) or as_of > now + timedelta(seconds=5):
            code = "created_in_future" if created > now + timedelta(seconds=5) else "source_in_future"
        elif as_of.astimezone(BJ).date().isoformat() != row["trade_date"]:
            code = "source_trade_day_mismatch"
        elif created.astimezone(BJ).date().isoformat() != row["trade_date"]:
            code = "archive_trade_day_mismatch"
        elif as_of > created + timedelta(seconds=5):
            code = "source_after_archive_clock"
        if code:
            unknown(code, table, row.get("snapshot_id"))
        return code is None

    daily_pairs, generated_pairs = set(), set()
    daily_records, entry_states = [], []
    for row in data["daily_pick_set"] or []:
        if not _day(row["date"]):
            unknown("trade_day_unknown", "daily_pick_set")
        if not in_window(row["date"]):
            continue
        items = parse(row["items"], "daily_pick_set", row["date"], list)
        meta = parse(row["meta"], "daily_pick_set", row["date"])
        if items is None or meta is None:
            daily_records.append({"trade_date": row["date"], "state": "unknown", "item_count": None})
            continue
        created, generated = _clock(row["created_at"]), _clock(meta.get("generated_at"), None)
        normal = bool(created and generated and created <= now + timedelta(seconds=5)
                      and generated <= now + timedelta(seconds=5)
                      and generated <= created + timedelta(seconds=5)
                      and generated.astimezone(BJ).date().isoformat() == row["date"])
        if not normal:
            unknown("created_in_future" if created and created > now + timedelta(seconds=5)
                    else "original_generation_unknown", "daily_pick_set", row["date"])
        gate = meta.get("gate") if isinstance(meta.get("gate"), dict) else {}
        entries = meta.get("selection_entries") if isinstance(meta.get("selection_entries"), dict) else {}
        selected = []
        for item in items:
            if not isinstance(item, dict) or not re.fullmatch(r"\d{6}", str(item.get("symbol", ""))):
                unknown("selection_identity_unknown", "daily_pick_set", row["date"])
                continue
            pair = (row["date"], item["symbol"])
            daily_pairs.add(pair)
            if normal:
                generated_pairs.add(pair)
            entry = entries.get(item["symbol"])
            entry = entry if isinstance(entry, dict) else {}
            entry_states.append(entry.get("state", "unknown"))
            conf = item.get("confidence") if isinstance(item.get("confidence"), dict) else {}
            selected.append({"symbol": item["symbol"], "source": "daily", "source_version": _text(meta.get("selection_version"), 128),
                "confidence_tier": _text(conf.get("tier")), "observation_only": item.get("observation_only") is True,
                "follow_state": _text(item.get("follow_state")), "buy_range_present": isinstance(item.get("buy_range"), dict),
                "first_entry_state": _text(entry.get("state")) or "unknown", "selected_at": _text(entry.get("selected_at")),
                "generation_state": "time_consistent_not_authenticity_proof" if normal else "provenance_unknown"})
        daily_records.append({"trade_date": row["date"], "source": "daily", "created_at": _text(row["created_at"]),
            "generated_at": _text(meta.get("generated_at")), "state": "time_consistent" if normal else "unknown",
            "item_count": len(items), "gate_stand_aside": gate.get("stand_aside"),
            "gate_level": _text(gate.get("level")), "items": selected})
    report["daily"] = {"saved_dates": len(daily_records) if data["daily_pick_set"] is not None else None,
        "raw_day_symbols": len(daily_pairs) if data["daily_pick_set"] is not None else None,
        "normal_generation_day_symbols": len(generated_pairs) if data["daily_pick_set"] is not None else None,
        "first_entry_states": _count(entry_states), "records": daily_records}

    for row in data["opportunity_decision_snapshot"] or []:
        if not _day(row["trade_date"]):
            unknown("trade_day_unknown", "opportunity_decision_snapshot", row["snapshot_id"])
    snapshots = [row for row in data["opportunity_decision_snapshot"] or [] if in_window(row["trade_date"])]
    runs = {row["run_id"]: row for row in data["opportunity_decision_run"] or []}
    rank_rows, first_entries, notification_rows, contract_ranked = [], {}, [], 0
    for row in sorted(snapshots, key=lambda row: (str(row["created_at"]), row["id"])):
        ev = parse(row["evidence"], "opportunity_decision_snapshot", row["snapshot_id"])
        valid_time = time_consistent(row, "opportunity_decision_snapshot")
        if row["stage"] == "rank" and row["decision"] == "ranked":
            rank_rows.append(row)
            ev = ev or {}
            created = _clock(row["created_at"])
            rank, limit = row["rank"], ev.get("effective_limit")
            if (valid_time and row["scenario"] == "intraday_opportunity" and created
                    and created.astimezone(BJ).date().isoformat() == row["trade_date"]
                    and ev.get("selection_entry_contract") == "selection-entry-v1"
                    and ev.get("within_display_capacity") is True
                    and type(rank) is int and type(limit) is int and 1 <= rank <= limit):
                contract_ranked += 1
                first_entries.setdefault((row["trade_date"], row["symbol"]), row)
        if row["stage"] != "notification":
            continue
        ev = ev or {}
        contract = ev.get("execution_contract") if isinstance(ev.get("execution_contract"), dict) else {}
        quote = contract.get("executable_snapshot") if isinstance(contract.get("executable_snapshot"), dict) else {}
        decision_id, version = _text(contract.get("decision_id"), 128), _text(contract.get("decision_version"), 128)
        run = runs.get(row["run_id"])
        bound_run = bool(run and run["trade_date"] == row["trade_date"])
        passed_claim = ev.get("gate_decision") == "passed" or row["decision"] in {"eligible", "notified"}
        qualified = bool(valid_time and decision_id and version and bound_run and ev.get("gate_decision") == "passed")
        if passed_claim and not qualified:
            unknown("pass_contract_or_source_unknown", "opportunity_decision_snapshot", row["snapshot_id"])
        gaps = []
        if ev.get("confidence_tier") is not None:
            if not isinstance(ev["confidence_tier"], str):
                unknown("gate_contract_unknown", "opportunity_decision_snapshot", row["snapshot_id"])
            elif ev["confidence_tier"] not in {"executable", "strong"}:
                gaps.append("confidence_not_executable")
        if isinstance(ev.get("vetoes"), list) and ev["vetoes"]:
            gaps.append("veto_present")
        if ev.get("observation_only") is True:
            gaps.append("observation_only_recorded")
        if "buy_range" in ev and (not isinstance(ev["buy_range"], dict)
                or ev["buy_range"].get("low") is None or ev["buy_range"].get("high") is None):
            gaps.append("buy_range_missing")
        if quote.get("state") is not None and quote["state"] != "ready":
            gaps.append("snapshot_not_ready")
        if isinstance(quote.get("age_seconds"), (int, float)) and quote["age_seconds"] > 60:
            gaps.append("snapshot_age_over_60_seconds")
        checked = _clock(quote.get("checked_at"), BJ)
        if checked:
            minute = checked.astimezone(BJ).hour * 60 + checked.astimezone(BJ).minute
            if not (570 <= minute < 690 or 780 <= minute < 897):
                gaps.append("checked_outside_continuous_session")
        notification_rows.append({"snapshot_id": _text(row["snapshot_id"], 128), "run_id": _text(row["run_id"], 128),
            "trade_date": row["trade_date"], "symbol": _text(row["symbol"], 12), "source": _text(row["scenario"]),
            "strategy_version": _text(row["strategy_version"], 128), "feature_version": _text(row["feature_version"], 128),
            "as_of": _text(row["as_of"]), "created_at": _text(row["created_at"]), "decision": _text(row["decision"]),
            "decision_id": decision_id, "decision_version": version, "gate_decision": _text(ev.get("gate_decision")),
            "first_blocker": _text(ev.get("gate_reason")), "confidence_tier": _text(ev.get("confidence_tier")),
            "execution_state": _text(quote.get("state")), "independent_recorded_gaps": gaps,
            "provenance_state": "time_consistent_not_authenticity_proof" if valid_time else "unknown",
            "execution_contract_state": "bound" if decision_id and version else "legacy_or_missing",
            "run_registered": bound_run, "recorded_passed": qualified, "position_consumer_count": 0, "orders": []})

    consumer_records = []
    for row in data["buy_point_consumption"] or []:
        payload = parse(row["payload"], "buy_point_consumption", row["event_key"])
        if payload is None:
            continue
        alert = payload.get("alert") if isinstance(payload.get("alert"), dict) else {}
        meta = alert.get("meta") if isinstance(alert.get("meta"), dict) else {}
        ref = meta.get("execution_ref") if isinstance(meta.get("execution_ref"), dict) else {}
        day = meta.get("trade_date")
        if _day(day) and not in_window(day):
            continue
        if not _day(day):
            unknown("consumer_trade_day_unknown", "buy_point_consumption", row["event_key"])
        result = payload.get("execution_result") if isinstance(payload.get("execution_result"), dict) else {}
        consumer_records.append({"event_key": _text(row["event_key"], 128), "event_id": row["event_id"],
            "consumer": _text(row["consumer"]), "trade_date": _text(day), "symbol": _text(alert.get("symbol"), 12),
            "decision_id": _text(ref.get("decision_id"), 128), "decision_version": _text(ref.get("decision_version"), 128),
            "source": "buy_point_consumption", "state": _text(row["state"]), "reason": _text(row["reason"]),
            "attempts": row["attempts"], "order_id": result.get("order_id") if type(result.get("order_id")) is int else None,
            "opened": result.get("opened") is True, "execution_unknown": result.get("execution_unknown") is True})
    report["consumers"] = {"raw_rows": len(data["buy_point_consumption"]) if data["buy_point_consumption"] is not None else None,
        "window_rows": len(consumer_records) if data["buy_point_consumption"] is not None else None,
        "states": _count(f'{row["consumer"]}:{row["state"]}' for row in consumer_records),
        "records": consumer_records}

    orders = []
    for row in data["paper_order"] or []:
        created = _clock(row["created_at"])
        if not created:
            unknown("order_clock_unknown", "paper_order", row["id"])
        if created and not in_window(created.astimezone(BJ).date().isoformat()):
            continue
        orders.append(row)
    report["orders"] = {"row_count": len(orders) if data["paper_order"] is not None else None,
        "main_filled_count": sum(row["scope"] == "main" and row["status"] == "filled" for row in orders)
            if data["paper_order"] is not None else None,
        "scope_states": _count(f'{row["scope"]}:{row["side"]}:{row["status"]}' for row in orders)}
    by_order = {row["id"]: row for row in orders}
    for record in notification_rows:
        linked = [row for row in consumer_records if row["consumer"] == "position" and record["decision_id"]
            and row["trade_date"] == record["trade_date"] and row["symbol"] == record["symbol"]
            and row["decision_id"] == record["decision_id"] and row["decision_version"] == record["decision_version"]]
        record["position_consumer_count"] = len(linked) if data["buy_point_consumption"] is not None else None
        record["orders"] = ([{"order_id": order["id"], "scope": _text(order["scope"]), "status": _text(order["status"])}
            for row in linked if (order := by_order.get(row["order_id"])) is not None]
            if data["paper_order"] is not None else None)
    grouped = defaultdict(list)
    for row in sorted(notification_rows, key=lambda row: (row["as_of"] or "", row["snapshot_id"] or "")):
        grouped[(row["trade_date"], row["symbol"])].append(row)
    groups = [{"trade_date": key[0], "symbol": key[1], "record_count": len(values), "first": values[0],
               "all_first_blockers": _count(row["first_blocker"] for row in values)} for key, values in sorted(grouped.items())]
    passed = [row for row in notification_rows if row["recorded_passed"]]
    report["notification"] = {"raw_rows": len(notification_rows) if data["opportunity_decision_snapshot"] is not None else None,
        "distinct_day_symbols": len(grouped) if data["opportunity_decision_snapshot"] is not None else None,
        "decision_states": _count(row["decision"] for row in notification_rows),
        "first_blockers": _count(group["first"]["first_blocker"] for group in groups),
        "all_recorded_first_blockers": _count(row["first_blocker"] for row in notification_rows),
        "independent_recorded_gaps": _count(gap for row in notification_rows for gap in row["independent_recorded_gaps"]),
        "recorded_passed_rows": len(passed) if data["opportunity_decision_snapshot"] is not None else None,
        "passed_without_position_consumer": sum(not row["position_consumer_count"] for row in passed)
            if data["buy_point_consumption"] is not None else None,
        "groups": groups, "records": notification_rows}
    notification_pairs = set(grouped)
    snapshots_known = data["opportunity_decision_snapshot"] is not None
    report["intraday"] = {"raw_ranked_rows": len(rank_rows) if snapshots_known else None,
        "raw_ranked_day_symbols": len({(row["trade_date"], row["symbol"]) for row in rank_rows}) if snapshots_known else None,
        "first_entry_day_symbols": len(first_entries) if snapshots_known else None,
        "contract_ranked_rows": contract_ranked if snapshots_known else None,
        "repeat_contract_ranked_rows": contract_ranked - len(first_entries) if snapshots_known else None,
        "first_entry_states": _count(row["data_state"] for row in first_entries.values()),
        "legacy_or_unqualified_ranked_rows": len(rank_rows) - contract_ranked if snapshots_known else None,
        "without_buy_point_day_symbols": len(set(first_entries) - notification_pairs) if snapshots_known else None,
        "records": [{key: _text(row[key], 128) for key in ("snapshot_id", "run_id", "trade_date", "symbol",
            "strategy_version", "feature_version", "data_state", "as_of", "created_at")} for row in first_entries.values()]}
    denominator_known = snapshots_known and data["daily_pick_set"] is not None
    report["denominators"] = {"daily_raw_and_first_entry_union": len(daily_pairs | set(first_entries)) if denominator_known else None,
        "generation_and_first_entry_union": len(generated_pairs | set(first_entries)) if denominator_known else None,
        "daily_intraday_overlap": len(daily_pairs & set(first_entries)) if denominator_known else None,
        "semantics": "mixed_saved_generation_and_first_entry_not_trades"}

    alerts = []
    for row in data["alert_event"] or []:
        ev = parse(row["snapshot"], "alert_event", row["id"])
        if ev is None or ev.get("kind") not in {"selection", "buy_point", "confirm", "position_open"}:
            continue
        day = ev.get("trade_date")
        if not _day(day):
            clock = _clock(row["triggered_at"], BJ)
            day = clock.astimezone(BJ).date().isoformat() if clock else None
        if _day(day) and not in_window(day):
            continue
        meta = ev.get("meta") if isinstance(ev.get("meta"), dict) else {}
        ref = meta.get("execution_ref") if isinstance(meta.get("execution_ref"), dict) else {}
        alerts.append({"event_id": row["id"], "trade_date": day, "symbol": _text(row["symbol"], 12),
            "kind": _text(ev.get("kind")), "source": _text(ev.get("selection_source")),
            "source_version": _text(ev.get("source_version"), 128), "source_as_of": _text(ev.get("source_as_of")),
            "certainty_level": _text(meta.get("certainty_level")), "decision_id": _text(ref.get("decision_id"), 128),
            "decision_version": _text(ref.get("decision_version"), 128)})
    report["messages"] = {"states": _count(row["kind"] for row in alerts), "records": alerts}
    kind_by_event = {row["event_id"]: row["kind"] for row in alerts}
    outbox = []
    for row in data["notification_outbox"] or []:
        try:
            day = datetime.fromtimestamp(row["created_at_ms"] / 1000, BJ).date().isoformat()
        except (ValueError, TypeError, OverflowError, OSError):
            unknown("outbox_clock_unknown", "notification_outbox", row["id"])
            day = None
        if day and not in_window(day):
            continue
        outbox.append({"outbox_id": row["id"], "event_id": row["event_id"], "trade_date": day,
            "kind": kind_by_event.get(row["event_id"], "other_or_unlinked"), "channel": _text(row["channel"]),
            "state": _text(row["state"]), "reason": _text(row["reason"])})
    report["outbox"] = {"raw_rows": len(data["notification_outbox"]) if data["notification_outbox"] is not None else None,
        "window_rows": len(outbox) if data["notification_outbox"] is not None else None, "records": outbox}
    reset_rows, receipt_rows = [], []
    for table, target in (("paper_reset_backup", reset_rows), ("paper_action_receipt", receipt_rows)):
        for row in data[table] or []:
            created = _clock(row["created_at"])
            if not created:
                unknown("action_clock_unknown", table)
            if created and not in_window(created.astimezone(BJ).date().isoformat()):
                continue
            target.append(row)
    report["reset"] = {"backup_count": len(reset_rows) if data["paper_reset_backup"] is not None else None,
        "scope_states": _count(f'{row["scope"]}:{row["restored"]}' for row in reset_rows),
        "action_receipt_count": len(receipt_rows) if data["paper_action_receipt"] is not None else None,
        "receipt_scope_states": _count(row["scope"] for row in receipt_rows)}

    if position_plans is not None:
        records, files = [], 0
        folder = Path(position_plans)
        if not folder.is_dir() or folder.is_symlink():
            unknown("plan_directory_unavailable", "position_plans")
        else:
            for plan_path in sorted(folder.glob("*.json")):
                day = _day(plan_path.stem)
                if not day or not in_window(day):
                    continue
                files += 1
                if plan_path.is_symlink() or plan_path.stat().st_size > MAX_JSON_BYTES:
                    unknown("plan_file_unavailable", "position_plans", day)
                    continue
                try:
                    plan = parse(plan_path.read_text(), "position_plans", day)
                except (OSError, UnicodeError):
                    unknown("plan_file_unavailable", "position_plans", day)
                    continue
                if plan is None:
                    continue
                decisions = plan.get("decisions")
                if plan.get("date") != day or not isinstance(decisions, list):
                    unknown("plan_contract_unknown", "position_plans", day)
                    continue
                for row in decisions:
                    if not isinstance(row, dict):
                        unknown("plan_contract_unknown", "position_plans", day)
                        continue
                    records.append({"trade_date": day, **{key: _text(row.get(key), 128) for key in (
                        "symbol", "action", "trigger", "decision_id", "decision_version")},
                        "order_id": row.get("order_id") if type(row.get("order_id")) is int else None})
        report["plans"] = {"state": "read", "files": files, "records": records,
                           "action_states": _count(row["action"] for row in records)}
    if report["unknowns"]:
        report["status"] = "partial"
    elif passed:
        report["conclusion"]["state"] = "passed_action_needs_trace_review"
    else:
        report["conclusion"]["state"] = "no_recorded_pass"
    report["conclusion"]["evidence_boundary"] = "Only inspected original records; no counterfactual order or fill inferred."
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New local JSON artifact; existing files are never replaced")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--position-plans", type=Path, help="Optional explicit local directory, read-only")
    args = parser.parse_args(argv)
    protected = {args.database.resolve(), *(Path(str(args.database.resolve()) + suffix)
                 for suffix in ("-wal", "-shm", "-journal"))}
    if args.output.exists() or args.output.is_symlink() or args.output.resolve() in protected:
        print(json.dumps({"status": "unknown", "code": "output_not_new_artifact"}))
        return 2
    try:
        report = audit(args.database, start_date=args.start_date, end_date=args.end_date, position_plans=args.position_plans)
        body = (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, ValueError, sqlite3.Error):
        print(json.dumps({"status": "unknown", "code": "audit_or_output_unavailable"}))
        return 2
    print(json.dumps({"status": report["status"], "conclusion": report["conclusion"]["state"]}))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
