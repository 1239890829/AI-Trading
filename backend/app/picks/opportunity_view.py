"""Read-only projection of existing decisions; never a new selector or order gate."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date

from sqlalchemy import func, select

from app.core.db import get_session_factory
from app.models.opportunity_learning import OpportunityDecisionRun as Run
from app.models.opportunity_learning import OpportunityDecisionSnapshot as Snapshot

VERSION = "opportunity-view-v1"
DISCLAIMER = "候选与研究观察不代表可执行或实际成交，不构成买卖建议。"


def _object(raw: str) -> dict:
    try:
        value = json.loads(raw)
        json.dumps(value, allow_nan=False)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _identity(value: dict, prefix: str) -> str:
    return prefix + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                             default=str).encode()).hexdigest()[:32]


def _material_evidence(evidence: dict) -> dict:
    # Snapshot refresh metadata is retained in display evidence, but is not a new judgment.
    material = json.loads(json.dumps(evidence))
    for item in material.values():
        facts = item.get("facts") if isinstance(item.get("facts"), dict) else {}
        for key in ("snapshot_as_of", "checked_at", "version"):
            facts.pop(key, None)
        seal = item.get("seal_state") if isinstance(item.get("seal_state"), dict) else {}
        for key in ("snapshot_as_of", "checked_at", "as_of"):
            seal.pop(key, None)
    return material


def _hypothesis(rows: list[Snapshot], first_seen: str) -> dict:
    by_stage = {r.stage: r for r in rows}
    row = by_stage.get("notification") or by_stage.get("rank") or by_stage.get("hard_gate") or rows[0]
    evidence = {stage: _object(r.evidence) for stage, r in by_stage.items()}
    decision = row.decision
    state = "triggered" if decision in {"ranked", "eligible", "notified", "suppressed"} else "waiting"
    if decision == "included":
        state = "observing"
    if row.data_state != "ready" or decision == "unknown":
        state = "unknown"
    raw_execution = evidence.get("notification", {}).get("execution_contract")
    execution = raw_execution if isinstance(raw_execution, dict) else None
    identity = {"date": row.trade_date, "symbol": row.symbol,
                "scenario": row.scenario, "theme": row.source_theme}
    decision_id = (execution or {}).get("decision_id") or _identity(identity, "OD-")
    # Material decisions/facts, not polling timestamps or dispatch receipts, define versions.
    material_evidence = _material_evidence(evidence)
    material = {"contract": VERSION, "identity": identity,
                "strategy": row.strategy_version, "feature": row.feature_version,
                "data_state": row.data_state, "price": row.entry_price,
                "stages": {stage: {"decision": r.decision, "evidence": material_evidence[stage]}
                           for stage, r in by_stage.items()}}
    version = (execution or {}).get("decision_version") or _identity(material, "ODV-")
    reasons = [str(e.get(k)) for e in evidence.values()
               for k in ("filter_reason", "linkage_basis", "gate_reason") if e.get(k)]
    unknowns = []
    price = row.entry_price
    if price is not None and (not math.isfinite(price) or price <= 0):
        price = None
        state = "unknown"
        unknowns.append("原始参考价无效")
    if row.data_state != "ready":
        unknowns.append("来源或行情质量未就绪")
    if not reasons:
        unknowns.append("缺少已记录的入选依据")
    if any(not e for e in evidence.values()):
        state = "unknown"
        unknowns.append("原始证据为空或格式损坏")
    if not execution:
        unknowns.append("没有执行决策契约；有效进入区间、失效时点及动作前重检待核")
    kb = _object(row.kb_refs)
    gates = (execution or {}).get("gate_inputs")
    gates = gates if isinstance(gates, dict) else {}
    return {
        "opportunity_id": _identity(identity, "OP-"), "decision_id": decision_id,
        "decision_version": version, "scenario": row.scenario, "source_theme": row.source_theme,
        "first_seen": first_seen, "as_of": row.as_of.isoformat(), "state": state,
        "data_state": row.data_state, "source": "opportunity_decision_snapshot",
        "strategy_version": row.strategy_version, "feature_version": row.feature_version,
        "stages": {stage: r.decision for stage, r in by_stage.items()},
        "reasons": list(dict.fromkeys(reasons)), "unknowns": unknowns,
        "evidence": evidence, "kb_refs": kb,
        "counterevidence": [e.get("vetoes") for e in evidence.values() if e.get("vetoes")],
        "reference": {"price": price, "semantics": "reference_only_not_fill"},
        "execution_contract": execution, "actionable": False,
        "entry_conditions": gates,
        "execution_blocker": "只读历史决定；实际动作必须由执行owner同版重检与准入",
        "expires_at": None, "history_run_id": row.run_id,
        "snapshot_refs": [r.snapshot_id for r in rows],
        "replay_url": f"/api/picks/opportunity-learning/replay/{row.run_id}",
    }


def read_opportunities(trade_date: str, session_factory=None) -> dict:
    """Use latest run per scenario, even when that run has zero candidates.

    Past snapshots stay immutable. New empty/rejected runs cannot resurrect an old
    accepted candidate. Outcome labels are never used to classify, order or populate these hypotheses.
    """
    date.fromisoformat(trade_date)
    sf = session_factory or get_session_factory()
    with sf() as db:
        ranked_runs = select(Run.run_id, func.row_number().over(
            partition_by=Run.scenario, order_by=(Run.as_of.desc(), Run.run_id.desc())
        ).label("position")).where(Run.trade_date == trade_date).subquery()
        runs = list(db.scalars(select(Run).join(ranked_runs, Run.run_id == ranked_runs.c.run_id)
                              .where(ranked_runs.c.position == 1)).all())
        latest = {run.scenario: run for run in runs}
        rows = list(db.scalars(select(Snapshot).where(
            Snapshot.run_id.in_([r.run_id for r in latest.values()])
        ).order_by(Snapshot.symbol, Snapshot.source_theme, Snapshot.id)).all()) if latest else []
        firsts = db.execute(select(Snapshot.scenario, Snapshot.symbol, Snapshot.source_theme,
                                  func.min(Snapshot.as_of)).where(Snapshot.trade_date == trade_date)
                            .group_by(Snapshot.scenario, Snapshot.symbol, Snapshot.source_theme)).all()
    first_by = {(s, symbol, theme): when.isoformat() for s, symbol, theme, when in firsts}
    grouped = {}
    for row in rows:
        grouped.setdefault((row.scenario, row.symbol, row.source_theme), []).append(row)
    cards = {}
    for key, group in grouped.items():
        row = group[0]
        card = cards.setdefault(row.symbol, {"symbol": row.symbol, "name": row.name, "hypotheses": []})
        card["hypotheses"].append(_hypothesis(group, first_by[key]))
    from app.research.leader_followthrough import summary

    research = summary(trade_date, session_factory=sf)
    for observed in research["cards"]:
        card = cards.setdefault(observed["symbol"], {
            "symbol": observed["symbol"], "name": observed["name"], "hypotheses": [],
        })
        identity = {"symbol": observed["symbol"], "first_seen": observed["first_seen"],
                    "scenario": "leader_research"}
        card["hypotheses"].append({
            "opportunity_id": _identity(identity, "RO-"),
            "decision_id": None, "decision_version": None,
            "observation_id": observed["observation_id"],
            "scenario": "leader_research", "routes": observed["routes"], "source_theme": "",
            "first_seen": observed["first_seen"], "as_of": observed["source_as_of"],
            "state": "expired" if observed["expired"] else "stale" if observed["stale"] else observed["state"],
            "data_state": "research_only", "source": observed["source"],
            "reasons": observed["reasons"], "unknowns": observed["unknowns"],
            "counterevidence": [observed["invalidation"]],
            "event_refs": observed["event_refs"],
            "reference": {"price": observed["reference_price"], "semantics": "reference_only_not_fill"},
            "actionable": False, "execution_contract": None,
            "execution_blocker": observed["entry_state"],
            "expires_at": None, "snapshot_refs": [observed["observation_id"]],
        })
    return {
        "trade_date": trade_date, "contract_version": VERSION,
        "state": ("ready" if cards else "unavailable" if any(r.data_state != "ready" for r in runs)
                  else "collected_empty" if latest or research["state"] == "collected_empty" else "not_collected"),
        "time_basis": "Asia/Shanghai",
        "research_state": research["state"],
        "cards": list(cards.values()), "disclaimer": DISCLAIMER,
        "runs": [{"run_id": r.run_id, "scenario": r.scenario, "as_of": r.as_of.isoformat(),
                  "data_state": r.data_state, "records": r.records_total,
                  "gate_counts": _object(r.linkage_stats).get("theme_gate_counts"),
                  "summary": _object(r.summary)} for r in latest.values()],
        "coverage": "仅已有运行头对应的持久决定；未采集路径不等于没有机会；不重新扫描潜伏/接力/RPS",
        "ordering": "证券身份稳定排序，不是新增推荐排名",
    }
