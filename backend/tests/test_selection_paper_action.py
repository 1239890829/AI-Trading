"""Real router/engine against temporary funds, fixed quotes and explicit device authority."""
import json
from datetime import datetime, timedelta
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
from app.core import device_access as access
from app.core.config import settings
from app.models.daily_pick import DailyPickSet
from app.models.notification_outbox import BuyPointConsumption
from app.models.paper import PaperAccount, PaperActionReceipt, PaperOrder, PaperPosition
from app.models.watchlist import Base
from app.paper.engine import PaperTradingEngine, calc_fee
from app.paper.selection_action import execution_status
from app.picks.opportunity_learning import archive_notification_pipeline
from app.risk.engine import RiskEngine
from app.risk.config import get_params
from app.schemas.market import Quote, Quality
from app.services.selection_notifications import daily_selection_version

ORIGIN, PHONE = "http://paper-owner.test:3000", "http://paper-reader.test:3000"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    bind = create_engine(f"sqlite:///{tmp_path / 'paper-selection.sqlite'}")
    Base.metadata.create_all(bind)
    sf = sessionmaker(bind=bind, expire_on_commit=False)
    clock = [datetime(2026, 9, 29, 10, 0, tzinfo=BJ_TZ)]
    monkeypatch.setattr("app.paper.engine.beijing_now", lambda: clock[0])
    monkeypatch.setattr("app.paper.engine._today", lambda: clock[0].strftime("%Y%m%d"))
    monkeypatch.setattr("app.paper.selection_action.beijing_now", lambda: clock[0])
    monkeypatch.setattr("app.paper.execution_guard.known_trade_days", lambda: [clock[0].date()])
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    monkeypatch.setattr("app.paper.actions.datetime", Clock)
    monkeypatch.setattr("app.paper.selection_action.datetime", Clock)
    quote = Quote(symbol="600127", name="隔离材料", price=10., prev_close=9.8, change_pct=2.,
                  limit_up_price=10.78, limit_down_price=8.82, amount=100_000_000,
                  source="fixture", data_timestamp=clock[0], received_at=clock[0])
    async def quote_fn(_): return quote
    async def days_fn(): return ["20260929", "20260930"]
    risk = RiskEngine(SimpleNamespace(get_quotes=lambda: [quote]), None, sf)
    risk._state = "震荡偏多"
    risk._params = get_params(risk._state)
    paper = PaperTradingEngine(sf, quote_fn, days_fn, risk_engine=risk)
    paper.ensure_account()  # Only the temporary test account, never done by GET.
    generated = clock[0].replace(hour=9, minute=26).isoformat()
    item = {"symbol": quote.symbol, "name": quote.name, "price": 9.9,
            "confidence": {"tier": "strong"}, "buy_range": {"low": 9., "high": 10.5},
            "vetoes": [], "observation_only": False, "follow_state": "followable"}
    items = [item]
    version = daily_selection_version("2026-09-29", generated, items)
    meta = {"generated_at": generated, "selection_version": version, "gate": {"stand_aside": False}}
    with sf() as db:
        db.add(DailyPickSet(date="2026-09-29", items=json.dumps(items), meta=json.dumps(meta)))
        db.commit()
    archive_notification_pipeline(items, trade_date="2026-09-29", as_of=clock[0],
        hits=[{"item": item, "price": 10., "chg": 2.}], skips=[], dispatch_by_symbol={},
        execution_by_symbol={quote.symbol: {"state": "ready", "price": 10., "change_pct": 2.,
            "prev_close": 9.8, "source": "fixture", "as_of": clock[0].isoformat(), "checked_at": clock[0].isoformat()}},
        session_factory=sf, pick_generated_at=generated)
    monkeypatch.setattr(settings, "api_token", "fixture-paper-service")
    monkeypatch.setattr(access, "STORE_PATH", tmp_path / "paper-access.sqlite")
    store = access.get_device_store()
    owner, _ = store.bootstrap(store.bootstrap_code(), "合成管理员", ORIGIN)
    token, device = store.start("合成读者", PHONE)
    store.manage(owner, ORIGIN, device["id"], "approve")
    headers = {"X-API-Token": settings.api_token, access.BROWSER_HEADER: "1",
               access.SESSION_HEADER: token, access.ORIGIN_HEADER: PHONE}
    app = FastAPI()
    app.state.paper = paper
    app.include_router(router, prefix="/api", dependencies=[Depends(require_api_token)])
    params = {"selection_ref": "daily:2026-09-29:600127", "source_version": version, "quantity": 100}
    with TestClient(app) as client:
        yield SimpleNamespace(sf=sf, clock=clock, quote=quote, risk=risk, paper=paper,
            client=client, params=params, headers=headers, store=store, owner=owner, device=device)
    bind.dispose()


def state(sf):
    with sf() as db:
        return {m.__tablename__: [tuple(getattr(r, c.name) for c in m.__table__.columns)
                 for r in db.scalars(select(m))] for m in (PaperAccount, PaperPosition, PaperOrder, PaperActionReceipt)}


def preview(rig):
    response = rig.client.get("/api/paper/selection-action", params=rig.params, headers=rig.headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def body(rig, preflight):
    p = preflight["preflight"]
    return {**rig.params, "symbol": "600127", "side": "buy", "price": p["price"],
            "request_id": str(uuid4()), "expires_at": p["expires_at"],
            "decision_id": p["decision_id"], "decision_version": p["decision_version"]}


def grant(rig):
    rig.store.manage(rig.owner, ORIGIN, rig.device["id"], "grant-maintenance")


def test_ordinary_paired_get_is_exact_read_capability_and_post_still_requires_maintenance(rig):
    before = state(rig.sf)
    data = preview(rig)
    assert data["scope"] == "main" and data["read_only"] is True
    assert data["eligibility"]["state"] == "ready"
    assert data["preflight"]["allowed"] is True
    assert data["permission"] == {"maintenance_required": True, "maintenance_granted": False}
    assert state(rig.sf) == before
    rejected = rig.client.post("/api/paper/orders", json=body(rig, data), headers=rig.headers)
    assert rejected.status_code == 403
    assert state(rig.sf) == before
    assert rig.client.get("/api/paper/account", params={"read_only": True}, headers=rig.headers).status_code == 403
    assert rig.client.get("/api/paper/selection-action-extra", params=rig.params, headers=rig.headers).status_code == 404


def test_real_bound_fill_fee_identity_and_replay_do_not_duplicate(rig):
    grant(rig)
    data = preview(rig)
    draft = body(rig, data)
    first = rig.client.post("/api/paper/orders", json=draft, headers=rig.headers)
    assert first.status_code == 200, first.text
    result = first.json()["data"]
    assert result["status"] == "filled" and result["filled_price"] == 10.
    assert result["fee"] == calc_fee("buy", 10., 100)
    again = rig.client.post("/api/paper/orders", json=draft, headers=rig.headers)
    assert again.status_code == 200 and again.json()["data"]["replayed"] is True
    with rig.sf() as db:
        assert db.query(PaperOrder).count() == 1
        pos = db.query(PaperPosition).one()
        assert pos.scope == "main" and pos.quantity == pos.frozen_today == 100
        assert {a.scope for a in db.scalars(select(PaperAccount))} == {"main"}
    tracked = preview(rig)["execution"]
    assert tracked["state"] == "filled" and tracked["order"]["id"] == result["id"]
    assert preview(rig)["eligibility"]["reason_code"] == "execution_exists"
    recovery = rig.client.get("/api/paper/selection-action", params={**rig.params, "request_id": str(uuid4())}, headers=rig.headers)
    assert recovery.json()["data"]["eligibility"]["reason_code"] == "execution_exists"
    assert recovery.json()["data"]["execution"]["state"] == "filled"
    draft["request_id"] = str(uuid4())
    duplicate = rig.client.post("/api/paper/orders", json=draft, headers=rig.headers)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "execution_exists"
    with rig.sf() as db:
        assert db.query(PaperOrder).count() == db.query(PaperActionReceipt).count() == 1


@pytest.mark.parametrize("change,code", [("source", "selection_superseded"), ("quote", "quote_untrusted"),
                                       ("decision", "decision_superseded"), ("quantity", "invalid_lot")])
def test_preflight_is_not_permission_or_permanent_execution_evidence(rig, change, code):
    grant(rig)
    draft = body(rig, preview(rig))
    if change == "source": draft["source_version"] = "changed"
    if change == "decision": draft["decision_version"] = "changed"
    if change == "quote": rig.quote.data_timestamp = rig.clock[0] - timedelta(seconds=61)
    if change == "quantity": draft["quantity"] = 101
    draft["allowed"] = True  # Never consumed as an authorization or decision.
    before = state(rig.sf)
    result = rig.client.post("/api/paper/orders", json=draft, headers=rig.headers)
    assert result.status_code == 422, result.text
    assert result.json()["detail"]["code"] == code
    assert state(rig.sf) == before


def test_observation_only_or_market_wait_never_creates_an_action(rig):
    before = state(rig.sf)
    rig.clock[0] = rig.clock[0].replace(hour=12)
    data = preview(rig)
    assert data["eligibility"]["reason_code"] == "market_closed"
    assert data["execution"]["state"] == "not_started"
    assert state(rig.sf) == before
    rig.clock[0] = rig.clock[0].replace(hour=10)
    with rig.sf() as db:
        row = db.scalar(select(DailyPickSet))
        items = json.loads(row.items)
        items[0]["observation_only"] = True
        row.items = json.dumps(items)
        meta = json.loads(row.meta)
        meta["selection_version"] = daily_selection_version(row.date, meta["generated_at"], items)
        row.meta = json.dumps(meta)
        rig.params["source_version"] = meta["selection_version"]
        db.commit()
    data = preview(rig)
    assert data["eligibility"]["reason_code"] == "observation_only"
    assert data["preflight"] is None
    assert state(rig.sf) == before


def test_uninitialized_read_remains_uninitialized_and_legacy_maintenance_action_still_works(rig):
    with rig.sf() as db:
        db.query(PaperAccount).delete()
        db.commit()
    before = state(rig.sf)
    assert preview(rig)["eligibility"]["reason_code"] == "account_uninitialized"
    assert state(rig.sf) == before
    grant(rig)
    draft = {"symbol": "600127", "side": "buy", "price": 10., "quantity": 100,
             "request_id": str(uuid4()), "expires_at": (rig.clock[0] + timedelta(seconds=30)).isoformat()}
    result = rig.client.post("/api/paper/orders", json=draft, headers=rig.headers)
    assert result.status_code == 200 and result.json()["data"]["status"] == "filled"


def test_cancelled_order_is_projected_from_current_order_not_old_pending_receipt(rig):
    grant(rig)
    draft = body(rig, preview(rig))
    draft["price"] = 9.5
    response = rig.client.post("/api/paper/orders", json=draft, headers=rig.headers)
    order_id = response.json()["data"]["id"]
    assert response.json()["data"]["status"] == "pending"
    cancelled = rig.client.delete(f"/api/paper/orders/{order_id}", headers=rig.headers)
    assert cancelled.status_code == 200 and cancelled.json()["data"]["status"] == "cancelled"
    data = preview(rig)
    assert data["execution"]["state"] == data["execution"]["order"]["status"] == "cancelled"
    assert data["eligibility"]["state"] == "ready"
    with rig.sf() as db:
        account = db.scalar(select(PaperAccount))
        assert account.cash == account.initial_cash
        assert db.query(PaperPosition).count() == 0


@pytest.mark.parametrize("hour,minute,expected", [(9, 27, "market_closed"), (9, 30, "ready"),
    (11, 30, "market_closed"), (13, 0, "ready"), (14, 57, "market_closed"), (15, 0, "market_closed")])
def test_current_continuous_execution_windows_do_not_accept_auction_or_lunch(rig, hour, minute, expected):
    rig.clock[0] = rig.clock[0].replace(hour=hour, minute=minute)
    rig.quote.data_timestamp = rig.clock[0]
    before = state(rig.sf)
    assert preview(rig)["eligibility"]["reason_code"] == expected
    assert state(rig.sf) == before


@pytest.mark.parametrize("failure,expected", [("quote", "quote_unavailable"), ("risk", "risk_unavailable")])
def test_failed_read_dependencies_are_explicit_unknown_without_writes(rig, monkeypatch, failure, expected):
    if failure == "quote":
        async def unavailable(_):
            raise RuntimeError("synthetic unavailable")
        monkeypatch.setattr(rig.paper, "_quote_fn", unavailable)
    else:
        def unavailable(*_args, **_kwargs):
            raise RuntimeError("synthetic unavailable")
        monkeypatch.setattr(rig.risk, "check_order", unavailable)
    before = state(rig.sf)
    data = preview(rig)
    assert data["eligibility"]["state"] == "unknown"
    assert data["eligibility"]["reason_code"] == expected
    assert data["preflight"]["allowed"] is False
    assert state(rig.sf) == before


def test_durable_unknown_or_existing_order_cannot_be_hidden_by_later_rejection(rig):
    decision = {"decision_id": "original-decision", "decision_version": "original-version"}
    with rig.sf() as db:
        db.add(BuyPointConsumption(event_key="synthetic-event", event_id=1, consumer="position",
            state="unknown", reason="original interrupted", attempts=1, expires_at_ms=1, updated_at_ms=1,
            payload=json.dumps({"alert": {"meta": {"execution_ref": decision}},
                                "execution_result": {"opened": False, "execution_unknown": True}})))
        db.add(PaperActionReceipt(scope="main", request_id=str(uuid4()),
            draft=json.dumps(rig.params), result=json.dumps({"status": "rejected", "reason": "later rejection"})))
        db.commit()
    result = execution_status(rig.sf, rig.params["selection_ref"], rig.params["source_version"], decision=decision)
    assert result["state"] == "unknown" and result["reason"] == "original interrupted"


@pytest.mark.parametrize("failure", ["lunch", "closing_auction", "source_age", "quality", "symbol", "risk", "limit"])
def test_pending_fill_rechecks_current_context_and_keeps_reserved_money(rig, failure):
    import asyncio
    grant(rig)
    draft = body(rig, preview(rig))
    draft["price"] = 9.5
    accepted = rig.client.post("/api/paper/orders", json=draft, headers=rig.headers)
    assert accepted.status_code == 200 and accepted.json()["data"]["status"] == "pending"
    with rig.sf() as db:
        frozen_cash = db.scalar(select(PaperAccount)).cash
    rig.quote.price = 9.4
    if failure == "lunch": rig.clock[0] = rig.clock[0].replace(hour=12)
    if failure == "closing_auction": rig.clock[0] = rig.clock[0].replace(hour=14, minute=58)
    if failure == "source_age": rig.quote.data_timestamp -= timedelta(seconds=61)
    if failure == "quality": rig.quote.quality = Quality.stale
    if failure == "symbol": rig.quote.symbol = "600128"
    if failure == "risk": rig.risk._state = "下跌趋势"
    if failure == "limit": rig.quote.limit_up_price = 9.4
    assert asyncio.run(rig.paper.match_pending()) == 1
    with rig.sf() as db:
        order = db.scalar(select(PaperOrder))
        assert order.status == "pending" and order.reason
        assert db.scalar(select(PaperAccount)).cash == frozen_cash
        assert db.query(PaperPosition).count() == 0
    assert preview(rig)["execution"]["state"] == "pending"
    rig.clock[0] = rig.clock[0].replace(hour=10, minute=0)
    rig.quote.data_timestamp = rig.clock[0]
    rig.quote.quality = Quality.high
    rig.quote.symbol = "600127"
    rig.quote.limit_up_price = 10.78
    rig.risk._state = "震荡偏多"
    assert asyncio.run(rig.paper.match_pending()) == 0
    with rig.sf() as db:
        order = db.scalar(select(PaperOrder))
        assert order.status == "filled" and order.filled_price == 9.4 and order.reason is None
        assert order.fee == calc_fee("buy", 9.4, 100)
        account = db.scalar(select(PaperAccount))
        assert account.cash == pytest.approx(account.initial_cash - 940 - order.fee)
    assert preview(rig)["execution"]["state"] == "filled"


def _advance_buy_point_decision(rig):
    from app.picks.opportunity_learning import latest_notification_execution
    rig.clock[0] += timedelta(minutes=1)
    rig.quote.price = 10.05
    rig.quote.data_timestamp = rig.clock[0]
    with rig.sf() as db:
        row = db.scalar(select(DailyPickSet))
        items, meta = json.loads(row.items), json.loads(row.meta)
    archive_notification_pipeline(items, trade_date="2026-09-29", as_of=rig.clock[0],
        hits=[{"item": items[0], "price": rig.quote.price, "chg": rig.quote.change_pct}], skips=[], dispatch_by_symbol={},
        execution_by_symbol={rig.quote.symbol: {"state": "ready", "price": rig.quote.price,
            "change_pct": rig.quote.change_pct, "prev_close": rig.quote.prev_close, "source": rig.quote.source,
            "as_of": rig.clock[0].isoformat(), "checked_at": rig.clock[0].isoformat()}},
        session_factory=rig.sf, pick_generated_at=meta["generated_at"])
    return latest_notification_execution("2026-09-29", rig.sf)[rig.quote.symbol]


@pytest.mark.parametrize("outcome", ["unknown", "running", "filled", "pending"])
def test_previous_automatic_decision_keeps_occupancy_after_new_eligible_version(rig, outcome):
    import asyncio
    from app.picks.opportunity_learning import latest_notification_execution
    from app.picks.buy_point import _buy_point_dedup_key
    old = latest_notification_execution("2026-09-29", rig.sf)[rig.quote.symbol]
    new = _advance_buy_point_decision(rig)
    assert new["decision_id"] == old["decision_id"] and new["decision_version"] != old["decision_version"]
    draft = body(rig, preview(rig))  # A legitimate B preflight before A evidence is observed.
    result = {"opened": False, "execution_unknown": True}
    if outcome in {"filled", "pending"}:
        order = asyncio.run(rig.paper.place_order(rig.quote.symbol, "buy", 10.05 if outcome == "filled" else 9.5, 100))
        assert order.status == outcome
        result = {"opened": outcome == "filled", "accepted": True, "order_id": order.id}
    result.update(decision_id=old["decision_id"], decision_version=old["decision_version"])
    meta = {"selection_ref": rig.params["selection_ref"], "selection_source_version": rig.params["source_version"],
            "trade_date": "2026-09-29", "execution_ref": {"decision_id": old["decision_id"], "decision_version": old["decision_version"]}}
    if outcome == "running":
        result = None
    with rig.sf() as db:
        db.add(BuyPointConsumption(event_key=_buy_point_dedup_key("2026-09-29", rig.quote.symbol), event_id=1,
            consumer="position", state="done" if outcome in {"filled", "pending"} else outcome,
            reason="original A outcome retained", attempts=1, expires_at_ms=1, updated_at_ms=1,
            payload=json.dumps({"alert": {"symbol": rig.quote.symbol, "meta": meta}, "execution_result": result})))
        db.commit()
    expected = "pending" if outcome == "running" else outcome
    before = state(rig.sf)
    data = preview(rig)
    assert data["execution"]["state"] == expected
    assert data["eligibility"]["reason_code"] == "execution_exists"
    assert state(rig.sf) == before
    grant(rig)
    response = rig.client.post("/api/paper/orders", json=draft, headers=rig.headers)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "execution_exists"
    assert state(rig.sf) == before
    if outcome in {"filled", "pending"}:
        assert data["execution"]["order"]["id"] == order.id and data["execution"]["order"]["status"] == outcome


def test_legacy_automatic_unknown_without_selection_binding_still_blocks_new_version(rig):
    from app.picks.opportunity_learning import latest_notification_execution
    from app.picks.buy_point import _buy_point_dedup_key
    old = latest_notification_execution("2026-09-29", rig.sf)[rig.quote.symbol]
    _advance_buy_point_decision(rig)
    draft = body(rig, preview(rig))
    with rig.sf() as db:
        db.add(BuyPointConsumption(event_key=_buy_point_dedup_key("2026-09-29", rig.quote.symbol), event_id=1,
            consumer="position", state="unknown", reason="original A unknown", attempts=1, expires_at_ms=1, updated_at_ms=1,
            payload=json.dumps({"alert": {"symbol": rig.quote.symbol, "meta": {"trade_date": "2026-09-29",
                "execution_ref": {"decision_id": old["decision_id"], "decision_version": old["decision_version"]}}},
                "execution_result": {"opened": False, "execution_unknown": True}})))
        db.commit()
    before = state(rig.sf)
    data = preview(rig)
    assert data["execution"]["state"] == "unknown" and data["execution"]["order"] is None
    assert data["eligibility"]["reason_code"] == "execution_exists"
    grant(rig)
    response = rig.client.post("/api/paper/orders", json=draft, headers=rig.headers)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "execution_exists"
    assert state(rig.sf) == before


def test_same_uuid_replay_survives_new_decision_and_new_uuid_still_cannot_duplicate(rig):
    grant(rig)
    draft = body(rig, preview(rig))
    first = rig.client.post("/api/paper/orders", json=draft, headers=rig.headers)
    assert first.status_code == 200 and first.json()["data"]["status"] == "filled"
    new = _advance_buy_point_decision(rig)
    before = state(rig.sf)
    replayed = rig.client.post("/api/paper/orders", json=draft, headers=rig.headers)
    assert replayed.status_code == 200 and replayed.json()["data"]["replayed"] is True
    assert replayed.json()["data"]["id"] == first.json()["data"]["id"]
    next_draft = {**draft, "request_id": str(uuid4()), "decision_id": new["decision_id"],
        "decision_version": new["decision_version"], "expires_at": (rig.clock[0] + timedelta(seconds=30)).isoformat()}
    duplicate = rig.client.post("/api/paper/orders", json=next_draft, headers=rig.headers)
    assert duplicate.status_code == 409 and duplicate.json()["detail"]["code"] == "execution_exists"
    assert state(rig.sf) == before
