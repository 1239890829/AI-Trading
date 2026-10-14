"""Real route boundaries and counted offline factories; no model or database."""
import asyncio
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace as NS
import pytest
from app.api.routes import market_quotes as quotes, market_pools as pools
from app.api.routes import market_stock as stock, market_themes as themes, news as digests
from app.data_providers.composite import CompositeProvider
from app.schemas.market import Kline, LimitUpRecord, OrderBook, Trade, Quality
from app.services import picks_pipeline
from app.services.source_reads import read_pool_input
NOW=datetime(2026,10,14,2,tzinfo=timezone.utc)
DAY=NOW.date()
def request(): return NS(app=NS(state=NS(snapshot_service=NS())))
def hub(provider): return NS(provider=provider,name="offline",last_success_refresh=None,is_stale=lambda:False)
async def turns():
    for _ in range(12): await asyncio.sleep(0)
class Source:
    name="offline-source"
    realtime=True
    def __init__(self,release): self.release,self.calls=release,Counter()
    async def read(self,method,result):
        self.calls[method]+=1
        await self.release.wait()
        return result
    async def get_minute_line(self,symbol):
        return await self.read("minute",[{"t":"10:00","price":10.0}])
    async def get_kline(self,symbol,timeframe,start,end):
        return await self.read("kline",[Kline(symbol=symbol,timeframe=timeframe,ts=NOW,close=10,source=self.name)])
    async def get_order_book(self,symbol):
        return await self.read("book",OrderBook(symbol=symbol,source=self.name,data_timestamp=NOW))
    async def get_trades(self,symbol):
        return await self.read("trades",[Trade(symbol=symbol,price=10,ts=NOW,source=self.name)])
    async def get_limit_up_pool(self,day):
        return await self.read("up",[LimitUpRecord(symbol="600001",trade_date=day,source=self.name)])
    async def get_limit_break_pool(self,day): return await self.read("break",[])
    async def get_news(self,symbol,limit):
        return await self.read("news",[{"symbol":symbol,"title":"订单公告","date":"2026-10-14","source":self.name}])
    async def get_announcements(self,symbol,limit):
        return await self.read("ann",[{"symbol":symbol,"title":"订单公告","date":"2026-10-14","source":self.name}])
    async def aclose(self): pass

@pytest.mark.parametrize("kind",["minute","kline","book","trades"])
def test_same_chart_read_enters_one_source_factory(monkeypatch,kind):
    from app.market import minute_backfill
    monkeypatch.setattr(quotes,"meta_payload",lambda h:{})
    monkeypatch.setattr(minute_backfill,"load_vr_baseline",lambda symbol:None)
    async def status(*a,**k): return None
    monkeypatch.setattr(quotes,"_trading_status_payload",status)
    async def run():
        release=asyncio.Event();source=Source(release);h=hub(CompositeProvider([source]))
        async def read():
            if kind=="minute": return await quotes.minute_line("600001",h)
            if kind=="kline": return await quotes._kline_payload(h,"600001","1d",250,None,None)
            if kind=="book": return await quotes.order_book("600001",h)
            return await quotes.trades("600001",50,h)
        tasks=[asyncio.create_task(read()) for _ in range(3)]
        try:
            await turns();release.set()
            results=await asyncio.gather(*tasks)
            assert len(results)==3
            assert source.calls[kind]==1,dict(source.calls)
        finally:
            release.set();await asyncio.gather(*tasks,return_exceptions=True)
            await h.provider.aclose()
    asyncio.run(run())

def test_theme_cold_route_factory_is_shared(monkeypatch):
    from app.services import theme_service
    monkeypatch.setattr(themes,"load_snapshot_map",lambda *a:{})
    async def meta(*a): return {}
    monkeypatch.setattr(themes,"dated_meta",meta)
    calls=[]
    async def run():
        release=asyncio.Event()
        async def build(*a,**k):
            calls.append(True);await release.wait()
            return {"themes":[],"input_version":"source-original"}
        monkeypatch.setattr(theme_service,"build_theme_board",build)
        req=request();tasks=[asyncio.create_task(themes._theme_board_cached(req,hub(NS()),DAY)) for _ in range(3)]
        try:
            await turns();release.set();results=await asyncio.gather(*tasks)
            assert len(calls)==1,calls
            results[0]["data"]["themes"].append("caller-change")
            assert results[1]["data"]["themes"]==[]
        finally:
            release.set();await asyncio.gather(*tasks,return_exceptions=True)
    asyncio.run(run())

@pytest.mark.parametrize("kind",["up","break"])
def test_pool_page_and_direct_reader_share_same_input(monkeypatch,kind):
    async def verify(*a,**k): pass
    async def calendar(*a,**k): return [DAY]
    monkeypatch.setattr(picks_pipeline.tc,"trading_days",calendar)
    async def meta(*a,**k): return {}
    monkeypatch.setattr(pools,"verify_pool_date",verify);monkeypatch.setattr(pools,"dated_meta",meta)
    async def run():
        release=asyncio.Event();source=Source(release);provider=CompositeProvider([source]);req=request()
        tasks=[asyncio.create_task(pools.limit_up(req,DAY.isoformat(),hub(provider),include_breaks=kind=="break"))]
        if kind=="up":
            direct=picks_pipeline._fetch_limit_up_pool(hub(provider))
        else:
            direct=read_pool_input(provider,"get_limit_break_pool",DAY)
        tasks.append(asyncio.create_task(direct))
        try:
            await turns();release.set();await asyncio.gather(*tasks)
            assert source.calls[kind]==1,dict(source.calls)
        finally:
            release.set();await asyncio.gather(*tasks,return_exceptions=True);await provider.aclose()
    asyncio.run(run())

@pytest.mark.parametrize("kind",["news","ann"])
def test_raw_news_and_digest_share_only_input_factories(monkeypatch,kind):
    monkeypatch.setattr(stock,"meta_payload",lambda h:{})
    class RulesOnly:
        def __init__(self,**kwargs): pass
        def summarize(self,news,ann):
            from app.news.rules import RulesSummarizer
            return RulesSummarizer().summarize(news,ann),{"requested":"rules","actual":"rules","degraded":False}
    monkeypatch.setattr(digests,"SummaryRouter",RulesOnly)
    async def run():
        release=asyncio.Event();source=Source(release);h=hub(CompositeProvider([source]));req=request()
        raw=stock.news if kind=="news" else stock.announcements
        tasks=[asyncio.create_task(raw("600001",req,10,h)),asyncio.create_task(digests.news_digest("600001",req,10,h))]
        try:
            await turns();release.set();await asyncio.gather(*tasks)
            assert source.calls[kind]==1,dict(source.calls)
        finally:
            release.set();await asyncio.gather(*tasks,return_exceptions=True);await h.provider.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["minute", "kline", "book", "trades"])
def test_chart_failure_is_shared_then_retries_without_result_cache(monkeypatch, kind):
    from app.market import minute_backfill
    from fastapi import HTTPException
    monkeypatch.setattr(quotes, "meta_payload", lambda h: {})
    monkeypatch.setattr(minute_backfill, "load_vr_baseline", lambda symbol: None)
    monkeypatch.setattr(minute_backfill, "tdx_minute_line_fallback", lambda symbol: (_ for _ in ()).throw(RuntimeError("offline TDX")))
    async def status(*a, **k): return None
    monkeypatch.setattr(quotes, "_trading_status_payload", status)
    async def run():
        release = asyncio.Event()
        failing = [True]
        class Failing(Source):
            async def read(self, method, result):
                self.calls[method] += 1
                await release.wait()
                if failing[0]:
                    raise RuntimeError("offline source failure")
                return result
        source = Failing(release)
        h = hub(CompositeProvider([source]))
        async def read():
            if kind == "minute": return await quotes.minute_line("600001", h)
            if kind == "kline": return await quotes._kline_payload(h, "600001", "1d", 250, None, None)
            if kind == "book": return await quotes.order_book("600001", h)
            return await quotes.trades("600001", 50, h)
        tasks = [asyncio.create_task(read()) for _ in range(3)]
        try:
            await turns()
            release.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert all(isinstance(r, HTTPException) and r.status_code == 502 for r in results)
            assert source.calls[kind] == 1
            failing[0] = False
            assert (await read())["data"]
            assert source.calls[kind] == 2
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


def test_kline_complete_parameters_and_original_budget_do_not_merge(monkeypatch):
    monkeypatch.setattr(quotes, "meta_payload", lambda h: {})
    async def status(*a, **k): return None
    monkeypatch.setattr(quotes, "_trading_status_payload", status)
    async def run():
        release = asyncio.Event()
        source = Source(release)
        provider = CompositeProvider([source])
        budget = [12]
        monkeypatch.setattr(provider, "budget_for", lambda method: budget[0])
        h = hub(provider)
        args = [("600001", "1d", 50, None, None), ("600001", "1d", 100, None, None),
                ("600001", "1m", 50, None, None), ("600002", "1d", 50, None, None),
                ("600001", "1d", 50, NOW, None), ("600001", "1d", 50, None, NOW)]
        tasks = [asyncio.create_task(quotes._kline_payload(h, *arg)) for arg in args]
        try:
            await turns()
            budget[0] = 6
            tasks.append(asyncio.create_task(quotes._kline_payload(h, *args[0])))
            await turns()
            release.set()
            await asyncio.gather(*tasks)
            assert source.calls["kline"] == 7
            await quotes._kline_payload(h, *args[0])
            assert source.calls["kline"] == 8  # No completed cache.
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


def test_opted_pool_reader_never_merges_a_direct_probe_or_other_date():
    async def run():
        release = asyncio.Event()
        source = Source(release)
        provider = CompositeProvider([source])
        tasks = [asyncio.create_task(read_pool_input(provider, "get_limit_up_pool", DAY)),
                 asyncio.create_task(read_pool_input(provider, "get_limit_up_pool", DAY)),
                 asyncio.create_task(provider.get_limit_up_pool(DAY)),
                 asyncio.create_task(read_pool_input(provider, "get_limit_up_pool", DAY.replace(day=13)))]
        try:
            await turns()
            release.set()
            outputs = await asyncio.gather(*tasks)
            assert source.calls["up"] == 3  # Same-day display shared, direct probe and other day independent.
            assert outputs[0][0].trade_date == DAY
            assert outputs[-1][0].trade_date == DAY.replace(day=13)
            outputs[0][0].reason = "caller mutation"
            assert outputs[1][0].reason is None
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["minute", "trades"])
def test_whole_chart_fallback_is_shared_without_laundering_tdx_source(monkeypatch, kind):
    from app.market import minute_backfill, tdx_tick
    monkeypatch.setattr(quotes, "meta_payload", lambda h: {})
    monkeypatch.setattr(minute_backfill, "load_vr_baseline", lambda symbol: None)
    calls = []
    def minute(symbol):
        calls.append(symbol)
        return [{"t": "10:00", "price": 10, "source": "tdx", "quality": "stale", "as_of": "original"}]
    def trades(symbol, **kwargs):
        calls.append(symbol)
        return [Trade(symbol=symbol, ts=NOW, price=10, source="tdx", quality=Quality.stale)]
    monkeypatch.setattr(minute_backfill, "tdx_minute_line_fallback", minute)
    monkeypatch.setattr(tdx_tick, "fetch_tdx_trades", trades)
    monkeypatch.setattr(tdx_tick.settings, "trades_tdx_fallback_enabled", True)
    async def run():
        release = asyncio.Event()
        class Failed(Source):
            async def read(self, method, result):
                self.calls[method] += 1
                await release.wait()
                raise RuntimeError("offline primary failed")
        source = Failed(release)
        h = hub(CompositeProvider([source]))
        async def read():
            return await (quotes.minute_line("600001", h) if kind == "minute" else quotes.trades("600001", 50, h))
        tasks = [asyncio.create_task(read()) for _ in range(3)]
        try:
            await turns()
            release.set()
            results = await asyncio.gather(*tasks)
            assert calls == ["600001"] and source.calls[kind] == 1
            if kind == "minute":
                assert results[0]["data"]["points"][0]["as_of"] == "original"
                assert results[0]["data"]["points"][0]["source"] == "tdx"
                assert results[0]["data"]["points"][0]["quality"] == "stale"
            else:
                assert results[0]["meta"]["trades_source"] == "tdx"
                assert results[0]["data"][0]["source"] == "tdx"
                assert results[0]["data"][0]["quality"] == "stale"
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


def test_raw_news_digest_failure_and_full_rules_result_recover(monkeypatch):
    from fastapi import HTTPException
    from app.news.rules import RulesSummarizer
    monkeypatch.setattr(stock, "meta_payload", lambda h: {})
    summaries = []
    class RulesOnly:
        def __init__(self, **kwargs): pass
        def summarize(self, news, announcements):
            summaries.append((news, announcements))
            return RulesSummarizer().summarize(news, announcements), {
                "requested": "rules", "actual": "rules", "degraded": False}
    monkeypatch.setattr(digests, "SummaryRouter", RulesOnly)
    async def run():
        release = asyncio.Event()
        failing = [True]
        class Failed(Source):
            async def read(self, method, result):
                self.calls[method] += 1
                await release.wait()
                if failing[0]:
                    raise RuntimeError("offline source failure")
                return result
        source = Failed(release)
        h, req = hub(CompositeProvider([source])), request()
        async def cohort():
            return await asyncio.gather(stock.news("600001", req, 10, h),
                stock.announcements("600001", req, 10, h),
                digests.news_digest("600001", req, 10, h), return_exceptions=True)
        pending = asyncio.create_task(cohort())
        try:
            await turns()
            release.set()
            failed = await pending
            assert all(isinstance(r, HTTPException) and r.status_code == 502 for r in failed)
            assert source.calls == {"news": 1, "ann": 1} and summaries == []
            failing[0] = False
            release.clear()
            pending = asyncio.create_task(cohort())
            await turns()
            release.set()
            raw, ann, digest = await pending
            assert source.calls == {"news": 2, "ann": 2} and len(summaries) == 1
            expected = RulesSummarizer().summarize(raw["data"]["items"], ann["data"]["items"])
            for kind in ("news", "announcements"):
                assert digest["data"][kind][0]["title"] == expected[kind][0]["title"]
                assert digest["data"][kind][0]["digest"] == expected[kind][0]["digest"]
                assert digest["data"][kind][0]["importance"] == expected[kind][0]["importance"]
            raw["data"]["items"][0]["title"] = "caller mutation"
            assert summaries[0][0][0]["title"] == "订单公告"
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
    asyncio.run(run())


def test_news_symbols_limits_kinds_and_cross_day_are_independent(monkeypatch):
    from app.services import source_reads
    from app.services.source_reads import read_news_input
    now = [NOW]
    monkeypatch.setattr(source_reads, "beijing_now", lambda: now[0])
    async def run():
        release = asyncio.Event()
        source = Source(release)
        provider = CompositeProvider([source])
        args = [("get_news", "600001", 10), ("get_news", "600001", 10),
                ("get_news", "600001", 20), ("get_news", "600002", 10),
                ("get_announcements", "600001", 10)]
        tasks = [asyncio.create_task(read_news_input(provider, *arg)) for arg in args]
        try:
            await turns()
            now[0] = NOW.replace(day=15)
            tasks.append(asyncio.create_task(read_news_input(provider, *args[0])))
            await turns()
            release.set()
            await asyncio.gather(*tasks)
            assert source.calls == {"news": 4, "ann": 1}
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


def test_theme_failures_cancel_and_original_sixty_second_cache(monkeypatch):
    from app.services import theme_service
    from app.core import ttl_cache
    from fastapi import HTTPException
    clock = [0.0]
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock[0])
    monkeypatch.setattr(themes, "load_snapshot_map", lambda *a: {})
    async def meta(*a): return {"source_time": "retained"}
    monkeypatch.setattr(themes, "dated_meta", meta)
    async def run():
        release = asyncio.Event()
        failing = [True]
        calls = []
        async def build(*a, **k):
            calls.append(True)
            await release.wait()
            if failing[0]:
                raise RuntimeError("offline source failure")
            return {"themes": [], "source_version": len(calls)}
        monkeypatch.setattr(theme_service, "build_theme_board", build)
        req, h = request(), hub(NS())
        tasks = [asyncio.create_task(themes._theme_board_cached(req, h, DAY)) for _ in range(3)]
        try:
            await turns()
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            release.set()
            results = await asyncio.gather(*tasks[1:], return_exceptions=True)
            assert len(calls) == 1 and all(isinstance(r, HTTPException) for r in results)
            failing[0] = False
            result = await themes._theme_board_cached(req, h, DAY)
            assert result["data"]["source_version"] == 2
            result["data"]["themes"].append("caller mutation")
            clock[0] = 59
            cached = await themes._theme_board_cached(req, h, DAY)
            assert cached["data"]["themes"] == [] and len(calls) == 2
            assert cached["meta"]["source_time"] == "retained"
            clock[0] = 60
            assert (await themes._theme_board_cached(req, h, DAY))["data"]["source_version"] == 3
            assert len(calls) == 3
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


def test_pool_route_and_picks_keep_failure_cancel_and_retry_semantics(monkeypatch):
    from fastapi import HTTPException
    async def calendar(*a, **k): return [DAY]
    async def verify(*a, **k): pass
    async def meta(*a, **k): return {}
    monkeypatch.setattr(picks_pipeline.tc, "trading_days", calendar)
    monkeypatch.setattr(pools, "verify_pool_date", verify)
    monkeypatch.setattr(pools, "dated_meta", meta)
    async def run():
        release = asyncio.Event()
        failing = [True]
        class Failed(Source):
            async def read(self, method, result):
                self.calls[method] += 1
                await release.wait()
                if failing[0]:
                    raise RuntimeError("offline pool failure")
                return result
        source = Failed(release)
        h, req = hub(CompositeProvider([source])), request()
        tasks = [asyncio.create_task(pools.limit_up(req, DAY.isoformat(), h)),
                 asyncio.create_task(picks_pipeline._fetch_limit_up_pool(h)),
                 asyncio.create_task(read_pool_input(h.provider, "get_limit_up_pool", DAY))]
        try:
            await turns()
            tasks[-1].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[-1]
            release.set()
            page, picked = await asyncio.gather(*tasks[:2], return_exceptions=True)
            assert isinstance(page, HTTPException) and page.status_code == 502
            assert picked == (None, []) and source.calls["up"] == 1
            failing[0] = False
            recovered = await pools.limit_up(req, DAY.isoformat(), h)
            assert recovered["data"]["pool"][0]["trade_date"] == DAY.isoformat()
            assert source.calls["up"] == 2
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


def test_chart_different_hub_context_never_shares_envelope(monkeypatch):
    monkeypatch.setattr(quotes, "meta_payload", lambda h: {"context": h.context})
    async def run():
        release = asyncio.Event()
        source = Source(release)
        provider = CompositeProvider([source])
        first, second = hub(provider), hub(provider)
        first.context, second.context = "first", "second"
        tasks = [asyncio.create_task(quotes.order_book("600001", first)),
                 asyncio.create_task(quotes.order_book("600001", second))]
        try:
            await turns()
            release.set()
            results = await asyncio.gather(*tasks)
            assert source.calls["book"] == 2
            assert [r["meta"]["context"] for r in results] == ["first", "second"]
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())
