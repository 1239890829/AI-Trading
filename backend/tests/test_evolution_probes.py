from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.freshness import Freshness
from app.core.scheduler import SchedulerRegistry
from app.core.ttl_cache import TTLCache
from app.models.agent import AgentAgenda
from app.models.alert import AlertEvent, AlertRule
from app.models.watch_ledger import WatchLedger
from app.models.watchlist import Base
from app.services.evolution_probes import collect_vulnerability_evidence


def _sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'probes.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class _App:
    def __init__(self, state=None, suffix=""):
        self.state = state or SimpleNamespace()
        self._suffix = suffix

    def openapi(self):
        return {"paths": {f"/api/health{self._suffix}": {"get": {"operationId": "health"}}}}


def test_scheduler_probe_reads_registry_not_scheduler_self_report(tmp_path):
    sf = _sf(tmp_path)
    reg = SchedulerRegistry()
    rec = reg.add("dead-task", lambda: asyncio.sleep(0))
    rec.finished_reason = "exception"
    app = _App(SimpleNamespace(schedulers=reg))
    out = collect_vulnerability_evidence(sf, app, now=datetime(2026, 9, 16, 9))
    probe = next(p for p in out["probes"] if p["name"] == "scheduler_liveness")
    assert probe["state"] == "issue" and probe["evidence"]["dead"] == ["dead-task"]


def test_cache_probe_archives_singleflight_coalescing(tmp_path):
    sf = _sf(tmp_path)
    cache = TTLCache("probe.singleflight", 60)
    calls = 0

    async def load():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return {"ok": True}

    async def scenario():
        await asyncio.gather(*(cache.get_or_set("same", load) for _ in range(5)))

    asyncio.run(scenario())
    out = collect_vulnerability_evidence(sf, _App(), now=datetime(2026, 9, 16, 9))
    probe = next(p for p in out["probes"] if p["name"] == "cache_singleflight")
    row = next(c for c in probe["evidence"]["caches"] if c["name"] == "probe.singleflight")
    assert calls == row["factory_calls"] == 1
    assert row["coalesced_waiters"] == 4 and row["same_key_parallel_violations"] == 0


def test_api_contract_uses_previous_archived_fingerprint(tmp_path):
    sf = _sf(tmp_path)
    first = collect_vulnerability_evidence(sf, _App(), now=datetime(2026, 9, 16, 9))
    fingerprint = first["api_contract"]["fingerprint"]
    with sf() as db:
        db.add(AgentAgenda(date="2026-09-15", status="ready", inputs=json.dumps({
            "vulnerability_probes": {"api_contract": {"fingerprint": fingerprint}}
        })))
        db.commit()
    same = collect_vulnerability_evidence(sf, _App(), now=datetime(2026, 9, 16, 9))
    changed = collect_vulnerability_evidence(sf, _App(suffix="-changed"), now=datetime(2026, 9, 16, 9))
    assert next(p for p in same["probes"] if p["name"] == "api_contract")["state"] == "ok"
    assert next(p for p in changed["probes"] if p["name"] == "api_contract")["state"] == "issue"


def test_stale_consumption_requires_decision_after_expiry(tmp_path):
    sf = _sf(tmp_path)
    as_of = datetime(2026, 9, 16, 0, 0, tzinfo=timezone.utc)
    svc = SimpleNamespace(
        poll_interval=60,
        freshness=lambda: Freshness.stale(reason="停更", as_of=as_of, age_seconds=600, source="snapshot"),
    )
    with sf() as db:
        db.add(WatchLedger(trade_date="20260916", symbol="600000", created_at=datetime(2026, 9, 16, 0, 4)))
        db.commit()
    out = collect_vulnerability_evidence(sf, _App(SimpleNamespace(snapshot_service=svc)),
                                         now=datetime(2026, 9, 16, 9))
    probe = next(p for p in out["probes"] if p["name"] == "stale_data_consumption")
    assert probe["state"] == "issue" and probe["evidence"]["decisions_after_expiry"] == 1


def test_alert_probe_separates_duplicate_fault_from_zero_trigger_signal(tmp_path):
    sf = _sf(tmp_path)
    now = datetime(2026, 9, 16, 9)
    with sf() as db:
        duplicate = AlertRule(name="dup", enabled=1, cooldown_seconds=300,
                              created_at=now - timedelta(days=10))
        silent = AlertRule(name="silent", enabled=1, cooldown_seconds=300,
                           created_at=now - timedelta(days=10))
        db.add_all([duplicate, silent]); db.flush()
        db.add_all([
            AlertEvent(rule_id=duplicate.id, symbol="600000", trigger_value=1, threshold=1,
                       triggered_at=now - timedelta(minutes=2)),
            AlertEvent(rule_id=duplicate.id, symbol="600001", trigger_value=1, threshold=1,
                       triggered_at=now - timedelta(minutes=1)),
        ])
        db.commit()
        dup_id, silent_id = duplicate.id, silent.id
    out = collect_vulnerability_evidence(sf, _App(), now=now)
    probe = next(p for p in out["probes"] if p["name"] == "alert_effectiveness")
    assert probe["state"] == "issue"
    assert probe["evidence"]["duplicate_rule_ids"] == [dup_id]
    assert silent_id in probe["evidence"]["zero_trigger_rule_ids"]
