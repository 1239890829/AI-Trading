"""Real stdlib/uvloop scheduling contracts, with offline providers and calendar."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta

import pytest

from app.core import freshness
from app.data_quality import validator
from app.market.indices import INDEX_MARKETS
from app.schemas.market import Quality, Quote
from app.services import quote_hub as qh

NOW = datetime.fromisoformat("2026-10-14T11:00:00+08:00")


class Provider:
    name = "offline-timer"
    realtime = True

    def __init__(self):
        self.calls = Counter()
        self.active = Counter()
        self.gate = None

    async def read(self, domain):
        self.calls[domain] += 1
        self.active[domain] += 1
        try:
            if self.gate is not None:
                await self.gate.wait()
            symbols = INDEX_MARKETS if domain == "indices" else {"600105": "SH"}
            return [Quote(symbol=symbol, market=market, source=self.name,
                          price=10 + self.calls[domain] / 10, prev_close=10,
                          data_timestamp=NOW + timedelta(milliseconds=self.calls[domain]), received_at=NOW)
                    for symbol, market in symbols.items()]
        finally:
            self.active[domain] -= 1

    async def get_quotes(self, symbols):
        return await self.read("quotes")

    async def get_indices(self):
        return await self.read("indices")


@pytest.fixture(params=["stdlib", "uvloop"])
def run_in_loop(request):
    factory = asyncio.new_event_loop
    if request.param == "uvloop":
        factory = pytest.importorskip("uvloop").new_event_loop

    def run(scenario):
        # Keep the production implementation; do not change the process policy
        # or replace the event loop's clock/timer handles with wider fakes.
        with asyncio.Runner(loop_factory=factory) as runner:
            return runner.run(scenario())
    return run


@pytest.fixture
def hub(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            current = NOW + timedelta(seconds=5)
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)
    async def calendar(*args, **kwargs):
        return []
    monkeypatch.setattr(qh.tc, "trading_days", calendar)
    monkeypatch.setattr(qh.tc, "market_open_state", lambda *a, **k: qh.tc.MARKET_OPEN)
    monkeypatch.setattr(qh.tc, "in_trading_window", lambda: True)
    monkeypatch.setattr(qh, "utcnow", lambda: NOW + timedelta(seconds=5))
    monkeypatch.setattr(qh, "beijing_now", lambda: NOW + timedelta(seconds=5))
    monkeypatch.setattr(validator, "utcnow", lambda: NOW + timedelta(seconds=5))
    monkeypatch.setattr(freshness, "datetime", Clock)
    return qh.QuoteHub(Provider(), poll_interval=0.5, get_watchlist=lambda: ["600105"])


async def settle():
    for _ in range(8):
        await asyncio.sleep(0)


def assert_timer_cleared(hub):
    assert hub._publish_timer is None
    assert getattr(hub, "_publish_deadline", None) is None


def test_same_tick_immediate_double_schedule_publishes_once(hub, run_in_loop):
    async def scenario():
        await hub._refresh_domain("quotes")
        queue = hub.subscribe({"600105"})
        hub._schedule_publish()
        hub._schedule_publish()
        await hub._flush_pending()
        await settle()
        assert queue.qsize() == 1
        assert queue.get_nowait()["data"][0]["price"] == 10.1
        assert_timer_cleared(hub)
    run_in_loop(scenario)


def test_later_due_coalesces_and_earlier_due_rearms(hub, run_in_loop, monkeypatch):
    async def scenario():
        await hub._refresh_domain("quotes")
        queue = hub.subscribe({"600105"})
        due = [asyncio.get_running_loop().time() + 30]
        monkeypatch.setattr(hub, "_publish_due_at", lambda: due[0])
        hub._schedule_publish()
        original = hub._publish_timer
        due[0] += 10
        hub._schedule_publish()
        assert hub._publish_timer is original and not original.cancelled()
        due[0] -= 20
        hub._schedule_publish()
        assert original.cancelled() and hub._publish_timer is not original
        await hub._flush_pending()
        assert queue.qsize() == 1
        assert_timer_cleared(hub)
    run_in_loop(scenario)


def test_callback_clears_timer_before_next_schedule(hub, run_in_loop):
    async def scenario():
        await hub._refresh_domain("quotes")
        queue = hub.subscribe({"600105"})
        hub._schedule_publish()
        first = await asyncio.wait_for(queue.get(), 1)
        await settle()
        assert_timer_cleared(hub)
        await hub._refresh_domain("quotes")
        hub._schedule_publish()
        second = await asyncio.wait_for(queue.get(), 1.5)
        await settle()
        assert first["seq"] < second["seq"]
        assert second["data"][0]["price"] == 10.2
        assert queue.empty()
        assert_timer_cleared(hub)
    run_in_loop(scenario)


def test_flush_cancels_pending_timer_without_duplicate_frame(hub, run_in_loop, monkeypatch):
    async def scenario():
        await hub._refresh_domain("quotes")
        queue = hub.subscribe({"600105"})
        monkeypatch.setattr(hub, "_publish_due_at", lambda: asyncio.get_running_loop().time() + 0.02)
        hub._schedule_publish()
        old = hub._publish_timer
        await hub._flush_pending()
        assert old.cancelled()
        await asyncio.sleep(0.05)
        assert queue.qsize() == 1
        assert_timer_cleared(hub)
    run_in_loop(scenario)


def test_stop_cancels_pending_timer_without_final_frame(hub, run_in_loop, monkeypatch):
    async def scenario():
        await hub._refresh_domain("quotes")
        queue = hub.subscribe({"600105"})
        hub.provider.gate = asyncio.Event()
        monkeypatch.setattr(hub, "_publish_due_at", lambda: asyncio.get_running_loop().time() + 0.03)
        poller = asyncio.create_task(hub.run())
        await settle()
        hub._schedule_publish()
        old = hub._publish_timer
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
        await asyncio.sleep(0.06)
        assert old.cancelled() and queue.empty()
        assert_timer_cleared(hub)
        assert not hub._pending_domains and not hub._domain_tasks
        assert hub._publish_task is None and not any(hub.provider.active.values())
    run_in_loop(scenario)


def test_actual_poller_and_startup_fill_survive_two_ticks(hub, run_in_loop):
    async def scenario():
        queue = hub.subscribe({"600105", "sh000001"})
        poller = asyncio.create_task(hub.run())
        manual = asyncio.create_task(hub.refresh())
        try:
            await asyncio.wait_for(asyncio.shield(manual), 1)
            first = await asyncio.wait_for(queue.get(), 1)
            second = await asyncio.wait_for(queue.get(), 1.5)
            assert not poller.done()
            assert all(hub.provider.calls[domain] >= 2 for domain in ("indices", "quotes"))
            assert first["seq"] < second["seq"]
            first_rows = {row["symbol"]: row for row in first["data"]}
            second_rows = {row["symbol"]: row for row in second["data"]}
            assert second_rows["600105"]["data_timestamp"] > first_rows["600105"]["data_timestamp"]
            assert hub.quotes["600105"].quality == Quality.high
            assert not hub.last_quote_rejections and not hub.last_index_rejections
        finally:
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
            await asyncio.gather(manual, return_exceptions=True)
        await settle()
        assert_timer_cleared(hub)
        assert not hub._running and not hub._domain_tasks and not hub._pending_domains
        assert hub._publish_task is None and not any(hub.provider.active.values())
        assert queue.empty()
    run_in_loop(scenario)
