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


def test_raw_daily_kline_uses_none_adjust(monkeypatch):
    captured: dict = {}

    async def fake_get(path, params):
        captured["path"] = path
        captured["params"] = params
        return {
            "item": [{
                "date_ms": 1756684800000, "open_price": 10.0, "high_price": 11.0,
                "low_price": 9.5, "close_price": 10.5, "volume": 1000, "turnover": 10500.0,
            }]
        }

    p = _provider()
    monkeypatch.setattr(p, "_get", fake_get)
    bars = asyncio.run(p.get_raw_daily_kline("600519"))
    assert captured["path"] == "/api/a-share/prices/historical"
    assert captured["params"]["adjust"] == "none"
    assert bars[0].close == 10.5 and bars[0].source == "ths"


def test_kline_empty_raises(monkeypatch):
    async def fake_get(path, params):
        return {"item": []}

    p = _provider()
    monkeypatch.setattr(p, "_get", fake_get)
    with pytest.raises(ProviderError, match="empty"):
        asyncio.run(p.get_kline("600519", "1d"))


@pytest.mark.parametrize("symbol,thscode", [
    ("sh000001", "000001.SH"), ("sz399001", "399001.SZ"),
    ("sz399006", "399006.SZ"), ("sh000688", "000688.SH"),
    ("sh000300", "000300.SH"), ("sh000852", "000852.SH"),
    ("000001.SH", "000001.SH"), ("399006.SZ", "399006.SZ"),
])
def test_explicit_catalog_index_uses_index_history_without_stock_adjust(monkeypatch, symbol, thscode):
    captured = {}

    async def fake_get(path, params):
        captured.update(path=path, params=params)
        return {"thscode": thscode, "interval": "1d", "adjust": None, "item": [{
            "date_ms": 1792339200000, "open_price": 3694.12, "high_price": 3743.8,
            "low_price": 3692.29, "close_price": 3730.92, "volume": 41779125000,
            "turnover": 687009730000,
        }]}

    p = _provider()
    monkeypatch.setattr(p, "_get", fake_get)
    bars = asyncio.run(p.get_kline(symbol, "1d"))
    assert captured["path"] == "/api/a-share-index/prices/historical"
    assert captured["params"]["thscode"] == thscode
    assert "adjust" not in captured["params"]
    assert bars[0].symbol == symbol and bars[0].close == 3730.92
    assert bars[0].volume == 41779125000  # Official index history is already shares.


@pytest.mark.parametrize("symbol,thscode", [
    ("000001", "000001.SZ"), ("sz000001", "000001.SZ"),
    ("000001.SZ", "000001.SZ"), ("600519", "600519.SH"),
    ("sh600519", "600519.SH"), ("600519.SH", "600519.SH"),
    ("920001", "920001.BJ"), ("bj920001", "920001.BJ"),
])
def test_stock_identity_and_forward_adjust_survive_index_routing(monkeypatch, symbol, thscode):
    captured = {}

    async def fake_get(path, params):
        captured.update(path=path, params=params)
        return {"thscode": thscode, "item": [{"date_ms": 1756684800000,
                "open_price": 10, "high_price": 11, "low_price": 9, "close_price": 10.5}]}

    p = _provider()
    monkeypatch.setattr(p, "_get", fake_get)
    bars = asyncio.run(p.get_kline(symbol, "1d"))
    assert captured["path"] == "/api/a-share/prices/historical"
    assert captured["params"]["thscode"] == thscode
    assert captured["params"]["adjust"] == "forward"
    assert bars[0].symbol == symbol


@pytest.mark.parametrize("symbol", [
    "sh000001.SZ", "sz600519", "600519.SZ", "bj000001", "000001.BJ",
    "sh00001", "000001.SHjunk", "sh000001,sz399006", "not-a-code", "sz399999",
])
def test_ambiguous_or_malformed_kline_identity_never_reaches_source(monkeypatch, symbol):
    async def no_request(*_args, **_kwargs):
        pytest.fail("invalid identity reached the upstream source")

    p = _provider()
    monkeypatch.setattr(p, "_get", no_request)
    with pytest.raises(ProviderError, match="身份"):
        asyncio.run(p.get_kline(symbol, "1d"))


@pytest.mark.parametrize("patch", [
    {"thscode": "000001.SZ"}, {"thscode": None}, {"interval": "1w"}, {"adjust": "forward"},
])
def test_index_history_requires_the_requested_identity_and_unadjusted_daily_basis(monkeypatch, patch):
    async def fake_get(*_args):
        return {"thscode": "000001.SH", "interval": "1d", "adjust": None,
                "item": [{"date_ms": 1792339200000, "open_price": 3700,
                          "high_price": 3750, "low_price": 3690, "close_price": 3730}], **patch}

    p = _provider()
    monkeypatch.setattr(p, "_get", fake_get)
    with pytest.raises(ProviderError, match="身份|口径"):
        asyncio.run(p.get_kline("sh000001", "1d"))


def test_raw_index_history_still_has_no_adjust_parameter(monkeypatch):
    captured = {}

    async def fake_get(path, params):
        captured.update(path=path, params=params)
        return {"thscode": "000001.SH", "interval": "1d", "adjust": None,
                "item": [{"date_ms": 1792339200000, "open_price": 3700,
                          "high_price": 3750, "low_price": 3690, "close_price": 3730}]}

    p = _provider()
    monkeypatch.setattr(p, "_get", fake_get)
    bars = asyncio.run(p.get_raw_daily_kline("sh000001"))
    assert captured["path"] == "/api/a-share-index/prices/historical"
    assert "adjust" not in captured["params"] and bars[0].symbol == "sh000001"


def test_index_source_failure_falls_back_without_changing_market_identity(monkeypatch):
    from app.data_providers.composite import CompositeProvider
    from app.schemas.market import Kline

    requested = []

    async def limited(path, params):
        requested.append((path, params))
        raise ProviderError("ths index history HTTP 429")

    class ExistingTencent:
        name = "tencent"

        async def get_kline(self, symbol, timeframe, start, end):
            assert symbol == "sh000001" and timeframe == "1d"
            return [Kline(symbol=symbol, timeframe=timeframe, ts=datetime.fromisoformat(
                "2026-10-19T00:00:00+08:00"), open=3700, high=3750, low=3690,
                close=3730, source="tencent")]

    p = _provider()
    monkeypatch.setattr(p, "_get", limited)
    bars = asyncio.run(CompositeProvider([p, ExistingTencent()]).get_kline("sh000001", "1d", None, None))
    assert requested[0][0] == "/api/a-share-index/prices/historical"
    assert requested[0][1]["thscode"] == "000001.SH"
    assert bars[0].symbol == "sh000001" and bars[0].source == "tencent"
