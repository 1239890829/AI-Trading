"""BUG-020: unavailable data must not become a successful empty limit-down pool."""
from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_hub
from app.api.routes import market_pools
from app.api.routes.market_pools import router
from app.data_providers import CompositeProvider, EastmoneyProvider, SinaProvider, TencentProvider
from app.data_providers.eastmoney import ProviderError

DAY = date(2026, 9, 17)
EMPTY = {"rc": 0, "data": {"tc": 0, "pool": []}}


@pytest.fixture
def chain(monkeypatch):
    eastmoney = EastmoneyProvider()
    provider = CompositeProvider([TencentProvider(), eastmoney, SinaProvider()])
    reply = {"payload": EMPTY}

    async def fetch(*args, **kwargs):
        if "error" in reply:
            raise reply["error"]
        return reply["payload"]

    monkeypatch.setattr(eastmoney, "_get_json", fetch)
    yield provider, eastmoney, reply
    asyncio.run(provider.aclose())


@pytest.mark.parametrize("payload", [
    {"rc": 102, "data": None},
    {"rc": 102, "data": {"tc": 0, "pool": []}},
    {"data": {"tc": 0, "pool": []}},
    {"rc": False, "data": {"tc": 0, "pool": []}},
    {"rc": 0, "data": None},
    {"rc": 0, "data": {}},
    {"rc": 0, "data": {"tc": 0}},
    {"rc": 0, "data": {"tc": 0, "pool": None}},
    {"rc": 0, "data": {"tc": 0, "pool": {}}},
    {"rc": 0, "data": {"pool": []}},
    {"rc": 0, "data": {"tc": 1, "pool": []}},
    {"rc": 0, "data": {"tc": False, "pool": []}},
    {"rc": 0, "data": {"tc": 1, "pool": [{"n": "missing code"}]}},
    {"rc": 0, "data": {"tc": 1, "pool": [None]}},
    {"rc": 0, "data": {"tc": 1, "pool": [{"c": "invalid"}]}},
    {"rc": 0, "data": {"tc": 2, "pool": [{"c": "600000"}, {"c": "600000"}]}},
])
def test_unavailable_payload_is_an_explicit_provider_failure(chain, payload):
    _, eastmoney, reply = chain
    reply["payload"] = payload
    with pytest.raises(ProviderError):
        asyncio.run(eastmoney.get_limit_down_pool(DAY))


def test_real_chain_does_not_hide_business_failure(chain):
    provider, _, reply = chain
    reply["payload"] = {"rc": 102, "data": None}
    with pytest.raises(ProviderError):
        asyncio.run(provider.get_limit_down_pool(DAY))
    assert provider._failures == {("get_limit_down_pool", "eastmoney"): 1}


def test_real_chain_does_not_hide_transport_failure(chain):
    provider, _, reply = chain
    reply["error"] = ProviderError("fixture transport failure")
    with pytest.raises(ProviderError):
        asyncio.run(provider.get_limit_down_pool(DAY))


def test_unsupported_sources_alone_are_not_a_valid_empty(chain):
    provider, _, _ = chain
    provider = CompositeProvider([p for p in provider.providers if p.name != "eastmoney"])
    with pytest.raises(ProviderError):
        asyncio.run(provider.get_limit_down_pool(DAY))
    assert provider._failures == {}, "Unsupported is not a failed network request"


def test_valid_empty_recovers_health_after_failure(chain):
    provider, _, reply = chain
    reply["error"] = ProviderError("fixture failure")
    with pytest.raises(ProviderError):
        asyncio.run(provider.get_limit_down_pool(DAY))
    del reply["error"]
    assert asyncio.run(provider.get_limit_down_pool(DAY)) == []
    health = provider.provider_health()
    assert health["last_good"]["get_limit_down_pool"] == "eastmoney"
    assert not provider._failures
    eastmoney = next(p for p in health["providers"] if p["name"] == "eastmoney")
    assert "get_limit_down_pool" in eastmoney["methods"]


def test_pool_failure_reaches_http_as_502(chain, monkeypatch):
    provider, _, reply = chain
    reply["payload"] = {"rc": 102, "data": None}
    async def calendar(_provider):
        return [DAY]
    monkeypatch.setattr(market_pools, "trading_days", calendar)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_hub] = lambda: SimpleNamespace(provider=provider)
    with TestClient(app) as client:
        response = client.get("/api/limit-down?date=2026-09-17")
    assert response.status_code == 502
    assert "跌停池数据源失败" in response.json()["detail"]


@pytest.mark.parametrize(("days", "status"), [
    ([date(2026, 9, 24), date(2026, 9, 28)], 422),
    ([date(2026, 9, 24)], 503),
])
def test_nontrading_or_uncovered_date_never_queries_pool(monkeypatch, days, status):
    calls = []
    async def calendar(_provider):
        return days
    async def pool(day):
        calls.append(day)
        return []
    monkeypatch.setattr(market_pools, "trading_days", calendar)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_hub] = lambda: SimpleNamespace(
        provider=SimpleNamespace(get_limit_down_pool=pool)
    )
    with TestClient(app) as client:
        response = client.get("/api/limit-down?date=2026-09-25")
    assert response.status_code == status
    assert calls == []


def test_calendar_failure_does_not_query_or_label_upstream_pool(monkeypatch):
    calls = []
    async def calendar(_provider):
        raise RuntimeError("calendar unavailable")
    async def pool(day):
        calls.append(day)
        return []
    monkeypatch.setattr(market_pools, "trading_days", calendar)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_hub] = lambda: SimpleNamespace(
        provider=SimpleNamespace(get_limit_down_pool=pool)
    )
    with TestClient(app) as client:
        response = client.get("/api/limit-down?date=2026-09-25")
    assert response.status_code == 503
    assert "交易日历不可用" in response.json()["detail"]
    assert calls == []


@pytest.mark.parametrize("empty_first", [False, True])
def test_pool_budget_cancels_hung_source_without_calling_next(monkeypatch, empty_first):
    import app.data_providers.composite as module

    cancelled = []
    called = []

    async def hung(day):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def next_source(day):
        called.append(True)
        return []

    async def valid_empty(day):
        return []

    sources = [
        SimpleNamespace(name="hung", get_limit_down_pool=hung),
        SimpleNamespace(name="next", get_limit_down_pool=next_source),
    ]
    if empty_first:
        sources.insert(0, SimpleNamespace(name="empty", get_limit_down_pool=valid_empty))
    provider = CompositeProvider(sources)
    monkeypatch.setattr(module, "REQUEST_BUDGET_SECONDS", 0.02)

    async def run():
        with pytest.raises(ProviderError, match="预算"):
            await asyncio.wait_for(provider.get_limit_down_pool(DAY), timeout=1)

    asyncio.run(run())
    assert cancelled == [True] and called == []
    assert provider._failures == {("get_limit_down_pool", "hung"): 1}


def test_nonempty_fallback_keeps_one_shared_deadline(monkeypatch):
    async def empty(day):
        return []

    rows = [SimpleNamespace(symbol="600000")]

    async def nonempty(day):
        return rows

    provider = CompositeProvider([
        SimpleNamespace(name="empty", get_limit_down_pool=empty),
        SimpleNamespace(name="nonempty", get_limit_down_pool=nonempty),
    ])
    attempt = provider._attempt
    deadlines = []

    async def tracked(method, source, args, deadline=None):
        deadlines.append(deadline)
        return await attempt(method, source, args, deadline)

    monkeypatch.setattr(provider, "_attempt", tracked)
    assert asyncio.run(provider.get_limit_down_pool(DAY)) == rows
    assert len(deadlines) == 2 and deadlines[0] is not None and deadlines[0] == deadlines[1]
    assert provider.provider_health()["last_good"]["get_limit_down_pool"] == "nonempty"


def test_none_is_not_a_valid_empty_pool():
    async def missing(day):
        return None

    provider = CompositeProvider([SimpleNamespace(name="missing", get_limit_down_pool=missing)])
    with pytest.raises(ProviderError):
        asyncio.run(provider.get_limit_down_pool(DAY))
