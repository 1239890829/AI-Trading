"""常驻调度任务的注册表：一次声明、统一启动、统一收割、可观测（S2-2）。

**要解决的问题**（2026-09-11 复盘账本 §6.5）：`main.py` 的 lifespan 里有 23 处
`asyncio.create_task`（共 26 个常驻任务），而停机收割是**另一份手写清单**。两份清单
必须人工保持同步——漏启动侧只是任务没起，漏收割侧则是"任务起了但关不掉"，而
`TestClient.__exit__` 会等 lifespan 完全结束，于是全量 pytest 整场挂死（2026-09-03
两连复现）。更糟的是**可观测性**：除 evolution 外没有任何调度器暴露自己的存活状态，
"任务静默死亡"只能靠人偶然发现（历史已两次出事）。

**本模块提供**：
- `SchedulerRegistry`：`add()` / `add_periodic()` 声明，`start()` 拉起，`shutdown()` 收割；
- 每个任务记录 `state / last_tick / failures / last_error / restarts`；
- **死亡自愈**：任务异常退出时记 ERROR 并按指数退避重启（`restart=False` 可关）；
- `snapshot()` 直接喂给 `GET /api/system/schedulers`。

**heartbeat 的诚实边界**：只有经 `add_periodic()` 托管的循环才由注册表自动记
`last_tick`；在别处 `create_task` 的外部循环（`app/picks/**`、`app/services/**` 下的
`*_loop`）按名字登记后 `heartbeat` 字段显式标 `"external"`——**不假装有 tick 数据**。

**依赖约束**：本模块只依赖标准库，且**不得在模块级 import `app.core.config`**。
`tests/conftest.py` 需要在 `settings` 被实例化**之前**读取
`SCHEDULER_SWITCH_ENV_VARS`；若这里模块级 import settings，会把生产 `.env` 固化下来，
conftest 之后设的 `ASHARE_REVIEW_MODEL=rules` 等全部失效。settings 只在 `add()`
内部延迟取用（那时 lifespan 已在跑，settings 早已定型）。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

#: 停机时给单个任务的收尾宽限（秒）。超时 → 强制 cancel → 再等一轮。
SHUTDOWN_GRACE_SECONDS = 10.0

#: 重启退避：首次 30s 起翻倍，封顶 300s（避免"刚崩就重崩"把上游打爆）。
_RESTART_BASE_SECONDS = 30.0
_RESTART_MAX_SECONDS = 300.0


def switch_env_var(attr: str) -> str:
    """settings 属性名 → 环境变量名（`ASHARE_` 前缀来自 Settings 的 env_prefix）。"""
    return "ASHARE_" + attr.upper()


#: **唯一真相源**：带 `ASHARE_*_ENABLED` 开关的调度器（元素为 settings 属性名）。
#:
#: 两个消费者都必须与它对齐：
#:  1. `main.py` 的 `add(switch=...)`——传了不在表里的名字直接 `ValueError`（fail-fast）。
#:     漏登记的开关在测试环境不会被关掉，于是会**真的跑起生产调度**：2026-09-10 的
#:     `event_collector` 就是这样反复写库、拖垮三个完全无关的用例。
#:  2. `tests/conftest.py` 由它派生「测试环境全关」的 env 清单——新增调度器不必再手补一行。
#:
#: 注：以下常驻任务**无开关**（`switch=None`，始终启动），与改造前行为一致：
#: quote-poller / market-snapshot / alert-triage / evolution-agenda /
#: data-health-sentinel / pre-limit-radar / position-monitor / llm-aux-judge /
#: paper-matcher / alert-quotes-feeder / risk-refresher / alert-engine。
#: 其中 `llm-aux-judge` 的开关（`event_llm_aux_enabled`）在**循环内部**逐拍读取，
#: 属运行时可切；挪到启动期门控会改变"运行时能开关"的语义，故保持原样。
SCHEDULER_SWITCH_ATTRS: tuple[str, ...] = (
    "review_scheduler_enabled",
    "picks_autogen_enabled",
    "event_collector_enabled",
    "sentiment_history_backfill_enabled",
    "premarket_brief_enabled",
    "picks_watcher_enabled",
    "picks_buy_point_enabled",
    "picks_review_enabled",
    "ths_sentinel_enabled",
    "sentiment_monitor_enabled",
    "picks_shadow_enabled",
    "marketdb_sync_enabled",
    "flash_news_enabled",
    "llm_probe_enabled",
)

#: 环境变量名清单（conftest 直接 `os.environ[x] = "false"`）。
SCHEDULER_SWITCH_ENV_VARS: tuple[str, ...] = tuple(
    switch_env_var(a) for a in SCHEDULER_SWITCH_ATTRS
)


async def _wait_quit(task: asyncio.Task, timeout: float) -> bool:
    """等任务在 timeout 内结束。True=已结束（正常返回/自行抛错/被取消都算）。"""
    try:
        # shield：超时只取消"等待"本身，任务留给调用方决定 cancel 时机——
        # 避免与"任务早已被 cancel 过"的路径产生隐式取消语义纠缠
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return True
    except TimeoutError:
        return False
    except asyncio.CancelledError:
        return True
    except Exception:
        return True


async def _reap(task: asyncio.Task | None, *, name: str, grace: float = SHUTDOWN_GRACE_SECONDS) -> None:
    """停机收割：先给 grace 秒自然退出，超时转 cancel 再收割；异常一律吞掉。

    为什么不能裸 `await task`：cancel()/stop.set() 都打不断 in-flight 的
    await——调度器 tick 一旦卡在无超时边界的调用上，lifespan 关闭就被单个
    任务整体挂死。而 TestClient.__exit__ 的语义是"等 lifespan 完全结束"，
    全量 pytest 因此在首个用例（test_alerts，字母序最先）整场卡死
    （2026-09-03 两连复现，faulthandler 栈转储实证卡点 wait_shutdown；
    tests/conftest.py 同日已把测试环境调度器全关，这里是生产侧兜底：
    任何单任务不得拖死关机）。cancel 后仍杀不掉（sync 调用里僵死）就
    放弃等待——悬挂任务会在循环关闭时打 "Task was destroyed"，但不阻塞关机。
    """
    if task is None or task.done():
        return
    if not await _wait_quit(task, grace):
        log.warning("lifespan shutdown: %s 超过 %.0fs 未退出，强制 cancel", name, grace)
        task.cancel()
        if not await _wait_quit(task, grace):
            log.error("lifespan shutdown: %s cancel 后 %.0fs 仍未退出，放弃等待", name, grace)


async def wait_or_stop(stop: asyncio.Event | None, seconds: float) -> bool:
    """睡 `seconds` 秒，或被 `stop` 提前唤醒。返回 True = 收到停止信号（该退了）。

    比裸 `asyncio.sleep` 多一个好处：停机时调度器立刻醒，不必等满一个周期
    （收窄了"停机要等宽限超时"的窗口）。
    """
    if not seconds or seconds <= 0:
        return bool(stop is not None and stop.is_set())
    if stop is None:
        await asyncio.sleep(seconds)
        return False
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    return False


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def _coerce_interval(returned: Any, default: float) -> float:
    """tick 的返回值若是一个正数就当成本轮之后的间隔，否则用默认值。"""
    if isinstance(returned, (int, float)) and not isinstance(returned, bool) and returned > 0:
        return float(returned)
    return float(default)


@dataclass
class TaskRecord:
    """一个常驻任务的声明 + 运行状态。"""

    name: str
    factory: Callable[[], Awaitable[Any]]
    stop: asyncio.Event | None = None
    enabled: bool = True
    switch: str | None = None
    reason: str | None = None  # 未启动的原因（开关关 / 前置条件不满足）
    restart: bool = True
    heartbeat: bool = False  # True = 注册表驱动循环，last_tick 自动记
    interval_seconds: float | None = None
    task: asyncio.Task | None = None
    started_at: float | None = None
    last_tick: float | None = None
    tick_count: int = 0
    failures: int = 0
    restarts: int = 0
    last_error: str | None = None
    finished_reason: str | None = None  # exception / exited / stopped / disabled
    restart_at: float | None = None
    restart_handle: asyncio.TimerHandle | None = None

    @property
    def state(self) -> str:
        """disabled / running / restarting / dead / exited / stopped。"""
        if not self.enabled:
            return "disabled"
        if self.task is not None and not self.task.done():
            return "running"
        if self.restart_at is not None:
            return "restarting"
        if self.finished_reason == "exception":
            return "dead"
        if self.finished_reason == "exited":
            return "exited"
        return "stopped"


class SchedulerRegistry:
    """常驻任务的声明式登记 + 启动 + 观测 + 统一收割。"""

    def __init__(self) -> None:
        self._records: dict[str, TaskRecord] = {}
        self._closing = False
        self._declared_switches: set[str] = set()

    # ------------------------------------------------------------------ 声明
    def add(
        self,
        name: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        stop: asyncio.Event | None = None,
        switch: str | None = None,
        enabled: bool = True,
        reason: str | None = None,
        restart: bool = True,
    ) -> TaskRecord:
        """登记一个常驻任务（**不立即启动**，由 `start()` 统一拉起）。

        `switch`：本任务的 settings 属性名（如 `"picks_watcher_enabled"`）。给了就
        必须已在 `SCHEDULER_SWITCH_ATTRS` 登记，且开关为假时任务不启动。
        `enabled`：除开关之外的附加前置条件（如 marketdb 需要 ths key、
        shadow 需要 paper_shadow 已装配）；为假时请同时给 `reason` 说明原因。
        """
        if name in self._records:
            # 重名会让收割与观测按名字取错记录：宁可启动期直接失败
            raise ValueError(f"调度器重名：{name}")
        if switch is not None:
            if switch not in SCHEDULER_SWITCH_ATTRS:
                raise ValueError(
                    f"调度器 {name} 的开关 {switch} 未登记进 SCHEDULER_SWITCH_ATTRS ——"
                    "测试环境不会关掉它（会真的跑起生产调度）。请先把该开关名补进元组。"
                )
            self._declared_switches.add(switch)
            # 延迟导入：本模块被 conftest 提前 import 时不得固化 settings
            from app.core.config import settings

            if not bool(getattr(settings, switch, False)):
                enabled = False
                reason = reason or f"{switch_env_var(switch)} 未开启"

        rec = TaskRecord(
            name=name, factory=factory, stop=stop, enabled=enabled,
            switch=switch, reason=reason, restart=restart,
        )
        self._records[name] = rec
        return rec

    def add_periodic(
        self,
        name: str,
        tick: Callable[[], Awaitable[Any]],
        *,
        interval: float,
        stop: asyncio.Event | None = None,
        switch: str | None = None,
        enabled: bool = True,
        reason: str | None = None,
        restart: bool = True,
        first_delay: float = 0.0,
    ) -> TaskRecord:
        """登记一个**由注册表驱动**的常驻循环：每 `interval` 秒调一次 `tick()`。

        `tick` 返回正数可覆盖本轮之后的间隔（`None` = 沿用 `interval`）——给
        "盘中密、盘外疏"这类自适应节奏留口子。
        单拍异常由驱动吞掉并记日志（循环继续）；`last_tick` 每拍自动更新。
        """

        async def _driver() -> None:
            if first_delay:
                if await wait_or_stop(stop, first_delay):
                    return
            while True:
                if stop is not None and stop.is_set():
                    return
                nxt: Any = None
                try:
                    nxt = await tick()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001  单拍失败不终止循环
                    log.exception("调度器 %s 单拍失败（循环继续）", name)
                self.tick(name)  # 心跳：跑完一拍就算活着（失败也记）
                if await wait_or_stop(stop, _coerce_interval(nxt, interval)):
                    return

        rec = self.add(
            name, _driver, stop=stop, switch=switch, enabled=enabled,
            reason=reason, restart=restart,
        )
        rec.heartbeat = True
        rec.interval_seconds = float(interval)
        return rec

    # ------------------------------------------------------------------ 启动
    def _spawn(self, rec: TaskRecord) -> None:
        rec.task = asyncio.create_task(rec.factory(), name=rec.name)
        rec.started_at = time.time()
        rec.finished_reason = None
        rec.restart_at = None
        rec.restart_handle = None
        rec.task.add_done_callback(lambda _t, r=rec: self._on_done(r))

    async def start(self) -> None:
        """拉起所有 enabled 的任务。返回后 `snapshot()` 即可看到全部状态。"""
        for rec in self._records.values():
            if not rec.enabled:
                rec.finished_reason = "disabled"
                continue
            self._spawn(rec)
        idle = [r for r in self._records.values() if r.task is None]
        log.info(
            "调度注册表：启动 %d/%d 个常驻任务%s",
            len(self._records) - len(idle), len(self._records),
            "" if not idle else "；未启动=" + ",".join(r.name for r in idle),
        )
        for r in idle:
            log.info("调度器 %s 未启动：%s", r.name, r.reason or "开关关闭")

    def _on_done(self, rec: TaskRecord) -> None:
        task = rec.task
        rec.task = None
        if self._closing or (task is not None and task.cancelled()):
            rec.finished_reason = "stopped"
            return
        exc = task.exception() if task is not None else None
        if exc is None:
            rec.finished_reason = "exited"
            log.warning("调度器 %s 自行退出（未抛异常）——若为意外退出请查其退出条件", rec.name)
        else:
            rec.finished_reason = "exception"
            rec.failures += 1
            rec.last_error = f"{type(exc).__name__}: {exc}"
            log.error(
                "调度器 %s 异常死亡（累计 %d 次）：%s",
                rec.name, rec.failures, rec.last_error, exc_info=exc,
            )
        if not rec.restart or task is None:
            return
        delay = min(_RESTART_BASE_SECONDS * (2 ** min(rec.restarts, 4)), _RESTART_MAX_SECONDS)
        rec.restarts += 1
        rec.restart_at = time.time() + delay
        log.warning("调度器 %s 将在 %.0fs 后重启（累计第 %d 次）", rec.name, delay, rec.restarts)
        # 存句柄：停机时撤掉待执行的重启，避免留下"将在 Xs 后重启"的假状态
        rec.restart_handle = task.get_loop().call_later(delay, self._restart, rec)

    def _restart(self, rec: TaskRecord) -> None:
        if self._closing or not rec.enabled:
            return
        log.info("重启调度器 %s", rec.name)
        try:
            self._spawn(rec)
        except Exception:  # noqa: BLE001
            log.exception("调度器 %s 重启失败", rec.name)

    # ------------------------------------------------------------------ 观测
    def tick(self, name: str) -> None:
        """记一次心跳。`add_periodic` 的驱动会自动调；外部循环可自行调用。"""
        rec = self._records.get(name)
        if rec is None:
            return
        rec.last_tick = time.time()
        rec.tick_count += 1

    def snapshot(self) -> list[dict]:
        now = time.time()
        return [self._row(rec, now) for rec in self._records.values()]

    def _row(self, rec: TaskRecord, now: float) -> dict:
        return {
            "name": rec.name,
            "state": rec.state,
            "enabled": rec.enabled,
            "switch": rec.switch,
            "not_started_reason": None if rec.enabled else rec.reason,
            "heartbeat": "registry" if rec.heartbeat else "external",
            "interval_seconds": rec.interval_seconds,
            "started_at": _iso(rec.started_at),
            "uptime_seconds": (
                round(now - rec.started_at, 1)
                if rec.started_at is not None and rec.state == "running"
                else None
            ),
            "last_tick": _iso(rec.last_tick),
            "idle_seconds": round(now - rec.last_tick, 1) if rec.last_tick else None,
            "tick_count": rec.tick_count,
            "failures": rec.failures,
            "restarts": rec.restarts,
            "last_error": rec.last_error,
            "restart_in_seconds": (
                round(rec.restart_at - now, 1) if rec.restart_at is not None else None
            ),
        }

    def counts(self) -> dict:
        out: dict[str, int] = {
            "total": len(self._records), "running": 0, "disabled": 0,
            "restarting": 0, "dead": 0, "exited": 0, "stopped": 0,
        }
        for rec in self._records.values():
            out[rec.state] = out.get(rec.state, 0) + 1
        return out

    def dead_names(self) -> list[str]:
        """当前**不健康**的调度器名：异常退出且尚未恢复（等待重启 / 已放弃）。

        等待重启也计入：崩溃到重启之间是一个真实的服务空窗，而"崩了但还没重启"
        恰恰是最需要被人知道的时刻（2026-09-03 那类静默失效都发生在空窗里）。
        """
        return [r.name for r in self._records.values() if r.state in ("dead", "restarting")]

    @property
    def declared_switches(self) -> set[str]:
        """本轮 lifespan 实际用到的开关名（供漂移检测测试比对真相源）。"""
        return set(self._declared_switches)

    # ------------------------------------------------------------------ 收割
    async def shutdown(self, *, grace: float = SHUTDOWN_GRACE_SECONDS) -> None:
        """统一停机：先发停止信号（能自己退的别强杀），再逐个限时收割。"""
        self._closing = True
        pending: list[tuple[str, asyncio.Task]] = []
        for rec in self._records.values():
            if rec.restart_handle is not None:
                rec.restart_handle.cancel()
                rec.restart_handle = None
                rec.restart_at = None
            task = rec.task
            if task is None or task.done():
                continue
            pending.append((rec.name, task))
            if rec.stop is not None:
                rec.stop.set()
            else:
                task.cancel()
        for name, task in pending:
            await _reap(task, name=name, grace=grace)
        # 收尾后统一标 stopped：否则"崩溃在停机前的任务"会一直显示 dead，
        # 让人以为它还等着被救（failures/last_error/restarts 仍保留，证据不丢）
        for rec in self._records.values():
            if rec.enabled:
                rec.finished_reason = "stopped"
