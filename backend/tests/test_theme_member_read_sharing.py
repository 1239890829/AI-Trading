"""Real member DB writes with isolated sources; no model or production state."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.routes import events as route
from app.core.bjtime import beijing_now_naive
from app.events.store import EventStore
from app.models.event import EventCard, EventDirection
from app.models.theme_catalog import Theme, ThemeMember
from app.models.watchlist import Base
from app.services.theme_catalog_service import ThemeCatalogService


def _setup(tmp_path, filename="members.db"):
    engine = create_engine(f"sqlite:///{tmp_path / filename}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    svc = ThemeCatalogService(factory, api_key="isolated-test-key")
    return engine, factory, svc


def test_two_event_consumers_match_single_correct_pool_with_one_sync_per_theme(tmp_path, monkeypatch):
    engine, factory, svc = _setup(tmp_path)
    codes = ["885001.TI", "885002.TI", "885003.TI"]
    with factory() as db:
        for i, code in enumerate(codes):
            db.add(Theme(code=code, name=f"题材{i}"))
        card = EventCard(fingerprint="isolated-pool", title="三个题材关联", source="fixture",
            published_at=beijing_now_naive() - timedelta(minutes=1),
            directions=[EventDirection(target_type="theme", target=f"题材{i}",
                direction=1, strength=1, chain="fixture", basis="fixture", matched_by="name") for i in range(3)])
        db.add(card)
        db.commit()
        event_id = card.id
    calls = Counter()

    async def source(code):
        calls[code] += 1
        await asyncio.sleep(.005)
        return [{"symbol": f"{600001 + codes.index(code):06d}", "name": "已核关联"}]

    monkeypatch.setattr(svc, "fetch_members", source)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(theme_catalog=svc)))
    store = EventStore(factory)

    async def run():
        try:
            concurrent = await asyncio.gather(*(route.event_stocks(event_id, request, store) for _ in range(2)))
            single = await route.event_stocks(event_id, request, store)
            assert calls == Counter({code: 1 for code in codes})
            assert all(result == single for result in concurrent)
            assert [len(pool["stocks"]) for pool in single["data"]["pools"]] == [1, 1, 1]
            assert "不构成买卖建议" in single["meta"]["disclaimer"]
            assert not svc._member_reads
            with factory() as db:
                assert len(db.scalars(select(ThemeMember)).all()) == 3
        finally:
            await svc._client.aclose()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()


def test_one_canceled_consumer_does_not_cancel_other_or_member_write(tmp_path, monkeypatch):
    engine, _, svc = _setup(tmp_path)
    calls = []

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def source(code):
            calls.append(code)
            entered.set()
            await release.wait()
            return [{"symbol": "600127", "name": "fixture"}]

        monkeypatch.setattr(svc, "fetch_members", source)
        first = asyncio.create_task(svc.sync_members("885001.TI"))
        second = asyncio.create_task(svc.sync_members("885001.TI"))
        try:
            await entered.wait()
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            release.set()
            assert await second == 1
            assert calls == ["885001.TI"]
            assert [row.symbol for row in svc.get_members("885001.TI")] == ["600127"]
            assert not svc._member_reads
        finally:
            release.set()
            await asyncio.gather(first, second, return_exceptions=True)
            await svc._client.aclose()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()


def test_shared_failure_retries_and_later_sync_still_replaces_members(tmp_path, monkeypatch):
    engine, _, svc = _setup(tmp_path)
    calls = []
    version = {"items": None}

    async def source(code):
        calls.append(code)
        await asyncio.sleep(0)
        if version["items"] is None:
            raise ValueError("isolated source unavailable")
        return version["items"]

    monkeypatch.setattr(svc, "fetch_members", source)

    async def run():
        try:
            failed = await asyncio.gather(*(svc.sync_members("885001.TI") for _ in range(8)), return_exceptions=True)
            assert all(isinstance(error, ValueError) for error in failed)
            assert len(calls) == 1 and not svc._member_reads
            version["items"] = [{"symbol": "600127", "name": "first"}]
            assert await svc.sync_members("885001.TI") == 1
            version["items"] = [{"symbol": "000001", "name": "second"}]
            assert await svc.sync_members("885001.TI") == 1
            assert [row.symbol for row in svc.get_members("885001.TI")] == ["000001"]
            # No completed-value TTL: the immediately following sync still calls its source.
            version["items"] = []
            assert await svc.sync_members("885001.TI") == 0
            assert svc.get_members("885001.TI") == []
            assert len(calls) == 4 and not svc._member_reads
        finally:
            await svc._client.aclose()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()


def test_different_service_provider_or_code_never_share_results(tmp_path, monkeypatch):
    engine, _, svc = _setup(tmp_path)
    other_engine, _, other = _setup(tmp_path, "other-provider.db")
    calls = []

    async def source(code):
        calls.append(("first", code))
        await asyncio.sleep(0)
        return [{"symbol": "600127", "name": code}]

    async def other_source(code):
        calls.append(("other", code))
        await asyncio.sleep(0)
        return [{"symbol": "000001", "name": "other provider"}]

    monkeypatch.setattr(svc, "fetch_members", source)
    monkeypatch.setattr(other, "fetch_members", other_source)

    async def run():
        try:
            assert await asyncio.gather(svc.sync_members("885001.TI"), svc.sync_members("885002.TI"),
                                       other.sync_members("885001.TI")) == [1, 1, 1]
            assert Counter(calls) == Counter({("first", "885001.TI"): 1,
                                              ("first", "885002.TI"): 1,
                                              ("other", "885001.TI"): 1})
            assert [row.symbol for row in other.get_members("885001.TI")] == ["000001"]
        finally:
            await svc._client.aclose()
            await other._client.aclose()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()
        other_engine.dispose()


def test_service_close_drains_source_when_all_views_cancel(tmp_path, monkeypatch):
    engine, _, svc = _setup(tmp_path)

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        order = []

        async def source(code):
            entered.set()
            await release.wait()
            order.append("source-completed")
            return [{"symbol": "600127", "name": "fixture"}]

        monkeypatch.setattr(svc, "fetch_members", source)
        original_close = svc._client.aclose
        async def close_client():
            order.append("client-closed")
            await original_close()
        monkeypatch.setattr(svc._client, "aclose", close_client)
        view = asyncio.create_task(svc.sync_members("885001.TI"))
        closing = None
        try:
            await entered.wait()
            view.cancel()
            with pytest.raises(asyncio.CancelledError):
                await view
            closing = asyncio.create_task(svc.aclose())
            await asyncio.sleep(0)
            assert not closing.done() and order == []
            with pytest.raises(RuntimeError, match="closing"):
                await svc.sync_members("885002.TI")
            release.set()
            await closing
            assert order == ["source-completed", "client-closed"]
            assert not svc._member_reads
            assert [row.symbol for row in svc.get_members("885001.TI")] == ["600127"]
            with pytest.raises(RuntimeError, match="closing"):
                await svc.sync_members("885001.TI")
        finally:
            release.set()
            await asyncio.gather(view, *([closing] if closing else []), return_exceptions=True)
            await original_close()

    try:
        asyncio.run(asyncio.wait_for(run(), timeout=3))
    finally:
        engine.dispose()


def test_service_close_waits_for_worker_commit_after_view_cancels(tmp_path, monkeypatch):
    engine, _, svc = _setup(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original_write = svc._write_members

    async def source(code):
        return [{"symbol": "600127", "name": "fixture"}]

    def paused_write(code, items):
        entered.set()
        assert release.wait(3), "test did not release its worker"
        return original_write(code, items)

    monkeypatch.setattr(svc, "fetch_members", source)
    monkeypatch.setattr(svc, "_write_members", paused_write)

    async def run():
        view = asyncio.create_task(svc.sync_members("885001.TI"))
        closing = None
        try:
            while not entered.is_set():
                await asyncio.sleep(.001)
            view.cancel()
            with pytest.raises(asyncio.CancelledError):
                await view
            closing = asyncio.create_task(svc.aclose())
            await asyncio.sleep(0)
            assert not closing.done(), "worker was mistaken for already stopped"
            release.set()
            await closing
            assert [row.symbol for row in svc.get_members("885001.TI")] == ["600127"]
            assert not svc._member_reads
        finally:
            release.set()
            await asyncio.gather(view, *([closing] if closing else []), return_exceptions=True)
            await svc._client.aclose()

    try:
        asyncio.run(asyncio.wait_for(run(), timeout=4))
    finally:
        engine.dispose()
