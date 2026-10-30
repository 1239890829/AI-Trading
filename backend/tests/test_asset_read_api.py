"""Actual asset read routes retain paired reads and unified error contracts."""
import asyncio

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import httpx

from app.api.deps import require_api_token
from app.api.routes.assets import router
from app.assets.service import AssetReadService
from app.core import device_access as access
from app.core.config import settings
from app.core.errors import register_error_handlers


def assembled(service=None, *, authenticated=False):
    app = FastAPI()
    if service is not None:
        app.state.asset_service = service
    register_error_handlers(app)
    app.include_router(router, prefix="/api",
                       dependencies=[Depends(require_api_token)] if authenticated else [])
    return app


def test_read_routes_require_pairing_but_not_maintenance(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "api_token", "fixture-service")
    monkeypatch.setattr(access, "STORE_PATH", tmp_path / "device.sqlite3")
    store = access.get_device_store()
    origin = "http://fund.test:3000"
    admin, _ = store.bootstrap(store.bootstrap_code(), "本机", origin)
    token, row = store.start("基金普通设备", origin)
    store.manage(admin, origin, row["id"], "approve")
    assert store.authorize(token, origin)["maintenance"] is False
    headers = {"X-API-Token": "fixture-service", access.BROWSER_HEADER: "1",
               access.SESSION_HEADER: token, access.ORIGIN_HEADER: origin}
    # Disabled reads are deterministic and cannot touch a real provider.
    service = AssetReadService()
    try:
        with TestClient(assembled(service, authenticated=True)) as client:
            for path in ("/api/assets/capabilities", "/api/assets/search?q=040046",
                         "/api/assets/funds/040046.OF/snapshot"):
                assert client.get(path).status_code == 401
            caps = client.get("/api/assets/capabilities", headers=headers)
            assert caps.status_code == 200
            assert all(item["state"] == "blocked" for item in caps.json()["data"]["items"])
            result = client.get("/api/assets/funds/040046.OF/snapshot", headers=headers)
            assert result.status_code == 503
            assert result.json()["code"] == "asset_source_disabled"
            us = client.get("/api/assets/search?q=AAPL&asset_type=us_equity", headers=headers)
            assert us.status_code == 200 and us.json()["data"]["state"] == "blocked"
    finally:
        asyncio.run(service.aclose())


def test_capacity_and_scope_errors_keep_global_http_contract_without_source_calls():
    async def run():
        calls = []
        async def source(request):
            calls.append(request.url.path)
            raise AssertionError("invalid or saturated reads must not start source")
        source_client = httpx.AsyncClient(base_url="https://fuyao.aicubes.cn",
                                         transport=httpx.MockTransport(source))
        service = AssetReadService("fixture-key", http_client=source_client)
        service._snapshots.maxsize = 0
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=assembled(service)),
                                         base_url="http://test") as client:
                busy = await client.get("/api/assets/funds/040046.OF/snapshot")
                assert busy.status_code == 503
                assert busy.json() == {"detail": "当前读取繁忙，请稍后重试", "code": "local_read_capacity_exceeded"}
                for path in ("/api/assets/funds/040046/snapshot", "/api/assets/funds/159632.SZ/snapshot",
                             "/api/assets/funds/040046.OF/snapshot?range=all", "/api/assets/search?q=a&limit=21"):
                    assert (await client.get(path)).status_code == 422
                assert calls == []
        finally:
            await service.aclose()
    asyncio.run(run())


def test_source_429_is_one_request_with_sanitized_error_and_can_recover():
    async def run():
        calls = []
        async def source(request):
            calls.append(request.url.path)
            if len(calls) == 1:
                return httpx.Response(429, text="provider-private-diagnostic")
            return httpx.Response(200, json={"code": 0, "data": {"item": []}})
        source_client = httpx.AsyncClient(base_url="https://fuyao.aicubes.cn",
                                         transport=httpx.MockTransport(source))
        service = AssetReadService("fixture-key", http_client=source_client)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=assembled(service)),
                                         base_url="http://test") as client:
                result = await client.get("/api/assets/search?q=040046")
                assert result.status_code == 502 and result.json()["code"] == "fund_source_request_failed"
                assert "provider-private-diagnostic" not in result.text and len(calls) == 1
                recovered = await client.get("/api/assets/search?q=040046")
                assert recovered.status_code == 200 and recovered.json()["data"]["state"] == "partial"
                assert recovered.json()["data"]["items"] == [] and len(calls) == 2
        finally:
            await service.aclose()
    asyncio.run(run())
