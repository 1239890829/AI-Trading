"""Current index membership: no quote subscriptions, DB writes or false empty sets."""
import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import market_quotes
from app.api.routes.market_quotes import router
from app.services.theme_catalog_service import ThemeCatalogService


def test_index_source_validates_identity_and_keeps_snapshot_clock():
    service = ThemeCatalogService(None, api_key="fixture")
    payload = {"code": 0, "data": {"timestamp": 1792049347446, "item": [
        {"thscode": "600519.SH", "ticker": "600519", "name": "贵州茅台"},
        {"thscode": "000001.SZ", "ticker": "000001", "name": "平安银行"},
    ]}}
    calls = []

    async def respond(request):
        calls.append(request.url.params["thscode"])
        return httpx.Response(200, json=payload)

    async def run():
        await service._client.aclose()
        service._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            result = await service.fetch_index_members("000300.SH")
            assert result["snapshot_at"] == "2026-10-15T07:29:07.446000+00:00"
            assert result["items"] == [{"symbol": "600519", "name": "贵州茅台", "market": "SH"}, {"symbol": "000001", "name": "平安银行", "market": "SZ"}]
            payload["data"]["item"][0]["thscode"] = "000001.SH"
            with pytest.raises(RuntimeError):
                await service.fetch_index_members("000300.SH")
            payload["data"]["item"] = []
            with pytest.raises(RuntimeError):
                await service.fetch_index_members("000300.SH")
        finally:
            await service.aclose()
    asyncio.run(run())
    assert calls == ["000300.SH"] * 3


def test_route_only_known_index_and_cache_not_quote_cache(monkeypatch):
    monkeypatch.setattr(market_quotes, "beijing_now", lambda: SimpleNamespace(date=lambda: date(2026, 10, 15)))
    app = FastAPI()
    app.include_router(router, prefix="/api")
    service = SimpleNamespace(fetch_index_members=AsyncMock(return_value={
        "items": [{"symbol": "600519", "name": "贵州茅台", "market": "SH"}], "snapshot_at": "2026-10-15T07:29:07+00:00"}))
    app.state.theme_catalog = service
    with TestClient(app) as client:
        assert client.get("/api/indices/000001/members").status_code == 400
        assert client.get("/api/indices/sh600519/members").status_code == 400
        first = client.get("/api/indices/sh000300/members")
        second = client.get("/api/indices/sh000300/members")
        assert first.status_code == second.status_code == 200
        assert first.json()["data"]["symbol"] == "sh000300"
        assert second.json()["data"]["cached"] is True
        assert service.fetch_index_members.await_count == 1
        service.fetch_index_members.assert_awaited_with("000300.SH")
        monkeypatch.setattr(market_quotes, "beijing_now", lambda: SimpleNamespace(date=lambda: date(2026, 10, 16)))
        assert client.get("/api/indices/sh000300/members").status_code == 200
        assert service.fetch_index_members.await_count == 2
        service.fetch_index_members.side_effect = RuntimeError("synthetic source unavailable")
        assert client.get("/api/indices/sz399006/members").status_code == 502
        service.fetch_index_members.side_effect = None
        assert client.get("/api/indices/sz399006/members").status_code == 200


def test_concurrent_membership_reads_share_one_upstream():
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def fetch(code):
            entered.set()
            await release.wait()
            return {"items": [{"symbol": "600519", "name": "贵州茅台", "market": "SH"}], "snapshot_at": "2026-10-15T07:29:07+00:00"}

        service = SimpleNamespace(fetch_index_members=AsyncMock(side_effect=fetch))
        app.state.theme_catalog = service
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            first = asyncio.create_task(client.get("/api/indices/sh000300/members"))
            await entered.wait()
            second = asyncio.create_task(client.get("/api/indices/sh000300/members"))
            await asyncio.sleep(0)
            release.set()
            assert [response.status_code for response in await asyncio.gather(first, second)] == [200, 200]
            assert service.fetch_index_members.await_count == 1
    asyncio.run(run())


def test_member_percent_reads_current_snapshot_each_time_without_refetching_membership():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    state = {"pct": -2.5}
    class Snapshot:
        def versioned_snapshot(self):
            return ([{"symbol": "600519", "market": "SH", "price": 100, "prev_close": 101,
                      "change_pct": state["pct"], "source": "sina_market", "received_at": now.isoformat()}], now)
        def freshness(self):
            return SimpleNamespace(as_of=now, state="ready")
    app = FastAPI();app.include_router(router, prefix="/api")
    app.state.snapshot_service = Snapshot()
    app.state.theme_catalog = SimpleNamespace(fetch_index_members=AsyncMock(return_value={
        "items": [{"symbol": "600519", "name": "贵州茅台", "market": "SH"},
                  {"symbol": "000001", "name": "平安银行", "market": "SZ"}], "snapshot_at": "2026-10-15T07:29:07+00:00"}))
    with TestClient(app) as client:
        first = client.get("/api/indices/sh000300/members").json()["data"]
        assert first["items"][0]["change_pct"] == -2.5
        assert first["items"][1]["change_pct"] is None
        assert first["items"][0]["source_clock"] is None
        assert first["items"][0]["quote_observed_at"] == now.isoformat()
        assert first["quote_snapshot"]["known_count"] == 1
        assert first["quote_snapshot"]["total"] == 2
        assert first["quote_snapshot"]["observed_at"] == now.isoformat()
        state["pct"] = 0
        second = client.get("/api/indices/sh000300/members").json()["data"]
        assert second["items"][0]["change_pct"] == 0
        assert second["cached"] is True
        assert second["snapshot_at"] == first["snapshot_at"]
        assert app.state.theme_catalog.fetch_index_members.await_count == 1


@pytest.mark.parametrize("patch", [
    {"change_pct": None}, {"change_pct": float("nan")}, {"change_pct": float("inf")},
    {"change_pct": True}, {"price": 0}, {"prev_close": -1}, {"quality": "invalid"},
    {"source": "mock"}, {"source": ""}, {"market": "SZ"},
    {"symbol": []}, {"quality": []}, {"market": {}},
    {"data_timestamp": "2026-10-20T10:00:00"},
    {"data_timestamp": "2099-01-01T00:00:00+08:00"},
    {"received_at": "2099-01-01T00:00:00+08:00"},
])
def test_member_quote_invalid_observations_stay_unknown(patch):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    row = {"symbol": "600519", "market": "SH", "price": 100, "prev_close": 101,
           "change_pct": -1, "source": "sina_market", **patch}
    snapshot = SimpleNamespace(versioned_snapshot=lambda: ([row], now),
                               freshness=lambda: SimpleNamespace(as_of=now, state="ready"))
    items = [{"symbol": "600519", "market": "SH", "name": "贵州茅台"}]
    before = repr((items, row))
    output, meta = market_quotes._index_member_quote_observations(items, snapshot, None)
    assert output[0]["change_pct"] is None and meta["known_count"] == 0
    assert repr((items, row)) == before


def test_member_quote_duplicates_and_real_missing_are_not_zero():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    rows = [{"symbol": "600519", "market": "SH", "price": 100, "prev_close": 101,
             "change_pct": -1, "source": "sina_market"}] * 2
    snapshot = SimpleNamespace(versioned_snapshot=lambda: (rows, now),
                               freshness=lambda: SimpleNamespace(as_of=now, state="ready"))
    items = [{"symbol": "600519", "market": "SH", "name": "贵州茅台"}]
    output, meta = market_quotes._index_member_quote_observations(items, snapshot, None)
    assert output[0]["change_pct"] is None and meta["known_count"] == 0


def test_member_quotes_use_only_passive_hub_on_absent_snapshot_with_explicit_clocks():
    from datetime import datetime, timezone
    from app.schemas.market import Quote
    now = datetime.now(timezone.utc)
    quote = Quote(symbol="600519", market="SH", name="贵州茅台", price=100, prev_close=100,
                  change_pct=0, volume=1, amount=1, source="tencent", data_timestamp=now, received_at=now)
    class Hub:
        def get_quotes(self, symbols):
            assert symbols == ["600519"]
            return [quote]
        async def get_quote(self, *args):
            raise AssertionError("no upstream read")
    items = [{"symbol": "600519", "market": "SH", "name": "贵州茅台"}]
    output, meta = market_quotes._index_member_quote_observations(items, None, Hub())
    assert output[0]["change_pct"] == 0
    assert output[0]["source_clock"] == now.isoformat()
    assert output[0]["quote_observed_at"] == now.isoformat()
    assert output[0]["quote_quality"] == "high"
    assert meta["known_count"] == meta["total"] == 1
    assert meta["source_clock"] == now.isoformat()
