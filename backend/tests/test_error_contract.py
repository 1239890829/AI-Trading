"""B1 统一错误契约：所有错误响应必须形态一致 {detail, code}。

用最小 app 挂同一 register_error_handlers 验证四层 handler——
不 import app.main（避免 lifespan 拉起 provider 链）。
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from app.core.errors import AppError, UpstreamError, register_error_handlers
from app.core.inflight_reads import LocalReadCapacityExceeded


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/app-error")
    async def app_error():
        raise UpstreamError("涨停池数据源失败：ths HTTP 500")

    @app.get("/custom-code")
    async def custom():
        raise AppError("参数不合法", code="bad_symbol", status_code=400)

    @app.get("/http-exc")
    async def http_exc():
        raise HTTPException(status_code=404, detail="000001 无盘口数据")

    @app.get("/boom")
    async def boom():
        raise ValueError("意外炸了")

    @app.get("/local-capacity")
    async def local_capacity():
        raise LocalReadCapacityExceeded()

    @app.get("/needs-int")
    async def needs_int(n: int):
        return {"n": n}

    return app


def test_app_error_shape():
    c = TestClient(_app())
    r = c.get("/app-error")
    assert r.status_code == 502
    body = r.json()
    assert body["code"] == "upstream_failed"
    assert "涨停池数据源失败" in body["detail"]


def test_app_error_custom_code():
    c = TestClient(_app())
    r = c.get("/custom-code")
    assert r.status_code == 400
    assert r.json()["code"] == "bad_symbol"


def test_http_exception_keeps_detail_adds_code():
    """前端 api.ts 读 body.detail——旧契约必须保持，code 是增量。"""
    c = TestClient(_app())
    r = c.get("/http-exc")
    assert r.status_code == 404
    body = r.json()
    assert body["detail"] == "000001 无盘口数据"
    assert body["code"] == "http_404"


def test_validation_error_422_shape():
    c = TestClient(_app())
    r = c.get("/needs-int", params={"n": "abc"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert "n" in body["detail"]


def test_unhandled_500_unified_body():
    c = TestClient(_app(), raise_server_exceptions=False)
    r = c.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "internal_error"
    assert body["detail"] == "服务器内部错误"
    assert "意外炸了" not in r.text  # 堆栈细节不外漏


def test_local_capacity_is_503_and_not_a_provider_error():
    c = TestClient(_app())
    r = c.get("/local-capacity")
    assert r.status_code == 503
    assert r.json() == {"detail": "当前读取繁忙，请稍后重试", "code": "local_read_capacity_exceeded"}


@pytest.mark.parametrize("path", ["/kline/600519", "/order-book/600519", "/minute-line/600519", "/trades/600519"])
def test_market_readers_preserve_local_busy_without_secondary_source(path, monkeypatch):
    from types import SimpleNamespace
    from app.api.deps import get_hub
    from app.api.routes.market_quotes import router
    from app.market import minute_backfill, tdx_tick, tencent_tick
    from app.core.config import settings

    calls = []
    async def fail():
        calls.append(True)
        raise LocalReadCapacityExceeded()
    class Provider:
        name = "offline-local-capacity"
        async def get_kline(self, symbol, timeframe, start, end):
            return await fail()
        async def get_order_book(self, symbol):
            return await fail()
        async def get_minute_line(self, symbol):
            return await fail()
        async def get_trades(self, symbol):
            return await fail()
    def secondary(*args, **kwargs):
        calls.append("unexpected-secondary")
        raise AssertionError("local admission must not trigger source failover")
    monkeypatch.setattr(minute_backfill, "tdx_minute_line_fallback", secondary)
    monkeypatch.setattr(tdx_tick, "fetch_tdx_trades", secondary)
    monkeypatch.setattr(tencent_tick, "fetch_tencent_trades", secondary)
    monkeypatch.setattr(settings, "trades_tdx_fallback_enabled", True)
    monkeypatch.setattr(settings, "trades_tencent_http_fallback_enabled", True)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_hub] = lambda: SimpleNamespace(provider=Provider())
    with TestClient(app) as client:
        r = client.get(path)
    assert r.status_code == 503
    assert r.json() == {"detail": "当前读取繁忙，请稍后重试", "code": "local_read_capacity_exceeded"}
    assert calls == [True]


@pytest.mark.parametrize("path", [
    "/news/600519", "/announcements/600519", "/news/digest/600519",
    "/limit-up?date=2026-10-29", "/limit-up?date=2026-10-29&include_breaks=true",
    "/themes/catalog?kind=industry", "/themes/catalog/quotes?scope=directory",
    "/themes/catalog/quotes?codes=885517.TI",
])
def test_shared_input_consumers_do_not_relabel_local_busy_or_cache_it(path, monkeypatch):
    from types import SimpleNamespace as NS
    from app.api.deps import get_hub
    from app.api.routes import market_stock, market_pools, theme_catalog, news
    from app.core.inflight_reads import InflightReads, read_inflight

    async def unexpected(*args, **kwargs):
        pytest.fail("local admission must not start a source factory")

    async def valid_date(*args, **kwargs):
        return None

    provider = NS(name="offline", get_news=unexpected, get_announcements=unexpected,
                  get_limit_up_pool=unexpected, get_limit_break_pool=unexpected,
                  _inflight_source_reads=InflightReads(maxsize=0))
    svc = NS(_inflight_source_reads=InflightReads(maxsize=0))

    async def directory(kind):
        if "codes=" in path:
            return {"items": [{"code": "885517.TI"}]}
        return await read_inflight(svc, ("official-directory", kind), unexpected)

    async def ranking(kind, *, fresh=False):
        return await read_inflight(svc, ("official-board-ranking", kind, fresh), unexpected)

    async def snapshot(codes):
        return await read_inflight(svc, ("official-board-snapshot", tuple(codes)), unexpected)

    svc.read_board_catalog, svc.read_board_ranking, svc.read_board_snapshots = directory, ranking, snapshot
    monkeypatch.setattr(market_pools, "verify_pool_date", valid_date)
    monkeypatch.setattr(news, "SummaryRouter", lambda **kwargs: pytest.fail("busy inputs must not start summary"))
    app = FastAPI()
    register_error_handlers(app)
    for router in (market_stock.router, market_pools.router, theme_catalog.router, news.router):
        app.include_router(router)
    app.dependency_overrides[get_hub] = lambda: NS(provider=provider)
    app.dependency_overrides[theme_catalog.get_service] = lambda: svc
    with TestClient(app) as client:
        for _ in range(2):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json() == {"detail": "当前读取繁忙，请稍后重试", "code": "local_read_capacity_exceeded"}
    for value in app.state._state.values():
        if hasattr(value, "_data"):
            assert not value._data, "local busy must not be cached as a successful source result"


def test_nested_theme_board_busy_keeps_global_503_contract(monkeypatch):
    from datetime import date
    from types import SimpleNamespace as NS
    from app.api.routes import market_themes
    from app.services import theme_service

    async def busy(*args, **kwargs):
        raise LocalReadCapacityExceeded()

    monkeypatch.setattr(theme_service, "build_theme_board", busy)
    monkeypatch.setattr(market_themes, "load_snapshot_map", lambda *args: {})
    app = FastAPI()
    register_error_handlers(app)
    app.state.snapshot_service = NS()

    @app.get("/nested-theme")
    async def read(request: Request):
        return await market_themes._theme_board_cached(request, NS(provider=NS()), date(2026, 10, 29))

    with TestClient(app) as client:
        response = client.get("/nested-theme")
    assert response.status_code == 503
    assert response.json()["code"] == "local_read_capacity_exceeded"
