"""Explicit empty break pools are valid; missing/failed reads and exhausted budgets are not."""
import asyncio
from datetime import date
from types import SimpleNamespace as NS

import pytest

from app.data_providers import composite as cp
from app.data_providers.eastmoney import ProviderError

DAY = date(2026, 10, 14)


def test_valid_empty_pool_recovers_health_without_counting_empty_as_failure():
    async def empty(day):
        return []
    provider = cp.CompositeProvider([NS(name="empty", get_limit_break_pool=empty)])
    provider._failures["get_limit_break_pool", "empty"] = 1
    assert asyncio.run(provider.get_limit_break_pool(DAY)) == []
    assert provider.provider_health()["last_good"]["get_limit_break_pool"] == "empty"
    assert not provider._failures
    assert provider.budget_for("get_limit_break_pool") == cp.REQUEST_BUDGET_SECONDS == 12


@pytest.mark.parametrize("payload", [None, (), {}, "invalid"])
def test_only_actual_list_is_a_successful_pool(payload):
    async def invalid(day):
        return payload
    provider = cp.CompositeProvider([NS(name="invalid", get_limit_break_pool=invalid)])
    with pytest.raises(ProviderError):
        asyncio.run(provider.get_limit_break_pool(DAY))


@pytest.mark.parametrize("empty_first", [False, True])
def test_break_pool_retains_original_budget_and_cancels_hung_source(monkeypatch, empty_first):
    called, cancelled = [], []
    async def hung(day):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    async def empty(day):
        return []
    async def next_source(day):
        called.append(True)
        return []
    sources = [NS(name="hung", get_limit_break_pool=hung), NS(name="next", get_limit_break_pool=next_source)]
    if empty_first:
        sources.insert(0, NS(name="empty", get_limit_break_pool=empty))
    provider = cp.CompositeProvider(sources)
    monkeypatch.setattr(cp, "REQUEST_BUDGET_SECONDS", 0.02)
    with pytest.raises(ProviderError, match="预算"):
        asyncio.run(provider.get_limit_break_pool(DAY))
    assert cancelled == [True] and not called
    assert provider._failures == {("get_limit_break_pool", "hung"): 1}


def test_failure_and_legal_empty_keep_existing_fallback_order_and_one_deadline(monkeypatch):
    async def fail(day):
        raise ProviderError("offline")
    async def empty(day):
        return []
    async def good(day):
        return [NS(symbol="600105")]
    provider = cp.CompositeProvider([NS(name=name, get_limit_break_pool=fn)
                                     for name, fn in [("failed", fail), ("empty", empty), ("good", good)]])
    deadlines = []
    original = provider._attempt
    async def tracked(method, source, args, deadline=None):
        deadlines.append(deadline)
        return await original(method, source, args, deadline)
    monkeypatch.setattr(provider, "_attempt", tracked)
    assert asyncio.run(provider.get_limit_break_pool(DAY))[0].symbol == "600105"
    assert len(deadlines) == 3 and len(set(deadlines)) == 1 and deadlines[0] is not None
    assert provider._failures == {("get_limit_break_pool", "failed"): 1}
