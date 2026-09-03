"""marketdb 盘后同步调度器测试（app/market/marketdb_sync.py）。

锁三类行为：
1. 窗口判定纯函数：过点 + 当日未同步 → 跑；窗口前 / 当日已同步 → 不跑。
2. 磁盘幂等：成功写 state（重启安全），失败不写（下轮重试）；state 缺失/损坏当空。
3. loop 行为（假 runner，绝不真跑子进程）：到点当日只跑一次；窗口前空转。

不真拉数据：_run_subprocess 与 beijing_now 全部 monkeypatch，
STATE_PATH 注入 tmp_path（不碰生产 data/marketdb/）。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import app.market.marketdb_sync as ms


# ---------------------------------------------------------------- 窗口判定


def test_should_run_sync_window():
    at_1630 = datetime(2026, 9, 3, 16, 30)
    at_1629 = datetime(2026, 9, 3, 16, 29)
    at_0900 = datetime(2026, 9, 3, 9, 0)

    # 未到点一律不跑（哪怕 state 为空）
    assert not ms.should_run_sync({}, at_1629, run_hour=16, run_minute=30)
    assert not ms.should_run_sync({}, at_0900, run_hour=16, run_minute=30)
    # 过点 + 无 state / 昨日 state → 跑
    assert ms.should_run_sync({}, at_1630, run_hour=16, run_minute=30)
    assert ms.should_run_sync(
        {"last_sync_date": "20260902"}, at_1630, run_hour=16, run_minute=30
    )
    # 当日已同步 → 不跑（幂等）
    assert not ms.should_run_sync(
        {"last_sync_date": "20260903"}, at_1630, run_hour=16, run_minute=30
    )
    # 过点很久（次日清晨场景不存在——now 是当日；同日 23:59 仍不重跑）
    assert not ms.should_run_sync(
        {"last_sync_date": "20260903"}, datetime(2026, 9, 3, 23, 59),
        run_hour=16, run_minute=30,
    )


# ---------------------------------------------------------------- 磁盘幂等 state


def test_state_roundtrip_and_corruption(monkeypatch, tmp_path):
    state_path = tmp_path / "marketdb" / "sync_state.json"
    monkeypatch.setattr(ms, "STATE_PATH", state_path)

    # 缺失 → 空
    assert ms._load_state() == {}
    # 写入 → 读回
    ms._mark_synced(datetime(2026, 9, 3, 16, 31, 5))
    state = ms._load_state()
    assert state["last_sync_date"] == "20260903"
    assert state["at"] == "2026-09-03T16:31:05"
    # 损坏 → 当空（宁可多跑一次幂等同步）
    state_path.write_text("not-json{", encoding="utf-8")
    assert ms._load_state() == {}


def test_run_sync_once_marks_state_only_on_success(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(ms, "STATE_PATH", state_path)
    monkeypatch.setattr(
        ms, "beijing_now", lambda: datetime(2026, 9, 3, 16, 31)
    )

    async def ok():
        return 0, ""

    async def boom():
        return 1, "sync failed: some stderr tail"

    monkeypatch.setattr(ms, "_run_subprocess", ok)
    assert asyncio.run(ms.run_sync_once()) is True
    assert json.loads(state_path.read_text())["last_sync_date"] == "20260903"

    # 失败：state 保持上一次成功的内容（不覆盖、不误标）
    monkeypatch.setattr(ms, "_run_subprocess", boom)
    assert asyncio.run(ms.run_sync_once()) is False
    assert json.loads(state_path.read_text())["last_sync_date"] == "20260903"

    # 全新 state + 失败 → 不写文件
    state_path.unlink()
    assert asyncio.run(ms.run_sync_once()) is False
    assert not state_path.exists()


# ---------------------------------------------------------------- loop 行为


def test_scheduler_runs_once_per_day(monkeypatch, tmp_path):
    """到点后当日只跑一次：第一轮执行 + 写 state，后续轮次幂等跳过。"""
    state_path = tmp_path / "state.json"
    calls: list[int] = []

    async def fake_runner():
        calls.append(1)
        return 0, ""

    monkeypatch.setattr(ms, "_run_subprocess", fake_runner)
    monkeypatch.setattr(ms, "STATE_PATH", state_path)
    monkeypatch.setattr(
        ms, "beijing_now", lambda: datetime(2026, 9, 3, 16, 31)
    )

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(
            ms.marketdb_sync_scheduler(stop=stop, check_interval_seconds=0.01)
        )
        await asyncio.sleep(0.08)
        assert len(calls) == 1  # 第一轮到点执行
        assert state_path.exists()
        stop.set()
        await asyncio.wait_for(task, timeout=2)
        assert len(calls) == 1  # state 幂等：后续轮次不重跑

    asyncio.run(go())


def test_scheduler_skips_before_window(monkeypatch, tmp_path):
    """窗口前空转：任何轮次都不触发同步。"""
    calls: list[int] = []

    async def fake_runner():
        calls.append(1)
        return 0, ""

    monkeypatch.setattr(ms, "_run_subprocess", fake_runner)
    monkeypatch.setattr(ms, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(
        ms, "beijing_now", lambda: datetime(2026, 9, 3, 10, 0)
    )

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(
            ms.marketdb_sync_scheduler(stop=stop, check_interval_seconds=0.01)
        )
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2)
        assert calls == []

    asyncio.run(go())


def test_scheduler_survives_runner_exception(monkeypatch, tmp_path):
    """runner 抛异常（子进程创建失败等）→ loop 捕获留痕继续下一轮，不拖垮 lifespan。"""
    state_path = tmp_path / "state.json"
    calls: list[int] = []

    async def broken():
        calls.append(1)
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(ms, "_run_subprocess", broken)
    monkeypatch.setattr(ms, "STATE_PATH", state_path)
    monkeypatch.setattr(
        ms, "beijing_now", lambda: datetime(2026, 9, 3, 16, 31)
    )

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(
            ms.marketdb_sync_scheduler(stop=stop, check_interval_seconds=0.01)
        )
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(go())
    assert len(calls) >= 1  # 跑过且 loop 活着走到了 stop（未抛出）
    assert not state_path.exists()


def test_config_defaults_off():
    """开关默认关：每日几十 MB dump 下载须使用者知情打开（config 契约）。

    断言字段定义默认值而非 Settings() 实例——conftest 会注入
    ASHARE_MARKETDB_SYNC_ENABLED=false 环境变量，实例断言测到的是
    conftest 而不是默认值。
    """
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["marketdb_sync_enabled"].default is False
    assert fields["marketdb_sync_hour"].default == 16
    assert fields["marketdb_sync_minute"].default == 30
