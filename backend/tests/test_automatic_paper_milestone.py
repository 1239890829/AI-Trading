"""Real isolated paper orders link to exact saved buy-point/selection facts."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.models.agent import AgentTriage
from app.models.alert import AlertEvent
from app.models.notification_outbox import BuyPointConsumption as Progress
from app.models.opportunity_learning import OpportunityDecisionSnapshot as Decision
from app.models.paper import PaperActionReceipt, PaperOrder, PaperPosition
from app.models.watchlist import Base
from app.paper.engine import PaperTradingEngine
from app.picks import buy_point_consumers as consumers, position_engine
from app.picks.buy_point import _buy_point_dedup_key, _execution_ref
from app.picks.opportunity_learning import build_execution_contract
from app.repositories.alert_repo import AlertRepository
from app.schemas.market import Quote
from app.services import selection_workflow as workflow
from app.services.selection_notifications import SELECTION_RULE, selection_dedup_key

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=BJ_TZ)
DAY = NOW.date().isoformat()


@pytest.fixture
def env(tmp_path, monkeypatch):
    assert AgentTriage.__tablename__
    engine = create_engine(f"sqlite:///{tmp_path / 'auto.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(consumers, "beijing_now", lambda: NOW)
    monkeypatch.setattr("app.market.trade_calendar.in_trading_window", lambda *_: True)
    monkeypatch.setattr("app.paper.engine.beijing_now", lambda: NOW)
    monkeypatch.setattr("app.paper.engine._today", lambda: NOW.strftime("%Y%m%d"))
    monkeypatch.setattr(position_engine, "beijing_now", lambda: NOW)
    monkeypatch.setattr(position_engine, "_PLAN_DIR", tmp_path / "plans")
    monkeypatch.setattr(position_engine, "_today_gate_and_phase", lambda: ({}, "发酵"))
    # The old brief/source projection is unrelated to the new station-only proof.
    monkeypatch.setattr(position_engine, "_publish_open_decision", lambda *a: None)
    def order_clock(mapper, connection, order):
        order.created_at = order.updated_at = NOW.astimezone(timezone.utc).replace(tzinfo=None)
    event.listen(PaperOrder, "before_insert", order_clock)
    quote_state = {"price": 10.0}
    async def quote(symbol):
        return Quote(symbol=symbol, price=quote_state["price"], source="fixture", quality="high", data_timestamp=NOW,
                     limit_up_price=11, limit_down_price=9)
    async def days():
        return [NOW.date()]
    risk = SimpleNamespace(hub=SimpleNamespace(get_quotes=lambda: []), check_order=lambda **kw: {"allowed": True})
    paper = PaperTradingEngine(sf, quote, days, risk_engine=risk)
    paper.ensure_account()
    app = SimpleNamespace(state=SimpleNamespace(paper=paper, hub=SimpleNamespace()))
    generated = (NOW - timedelta(minutes=5)).isoformat()
    item = {"symbol": "600001", "price": 10, "confidence": {"tier": "strong"}, "buy_range": {"low": 9.5, "high": 10.2}}
    contract = build_execution_contract(trade_date=DAY, symbol="600001", item=item,
        executable_snapshot={"price": 10, "state": "ready", "source": "fixture", "as_of": NOW.isoformat()},
        gate_decision="passed", gate_reason=None, pick_generated_at=generated)
    execution = _execution_ref(contract)
    source_version = "v-original-fixture"
    repo = AlertRepository(sf)
    selection_rule = repo.create_rule(name=SELECTION_RULE, condition_type="source_event", channels=["in_app"])
    origin, _ = repo.record_trigger_once(selection_rule.id, "600001", 0, 0,
        dedup_key=selection_dedup_key(DAY, "600001"), snapshot={"kind": "selection", "name": "甲公司", "trade_date": DAY,
            "selection_source": "daily", "source_version": source_version, "source_id": "fixture",
            "source_as_of": generated, "selection_evidence": {}})
    buy_rule = repo.create_rule(name="__picks_buy_point__", condition_type="source_event", channels=["in_app"])
    key = _buy_point_dedup_key(DAY, "600001")
    buy, _ = repo.record_trigger_once(buy_rule.id, "600001", 0, 0, dedup_key=key,
        snapshot={"kind": "buy_point", "name": "甲公司", "trade_date": DAY, "execution_ref": execution})
    alert = {"kind": "buy_point", "symbol": "600001", "name": "甲公司", "at": NOW.isoformat(),
        "meta": {"trade_date": DAY, "execution_ref": execution,
                 "selection_ref": f"daily:{DAY}:600001", "selection_source_version": source_version}}
    with sf() as db:
        db.add(Decision(snapshot_id="a" * 40, run_id="fixture", trade_date=DAY, as_of=NOW.replace(tzinfo=None),
            scenario="buy_point", stage="notification", symbol="600001", decision="eligible", data_state="ready",
            strategy_version="fixture", feature_version="fixture", evidence=json.dumps({"execution_contract": contract})))
        db.add(Progress(event_key=key, event_id=buy.id, consumer="position", state="pending", payload=json.dumps({"alert": alert, "snapshot": {}}),
            expires_at_ms=int(time.time() * 1000) + 60_000, updated_at_ms=int(time.time() * 1000)))
        db.commit()
    yield SimpleNamespace(sf=sf, engine=engine, app=app, paper=paper, key=key, origin=origin,
                          buy=buy, quote=quote_state, contract=contract)
    event.remove(PaperOrder, "before_insert", order_clock)
    engine.dispose()


def milestones(env):
    with env.sf() as db:
        return [json.loads(row.snapshot) for row in db.scalars(select(AlertEvent)) if json.loads(row.snapshot).get("kind") == "paper_status"]


def test_saved_auto_fill_notifies_once_without_action_receipt_or_second_order(env):
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    with env.sf() as db:
        progress = db.get(Progress, (env.key, "position"))
        payload = json.loads(progress.payload)
        assert progress.state == "done" and payload["execution_result"]["opened"] is True
        assert payload["paper_projection"]["state"] == "recorded"
        assert db.query(PaperOrder).count() == db.query(PaperPosition).count() == 1
        assert db.query(PaperActionReceipt).count() == 0
        order = db.query(PaperOrder).one()
        assert order.status == "filled" and order.filled_price == 10
    first = milestones(env)
    assert len(first) == 1 and first[0]["paper_execution"]["status"] == "filled"
    assert first[0]["paper_execution"]["decision_version"] == env.contract["decision_version"]
    assert first[0]["paper_execution"]["reason"] is None
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    again = workflow.record_paper_status(None, None, order.id, env.sf)
    assert again["state"] == "recorded" and again["created"] is False and milestones(env) == first
    with env.sf() as db:
        assert db.query(PaperOrder).count() == 1 and db.query(PaperPosition).one().quantity == order.quantity


def test_auto_pending_then_real_match_projects_new_status_on_same_order(env):
    env.quote["price"] = 10.1
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    assert [m["paper_execution"]["status"] for m in milestones(env)] == ["pending"]
    env.quote["price"] = 10
    assert asyncio.run(env.paper.match_pending()) == 0
    assert {m["paper_execution"]["status"] for m in milestones(env)} == {"pending", "filled"}
    with env.sf() as db:
        assert db.query(PaperOrder).count() == 1 and db.query(PaperPosition).count() == 1
        assert db.query(PaperActionReceipt).count() == 0


def test_projection_failure_retries_saved_result_without_repeating_paper_action(env, monkeypatch):
    original = workflow.record_automatic_paper_status
    calls = []
    def unavailable(**kwargs):
        calls.append("projection")
        if len(calls) == 1:
            raise RuntimeError("isolated notification commit failure")
        return original(**kwargs)
    monkeypatch.setattr(workflow, "record_automatic_paper_status", unavailable)
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    with env.sf() as db:
        assert db.query(PaperOrder).count() == 1
        assert json.loads(db.get(Progress, (env.key, "position")).payload)["paper_projection"]["state"] == "pending"
    assert milestones(env) == []
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    assert len(milestones(env)) == 1 and len(calls) == 2
    with env.sf() as db:
        assert db.query(PaperOrder).count() == 1 and db.query(PaperPosition).count() == 1


@pytest.mark.parametrize("change", ["decision", "selection_version", "scope", "source_event", "day", "unknown", "no_order", "wrong_symbol"])
def test_damaged_saved_link_cannot_borrow_same_stock_day_or_order(env, monkeypatch, change):
    # Preserve the real execution but stop the projection to inject one binding failure.
    monkeypatch.setattr(consumers, "_project_positions", _no_projection)
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    with env.sf() as db:
        progress = db.get(Progress, (env.key, "position"))
        payload = json.loads(progress.payload)
        order = db.query(PaperOrder).one()
        if change == "decision":
            payload["execution_result"]["decision_version"] = "different"
        elif change == "selection_version":
            payload["alert"]["meta"]["selection_source_version"] = "different"
        elif change == "scope":
            order.scope = "shadow"
        elif change == "source_event":
            progress.event_id = env.origin.id
        elif change == "day":
            payload["alert"]["meta"]["selection_ref"] = "daily:2026-10-08:600001"
        elif change == "unknown":
            payload["execution_result"]["execution_unknown"] = True
        elif change == "no_order":
            payload["execution_result"].pop("order_id")
        elif change == "wrong_symbol":
            order.symbol = "600002"
        progress.payload = json.dumps(payload)
        db.commit()
    assert workflow.record_automatic_paper_status(event_key=env.key, session_factory=env.sf)["state"] in {"rejected", "unavailable"}
    assert milestones(env) == []
    with env.sf() as db:
        assert db.query(PaperOrder).count() == 1 and db.query(PaperActionReceipt).count() == 0


async def _no_projection(*args, **kwargs):
    pass


def test_uncommitted_consumer_outcome_cannot_publish_completion_or_repeat_order(env):
    def fail_done(conn, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE buy_point_consumption") and "done" in parameters:
            raise RuntimeError("isolated consumer outcome commit failure")
    event.listen(env.engine, "before_cursor_execute", fail_done)
    try:
        with pytest.raises(RuntimeError, match="outcome commit failure"):
            asyncio.run(consumers.consume_pending(env.app, env.sf))
    finally:
        event.remove(env.engine, "before_cursor_execute", fail_done)
    assert milestones(env) == []
    with env.sf() as db:
        assert db.query(PaperOrder).count() == 1
        progress = db.get(Progress, (env.key, "position"))
        assert progress.state == "running" and "execution_result" not in json.loads(progress.payload)
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    with env.sf() as db:
        assert db.query(PaperOrder).count() == 1
    assert milestones(env) == []
