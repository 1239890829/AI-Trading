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
from app.models.daily_pick import DailyPickSet
from app.models.notification_outbox import BuyPointConsumption as Progress
from app.models.opportunity_learning import OpportunityDecisionSnapshot as Decision
from app.models.paper import PaperAccount, PaperActionReceipt, PaperOrder, PaperPosition
from app.models.watchlist import Base
from app.paper.engine import PaperTradingEngine
from app.picks import buy_point_consumers as consumers, position_engine
from app.picks.buy_point import _buy_point_dedup_key, _execution_ref
from app.picks.opportunity_learning import build_execution_contract
from app.repositories.alert_repo import AlertRepository
from app.schemas.market import Quote
from app.services import selection_workflow as workflow
from app.services.selection_notifications import SELECTION_RULE, daily_selection_version, selection_dedup_key

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
    monkeypatch.setattr("app.paper.selection_action.beijing_now", lambda: NOW)
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
        if quote_state.get("on_quote"):
            quote_state.pop("on_quote")()
        return Quote(symbol=symbol, price=quote_state["price"], source="fixture", quality="high", data_timestamp=NOW,
                     limit_up_price=11, limit_down_price=9, prev_close=9.8,
                     change_pct=(quote_state["price"] / 9.8 - 1) * 100)
    async def days():
        if quote_state.get("on_calendar"):
            quote_state.pop("on_calendar")()
        return [NOW.date()]
    risk = SimpleNamespace(hub=SimpleNamespace(get_quotes=lambda: []), check_order=lambda **kw: {"allowed": True})
    paper = PaperTradingEngine(sf, quote, days, risk_engine=risk)
    paper.ensure_account()
    app = SimpleNamespace(state=SimpleNamespace(paper=paper, hub=SimpleNamespace()))
    generated = (NOW - timedelta(minutes=5)).isoformat()
    item = {"symbol": "600001", "price": 10, "score": 80, "confidence": {"tier": "strong"}, "buy_range": {"low": 9.8, "high": 10.2}}
    contract = build_execution_contract(trade_date=DAY, symbol="600001", item=item,
        executable_snapshot={"price": 10, "state": "ready", "source": "fixture", "as_of": NOW.isoformat()},
        gate_decision="passed", gate_reason=None, pick_generated_at=generated)
    execution = _execution_ref(contract)
    source_version = daily_selection_version(DAY, generated, [item])
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
        db.add(DailyPickSet(date=DAY, items=json.dumps([item]), meta=json.dumps({
            "generated_at": generated, "selection_version": source_version,
            "gate": {}, "market_phase": "发酵"})))
        db.add(Decision(snapshot_id="a" * 40, run_id="fixture", trade_date=DAY, as_of=NOW.replace(tzinfo=None),
            scenario="buy_point", stage="notification", symbol="600001", decision="eligible", data_state="ready",
            strategy_version="fixture", feature_version="fixture", evidence=json.dumps({"execution_contract": contract})))
        db.add(Progress(event_key=key, event_id=buy.id, consumer="position", state="pending", payload=json.dumps({"alert": alert, "snapshot": {}}),
            expires_at_ms=int(time.time() * 1000) + 60_000, updated_at_ms=int(time.time() * 1000)))
        db.commit()
    yield SimpleNamespace(sf=sf, engine=engine, app=app, paper=paper, key=key, origin=origin,
                          buy=buy, quote=quote_state, contract=contract, source_version=source_version)
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


@pytest.mark.parametrize("status", ["pending", "filled"])
@pytest.mark.parametrize("failure", ["load", "save", "event"])
def test_accepted_order_survives_secondary_projection_failure_without_repeating(env, monkeypatch, status, failure):
    if status == "pending":
        env.quote["price"] = 10.1
    def unavailable(*_args, **_kwargs):
        raise OSError("isolated secondary projection failure")
    monkeypatch.setattr(position_engine, {"load": "load_plan", "save": "save_plan", "event": "_publish_open_decision"}[failure], unavailable)
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    with env.sf() as db:
        order = db.query(PaperOrder).one()
        progress = db.get(Progress, (env.key, "position"))
        saved = json.loads(progress.payload)["execution_result"]
        assert progress.state == "done"
        assert saved["order_id"] == order.id and order.status == status
        assert saved["opened"] is (status == "filled")
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    assert len(milestones(env)) == 1
    with env.sf() as db:
        assert db.query(PaperOrder).count() == 1


@pytest.mark.parametrize("boundary", ["on_quote", "on_calendar"])
@pytest.mark.parametrize("change", ["source", "member", "decision"])
def test_automatic_action_rechecks_original_source_after_real_executor_awaits(env, boundary, change):
    def drift():
        with env.sf() as db:
            if change == "decision":
                row = db.query(Decision).one()
                evidence = json.loads(row.evidence)
                evidence["execution_contract"]["decision_version"] = "B-new-version"
                row.evidence = json.dumps(evidence)
            else:
                row = db.query(DailyPickSet).one()
                items, meta = json.loads(row.items), json.loads(row.meta)
                if change == "member":
                    items = []
                meta["generated_at"] = (NOW - timedelta(minutes=1)).isoformat()
                meta["selection_version"] = daily_selection_version(DAY, meta["generated_at"], items)
                row.items, row.meta = json.dumps(items), json.dumps(meta)
            db.commit()
    env.quote[boundary] = drift
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    with env.sf() as db:
        assert db.query(PaperOrder).count() == db.query(PaperPosition).count() == 0
        progress = db.get(Progress, (env.key, "position"))
        result = json.loads(progress.payload)["execution_result"]
        assert progress.state == "done" and result["opened"] is False
        assert not result.get("execution_unknown")
    assert milestones(env) == []


def test_new_automatic_action_without_explicit_selection_binding_fails_closed(env):
    with env.sf() as db:
        progress = db.get(Progress, (env.key, "position"))
        payload = json.loads(progress.payload)
        payload["alert"]["meta"].pop("selection_ref")
        progress.payload = json.dumps(payload)
        db.commit()
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    with env.sf() as db:
        assert db.query(PaperOrder).count() == db.query(PaperPosition).count() == 0
        progress = db.get(Progress, (env.key, "position"))
        assert progress.state == "done" and "缺少" in progress.reason


def qualified_original(env):
    from app.picks.execution_qualification import requalify_item
    from app.picks.meta_confidence import DIMENSIONS
    with env.sf() as db:
        row = db.query(DailyPickSet).one()
        original, meta = json.loads(row.items)[0], json.loads(row.meta)
        seed = {"version": 1, "score": 80, "sub_scores": dict.fromkeys(DIMENSIONS, 80),
            "dimension_evidence": {key: {"state": "success"} for key in DIMENSIONS},
            "vetoes": [], "halt_penalty": 0, "chip_signal": None,
            "buy_range": original["buy_range"], "observation_only": False, "follow_state": None}
        original.update(execution_seed=seed, observation_only=True, confidence={"tier": "observe"})
        original.pop("buy_range")
        version = daily_selection_version(DAY, meta["generated_at"], [original])
        meta.update(selection_version=version, market_phase="退潮",
            gate={"phase": "退潮", "stand_aside": True, "level": "strong", "strip_buy_range": True})
        context = {"state": "ready", "phase": "发酵", "trade_date": DAY,
            "judged_at": NOW.isoformat(), "source_as_of": NOW.isoformat(), "source_state": "ready",
            "gate": {"phase": "发酵", "stand_aside": False, "strip_buy_range": False}}
        effective = requalify_item(original, version, context, NOW)
        contract = build_execution_contract(trade_date=DAY, symbol="600001", item=effective,
            executable_snapshot={"price": 10., "state": "ready", "source": "fixture", "as_of": NOW.isoformat()},
            gate_decision="passed", gate_reason=None, pick_generated_at=meta["generated_at"])
        reference = _execution_ref(contract)
        row.items, row.meta = json.dumps([original]), json.dumps(meta)
        db.query(Decision).one().evidence = json.dumps({"execution_contract": contract})
        progress = db.get(Progress, (env.key, "position"))
        payload = json.loads(progress.payload)
        payload["alert"]["meta"].update(selection_source_version=version, execution_ref=reference)
        progress.payload = json.dumps(payload)
        event = db.get(AlertEvent, env.buy.id)
        snapshot = json.loads(event.snapshot)
        snapshot["execution_ref"] = reference
        event.snapshot = json.dumps(snapshot)
        origin = db.get(AlertEvent, env.origin.id)
        snapshot = json.loads(origin.snapshot)
        snapshot["source_version"] = version
        origin.snapshot = json.dumps(snapshot)
        db.commit()
    return original


def test_validated_live_context_sizes_the_real_action_without_reusing_morning_mask(env):
    original = qualified_original(env)
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    with env.sf() as db:
        order = db.query(PaperOrder).one()
        assert order.status == "filled" and order.quantity == 19_500
        assert json.loads(db.query(DailyPickSet).one().items) == [original]


@pytest.mark.parametrize("expires", ["qualification", "action"])
def test_automatic_deadlines_are_rechecked_after_quote_await_without_any_order(env, monkeypatch, expires):
    qualified_original(env)
    def expire():
        if expires == "qualification":
            monkeypatch.setattr("app.paper.selection_action.beijing_now", lambda: NOW + timedelta(seconds=61))
        else:
            monkeypatch.setattr("app.paper.selection_action.time", SimpleNamespace(time=lambda: time.time() + 61))
    env.quote["on_quote"] = expire
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    with env.sf() as db:
        assert db.query(PaperOrder).count() == db.query(PaperPosition).count() == 0
        progress = db.get(Progress, (env.key, "position"))
        assert progress.state == "done" and "过期" in progress.reason


@pytest.mark.parametrize("change", ["source", "qualification"])
def test_existing_automatic_pending_keeps_same_order_and_funds_until_original_binding_is_valid(env, monkeypatch, change):
    qualified_original(env)
    env.quote["price"] = 10.1
    asyncio.run(consumers.consume_pending(env.app, env.sf))
    with env.sf() as db:
        order = db.query(PaperOrder).one()
        order_id = order.id
        cash = env.paper.account_summary(0, initialize=False)["cash"]
        daily = db.query(DailyPickSet).one()
        original_meta = daily.meta
        if change == "source":
            meta = json.loads(daily.meta)
            meta["generated_at"] = NOW.isoformat()
            meta["selection_version"] = daily_selection_version(DAY, meta["generated_at"], json.loads(daily.items))
            daily.meta = json.dumps(meta)
            db.commit()
    if change == "qualification":
        monkeypatch.setattr("app.paper.selection_action.beijing_now", lambda: NOW + timedelta(seconds=61))
    env.quote["price"] = 10.
    assert asyncio.run(env.paper.match_pending()) == 1
    with env.sf() as db:
        order = db.query(PaperOrder).one()
        assert order.id == order_id and order.status == "pending" and order.reason
        assert db.query(PaperPosition).count() == 0
        assert env.paper.account_summary(0, initialize=False)["cash"] == cash
        db.query(DailyPickSet).one().meta = original_meta
        db.commit()
    monkeypatch.setattr("app.paper.selection_action.beijing_now", lambda: NOW)
    assert asyncio.run(env.paper.match_pending()) == 0
    with env.sf() as db:
        order = db.query(PaperOrder).one()
        assert order.id == order_id and order.status == "filled"
        assert db.query(PaperPosition).count() == 1


@pytest.mark.parametrize("stage", ["submit", "match"])
def test_real_quote_outside_original_buy_range_never_fills_even_when_source_and_macro_match(env, stage):
    if stage == "match":
        env.quote["price"] = 10.1
        asyncio.run(consumers.consume_pending(env.app, env.sf))
        with env.sf() as db:
            order_id = db.query(PaperOrder).one().id
            frozen_cash = db.query(PaperAccount).one().cash
    env.quote["price"] = 9.7  # Valid source/quality/window/limits, below the original 9.8–10.2 range.
    if stage == "submit":
        asyncio.run(consumers.consume_pending(env.app, env.sf))
        with env.sf() as db:
            assert db.query(PaperOrder).count() == db.query(PaperPosition).count() == 0
            progress = db.get(Progress, (env.key, "position"))
            assert progress.state == "done" and "不在买入区间" in progress.reason
            assert db.query(PaperAccount).one().cash == db.query(PaperAccount).one().initial_cash
    else:
        assert asyncio.run(env.paper.match_pending()) == 1
        with env.sf() as db:
            order = db.query(PaperOrder).one()
            assert order.id == order_id and order.status == "pending" and "不在买入区间" in order.reason
            assert db.query(PaperPosition).count() == 0 and db.query(PaperAccount).one().cash == frozen_cash
        env.quote["price"] = 10.
        assert asyncio.run(env.paper.match_pending()) == 0
        with env.sf() as db:
            order = db.query(PaperOrder).one()
            assert order.id == order_id and order.status == "filled"
            assert db.query(PaperPosition).one().quantity == order.quantity
            account = db.query(PaperAccount).one()
            assert account.cash == pytest.approx(account.initial_cash - order.filled_price * order.quantity - order.fee)


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
