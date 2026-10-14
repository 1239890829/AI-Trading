"""Offline contract checks: a valid empty break pool must not hide a source failure."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date

import httpx
import pytest

from app.data_providers.eastmoney import EastmoneyProvider, ProviderError
from app.data_providers.ths import ThsFuyaoProvider, date_ms


_DAY = date(2026, 8, 28)
_EM_EMPTY = {"rc": 0, "data": {"tc": 0, "pool": []}}
_THS_EMPTY = {
    "code": 0,
    "data": {"pagination": {"total": 0, "pages": 0, "page": 1, "size": 200}, "item": []},
}


def _read(provider_name: str, payload, status: int = 200):
    """Exercise the actual HTTP envelope without a supplier request or credentials."""
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            if provider_name == "eastmoney":
                assert request.url.path.endswith("/getTopicZBPool")
                assert request.url.params["date"] == "20260828"
            else:
                assert request.url.path.endswith("/limit-break-pool")
                assert request.url.params["date_ms"] == str(date_ms(_DAY))
                assert int(request.url.params["page"]) >= 1
                assert request.url.params["size"] == "200"
            return httpx.Response(status, json=payload(request) if callable(payload) else payload)

        if provider_name == "eastmoney":
            provider = EastmoneyProvider()
        else:
            provider = ThsFuyaoProvider(api_key="offline-placeholder")
        await provider._client.aclose()
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await provider.get_limit_break_pool(_DAY)
        finally:
            await provider.aclose()

    return asyncio.run(run())


@pytest.mark.parametrize("provider,payload", [("eastmoney", _EM_EMPTY), ("ths", _THS_EMPTY)])
def test_explicit_complete_empty_pool_is_success(provider, payload):
    assert _read(provider, deepcopy(payload)) == []


@pytest.mark.parametrize("payload", [
    None, [], {"data": {"tc": 0, "pool": []}}, {"rc": -1, "data": None},
    {"rc": False, "data": {"tc": 0, "pool": []}}, {"rc": 0},
    {"rc": 0, "data": None}, {"rc": 0, "data": []},
    {"rc": 0, "data": {"tc": 0}}, {"rc": 0, "data": {"tc": 0, "pool": None}},
    {"rc": 0, "data": {"tc": 0, "pool": {}}},
    {"rc": 0, "data": {"pool": []}}, {"rc": 0, "data": {"tc": False, "pool": []}},
    {"rc": 0, "data": {"tc": "0", "pool": []}},
    {"rc": 0, "data": {"tc": -1, "pool": []}},
    {"rc": 0, "data": {"tc": 1, "pool": []}},
    {"rc": 0, "data": {"tc": 0, "pool": [{"c": "600103"}]}},
    {"rc": 0, "data": {"tc": 2, "pool": [{"c": ""}, {"c": "600103"}]}},
    {"rc": 0, "data": {"tc": 1, "pool": [None]}},
    {"rc": 0, "data": {"tc": 1, "pool": [{"c": "６００１０３"}]}},
    {"rc": 0, "data": {"tc": 2, "pool": [{"c": "600103"}, {"c": "600103"}]}},
])
def test_eastmoney_failure_or_partial_pool_never_becomes_valid_empty(payload):
    with pytest.raises(ProviderError):
        _read("eastmoney", payload)


@pytest.mark.parametrize("payload", [
    None, [], {}, {"code": -1, "data": None}, {"code": False, "data": _THS_EMPTY["data"]},
    {"code": 0}, {"code": 0, "data": None}, {"code": 0, "data": []},
    {"code": 0, "data": {"pagination": {"total": 0}}},
    {"code": 0, "data": {"pagination": {"total": 0}, "item": None}},
    {"code": 0, "data": {"pagination": {"total": 0}, "item": {}}},
    {"code": 0, "data": {"item": []}},
    {"code": 0, "data": {"pagination": {}, "item": []}},
    {"code": 0, "data": {"pagination": {"total": False}, "item": []}},
    {"code": 0, "data": {"pagination": {"total": "0"}, "item": []}},
    {"code": 0, "data": {"pagination": {"total": -1}, "item": []}},
    {"code": 0, "data": {"pagination": {"total": 1}, "item": []}},
    {"code": 0, "data": {"pagination": {"total": 0, "page": 2}, "item": []}},
    {"code": 0, "data": {"pagination": {"total": 0, "pages": 2}, "item": []}},
    {"code": 0, "data": {"pagination": {"total": 1}, "item": [None]}},
    {"code": 0, "data": {"pagination": {"total": 1}, "item": [{"thscode": ""}]}},
    {"code": 0, "data": {"pagination": {"total": 1}, "item": [{"thscode": "600103.SZ"}]}},
    {"code": 0, "data": {"pagination": {"total": 1}, "item": [{"thscode": "６００１０３.SH"}]}},
    {"code": 0, "data": {"pagination": {"total": 1}, "item": [{"thscode": "600103.SH", "ticker": "000001"}]}},
    {"code": 0, "data": {"pagination": {"total": 2}, "item": [{"thscode": "600103.SH"}, {"thscode": "600103.SH"}]}},
])
def test_ths_failure_or_partial_pool_never_becomes_valid_empty(payload):
    with pytest.raises(ProviderError):
        _read("ths", payload)


@pytest.mark.parametrize("provider,payload", [("eastmoney", _EM_EMPTY), ("ths", _THS_EMPTY)])
def test_http_failure_cannot_be_a_successful_empty_pool(provider, payload):
    with pytest.raises(ProviderError):
        _read(provider, payload, status=503)


def test_success_keeps_all_valid_symbols_optional_values_and_source_units():
    em = _read("eastmoney", {"rc": 0, "data": {"tc": 2, "pool": [
        {"c": "600103", "p": 3850}, {"c": "000001"},
    ]}})
    ths = _read("ths", {"code": 0, "data": {"pagination": {"total": 3}, "item": [
        {"thscode": "600103.SH", "last_price": 3.85, "open_times": 2},
        {"thscode": "000001.SZ", "ticker": "000001"}, {"thscode": "920001.BJ"},
    ]}})
    assert [(row.symbol, row.price) for row in em] == [("600103", 3.85), ("000001", None)]
    assert [(row.symbol, row.price) for row in ths] == [("600103", 3.85), ("000001", None), ("920001", None)]
    assert ths[0].break_count == 2
    assert all(row.trade_date == _DAY for row in em + ths)


def test_update_clock_is_not_misused_as_historical_pool_trade_date():
    # qdate is latest update day; THS timestamp is data-ready time, not pool trade day.
    # Their disagreement with the requested day cannot prove a mixed-day response.
    em = deepcopy(_EM_EMPTY)
    em["data"]["qdate"] = 20260831
    ths = deepcopy(_THS_EMPTY)
    ths["data"]["timestamp"] = date_ms(date(2026, 8, 31))
    assert _read("eastmoney", em) == []
    assert _read("ths", ths) == []


@pytest.mark.parametrize("fault", [None, "total_changed", "repeated_page", "duplicate", "empty_tail", "error_tail"])
def test_ths_pagination_is_complete_or_fails_without_returning_first_page(fault):
    symbols = [f"{number:06d}" for number in range(1, 202)]
    calls = []

    def response(request):
        page = int(request.url.params["page"])
        calls.append(page)
        rows = symbols[:200] if page == 1 else symbols[200:]
        pagination = {"total": 201, "size": 200, "page": page, "pages": 2}
        if page == 2:
            if fault == "total_changed":
                pagination["total"] = 202
            elif fault == "repeated_page":
                pagination["page"] = 1
            elif fault == "duplicate":
                rows = [symbols[0]]
            elif fault == "empty_tail":
                rows = []
            elif fault == "error_tail":
                return {"code": -1, "data": None}
        return {"code": 0, "data": {"pagination": pagination, "item": [
            {"thscode": f"{symbol}.SZ"} for symbol in rows
        ]}}

    if fault:
        with pytest.raises(ProviderError):
            _read("ths", response)
    else:
        assert [row.symbol for row in _read("ths", response)] == symbols
    assert calls == [1, 2]
