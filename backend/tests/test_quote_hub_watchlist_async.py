"""A blocked watchlist repository must not block independent market delivery."""
from __future__ import annotations

import asyncio
import json
import threading
from collections import Counter

import pytest

from app.market.indices import INDEX_MARKETS
from app.schemas.market import Quote, utcnow
from app.services import quote_hub as qh


class Source:
    name = "offline-watchlist"
    realtime = True

    def __init__(self):
        self.calls = Counter()
        self.requested = []
        self.index_returned = threading.Event()

    async def get_indices(self):
        self.calls["indices"] += 1
        self.index_returned.set()
        return [quote(symbol, market) for symbol, market in INDEX_MARKETS.items()]

    async def get_quotes(self, symbols):
        self.calls["quotes"] += 1
        self.requested.append(symbols)
        return [quote(symbol) for symbol in symbols]


def quote(symbol, market="SH"):
    return Quote(symbol=symbol, market=market, price=10, prev_close=10,
                 data_timestamp=utcnow(), source="offline-watchlist")


@pytest.fixture(autouse=True)
def open_session(monkeypatch):
    async def calendar(*args, **kwargs):
        return []
    monkeypatch.setattr(qh.tc, "trading_days", calendar)
    monkeypatch.setattr(qh.tc, "in_trading_window", lambda: True)
    monkeypatch.setattr(qh.tc, "market_open_state", lambda *a, **k: qh.tc.MARKET_OPEN)


async def settle():
    for _ in range(8):
        await asyncio.sleep(0)


def test_blocked_watchlist_repository_does_not_block_index_or_heartbeat():
    async def check():
        release, heartbeat = threading.Event(), threading.Event()
        source = Source()
        observed = []
        def lookup():
            if not release.wait(2):
                raise RuntimeError("offline deadman expired")
            return ["600105"]
        def unblock():
            observed.append((source.index_returned.is_set(), heartbeat.is_set()))
            release.set()
        # An independent thread guarantees that the old synchronous lookup also
        # exits. Its observation precedes releasing the simulated DB wait.
        timer = threading.Timer(0.25, unblock)
        timer.daemon = True
        hub = qh.QuoteHub(source, 0.5, get_watchlist=lookup)
        timer.start()
        tasks = [asyncio.create_task(hub._read_domain("quotes")),
                 asyncio.create_task(hub._read_domain("indices"))]
        async def beat():
            await asyncio.sleep(0)
            heartbeat.set()
        tasks.append(asyncio.create_task(beat()))
        try:
            await asyncio.gather(*tasks)
            assert observed == [(True, True)]
            assert source.requested == [["600105"]]
        finally:
            release.set()
            timer.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            timer.join(1)
    asyncio.run(check())


@pytest.mark.parametrize("failed", [False, True])
def test_watchlist_singleflight_merges_current_subscriptions_and_falls_back_on_loop(failed):
    async def check():
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        release, entered = threading.Event(), asyncio.Event()
        calls = []
        def lookup():
            calls.append(threading.get_ident())
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(2):
                raise RuntimeError("offline deadman expired")
            if failed:
                raise RuntimeError("offline locked repository")
            return ["600105"]
        class LoopOnlyQuotes(dict):
            def keys(self):
                assert threading.get_ident() == loop_thread
                return super().keys()
        class LoopOnlySubscribers(list):
            def __iter__(self):
                assert threading.get_ident() == loop_thread
                return super().__iter__()
        source = Source()
        hub = qh.QuoteHub(source, 0.5, get_watchlist=lookup)
        hub.quotes = LoopOnlyQuotes({"600127": quote("600127")})
        hub._subscribers = LoopOnlySubscribers()
        queue = hub.subscribe({"300750"})
        timer = threading.Timer(0.25, release.set)
        timer.daemon = True
        timer.start()
        first = asyncio.create_task(hub._read_domain("quotes"))
        second = asyncio.create_task(hub._read_domain("quotes"))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            hub.update_symbols(queue, {"002594", "sh000001"})
            first.cancel()  # A canceled waiter must not replace the domain owner.
            await asyncio.gather(first, return_exceptions=True)
            release.set()
            await second
            assert len(calls) == 1
            assert source.requested == [["002594", "600127" if failed else "600105"]]
        finally:
            release.set()
            timer.cancel()
            await asyncio.gather(first, second, return_exceptions=True)
            timer.join(1)
    asyncio.run(check())


def test_hub_shutdown_drains_real_lookup_and_rejects_an_overlapping_owner():
    async def check():
        loop = asyncio.get_running_loop()
        release, entered = threading.Event(), asyncio.Event()
        finished, calls = threading.Event(), []
        def lookup():
            calls.append(True)
            loop.call_soon_threadsafe(entered.set)
            try:
                if not release.wait(2):
                    raise RuntimeError("offline deadman expired")
                return ["600105"]
            finally:
                finished.set()
        source = Source()
        hub = qh.QuoteHub(source, 0.5, get_watchlist=lookup)
        timer = threading.Timer(0.5, release.set)
        timer.daemon = True
        timer.start()
        poller = asyncio.create_task(hub.run())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            poller.cancel()
            await settle()
            poller.cancel()  # Repeated cancellation cannot orphan the worker.
            await settle()
            assert not finished.is_set() and not poller.done()
            assert hub._running
            assert not hub._domain_tasks["quotes"].done()
            with pytest.raises(RuntimeError, match="already running"):
                await hub.run()
            assert calls == [True] and source.calls["quotes"] == 0
            release.set()
            await asyncio.gather(poller, return_exceptions=True)
            assert finished.is_set() and not hub._running and not hub._domain_tasks
            assert source.calls["quotes"] == 0 and not hub.quotes
        finally:
            release.set()
            timer.cancel()
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
            timer.join(1)
    asyncio.run(check())


def test_shutdown_does_not_rearm_publisher_while_lookup_drains(monkeypatch):
    async def check():
        loop = asyncio.get_running_loop()
        lookup_entered, calendar_entered = asyncio.Event(), asyncio.Event()
        second_indices = asyncio.Event()
        quote_returned, broadcasted = asyncio.Event(), asyncio.Event()
        lookup_release = threading.Event()
        calendar_release = asyncio.Event()
        calendar_calls, broadcasts = [], []

        def lookup():
            loop.call_soon_threadsafe(lookup_entered.set)
            if not lookup_release.wait(3):
                raise RuntimeError("offline deadman expired")
            return ["600105"]

        async def calendar(*args, **kwargs):
            calendar_calls.append(True)
            calendar_entered.set()
            await calendar_release.wait()
            return []

        class RepeatingSource(Source):
            async def get_indices(self):
                rows = await super().get_indices()
                if calendar_entered.is_set():
                    second_indices.set()
                return rows

            async def get_quotes(self, symbols):
                rows = await super().get_quotes(symbols)
                quote_returned.set()
                return rows

        monkeypatch.setattr(qh.tc, "trading_days", calendar)
        source = RepeatingSource()
        hub = qh.QuoteHub(source, 0.02, get_watchlist=lookup)
        def broadcast(*args, **kwargs):
            broadcasts.append(hub._running)
            broadcasted.set()
        monkeypatch.setattr(hub, "_broadcast", broadcast)
        watchdog = threading.Timer(2, lookup_release.set)
        watchdog.daemon = True
        watchdog.start()
        poller = asyncio.create_task(hub.run())
        try:
            await asyncio.wait_for(lookup_entered.wait(), 1)
            await asyncio.wait_for(calendar_entered.wait(), 1)
            await asyncio.wait_for(second_indices.wait(), 1)
            await settle()
            assert hub._pending_domains == {"indices"}
            poller.cancel()
            await settle()
            assert hub._running and not poller.done()
            with pytest.raises(RuntimeError, match="already running"):
                await hub.run()
            # Give an immediate call_at a real event-loop turn to fire. A canceled
            # publisher must not create another one while the DB thread drains.
            await asyncio.sleep(0.03)
            calls_during_drain = len(calendar_calls)
            live_during_drain = [t for t in asyncio.all_tasks()
                                 if t.get_name() == "quote-hub-publish" and not t.done()]
            lookup_release.set()
            await asyncio.wait_for(asyncio.gather(poller, return_exceptions=True), 1)
            live_after_stop = [t for t in asyncio.all_tasks()
                               if t.get_name() == "quote-hub-publish" and not t.done()]
            calendar_release.set()
            await settle()
            facts = {"calendar_calls_during_drain": calls_during_drain,
                     "publishers_during_drain": len(live_during_drain),
                     "publishers_after_stop": len(live_after_stop),
                     "broadcasts_after_stop": sum(not running for running in broadcasts),
                     "timer_after_stop": hub._publish_timer is not None,
                     "owner_released": not hub._running and not hub._domain_tasks}
            print(json.dumps(facts, sort_keys=True))
            assert facts == {"calendar_calls_during_drain": 1,
                             "publishers_during_drain": 0,
                             "publishers_after_stop": 0,
                             "broadcasts_after_stop": 0,
                             "timer_after_stop": False,
                             "owner_released": True}
            # The stop guard must reset when this same Hub starts a later owner.
            again = asyncio.create_task(hub.run())
            try:
                await asyncio.wait_for(quote_returned.wait(), 1)
                await asyncio.wait_for(broadcasted.wait(), 1)
                assert hub._running and not again.done()
                assert source.calls["quotes"] >= 1
                assert broadcasts
            finally:
                again.cancel()
                await asyncio.gather(again, return_exceptions=True)
            previous_calls, previous_frames = source.calls["quotes"], len(broadcasts)
            await asyncio.wait_for(hub.refresh(), 1)
            assert source.calls["quotes"] == previous_calls + 1
            assert len(broadcasts) == previous_frames + 1
            assert hub._publish_timer is None and hub._publish_deadline is None
        finally:
            lookup_release.set()
            calendar_release.set()
            watchdog.cancel()
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
            leftovers = [t for t in asyncio.all_tasks()
                         if t.get_name() == "quote-hub-publish" and not t.done()]
            for task in leftovers:
                task.cancel()
            await asyncio.gather(*leftovers, return_exceptions=True)
            watchdog.join(1)
    asyncio.run(check())
