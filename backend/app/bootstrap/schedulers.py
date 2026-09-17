"""应用启动装配（二）：**常驻调度任务声明**。

从 `app/main.py` 的 `lifespan` 中切出（`IMP-027` 装配重构，2026-09-15）。

**为什么单独成模块**：`lifespan` 原有约 550 行，其中近 400 行是"声明 25 个常驻循环"——
它长得像配置，混在服务构建里会让 `main.py` 读不出"进程里到底跑着什么"。切出后，
"有哪些服务"（`services.py`）与"跑着哪些循环"（本模块）各自可读。

**⚠️ 判据（重构可信的前提）**：函数体刻意与原文**逐字保持一致** —— 服务成员在函数首部
绑定为**同名局部别名**（`hub = services.hub` 等），从而使整段声明清单无需做任何
`hub` → `services.hub` 的引用改写。这样"行为不变"可被机械核验（比对原文即可），
而不是依赖人工审查 400 行替换是否漏改。

**⚠️ 顺序即语义**：`reg.add()` 的**调用顺序**会被 `GET /api/system/schedulers` 的
`snapshot()` 原样呈现，且 `SchedulerRegistry.declared_switches` 会与
`app/core/scheduler.py::SCHEDULER_SWITCH_ATTRS` 双向比对（漏/多都报红）。
一律保序，不重排、不合并。

**⚠️ 延迟导入保持延迟**：诸如 `from app.picks.shadow import shadow_loop` 原本写在函数内，
提到模块级会改变加载期依赖图，可能**引入新的 import 环**（用
`scripts/audit/import_cycles.py` 列静态候选，仍须实际导入核验）。一律维持函数内导入。

**⚠️ 开关是"声明期 fail-fast"**：`add(..., switch=...)` 传了未登记的开关名会立刻抛错
（`SCHEDULER_SWITCH_ATTRS` 是唯一真相源）。新增调度器时**必须**同步登记，否则测试
`test_declared_switches_match_the_truth_source` 会红——这是设计意图，不是障碍。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

from app.bootstrap.services import AppServices
from app.core.config import settings
from app.core.scheduler import SchedulerRegistry, wait_or_stop
# 与原 `main.py` 同为**模块级**导入（依赖关系不变，故不新增加载期环）。
from app.review.service import review_scheduler

log = logging.getLogger(__name__)

#: 冷启动时 `market-snapshot` 首轮延迟秒数（`OPS-001`，2026-09-14）。
#:
#: `reg.start()` 会**同时**拉起全部常驻任务，其首批 tick 与紧随的 `hub.refresh()`
#: （`main.py` 的 lifespan）叠加，把新浪的「时间窗累计请求数」配额一次性打满 ⇒
#: `market-snapshot` 的**首个请求**即返回 456（事故原文见 `docs/kb/03-engineering.md`
#: KB-ENG-83；实测封禁窗口约 7.4 分钟）。
#:
#: 幅度依据（**非直觉**）：本任务单独跑已被长期验证安全（休市 240s / 盘中 60s 一轮
#: 57 个请求，多轮稳定成功），故只需推后到突发窗口之后。取值 60s 与**盘中轮询间隔**
#: 同量级，且与仓内既有错峰先例一致（`event-collector` 的 `first_delay=45.0`）。
#: 该项首次冷启动实测的观测数据见账本 §6.7-C3。
COLD_START_SNAPSHOT_DELAY_SECONDS = 60.0


def _session_interval(active: float, idle: float) -> float:
    """盘中用 `active`、盘外用 `idle`——给"盘外不必 5s 空转"的调度器用（P2-9/P1-3）。"""
    from app.market.trade_calendar import in_trading_window

    return active if in_trading_window() else idle


async def _metric_history_backfiller(hub, stop: asyncio.Event | None = None):
    """情绪历史指标库的**增量**维护（P0-3b 分位校准的数据底座）。

    没有这个任务，库会停在首次手工回补的那天：半年后界面仍写着"按近 120
    个交易日分位校准"，实际窗口早已漂移到半年前——这正是"数字看着合理、
    结论其实是错的"那类静默失效。故必须自动跑；回补是增量的（已在库的
    日期不重拉），稳态下每轮只拉 1–2 天 × 2 个请求，配额开销可忽略。

    启动延迟 90s：让冷启动的行情/快照先填完，不和其他网络请求抢配额。
    """
    if await wait_or_stop(stop, 90):
        return
    while True:
        try:
            from app.market import trade_calendar as tc
            from app.sentiment import metric_history

            # 日历一律走 trade_calendar（**唯一归一化入口**：返回 date 列表，
            # 官方日历失败回落日 K 推导）。**不要直取 provider 原始日历**——
            # 那是字符串，与 backfill 的 date 比较类型不符 → TypeError →
            # 异常被下面的 except 收成一条日志，库静默陈旧而界面照旧宣称
            # 「按近 N 个交易日校准」（2026-09-10 实测：库停在 09-01，
            # 连续 6 个交易日没更新）。
            try:
                days = await tc.trading_days(hub.provider)
            except Exception as exc:
                log.warning("metric history backfill skipped: 交易日历不可用（%s）", exc)
                days = []
            if not days:
                log.warning("metric history backfill skipped: 交易日历为空")
            else:
                stats = await metric_history.backfill(
                    hub.provider,
                    days,
                    lookback=settings.sentiment_history_lookback,
                )
                if stats["added"] or stats["suspicious"]:
                    log.info(
                        "metric history backfill: +%s 天（跳过 %s / 共 %s 天）",
                        stats["added"], stats["skipped"], stats["total"],
                    )
                else:
                    # 心跳：稳态（无新增）时也留一行，让"回补到底有没有在跑"
                    # 可以从日志直接回答。此前只在 added>0 时打日志，
                    # 而失败又是另一条 ERROR——两条都没有时无法区分
                    # "跑得很健康" 与 "根本没跑"（2026-09-10 的 6 个交易日
                    # 静默陈旧正是栽在这个盲区上）。
                    log.info(
                        "metric history backfill: 无新增（窗口尾 %s / 共 %s 天）",
                        (stats["days"] or [None])[-1], stats["total"],
                    )
                if stats["suspicious"]:
                    # 数据源日期回退（东财 push2ex 的前科）会污染整个分布，
                    # 剔除后宁可少样本。非 0 属异常，必须留痕。
                    log.warning(
                        "metric history: %s 天涨停池与前一日完全相同，疑似数据源日期回退，已剔除",
                        stats["suspicious"],
                    )
        except Exception:
            log.exception("metric history backfill failed")
        if await wait_or_stop(stop, settings.sentiment_history_backfill_interval_seconds):
            return


def register_schedulers(reg: SchedulerRegistry, app: FastAPI, services: AppServices) -> None:
    """把全部常驻循环声明进注册表（只声明，不启动）。

    ⚠️ **不改写函数体内的引用**：下面这些同名局部别名正是原 `lifespan` 里的变量名，
    绑定后函数体可与原文逐字比对（见模块 docstring）。
    """
    hub = services.hub
    alert_engine = services.alert_engine
    risk_engine = services.risk_engine
    paper = services.paper
    snapshot_service = services.snapshot_service
    review_svc = services.review

    triage_stop = asyncio.Event()
    from app.services.alert_triage import triage_loop

    reg.add("alert-triage", lambda: triage_loop(triage_stop), stop=triage_stop)

    # --- AI 大脑：每日进化议程（docs/summary/ai-evolution.md，交易日 15:45）---
    # 无条件挂载：autonomy 关闭时议程照常生成（仅不执行，降级为建议清单）
    evolution_stop = asyncio.Event()
    from app.services.evolution import evolution_scheduler

    reg.add(
        "evolution-agenda",
        lambda: evolution_scheduler(
            app,
            stop=evolution_stop,
            run_hour=settings.agent_evolution_hour,
            run_minute=settings.agent_evolution_minute,
            check_interval_seconds=settings.review_check_interval_seconds,
        ),
        stop=evolution_stop,
    )

    review_stop = asyncio.Event()
    reg.add(
        "review-scheduler",
        lambda: review_scheduler(
            review_svc,
            run_hour=settings.review_run_hour,
            run_minute=settings.review_run_minute,
            check_interval_seconds=settings.review_check_interval_seconds,
            stop=review_stop,
        ),
        stop=review_stop,
        switch="review_scheduler_enabled",
    )

    reg.add("quote-poller", hub.run)
    # 首轮错峰（`OPS-001`）：避免与上述任务的首批 tick / 后文 `hub.refresh()` 叠加
    # 打满上游配额 —— 判据与幅度依据见 `COLD_START_SNAPSHOT_DELAY_SECONDS` 注释。
    reg.add(
        "market-snapshot",
        lambda: snapshot_service.run(first_delay=COLD_START_SNAPSHOT_DELAY_SECONDS),
    )

    # 数据健康哨兵盘中循环（push_policy ② ANOMALY：交易时段 15 分钟一轮，
    # 新异常推飞书摘要卡——2026-09-08 推送矩阵）
    # 每日组合自动生成（09:26，幂等 by DailyPickSet——旧 09:26 automation 停用后的生成接盘者）
    picks_autogen_stop = asyncio.Event()
    from app.picks.picks_autogen import picks_autogen_scheduler

    reg.add(
        "picks-autogen",
        lambda: picks_autogen_scheduler(
            app, stop=picks_autogen_stop,
            run_hour=settings.picks_autogen_hour,
            run_minute=settings.picks_autogen_minute,
        ),
        stop=picks_autogen_stop,
        switch="picks_autogen_enabled",
    )

    data_health_stop = asyncio.Event()
    from app.services.data_health_loop import data_health_loop

    reg.add(
        "data-health-sentinel",
        lambda: data_health_loop(app, stop=data_health_stop),
        stop=data_health_stop,
    )

    # 临板雷达（KB-DEC-011）：涨停前识别与提醒，交易时段 6s 一轮，封板后不入册
    radar_stop = asyncio.Event()
    from app.picks.pre_limit_radar import pre_limit_loop

    reg.add("pre-limit-radar", lambda: pre_limit_loop(app, stop=radar_stop), stop=radar_stop)

    # pending 事件 LLM 辅助判定（P2-3 层1）：交易时段低频攒批，默认关
    # 注：开关在**循环内部**逐拍读取（运行时可切），故不在此处做启动期门控。
    llm_aux_stop = asyncio.Event()
    from app.events.llm_aux import llm_aux_loop

    reg.add("llm-aux-judge", lambda: llm_aux_loop(app, stop=llm_aux_stop), stop=llm_aux_stop)

    # 持仓监护（闭环「离场」段）：止损/移动止盈/弱转强识别，交易时段 15s 一轮
    position_stop = asyncio.Event()
    from app.picks.exit_engine import position_loop

    reg.add("position-monitor", lambda: position_loop(app, stop=position_stop), stop=position_stop)

    # 技术债 #5：无挂单时空转降频（30s 查一次挂单表），有挂单才 5s 密集轮询
    async def _paper_match_tick() -> float:
        pending = await paper.match_pending()
        return 5.0 if pending > 0 else 30.0

    reg.add_periodic("paper-matcher", _paper_match_tick, interval=30.0)

    async def _alert_quotes_tick() -> float:
        alert_engine.update_quotes({s: q.model_dump() for s, q in hub.quotes.items()})
        # P2-9：盘外行情不再变化，喂给引擎无意义 —— 降到 5 分钟一次
        return _session_interval(settings.alert_poll_interval_seconds, 300.0)

    reg.add_periodic(
        "alert-quotes-feeder", _alert_quotes_tick,
        interval=settings.alert_poll_interval_seconds,
    )
    reg.add("alert-engine", alert_engine.run)

    async def _risk_tick() -> float:
        # P1-3：盘外 30 分钟刷一次即可（此前恒定 60s，与情绪计算一起空转回源）
        await risk_engine.refresh()
        return _session_interval(60.0, 1800.0)

    reg.add_periodic("risk-refresher", _risk_tick, interval=60.0)

    async def _event_collect_tick() -> None:
        """事件采集调度（P1）：自选新闻 → 事件卡，指纹去重保证幂等。

        此前事件只有手工/半自动录入，活跃事件长期个位数，选股消息面近乎
        空转（2026-08-31 盘点）。30 分钟一轮：新闻源本身更新频率低，
        去重后重复采集只产生 duplicated 计数，无害。

        非盘中轮次（≥15:05 或 <09:15）附带把最近交易日涨停股纳入采集范围
        （R6，2026-09-01）：盘中轮次范围保持 自选∪组合∪持仓，控上游配额。
        """
        from datetime import datetime as _dt, time as _time

        from app.api.routes.events import collect_news_events

        now = _dt.now().time()
        after_hours = now >= _time(15, 5) or now < _time(9, 15)
        stats = await collect_news_events(app.state, include_limit_up=after_hours)
        if stats.get("created"):
            log.info("event collector: +%s 新事件（duplicated %s）", stats["created"], stats["duplicated"])

    # 停机开关（2026-09-10 补）：此前它是唯一无开关的调度器，测试里会真的跑起来
    # 打网络并重复写 event_direction（撞 UNIQUE 约束）。测试一律关（见 conftest）。
    reg.add_periodic(
        "event-collector", _event_collect_tick,
        interval=1800.0, first_delay=45.0,  # 启动先让目录同步/行情填充完成
        switch="event_collector_enabled",
    )

    metric_stop = asyncio.Event()
    reg.add(
        "metric-history-backfill",
        lambda: _metric_history_backfiller(hub, stop=metric_stop),
        stop=metric_stop,
        switch="sentiment_history_backfill_enabled",
    )

    # --- 盘前简报 + 盘中跟踪（选股 2.0 批次 B）---
    # 环境缓存放 state：手动单拍端点与 watcher_loop 共用同一份（避免各自重算情绪）
    app.state.picks_env_cache = {"at": 0.0, "env": None}

    premarket_stop = asyncio.Event()
    from app.picks.morning_brief import premarket_scheduler

    reg.add(
        "premarket-brief",
        lambda: premarket_scheduler(
            app,
            stop=premarket_stop,
            run_hour=settings.premarket_brief_hour,
            run_minute=settings.premarket_brief_minute,
            check_interval_seconds=settings.review_check_interval_seconds,
        ),
        stop=premarket_stop,
        switch="premarket_brief_enabled",
    )

    watcher_stop = asyncio.Event()
    from app.picks.watcher import watcher_loop

    reg.add(
        "picks-watcher",
        lambda: watcher_loop(app, stop=watcher_stop),
        stop=watcher_stop,
        switch="picks_watcher_enabled",
    )

    # --- 盘中买点推送（2026-09-08 用户定稿：唯一保留的盘中飞书推送）---
    buy_point_stop = asyncio.Event()
    from app.picks.buy_point import buy_point_loop

    reg.add(
        "picks-buy-point",
        lambda: buy_point_loop(app, stop=buy_point_stop),
        stop=buy_point_stop,
        switch="picks_buy_point_enabled",
    )

    # --- 板块异动检测器（2026-09-13 第一期：自主发现 + 归因 + 强度序列落库）---
    # 与 watcher 并行不混算：watcher 跟盘前登记方向，本任务补「盘前没人登记、
    # 盘中自己冒出来」的题材（09-11 MLCC/PCB 案例）。阈值未经实证，告警走
    # in_app+triage（推送矩阵未改动）。
    board_surge_stop = asyncio.Event()
    from app.picks.board_surge import board_surge_loop

    reg.add(
        "board-surge",
        lambda: board_surge_loop(app, stop=board_surge_stop),
        stop=board_surge_stop,
        switch="board_surge_enabled",
    )

    # --- 龙虎榜当日归档（P2-37 二期 E4）：盘后 17:05 起 data/lhb/<date>.json ---
    lhb_stop = asyncio.Event()
    from app.market.lhb_archive import lhb_archive_loop

    reg.add(
        "lhb-archive",
        lambda: lhb_archive_loop(app, stop=lhb_stop),
        stop=lhb_stop,
        switch="lhb_archive_enabled",
    )

    # --- 盘后方向对照（选股 2.0 批次 C）：15:35 对照当日简报 + 提醒收益回填 ---
    review_intraday_stop = asyncio.Event()
    from app.picks.review_intraday import intraday_review_scheduler

    reg.add(
        "picks-intraday-review",
        lambda: intraday_review_scheduler(
            app,
            stop=review_intraday_stop,
            run_hour=settings.picks_review_hour,
            run_minute=settings.picks_review_minute,
            check_interval_seconds=settings.review_check_interval_seconds,
        ),
        stop=review_intraday_stop,
        switch="picks_review_enabled",
    )

    # --- ths 涨停原因单点哨兵（P0-B）：交易时段探测 reason 非空率，缺原因即告警 ---
    ths_sentinel_stop = asyncio.Event()
    from app.services.ths_sentinel import sentinel_loop

    reg.add(
        "ths-reason-sentinel",
        lambda: sentinel_loop(app, stop=ths_sentinel_stop),
        stop=ths_sentinel_stop,
        switch="ths_sentinel_enabled",
    )

    # --- 盘中情绪监控（sentiment P2 #14）：高度板炸板/炸板率破位/指数急杀 → 告警 ---
    sentiment_monitor_stop = asyncio.Event()
    from app.sentiment.intraday_monitor import sentiment_monitor_loop

    reg.add(
        "sentiment-monitor",
        lambda: sentiment_monitor_loop(app, stop=sentiment_monitor_stop),
        stop=sentiment_monitor_stop,
        switch="sentiment_monitor_enabled",
    )

    # --- 影子持仓晨窗（P0-B）：09:26 竞价后按执行闸门模拟执行最新组合 ---
    shadow_stop = asyncio.Event()
    from app.picks.shadow import shadow_loop

    reg.add(
        "picks-shadow",
        lambda: shadow_loop(app, stop=shadow_stop),
        stop=shadow_stop,
        switch="picks_shadow_enabled",
        # state 上可能压根没有这个键（开关关时上面不赋值）——用 getattr 而非属性直取
        enabled=getattr(app.state, "paper_shadow", None) is not None,
        reason="picks_shadow_enabled 但 paper_shadow 未装配",
    )

    # --- marketdb 盘后增量同步（RPS/tech_score 数据地基；子进程隔离 + 磁盘幂等）---
    marketdb_stop = asyncio.Event()
    from app.market.marketdb_sync import marketdb_sync_scheduler

    reg.add(
        "marketdb-sync",
        lambda: marketdb_sync_scheduler(
            stop=marketdb_stop,
            run_hour=settings.marketdb_sync_hour,
            run_minute=settings.marketdb_sync_minute,
            check_interval_seconds=settings.marketdb_sync_check_interval_seconds,
        ),
        stop=marketdb_stop,
        switch="marketdb_sync_enabled",
        enabled=bool(settings.ths_api_key),
        reason="marketdb_sync_enabled 但 ths_api_key 缺失",
    )

    # --- 因子 IC 月度复核（S2-11：闭环——评估产物不再只写不读、不再停在 09-07）---
    factor_eval_stop = asyncio.Event()
    from app.factors.evaluate import factor_eval_scheduler

    reg.add(
        "factor-eval",
        lambda: factor_eval_scheduler(
            stop=factor_eval_stop,
            run_day=settings.factor_eval_run_day,
            run_hour=settings.factor_eval_hour,
            run_minute=settings.factor_eval_minute,
            check_interval_seconds=settings.factor_eval_check_interval_seconds,
        ),
        stop=factor_eval_stop,
        switch="factor_eval_enabled",
    )

    # --- 东财 7x24 快讯流（hotspot-pipeline G1/P0①）：宏观快讯 → build_event → EventStore ---
    flash_stop = asyncio.Event()
    from app.news.flash import flash_news_loop

    reg.add(
        "news-flash",
        lambda: flash_news_loop(app, stop=flash_stop),
        stop=flash_stop,
        switch="flash_news_enabled",
    )

    # --- LLM 网关健康探针（2026-09-06）：区分额度不足/网关失败，避免静默降级 ---
    llm_probe_stop = asyncio.Event()
    from app.services.llm_probe import probe_loop

    reg.add(
        "llm-gateway-probe",
        lambda: probe_loop(app, stop=llm_probe_stop),
        stop=llm_probe_stop,
        switch="llm_probe_enabled",
        enabled=settings.llm_probe_interval_seconds > 0,
        reason="llm_probe_interval_seconds <= 0",
    )
