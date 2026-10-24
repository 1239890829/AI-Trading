"""The same real executor rejects unsafe manual and automatic main actions."""
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.deps import require_api_token
from app.api.routes.paper import router
from app.core.bjtime import BJ_TZ
from app.core.config import settings
from app.models.paper import PaperAccount, PaperOrder, PaperPosition
from app.models.daily_pick import DailyPickSet
from app.models.notification_outbox import BuyPointConsumption
from app.models.opportunity_learning import OpportunityDecisionSnapshot
from app.models.watchlist import Base
from app.paper.engine import PaperTradingEngine
from app.picks import position_engine
from app.picks.buy_point import _buy_point_dedup_key, _execution_ref
from app.picks.opportunity_learning import build_execution_contract
from app.services.selection_notifications import daily_selection_version
from app.risk.config import get_params
from app.risk.engine import RiskEngine
from app.schemas.market import Quality, Quote


@pytest.fixture
def execution(tmp_path, monkeypatch):
    sql = create_engine(f"sqlite:///{tmp_path/'execution.sqlite'}")
    Base.metadata.create_all(sql)
    sf = sessionmaker(bind=sql)
    clock = [datetime(2026, 9, 29, 10, 0, tzinfo=BJ_TZ)]
    monkeypatch.setattr("app.paper.engine.beijing_now", lambda: clock[0])
    monkeypatch.setattr("app.paper.engine._today", lambda: clock[0].strftime("%Y%m%d"))
    monkeypatch.setattr("app.paper.selection_action.beijing_now", lambda: clock[0])
    monkeypatch.setattr(position_engine, "beijing_now", lambda: clock[0])
    monkeypatch.setattr(position_engine, "_PLAN_DIR", tmp_path/'plans')
    monkeypatch.setattr(position_engine, "_today_gate_and_phase", lambda: ({}, "冰点"))
    monkeypatch.setattr(position_engine, "_publish_open_decision", lambda *_a: None)
    quote = Quote(symbol="600001", price=10., change_pct=2., prev_close=9.8,
                  limit_up_price=11., limit_down_price=9., amount=100_000_000,
                  source="fixture", data_timestamp=clock[0])
    async def current(_): return quote
    async def days(): return ["20260929", "20260930"]
    hub = SimpleNamespace(get_quotes=lambda: [quote])
    risk = RiskEngine(hub, None, sf)
    risk._state, risk._params = "震荡偏多", get_params("震荡偏多")
    paper = PaperTradingEngine(sf, current, days, risk_engine=risk)
    paper.ensure_account()
    day, generated = clock[0].date().isoformat(), clock[0].replace(hour=9, minute=26).isoformat()
    item = {"symbol": "600001", "price": 10., "score": 80, "confidence": {"tier": "strong"},
            "buy_range": {"low": 9., "high": 10.5}}
    version = daily_selection_version(day, generated, [item])
    contract = build_execution_contract(trade_date=day, symbol="600001", item=item,
        executable_snapshot={"state": "ready", "price": 10., "source": "fixture", "as_of": clock[0].isoformat()},
        gate_decision="passed", gate_reason=None, pick_generated_at=generated)
    key, expires = _buy_point_dedup_key(day, "600001"), int(time.time() * 1000) + 60_000
    binding = {"selection_ref": f"daily:{day}:600001", "source_version": version,
        "decision_id": contract["decision_id"], "decision_version": contract["decision_version"],
        "consumer_event_key": key, "expires_at_ms": expires}
    with sf() as db:
        db.add(DailyPickSet(date=day, items=json.dumps([item]), meta=json.dumps({
            "generated_at": generated, "selection_version": version, "gate": {}, "market_phase": "冰点"})))
        db.add(OpportunityDecisionSnapshot(snapshot_id="b" * 40, run_id="fixture", trade_date=day,
            as_of=clock[0].replace(tzinfo=None), symbol="600001", scenario="buy_point", stage="notification",
            decision="eligible", data_state="ready", strategy_version="fixture", feature_version="fixture",
            evidence=json.dumps({"execution_contract": contract})))
        db.add(BuyPointConsumption(event_key=key, event_id=1, consumer="position", state="running", expires_at_ms=expires,
            updated_at_ms=int(time.time() * 1000), payload=json.dumps({"alert": {"meta": {
                "selection_ref": binding["selection_ref"], "selection_source_version": version,
                "execution_ref": _execution_ref(contract)}}})))
        db.commit()
    app = FastAPI()
    app.state.paper, app.state.hub = paper, hub
    app.include_router(router, prefix="/api", dependencies=[Depends(require_api_token)])
    monkeypatch.setattr(settings, "api_token", "isolated-paper-execution")
    with TestClient(app) as client:
        yield SimpleNamespace(sf=sf, clock=clock, quote=quote, paper=paper, app=app, client=client,
                              headers={"X-API-Token": settings.api_token}, binding=binding)
    sql.dispose()


def unchanged(execution):
    with execution.sf() as db:
        assert db.query(PaperOrder).count() == db.query(PaperPosition).count() == 0
        account = db.scalar(select(PaperAccount))
        assert account.cash == account.initial_cash


@pytest.mark.parametrize("path", ["unbound_post", "automatic"])
@pytest.mark.parametrize("failure", ["old_source", "wrong_symbol", "closing_auction", "missing_risk"])
def test_all_main_entry_paths_recheck_execution_context(execution, path, failure):
    if failure == "old_source": execution.quote.data_timestamp -= timedelta(seconds=61)
    if failure == "wrong_symbol": execution.quote.symbol = "600002"
    if failure == "closing_auction":
        execution.clock[0] = execution.clock[0].replace(hour=14, minute=58)
        execution.quote.data_timestamp = execution.clock[0]
    if failure == "missing_risk": execution.paper._risk_engine = None
    if path == "unbound_post":
        result = execution.client.post("/api/paper/orders", headers=execution.headers, json={
            "symbol":"600001", "side":"buy", "price":10., "quantity":100,
            "request_id":str(uuid4()), "expires_at":(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()})
        assert result.status_code == 422, result.text
    else:
        result = asyncio.run(position_engine.maybe_open(execution.app, symbol="600001", name="合成观察",
                                                       trigger="buy_point", price=10., selection_context=execution.binding))
        assert result["opened"] is False and "撮合拒绝" in result["reason"]
        assert not result.get("execution_unknown")
    unchanged(execution)


@pytest.mark.parametrize("path", ["unbound_post", "automatic"])
def test_qualified_real_main_executor_still_fills(execution, path):
    if path == "unbound_post":
        result = execution.client.post("/api/paper/orders", headers=execution.headers, json={
            "symbol":"600001", "side":"buy", "price":10., "quantity":100,
            "request_id":str(uuid4()), "expires_at":(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()})
        assert result.status_code == 200 and result.json()["data"]["status"] == "filled"
    else:
        result = asyncio.run(position_engine.maybe_open(execution.app, symbol="600001", name="合成观察",
                                                       trigger="buy_point", price=10., selection_context=execution.binding))
        assert result["opened"] is True
    with execution.sf() as db:
        order = db.scalar(select(PaperOrder))
        pos = db.scalar(select(PaperPosition))
        assert order.status == "filled" and order.fee > 0
        assert pos.scope == "main" and pos.frozen_today == pos.quantity > 0


def test_main_and_hunting_reject_bad_quality_but_shadow_keeps_its_declared_morning_scope(execution):
    execution.quote.quality = Quality.stale
    for scope in ("main", "hunting_shadow"):
        execution.paper.scope = scope
        result = asyncio.run(execution.paper.place_order("600001", "buy", 10., 100))
        assert result.status == "rejected"
    execution.quote.quality = Quality.high
    execution.clock[0] = execution.clock[0].replace(hour=9, minute=26)
    execution.quote.data_timestamp = execution.clock[0]
    execution.paper.scope = "shadow"
    execution.paper._risk_engine = None
    assert asyncio.run(execution.paper.place_order("600001", "buy", 10., 100)).status == "filled"
    with execution.sf() as db:
        assert {o.scope for o in db.scalars(select(PaperOrder))} == {"shadow"}


@pytest.mark.parametrize("path",["submit","match"])
@pytest.mark.parametrize("failure",["auction_boundary","source_expires"])
def test_calendar_await_cannot_invalidate_the_gate_before_actual_fill(execution,path,failure):
    if path=="match":
        assert asyncio.run(execution.paper.place_order("600001","buy",9.5,100)).status=="pending"
        execution.quote.price=9.4
    with execution.sf() as db:
        db.add(PaperPosition(scope="main",symbol="600002",quantity=100,frozen_today=100,
                             cost_price=10.,buy_date="20260929"))
        db.commit()
        cash=db.scalar(select(PaperAccount)).cash
    start=(execution.clock[0].replace(hour=14,minute=56,second=59)
           if failure=="auction_boundary" else execution.clock[0])
    execution.clock[0]=execution.quote.data_timestamp=start
    calls=[]
    async def calendar():
        calls.append(True)
        if len(calls)==2:
            execution.clock[0]=start+timedelta(seconds=2 if failure=="auction_boundary" else 61)
        return ["20260929","20260930"]
    execution.paper._tdays_fn=calendar
    if path=="submit":
        result=asyncio.run(execution.paper.place_order("600001","buy",10.,100))
        assert result.status=="rejected"
    else:
        assert asyncio.run(execution.paper.match_pending())==1
    with execution.sf() as db:
        orders=list(db.scalars(select(PaperOrder)))
        assert not any(o.status=="filled" for o in orders)
        assert db.scalar(select(PaperAccount)).cash==cash
        assert {p.symbol for p in db.scalars(select(PaperPosition))}=={"600002"}


@pytest.mark.parametrize("failure",["old_source","missing_risk","risk_rejected"])
def test_rejected_automatic_plan_does_not_initialize_an_empty_account(execution,failure):
    with execution.sf() as db:
        db.query(PaperAccount).delete()
        db.commit()
    if failure=="old_source": execution.quote.data_timestamp-=timedelta(seconds=61)
    if failure=="missing_risk": execution.paper._risk_engine=None
    if failure=="risk_rejected":
        execution.paper._risk_engine._state="下跌趋势"
        execution.paper._risk_engine._params=get_params("下跌趋势")
    result=asyncio.run(position_engine.maybe_open(execution.app,symbol="600001",name="合成观察",trigger="buy_point",price=10.,selection_context=execution.binding))
    assert result["opened"] is False and not result.get("execution_unknown")
    with execution.sf() as db:
        assert db.query(PaperAccount).count()==db.query(PaperOrder).count()==db.query(PaperPosition).count()==0


def test_qualified_automatic_plan_initializes_only_inside_real_executor(execution):
    with execution.sf() as db:
        db.query(PaperAccount).delete()
        db.commit()
    result=asyncio.run(position_engine.maybe_open(execution.app,symbol="600001",name="合成观察",trigger="buy_point",price=10.,selection_context=execution.binding))
    assert result["opened"] is True
    with execution.sf() as db:
        assert db.query(PaperAccount).count()==db.query(PaperOrder).count()==db.query(PaperPosition).count()==1
