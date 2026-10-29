"""Completion observes the original read and never creates another source attempt."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

from app.core import inflight_reads
from app.services import official_match as official


def service(*, fail=False):
    calls = []
    gate = asyncio.Event()

    async def read(kind):
        calls.append(kind)
        await gate.wait()
        if kind == "industry" and fail:
            raise OSError("isolated source failure")
        code, name = ("885517.TI", "机器人概念") if kind == "concept" else ("881126.TI", "汽车零部件")
        return {"kind": kind, "source": "ths_official", "catalog_state": "ready", "total": 1,
                "synced_at": "2026-10-28T00:00:00+00:00", "items": [
                    {"code": code, "name": name, "kind": kind, "source": "ths_official"}]}

    svc = NS(read_board_catalog=read, revision=0)
    return NS(theme_catalog=svc), gate, calls


async def pending(holder):
    value = await official.resolve_official_catalog_view(holder, wait_seconds=0.001)
    assert value["reason"] == "catalog_read_pending"
    assert len(value["completion_key"]) == 32
    return value


@pytest.mark.parametrize("fail", [False, True])
def test_pending_completion_joins_once_then_done_cache_handles_owner_removal(fail):
    async def run():
        holder, release, calls = service(fail=fail)
        value = await pending(holder)
        card = official.mapping_for_name("汽车零部件", value)
        assert card["catalog_mapping_context"]["completion_key"] == value["completion_key"]
        ref = await official.current_catalog_reference(holder, ["汽车零部件"], resolution=value)
        assert ref["completion_key"] == value["completion_key"]
        waiting = asyncio.create_task(official.complete_official_catalog(holder, value["completion_key"]))
        release.set()
        result = await waiting
        assert result["scope"] == "current_directory_resolution" and result["completion_key"] == value["completion_key"]
        assert result["state"] == ("unavailable" if fail else "available")
        if fail:
            assert result["identities"] == {} and result["reason"] == "industry_directory_OSError"
        else:
            assert result["identities"]["汽车零部件"]["code"] == "881126.TI"
            assert result["identities"]["机器人概念"]["kind"] == "concept"
        await asyncio.sleep(0)
        assert inflight_reads.peek_inflight(holder.theme_catalog, ("theme-official-identities", ("source-identity-v4-kinds", 0))) is None
        again = await official.complete_official_catalog(holder, value["completion_key"])
        assert again == result and sorted(calls) == ["concept", "industry"]
        frozen = await official.resolve_official_catalog(holder)
        ordinary = await official.current_catalog_reference(holder, ["汽车零部件"], resolution=frozen)
        assert "completion_key" not in ordinary
        assert "completion_key" not in ordinary["items"][0]["catalog_mapping_context"]

    asyncio.run(run())


@pytest.mark.parametrize("fail,ttl", [(False, 60), (True, 5)])
def test_expired_old_attempt_never_starts_or_borrows_a_new_attempt(monkeypatch, fail, ttl):
    import app.core.ttl_cache as cache
    tick = {"value": 1000.0}
    monkeypatch.setattr(cache, "monotonic", lambda: tick["value"])

    async def run():
        holder, release, calls = service(fail=fail)
        first = await pending(holder)
        release.set()
        await official.complete_official_catalog(holder, first["completion_key"])
        tick["value"] += ttl
        expired = await official.complete_official_catalog(holder, first["completion_key"])
        assert expired["state"] == "unavailable" and expired["reason"] == "completion_expired_or_unknown"
        assert len(calls) == 2
        second = await official.resolve_official_catalog(holder)
        assert second["completion_key"] != first["completion_key"] and len(calls) == 4
        old = await official.complete_official_catalog(holder, first["completion_key"])
        assert old["state"] == "unavailable" and old["reason"] == "completion_unknown_or_superseded"
        assert len(calls) == 4

    asyncio.run(run())


def test_canceling_completion_waiter_never_cancels_shared_source_owner():
    async def run():
        holder, release, calls = service()
        value = await pending(holder)
        first = asyncio.create_task(official.complete_official_catalog(holder, value["completion_key"]))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not release.is_set() and sorted(calls) == ["concept", "industry"]
        survivor = asyncio.create_task(official.complete_official_catalog(holder, value["completion_key"]))
        release.set()
        assert (await survivor)["state"] == "available"
        assert sorted(calls) == ["concept", "industry"]

    asyncio.run(run())


def test_transport_timeout_is_terminal_but_owner_can_finish_without_retry(monkeypatch):
    monkeypatch.setattr(official, "CATALOG_COMPLETION_WAIT_SECONDS", 0.005)

    async def run():
        holder, release, calls = service()
        value = await pending(holder)
        result = await official.complete_official_catalog(holder, value["completion_key"])
        assert result["state"] == "unavailable" and result["reason"] == "completion_wait_timeout"
        assert not release.is_set() and sorted(calls) == ["concept", "industry"]
        release.set()
        actual = await official.complete_official_catalog(holder, value["completion_key"])
        assert actual["state"] == "available" and sorted(calls) == ["concept", "industry"]

    asyncio.run(run())


def test_revision_and_instance_scope_cannot_reuse_an_old_ticket():
    async def run():
        holder, release, calls = service()
        value = await pending(holder)
        other, other_release, other_calls = service()
        unknown = await official.complete_official_catalog(other, value["completion_key"])
        assert unknown["state"] == "unavailable" and other_calls == []
        holder.theme_catalog.revision = 1
        stale = await official.complete_official_catalog(holder, value["completion_key"])
        assert stale["state"] == "unavailable" and stale["reason"] == "catalog_revision_changed"
        release.set()
        other_release.set()
        # Drain the original owner only; no current revision resolver is started.
        reads = holder.theme_catalog._inflight_source_reads
        await reads.close()
        assert sorted(calls) == ["concept", "industry"]

    asyncio.run(run())


def test_unknown_completion_does_not_initialize_a_cache_owner_or_source():
    async def run():
        holder, _, calls = service()
        result = await official.complete_official_catalog(holder, "0" * 32)
        assert result["state"] == "unavailable" and calls == []
        assert not hasattr(holder.theme_catalog, "_inflight_source_reads")
        assert not hasattr(holder.theme_catalog, "_ttl_cache_themes.official.identities")

    asyncio.run(run())


def test_registered_owner_gets_one_ticket_before_factory_starts(monkeypatch):
    async def run():
        holder, source_release, calls = service()
        factory_release = asyncio.Event()
        original = official.read_inflight

        async def delayed(holder, key, factory):
            async def delayed_factory():
                await factory_release.wait()
                return await factory()
            return await original(holder, key, delayed_factory)

        monkeypatch.setattr(official, "read_inflight", delayed)
        try:
            value = await pending(holder)
            assert calls == [], "the original factory has not started"
            completion = asyncio.create_task(official.complete_official_catalog(holder, value["completion_key"]))
            factory_release.set()
            source_release.set()
            result = await completion
            assert result["state"] == "available" and result["completion_key"] == value["completion_key"]
            assert sorted(calls) == ["concept", "industry"]
        finally:
            factory_release.set()
            source_release.set()
            await holder.theme_catalog._inflight_source_reads.close()

    asyncio.run(run())


def test_full_existing_registry_cannot_claim_unjoinable_pending():
    async def run():
        holder, release, calls = service()
        reads = inflight_reads.InflightReads()
        holder.theme_catalog._inflight_source_reads = reads
        occupied = asyncio.Event()

        async def held():
            await occupied.wait()

        owners = [asyncio.create_task(reads.read(("occupied", i), held)) for i in range(128)]
        try:
            # Rendezvous through the original core's registered factories, not a clock freeze.
            while len(reads.tasks) < 128:
                await asyncio.sleep(0)
            value = await official.resolve_official_catalog_view(holder, wait_seconds=0.001)
            assert value["state"] == "unavailable" and value["reason"] == "local_read_capacity_exceeded"
            assert "completion_key" not in value
            assert calls == [], "local admission must not start untracked source reads"
            cache = getattr(holder.theme_catalog, "_ttl_cache_themes.official.identities")
            assert cache.get(("source-identity-v4-kinds", 0))[0] is False
            occupied.set()
            release.set()
            await asyncio.gather(*owners)
            recovered = await official.resolve_official_catalog_view(holder, wait_seconds=1)
            assert recovered["state"] == "available"
            assert sorted(calls) == ["concept", "industry"]
        finally:
            occupied.set()
            release.set()
            await asyncio.gather(*owners)
            await reads.close()

    asyncio.run(run())


@pytest.mark.parametrize("other_source_failed", [False, True])
def test_nested_directory_admission_does_not_cache_failure_or_issue_completion(other_source_failed):
    async def run():
        holder, release, calls = service()
        svc = holder.theme_catalog
        original_read = svc.read_board_catalog
        reads = inflight_reads.InflightReads(maxsize=1)
        svc._inflight_source_reads = reads
        source_factories = []

        async def directory(kind):
            if kind == "concept":
                if other_source_failed:
                    raise OSError("offline independent source failure")
                return await original_read(kind)
            async def fetch():
                source_factories.append(kind)
                return await original_read(kind)
            return await inflight_reads.read_inflight(svc, ("official-directory", kind), fetch)

        svc.read_board_catalog = directory
        release.set()
        try:
            for _ in range(2):
                value = await official.resolve_official_catalog_view(holder, wait_seconds=1)
                assert value["reason"] == "local_read_capacity_exceeded"
                assert "completion_key" not in value and value["identities"] == {}
                cache = getattr(svc, "_ttl_cache_themes.official.identities")
                assert cache.get(("source-identity-v4-kinds", 0))[0] is False
            assert source_factories == []
            reads.maxsize = 2
            svc.read_board_catalog = original_read
            assert (await official.resolve_official_catalog_view(holder, wait_seconds=1))["state"] == "available"
        finally:
            await reads.close()
    asyncio.run(run())


def test_current_market_pending_reference_can_complete_without_mutating_original_facts(monkeypatch):
    from datetime import datetime
    import app.core.bjtime as clock
    from app.api.routes import market_themes, theme_catalog
    from copy import deepcopy

    monkeypatch.setattr(clock, "beijing_now", lambda: datetime.fromisoformat("2026-10-28T10:00:00+08:00"))

    async def run():
        holder, release, calls = service()
        request = NS(app=NS(state=holder))
        original = {"theme": "汽车零部件", "strength_score": 73,
                    "persistence": {"position_risk": False, "grade": "原判断"}}

        async def cached(request, hub, day):
            return {"data": {"themes": [deepcopy(original)]}, "meta": {}}

        monkeypatch.setattr(market_themes, "_theme_board_cached", cached)
        try:
            payload = await market_themes.themes(date_str="2026-10-28", min_boards=0, min_count=1,
                                                 sort="strength", limit=30, request=request, hub=NS())
            card = payload["data"]["themes"][0]
            reference = payload["data"]["current_theme_catalog"]
            assert reference["completion_key"] == card["catalog_mapping_context"]["completion_key"]
            frozen = deepcopy(payload)
            release.set()
            completed = await theme_catalog.theme_catalog_completion(request, reference["completion_key"])
            assert completed["data"]["state"] == "available"
            assert completed["data"]["identities"]["汽车零部件"]["kind"] == "industry"
            assert payload == frozen and card["persistence"] == original["persistence"]
            assert card["strength_score"] == 73 and card["exact_catalog"] is None
            assert sorted(calls) == ["concept", "industry"]
        finally:
            release.set()
            await holder.theme_catalog._inflight_source_reads.close()

    asyncio.run(run())
