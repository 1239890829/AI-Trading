"""SchedulerRegistry（S2-2）的行为契约。

锁住七件事：
1. 开关未登记的调度器**启动期直接失败**（否则测试环境不会关掉它，会真的跑生产调度）；
2. 开关关闭 → 不 spawn，且**记下原因**（"没启动"必须是可解释的，不是静默消失）；
3. 重名 → 失败（重名会让收割与观测按名字取错记录）；
4. 启动后可观测（state / uptime / last_tick），收割后为 stopped；
5. `add_periodic` 的 heartbeat 自动记，tick 返回值可以改本轮之后的节奏；
6. 任务异常死亡 → 记 failures/last_error + 排重启，且出现在 `dead_names()`（哨兵出口）；
7. snapshot 可 JSON 序列化（直接喂 REST）+ 外部循环的 heartbeat 显式标 external。
"""
from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from app.core.scheduler import SCHEDULER_SWITCH_ATTRS, SchedulerRegistry


def _run(coro):
    return asyncio.run(coro)


async def _forever(stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(0.01)


def test_undeclared_switch_fails_fast():
    """漏登记的开关在测试环境不会被关掉 —— 必须启动期就炸，不能静默放行。"""
    reg = SchedulerRegistry()
    with pytest.raises(ValueError, match="SCHEDULER_SWITCH_ATTRS"):
        reg.add("bad", lambda: asyncio.sleep(0), switch="not_a_real_switch_enabled")


def test_duplicate_name_raises():
    reg = SchedulerRegistry()
    reg.add("dup", lambda: asyncio.sleep(0))
    with pytest.raises(ValueError, match="重名"):
        reg.add("dup", lambda: asyncio.sleep(0))


def test_switch_off_is_not_spawned_and_reason_is_recorded():
    """conftest 把全部登记开关设为 false —— 关掉的调度器不得 spawn，且原因可见。"""

    async def scenario():
        reg = SchedulerRegistry()
        called: list[int] = []

        async def worker() -> None:
            called.append(1)

        rec = reg.add("off", worker, switch="picks_watcher_enabled")
        assert rec.enabled is False
        assert "ASHARE_PICKS_WATCHER_ENABLED" in (rec.reason or "")

        await reg.start()
        await asyncio.sleep(0.01)
        assert called == []

        row = reg.snapshot()[0]
        assert row["state"] == "disabled"
        assert row["not_started_reason"]  # 不静默：没启动必须给原因
        assert row["heartbeat"] == "external"  # 外部循环不假装有 tick
        assert row["last_tick"] is None
        assert reg.counts()["disabled"] == 1

        await reg.shutdown()

    _run(scenario())


def test_start_reports_running_and_shutdown_stops_via_event():
    async def scenario():
        reg = SchedulerRegistry()
        stop = asyncio.Event()
        reg.add("worker", lambda: _forever(stop), stop=stop)

        await reg.start()
        row = reg.snapshot()[0]
        assert row["state"] == "running"
        assert row["uptime_seconds"] is not None

        await reg.shutdown()
        assert stop.is_set(), "有 stop 事件就必须走优雅停机，而不是直接 cancel"
        assert reg.snapshot()[0]["state"] == "stopped"

    _run(scenario())


def test_shutdown_cancels_tasks_without_stop_event():
    async def scenario():
        reg = SchedulerRegistry()
        reg.add("no-stop", lambda: asyncio.sleep(3600))

        await reg.start()
        assert reg.snapshot()[0]["state"] == "running"

        # 无 stop 事件 → 只能 cancel；grace 内必须收完（不得拖死关机）
        await reg.shutdown(grace=0.05)
        assert reg.snapshot()[0]["state"] == "stopped"

    _run(scenario())


def test_periodic_records_heartbeat_and_honours_returned_interval():
    async def scenario():
        reg = SchedulerRegistry()
        seen: list[int] = []

        async def tick():
            seen.append(1)
            return 0.01  # 覆盖本轮之后的间隔（声明值是 30s，不该被它改写）

        rec = reg.add_periodic("fast", tick, interval=30.0)
        assert rec.heartbeat is True
        assert rec.interval_seconds == 30.0

        await reg.start()
        await asyncio.sleep(0.2)
        await reg.shutdown()

        row = reg.snapshot()[0]
        assert row["heartbeat"] == "registry"
        # 若驱动忽略 tick 的返回值，0.2s 内只会有 1 拍
        assert row["tick_count"] >= 3, f"tick_count={row['tick_count']}"
        assert row["last_tick"] is not None
        assert row["interval_seconds"] == 30.0
        assert row["failures"] == 0

    _run(scenario())


def test_failed_tick_does_not_kill_the_loop():
    """单拍异常（上游抖动）不得终止调度——否则一次网络抖动就静默少了一个循环。"""

    async def scenario():
        reg = SchedulerRegistry()
        n = {"i": 0}

        async def tick():
            n["i"] += 1
            if n["i"] == 1:
                raise RuntimeError("boom-tick")
            return 0.01

        reg.add_periodic("flaky", tick, interval=0.01)
        await reg.start()
        await asyncio.sleep(0.2)
        await reg.shutdown()

        assert n["i"] >= 2, "第一拍抛异常后循环必须继续"
        assert reg.snapshot()[0]["state"] == "stopped"

    _run(scenario())


def test_exception_death_is_recorded_and_restart_is_scheduled():
    async def scenario():
        reg = SchedulerRegistry()

        async def boom() -> None:
            raise RuntimeError("scheduler-own-failure")

        reg.add("dead", boom)
        await reg.start()
        await asyncio.sleep(0.05)

        row = reg.snapshot()[0]
        assert row["failures"] == 1
        assert "scheduler-own-failure" in (row["last_error"] or "")
        assert row["state"] == "restarting"
        assert row["restarts"] == 1
        assert row["restart_in_seconds"] is not None
        # 哨兵出口：等待重启也算"不健康"，否则崩了还没重启的空窗查不到
        assert reg.dead_names() == ["dead"]
        assert reg.counts()["restarting"] == 1

        await reg.shutdown()
        # 关闭后不该再留一个"将在 Xs 后重启"的假状态
        assert reg.snapshot()[0]["state"] == "stopped"

    _run(scenario())


def test_restart_disabled_records_dead_without_rescheduling():
    async def scenario():
        reg = SchedulerRegistry()

        async def boom() -> None:
            raise RuntimeError("no-restart")

        reg.add("fragile", boom, restart=False)
        await reg.start()
        await asyncio.sleep(0.05)

        row = reg.snapshot()[0]
        assert row["state"] == "dead"
        assert row["restarts"] == 0
        assert reg.dead_names() == ["fragile"]
        await reg.shutdown()

    _run(scenario())


def test_snapshot_is_json_serializable():
    async def scenario():
        reg = SchedulerRegistry()
        reg.add("a", lambda: asyncio.sleep(3600))
        reg.add_periodic("b", lambda: asyncio.sleep(0), interval=1.0)
        await reg.start()
        await asyncio.sleep(0.01)
        payload = json.dumps({"schedulers": reg.snapshot(), "counts": reg.counts()})
        assert '"schedulers"' in payload
        await reg.shutdown(grace=0.05)

    _run(scenario())


# ------------------------------------------------------------------ lifespan 集成
def test_declared_switches_match_the_truth_source(client):
    """双向防漂移：① 传了未登记的开关 → add() 已 fail-fast；② 登记了没人用的开关。"""
    from app.main import app

    reg = app.state.schedulers
    assert reg.declared_switches == set(SCHEDULER_SWITCH_ATTRS)


def test_schedulers_endpoint_exposes_state(client):
    resp = client.get("/api/system/schedulers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    names = {s["name"] for s in body["schedulers"]}
    # 三态构造：常驻（无开关）/ 带开关 / 注册表驱动
    assert {"quote-poller", "alert-engine", "data-health-sentinel", "risk-refresher"} <= names
    assert body["counts"]["total"] == len(body["schedulers"])
    # 测试环境开关全关 → 必须至少有一个 disabled（证明派生表生效）
    assert body["counts"]["disabled"] >= 1
    assert body["dead"] == []
    for s in body["schedulers"]:
        assert s["state"] in {
            "disabled", "running", "restarting", "dead", "exited", "stopped",
        }


def test_session_interval_helper_uses_idle_outside_window(monkeypatch):
    """P2-9/P1-3 的口径入口：盘外必须给 idle 节奏（否则 5s/60s 空转继续）。"""
    from app.main import _session_interval
    from app.market import trade_calendar

    monkeypatch.setattr(trade_calendar, "in_trading_window", lambda *a, **k: True)
    assert _session_interval(5.0, 300.0) == 5.0
    monkeypatch.setattr(trade_calendar, "in_trading_window", lambda *a, **k: False)
    assert _session_interval(5.0, 300.0) == 300.0


def test_alert_engine_skips_tick_outside_trading_window(monkeypatch):
    """P2-9：盘外只空转不判读（不再用陈旧收盘价反复求值规则）。"""
    from app.market import trade_calendar
    from app.market.alert_engine import AlertEngine

    engine = AlertEngine(None, None, interval=5.0)  # type: ignore[arg-type]
    assert engine._idle_interval == 300.0

    ticks: list[int] = []

    async def fake_tick() -> None:
        ticks.append(1)

    monkeypatch.setattr(engine, "_tick", fake_tick)

    async def scenario(outside: bool) -> None:
        monkeypatch.setattr(trade_calendar, "in_trading_window", lambda *a, **k: not outside)
        ticks.clear()
        task = asyncio.create_task(engine.run())
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    _run(scenario(outside=True))
    assert ticks == [], "盘外不得判读"
    _run(scenario(outside=False))
    assert ticks, "盘中必须判读"
