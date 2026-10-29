"""An uncovered requested range is not proof of a whole-source outage."""
import asyncio
from datetime import datetime

import httpx
import pytest

from app.core.bjtime import BJ_TZ
from app.data_providers.composite import CompositeProvider
from app.data_providers.eastmoney import HistoryRangeUnavailable, ProviderError
from app.data_providers.tencent import TencentProvider, parse_kline_payload


START = datetime.fromisoformat("2023-01-03T00:00:00+08:00")
END = datetime.fromisoformat("2023-01-05T23:59:59+08:00")


async def _provider(handler):
    provider = TencentProvider()
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


def _reply(symbol="sh600519", rows=None):
    return {"code": 0, "data": {symbol: {"qfqday": rows if rows is not None else [
        ["2026-10-28", "10", "10.5", "11", "9", "30"],
    ]}}}


def test_uncovered_interval_does_not_disable_other_stock_history():
    async def scenario():
        def handler(request):
            symbol = request.url.params["param"].split(",")[0]
            return httpx.Response(200, json=_reply(symbol))
        provider = await _provider(handler)
        chain = CompositeProvider([provider])
        try:
            for _ in range(3):
                with pytest.raises(ProviderError):
                    await chain.get_kline("600519", "1d", START, END)
            assert not chain._in_cooldown("get_kline", "tencent")
            assert ("get_kline", "tencent") not in chain._failures
            metrics = chain.provider_health()["method_attempts"]["get_kline@tencent"]
            assert metrics["history_range_unavailable"] == 3
            assert metrics["provider_method_error"] == 0
            bars = await chain.get_kline("000001", "1d", datetime(2026, 10, 28, tzinfo=BJ_TZ),
                                         datetime(2026, 10, 28, 23, 59, tzinfo=BJ_TZ))
            assert len(bars) == 1 and bars[0].symbol == "000001"
        finally:
            await chain.aclose()
    asyncio.run(scenario())


def test_uncovered_interval_does_not_clear_previous_source_failure():
    async def scenario():
        provider = await _provider(lambda request: httpx.Response(200, json=_reply()))
        chain = CompositeProvider([provider])
        chain._record_failure("get_kline", "tencent")
        chain._record_failure("get_kline", "tencent")
        try:
            with pytest.raises(ProviderError):
                await chain.get_kline("600519", "1d", START, END)
            assert chain._failures[("get_kline", "tencent")] == 2
            assert chain.provider_health()["last_good"] == {}
        finally:
            await chain.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [_reply(rows=[]), {"code": 0, "data": {}}, _reply(rows=[
    ["bad-date", "10", "10.5", "11", "9", "30"],
]), _reply(rows=[["2026-10-28", "10", "10.5", "8", "9", "30"]]),
    {**_reply(), "code": -1},
    _reply(rows=[["2026-10-28", "10", "10.5", "11", "9", "NaN"]]),
])
def test_empty_or_malformed_source_remains_a_real_failure(payload):
    async def scenario():
        provider = await _provider(lambda request: httpx.Response(200, json=payload))
        chain = CompositeProvider([provider])
        try:
            for _ in range(3):
                with pytest.raises(ProviderError):
                    await chain.get_kline("600519", "1d", START, END)
            assert chain._in_cooldown("get_kline", "tencent")
            assert chain._failures[("get_kline", "tencent")] == 3
        finally:
            await chain.aclose()
    asyncio.run(scenario())


def test_uncovered_range_still_tries_backup_without_declaring_end_of_history():
    async def scenario():
        provider = await _provider(lambda request: httpx.Response(200, json=_reply()))

        class Backup:
            name = "backup"

            async def get_kline(self, symbol, timeframe, start=None, end=None):
                assert (symbol, timeframe, start, end) == ("600519", "1d", START, END)
                bars = parse_kline_payload(symbol, timeframe, _reply(rows=[
                    ["2023-01-04", "10", "10.5", "11", "9", "30"],
                ]))
                bars[0].source = self.name
                return bars

            async def aclose(self):
                pass

        chain = CompositeProvider([provider, Backup()])
        try:
            bars = await chain.get_kline("600519", "1d", START, END)
            assert bars[0].source == "backup"
            assert bars[0].ts.date().isoformat() == "2023-01-04"
            assert chain._failures == {}
        finally:
            await chain.aclose()
    asyncio.run(scenario())


def test_mixed_adjustment_outside_range_is_not_qualified_range_evidence():
    async def scenario():
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            payload = _reply()
            if calls == 2:
                node = payload["data"]["sh600519"]
                node["day"] = node.pop("qfqday")
            return httpx.Response(200, json=payload)

        provider = await _provider(handler)
        try:
            with pytest.raises(ProviderError) as caught:
                await provider.get_kline("600519", "1d", START, datetime(2025, 1, 6, tzinfo=BJ_TZ))
            assert not isinstance(caught.value, HistoryRangeUnavailable)
            assert calls == 2
        finally:
            await provider.aclose()
    asyncio.run(scenario())
