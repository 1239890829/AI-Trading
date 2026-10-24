"""The real confirm producer keeps the high gate and persists its actual refusal."""
import asyncio
from datetime import datetime
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.models.agent import AgentTriage
from app.models.alert import AlertEvent
from app.models.paper import PaperAccount, PaperOrder
from app.models.watchlist import Base
from app.paper.engine import PaperTradingEngine
from app.picks import morning_brief, position_engine, watcher
from app.repositories.alert_repo import AlertRepository

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=BJ_TZ)


@pytest.fixture
def confirm(tmp_path, monkeypatch):
    assert AgentTriage.__tablename__
    sql = create_engine(f"sqlite:///{tmp_path / 'confirm.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sql)
    sf = sessionmaker(bind=sql, expire_on_commit=False)
    monkeypatch.setattr(watcher, "get_session_factory", lambda: sf)
    monkeypatch.setattr(watcher, "beijing_now", lambda: NOW)
    monkeypatch.setattr(position_engine, "beijing_now", lambda: NOW)
    monkeypatch.setattr(position_engine, "_PLAN_DIR", tmp_path / "plans")
    monkeypatch.setattr(position_engine, "_today_gate_and_phase", lambda: ({}, "修复"))
    monkeypatch.setattr(morning_brief, "append_alert", lambda *_: True)
    monkeypatch.setattr(morning_brief, "brief_for_today", lambda: ("20261009", None))
    monkeypatch.setattr("app.picks.watch_ledger.record_sighting", lambda **_: None)
    class Registry:
        async def dispatch(self, *_args, **_kwargs):
            return ["in_app"]
    monkeypatch.setattr(watcher, "get_notifier_registry", lambda: Registry())
    async def forbidden_quote(_):
        raise AssertionError("missing confirm certainty must never reach the financial executor")
    paper = PaperTradingEngine(sf, forbidden_quote)
    repo = AlertRepository(sf)
    svc = SimpleNamespace(snapshot=[{"symbol": "600001", "name": "合成确认", "price": 10, "change_pct": 7}])
    app = SimpleNamespace(state=SimpleNamespace(paper=paper, alert_repo=repo, snapshot_service=svc))
    tracker = watcher.DirectionTracker(direction="隔离方向")
    alert = tracker.step({"pct": 2., "limit_up": 3, "max_boards": 3, "leader_pct": 7.,
        "volume_ratio": 1.8, "leader_symbol": "600001", "leader_name": "合成确认"},
        {"phase": "发酵", "promo_percentile": 60.}, 590)[0]
    yield SimpleNamespace(sf=sf, app=app, alert=alert, repo=repo)
    sql.dispose()


def persisted(confirm):
    with confirm.sf() as db:
        row = db.query(AlertEvent).one()
        assert db.query(PaperOrder).count() == db.query(PaperAccount).count() == 0
        return row.id, json.loads(row.snapshot)["paper_execution"]


def test_real_confirm_without_certainty_records_refusal_and_original_identity(confirm):
    assert confirm.alert["meta"]["strength"] == 1.0
    assert "certainty_level" not in confirm.alert["meta"]
    assert asyncio.run(watcher.dispatch_alert(confirm.app, confirm.alert)) is True
    event_id, saved = persisted(confirm)
    assert saved["state"] == "rejected" and saved["trace_state"] == "recorded"
    assert saved["trigger_event_id"] == event_id and saved["triggered_at"] == confirm.alert["at"]
    assert saved["certainty_level"] is None and "certainty_level" in saved["unknown_fields"]
    assert "未知 ≠ 高" in saved["reason"]
    decision = position_engine.load_plan()["decisions"][0]
    assert decision["action"] == "open_rejected" and decision["trigger_event_id"] == event_id
    assert decision["triggered_at"] == confirm.alert["at"]
    assert position_engine.reconcile_position_open_events() == 0


def test_confirm_failure_is_recorded_unknown_without_replaying(confirm, monkeypatch):
    async def failed(*_args, **_kwargs):
        raise RuntimeError("isolated attempt failure")
    monkeypatch.setattr(position_engine, "maybe_open", failed)
    assert asyncio.run(watcher.dispatch_alert(confirm.app, confirm.alert)) is True
    _, saved = persisted(confirm)
    assert saved["state"] == "unknown" and "RuntimeError" in saved["reason"]
    assert position_engine.load_plan()["decisions"][0]["action"] == "open_unknown"


def test_confirm_plan_record_failure_remains_explicit_unknown_in_original_event(confirm, monkeypatch):
    def failed(_):
        raise OSError("isolated plan write failure")
    monkeypatch.setattr(position_engine, "save_plan", failed)
    assert asyncio.run(watcher.dispatch_alert(confirm.app, confirm.alert)) is True
    _, saved = persisted(confirm)
    assert saved["state"] == "rejected" and saved["trace_state"] == "unknown"


def test_original_event_must_commit_before_any_confirm_attempt(confirm, monkeypatch):
    attempted = []
    async def attempted_open(*_args, **_kwargs):
        attempted.append(True)
        return {"opened": False, "reason": "not reached"}
    def failed(*_args, **_kwargs):
        raise OSError("isolated source commit failure")
    monkeypatch.setattr(position_engine, "maybe_open", attempted_open)
    monkeypatch.setattr(confirm.repo, "record_trigger", failed)
    with pytest.raises(OSError):
        asyncio.run(watcher.dispatch_alert(confirm.app, confirm.alert))
    assert attempted == []
