"""One unfinished transport may serve quote/book subsets; completed values never cache."""
import asyncio
import httpx
import pytest
from app.data_providers.tencent import TencentProvider


def raw(code):
    f = [''] * 62
    for i, v in {1:'fixture',2:code,3:'12.34',4:'12',9:'12.34',10:'250',19:'12.35',20:'180',
                 30:'20261015103000',33:'12.5',34:'12',36:'10000',37:'1200'}.items():
        f[i] = v
    return '~'.join(f)


@pytest.mark.parametrize('cancel_quote', [False, True])
def test_book_reuses_active_quote_superset_but_not_completed_payload(cancel_quote):
    async def run():
        p = TencentProvider()
        await p._client.aclose()
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def handle(req):
            calls.append(str(req.url));started.set();await release.wait()
            return httpx.Response(200, content=('v_sh600127="'+raw('600127')+'";v_sz000001="'+raw('000001')+'";').encode('gbk'))
        p._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        try:
            quote = asyncio.create_task(p.get_quotes(['600127', '000001']))
            await started.wait()
            book = asyncio.create_task(p.get_order_book('600127'))
            await asyncio.sleep(0)
            if cancel_quote:
                quote.cancel()
                with pytest.raises(asyncio.CancelledError):await quote
            release.set()
            result = await book
            if not cancel_quote:assert len(await quote) == 2
            assert result.bids[0].volume == 25000
            assert len(calls) == 1
            await p.get_order_book('600127')
            assert len(calls) == 2  # no completed cache / TTL
        finally:await p.aclose()
    asyncio.run(run())


def test_shared_failure_is_visible_to_both_and_next_read_retries():
    from app.data_providers.eastmoney import ProviderError
    async def run():
        provider = TencentProvider()
        await provider._client.aclose()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def handle(req):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set(); await release.wait()
                return httpx.Response(503)
            return httpx.Response(200, content=('v_sh600127="'+raw('600127')+'";').encode('gbk'))
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        try:
            quote = asyncio.create_task(provider.get_quotes(['600127']))
            await entered.wait()
            book = asyncio.create_task(provider.get_order_book('600127'))
            await asyncio.sleep(0);release.set()
            result = await asyncio.gather(quote, book, return_exceptions=True)
            assert calls == 1 and all(isinstance(r, ProviderError) for r in result)
            assert len(await provider.get_quotes(['600127'])) == 1 and calls == 2
        finally:
            await provider.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("cancel_indices", [False, True])
def test_indices_share_active_raw_snapshot_with_index_detail_but_never_cache_completed(cancel_indices):
    from app.market.indices import INDEX_CATALOG

    async def run():
        provider = TencentProvider()
        await provider._client.aclose()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handle(request):
            calls.append(str(request.url))
            entered.set()
            await release.wait()
            body = "".join(f'v_{market.lower()}{code}="{raw(code)}";' for code, market, _ in INDEX_CATALOG)
            return httpx.Response(200, content=body.encode("gbk"))

        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        try:
            indices = asyncio.create_task(provider.get_indices())
            await entered.wait()
            detail = asyncio.create_task(provider.get_quote("sh000001"))
            await asyncio.sleep(0)
            if cancel_indices:
                indices.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await indices
            release.set()
            result = await detail
            if not cancel_indices:
                rows = await indices
                assert {(row.symbol, row.market) for row in rows} == {(code, market) for code, market, _ in INDEX_CATALOG}
            assert result.symbol == "sh000001" and result.market == "SH"
            assert len(calls) == 1
            await provider.get_quote("sh000001")
            assert len(calls) == 2  # A finished payload must not become a price TTL.
        finally:
            release.set()
            await provider.aclose()

    asyncio.run(run())


def test_shared_index_read_preserves_duplicate_rows_for_the_hub_identity_gate():
    async def run():
        provider = TencentProvider()
        await provider._client.aclose()

        def handle(request):
            body = f'v_sh000001="{raw("000001")}";v_sh000001="{raw("000001")}";v_sz000001="{raw("000001")}";'
            return httpx.Response(200, content=body.encode("gbk"))

        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        try:
            rows = await provider.get_indices()
            assert [(row.symbol, row.market) for row in rows] == [("000001", "SH"), ("000001", "SH"), ("000001", "SZ")]
        finally:
            await provider.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("method,symbol", [
    ("get_quote", "600127"), ("get_quote", "sh600127"),
    ("get_order_book", "600127"), ("get_indices", None),
])
def test_snapshot_header_cannot_relabel_another_security(method, symbol):
    from app.data_providers.eastmoney import ProviderError

    async def run():
        provider = TencentProvider()
        await provider._client.aclose()
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=(
                f'v_sh600127="{raw("600519")}";'
            ).encode("gbk"))))
        try:
            with pytest.raises(ProviderError, match="identity"):
                call = getattr(provider, method)
                await (call(symbol) if symbol is not None else call())
        finally:
            await provider.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("method", ["get_quote", "get_order_book"])
def test_single_security_snapshot_cannot_silently_choose_duplicate(method):
    from app.data_providers.eastmoney import ProviderError

    async def run():
        provider = TencentProvider()
        await provider._client.aclose()
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=(
                f'v_sh600127="{raw("600127")}";v_sh600127="{raw("600127")}";'
            ).encode("gbk"))))
        try:
            with pytest.raises(ProviderError, match="duplicate"):
                await getattr(provider, method)("600127")
        finally:
            await provider.aclose()

    asyncio.run(run())
