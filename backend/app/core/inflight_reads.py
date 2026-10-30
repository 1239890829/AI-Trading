"""Bounded sharing of active read owners; no completed result cache or TTL."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Callable


class LocalReadCapacityExceeded(RuntimeError):
    """No new source work was started; this is not a provider failure."""

    def __init__(self):
        super().__init__("local active-read capacity reached")


class InflightReads:
    def __init__(self, maxsize: int = 128):
        self.maxsize = maxsize
        self.tasks: dict[tuple, asyncio.Task] = {}
        self.closing = False

    def peek(self, key: tuple) -> asyncio.Task | None:
        """Observe an existing owner on this loop; never register or run a factory."""
        return self.tasks.get((asyncio.get_running_loop(), key))

    async def join_existing(self, key: tuple, expected: asyncio.Task) -> tuple[bool, Any]:
        """Join only the exact observed owner, including its done-callback race."""
        if self.peek(key) is not expected and not expected.done():
            return False, None
        return True, deepcopy(await asyncio.shield(expected))

    async def read(self, key: tuple, factory: Callable) -> Any:
        if self.closing:
            raise RuntimeError("read owner is closing")
        identity = (asyncio.get_running_loop(), key)
        task = self.tasks.get(identity)
        if task is None or task.done():
            # Completed callbacks may be queued for the next loop turn. They
            # no longer consume active capacity, even before callback cleanup.
            for finished_key, finished in tuple(self.tasks.items()):
                if finished.done() and self.tasks.get(finished_key) is finished:
                    self.tasks.pop(finished_key, None)
            # Do not bypass ownership at capacity. A queue here would run before
            # the factory's own deadline and silently extend its end-to-end
            # budget. Existing keys still join; new work fails without starting.
            if len(self.tasks) >= self.maxsize:
                raise LocalReadCapacityExceeded()
            task = asyncio.create_task(factory())
            self.tasks[identity] = task

            def completed(done: asyncio.Task) -> None:
                if self.tasks.get(identity) is done:
                    self.tasks.pop(identity, None)
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(completed)
        # One or all disconnected waiters must not abandon an owned to_thread
        # operation. The existing factory deadline still bounds network reads.
        return deepcopy(await asyncio.shield(task))

    async def close(self) -> None:
        self.closing = True
        owned = tuple(self.tasks.values())
        if not owned:
            return
        drained = asyncio.gather(*owned, return_exceptions=True)
        cancelled = False
        while not drained.done():
            try:
                await asyncio.shield(drained)
            except asyncio.CancelledError:
                cancelled = True
        drained.result()
        if cancelled:
            raise asyncio.CancelledError


def read_policy(provider, method: str) -> tuple:
    """Keep provider strategy and original method budget in the read identity."""
    chain = getattr(provider, "providers", (provider,))
    budget_for = getattr(provider, "budget_for", None)
    budget = budget_for(method) if callable(budget_for) else None
    return tuple((id(source), getattr(source, "name", None),
                  getattr(source, "realtime_rank", 100)) for source in chain), budget


async def read_inflight(holder, key: tuple, factory: Callable):
    reads = getattr(holder, "_inflight_source_reads", None)
    if reads is None:
        reads = InflightReads()
        holder._inflight_source_reads = reads
    return await reads.read(key, factory)


def peek_inflight(holder, key: tuple) -> asyncio.Task | None:
    reads = getattr(holder, "_inflight_source_reads", None)
    return reads.peek(key) if isinstance(reads, InflightReads) else None


async def join_existing_inflight(holder, key: tuple, expected: asyncio.Task) -> tuple[bool, Any]:
    reads = getattr(holder, "_inflight_source_reads", None)
    return await reads.join_existing(key, expected) if isinstance(reads, InflightReads) else (False, None)


async def close_inflight_reads(holder) -> None:
    reads = getattr(holder, "_inflight_source_reads", None)
    if reads is not None:
        await reads.close()
