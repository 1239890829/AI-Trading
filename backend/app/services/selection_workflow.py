"""Persist workflow milestones without requalifying a stock or sending a channel."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.alert import AlertEvent, AlertRule
from app.repositories.alert_repo import AlertRepository
from app.services.selection_notifications import SELECTION_RULE, selection_dedup_key

REPORTS = {"market_report", "sentiment_report", "news_report", "fundamentals_report",
           "investment_plan", "trader_investment_plan", "final_trade_decision"}


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _clock(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp if stamp.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def record_research_ready(*, selection: dict, report: dict, session_factory=None) -> dict:
    """Called only after a native result and its completed claim were persisted.

    The original selection remains the owner. A research milestone is neither a
    new selection nor an executable trading signal. Ordinary reads never call it.
    """
    if not isinstance(selection, dict) or not isinstance(report, dict):
        return {"state": "rejected", "reason": "invalid_binding"}
    symbol, day = selection.get("symbol"), selection.get("trade_date")
    generated, selected = _clock(report.get("generated_at")), _clock(selection.get("selected_at"))
    reports = report.get("reports")
    if (not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol)
            or not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)
            or selection.get("source") not in {"daily_generation", "intraday_rank_archive"}
            or not isinstance(selection.get("source_version"), str) or not selection["source_version"]
            or not re.fullmatch(r"[0-9a-f]{64}", str(selection.get("selection_key") or ""))
            or report.get("selection") != selection or report.get("symbol") != symbol
            or report.get("state") != "completed" or report.get("production_eligible") is not False
            or report.get("verification") != "unverified_full_graph_research"
            or not re.fullmatch(r"[0-9a-f]{32}", str(report.get("run_id") or ""))
            or not re.fullmatch(r"[0-9a-f]{64}", str(report.get("method_sha256") or ""))
            or not generated or not selected or generated < selected
            or not isinstance(reports, dict) or set(reports) != REPORTS
            or any(not isinstance(v, str) or not v.strip() for v in reports.values())):
        return {"state": "rejected", "reason": "invalid_completed_result"}
    sf = session_factory or get_session_factory()
    with sf() as db:
        origin = db.scalar(select(AlertEvent).join(AlertRule, AlertRule.id == AlertEvent.rule_id).where(
            AlertEvent.dedup_key == selection_dedup_key(day, symbol), AlertRule.name == SELECTION_RULE))
        if origin is None:
            return {"state": "unavailable", "reason": "original_selection_message_missing"}
        try:
            snap = json.loads(origin.snapshot or "{}")
        except (ValueError, TypeError):
            return {"state": "rejected", "reason": "original_selection_mismatch"}
        if not isinstance(snap, dict):
            return {"state": "rejected", "reason": "original_selection_mismatch"}
        if (snap.get("kind") != "selection" or snap.get("trade_date") != day
                or snap.get("selection_source") != ("daily" if selection["source"] == "daily_generation" else "intraday")
                or snap.get("source_version") != selection["source_version"]):
            return {"state": "rejected", "reason": "original_selection_mismatch"}
        rule_id, origin_id, name = origin.rule_id, origin.id, str(snap.get("name") or symbol)
    version = _digest(["research-ready:v1", selection["selection_key"], report["run_id"], report["method_sha256"]])
    snapshot = {"kind": "research_ready", "name": name, "trade_date": day,
                "selection_source": "daily" if selection["source"] == "daily_generation" else "intraday",
                "source_id": f"selection:{origin_id}", "source_version": version,
                "source_as_of": report["generated_at"], "run_id": report["run_id"],
                "origin_event_id": origin_id, "push_policy": "in_app",
                "text": "本股完整研究已保存，可逐角色核对原答与七份报告；研究结论仍待核，不是买卖指令。",
                "research": {"run_id": report["run_id"], "method_sha256": report["method_sha256"],
                             "generated_at": report["generated_at"], "as_of": report.get("as_of"),
                             "selection_key": selection["selection_key"], "report_count": len(reports)}}
    event, inserted = AlertRepository(sf).record_trigger_once(rule_id, symbol, 0, 0,
        dedup_key=version, snapshot=snapshot, delivered_channels=["in_app"])
    return {"state": "recorded", "created": bool(inserted), "event_id": event.id}


def record_paper_status(selection_ref, source_version, order_id, session_factory=None) -> dict:
    """Publish only a committed, selection-bound main order's real state.

    Matcher callers may omit a binding; its persisted main receipt is required.
    No account data, funds, position mutation or external channel is involved.
    """
    from sqlalchemy import func
    from app.core.bjtime import to_beijing
    from app.models.paper import PaperActionReceipt, PaperOrder
    from app.picks.review_contract import object_json
    sf = session_factory or get_session_factory()
    with sf() as db:
        query = select(PaperActionReceipt).where(PaperActionReceipt.scope == "main")
        if order_id is not None:
            query = query.where(func.json_extract(PaperActionReceipt.result, "$.id") == order_id)
        elif selection_ref and source_version:
            query = query.where(func.json_extract(PaperActionReceipt.draft, "$.selection_ref") == selection_ref,
                                func.json_extract(PaperActionReceipt.draft, "$.source_version") == source_version)
        else:
            return {"state": "unavailable", "reason": "selection_receipt_missing"}
        receipt = db.scalar(query.order_by(PaperActionReceipt.created_at.desc()).limit(1))
        if receipt is None:
            # Automatic buy-point actions own a different durable receipt. Do
            # not fabricate a manual ActionReceipt or match by stock/day alone.
            db.close()
            return record_automatic_paper_status(order_id=order_id, session_factory=sf) if order_id is not None else {
                "state": "unavailable", "reason": "selection_receipt_missing"}
        draft, result = object_json(receipt.draft), object_json(receipt.result)
        ref, version = draft.get("selection_ref"), draft.get("source_version")
        match = re.fullmatch(r"(daily|intraday):(\d{4}-\d{2}-\d{2}):(\d{6})", ref or "")
        if not match or not version or (selection_ref and selection_ref != ref) or (source_version and source_version != version):
            return {"state": "rejected", "reason": "selection_receipt_mismatch"}
        scope, day, symbol = match.groups()
        row = db.get(PaperOrder, result.get("id")) if result.get("id") is not None else None
        if (row is not None and (row.scope != "main" or row.symbol != symbol or row.side != "buy" or row.status not in {"pending", "filled", "rejected", "cancelled"})) or (row is None and (result.get("id") is not None or result.get("status") != "rejected")):
            return {"state": "unavailable", "reason": "bound_main_order_missing"}
        origin = db.scalar(select(AlertEvent).join(AlertRule, AlertRule.id == AlertEvent.rule_id).where(
            AlertEvent.dedup_key == selection_dedup_key(day, symbol), AlertRule.name == SELECTION_RULE))
        snap = object_json(origin.snapshot) if origin else {}
        if (origin is None or snap.get("kind") != "selection" or snap.get("trade_date") != day
                or snap.get("selection_source") != scope or snap.get("source_version") != version):
            return {"state": "unavailable", "reason": "original_selection_message_missing"}
        from datetime import timezone
        recorded = row.updated_at if row else receipt.created_at
        changed = recorded.replace(tzinfo=timezone.utc) if recorded.tzinfo is None else recorded
        clock = to_beijing(changed).isoformat()
        status = row.status if row else "rejected"
        reason = row.reason if row else result.get("reason")
        identity = _digest(["paper-status:v1", ref, version, row.id if row else receipt.request_id, status])
        state_labels = {"pending": "模拟委托待成交", "filled": "模拟买入已成交", "rejected": "模拟委托被拒绝", "cancelled": "模拟委托已取消"}
        snapshot = {"kind": "paper_status", "name": str(snap.get("name") or symbol), "trade_date": day,
                    "selection_source": scope, "source_id": f"selection:{origin.id}",
                    "source_version": identity, "source_as_of": clock,
                    "origin_event_id": origin.id, "push_policy": "in_app",
                    "text": state_labels[status] + (f"：{reason}" if reason else "。请核对原委托记录。"),
                    "paper_execution": {"scope": "main", "order_id": row.id if row else None, "status": status,
                                        "request_id": receipt.request_id, "reason": reason,
                                        "selection_ref": ref, "source_version": version,
                                        "quantity": row.quantity if row else draft.get("quantity"),
                                        "filled_price": row.filled_price if row else None, "fee": row.fee if row else None}}
        rule_id = origin.rule_id
    event, inserted = AlertRepository(sf).record_trigger_once(rule_id, symbol, 0, 0,
        dedup_key=identity, snapshot=snapshot, delivered_channels=["in_app"])
    return {"state": "recorded", "created": bool(inserted), "event_id": event.id}


def record_automatic_paper_status(*, event_key=None, order_id=None, session_factory=None) -> dict:
    """Project a saved consumer outcome through its exact original decision.

    Every link is explicit: immutable buy-point event, original selection version,
    archived decision/version, saved consumer result, and one real main buy order.
    This function never places an order or creates an ActionReceipt.
    """
    from sqlalchemy import case, func
    from datetime import timezone
    from app.core.bjtime import to_beijing
    from app.models.notification_outbox import BuyPointConsumption as Progress
    from app.models.opportunity_learning import OpportunityDecisionSnapshot as Decision
    from app.models.paper import PaperOrder
    from app.picks.buy_point import _buy_point_dedup_key
    from app.picks.review_contract import object_json
    sf = session_factory or get_session_factory()
    with sf() as db:
        safe = case((func.json_valid(Progress.payload), Progress.payload), else_="{}")
        query = select(Progress).where(Progress.consumer == "position", Progress.state == "done")
        if event_key is not None:
            query = query.where(Progress.event_key == event_key)
        elif type(order_id) is int and order_id > 0:
            query = query.where(func.json_type(safe, "$.execution_result.order_id") == "integer",
                                func.json_extract(safe, "$.execution_result.order_id") == order_id)
        else:
            return {"state": "unavailable", "reason": "automatic_receipt_missing"}
        matches = list(db.scalars(query.limit(2)))
        if len(matches) != 1:
            return {"state": "unavailable", "reason": "automatic_receipt_ambiguous_or_missing"}
        progress = matches[0]
        payload = object_json(progress.payload)
        alert, result = object_json(payload.get("alert")), object_json(payload.get("execution_result"))
        meta = object_json(alert.get("meta"))
        ref, version = meta.get("selection_ref"), meta.get("selection_source_version")
        match = re.fullmatch(r"daily:(\d{4}-\d{2}-\d{2}):(\d{6})", ref or "")
        if not match or not isinstance(version, str) or not version:
            return {"state": "unavailable", "reason": "automatic_selection_binding_missing"}
        day, symbol = match.groups()
        execution = object_json(meta.get("execution_ref"))
        identifier, decision_version = execution.get("decision_id"), execution.get("decision_version")
        saved_order_id = result.get("order_id")
        if (alert.get("kind") != "buy_point" or alert.get("symbol") != symbol or meta.get("trade_date") != day
                or progress.event_key != _buy_point_dedup_key(day, symbol)
                or not identifier or not decision_version
                or result.get("decision_id") != identifier or result.get("decision_version") != decision_version
                or type(saved_order_id) is not int or saved_order_id <= 0
                or (order_id is not None and saved_order_id != order_id) or result.get("execution_unknown")
                or not (result.get("opened") is True or result.get("accepted") is True)):
            return {"state": "rejected", "reason": "automatic_receipt_mismatch"}
        event = db.get(AlertEvent, progress.event_id)
        rule = db.get(AlertRule, event.rule_id) if event else None
        event_snapshot = object_json(event.snapshot) if event else {}
        if (event is None or rule is None or rule.name != "__picks_buy_point__" or event.symbol != symbol
                or event.dedup_key != progress.event_key or event_snapshot.get("kind") != "buy_point"
                or event_snapshot.get("execution_ref") != execution):
            return {"state": "rejected", "reason": "automatic_source_event_mismatch"}
        safe_evidence = case((func.json_valid(Decision.evidence), Decision.evidence), else_="{}")
        decisions = list(db.scalars(select(Decision).where(Decision.trade_date == day, Decision.symbol == symbol,
            Decision.scenario == "buy_point", Decision.stage == "notification", Decision.decision == "eligible",
            Decision.data_state == "ready", func.json_extract(safe_evidence, "$.execution_contract.decision_id") == identifier,
            func.json_extract(safe_evidence, "$.execution_contract.decision_version") == decision_version)))
        contracts = [object_json(object_json(row.evidence).get("execution_contract")) for row in decisions]
        if not contracts or any(contract.get("gate_decision") != "passed"
                or object_json(contract.get("executable_snapshot")).get("state") != "ready" for contract in contracts):
            return {"state": "rejected", "reason": "automatic_decision_missing_or_ineligible"}
        origin = db.scalar(select(AlertEvent).join(AlertRule, AlertRule.id == AlertEvent.rule_id).where(
            AlertEvent.dedup_key == selection_dedup_key(day, symbol), AlertRule.name == SELECTION_RULE))
        snap = object_json(origin.snapshot) if origin else {}
        if (origin is None or origin.symbol != symbol or snap.get("kind") != "selection" or snap.get("trade_date") != day
                or snap.get("selection_source") != "daily" or snap.get("source_version") != version
                or any(object_json(contract.get("reference_entry")).get("source") != "daily_pick_set"
                       or object_json(contract.get("reference_entry")).get("as_of") != snap.get("source_as_of") for contract in contracts)):
            return {"state": "unavailable", "reason": "original_selection_message_missing"}
        row = db.get(PaperOrder, saved_order_id)
        if (row is None or row.scope != "main" or row.symbol != symbol or row.side != "buy"
                or row.status not in {"pending", "filled", "rejected", "cancelled"}
                or to_beijing(row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at).date().isoformat() != day):
            return {"state": "rejected", "reason": "automatic_main_order_mismatch"}
        changed = row.updated_at.replace(tzinfo=timezone.utc) if row.updated_at.tzinfo is None else row.updated_at
        clock = to_beijing(changed).isoformat()
        identity = _digest(["paper-status:v1", ref, version, row.id, row.status])
        labels = {"pending": "模拟委托待成交", "filled": "模拟买入已成交", "rejected": "模拟委托被拒绝", "cancelled": "模拟委托已取消"}
        snapshot = {"kind": "paper_status", "name": str(snap.get("name") or symbol), "trade_date": day,
            "selection_source": "daily", "source_id": f"selection:{origin.id}", "source_version": identity,
            "source_as_of": clock, "origin_event_id": origin.id, "push_policy": "in_app",
            "text": labels[row.status] + (f"：{row.reason}" if row.reason else "。请核对原委托记录。"),
            "paper_execution": {"scope": "main", "order_id": row.id, "status": row.status,
                "reason": row.reason, "request_id": None, "consumer_event_key": progress.event_key,
                "buy_point_event_id": event.id, "decision_id": identifier, "decision_version": decision_version,
                "selection_ref": ref, "source_version": version,
                "quantity": row.quantity, "filled_price": row.filled_price, "fee": row.fee}}
        rule_id = origin.rule_id
    projected, inserted = AlertRepository(sf).record_trigger_once(rule_id, symbol, 0, 0,
        dedup_key=identity, snapshot=snapshot, delivered_channels=["in_app"])
    return {"state": "recorded", "created": bool(inserted), "event_id": projected.id}
