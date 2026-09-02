"""ths prices-historical 日 K 解析单测（P1-B 配套；网络口径一致性见 parity 实测
2026-09-02：60/60 与腾讯 qfq 全一致，最大偏差 0.0023%，数据在调研文档 §9）。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from app.data_providers.eastmoney import ProviderError
from app.data_providers.ths import ThsFuyaoProvider


def _provider() -> ThsFuyaoProvider:
    return ThsFuyaoProvider(api_key="test-key")


def test_kline_rejects_non_daily():
    with pytest.raises(ProviderError, match="1d"):
        asyncio.run(_provider().get_kline("600519", "5m"))


def test_kline_rejects_bad_range():
    end = datetime(2026, 9, 1)
    with pytest.raises(ProviderError, match="区间非法"):
        asyncio.run(_provider().get_kline("600519", "1d", start=end, end=end - timedelta(days=1)))


def test_kline_rejects_window_over_10y():
    end = datetime(2026, 9, 1)
    with pytest.raises(ProviderError, match="10 年"):
        asyncio.run(_provider().get_kline("600519", "1d", start=end - timedelta(days=3651), end=end))


def test_kline_params_and_parsing(monkeypatch):
    captured: dict = {}

    async def fake_get(path, params):
        captured["path"] = path
        captured["params"] = params
        return {
            "item": [
                {"date_ms": 1756684800000, "open_price": 10.0, "high_price": 11.0,
                 "low_price": 9.5, "close_price": 10.5, "volume": 1000, "turnover": 10500.0},
                {"date_ms": 1756771200000, "open_price": 10.5, "high_price": 12.0,
                 "low_price": 10.4, "close_price": 11.55, "volume": 2000, "turnover": 23000.0},
            ]
        }

    p = _provider()
    monkeypatch.setattr(p, "_get", fake_get)
    bars = asyncio.run(p.get_kline("600519", "1d"))

    assert captured["path"] == "/api/a-share/prices/historical"
    params = captured["params"]
    assert params["thscode"] == "600519.SH"
    assert params["interval"] == "1d"
    assert params["adjust"] == "forward"
    assert isinstance(params["start"], int) and isinstance(params["end"], int)
    assert params["end"] > params["start"]

    assert [b.close for b in bars] == [10.5, 11.55]
    assert bars[0].change_pct is None  # 首根无前收
    assert bars[1].change_pct == 10.0  # (11.55-10.5)/10.5 自算
    assert all(b.source == "ths" and b.symbol == "600519" and b.timeframe == "1d" for b in bars)
    assert bars[-1].ts.tzinfo is not None  # 上海时区 aware


def test_kline_empty_raises(monkeypatch):
    async def fake_get(path, params):
        return {"item": []}

    p = _provider()
    monkeypatch.setattr(p, "_get", fake_get)
    with pytest.raises(ProviderError, match="empty"):
        asyncio.run(p.get_kline("600519", "1d"))
