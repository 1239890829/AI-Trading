"""Local read saturation must not be reported or retried as a source outage."""
import asyncio
import time

import pytest

from app.core.inflight_reads import LocalReadCapacityExceeded
from app.data_providers import composite as module
from app.data_providers.composite import CompositeProvider
from app.schemas.market import Quote


def _quote(symbol="600519", source="good"):
    return Quote(symbol=symbol, market="SH", name="测试", price=10, source=source)


class Source:
    def __init__(self, name, result=None, exc=None):
        self.name, self.result, self.exc = name, result, exc
        self.calls = 0

    async def get_quotes(self, symbols):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result

    async def get_kline(self, symbol, timeframe, start=None, end=None):
        return await self.get_quotes([symbol])


@pytest.mark.parametrize("method", ["get_quotes", "get_kline"])
def test_local_capacity_does_not_trigger_fallback_or_change_source_breaker(method):
    busy = Source("busy", exc=LocalReadCapacityExceeded())
    backup = Source("backup", result=[_quote(source="backup")])
    chain = CompositeProvider([busy, backup])
    chain._record_failure(method, "busy")

    async def scenario():
        with pytest.raises(LocalReadCapacityExceeded):
            if method == "get_quotes":
                await chain.get_quotes(["600519"])
            else:
                await chain.get_kline("600519", "1d")

    asyncio.run(scenario())
    assert busy.calls == 1 and backup.calls == 0
    assert chain._failures == {(method, "busy"): 1}
    metrics = chain.provider_health()["method_attempts"][f"{method}@busy"]
    assert metrics["local_admission_rejected"] == 1
    assert metrics["provider_method_error"] == 0


def test_started_hedge_success_is_kept_when_same_batch_peer_is_locally_busy(monkeypatch):
    monkeypatch.setattr(module, "HEDGE_DELAY", 0)

    async def scenario():
        ready, release = asyncio.Event(), asyncio.Event()

        class Busy(Source):
            async def get_quotes(self, symbols):
                self.calls += 1
                await release.wait()
                raise LocalReadCapacityExceeded()

        class Good(Source):
            async def get_quotes(self, symbols):
                self.calls += 1
                ready.set()
                await release.wait()
                return [_quote(source=self.name)]

        busy, good = Busy("busy"), Good("good")
        chain = CompositeProvider([busy, good])
        task = asyncio.create_task(chain.get_quotes(["600519"]))
        await asyncio.wait_for(ready.wait(), 2)
        release.set()
        result = await task
        assert result[0].source == "good"
        assert chain._failures == {}
        assert chain.provider_health()["last_good"]["get_quotes"] == "good"
    asyncio.run(scenario())


def test_local_busy_hedge_stops_rotation_and_drains_already_started_peer(monkeypatch):
    monkeypatch.setattr(module, "HEDGE_DELAY", 0)

    async def scenario():
        stopped = asyncio.Event()

        class Pending(Source):
            async def get_quotes(self, symbols):
                self.calls += 1
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

        primary = Pending("primary")
        busy = Source("busy", exc=LocalReadCapacityExceeded())
        third = Source("third", result=[_quote(source="third")])
        chain = CompositeProvider([primary, busy, third])
        with pytest.raises(LocalReadCapacityExceeded):
            await asyncio.wait_for(chain.get_quotes(["600519"]), 2)
        assert stopped.is_set()
        assert primary.calls == busy.calls == 1 and third.calls == 0
        assert chain._failures == {}
    asyncio.run(scenario())


def test_recovery_preserves_accepted_rows_when_next_source_is_locally_busy():
    async def scenario():
        original = Source("original")
        accepted = Source("accepted", result=[_quote(source="accepted")])
        busy = Source("busy", exc=LocalReadCapacityExceeded())
        later = Source("later", result=[_quote("600000", "later")])
        chain = CompositeProvider([original, accepted, busy, later])
        result = await chain.recover_quote_regressions(
            "get_quotes", {("600519", "SH"): "original", ("600000", "SH"): "original"},
            deadline=time.monotonic() + 2, validate=lambda quote, key: quote,
        )
        assert list(result) == [("600519", "SH")]
        assert result[("600519", "SH")].source == "accepted"
        assert busy.calls == 1 and later.calls == 0
        assert chain._failures == {}
    asyncio.run(scenario())
