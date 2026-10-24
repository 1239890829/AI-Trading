"""Existing maintenance route: an explicit closed date never runs another owner."""
from datetime import datetime
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.deps import require_api_token
from app.api.routes.picks_intraday import router
from app.core import device_access as access
from app.core.config import settings
from app.picks import review_intraday as owner


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "api_token", "isolated-outcome-service")
    monkeypatch.setattr(access, "STORE_PATH", tmp_path / "outcome-access.sqlite")
    store = access.get_device_store()
    origin = "http://outcome-owner.test:3000"
    manager, _ = store.bootstrap(store.bootstrap_code(), "合成管理员", origin)
    token, device = store.start("合成读者", origin)
    store.manage(manager, origin, device["id"], "approve")
    headers = {"X-API-Token": settings.api_token, access.BROWSER_HEADER: "1",
               access.SESSION_HEADER: token, access.ORIGIN_HEADER: origin}
    app = FastAPI()
    app.state.hub = SimpleNamespace(provider=object())
    app.include_router(router, prefix="/api", dependencies=[Depends(require_api_token)])
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, headers=headers, store=store,
                              manager=manager, origin=origin, device=device)


def grant(api):
    api.store.manage(api.manager, api.origin, api.device["id"], "grant-maintenance")


def test_date_recovery_still_requires_service_auth_and_maintenance(api, monkeypatch):
    calls = []
    async def collect(app, *, trade_date):
        calls.append(trade_date)
        return {"outcome_collection": {"state": "complete", "reason_codes": []}}
    async def forbidden(*_a, **_k):
        raise AssertionError("outcome-only request cannot run brief/model/notification owner")
    monkeypatch.setattr(owner, "collect_closed_outcomes", collect)
    monkeypatch.setattr(owner, "run_review", forbidden)
    url = "/api/picks/intraday-review/run?outcome_date=2026-09-01"
    assert api.client.post(url).status_code == 401
    assert api.client.post(url, headers=api.headers).status_code == 403
    assert calls == []
    grant(api)
    response = api.client.post(url, headers=api.headers)
    assert response.status_code == 200 and calls == ["2026-09-01"]
    assert response.json()["meta"] == {"mode": "outcome_only", "outcome_date": "2026-09-01"}


@pytest.mark.parametrize("day", ["20260901", "not-date", "2026-02-30"])
def test_invalid_dates_are_rejected_without_price_collection(api, monkeypatch, day):
    grant(api)
    monkeypatch.setattr(owner, "beijing_now", lambda: datetime(2026, 9, 2, 16))
    async def forbidden(*_a, **_k): raise AssertionError("invalid date must not fetch calendar/prices")
    monkeypatch.setattr(owner.tc, "trading_days", forbidden)
    assert api.client.post(f"/api/picks/intraday-review/run?outcome_date={day}", headers=api.headers).status_code == 422


def test_future_and_calendar_unknown_dates_do_not_fetch_prices(api, monkeypatch):
    grant(api)
    monkeypatch.setattr(owner, "beijing_now", lambda: datetime(2026, 9, 2, 16))
    async def empty(_): return []
    async def forbidden(*_a, **_k): raise AssertionError("unconfirmed date must not fetch prices")
    monkeypatch.setattr(owner.tc, "trading_days", empty)
    monkeypatch.setattr(owner, "_paired_daily_facts", forbidden)
    future = api.client.post("/api/picks/intraday-review/run?outcome_date=2026-09-03", headers=api.headers)
    assert future.status_code == 409 and future.json()["detail"]["reason_codes"] == ["target_not_closed"]
    missing = api.client.post("/api/picks/intraday-review/run?outcome_date=2026-09-01", headers=api.headers)
    assert missing.status_code == 409 and missing.json()["detail"]["state"] == "calendar_unknown"


def test_no_date_preserves_original_review_dispatch(api, monkeypatch):
    grant(api)
    async def review(app, *, trigger):
        assert trigger == "manual"
        return {"ok": True, "original_review": True}
    async def forbidden(*_a, **_k): raise AssertionError("no recovery date was requested")
    monkeypatch.setattr(owner, "run_review", review)
    monkeypatch.setattr(owner, "collect_closed_outcomes", forbidden)
    response = api.client.post("/api/picks/intraday-review/run", headers=api.headers)
    assert response.status_code == 200 and response.json()["data"]["original_review"] is True


@pytest.mark.parametrize("symbols",["", "600001,invalid", ",600001", ",".join(["600001"]*21)])
def test_invalid_symbol_batches_never_call_the_collection_owner(api, monkeypatch, symbols):
    grant(api)
    async def forbidden(*_a,**_k):raise AssertionError("invalid batch cannot enter a business writer")
    monkeypatch.setattr(owner,"collect_closed_outcomes",forbidden)
    response=api.client.post("/api/picks/intraday-review/run",params={"outcome_date":"2026-09-01","symbols":symbols},headers=api.headers)
    assert response.status_code==422


def test_explicit_symbol_batch_is_passed_only_to_outcome_owner(api, monkeypatch):
    grant(api)
    async def collect(app,*,trade_date,symbols):
        assert trade_date=="2026-09-01" and symbols=={"600001","600002"}
        return {"outcome_collection":{"state":"partial","partial_batch":True,"processed_count":2,"remaining_count":3,"scope_count":5,"reason_codes":["partial_batch"]}}
    monkeypatch.setattr(owner,"collect_closed_outcomes",collect)
    response=api.client.post("/api/picks/intraday-review/run",params={"outcome_date":"2026-09-01","symbols":"600001,600002"},headers=api.headers)
    assert response.status_code==200 and response.json()["data"]["outcome_collection"]["remaining_count"]==3
    assert api.client.post("/api/picks/intraday-review/run",params={"symbols":"600001"},headers=api.headers).status_code==422
