"""Real Hub ownership and independent-domain behavior with offline providers."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta

import pytest

from app.data_providers.composite import BudgetExhausted, CompositeProvider
from app.data_providers.eastmoney import ProviderError
from app.market.indices import INDEX_MARKETS
from app.schemas.market import Quality, Quote, utcnow
from app.services import quote_hub as qh


def quote(symbol, price=10.0, market="SH"):
    return Quote(symbol=symbol, market=market, price=price, prev_close=price,
                 change_pct=0, volume=100, amount=1000,
                 data_timestamp=utcnow(), source="offline-domain")


class Provider:
    name = "offline-domain"
    realtime = True

    def __init__(self):
        self.calls = Counter()
        self.active = Counter()
        self.maximum = Counter()
        self.gates = {}
        self.fail = set()
        self.price = 10.0
        self.three_stocks = asyncio.Event()
        self.three_indices = asyncio.Event()

    async def read(self, domain):
        self.calls[domain] += 1
        self.active[domain] += 1
        self.maximum[domain] = max(self.maximum[domain], self.active[domain])
        try:
            if domain in self.gates:
                await self.gates[domain].wait()
            if domain in self.fail:
                raise ProviderError(domain + " offline")
            if domain == "quotes" and self.calls[domain] >= 3:
                self.three_stocks.set()
            if domain == "indices" and self.calls[domain] >= 3:
                self.three_indices.set()
        finally:
            self.active[domain] -= 1

    async def get_indices(self):
        await self.read("indices")
        return [quote(symbol, 100.0, market) for symbol, market in INDEX_MARKETS.items()]

    async def get_quotes(self, symbols):
        await self.read("quotes")
        return [quote(symbol, self.price) for symbol in symbols]


@pytest.fixture
def hub(monkeypatch):
    async def no_calendar(*args, **kwargs):
        return []
    monkeypatch.setattr(qh.tc, "trading_days", no_calendar)
    monkeypatch.setattr(qh.tc, "in_trading_window", lambda: True)
    # 双域正常轮询/源钟行为以盘中为前提；空日历不是 open 的替代证据。
    monkeypatch.setattr(qh.tc, "market_open_state", lambda *a, **k: qh.tc.MARKET_OPEN)
    return qh.QuoteHub(Provider(), poll_interval=0.5,
                       get_watchlist=lambda: ["600519"], stale_after=10)


async def settle():
    for _ in range(8):
        await asyncio.sleep(0)


def test_startup_and_manual_fill_share_each_domain_and_one_frame(hub):
    async def scenario():
        gate = asyncio.Event()
        hub.provider.gates = {"indices": gate, "quotes": gate}
        queue = hub.subscribe({"600519", "sh000001"})
        poller = asyncio.create_task(hub.run())
        manual = asyncio.create_task(hub.refresh())
        try:
            await settle()
            assert hub.provider.calls == {"indices": 1, "quotes": 1}
            gate.set()
            await manual
            assert queue.qsize() == 1
            assert hub.last_success_refresh is not None
        finally:
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
        assert not any(hub.provider.active.values())
    asyncio.run(scenario())


def test_cancel_one_refresh_waiter_does_not_cancel_shared_read(hub):
    async def scenario():
        gate = asyncio.Event()
        hub.provider.gates = {"indices": gate, "quotes": gate}
        first = asyncio.create_task(hub.refresh())
        second = asyncio.create_task(hub.refresh())
        await settle()
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        gate.set()
        await second
        assert hub.provider.calls == {"indices": 1, "quotes": 1}
        assert hub.quotes["600519"].price == 10
        await hub.refresh()
        assert hub.provider.calls == {"indices": 2, "quotes": 2}
    asyncio.run(scenario())


@pytest.mark.parametrize("failed", ["indices", "quotes"])
def test_failed_domain_cannot_discard_other_domains_source_clock(hub, failed):
    async def scenario():
        await hub.refresh()
        complete = hub.last_success_refresh
        old = (hub.quotes["600519"] if failed == "indices" else hub.indices["000001"])
        old_clock = old.data_timestamp
        hub.provider.fail.add(failed)
        hub.provider.price = 11
        await hub.refresh()
        current = (hub.quotes["600519"] if failed == "indices" else hub.indices["000001"])
        assert current.data_timestamp > old_clock
        assert current.quality == Quality.high
        assert hub.last_success_refresh == complete
        domains = hub.refresh_domains()
        assert domains[failed]["last_error_type"] == "ProviderError"
        assert domains[failed]["consecutive_failures"] == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("slow", ["indices", "quotes"])
def test_slow_domain_does_not_stop_other_cadence_and_shutdown_drains(hub, slow):
    async def scenario():
        hub.provider.gates[slow] = asyncio.Event()
        queue = hub.subscribe({"600519"} if slow == "indices" else {"sh000001"})
        poller = asyncio.create_task(hub.run())
        try:
            third = hub.provider.three_stocks if slow == "indices" else hub.provider.three_indices
            await asyncio.wait_for(third.wait(), 1.6)
            assert hub.provider.calls[slow] == 1
            assert hub.provider.maximum == {"indices": 1, "quotes": 1}
            assert not queue.empty()
            assert all(queue.get_nowait()["type"] == "quotes" for _ in range(queue.qsize()))
        finally:
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
        assert not any(hub.provider.active.values())
        count = dict(hub.provider.calls)
        await settle()
        assert dict(hub.provider.calls) == count
    asyncio.run(scenario())


def test_normal_domains_batch_one_full_subscription_frame_per_tick(hub):
    async def scenario():
        queue = hub.subscribe({"600519", "sh000001"})
        poller = asyncio.create_task(hub.run())
        try:
            first = await asyncio.wait_for(queue.get(), 0.8)
            assert {r["symbol"] for r in first["data"]} == {"600519", "sh000001"}
            assert queue.empty()
            second = await asyncio.wait_for(queue.get(), 0.8)
            assert second["seq"] == first["seq"] + 1
            assert {r["symbol"] for r in second["data"]} == {"600519", "sh000001"}
            assert queue.empty()
        finally:
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("jitter", [True, False])
def test_normal_domain_jitter_still_batches_current_source_observations(hub, jitter):
    class Jitter(Provider):
        def __init__(self):
            super().__init__()
            self.observations = {}
        async def get_indices(self):
            await self.read("indices")
            n = self.calls["indices"]
            if jitter:
                await asyncio.sleep(0.02)
            rows = [quote(s, 100 + n, m) for s, m in INDEX_MARKETS.items()]
            self.observations["sh000001", n] = rows[0].model_dump(mode="json")["data_timestamp"]
            return rows
        async def get_quotes(self, symbols):
            await self.read("quotes")
            n = self.calls["quotes"]
            if jitter:
                await asyncio.sleep(0.04 if n == 1 else 0.08)
            rows = [quote(s, 100 + n) for s in symbols]
            self.observations["600519", n] = rows[0].model_dump(mode="json")["data_timestamp"]
            return rows

    async def scenario():
        hub.provider = Jitter()
        queue = hub.subscribe({"600519", "sh000001"})
        poller = asyncio.create_task(hub.run())
        try:
            await hub.refresh()
            queue.get_nowait()
            for n in (2, 3):
                frame = await asyncio.wait_for(queue.get(), 1.1)
                rows = {r["symbol"]: r for r in frame["data"]}
                assert rows["600519"]["price"] == rows["sh000001"]["price"] == 100 + n
                for symbol in rows:
                    assert rows[symbol]["data_timestamp"] == hub.provider.observations[symbol, n]
                assert queue.empty()
        finally:
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
    asyncio.run(scenario())


def test_stale_indices_do_not_mark_stock_only_subscription_stale(hub):
    async def scenario():
        await hub.refresh()
        stocks = hub.subscribe({"600519"})
        mixed = hub.subscribe({"600519", "sh000001"})
        hub.last_success_refresh = utcnow() - timedelta(seconds=11)
        for row in hub.indices.values():
            row.data_timestamp -= timedelta(seconds=11)
        hub.provider.fail.add("indices")
        hub.provider.price = 11
        await hub.refresh()
        stock_frame = stocks.get_nowait()
        mixed_frame = mixed.get_nowait()
        assert stock_frame["type"] == "quotes"
        assert stock_frame["data"][0]["quality"] == "high"
        assert mixed_frame["type"] == "stale"
        rows = {r["symbol"]: r for r in mixed_frame["data"]}
        assert rows["600519"]["price"] == 11
        assert rows["600519"]["quality"] == "high"
        assert rows["sh000001"]["quality"] == "stale"
        assert not stocks.qsize() and not mixed.qsize()
    asyncio.run(scenario())


def test_startup_partial_success_is_readable_but_not_global_ready(hub):
    async def scenario():
        hub.provider.fail.add("indices")
        await hub.refresh()
        assert hub.quotes["600519"].quality == Quality.high
        assert hub.indices == {}
        assert hub.last_success_refresh is None
        assert hub.freshness().state == "degraded"
        assert hub.refresh_domains()["indices"]["freshness"]["state"] == "unavailable"
    asyncio.run(scenario())


def test_health_and_market_metadata_expose_failed_domain_without_recollecting(hub):
    from types import SimpleNamespace
    from app.api.routes.health import health
    from app.api.routes.market_envelope import meta_payload

    async def scenario():
        await hub.refresh()
        hub.provider.fail.add("indices")
        await hub.refresh()
        calls = dict(hub.provider.calls)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
        status = await health(request, hub)
        meta = meta_payload(hub)
        assert status["status"] == "degraded"
        assert status["is_stale"] is False  # 完整轮钟仍未过期，失败与源年龄是不同事实。
        assert status["refresh_domains"] == meta["refresh_domains"]
        assert meta["refresh_domains"]["indices"]["consecutive_failures"] == 1
        assert meta["refresh_domains"]["quotes"]["freshness"]["state"] == "ready"
        assert dict(hub.provider.calls) == calls
    asyncio.run(scenario())


def test_first_failed_empty_index_subscription_gets_its_unavailable_frame(hub):
    async def scenario():
        indices = hub.subscribe({"sh000001"})
        stocks = hub.subscribe({"600519"})
        hub.provider.fail.add("indices")
        await hub.refresh()
        frame = indices.get_nowait()
        assert frame["type"] == "stale" and frame["data"] == []
        assert frame["meta"]["refresh_domains"]["indices"]["freshness"]["state"] == "unavailable"
        assert stocks.get_nowait()["type"] == "quotes"
        assert indices.empty() and stocks.empty()
    asyncio.run(scenario())


def test_old_unrequested_cache_does_not_degrade_current_stock_domain(hub):
    async def scenario():
        hub.quotes["600036"] = quote("600036")
        hub.quotes["600036"].data_timestamp -= timedelta(days=1)
        await hub.refresh()
        assert hub.refresh_domains()["quotes"]["source_stale_count"] == 0
        hub.quotes["600519"].data_timestamp -= timedelta(seconds=11)
        assert hub.refresh_domains()["quotes"]["source_stale_count"] == 1
        assert hub.quotes["600519"].quality == Quality.high  # 诊断读取不改缓存
    asyncio.run(scenario())


def test_fresh_low_quality_source_is_degraded_in_health_without_rejecting_price(hub):
    from types import SimpleNamespace
    from app.api.routes.health import health

    async def scenario():
        original = hub.provider.get_quotes
        async def unsettled_high_low(symbols):
            rows = await original(symbols)
            for row in rows:
                row.high = row.low = 0
            return rows
        hub.provider.get_quotes = unsettled_high_low
        await hub.refresh()
        row = hub.quotes["600519"]
        assert row.price == 10 and row.quality == Quality.low
        assert row.freshness(fresh_within=hub.stale_after).state == "degraded"
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
        status = await health(request, hub)
        assert status["status"] == "degraded"
        domain = status["refresh_domains"]["quotes"]
        assert domain["freshness"]["state"] == "ready"
        assert domain["source_degraded_count"] == 1 and domain["missing_count"] == 0
        assert domain["rejected_count"] == 0
    asyncio.run(scenario())


def test_composite_deadline_before_method_is_not_a_started_method():
    async def scenario():
        provider = Provider()
        comp = CompositeProvider([provider])
        _, _, error = await comp._attempt("get_indices", provider, (), -1)
        assert isinstance(error, BudgetExhausted)
        assert error.phase == "deadline_before_method"
        assert error.method_started is False
        assert provider.calls == {}
        row = comp.provider_health()["method_attempts"]["get_indices@offline-domain"]
        assert row["deadline_before_method"] == 1
        assert row["method_started"] == 0
    asyncio.run(scenario())


def test_provider_timeout_before_deadline_is_not_local_budget_exhaustion():
    class TimedOut:
        name = "timeout-source"
        async def get_indices(self):
            raise TimeoutError("source operation timed out")
    async def scenario():
        import time
        provider = TimedOut()
        comp = CompositeProvider([provider])
        _, _, error = await comp._attempt("get_indices", provider, (), time.monotonic() + 4)
        assert isinstance(error, TimeoutError) and not isinstance(error, BudgetExhausted)
        row = comp.provider_health()["method_attempts"]["get_indices@timeout-source"]
        assert row["provider_method_error"] == 1
        assert row["method_started"] == 1
        assert row["method_budget_exhausted"] == 0
    asyncio.run(scenario())


def test_actual_method_deadline_records_started_method():
    async def scenario():
        import time
        provider = Provider()
        provider.gates["indices"] = asyncio.Event()
        comp = CompositeProvider([provider])
        _, _, error = await comp._attempt("get_indices", provider, (), time.monotonic() + 0.005)
        assert isinstance(error, BudgetExhausted)
        assert error.phase == "method_budget_exhausted" and error.method_started is True
        assert provider.calls["indices"] == 1 and provider.active["indices"] == 0
        row = comp.provider_health()["method_attempts"]["get_indices@offline-domain"]
        assert row["method_started"] == row["method_budget_exhausted"] == 1
    asyncio.run(scenario())


def test_cancel_composite_owner_drains_both_hedged_methods(monkeypatch):
    from app.data_providers import composite
    monkeypatch.setattr(composite, "HEDGE_DELAY", 0.001)

    class Hanging:
        def __init__(self, name):
            self.name, self.task = name, None
            self.started, self.drained = asyncio.Event(), False
        async def get_indices(self):
            self.task = asyncio.current_task()
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.drained = True

    async def scenario():
        primary, backup = Hanging("primary"), Hanging("backup")
        comp = CompositeProvider([primary, backup])
        owner = asyncio.create_task(comp.get_indices())
        try:
            await asyncio.wait_for(backup.started.wait(), 0.2)
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
            assert primary.drained and backup.drained
            assert comp.provider_health()["breakers"] == {}
        finally:
            tasks = {p.task for p in (primary, backup) if p.task is not None} | {owner}
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(scenario())
