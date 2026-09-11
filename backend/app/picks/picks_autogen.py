"""每日组合自动生成调度（2026-09-09 用户指令「不是自动生成的吗」）。

背景：旧 09:26 WorkBuddy automation 同时承担「生成组合 + 飞书推送」，09-09 推送
纪律收敛（KB-DEC-001：每日仅一条总结报告）时被一并暂停——**生成断供**，猎场
横幅退回显示昨日组合。本模块把「生成」收回后端（确定性代码自带时钟），推送
纪律不受影响。

设计（对齐 premarket_scheduler 模式）：
- 交易日 (run_hour, run_minute)=09:26 后、14:00 前有效——盘后/午间重启可补跑
  当日（覆盖 2026-09-09 12:30 重启场景）；14:00 后不补（临近收盘重算意义有限）
- 幂等 = 数据库：DailyPickSet 当日行已存在则跳过——**重启安全，且不覆盖手动
  「生成/刷新组合」的结果**（手动与自动写同一张表，先到先得）
- 失败下一 tick（60s）自动重试；交易日历判定失败按非交易日跳过（与盘前简报
  同一保守口径），日历恢复后自然补跑
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from sqlalchemy import select

from app.core.db import get_session_factory
from app.market.trading_status import beijing_now
from app.picks.morning_brief import _is_trading_day

log = logging.getLogger(__name__)

#: 补跑截止（"HH:MM"）——此后不再触发当日生成
CATCHUP_DEADLINE = "14:00"


async def _wait_snapshot_ready(app, *, timeout: float = 120.0, interval: float = 5.0) -> bool:
    """等全市场快照就绪（breadth 非空）再生成。返回是否等到。

    2026-09-09 事故：backend 重启后内存快照为空（KB-TRADE-03），补跑若立刻跑，
    compute_market_sentiment 抛 CalendarUnavailable → market_phase=None 落库并
    定格全天（评分吃不到风格偏移，前端显示「相位缺失·未路由」）。补跑前等一等
    比重跑整条管线便宜得多；超时则按现状生成并留 warning（不无限阻塞调度）。
    """
    svc = getattr(getattr(app, "state", None), "snapshot_service", None)
    if svc is None:
        return False
    waited = 0.0
    while waited < timeout:
        if getattr(svc, "breadth", None) is not None:
            if waited > 0:
                log.info("picks autogen: 快照已就绪（等待 %.0fs）", waited)
            return True
        await asyncio.sleep(interval)
        waited += interval
    log.warning("picks autogen: 等待 %.0fs 快照仍未就绪，按现状生成（情绪相位可能缺失）", timeout)
    return False


async def _today_row_exists(today: str) -> bool:
    from app.models.daily_pick import DailyPickSet

    with get_session_factory()() as db:
        row = db.execute(
            select(DailyPickSet.date).where(DailyPickSet.date == today)
        ).scalar_one_or_none()
    return row is not None


async def picks_autogen_tick(app, *, now, run_hour: int, run_minute: int) -> bool:
    """调度单步：窗口内 + 交易日 + 当日无组合 → 生成一次。返回是否触发生成。

    生成调用 **服务层管线** `services/picks_pipeline.generate_picks_pipeline`
    （S2-4，2026-09-11）。此前是反向 `import app.api.routes.picks.generate_picks`
    并伪造 `SimpleNamespace(app=app)` 当 request——那是"业务逻辑住在 route"的直接
    后果，也是集成链路零测试覆盖的成因；现在依赖以 `PipelineDeps` 显式声明。

    日期统一取 now（北京）并**传给管线**：此前窗口判定用 now、写入用
    `date.today()`，两处日期源不同源（KB-TRADE-02）。写鉴权参数不再需要
    （进程内调用本就不走 HTTP）。
    """
    today = now.date().isoformat()
    if now.weekday() >= 5:
        return False
    if not ((now.hour, now.minute) >= (run_hour, run_minute) and now.strftime("%H:%M") < CATCHUP_DEADLINE):
        return False
    if await _today_row_exists(today):
        return False

    hub = getattr(app.state, "hub", None)
    if hub is None:
        log.warning("picks autogen: hub 未就绪，本 tick 跳过")
        return False

    # 交易日历判定（与盘前简报同一保守口径：日历失败=跳过，恢复后自然补跑）
    if not await _is_trading_day(hub, now.date()):
        log.info("picks autogen: %s 非交易日，跳过", now.date())
        return False

    from app.services.picks_pipeline import PipelineDeps, generate_picks_pipeline

    await _wait_snapshot_ready(app)
    log.info("picks autogen: 当日组合缺失，开始自动生成（%s）", now.strftime("%H:%M:%S"))
    await generate_picks_pipeline(PipelineDeps.from_state(app.state), hub, today=today)
    log.info("picks autogen: 当日组合生成完成")
    return True


async def picks_autogen_scheduler(
    app,
    *,
    stop: asyncio.Event,
    run_hour: int = 9,
    run_minute: int = 26,
    check_interval_seconds: float = 60.0,
) -> None:
    """常驻调度（lifespan 任务）：窗口内每 tick 检查，缺失即生成。"""
    log.info("picks autogen scheduler started (daily %02d:%02d–%s, idempotent by DailyPickSet)",
             run_hour, run_minute, CATCHUP_DEADLINE)
    running = False
    while not stop.is_set():
        if not running:
            try:
                now = beijing_now()
                if (now.hour, now.minute) >= (run_hour, run_minute) and now.strftime("%H:%M") < CATCHUP_DEADLINE:
                    if not await _today_row_exists(now.date().isoformat()):
                        running = True
                        try:
                            await picks_autogen_tick(app, now=now, run_hour=run_hour, run_minute=run_minute)
                        finally:
                            running = False
            except Exception:  # noqa: BLE001  单轮失败不终止调度
                log.exception("picks autogen scheduler failed")
                running = False
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=check_interval_seconds)
