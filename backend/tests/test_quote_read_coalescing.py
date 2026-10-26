"""Concurrent real read paths, bounded fake sources; no models or production files."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.bjtime import BJ_TZ
from app.core.ttl_cache import TTLCache
from app.market import stock_flow as sf
from app.market import board_flow as bf
from app.market import fund_flow as ff
from app.api.routes import market_stock as ms


def test_all_unsupported_stock_symbols_never_send_empty_source_query(monkeypatch):
    def forbidden():
        raise AssertionError("unsupported universe has no source consumer")

    monkeypatch.setattr(sf, "_http", forbidden)
    result = asyncio.run(sf.get_stock_flow(["830799", "920001", "830799"]))
    assert result["items"] == {} and result["no_data"] == ["830799", "920001"]
    assert result["as_of"] is None and "覆盖" in result["degraded"][0]


def test_stock_set_identity_cancellation_copy_and_date(monkeypatch):
    monkeypatch.setattr(sf, "_CACHE", TTLCache("stock-set-test", 30, maxsize=8))
    monkeypatch.setattr(sf, "_READS", {}, raising=False)
    now = [datetime(2026, 10, 13, 10, 0, tzinfo=BJ_TZ)]
    monkeypatch.setattr(sf, "beijing_now", lambda: now[0], raising=False)
    calls = []

    async def run():
        release = asyncio.Event()

        class HTTP:
            async def get(self, url, params, headers):
                calls.append(params["secids"])
                await release.wait()
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": {"diff": [
                    {"f12": "600519", "f14": "fixture", "f62": 1e8, "f124": now[0].timestamp()}
                ]}})

        monkeypatch.setattr(sf, "_http", lambda: HTTP())
        batch = [asyncio.create_task(sf.get_stock_flow(s)) for s in (
            ["600519", "000001", "830799"], ["830799", "000001", "600519"],
            ["600519", "000001", "830799", "600519"])]
        for _ in range(10):
            await asyncio.sleep(0)
        batch[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await batch[0]
        release.set()
        results = await asyncio.gather(*batch[1:])
        assert len(calls) == 1
        assert results[0]["no_data"] == ["830799", "000001"]
        assert results[0]["items"]["600519"]["main"] == 1
        source_time = results[0]["as_of"]
        results[0]["items"]["600519"]["main"] = 999
        assert results[1]["items"]["600519"]["main"] == 1
        warm = await sf.get_stock_flow(["000001", "600519", "830799"])
        assert warm["items"]["600519"]["main"] == 1
        assert warm["as_of"] == source_time and len(calls) == 1
        now[0] += timedelta(days=1)
        await sf.get_stock_flow(["000001", "600519", "830799"])
        assert len(calls) == 2
        await sf.get_stock_flow(["600519", "000002"])
        assert len(calls) == 3

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["minute", "members"])
def test_board_cold_reads_coalesce_cancel_and_retry(monkeypatch, kind):
    monkeypatch.setattr(bf, "_MINUTE_CACHE", TTLCache("board-minute-test", 60, maxsize=4))
    monkeypatch.setattr(bf, "_MEMBERS_CACHE", TTLCache("board-member-test", 60, maxsize=8))
    monkeypatch.setattr(bf, "_inflight_source_reads", None, raising=False)
    now = [datetime(2026, 10, 13, 10, 0, tzinfo=BJ_TZ)]
    monkeypatch.setattr(bf, "beijing_now", lambda: now[0])
    monkeypatch.setattr(bf, "_load_dayk_store", lambda: {"BK0001": {"bars": [["2026-10-12", 3, None]]}})
    calls = []
    fail = [False]

    async def run():
        release = asyncio.Event()

        async def minute(secid):
            calls.append(secid)
            await release.wait()
            return None if fail[0] else [("10:00", {"main": 0, "small": None})]

        async def members(*args, **kwargs):
            calls.append(args[0])
            await release.wait()
            return (None, None) if fail[0] else ([{"f12": "600519", "f14": "fixture", "f62": 0}], bf._HOSTS[0])

        monkeypatch.setattr(ff, "_em_fflow_kline", minute)
        monkeypatch.setattr(bf, "_clist_pages", members)
        read = bf.get_board_minute if kind == "minute" else bf.get_board_members
        tasks = [asyncio.create_task(read("BK0001")) for _ in range(8)]
        for _ in range(10):
            await asyncio.sleep(0)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        release.set()
        outputs = await asyncio.gather(*tasks[1:])
        assert len(calls) == 1
        field = "items" if kind == "minute" else "rows"
        number = "main" if kind == "minute" else "main_net_yi"
        outputs[0][field][0][number] = 999
        assert outputs[1][field][0][number] == 0
        assert (await read("BK0001"))[field][0][number] == 0 and len(calls) == 1
        fail[0] = True
        failed = await asyncio.gather(*(read("BK0002") for _ in range(4)))
        assert len(calls) == 2 and all(not o["available"] for o in failed)
        fail[0] = False
        assert (await read("BK0002"))["available"] and len(calls) == 3
        now[0] += timedelta(days=1)
        assert (await read("BK0001"))["available"] and len(calls) == 4
        if kind == "minute":
            bf._MINUTE_CACHE.invalidate()
            fail[0] = True
            partial = await read("BK0001")
            assert partial["items"] == [] and partial["available"] is False
            assert partial["daily_bars"] == [{"date": "2026-10-12", "main_yi": 3, "close_pct": None}]

    asyncio.run(run())


def test_stock_failure_shared_only_for_current_round_and_cap_identity(monkeypatch):
    monkeypatch.setattr(sf, "_CACHE", TTLCache("stock-fail-test", 30, maxsize=8))
    monkeypatch.setattr(sf, "_READS", {}, raising=False)
    calls = []
    fail = [True]
    original_sleep = asyncio.sleep

    async def short_sleep(_):
        await original_sleep(0)

    monkeypatch.setattr(sf.asyncio, "sleep", short_sleep)

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secids"])
            await original_sleep(0)
            if fail[0]:
                raise RuntimeError("fixture failure")
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": {"diff": [
                {"f12": "600519", "f62": 0} ]}})

    monkeypatch.setattr(sf, "_http", lambda: HTTP())

    async def run():
        outputs = await asyncio.gather(*(sf.get_stock_flow(["600519", "830799"]) for _ in range(8)))
        assert len(calls) == 3  # Original three attempts, shared by all eight callers.
        assert all(o["items"] == {} and o["degraded"] for o in outputs)
        fail[0] = False
        recovered = await sf.get_stock_flow(["600519", "830799"])
        assert recovered["items"]["600519"]["main"] == 0 and len(calls) == 4
        symbols = [str(600000 + i) for i in range(sf.MAX_SYMBOLS + 1)]
        await sf.get_stock_flow(symbols)
        await sf.get_stock_flow(list(reversed(symbols)))
        assert len(calls) == 6 and calls[-1] != calls[-2]

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["news", "announcements", "content"])
def test_news_reads_share_cancel_copy_failure_retry_and_identity(monkeypatch, kind):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    hub = SimpleNamespace(provider=SimpleNamespace())
    monkeypatch.setattr(ms, "meta_payload", lambda _: {"source": "fixture", "as_of": "2026-10-13T10:00:00+08:00"})
    calls = []
    fail = [False]

    async def run():
        release = asyncio.Event()

        async def source(*args):
            calls.append(args)
            await release.wait()
            if fail[0]:
                raise RuntimeError("fixture unavailable")
            if kind == "content":
                return {"paragraphs": ["condition preserved"], "blocks": [{"type": "p", "text": "condition preserved"}]}
            return [{"title": "fixture", "conditions": ["conditional"]}]

        if kind == "content":
            monkeypatch.setattr(ms, "fetch_article", source)
        else:
            setattr(hub.provider, "get_" + kind, source)

        async def read(identity="600519", limit=10):
            if kind == "content":
                return await ms.news_content(request, "https://finance.eastmoney.com/a/" + identity + ".html", hub)
            return await getattr(ms, kind)(identity, request, limit, hub)

        tasks = [asyncio.create_task(read()) for _ in range(8)]
        for _ in range(10):
            await asyncio.sleep(0)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        release.set()
        results = await asyncio.gather(*tasks[1:])
        assert len(calls) == 1
        if kind == "content":
            results[0]["data"]["paragraphs"][0] = "mutated"
            assert results[1]["data"]["paragraphs"] == ["condition preserved"]
        else:
            results[0]["data"]["items"][0]["conditions"][0] = "mutated"
            assert results[1]["data"]["items"][0]["conditions"] == ["conditional"]
        assert (await read())["meta"]["cached"] is True
        assert len(calls) == 1
        await read("000001")
        if kind != "content":
            await read(limit=20)
        fail[0] = True
        failed = await asyncio.gather(*(read("000002") for _ in range(4)), return_exceptions=True)
        assert all(isinstance(e, HTTPException) and e.status_code == 502 for e in failed)
        fail[0] = False
        assert "data" in await read("000002")
        assert len(calls) == (4 if kind == "content" else 5)
        if kind == "content":
            before = len(calls)
            with pytest.raises(HTTPException) as exc:
                await ms.news_content(request, "http://127.0.0.1/private", hub)
            assert exc.value.status_code == 400 and len(calls) == before

    asyncio.run(run())
