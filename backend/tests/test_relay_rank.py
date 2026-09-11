"""P1-6 relay_rank 单测：排序逻辑（mock 池+K 线）。"""
from __future__ import annotations

from datetime import date, datetime, timezone

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
