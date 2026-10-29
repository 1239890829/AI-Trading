"""Eastmoney qfq/raw daily lanes must use explicit fqt identities."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.core.bjtime import BJ_TZ
from app.data_providers.eastmoney import EastmoneyProvider, ProviderError
from app.schemas.market import Kline


def test_eastmoney_daily_basis_uses_explicit_fqt(monkeypatch):
    seen: list[str] = []
    p = EastmoneyProvider()

    async def fake_get_json(_url, params):
        seen.append(str(params["fqt"]))
        return {"data": {"klines": []}}

    monkeypatch.setattr(p, "_get_json", fake_get_json)

    async def run():
        try:
            assert await p.get_kline("600519", "1d") == []
            assert await p.get_raw_daily_kline("600519") == []
        finally:
            await p.aclose()

    asyncio.run(run())
    assert seen == ["1", "0"]


@pytest.mark.parametrize("fqt,basis", [("1", "qfq"), ("2", "hfq"), ("0", "none")])
def test_response_rows_keep_the_requested_adjustment_basis(monkeypatch, fqt, basis):
    p = EastmoneyProvider()

    async def fake_get_json(_url, params):
        assert params["fqt"] == fqt
        assert params["end"] == "20500101" and params["lmt"] == "1000"
        assert "beg" not in params
        return {"data": {"klines": ["2024-01-02,10,10.5,11,9,100,1050"]}}

    monkeypatch.setattr(p, "_get_json", fake_get_json)

    async def run():
        try:
            rows = await p._get_kline_with_fqt("600519", "1d", None, None, fqt=fqt)
            assert rows[0].price_basis == basis
            assert rows[0].close == 10.5 and rows[0].source == "eastmoney"
        finally:
            await p.aclose()

    asyncio.run(run())


def test_old_rows_do_not_inherit_an_adjustment_basis():
    row = Kline.model_validate({"symbol": "600519", "timeframe": "1d", "ts": "2024-01-02T00:00:00Z",
                               "close": 10.5, "source": "eastmoney"})
    assert row.price_basis == "unknown"


@pytest.mark.parametrize("start,end", [
    (datetime(2010, 1, 4, tzinfo=BJ_TZ), datetime(2010, 1, 4, 23, 59, 59, tzinfo=BJ_TZ)),
    (datetime(2010, 1, 3, 16, tzinfo=timezone.utc), datetime(2010, 1, 4, 15, 59, 59, tzinfo=timezone.utc)),
    (datetime(2010, 1, 4), datetime(2010, 1, 4, 23, 59, 59)),
])
def test_old_history_window_reaches_upstream_and_retains_only_requested_date(monkeypatch, start, end):
    p = EastmoneyProvider()

    async def fake_get_json(_url, params):
        assert params["beg"] == params["end"] == "20100104"
        assert params["lmt"] == "1000" and params["fqt"] == "1"
        return {"data": {"klines": [
            "2009-12-31,9,9.5,10,8,100,950", "2010-01-04,10,10.5,11,9,100,1050",
            "2010-01-05,11,11.5,12,10,100,1150",
        ]}}

    monkeypatch.setattr(p, "_get_json", fake_get_json)

    async def run():
        try:
            rows = await p.get_kline("600519", "1d", start, end)
            assert [row.ts.date().isoformat() for row in rows] == ["2010-01-04"]
            assert rows[0].price_basis == "qfq" and rows[0].close == 10.5
        finally:
            await p.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("symbol,secid,fqt,basis", [
    ("sh000001", "1.000001", "0", "none"), ("000001.SH", "1.000001", "0", "none"),
    ("sz399001", "0.399001", "0", "none"), ("399006.SZ", "0.399006", "0", "none"),
    ("000001", "0.000001", "1", "qfq"),
])
def test_explicit_index_identity_is_unadjusted_without_changing_bare_stock(monkeypatch, symbol, secid, fqt, basis):
    p = EastmoneyProvider()

    async def fake_get_json(_url, params):
        assert params["secid"] == secid and params["fqt"] == fqt
        return {"data": {"klines": ["2024-01-02,10,10.5,11,9,100,1050"]}}

    monkeypatch.setattr(p, "_get_json", fake_get_json)

    async def run():
        try:
            rows = await p.get_kline(symbol, "1d")
            assert rows[0].symbol == symbol and rows[0].price_basis == basis
        finally:
            await p.aclose()

    asyncio.run(run())


def test_reversed_history_window_stops_before_source_call(monkeypatch):
    p = EastmoneyProvider()

    async def no_request(*_args):
        pytest.fail("invalid history range reached the source")

    monkeypatch.setattr(p, "_get_json", no_request)

    async def run():
        try:
            with pytest.raises(ProviderError, match="start exceeds end"):
                await p.get_kline("600519", "1d", datetime(2024, 1, 3), datetime(2024, 1, 2))
        finally:
            await p.aclose()

    asyncio.run(run())
