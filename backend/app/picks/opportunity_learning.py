"""Replayable evidence and outcome labels for the stock-opportunity funnel.

Snapshots are append-only: later closes are written to a separate outcome table.
The archived evidence is sufficient to replay each deterministic stage without
calling a market-data provider or consulting today's mutable configuration.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.bjtime import beijing_now
from app.core.db import get_session_factory, utcnow
from app.models.opportunity_learning import OpportunityDecisionSnapshot, OpportunityOutcomeLabel

STRATEGY_VERSION = "stock-opportunity-funnel-v1"
FEATURE_VERSION = "pit-evidence-v1"
OUTCOME_HORIZON = "d0_close"
STAGES = ("candidate", "hard_gate", "rank", "notification")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any, length: int = 32) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()[:length]


def _data_state(payload: dict) -> str:
    if payload.get("linkage_note"):
        return "degraded"
    stats = payload.get("linkage_stats") or {}
    if int(stats.get("missing_quote") or 0) > 0 or payload.get("hot_available") is False:
        return "degraded"
    return "ready"


def build_intraday_records(payload: dict, *, trade_date: str, as_of: datetime) -> tuple[str, list[dict]]:
    """Turn one opportunity tree into candidate → hard-gate → rank evidence."""
    from app.picks.intraday_opportunity import top_watch_stocks

    as_of = as_of.replace(tzinfo=None)
    run_id = _hash({"scenario": "intraday", "trade_date": trade_date, "as_of": as_of.isoformat()})
    ranked = top_watch_stocks(payload, limit=10_000).get("items") or []
    rank_by_key = {
        (str(row.get("symbol") or ""), str(row.get("theme") or "")): n
        for n, row in enumerate(ranked, 1)
    }
    state = _data_state(payload)
    records: list[dict] = []
    for theme in payload.get("themes") or []:
        theme_name = str(theme.get("theme") or "")
        participants = theme.get("participants") or []
        participant_by = {str(s.get("symbol") or ""): s for s in participants}
        audit_rows = theme.get("_candidate_audit") or []
        if not audit_rows:
            audit_rows = [
                {
                    "symbol": s.get("symbol"), "name": s.get("name"),
                    "candidate_decision": "included",
                    "hard_gate_decision": (
                        "passed" if (s.get("tradability") or {}).get("level") == "可参与"
                        else "unknown" if (s.get("tradability") or {}).get("level") in (None, "unknown")
                        else "rejected"
                    ),
                    "reason": (s.get("tradability") or {}).get("basis"),
                    "price": s.get("price"), "change_pct": s.get("change_pct"),
                    "amount": s.get("amount"), "board": s.get("board"),
                    "facts": {
                        "quote_present": s.get("price") is not None,
                        "board_tradable": (s.get("tradability") or {}).get("level") == "可参与",
                        "change_pct": s.get("change_pct"),
                        "amount": s.get("amount"),
                    },
                }
                for s in participants
            ]
        for audit in audit_rows:
            symbol = str(audit.get("symbol") or "")
            if not symbol:
                continue
            stock = participant_by.get(symbol) or audit
            price = audit.get("price", stock.get("price"))
            common = {
                "run_id": run_id, "trade_date": trade_date, "as_of": as_of,
                "scenario": "intraday_opportunity", "symbol": symbol,
                "name": str(audit.get("name") or stock.get("name") or ""),
                "source_theme": theme_name, "strategy_version": STRATEGY_VERSION,
                "feature_version": FEATURE_VERSION, "data_state": state,
                "entry_price": float(price) if isinstance(price, (int, float)) and price > 0 else None,
            }
            candidate_evidence = {
                "theme_stage": theme.get("stage"),
                "theme_strength": theme.get("strength_tier"),
                "change_pct": audit.get("change_pct"), "amount": audit.get("amount"),
                "filter_reason": audit.get("reason"), "facts": audit.get("facts") or {},
            }
            records.append({
                **common, "stage": "candidate",
                "decision": str(audit.get("candidate_decision") or "unknown"),
                "rank": None, "evidence": candidate_evidence,
            })
            facts = audit.get("facts") or {}
            gate_evidence = {
                "filter_reason": audit.get("reason"), "board": audit.get("board"), "facts": facts,
                "tradability_level": (
                    "可参与" if audit.get("hard_gate_decision") == "passed"
                    else "unknown" if audit.get("hard_gate_decision") == "unknown"
                    else "不可参与"
                ),
            }
            records.append({
                **common, "stage": "hard_gate",
                "decision": str(audit.get("hard_gate_decision") or "unknown"),
                "rank": None, "evidence": gate_evidence,
            })

        for stock in participants:
            symbol = str(stock.get("symbol") or "")
            if not symbol:
                continue
            name = str(stock.get("name") or "")
            price = stock.get("price")
            tradability = stock.get("tradability") or {}
            linkage = stock.get("linkage") or {}
            common = {
                "run_id": run_id,
                "trade_date": trade_date,
                "as_of": as_of,
                "scenario": "intraday_opportunity",
                "symbol": symbol,
                "name": name,
                "source_theme": theme_name,
                "strategy_version": STRATEGY_VERSION,
                "feature_version": FEATURE_VERSION,
                "data_state": state,
                "entry_price": float(price) if isinstance(price, (int, float)) and price > 0 else None,
            }
            tradability_level = tradability.get("level")
            if tradability_level == "可参与":
                gate_decision = "passed"
            elif tradability_level == "unknown" or not tradability_level:
                gate_decision = "unknown"
            else:
                gate_decision = "rejected"
            rank = rank_by_key.get((symbol, theme_name))
            linkage_level = linkage.get("level")
            if gate_decision == "unknown" or linkage_level == "unknown" or not linkage_level:
                rank_decision = "unknown"
            elif gate_decision != "passed" or linkage_level not in ("高", "中"):
                rank_decision = "rejected"
            else:
                rank_decision = "ranked"
            rank_evidence = {
                "gate_decision": gate_decision,
                "linkage_level": linkage_level,
                "linkage_basis": linkage.get("basis"),
                "change_pct": stock.get("change_pct"),
                "expected_rank": rank,
            }
            records.append({**common, "stage": "rank", "decision": rank_decision,
                            "rank": rank, "evidence": rank_evidence})
    return run_id, records


def build_notification_records(
    items: list[dict], *, trade_date: str, as_of: datetime,
    hits: list[dict], skips: list[dict], dispatch_by_symbol: dict[str, str],
) -> tuple[str, list[dict]]:
    """Archive every notification-gate input, including negative decisions."""
    as_of = as_of.replace(tzinfo=None)
    run_id = _hash({"scenario": "notification", "trade_date": trade_date, "as_of": as_of.isoformat()})
    hit_by = {str(h.get("item", {}).get("symbol") or ""): h for h in hits}
    skip_by = {str(s.get("symbol") or ""): str(s.get("reason") or "") for s in skips}
    records: list[dict] = []
    for item in items or []:
        symbol = str(item.get("symbol") or "")
        if not symbol:
            continue
        hit = hit_by.get(symbol)
        dispatch = dispatch_by_symbol.get(symbol)
        if hit is None:
            decision = "rejected"
        elif dispatch == "notified":
            decision = "notified"
        elif dispatch == "suppressed":
            decision = "suppressed"
        else:
            decision = "eligible"
        price = (hit or {}).get("price")
        evidence = {
            "gate_decision": "passed" if hit is not None else "rejected",
            "gate_reason": skip_by.get(symbol),
            "dispatch": dispatch,
            "confidence_tier": (item.get("confidence") or {}).get("tier"),
            "vetoes": item.get("vetoes") or [],
            "follow_state": item.get("follow_state"),
            "observation_only": bool(item.get("observation_only")),
            "buy_range": item.get("buy_range"),
            "change_pct": (hit or {}).get("chg"),
        }
        records.append({
            "run_id": run_id, "trade_date": trade_date, "as_of": as_of,
            "scenario": "buy_point", "stage": "notification", "symbol": symbol,
            "name": str(item.get("name") or ""), "source_theme": "",
            "decision": decision, "rank": None, "strategy_version": STRATEGY_VERSION,
            "feature_version": FEATURE_VERSION,
            "data_state": "unknown" if "快照无现价" in skip_by.get(symbol, "") else "ready",
            "entry_price": float(price) if isinstance(price, (int, float)) and price > 0 else None,
            "evidence": evidence,
        })
    return run_id, records


def archive_records(run_id: str, records: list[dict], session_factory=None) -> dict:
    """Persist a whole run atomically; rerunning the same run is idempotent."""
    sf = session_factory or get_session_factory()
    inserted = 0
    with sf() as db:
        existing = set(db.execute(
            select(OpportunityDecisionSnapshot.snapshot_id).where(
                OpportunityDecisionSnapshot.run_id == run_id
            )
        ).scalars().all())
        for record in records:
            evidence = record.get("evidence") or {}
            snapshot_id = _hash({
                "run_id": run_id, "stage": record["stage"], "symbol": record["symbol"],
                "theme": record.get("source_theme") or "", "decision": record["decision"],
                "evidence": evidence,
            }, 40)
            if snapshot_id in existing:
                continue
            row = OpportunityDecisionSnapshot(
                snapshot_id=snapshot_id,
                evidence=_json(evidence),
                **{k: v for k, v in record.items() if k != "evidence"},
            )
            db.add(row)
            if record["stage"] in ("rank", "notification") and record["decision"] in (
                "ranked", "notified", "suppressed",
            ):
                db.add(OpportunityOutcomeLabel(
                    snapshot_id=snapshot_id, horizon=OUTCOME_HORIZON,
                    target_date=record["trade_date"], state="pending", label="unknown",
                    reference_price=record.get("entry_price"), reason="等待收盘价",
                ))
            existing.add(snapshot_id)
            inserted += 1
        db.commit()
    return {"run_id": run_id, "inserted": inserted, "records": len(records)}


def archive_intraday_pipeline(payload: dict, *, trade_date: str, as_of: datetime | None = None,
                              session_factory=None) -> dict:
    run_id, records = build_intraday_records(
        payload, trade_date=trade_date, as_of=as_of or beijing_now()
    )
    return archive_records(run_id, records, session_factory)


def archive_notification_pipeline(
    items: list[dict], *, trade_date: str, hits: list[dict], skips: list[dict],
    dispatch_by_symbol: dict[str, str], as_of: datetime | None = None, session_factory=None,
) -> dict:
    run_id, records = build_notification_records(
        items, trade_date=trade_date, as_of=as_of or beijing_now(), hits=hits, skips=skips,
        dispatch_by_symbol=dispatch_by_symbol,
    )
    return archive_records(run_id, records, session_factory)


def replay_decision(stage: str, evidence: dict) -> str:
    """Re-evaluate one archived stage using archived facts only."""
    if stage == "candidate":
        facts = evidence.get("facts") or {}
        if facts.get("sealed_pool") is True or facts.get("board_tradable") is False:
            return "rejected"
        if facts.get("quote_present") is False or (
            "change_pct" in facts and facts.get("change_pct") is None
        ):
            return "unknown"
        pct = facts.get("change_pct")
        if pct is not None and facts.get("linkage_min_pct") is not None:
            if pct < facts["linkage_min_pct"]:
                return "rejected"
        if facts.get("amount_min") is not None and (facts.get("amount") or 0) < facts["amount_min"]:
            return "rejected"
        if facts.get("seal_line") is not None and pct is not None and pct >= facts["seal_line"]:
            return "rejected"
        return "included"
    if stage == "hard_gate":
        facts = evidence.get("facts") or {}
        if facts.get("sealed_pool") is True or facts.get("board_tradable") is False:
            return "rejected"
        if facts.get("quote_present") is False or (
            "change_pct" in facts and facts.get("change_pct") is None
        ):
            return "unknown"
        pct = facts.get("change_pct")
        if facts.get("seal_line") is not None and pct is not None and pct >= facts["seal_line"]:
            return "rejected"
        level = evidence.get("tradability_level")
        return "unknown" if level in (None, "unknown") else ("passed" if level == "可参与" else "rejected")
    if stage == "rank":
        gate = evidence.get("gate_decision")
        linkage = evidence.get("linkage_level")
        if gate == "unknown" or linkage == "unknown" or not linkage:
            return "unknown"
        return "ranked" if gate == "passed" and linkage in ("高", "中") else "rejected"
    if stage == "notification":
        if evidence.get("gate_decision") != "passed":
            return "rejected"
        dispatch = evidence.get("dispatch")
        return dispatch if dispatch in ("notified", "suppressed") else "eligible"
    raise ValueError(f"unknown opportunity stage: {stage}")


def replay_run(run_id: str, session_factory=None) -> dict:
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(OpportunityDecisionSnapshot)
            .where(OpportunityDecisionSnapshot.run_id == run_id)
            .order_by(OpportunityDecisionSnapshot.id)
        ).scalars().all()
    items = []
    mismatches = 0
    for row in rows:
        try:
            evidence = json.loads(row.evidence or "{}")
        except Exception:
            evidence = {}
        replayed = replay_decision(row.stage, evidence)
        matches = replayed == row.decision
        mismatches += 0 if matches else 1
        items.append({
            "snapshot_id": row.snapshot_id, "stage": row.stage, "symbol": row.symbol,
            "theme": row.source_theme, "archived": row.decision, "replayed": replayed,
            "matches": matches, "evidence": evidence,
        })
    return {"run_id": run_id, "records": len(items), "mismatches": mismatches, "items": items}


def pending_symbols(trade_date: str, session_factory=None) -> set[str]:
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(OpportunityDecisionSnapshot.symbol)
            .join(OpportunityOutcomeLabel,
                  OpportunityOutcomeLabel.snapshot_id == OpportunityDecisionSnapshot.snapshot_id)
            .where(OpportunityDecisionSnapshot.trade_date == trade_date,
                   OpportunityOutcomeLabel.state == "pending")
        ).scalars().all()
    return set(rows)


def label_trade_date(trade_date: str, close_by_symbol: dict[str, float], session_factory=None) -> dict:
    """Attach D0 close labels; missing closes stay pending and can be retried."""
    sf = session_factory or get_session_factory()
    labeled = unknown = pending = 0
    with sf() as db:
        rows = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.trade_date == trade_date,
                   OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
                   OpportunityOutcomeLabel.state == "pending")
        ).all()
        for outcome, snapshot in rows:
            reference = outcome.reference_price or snapshot.entry_price
            if reference is None or reference <= 0:
                outcome.state = "unknown"
                outcome.label = "unknown"
                outcome.reason = "决策时点价格缺失，不能计算收益"
                outcome.labeled_at = utcnow()
                unknown += 1
                continue
            close = close_by_symbol.get(snapshot.symbol)
            if close is None or close <= 0:
                pending += 1
                continue
            ret = round((float(close) / float(reference) - 1) * 100, 2)
            outcome.state = "labeled"
            outcome.reference_price = float(reference)
            outcome.outcome_price = float(close)
            outcome.return_pct = ret
            outcome.label = "positive" if ret >= 0 else ("flat" if ret >= -2.0 else "negative")
            outcome.reason = f"决策时点价至收盘 {ret:+.2f}%"
            outcome.labeled_at = utcnow()
            labeled += 1
        db.commit()
    return {"trade_date": trade_date, "labeled": labeled, "unknown": unknown, "pending": pending}


def learning_summary(trade_date: str, session_factory=None) -> dict:
    sf = session_factory or get_session_factory()
    with sf() as db:
        snapshots = db.execute(
            select(OpportunityDecisionSnapshot).where(
                OpportunityDecisionSnapshot.trade_date == trade_date
            )
        ).scalars().all()
        outcomes = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot.stage)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.trade_date == trade_date)
        ).all()
    stage_counts = Counter(s.stage for s in snapshots)
    decision_counts = Counter(f"{s.stage}:{s.decision}" for s in snapshots)
    state_counts = Counter(o.state for o, _stage in outcomes)
    eligible = len(outcomes)
    labeled = state_counts.get("labeled", 0)
    return {
        "trade_date": trade_date,
        "snapshots": len(snapshots),
        "runs": len({s.run_id for s in snapshots}),
        "stages": {stage: stage_counts.get(stage, 0) for stage in STAGES},
        "decisions": dict(sorted(decision_counts.items())),
        "outcomes": dict(sorted(state_counts.items())),
        "label_coverage": round(labeled / eligible, 4) if eligible else None,
        "note": "样本不足时仅报告覆盖率与事实分布，不据此晋级策略",
    }
