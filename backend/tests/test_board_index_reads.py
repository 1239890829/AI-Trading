"""Two-kind board-index ownership: failures, policy identity and real shutdown."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.bjtime import BJ_TZ
from app.core.inflight_reads import close_inflight_reads
from app.core.ttl_cache import TTLCache
from app.market import board_flow as bf
from app.services import theme_service as ts


@pytest.fixture()
def index_scope(monkeypatch, tmp_path):
    clock = {"now": datetime(2026, 10, 14, 10, tzinfo=BJ_TZ), "session": True}
    monkeypatch.setattr(bf, "beijing_now", lambda: clock["now"])
    monkeypatch.setattr(bf, "_in_session", lambda: clock["session"])
    monkeypatch.setattr(bf, "_LIST_CACHE", TTLCache("index-test-list", 30, maxsize=4))
    monkeypatch.setattr(bf, "_LIST_EOD_CACHE", TTLCache("index-test-eod", 300, maxsize=4))
    monkeypatch.setattr(bf, "_inflight_source_reads", None, raising=False)
    monkeypatch.setattr(bf, "_DAILY_STORE", tmp_path / "daily.json")
    monkeypatch.setattr(bf, "_DAYK_STORE", tmp_path / "daykline.json")
    monkeypatch.setattr(bf, "_mem_memo", {"daily": (None, {}), "dayk": (None, {})})
    return clock


def _response(kind, *, shared_name=False):
    row = {"f12": "BK1001" if kind == "industry" else "BK2001",
           "f14": "same-name" if shared_name else kind,
           "f3": 1.0, "f6": 10e8, "f62": 1e8, "f184": 10.0}
    return SimpleNamespace(raise_for_status=lambda: None,
                           json=lambda: {"data": {"total": 1, "diff": [row]}})


async def _second_reader(started):
    started.set()
    return await ts._board_index()


def test_staggered_failure_index_is_shared_and_next_read_recovers(monkeypatch, index_scope):
    from app.core import ttl_cache

    clock = [0.0]
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock[0])
    async def scenario():
        concept_entered, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = Counter()
        recovered = False

        class HTTP:
            async def get(self, url, params, headers):
                kind = "industry" if "t:2" in params["fs"] else "concept"
                host = "delay" if "push2delay" in url else "primary"
                calls[(recovered, kind, host)] += 1
                if not recovered:
                    if kind == "concept" and host == "primary":
                        concept_entered.set()
                        await release.wait()
                    await asyncio.sleep(0)
                    raise RuntimeError("fixture disconnected before response")
                return _response(kind)

        monkeypatch.setattr(bf, "_HTTP", HTTP())
        first = asyncio.create_task(ts._board_index())
        second = None
        try:
            await asyncio.wait_for(concept_entered.wait(), 1)
            second = asyncio.create_task(_second_reader(second_started))
            await asyncio.wait_for(second_started.wait(), 1)
            release.set()
            results = await asyncio.gather(first, second)
            assert results == [{}, {}]
            assert calls[(False, "industry", "primary")] == 1
            assert calls[(False, "industry", "delay")] == 1
            assert calls[(False, "concept", "primary")] == 1
            assert calls[(False, "concept", "delay")] == 1
            recovered = True
            # Source recovery does not cause every consumer to bypass the
            # bounded failure cooldown; one shared read probes after expiry.
            assert await ts._board_index() == {}
            assert calls[(True, "industry", "primary")] == 0
            clock[0] = 30.0
            result = await ts._board_index()
            assert set(result) == {"industry", "concept"}
            assert calls[(True, "industry", "primary")] == 1
            assert calls[(True, "concept", "primary")] == 1
            assert bf._LIST_CACHE.ttl == 30 and bf._LIST_EOD_CACHE.ttl == 300
        finally:
            release.set()
            await asyncio.gather(*(t for t in (first, second) if t), return_exceptions=True)

    asyncio.run(scenario())


def test_shared_index_returns_deep_copies_and_industry_wins_name_collision(monkeypatch, index_scope):
    async def scenario():
        concept_entered, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = Counter()

        async def pages(fs, fields):
            kind = "industry" if "t:2" in fs else "concept"
            calls[kind] += 1
            if kind == "concept":
                concept_entered.set()
                await release.wait()
            return _response(kind, shared_name=True).json()["data"]["diff"], bf._HOSTS[0]

        monkeypatch.setattr(bf, "_clist_pages", pages)
        first = asyncio.create_task(ts._board_index())
        second = None
        try:
            await asyncio.wait_for(concept_entered.wait(), 1)
            second = asyncio.create_task(_second_reader(second_started))
            await asyncio.wait_for(second_started.wait(), 1)
            release.set()
            left, right = await asyncio.gather(first, second)
            assert calls == {"industry": 1, "concept": 1}
            assert right["same-name"]["kind"] == "industry"
            assert right["same-name"]["main_net_inflow"] == 1e8
            left["same-name"]["main_net_yi"] = 999
            left["same-name"]["nested"] = {"unexpected": True}
            assert right["same-name"]["main_net_yi"] == 1.0
            assert "nested" not in right["same-name"]
            later = await ts._board_index()
            assert later["same-name"]["main_net_yi"] == 1.0
            assert calls == {"industry": 1, "concept": 1}  # Original success cache only.
        finally:
            release.set()
            await asyncio.gather(*(t for t in (first, second) if t), return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("policy_change", ["date", "session", "hosts", "fields"])
def test_overlapping_index_policy_changes_do_not_share_old_owner(monkeypatch, index_scope, policy_change):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = Counter()

        async def pages(fs, fields):
            kind = "industry" if "t:2" in fs else "concept"
            calls[kind] += 1
            if kind == "concept" and calls[kind] == 1:
                entered.set()
                await release.wait()
            return _response(kind).json()["data"]["diff"], bf._HOSTS[0]

        monkeypatch.setattr(bf, "_clist_pages", pages)
        first = asyncio.create_task(ts._board_index())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if policy_change == "date":
                index_scope["now"] += timedelta(days=1)
            elif policy_change == "session":
                index_scope["session"] = False
            elif policy_change == "hosts":
                monkeypatch.setattr(bf, "_HOSTS", ("https://fixture-primary.invalid", "https://fixture-delay.invalid"))
            else:
                monkeypatch.setattr(bf, "_BOARD_LIST_FIELDS", bf._BOARD_LIST_FIELDS + ",fixture-field")
            current = await asyncio.wait_for(ts._board_index(), 1)
            assert set(current) == {"industry", "concept"}
            assert calls == {"industry": 2, "concept": 2}
        finally:
            release.set()
            await asyncio.gather(first, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_shutdown", [False, True])
def test_all_cancelled_waiters_keep_owner_until_application_close_drains(monkeypatch, index_scope, cancel_shutdown):
    from app.bootstrap.services import AppServices

    async def scenario():
        entered, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        provider_closed = asyncio.Event()
        catalog_closed = asyncio.Event()
        calls = Counter()

        async def pages(fs, fields):
            kind = "industry" if "t:2" in fs else "concept"
            calls[kind] += 1
            if kind == "concept":
                entered.set()
                await release.wait()
            return _response(kind).json()["data"]["diff"], bf._HOSTS[0]

        async def close_provider():
            provider_closed.set()

        async def close_catalog():
            catalog_closed.set()

        monkeypatch.setattr(bf, "_clist_pages", pages)
        services = SimpleNamespace(provider=SimpleNamespace(aclose=close_provider),
                                   theme_catalog=SimpleNamespace(aclose=close_catalog), asset_service=None)
        first = asyncio.create_task(ts._board_index())
        second = closing = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            second = asyncio.create_task(_second_reader(second_started))
            await asyncio.wait_for(second_started.wait(), 1)
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)
            read_owner = getattr(bf, "_inflight_source_reads", None)
            assert read_owner is not None, "Canceled waiters must leave an owned complete-index read"
            closing = asyncio.create_task(AppServices.aclose(services))
            async def close_started():
                while not read_owner.closing:
                    await asyncio.sleep(0)
            await asyncio.wait_for(close_started(), 1)
            assert not closing.done() and not provider_closed.is_set()
            if cancel_shutdown:
                closing.cancel()
                await asyncio.sleep(0)
                assert not closing.done() and not provider_closed.is_set()
            with pytest.raises(RuntimeError, match="closing"):
                await asyncio.wait_for(ts._board_index(), 1)
            with pytest.raises(RuntimeError, match="drain"):
                bf.start_board_read_lifecycle()
            release.set()
            if cancel_shutdown:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(closing, 1)
            else:
                await asyncio.wait_for(closing, 1)
            assert provider_closed.is_set() and catalog_closed.is_set()
            assert calls == {"industry": 1, "concept": 1}
            assert not any(not t.done() for t in bf._inflight_source_reads.tasks.values())
            with pytest.raises(RuntimeError, match="closing"):
                await asyncio.wait_for(ts._board_index(), 1)
            bf.start_board_read_lifecycle()
            current = await ts._board_index()
            assert set(current) == {"industry", "concept"}
        finally:
            release.set()
            await asyncio.gather(*(t for t in (first, second, closing) if t), return_exceptions=True)

    asyncio.run(scenario())


def test_cancelling_one_waiter_does_not_abandon_the_other(monkeypatch, index_scope):
    async def scenario():
        entered, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = Counter()

        async def pages(fs, fields):
            kind = "industry" if "t:2" in fs else "concept"
            calls[kind] += 1
            if kind == "concept":
                entered.set()
                await release.wait()
            return _response(kind).json()["data"]["diff"], bf._HOSTS[0]

        monkeypatch.setattr(bf, "_clist_pages", pages)
        first = asyncio.create_task(ts._board_index())
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            second = asyncio.create_task(_second_reader(second_started))
            await asyncio.wait_for(second_started.wait(), 1)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not second.done()
            release.set()
            assert set(await second) == {"industry", "concept"}
            assert calls == {"industry": 1, "concept": 1}
        finally:
            release.set()
            await asyncio.gather(*(t for t in (first, second) if t), return_exceptions=True)

    asyncio.run(scenario())


def test_starting_lifecycle_does_not_replace_open_active_owner(monkeypatch, index_scope):
    async def scenario():
        entered, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = Counter()

        async def pages(fs, fields):
            kind = "industry" if "t:2" in fs else "concept"
            calls[kind] += 1
            if kind == "concept":
                entered.set()
                await release.wait()
            return _response(kind).json()["data"]["diff"], bf._HOSTS[0]

        monkeypatch.setattr(bf, "_clist_pages", pages)
        first = asyncio.create_task(ts._board_index())
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            original_owner = bf._inflight_source_reads
            bf.start_board_read_lifecycle()
            assert bf._inflight_source_reads is original_owner
            second = asyncio.create_task(_second_reader(second_started))
            await asyncio.wait_for(second_started.wait(), 1)
            release.set()
            assert await first == await second
            assert calls == {"industry": 1, "concept": 1}
            await close_inflight_reads(bf)
        finally:
            release.set()
            await asyncio.gather(*(t for t in (first, second) if t), return_exceptions=True)

    asyncio.run(scenario())
