"""Pool dates must not relabel a previous day's payload; readers share one source call."""
import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.deps import get_hub
from app.api.routes import market_pools
from app.market import trade_calendar

DAY = date(2026, 10, 9)


def app_for(provider):
    app = FastAPI()
    app.include_router(market_pools.router, prefix="/api")
    app.dependency_overrides[get_hub] = lambda: SimpleNamespace(provider=provider)
    return app


@pytest.mark.parametrize(("days", "requested", "status"), [
    ([DAY], "2026-10-10", 422),
    ([date(2026, 9, 30), DAY], "2026-10-01", 422),
    ([DAY], "2026-10-12", 503),
    ([], "2026-10-09", 503),
])
def test_unknown_and_closed_dates_never_relabel_upstream(monkeypatch, days, requested, status):
    async def calendar(_provider):
        return days

    async def pool(_day):
        raise AssertionError("unverified date must not reach a source")

    monkeypatch.setattr(trade_calendar, "trading_days", calendar)
    with TestClient(app_for(SimpleNamespace(get_limit_up_pool=pool))) as client:
        response = client.get(f"/api/limit-up?date={requested}")
    assert response.status_code == status


@pytest.mark.parametrize("requested", ["2026-10-xx", "2026-13-01"])
def test_bad_date_does_not_query_calendar(monkeypatch, requested):
    async def calendar(_provider):
        raise AssertionError("malformed date must fail before calendar")

    monkeypatch.setattr(trade_calendar, "trading_days", calendar)
    with TestClient(app_for(SimpleNamespace())) as client:
        response = client.get(f"/api/limit-up?date={requested}")
    assert response.status_code == 422


def test_calendar_failure_is_unknown_not_empty_pool(monkeypatch):
    async def calendar(_provider):
        raise RuntimeError("isolated calendar failure")

    monkeypatch.setattr(trade_calendar, "trading_days", calendar)
    with TestClient(app_for(SimpleNamespace())) as client:
        response = client.get("/api/limit-up?date=2026-10-09")
    assert response.status_code == 503
    assert "交易日历不可用" in response.json()["detail"]


def test_default_date_and_explicit_date_share_source_without_mutating_evidence(monkeypatch):
    calls = []
    metadata_calls = []

    class Row:
        consecutive_boards = 2
        trade_date = DAY

        def model_dump(self, mode):
            return {"symbol": "600000", "reason": "来源原文", "source": "ths"}

    async def calendar(_provider):
        return [DAY]

    async def default_day(_hub):
        return DAY

    async def pool(day):
        calls.append(day)
        await asyncio.sleep(0)
        return [Row()]

    async def metadata(_hub, day):
        metadata_calls.append(day)
        return {"generated_at": "2026-10-10T08:00:00+08:00"}

    monkeypatch.setattr(trade_calendar, "trading_days", calendar)
    monkeypatch.setattr(market_pools, "default_trade_date", default_day)
    monkeypatch.setattr(market_pools, "dated_meta", metadata)
    hub = SimpleNamespace(provider=SimpleNamespace(get_limit_up_pool=pool))
    request = SimpleNamespace(app=FastAPI())

    async def run():
        first, second = await asyncio.gather(
            market_pools.limit_up(request, date_str=None, hub=hub),
            market_pools.limit_up(request, date_str=DAY.isoformat(), hub=hub),
        )
        assert first["data"]["trade_date"] == DAY.isoformat()
        assert second["data"]["pool"][0]["reason"] == "来源原文"
        first["data"]["pool"][0]["reason"] = "caller mutation"
        first["meta"]["generated_at"] = "caller time"
        third = await market_pools.limit_up(request, date_str=None, hub=hub)
        assert third["data"]["pool"][0]["reason"] == "来源原文"
        assert third["meta"]["generated_at"] == "2026-10-10T08:00:00+08:00"
        assert third["meta"]["cached"] is True

    asyncio.run(run())
    assert calls == [DAY]
    assert metadata_calls == [DAY]


def test_source_failure_is_not_cached_as_empty(monkeypatch):
    calls = []

    async def calendar(_provider):
        return [DAY]

    async def pool(day):
        calls.append(day)
        if len(calls) == 1:
            raise RuntimeError("temporary source failure")
        return []

    async def metadata(_hub, _day):
        return {"is_stale": True}

    monkeypatch.setattr(trade_calendar, "trading_days", calendar)
    monkeypatch.setattr(market_pools, "dated_meta", metadata)
    hub = SimpleNamespace(provider=SimpleNamespace(get_limit_up_pool=pool))
    request = SimpleNamespace(app=FastAPI())

    async def run():
        with pytest.raises(HTTPException) as exc:
            await market_pools.limit_up(request, date_str=DAY.isoformat(), hub=hub)
        assert exc.value.status_code == 502
        recovered = await market_pools.limit_up(request, date_str=DAY.isoformat(), hub=hub)
        assert recovered["data"]["pool"] == []
        assert recovered["meta"]["cached"] is False

    asyncio.run(run())
    assert calls == [DAY, DAY]


def test_wrong_row_date_is_rejected_before_caching(monkeypatch):
    calls = []

    async def calendar(_provider):
        return [DAY]

    async def pool(day):
        calls.append(day)
        return [SimpleNamespace(trade_date=date(2026, 10, 8))]

    monkeypatch.setattr(trade_calendar, "trading_days", calendar)
    hub = SimpleNamespace(provider=SimpleNamespace(get_limit_up_pool=pool))
    request = SimpleNamespace(app=FastAPI())

    async def run():
        for _ in range(2):
            with pytest.raises(HTTPException) as exc:
                await market_pools.limit_up(request, date_str=DAY.isoformat(), hub=hub)
            assert exc.value.status_code == 502
            assert "拒绝混日" in exc.value.detail

    asyncio.run(run())
    assert calls == [DAY, DAY]
