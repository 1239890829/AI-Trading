"""Offline same-day pool identity; current failures never invent a valid empty pool."""

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

import pytest

from app.api.routes import market_pools as routes
from app.core.ttl_cache import cache_on
from app.schemas.envelope import Envelope, LimitUpPoolPayload
from app.schemas.market import LimitUpRecord, Quality

DAY = date(2026, 10, 14)


@pytest.fixture
def pool_context(monkeypatch):
    async def verify(*args, **kwargs):
        return None
    async def meta(*args, **kwargs):
        return {"source": "offline"}
    monkeypatch.setattr(routes, "verify_pool_date", verify)
    monkeypatch.setattr(routes, "dated_meta", meta)
    return NS(app=NS(state=NS()))


def row(symbol="600001", day=DAY):
    return LimitUpRecord(symbol=symbol, trade_date=day, source="ths")


def test_enhanced_read_does_not_restart_cached_up_pool_age(pool_context, monkeypatch):
    from app.core import ttl_cache
    clock = [0.0]
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock[0])

    async def check():
        calls = {"up": 0, "break": 0}
        received = datetime(2026, 10, 14, 1, 35, tzinfo=timezone.utc)
        async def up(_day):
            calls["up"] += 1
            return [row().model_copy(update={"reason": f"version-{calls['up']}", "received_at": received})]
        async def broken(_day):
            calls["break"] += 1
            return []
        hub = NS(provider=NS(get_limit_up_pool=up, get_limit_break_pool=broken))
        await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=hub)
        clock[0] = 59
        first = await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=hub, include_breaks=True)
        assert first["data"]["pool"][0]["reason"] == "version-1"
        clock[0] = 61
        await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=hub)
        refreshed = await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=hub, include_breaks=True)
        assert refreshed["data"]["pool"][0]["reason"] == "version-2"
        assert calls == {"up": 2, "break": 1}
        assert first["data"]["qualification_observations"]["600001"][0]["observed_at"] == received.isoformat().replace("+00:00", "Z")
    asyncio.run(check())


def test_optional_break_read_is_singleflight_and_legacy_has_no_new_source(pool_context):
    async def check():
        calls = {"up": 0, "break": 0}
        async def up(_day):
            calls["up"] += 1
            return [row()]
        async def broken(_day):
            calls["break"] += 1
            await asyncio.sleep(0)
            return [row("600002")]
        hub = NS(provider=NS(get_limit_up_pool=up, get_limit_break_pool=broken))
        legacy = await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=hub)
        assert calls == {"up": 1, "break": 0}
        assert "break_pool" not in legacy["data"]
        results = await asyncio.gather(*(routes.limit_up(pool_context, date_str=DAY.isoformat(),
            hub=hub, include_breaks=True) for _ in range(3)))
        assert calls == {"up": 1, "break": 1}
        data = results[0]["data"]
        assert data["coverage"]["limit_up"]["state"] == "success"
        assert data["coverage"]["limit_break"]["state"] == "success"
        assert data["break_pool"][0]["symbol"] == "600002"
        assert data["qualification_observations"]["600002"][0]["kind"] == "limit_break"
        parsed = Envelope[LimitUpPoolPayload].model_validate(results[0])
        assert parsed.data.break_pool[0].symbol == "600002"
        data["qualification_observations"]["600002"][0]["source"] = "caller-mutation"
        hit = await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=hub, include_breaks=True)
        assert hit["data"]["qualification_observations"]["600002"][0]["source"] == "ths"
    asyncio.run(check())


def test_break_failure_is_not_empty_and_keeps_same_day_confirmed_observation(pool_context):
    async def check():
        async def up(_day):
            return []
        failed = False
        async def broken(_day):
            if failed:
                raise RuntimeError("offline source failure")
            return [row("600002")]
        hub = NS(provider=NS(get_limit_up_pool=up, get_limit_break_pool=broken))
        first = await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=hub, include_breaks=True)
        failed = True
        cache_on(pool_context.app.state, "market.limit_break", 60, maxsize=8).invalidate()
        second = await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=hub, include_breaks=True)
        data = second["data"]
        assert data["break_pool"] is None
        assert data["coverage"]["limit_break"] == {"state": "error", "error": "offline source failure"}
        assert data["qualification_observations"]["600002"] == first["data"]["qualification_observations"]["600002"]
    asyncio.run(check())


def test_up_failure_does_not_mask_successful_break_pool(pool_context):
    async def check():
        async def up(_day):
            raise RuntimeError("up unavailable")
        async def broken(_day):
            return [row("600002")]
        result = await routes.limit_up(pool_context, date_str=DAY.isoformat(),
            hub=NS(provider=NS(get_limit_up_pool=up, get_limit_break_pool=broken)), include_breaks=True)
        assert result["data"]["coverage"]["limit_up"]["state"] == "error"
        assert result["data"]["coverage"]["limit_break"]["state"] == "success"
        assert result["data"]["pool"] == []
        assert "600002" in result["data"]["qualification_observations"]
    asyncio.run(check())


def test_empty_break_is_valid_but_wrong_day_never_confirms_qualification(pool_context):
    async def check():
        async def up(_day):
            return []
        async def empty(_day):
            return []
        hub = NS(provider=NS(get_limit_up_pool=up, get_limit_break_pool=empty))
        result = await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=hub, include_breaks=True)
        assert result["data"]["break_pool"] == []
        assert result["data"]["coverage"]["limit_break"]["state"] == "success"
        async def wrong_day(_day):
            return [row("600002", date(2026, 10, 13))]
        hub.provider.get_limit_break_pool = wrong_day
        cache_on(pool_context.app.state, "market.limit_break", 60, maxsize=8).invalidate()
        result = await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=hub, include_breaks=True)
        assert result["data"]["coverage"]["limit_break"]["state"] == "error"
        assert result["data"]["break_pool"] is None
        assert result["data"]["qualification_observations"] == {}
    asyncio.run(check())


def test_failed_refresh_keeps_prior_up_identity_but_never_borrows_it_across_days(pool_context):
    async def check():
        failed = False
        async def up(_day):
            if failed:
                raise RuntimeError("up failed")
            return [row()]
        async def broken(_day):
            return []
        hub = NS(provider=NS(get_limit_up_pool=up, get_limit_break_pool=broken))
        first = await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=hub, include_breaks=True)
        prior = first["data"]["qualification_observations"]["600001"]
        failed = True
        for name in ("market.limit_up", "market.limit_break"):
            cache_on(pool_context.app.state, name, 60, maxsize=8).invalidate()
        second = await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=hub, include_breaks=True)
        assert second["data"]["pool"] == []
        assert second["data"]["coverage"]["limit_up"]["state"] == "error"
        assert second["data"]["qualification_observations"]["600001"] == prior
        next_day = await routes.limit_up(pool_context, date_str="2026-10-15", hub=hub, include_breaks=True)
        assert next_day["data"]["qualification_observations"] == {}
    asyncio.run(check())


def test_unavailable_or_invalid_quality_never_confirms_pool_membership(pool_context):
    async def check():
        async def up(_day):
            return [row().model_copy(update={"quality": Quality.invalid})]
        result = await routes.limit_up(pool_context, date_str=DAY.isoformat(),
            hub=NS(provider=NS(get_limit_up_pool=up)), include_breaks=True)
        data = result["data"]
        assert data["coverage"]["limit_up"]["state"] == "unknown"
        assert data["coverage"]["limit_break"]["state"] == "unknown"
        assert data["break_pool"] is None
        assert data["qualification_observations"] == {}
    asyncio.run(check())


def test_first_low_quality_source_rows_remain_readable_without_positive_qualification(pool_context):
    async def check():
        async def low(_day):
            return [row().model_copy(update={"quality": Quality.low, "reason": "来源原句仍可核对"})]
        result = await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=NS(provider=NS(
            get_limit_up_pool=low, get_limit_break_pool=low)), include_breaks=True)
        data = result["data"]
        assert data["pool"][0]["quality"] == data["break_pool"][0]["quality"] == "low"
        assert data["coverage"]["limit_up"]["state"] == data["coverage"]["limit_break"]["state"] == "success"
        assert data["reason_observations"]["600001"][0]["reason"] == "来源原句仍可核对"
        assert data["qualification_observations"] == {}
    asyncio.run(check())


def test_mixed_quality_pool_only_retains_high_or_medium_affirmative_identity(pool_context):
    async def check():
        async def up(_day):
            return [row("600001"), row("600002").model_copy(update={"quality": Quality.low})]
        async def broken(_day):
            return [row("600003").model_copy(update={"quality": Quality.medium}),
                    row("600004").model_copy(update={"quality": Quality.low})]
        result = await routes.limit_up(pool_context, date_str=DAY.isoformat(), hub=NS(provider=NS(
            get_limit_up_pool=up, get_limit_break_pool=broken)), include_breaks=True)
        data = result["data"]
        assert len(data["pool"]) == len(data["break_pool"]) == 2
        assert set(data["qualification_observations"]) == {"600001", "600003"}
        assert data["qualification_observations"]["600001"][0]["kind"] == "limit_up"
        assert data["qualification_observations"]["600003"][0]["kind"] == "limit_break"
    asyncio.run(check())


def test_http_response_preserves_optional_contract_without_expanding_legacy(pool_context, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.deps import get_hub
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    async def up(_day):
        return [row()]
    async def broken(_day):
        return []
    app.dependency_overrides[get_hub] = lambda: NS(provider=NS(
        get_limit_up_pool=up, get_limit_break_pool=broken))
    with TestClient(app) as client:
        legacy = client.get("/api/limit-up", params={"date": DAY.isoformat()}).json()["data"]
        assert "break_pool" not in legacy and "coverage" not in legacy
        data = client.get("/api/limit-up", params={"date": DAY.isoformat(), "include_breaks": "true"}).json()["data"]
        assert data["break_pool"] == []
        assert data["coverage"]["limit_break"]["state"] == "success"
        assert data["qualification_observations"]["600001"][0]["kind"] == "limit_up"
