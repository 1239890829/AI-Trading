"""P1-6 relay_rank 单测：排序逻辑（mock 池+K 线）+ P0-3 并发化/路由缓存。"""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.picks.relay_rank import _factors, compute_relay_rank


def _bar(d: str, o, h, l, c):
    class _B:
        ts = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        open, high, low, close, volume = o, h, l, c, 1.0
    return _B()


class _Rec:
    def __init__(self, symbol, name="", boards=1, reason=""):
        self.symbol, self.name, self.consecutive_boards, self.reason = symbol, name, boards, reason


class _MockProvider:
    """返回预设 K 线；按 symbol 给不同形态。"""
    def __init__(self, series: dict[str, list]):
        self._series = series

    async def get_limit_up_pool(self, trade_date):
        return [_Rec(s) for s in self._series]

    async def get_kline(self, symbol, timeframe):
        assert timeframe == "1d"  # 修过的口径
        return self._series[symbol]


def test_factors_kmid2_max20():
    # 涨停创新高：close=high>前高 → kmid2 高、max20<1
    rows = [_bar(f"2026-09-{i:02d}", 8.0, 9.0, 7.8, 9.0) for i in range(1, 21)]
    rows += [_bar("2026-09-20", 9.0, 10.0, 8.9, 10.0)]  # 当日涨停创新高
    fx = _factors(rows)
    assert fx["kmid2"] == pytest.approx(1.0 / 1.1, abs=1e-6)  # 实体1 / 全距1.1
    assert fx["max20"] == pytest.approx(9.0 / 10.0, abs=1e-6)  # 前20高9.0 / 收盘10.0


def test_compute_rank_order():
    # A 创新高涨停（kmid2 高、max20<1）应排 B（不创新高但 kmid2 高）前
    bars_a = [_bar(f"2026-09-{i:02d}", 10, 10.5, 9.9, 10.5) for i in range(1, 21)]
    bars_a += [_bar("2026-09-28", 10.5, 11.2, 10.4, 11.2)]
    bars_b = [_bar(f"2026-09-{i:02d}", 10, 10.5, 9.9, 10.5) for i in range(1, 21)]
    bars_b += [_bar("2026-09-28", 10.5, 11.0, 10.4, 11.0)]  # 未破前高 11.0<prior? prior=10.5 故创新高
    # 让 B 不创新高：prior 高 11.2
    bars_b = [_bar(f"2026-09-{i:02d}", 10, 11.5, 9.9, 10.5) for i in range(1, 21)]
    bars_b += [_bar("2026-09-28", 10.5, 11.0, 10.4, 11.0)]
    p = _MockProvider({"600001": bars_a, "600002": bars_b})
    out = asyncio_run(compute_relay_rank(p, date(2026, 9, 28)))
    assert out[0]["symbol"] == "600001"  # 创新高者排前


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


# ------------------------------------------------- P0-3 并发化 + 路由缓存

def _ok_bars():
    return [_bar(f"2026-09-{i:02d}", 8.0, 9.0, 7.8, 9.0) for i in range(1, 22)]


def test_fetch_is_concurrent_and_capped():
    """P0-3：池内逐只取 K 必须并发（原为串行 await），且并发有上限。

    原实现单请求耗时 = 池大小 × RTT，涨停 60+ 只时数十秒；无界 gather 又会把
    上游打出限流，所以这里同时钉住「确实并发了」与「没有超过信号量上限」。
    """
    import asyncio

    from app.picks.relay_rank import MAX_CONCURRENCY

    in_flight = 0
    peak = 0

    class _P:
        async def get_kline(self, symbol, timeframe):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)  # 让出事件循环：串行实现下 peak 恒为 1
            in_flight -= 1
            return _ok_bars()

    pool = [_Rec(f"600{i:03d}") for i in range(40)]
    out = asyncio_run(compute_relay_rank(_P(), date(2026, 9, 28), pool=pool))

    assert peak > 1, "未并发：逐只 await 会让峰值并发恒为 1"
    assert peak <= MAX_CONCURRENCY, "并发无上限 = 自我 DDoS"
    assert len(out) == 15  # TOP_N 截断不变


def test_single_symbol_failure_degrades_only_itself():
    """并发化后单只失败仍只降级自己（gather 不得把整池带崩）。"""
    class _P:
        async def get_kline(self, symbol, timeframe):
            if symbol == "600002":
                raise RuntimeError("boom")
            return _ok_bars()

    pool = [_Rec("600001"), _Rec("600002"), _Rec("600003")]
    out = asyncio_run(compute_relay_rank(_P(), date(2026, 9, 28), pool=pool))
    assert {r["symbol"] for r in out} == {"600001", "600003"}


def test_route_relay_rank_cached(monkeypatch: pytest.MonkeyPatch):
    """P0-3：端点加 300s 缓存——该结果按日变化，前端分钟级轮询不该反复重算。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import picks_intraday as route

    calls = {"n": 0}

    async def fake_compute(provider, trade_date, pool=None):
        calls["n"] += 1
        return [{"symbol": "600001", "name": "甲", "boards": 2, "reason": "",
                 "kmid2": 0.9, "max20": 0.8}]

    # 路由内是函数级 `from app.picks.relay_rank import compute_relay_rank`，
    # 名字在调用时从模块取 ⇒ 打桩点即该模块属性。
    monkeypatch.setattr("app.picks.relay_rank.compute_relay_rank", fake_compute)

    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.state.hub = SimpleNamespace(provider=object())
    with TestClient(app) as client:
        b1 = client.get("/api/picks/relay-rank").json()
        b2 = client.get("/api/picks/relay-rank").json()

    assert calls["n"] == 1, "300s TTL 内第二次请求必须命中缓存"
    assert b1 == b2
    assert b1["data"]["items"][0]["symbol"] == "600001"
