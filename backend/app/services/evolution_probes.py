"""Read-only vulnerability probes consumed by the daily evolution agenda.

The probes compare independent observation surfaces; they never repair state or
execute code.  Their payload is archived inside ``AgentAgenda.inputs``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.bjtime import beijing_now_naive
from app.core.ttl_cache import live_caches
from app.models.agent import AgentAgenda
from app.models.alert import AlertEvent, AlertRule
from app.models.watch_ledger import WatchLedger


def _probe(name: str, state: str, evidence: dict[str, Any], note: str = "") -> dict[str, Any]:
    return {"name": name, "state": state, "evidence": evidence, "note": note}


def _scheduler_probe(app: Any | None) -> dict[str, Any]:
    reg = getattr(getattr(app, "state", None), "schedulers", None)
    if reg is None:
        return _probe("scheduler_liveness", "unknown", {}, "当前调用未提供调度注册表")
    dead = sorted(reg.dead_names())
    failing = sorted(reg.failing_names())
    return _probe(
        "scheduler_liveness", "issue" if dead or failing else "ok",
        {"dead": dead, "failing": failing, "snapshot": reg.snapshot()},
    )


def _cache_probe() -> dict[str, Any]:
    caches = live_caches()
    violations = [c["name"] for c in caches if c.get("same_key_parallel_violations", 0)]
    return _probe(
        "cache_singleflight", "issue" if violations else "ok",
        {"violations": violations, "caches": caches},
        "factory_calls 是真实回源次数；coalesced_waiters 是被单航班合并的并发请求",
    )


def _contract_probe(session_factory, app: Any | None) -> dict[str, Any]:
    if app is None or not callable(getattr(app, "openapi", None)):
        return _probe("api_contract", "unknown", {}, "当前调用未提供 ASGI app")
    schema = app.openapi()
    surface = [
        (method.upper(), path, op.get("operationId", ""))
        for path, methods in schema.get("paths", {}).items()
        for method, op in methods.items()
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    ]
    surface.sort()
    fingerprint = hashlib.sha256(
        json.dumps(surface, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    previous = None
    with session_factory() as db:
        rows = db.execute(select(AgentAgenda.inputs).order_by(AgentAgenda.date.desc()).limit(30)).scalars()
        for raw in rows:
            try:
                previous = json.loads(raw or "{}").get("vulnerability_probes", {}).get("api_contract", {}).get("fingerprint")
            except (TypeError, json.JSONDecodeError):
                continue
            if previous:
                break
    state = "baseline" if previous is None else ("ok" if previous == fingerprint else "issue")
    return _probe(
        "api_contract", state,
        {"fingerprint": fingerprint, "previous_fingerprint": previous,
         "operations": len(surface), "paths": len(schema.get("paths", {}))},
        "首次采集只建立基线；后续指纹变化必须结合 PR/版本记录复核",
    )


def _stale_consumption_probe(session_factory, app: Any | None) -> dict[str, Any]:
    svc = getattr(getattr(app, "state", None), "snapshot_service", None)
    if svc is None or not callable(getattr(svc, "freshness", None)):
        return _probe("stale_data_consumption", "unknown", {}, "缺少快照服务新鲜度事实面")
    fresh = svc.freshness()
    as_of = fresh.as_of
    if fresh.state == "ready":
        return _probe("stale_data_consumption", "ok", {"freshness": fresh.model_dump(mode="json"), "decisions_after_expiry": 0})
    if as_of is None:
        return _probe("stale_data_consumption", "unknown", {"freshness": fresh.model_dump(mode="json")}, "无 as_of，无法证明消费发生在过期之后")
    ref = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    expiry = ref + timedelta(seconds=float(getattr(svc, "poll_interval", 60.0)) * 3)
    cutoff = expiry.replace(tzinfo=None)
    with session_factory() as db:
        rows = db.execute(
            select(WatchLedger.id, WatchLedger.trade_date, WatchLedger.symbol, WatchLedger.created_at)
            .where(WatchLedger.created_at > cutoff).order_by(WatchLedger.created_at.desc()).limit(20)
        ).all()
    evidence = {
        "freshness": fresh.model_dump(mode="json"),
        "expiry": expiry.isoformat(),
        "decisions_after_expiry": len(rows),
        "examples": [{"id": r.id, "date": r.trade_date, "symbol": r.symbol,
                      "created_at": r.created_at.isoformat()} for r in rows[:5]],
    }
    return _probe("stale_data_consumption", "issue" if rows else "ok", evidence)


def _alert_probe(session_factory, now: datetime) -> dict[str, Any]:
    cutoff = now - timedelta(days=7)
    with session_factory() as db:
        rules = db.execute(select(AlertRule).where(AlertRule.enabled == True)).scalars().all()  # noqa: E712
        events = db.execute(
            select(AlertEvent).where(AlertEvent.triggered_at >= cutoff)
            .order_by(AlertEvent.rule_id, AlertEvent.triggered_at)
        ).scalars().all()
    by_rule: dict[int, list[AlertEvent]] = {}
    for event in events:
        by_rule.setdefault(event.rule_id, []).append(event)
    duplicate_rule_ids: list[int] = []
    zero_trigger_rule_ids: list[int] = []
    for rule in rules:
        own = by_rule.get(rule.id, [])
        if rule.created_at <= cutoff and not own:
            zero_trigger_rule_ids.append(rule.id)
        if rule.cooldown_seconds > 0 and any(
            (b.triggered_at - a.triggered_at).total_seconds() < rule.cooldown_seconds
            for a, b in zip(own, own[1:])
        ):
            duplicate_rule_ids.append(rule.id)
    evidence = {"window_days": 7, "enabled_rules": len(rules), "events": len(events),
                "duplicate_rule_ids": duplicate_rule_ids,
                "zero_trigger_rule_ids": zero_trigger_rule_ids}
    state = "issue" if duplicate_rule_ids else ("review" if zero_trigger_rule_ids else "ok")
    return _probe("alert_effectiveness", state, evidence,
                  "零触发只作为待复核信号，不单独判故障")


def collect_vulnerability_evidence(session_factory, app: Any | None = None,
                                   *, now: datetime | None = None) -> dict[str, Any]:
    """Collect five independent, read-only evidence groups for the agenda."""
    observed_at = now or beijing_now_naive()
    probes = [
        _scheduler_probe(app),
        _cache_probe(),
        _contract_probe(session_factory, app),
        _stale_consumption_probe(session_factory, app),
        _alert_probe(session_factory, observed_at),
    ]
    return {
        "available": any(p["state"] != "unknown" for p in probes),
        "observed_at": observed_at.isoformat(),
        "n_issues": sum(p["state"] == "issue" for p in probes),
        "issues": [p["name"] for p in probes if p["state"] == "issue"],
        "probes": probes,
        "api_contract": next(p["evidence"] for p in probes if p["name"] == "api_contract"),
    }
