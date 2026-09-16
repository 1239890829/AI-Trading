"""行情身份必须包含市场；独立 HTTP 样本不借生产解析器生成期望值。"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.data_providers.sina import SinaProvider
from app.data_providers.tencent import TencentProvider, to_tencent_symbol
from app.data_providers.ths import ThsFuyaoProvider


def _run(provider, handler, method, *args):
    async def run():
        await provider._client.aclose()
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await getattr(provider, method)(*args)
        finally:
            await provider.aclose()

    return asyncio.run(run())


def _tencent_row(code, name, price):
    fields = [""] * 50
    fields[1:6] = [name, code[2:], str(price), str(price), str(price)]
    fields[30] = "20260916150000"
    return f'v_{code}="{"~".join(fields)}";'


def _sina_row(code, name, price):
    fields = ["0"] * 32
    fields[:6] = [name, str(price), str(price), str(price), str(price), str(price)]
    fields[10:12] = ["100", str(price)]
    fields[8] = "123"
    fields[30:32] = ["2026-09-16", "15:00:00"]
    return f'var hq_str_{code}="{",".join(fields)}";'


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("provider_cls,row", [(TencentProvider, _tencent_row), (SinaProvider, _sina_row)])
def test_same_six_digits_do_not_alias_across_markets(provider_cls, row, reverse):
    items = [("sz000001", "平安银行", 11.7), ("sh000001", "上证指数", 3891.6)]
    payload = "\n".join(row(*item) for item in (list(reversed(items)) if reverse else items))

    def respond(request):
        assert "sz000001,sh000001" in str(request.url)
        return httpx.Response(200, content=payload.encode("gbk"))

    quotes = _run(provider_cls(), respond, "get_quotes", ["000001", "sh000001"])
    assert [(q.symbol, q.market, q.name, q.price) for q in quotes] == [
        ("000001", "SZ", "平安银行", 11.7), ("sh000001", "SH", "上证指数", 3891.6),
    ]


def test_sina_indices_request_explicit_market_and_keep_public_symbols():
    # 固定期望集，不从生产 INDEX_SECIDS/INDEX_NAMES 派生。
    expected = ["sh000001", "sz399001", "sz399006", "sh000688", "sh000300", "sh000852"]

    def respond(request):
        assert str(request.url).endswith("list=" + ",".join(expected))
        return httpx.Response(200, content="\n".join(
            _sina_row(code, f"指数{i}", 3000 + i) for i, code in enumerate(expected)
        ).encode("gbk"))

    quotes = _run(SinaProvider(), respond, "get_indices")
    assert [q.symbol for q in quotes] == [code[2:] for code in expected]
    assert [q.market for q in quotes] == ["SH", "SZ", "SZ", "SH", "SH", "SH"]
    assert [q.volume for q in quotes] == [12300, 123, 123, 12300, 12300, 12300]


@pytest.mark.parametrize("symbol,source_code,expected", [
    ("sh000001", "sh000001", 12300), ("sz399001", "sz399001", 123),
    ("600519", "sh600519", 123), ("000001", "sz000001", 123),
])
def test_sina_index_volume_is_not_the_stock_volume_unit(symbol, source_code, expected):
    quotes = _run(SinaProvider(), lambda _: httpx.Response(
        200, content=_sina_row(source_code, "测试标的", 10).encode("gbk")
    ), "get_quotes", [symbol])
    assert quotes[0].volume == expected


def test_sina_prefixed_order_book_matches_market_key():
    def respond(request):
        assert str(request.url).endswith("list=sh000001")
        return httpx.Response(200, content=_sina_row("sh000001", "上证指数", 3891.6).encode("gbk"))

    book = _run(SinaProvider(), respond, "get_order_book", "sh000001")
    assert book is not None and book.symbol == "sh000001"
    assert book.bids[0].price == 3891.6


@pytest.mark.parametrize("symbol,expected", [
    ("920819", "bj920819"), ("430047", "bj430047"), ("833171", "bj833171"),
    ("900901", "sh900901"), ("600519", "sh600519"), ("000001", "sz000001"),
    ("sh000001", "sh000001"), ("bj920819", "bj920819"),
])
def test_tencent_market_mapping_does_not_treat_bj_920_as_sh(symbol, expected):
    assert to_tencent_symbol(symbol) == expected


def test_sina_preserves_bj_market():
    def respond(request):
        assert str(request.url).endswith("list=bj920819")
        return httpx.Response(200, content=_sina_row("bj920819", "颖泰生物", 10).encode("gbk"))

    quotes = _run(SinaProvider(), respond, "get_quotes", ["920819"])
    assert (quotes[0].symbol, quotes[0].market) == ("920819", "BJ")


def test_ths_indices_use_index_endpoint_and_thscode_not_vendor_ticker():
    def respond(request):
        assert request.url.path == "/api/a-share-index/prices/snapshot"
        assert request.url.params["thscodes"] == "000001.SH,399001.SZ,399006.SZ,000688.SH,000300.SH,000852.SH"
        return httpx.Response(200, json={"code": 0, "data": {"item": [
            {"thscode": "000001.SH", "ticker": "1A0001", "last_price": 3891.6},
        ]}})

    quotes = _run(ThsFuyaoProvider(api_key="test-only"), respond, "get_indices")
    assert [(q.symbol, q.market, q.name, q.price) for q in quotes] == [("000001", "SH", "上证指数", 3891.6)]


def test_ths_quote_preserves_bj_market():
    async def run():
        provider = ThsFuyaoProvider(api_key="test-only")
        try:
            quote = provider._parse_quote({"thscode": "920819.BJ", "last_price": 10})
            assert quote is not None and quote.market == "BJ"
        finally:
            await provider.aclose()

    asyncio.run(run())
