"""飙升榜（B1）与单股热榜排名趋势（B2）测试——零网络，假响应。

覆盖：provider 解析（坏代码剔除/字符串 heat）/飙升榜空集抛错/排名趋势空集合法、
composite 允许空语义（趋势 6 连调不打熔断——变异验证点）、路由 422/200/note。
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_hub
from app.api.routes import market as market_route
from app.data_providers.composite import CompositeProvider
from app.data_providers.eastmoney import ProviderError
from app.data_providers.ths import ThsFuyaoProvider


def _run(coro):
    return asyncio.run(coro)


def _provider(monkeypatch, responder):
    """ThsFuyaoProvider + _get 桩；返回 (provider, calls)。"""
    p = ThsFuyaoProvider(api_key="test-key")
    calls: list[tuple[str, dict]] = []

    async def fake_get(path, params):
        calls.append((path, dict(params)))
        return responder(path, params)

    monkeypatch.setattr(p, "fake_get", fake_get, raising=False)
    monkeypatch.setattr(p, "_get", fake_get)
    return p, calls


# ---------- provider 层 ----------

_SKY = {
    "timestamp": 1788147857073,
    "item": [
        {"thscode": "300001.SZ", "ticker": "300001", "name": "领涨股",
         "rank": 1, "heat": "1200000", "rank_change": 88, "rank_trend": "up"},
        {"thscode": "x.SH", "ticker": "BAD", "name": "坏代码",
         "rank": 2, "heat": "1", "rank_change": 0, "rank_trend": "flat"},
    ],
}


def test_skyrocket_parses_rows(monkeypatch):
    p, calls = _provider(monkeypatch, lambda path, params: _SKY)
    rows = _run(p.get_skyrocket_list("day"))
    assert calls[0][0] == "/api/a-share/special-data/skyrocket-list"
    assert calls[0][1] == {"period": "day"}
    assert [r["symbol"] for r in rows] == ["300001"], "坏代码剔除"
    assert rows[0]["rank_change"] == 88
    assert rows[0]["heat"] == 1200000.0, "heat 字符串 → 数值"
    assert rows[0]["source"] == "ths"


def test_skyrocket_empty_raises(monkeypatch):
    """飙升榜空集=异常（与热股榜同语义），喂熔断器由 _call 处理。"""
    p, _ = _provider(monkeypatch, lambda path, params: {"timestamp": None, "item": []})
    try:
        _run(p.get_skyrocket_list("hour"))
        raise AssertionError("should raise")
    except ProviderError:
        pass


def test_hot_stock_list_refactor_keeps_behavior(monkeypatch):
    """共用解析 helper 重构后热股榜行为不变（防回归）。"""
    p, _ = _provider(monkeypatch, lambda path, params: _SKY)
    rows = _run(p.get_hot_stock_list("day"))
    assert rows[0]["symbol"] == "300001" and rows[0]["rank"] == 1


def test_hot_rank_trend_parses_and_sends_iso_dates(monkeypatch):
    from datetime import date

    p, calls = _provider(monkeypatch, lambda path, params: {
        "timestamp": 1788100000000,
        "item": [
            {"thscode": "600519.SH", "ticker": "600519", "date": "2026-09-05", "date_ms": 1, "rank": 12},
            {"thscode": "600519.SH", "ticker": "600519", "date": "2026-09-04", "date_ms": 2, "rank": 40},
        ],
    })
    rows = _run(p.get_hot_rank_trend("600519", date(2026, 9, 1), date(2026, 9, 7)))
    assert calls[0][1] == {
        "thscode": "600519.SH",
        "start_date": "2026-09-01",
        "end_date": "2026-09-07",
    }
    assert [r["rank"] for r in rows] == [12, 40]
    assert all(r["symbol"] == "600519" for r in rows)


def test_hot_rank_trend_empty_is_ok(monkeypatch):
    """区间内从未上榜 → 空集合法（官方行为：无排名日期正常缺失），不抛错。"""
    from datetime import date

    p, _ = _provider(monkeypatch, lambda path, params: {"timestamp": None, "item": []})
    assert _run(p.get_hot_rank_trend("600519", date(2026, 9, 1), date(2026, 9, 7))) == []


# ---------- composite 层 ----------

class _TrendThs:
    name = "ths"
    realtime = False

    async def get_hot_rank_trend(self, symbol, start, end):
        return []

    async def get_skyrocket_list(self, period):
        raise RuntimeError("upstream down")


def test_composite_trend_empty_never_breaks():
    """空集连续调用不打熔断（_call_allow_empty 语义，变异验证点）。"""
    chain = CompositeProvider([_TrendThs()])
    from datetime import date

    for _ in range(6):  # 6 次 > 阈值 3，若计失败必炸
        assert _run(chain.get_hot_rank_trend("600519", date(2026, 9, 1), date(2026, 9, 7))) == []


def test_composite_skyrocket_error_raises():
    chain = CompositeProvider([_TrendThs()])
    try:
        _run(chain.get_skyrocket_list("day"))
        raise AssertionError("should raise")
    except ProviderError as exc:
        assert "all providers failed" in str(exc)


# ---------- 路由层 ----------

class _FakeProvider:
    name = "fake"

    def __init__(self, sky_rows, trend_points):
        self._sky = sky_rows
        self._trend = trend_points

    async def get_skyrocket_list(self, period):
        return list(self._sky)

    async def get_hot_rank_trend(self, symbol, start, end):
        return list(self._trend)


class _FakeHub:
    def __init__(self, provider):
        self.provider = provider
        self.last_success_refresh = None

    def is_stale(self):
        return False


def _client(sky_rows, trend_points) -> TestClient:
    app = FastAPI()
    app.include_router(market_route.router)
    app.dependency_overrides[get_hub] = lambda: _FakeHub(_FakeProvider(sky_rows, trend_points))
    return TestClient(app)


_SKY_ROW = {"rank": 1, "symbol": "300001", "name": "领涨股", "heat": 1200000.0,
            "rank_change": 88, "ts": "t", "source": "ths"}


def test_route_skyrocket_ok_and_validates_period():
    c = _client([_SKY_ROW], [])
    resp = c.get("/market/heat/skyrocket")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["rows"][0]["symbol"] == "300001" and data["period"] == "day"
    assert c.get("/market/heat/skyrocket", params={"period": "week"}).status_code == 422


def test_route_rank_trend_validates_symbol():
    c = _client([], [])
    assert c.get("/market/heat/rank-trend", params={"symbol": "60051"}).status_code == 422
    assert c.get("/market/heat/rank-trend", params={"symbol": "abc123"}).status_code == 422
    assert c.get("/market/heat/rank-trend", params={"symbol": "600519", "days": 400}).status_code == 422


def test_route_rank_trend_empty_has_explicit_note():
    """空集必须带显式 note——绝不静默。"""
    c = _client([], [])
    resp = c.get("/market/heat/rank-trend", params={"symbol": "600519"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["points"] == []
    assert "未上榜" in data["note"]


def test_route_rank_trend_ok():
    c = _client([], [{"symbol": "600519", "date": "2026-09-05", "rank": 12, "source": "ths"}])
    resp = c.get("/market/heat/rank-trend", params={"symbol": "600519", "days": 30})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["points"][0]["rank"] == 12
    assert data["note"] is None
