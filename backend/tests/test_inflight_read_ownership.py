"""Ownership, cancellation, identity and capacity of active read sharing."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from app.core.inflight_reads import InflightReads, close_inflight_reads, read_inflight


async def settle():
    for _ in range(8):
        await asyncio.sleep(0)


def test_success_is_copied_and_never_reused_after_completion():
    async def run():
        holder = SimpleNamespace()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def factory():
            calls.append(True)
            entered.set()
            await release.wait()
            return {"source_time": "unchanged", "rows": [{"price": 10}]}
        first = asyncio.create_task(read_inflight(holder, ("exact", 1), factory))
        second = asyncio.create_task(read_inflight(holder, ("exact", 1), factory))
        await entered.wait()
        release.set()
        a, b = await asyncio.gather(first, second)
        assert len(calls) == 1
        a["rows"][0]["price"] = 99
        assert b["rows"][0]["price"] == 10
        assert (await read_inflight(holder, ("exact", 1), factory))["source_time"] == "unchanged"
        assert len(calls) == 2
        await close_inflight_reads(holder)
    asyncio.run(run())


def test_same_round_failure_shared_but_next_round_can_retry():
    async def run():
        owner = InflightReads()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def factory():
            calls.append(True)
            entered.set()
            await release.wait()
            if len(calls) == 1:
                raise RuntimeError("isolated upstream failure")
            return {"available": True}
        tasks = [asyncio.create_task(owner.read(("input",), factory)) for _ in range(3)]
        await entered.wait()
        await settle()
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert len(calls) == 1 and all(isinstance(r, RuntimeError) for r in results)
        assert await owner.read(("input",), factory) == {"available": True}
        assert len(calls) == 2
        await owner.close()
    asyncio.run(run())


def test_one_waiter_cancel_does_not_cancel_shared_source():
    async def run():
        owner = InflightReads()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def factory():
            calls.append(True)
            entered.set()
            await release.wait()
            return [10]
        first = asyncio.create_task(owner.read(("input",), factory))
        second = asyncio.create_task(owner.read(("input",), factory))
        try:
            await entered.wait()
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            release.set()
            assert await second == [10] and len(calls) == 1
        finally:
            release.set()
            await asyncio.gather(first, second, return_exceptions=True)
            await owner.close()
    asyncio.run(run())


def test_all_waiters_cancel_and_close_really_drains_owned_thread():
    async def run():
        loop = asyncio.get_running_loop()
        owner = InflightReads()
        entered = asyncio.Event()
        release, finished = threading.Event(), threading.Event()
        def blocking():
            loop.call_soon_threadsafe(entered.set)
            try:
                if not release.wait(2):
                    raise RuntimeError("offline deadman expired")
                return [10]
            finally:
                finished.set()
        async def factory():
            return await asyncio.to_thread(blocking)
        tasks = [asyncio.create_task(owner.read(("input",), factory)) for _ in range(2)]
        closing = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            closing = asyncio.create_task(owner.close())
            await settle()
            assert not finished.is_set() and not closing.done()
            with pytest.raises(RuntimeError, match="closing"):
                await owner.read(("new",), factory)
            closing.cancel()
            await settle()
            closing.cancel()
            await settle()
            assert not closing.done()  # Cancellation does not pretend to kill the worker.
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert finished.is_set() and not owner.tasks
        finally:
            release.set()
            await asyncio.gather(*tasks, *([closing] if closing else []), return_exceptions=True)
    asyncio.run(run())


def test_full_mapping_preserves_caller_owned_overflow_and_exact_key_isolation():
    async def run():
        owner = InflightReads(maxsize=1)
        release = asyncio.Event()
        entered = asyncio.Event()
        calls, cancelled = [], []
        async def first():
            calls.append("first")
            entered.set()
            await release.wait()
            return [1]
        async def overflow():
            calls.append("overflow")
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        one = asyncio.create_task(owner.read(("input", 1), first))
        two = None
        try:
            await entered.wait()
            two = asyncio.create_task(owner.read(("input", 2), overflow))
            await settle()
            assert calls == ["first", "overflow"] and len(owner.tasks) == 1
            two.cancel()
            with pytest.raises(asyncio.CancelledError):
                await two
            assert cancelled == [True]
            release.set()
            assert await one == [1]
            await owner.close()
        finally:
            release.set()
            await asyncio.gather(one, *([two] if two else []), return_exceptions=True)
    asyncio.run(run())


def test_composite_close_drains_helper_owner_before_closing_source():
    from app.data_providers.composite import CompositeProvider
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        order = []
        class Source:
            name = "offline"
            async def aclose(self):
                order.append("client_closed")
        provider = CompositeProvider([Source()])
        async def factory():
            entered.set()
            await release.wait()
            order.append("read_finished")
            return [1]
        read = asyncio.create_task(read_inflight(provider, ("input",), factory))
        closing = None
        try:
            await entered.wait()
            read.cancel()
            await asyncio.gather(read, return_exceptions=True)
            closing = asyncio.create_task(provider.aclose())
            await settle()
            assert not closing.done() and order == []
            release.set()
            await closing
            assert order == ["read_finished", "client_closed"]
        finally:
            release.set()
            await asyncio.gather(read, *([closing] if closing else []), return_exceptions=True)
    asyncio.run(run())


def test_cancelled_composite_close_still_closes_client_after_owned_drain():
    from app.data_providers.composite import CompositeProvider
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        closed = []
        class Source:
            name = "offline"
            async def aclose(self): closed.append(True)
        provider = CompositeProvider([Source()])
        async def factory():
            entered.set()
            await release.wait()
            return [1]
        read = asyncio.create_task(read_inflight(provider, ("input",), factory))
        closing = None
        try:
            await entered.wait()
            read.cancel()
            await asyncio.gather(read, return_exceptions=True)
            closing = asyncio.create_task(provider.aclose())
            await settle()
            closing.cancel()
            await settle()
            assert not closing.done() and closed == []
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert closed == [True]
        finally:
            release.set()
            await asyncio.gather(read, *([closing] if closing else []), return_exceptions=True)
    asyncio.run(run())


def test_cancelled_application_close_still_closes_single_provider_after_drain():
    from app.bootstrap.services import AppServices
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        closed = []
        class Source:
            async def aclose(self): closed.append(True)
        provider = Source()
        async def factory():
            entered.set()
            await release.wait()
            return [1]
        read = asyncio.create_task(read_inflight(provider, ("input",), factory))
        closing = None
        try:
            await entered.wait()
            read.cancel()
            await asyncio.gather(read, return_exceptions=True)
            app_services = SimpleNamespace(provider=provider, theme_catalog=None)
            closing = asyncio.create_task(AppServices.aclose(app_services))
            await settle()
            closing.cancel()
            await settle()
            assert not closing.done() and closed == []
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert closed == [True]
        finally:
            release.set()
            await asyncio.gather(read, *([closing] if closing else []), return_exceptions=True)
    asyncio.run(run())
