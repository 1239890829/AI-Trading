"""东财指数响应身份：合成 HTTP 夹具，禁止为漏项制造报价。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.data_providers.eastmoney import EastmoneyProvider, ProviderError
from app.schemas.market import Quality, utcnow
from app.services import quote_hub as qh


SECIDS = ["1.000001", "0.399001", "0.399006", "1.000688", "1.000300", "1.000852"]


def rows():
    return [
        {"f12": sid[2:], "f13": int(sid[0]), "f14": f"指数-{sid[2:]}",
         "f2": 3000 + i, "f3": 0.5, "f4": 15, "f6": 100000,
         "f124": 1789617600}
        for i, sid in enumerate(SECIDS)
    ]


async def provider_for(handler):
    provider = EastmoneyProvider()
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


def fetch(diff):
    def respond(request):
        assert request.url.params["secids"] == ",".join(SECIDS)
        assert "f13" in request.url.params["fields"].split(",")
        return httpx.Response(200, json={"rc": 0, "data": {"diff": diff}})

    async def run():
        provider = await provider_for(respond)
        try:
            return await provider.get_indices()
        finally:
            await provider.aclose()

    return asyncio.run(run())


@pytest.mark.parametrize("reverse", [False, True])
def test_complete_reply_preserves_order_fields_and_source_time(reverse):
    reply = rows()
    quotes = fetch(list(reversed(reply)) if reverse else reply)
    assert [q.symbol for q in quotes] == ["000001", "399001", "399006", "000688", "000300", "000852"]
    assert [q.market for q in quotes] == ["SH", "SZ", "SZ", "SH", "SH", "SH"]
    assert [q.price for q in quotes] == [3000, 3001, 3002, 3003, 3004, 3005]
    assert all(q.source == "eastmoney" and q.change_pct == 0.5 and q.amount == 100000 for q in quotes)
    assert all(q.data_timestamp == datetime.fromtimestamp(1789617600, timezone.utc) for q in quotes)


@pytest.mark.parametrize("missing", range(6))
def test_missing_requested_row_stays_absent(missing):
    reply = rows()
    reply.pop(missing)
    quotes = fetch(reply)
    assert [q.symbol for q in quotes] == [sid[2:] for i, sid in enumerate(SECIDS) if i != missing]
    assert all(q.price is not None for q in quotes)


@pytest.mark.parametrize("diff", [[], None, [{"f12": "000001", "f13": 0, "f2": 11.7}]])
def test_no_requested_index_never_becomes_six_empty_quotes(diff):
    assert fetch(diff) == []


@pytest.mark.parametrize("reverse", [False, True])
def test_same_code_stock_does_not_replace_shanghai_index(reverse):
    reply = rows() + [{"f12": "000001", "f13": 0, "f14": "平安银行", "f2": 11.7}]
    quotes = fetch(list(reversed(reply)) if reverse else reply)
    assert len(quotes) == 6
    assert (quotes[0].symbol, quotes[0].market, quotes[0].price) == ("000001", "SH", 3000)


def test_duplicate_requested_identity_is_rejected():
    reply = rows()
    with pytest.raises(ProviderError, match="duplicate index"):
        fetch(reply + [{**reply[0], "f2": 9999}])


@pytest.mark.parametrize("diff", [[None], ["bad-row"], {"row": {"f12": "000001"}}])
def test_malformed_rows_raise_provider_error(diff):
    with pytest.raises(ProviderError, match="index.*(rows|row)"):
        fetch(diff)


def test_real_provider_and_hub_preserve_missing_cached_value_then_recover(monkeypatch):
    async def no_calendar(*args, **kwargs):
        return []

    monkeypatch.setattr(qh.tc, "trading_days", no_calendar)
    monkeypatch.setattr(qh.tc, "in_trading_window", lambda: True)

    async def run():
        complete = [{**row, "f124": int(utcnow().timestamp())} for row in rows()]
        replies = iter([complete, complete[1:], [{**complete[0], "f2": 3001}, *complete[1:]]])
        provider = await provider_for(lambda _: httpx.Response(
            200, json={"rc": 0, "data": {"diff": next(replies)}},
        ))
        try:
            hub = qh.QuoteHub(provider, poll_interval=1)
            await hub.refresh()
            before = hub.indices["000001"].model_dump()
            assert before["quality"] == Quality.high
            await hub.refresh()
            missed = hub.indices["000001"]
            assert missed.price == 3000
            assert missed.data_timestamp == before["data_timestamp"]
            assert missed.received_at == before["received_at"]
            assert missed.quality_reasons == ["index_batch_missing"]
            assert missed.quality == Quality.stale
            await hub.refresh()
            assert hub.indices["000001"].price == 3001
            assert hub.indices["000001"].quality == Quality.high
            assert hub.last_missing_indices == []
        finally:
            await provider.aclose()

    asyncio.run(run())
