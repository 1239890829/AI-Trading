"""Bounded sharing of active read owners; no completed result cache or TTL."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Callable


class InflightReads:
    def __init__(self, maxsize: int = 128):
        self.maxsize = maxsize
        self.tasks: dict[tuple, asyncio.Task] = {}
        self.closing = False

    async def read(self, key: tuple, factory: Callable) -> Any:
        if self.closing:
            raise RuntimeError("read owner is closing")
        identity = (asyncio.get_running_loop(), key)
        task = self.tasks.get(identity)
        if task is None or task.done():
            # At capacity retain the original caller-owned read. It creates no
            # detached owner; close() only drains owners created by this helper.
            if len(self.tasks) >= self.maxsize:
                return deepcopy(await factory())
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


async def close_inflight_reads(holder) -> None:
    reads = getattr(holder, "_inflight_source_reads", None)
    if reads is not None:
        await reads.close()
