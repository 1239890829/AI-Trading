"""Current index membership: no quote subscriptions, DB writes or false empty sets."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
        from datetime import date
        from app.api.routes import market_quotes
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
