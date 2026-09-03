"""minute-line TDX 降级备源（2026-09-03）：纯函数 + 守卫 + 路由双路径。

背景：腾讯是当日分时的链上唯一实现者（单点）。2026-08-31 腾讯 WAF 封禁
分时图真断过——降级备源 TDX 直连 m1（实测 600519 全日 240 根/约 1.6s）。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.market import minute_backfill as mb


def _raw_points() -> list[dict]:
    """两个交易日的引擎 schema 点（模拟 fetch_tdx_minutes 返回）。"""
    return [
        {"ts": "2026-09-02T07:00:00+00:00", "price": 1290.0, "volume": 100.0,
         "cum_amount": 129000.0, "cum_volume": 100, "avg": 1290.0, "source": "tdx"},
        {"ts": "2026-09-03T01:31:00+00:00", "price": 1297.1, "volume": 48300.0,
         "cum_amount": 62560216.0, "cum_volume": 48300, "avg": 1295.243, "source": "tdx"},
        {"ts": "2026-09-03T07:00:00+00:00", "price": 1298.88, "volume": 18400.0,
         "cum_amount": 2305193132.0, "cum_volume": 1774800, "avg": 1298.847, "source": "tdx"},
    ]


def test_latest_day_points_picks_newest_day():
    out = mb.latest_day_points(_raw_points())
    assert [p["ts"] for p in out] == [
        "2026-09-03T01:31:00+00:00", "2026-09-03T07:00:00+00:00",
    ], "只留最新交易日的点，且保持原顺序（分时 X 轴依赖）"


def test_latest_day_points_empty():
    assert mb.latest_day_points([]) == []


def test_fallback_guard_rejects_prefixed_symbols(monkeypatch):
    """带前缀符号必须被守卫拦下（TDX 市场映射对前缀符号必然判错），且不触网。"""

    def _must_not_fetch(*a, **k):
        raise AssertionError("守卫应在前置拦截，不应走到 fetch")

    monkeypatch.setattr(mb, "fetch_tdx_minutes", _must_not_fetch)
    for bad in ("sh000001", "sz399001", "000001.SZ", "", None):
        with pytest.raises(ValueError, match="裸 6 位"):
            mb.tdx_minute_line_fallback(bad)


def test_fallback_uses_latest_day_and_marks_source(monkeypatch):
    monkeypatch.setattr(mb, "fetch_tdx_minutes", lambda *a, **k: _raw_points())
    out = mb.tdx_minute_line_fallback("600519")
    assert len(out) == 2, "只回最新交易日（2026-09-03）的点"
    assert all(p["source"] == "tdx_m1" for p in out), "source 标注降级来源"
    assert out[-1]["price"] == 1298.88


def test_route_fallback_takes_over_on_provider_failure(monkeypatch):
    from app.api.routes import market as market_route
    from app.market import minute_backfill as mb

    class _P:
        name = "chain(test)"

        async def get_minute_line(self, symbol):
            raise RuntimeError("tencent minute HTTP 403")

    class _Hub:
        provider = _P()
        last_success_refresh = None

        def is_stale(self):
            return False

    seen = []

    def _fake_fallback(symbol, *, timeout=8.0):
        seen.append(symbol)
        return [{"ts": "2026-09-03T01:31:00+00:00", "price": 1297.1, "volume": 48300.0,
                 "cum_amount": 62560216.0, "cum_volume": 48300, "avg": 1295.243,
                 "source": "tdx_m1"}]

    monkeypatch.setattr(mb, "tdx_minute_line_fallback", _fake_fallback)
    payload = asyncio.run(market_route.minute_line("600519", hub=_Hub()))
    assert seen == ["600519"], "降级函数被路由调用且传入原始符号"
    assert payload["data"]["points"][0]["source"] == "tdx_m1"
    assert payload["data"]["symbol"] == "600519"


def test_route_502_when_fallback_also_fails(monkeypatch):
    """双源皆挂 → 502，detail 带主源+备源双错（排障需要看全貌）。"""
    from app.api.routes import market as market_route
    from app.market import minute_backfill as mb

    class _P:
        name = "chain(test)"

        async def get_minute_line(self, symbol):
            raise RuntimeError("tencent minute HTTP 403")

    class _Hub:
        provider = _P()
        last_success_refresh = None

        def is_stale(self):
            return False

    def _boom(symbol, *, timeout=8.0):
        raise ValueError("tdx minute fallback empty for 600519")

    monkeypatch.setattr(mb, "tdx_minute_line_fallback", _boom)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(market_route.minute_line("600519", hub=_Hub()))
    assert ei.value.status_code == 502
    assert "tencent minute HTTP 403" in ei.value.detail, "主源错误保留"
    assert "TDX 备源" in ei.value.detail, "备源错误保留"


def test_route_index_symbol_502_with_guard_note(monkeypatch):
    """指数符号（带前缀）不进 TDX 降级（市场映射撞车风险），守卫错误进 502 detail。"""
    from app.api.routes import market as market_route

    class _P:
        name = "chain(test)"

        async def get_minute_line(self, symbol):
            raise RuntimeError("tencent minute HTTP 403")

    class _Hub:
        provider = _P()
        last_success_refresh = None

        def is_stale(self):
            return False

    with pytest.raises(HTTPException) as ei:
        asyncio.run(market_route.minute_line("sh000001", hub=_Hub()))
    assert ei.value.status_code == 502
    assert "裸 6 位" in ei.value.detail, "守卫拒绝原因可见（指数不适用 TDX 降级）"


def test_route_fallback_full_stack_with_response_model(monkeypatch):
    """TestClient 全栈：降级点必须无损过 response_model 校验（真实 FastAPI 路径）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import get_hub
    from app.api.routes import market as market_route
    from app.market import minute_backfill as mb
    from app.schemas.envelope import Envelope, MinuteLinePayload

    class _P:
        name = "chain(test)"

        async def get_minute_line(self, symbol):
            raise RuntimeError("down")

    class _Hub:
        provider = _P()
        last_success_refresh = None

        def is_stale(self):
            return False

    def _fake_fallback(symbol, *, timeout=8.0):
        return [{"ts": "2026-09-03T01:31:00+00:00", "price": 1297.1, "volume": 48300.0,
                 "cum_amount": 62560216.0, "cum_volume": 48300, "avg": 1295.243,
                 "source": "tdx_m1"}]

    monkeypatch.setattr(mb, "tdx_minute_line_fallback", _fake_fallback)
    app = FastAPI()
    app.include_router(market_route.router, prefix="/api")
    app.dependency_overrides[get_hub] = lambda: _Hub()
    with TestClient(app) as client:
        resp = client.get("/api/minute-line/600519")
    assert resp.status_code == 200
    env = Envelope[MinuteLinePayload].model_validate(resp.json())
    assert env.data.points[0].cum_volume == 48300
    assert env.data.points[0].source == "tdx_m1"
