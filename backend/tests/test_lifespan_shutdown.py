"""lifespan 停机收割 _reap 的行为契约：任何单任务不得拖死关机。

背景：2026-09-03 全量 pytest 两连整场挂死（faulthandler 栈转储实证卡点
test_alerts 的 TestClient.__exit__ → wait_shutdown）：stop.set()/cancel()
打不断 in-flight 的 tick await，裸 `await task` 会把 lifespan 关闭整体挂死。
tests/conftest.py 已在测试环境关调度器（根因规避）；这里锁住生产侧兜底
_reap 的契约——宽限内自然退出、超时强制 cancel、异常吞掉、None/已结束直通。
"""
from __future__ import annotations

import asyncio
import contextlib
import time

from app.core.scheduler import _reap


def _run(coro) -> None:
    asyncio.run(coro)


async def _sleep_forever() -> None:
    await asyncio.sleep(3600)


async def _finish_after(delay: float) -> str:
    await asyncio.sleep(delay)
    return "ok"


async def _boom() -> None:
    raise RuntimeError("task-own-failure")


def test_reap_none_and_done_tasks_are_noop():
    async def scenario():
        await _reap(None, name="none")  # type: ignore[arg-type]

        done = asyncio.create_task(asyncio.sleep(0))
        await asyncio.sleep(0)  # 让 done 真正跑完
        await _reap(done, name="already-done")

    _run(scenario())


def test_reap_returns_quickly_when_task_finishes_in_grace():
    async def scenario():
        task = asyncio.create_task(_finish_after(0.01))
        t0 = time.monotonic()
        await _reap(task, name="normal", grace=5.0)
        assert time.monotonic() - t0 < 1.0
        assert task.done() and not task.cancelled()

    _run(scenario())


def test_reap_cancels_task_that_ignores_stop():
    """卡死任务：宽限超时 → cancel → 收割，总耗时有上界（不得等满 3600s）。"""

    async def scenario():
        task = asyncio.create_task(_sleep_forever())
        t0 = time.monotonic()
        await _reap(task, name="stuck", grace=0.05)
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, f"_reap 耗时 {elapsed:.2f}s，停机被单任务拖死"
        assert task.cancelled()

    _run(scenario())


def test_reap_swallows_task_own_exception():
    async def scenario():
        task = asyncio.create_task(_boom())
        await _reap(task, name="boom", grace=5.0)
        assert task.done() and not task.cancelled()

    _run(scenario())


def test_reap_swallows_already_cancelled_task():
    async def scenario():
        task = asyncio.create_task(_sleep_forever())
        task.cancel()
        await asyncio.sleep(0)  # 让 cancel 落地
        t0 = time.monotonic()
        await _reap(task, name="pre-cancelled", grace=5.0)
        assert time.monotonic() - t0 < 1.0

    _run(scenario())


def test_reap_gives_up_when_cancel_cannot_kill():
    """吞掉第一次 cancel 的任务：cancel 后第二段宽限仍超时 → _reap 必须
    放弃等待而不是挂死（对应生产里 shield-forever 级别的僵死任务）。"""

    async def scenario():
        async def swallow_one_cancel() -> None:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass  # 吞掉第一次 cancel（模拟僵死）
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise  # 第二次必死（测试收尾用）

        task = asyncio.create_task(swallow_one_cancel())
        t0 = time.monotonic()
        await _reap(task, name="swallower", grace=0.05)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"_reap 耗时 {elapsed:.2f}s，放弃路径失效"
        # 测试收尾：收割被放弃的悬挂任务，避免 asyncio.run 关循环时告警
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    _run(scenario())
