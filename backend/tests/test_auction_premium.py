"""竞价溢价比因子测试（system-review §4.2 P0）：纯函数 / 采集 / 路由。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.market.auction_premium import (
    BAND_NORMAL,
    BAND_STRONG,
    BAND_UNKNOWN,
    BAND_WEAK,
    classify_premium,
    collect_premium,
    summarize_premiums,
)

_ASOF = date(2026, 9, 4)  # 周五
_PREV = date(2026, 9, 3)  # 周四


@pytest.fixture(autouse=True)
def _pin_calendar(monkeypatch: pytest.MonkeyPatch):
    """钉死交易日历：collect_premium 内部走 trade_calendar（官方历→K线→持久化回退），
    环境相关的持久化日历会让 prev 日期漂移，测试必须确定性。"""
    import app.market.trade_calendar as tc

    async def fake_trading_days(provider, lookback_days=120):
        return [_PREV, _ASOF]

    monkeypatch.setattr(tc, "trading_days", fake_trading_days)


# ---------------------------------------------------------------- 纯函数


def test_classify_bands():
    assert classify_premium(None) == BAND_UNKNOWN
    assert classify_premium(6.0) == BAND_STRONG
    assert classify_premium(5.0) == BAND_STRONG
    assert classify_premium(3.0) == BAND_NORMAL, "3% 恰在边界上属于正常承接"
    assert classify_premium(2.99) == BAND_WEAK
    assert classify_premium(-1.0) == BAND_WEAK


def test_summarize_empty_and_all_unknown():
    for pcts in ([], [None, None]):
        s = summarize_premiums(pcts)
        assert s["judged"] == 0
        assert s["median_pct"] is None and s["weak_share"] is None


def test_summarize_unknown_not_in_denominator():
    # 2 弱 + 1 强 + 1 unknown：占比分母 = 3，不含 unknown
    s = summarize_premiums([1.0, 2.0, 6.0, None])
    assert s["total"] == 4 and s["judged"] == 3 and s["unknown"] == 1
    assert s["weak_share"] == round(2 / 3, 4)
    assert s["strong_share"] == round(1 / 3, 4)
    assert s["median_pct"] == 2.0


# ---------------------------------------------------------------- 采集


class _Rec(SimpleNamespace):
    pass


def _fake_hub(pool_rows, auction_rows=None, auction_exc=None, trading_days=None):
    class _Provider:
        name = "fake"

        async def get_trading_days(self):
            return trading_days

        async def get_limit_up_pool(self, trade_date):
            assert trade_date == _PREV
            return pool_rows

        async def get_auction_snapshot(self, symbols, stage="final"):
            if auction_exc is not None:
                raise auction_exc
            return [r for r in (auction_rows or []) if r["symbol"] in set(symbols)]

    class _Hub:
        name = "fake"
        provider = _Provider()
        is_stale = lambda self: False  # noqa: E731 — _meta 需要
        last_success_refresh = None

    return _Hub()


def test_collect_premium_maps_and_bands():
    pool = [
        _Rec(symbol="600001", name="甲", consecutive_boards=2),
        _Rec(symbol="600002", name="乙", consecutive_boards=1),
        _Rec(symbol="600003", name="丙", consecutive_boards=None),
    ]
    auction = [
        {"symbol": "600001", "name": "甲", "auction_pct": 6.2, "data_status": "final"},
        {"symbol": "600002", "name": "乙", "auction_pct": 1.0, "data_status": "final"},
        {"symbol": "600003", "name": "丙", "auction_pct": None, "data_status": "final"},
    ]
    days = ["2026-09-03", "2026-09-04"]
    import asyncio

    r = asyncio.run(collect_premium(_fake_hub(pool, auction, trading_days=days), _ASOF))
    assert r["pool_date"] == _PREV.isoformat() and not r["caveats"]
    bands = {it["symbol"]: it["band"] for it in r["items"]}
    assert bands == {"600001": BAND_STRONG, "600002": BAND_WEAK, "600003": BAND_UNKNOWN}
    assert r["summary"]["judged"] == 2 and r["summary"]["unknown"] == 1


def test_collect_premium_degrades_on_pool_failure():
    class _P:
        name = "fake"

        async def get_trading_days(self):
            return ["2026-09-03", "2026-09-04"]

        async def get_limit_up_pool(self, trade_date):
            raise RuntimeError("pool down")

        async def get_auction_snapshot(self, symbols, stage="final"):  # pragma: no cover
            return []

    import asyncio

    class _H:
        name = "fake"
        provider = _P()

    r = asyncio.run(collect_premium(_H(), _ASOF))
    assert r["items"] == [] and r["summary"]["judged"] == 0
    assert any("涨停池拉取失败" in c for c in r["caveats"])


def test_collect_premium_degrades_on_auction_failure():
    import asyncio

    pool = [_Rec(symbol="600001", name="甲", consecutive_boards=1)]
    r = asyncio.run(collect_premium(
        _fake_hub(pool, auction_exc=RuntimeError("auction down"),
                  trading_days=["2026-09-03", "2026-09-04"]),
        _ASOF,
    ))
    assert any("竞价快照批次 1 拉取失败" in c for c in r["caveats"])
    assert r["items"][0]["band"] == BAND_UNKNOWN


def test_collect_premium_calendar_unavailable(monkeypatch: pytest.MonkeyPatch):
    import asyncio

    import app.market.trade_calendar as tc

    async def empty_days(provider, lookback_days=120):
        return []

    monkeypatch.setattr(tc, "trading_days", empty_days)
    r = asyncio.run(collect_premium(_fake_hub([], trading_days=None), _ASOF))
    assert r["pool_date"] is None
    assert any("交易日历" in c for c in r["caveats"])


# ---------------------------------------------------------------- 路由


def test_route_auction_premium(monkeypatch: pytest.MonkeyPatch):
    from app.api.deps import get_hub
    from app.api.routes import market as market_route

    pool = [_Rec(symbol="600001", name="甲", consecutive_boards=2)]
    auction = [{"symbol": "600001", "name": "甲", "auction_pct": 4.0, "data_status": "final"}]
    hub = _fake_hub(pool, auction, trading_days=["2026-09-03", "2026-09-04"])

    async def fake_default_trade_date(_hub):
        return _ASOF

    monkeypatch.setattr(market_route, "_default_trade_date_async", fake_default_trade_date)

    app = FastAPI()
    app.include_router(market_route.router, prefix="/api")
    app.dependency_overrides[get_hub] = lambda: hub
    with TestClient(app) as client:
        body = client.get("/api/auction-premium").json()
        assert body["data"]["pool_date"] == _PREV.isoformat()
        assert body["data"]["items"][0]["band"] == BAND_NORMAL
        assert body["data"]["summary"]["median_pct"] == 4.0
        # 命中缓存不重算（第二次调用同结果即可）
        body2 = client.get("/api/auction-premium").json()
        assert body2["data"]["summary"] == body["data"]["summary"]
