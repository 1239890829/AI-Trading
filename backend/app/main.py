from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import agent as agent_route
from app.api.routes import backtest as backtest_route
from app.api.routes import health as health_route
from app.api.routes import market as market_route
from app.api.routes import news as news_route
from app.api.routes import paper as paper_route
from app.api.routes import review as review_route
from app.api.routes import watchlist as watchlist_route
from app.api.routes import alert as alert_route
from app.api.routes import risk as risk_route
from app.api.routes import theme_catalog as theme_catalog_route
from app.api.routes import events as events_route
from app.api.routes import picks as picks_route
from app.api.routes import picks_intraday as picks_intraday_route
from app.api.routes import real_position as real_position_route
from app.api.routes import assistant as assistant_route
from app.api.routes import ext_data as ext_data_route
from app.api.routes import notifications as notifications_route
from app.core.config import settings
from app.core.db import get_engine, get_session_factory
from app.core.scheduler import SHUTDOWN_GRACE_SECONDS, SchedulerRegistry, wait_or_stop
from app.data_providers import build_provider
from app.events.store import EventStore
from app.market.alert_engine import AlertEngine
from app.models.alert import AlertEvent, AlertRule
from app.models.event import EventCard, EventDirection
from app.models.paper import PaperAccount, PaperOrder, PaperPosition
from app.models.theme_catalog import Theme, ThemeMember, ThemeOverride
from app.risk.engine import RiskEngine
from app.predict.models import (  # noqa: F401  注册预判两张表
    PredictionReportRow,
    PredictionThemeRow,
)
from app.repositories.watchlist_repo import WatchlistRepository
from app.repositories.alert_repo import AlertRepository
from app.paper.engine import PaperTradingEngine
from app.review.models import (  # noqa: F401  注册复盘三张表
    ReviewActionItemRow,
    ReviewMetaInsightRow,
    ReviewReportRow,
)
from app.review.service import ReviewService, review_scheduler
from app.services.snapshot_service import MarketSnapshotService
from app.services.theme_catalog_service import ThemeCatalogService
from app.services.quote_hub import QuoteHub
from app.market.sentiment_history import SentimentHistoryRow  # noqa: F401  注册情绪序列表
from app.models.watch_ledger import WatchLedger  # noqa: F401  注册盘中跟踪台账表（猎场批次 A）
from app.models.notification import NotificationReadState  # noqa: F401  注册通知已读状态表（2026-09-12）
from app.websocket.routes import router as ws_router

# 显式持有引用：确保各模块的表注册进 Base.metadata，否则 create_all 不会建表
_REGISTERED_MODELS = (
    PaperAccount, PaperOrder, PaperPosition,
    ReviewReportRow, ReviewActionItemRow, ReviewMetaInsightRow,
    PredictionReportRow, PredictionThemeRow,
    SentimentHistoryRow,
    AlertRule, AlertEvent,
    Theme, ThemeMember, ThemeOverride,
    EventCard, EventDirection,
    WatchLedger,
    NotificationReadState,
)

logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


def _session_interval(active: float, idle: float) -> float:
    """盘中用 `active`、盘外用 `idle`——给"盘外不必 5s 空转"的调度器用（P2-9/P1-3）。"""
    from app.market.trade_calendar import in_trading_window

    return active if in_trading_window() else idle


@asynccontextmanager
async def lifespan(app: FastAPI):
    # schema 唯一归 alembic 管（B5）：三态 stamp-or-upgrade 替代裸 create_all
    from app.core.migrations import run_migrations

    migration_action = run_migrations(get_engine())
    log.info("database migration: %s", migration_action)
    repo = WatchlistRepository(get_session_factory())
    repo.ensure_seeded(settings.watchlist_symbols)

    provider = build_provider(settings)
    hub = QuoteHub(
        provider=provider,
        poll_interval=settings.poll_interval_seconds,
        get_watchlist=repo.list_symbols,
        stale_after=settings.stale_after_seconds,
    )
    app.state.hub = hub
    app.state.watchlist_repo = repo

    # --- 预警引擎（Phase 8）：规则轮询 + 通知通道抽象 ---
    alert_repo = AlertRepository(get_session_factory())
    alert_engine = AlertEngine(alert_repo, repo, interval=settings.alert_poll_interval_seconds)
    app.state.alert_repo = alert_repo
    app.state.alert_engine = alert_engine

    async def live_quote(symbol: str):
        try:
            from app.data_quality.validator import validate_quote
            from app.services.quote_enrich import fill_limit_prices

            q = await provider.get_quote(symbol)
            if q is None:
                return None
            # ths 快照无涨跌停价：从腾讯源补齐（撮合的涨跌停校验依赖它）。
            # 补全逻辑只有一份（quote_enrich），REST 单只行情端点共用，避免两边口径漂移。
            q = await fill_limit_prices(provider, q)
            return validate_quote(q)
        except Exception:
            return None

    async def hub_trading_days():
        providers = [hub.provider] + (hub.provider.providers if hasattr(hub.provider, "providers") else [])
        for prov in providers:
            if hasattr(prov, "get_trading_days"):
                try:
                    return await prov.get_trading_days()
                except Exception:
                    continue
        return None

    paper = PaperTradingEngine(get_session_factory(), live_quote, hub_trading_days)
    app.state.paper = paper

    # --- 影子持仓（picks-intraday-fusion-assessment P0-B）：scope=shadow 独立账户 ---
    # 每日精选的 A/B 对照组：晨窗把最新组合按执行闸门模拟执行，验证空仓闸门
    # 机会成本与执行闸门价值。与 main 账户数据完全隔离（scope 列）。
    paper_shadow = None
    if settings.picks_shadow_enabled:
        paper_shadow = PaperTradingEngine(
            get_session_factory(), live_quote, hub_trading_days, scope="shadow",
        )
        from app.picks.shadow import ShadowRunner

        app.state.paper_shadow = ShadowRunner(paper_shadow, get_session_factory())

    snapshot_service = MarketSnapshotService(
        poll_interval=settings.snapshot_poll_interval_seconds,
        save_interval=settings.snapshot_save_interval_seconds,
        parquet_dir=Path(settings.parquet_dir),
    )
    app.state.snapshot_service = snapshot_service

    # --- 全市场选股器（Phase 5）：快照截面过滤 + TDX 日K 技术评分卡 ---

    # --- 风险引擎（Phase 5）：市场状态 + 仓位参数 + 订单预检 ---
    risk_engine = RiskEngine(
        hub=hub,
        snapshot_service=snapshot_service,
        session_factory=get_session_factory(),
        app_state=app.state,  # P1-3：情绪判定并入全站共享 60s 槽
    )
    app.state.risk_engine = risk_engine

    # --- 题材字典/官方成分（architecture-design §1 T1）：fuyao 官方目录与成分同步 ---
    try:
        theme_catalog = ThemeCatalogService(get_session_factory())
    except RuntimeError as exc:
        # 未配置 ths key 时降级为 None：题材端点返回 503，其余功能不受影响
        log.warning("theme catalog disabled: %s", exc)
        theme_catalog = None
    app.state.theme_catalog = theme_catalog

    # --- 事件驱动（architecture-design §1 E1）：EventCard 存储/查询 ---
    app.state.event_store = EventStore(get_session_factory())

    # --- 盘后复盘 Agent：服务实例 + 收盘后调度 ---
    review_svc = ReviewService(
        hub=hub,
        snapshot_service=snapshot_service,
        session_factory=get_session_factory(),
        model=settings.review_model,
        llm_base_url=settings.review_llm_base_url,
        llm_api_key=settings.review_llm_api_key,
        llm_model=settings.review_llm_model,
        llm_provider=settings.llm_provider,
        llm_cli_path=settings.llm_cli_path,
        methodology_version=settings.review_methodology_version,
        state=app.state,
    )
    app.state.review = review_svc

    # AI 控制台运行时（任务执行器需要 state 上的服务）+ 启动对账：
    # 残留 running/queued 任务标为 failed（进程重启=任务已中断，不假装还在跑）
    from app.services.agent_tasks import init_agent_runtime, reconcile_on_startup

    init_agent_runtime(app)
    with contextlib.suppress(Exception):
        interrupted = reconcile_on_startup()
        if interrupted:
            log.warning("agent tasks interrupted by restart: %d 条已标 failed", interrupted)
    # 参数覆盖层恢复（重启后覆盖仍生效——覆盖表是持久层，provider 是进程内注册）
    with contextlib.suppress(Exception):
        from app.services.agent_params import refresh_runtime_overrides

        refresh_runtime_overrides()

    # 告警 AI 判读 worker（P1）：规则触发 → AI 判断是否值得提醒 → 悬浮球
    from app.services.alert_triage import set_app_state as _triage_set_app

    _triage_set_app(app)  # P1-5 响应建议需要 state（题材目录/快照）

    # --- 常驻调度注册表（S2-2）：声明 → 启动 → 收割全走**一份**清单 ---
    # 此前是 23 处 create_task 与一份手写停机清单并存，两边都要人工同步；
    # 现在 add() 一次声明，shutdown() 统一收割，并发死亡自愈 + 状态可查
    # （GET /api/system/schedulers）。详见 app/core/scheduler.py 模块 docstring。
    reg = SchedulerRegistry()
    app.state.schedulers = reg

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
    reg.add("market-snapshot", snapshot_service.run)

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

    async def metric_history_backfiller(stop: asyncio.Event | None = None):
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

    metric_stop = asyncio.Event()
    reg.add(
        "metric-history-backfill",
        lambda: metric_history_backfiller(stop=metric_stop),
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

    # --- 统一拉起（S2-2）：声明完毕，一次启动；此后由注册表负责观测与死亡自愈 ---
    await reg.start()

    try:
        await hub.refresh()  # 冷启动立即填充，接口首次调用即有数据
        await risk_engine.refresh()
    except Exception:
        log.exception("initial refresh failed; serving stale/empty until next cycle")
    yield
    # --- 停机：统一收割（先发信号、再限时收割，任何单任务不得拖死关机）---
    # 此前这里是与启动清单并列的**第二份手写清单**（26 行 _reap），现在由注册表
    # 按同一份声明收敛：能自己退的走 stop 事件，其余 cancel，逐个限时收割。
    await reg.shutdown()
    with contextlib.suppress(Exception, TimeoutError):
        await asyncio.wait_for(provider.aclose(), timeout=SHUTDOWN_GRACE_SECONDS)
    if app.state.theme_catalog is not None:
        with contextlib.suppress(Exception, TimeoutError):
            await asyncio.wait_for(app.state.theme_catalog.aclose(), timeout=SHUTDOWN_GRACE_SECONDS)


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

# 统一错误契约：所有错误响应形态 {detail, code}（技术评审 B1）
from app.core.errors import register_error_handlers  # noqa: E402

register_error_handlers(app)


# 性能基线中间件（策略进化 P1 方向4）：逐请求记录「路由模板 → 耗时」，
# /api/system/metrics 聚合 p50/p95/p99。call_next 返回后 scope["route"] 已被
# 路由器写入，拿不到（404/中间链异常）退化为数字折叠的原始路径。
@app.middleware("http")
async def _perf_middleware(request, call_next):
    import time as _time

    from app.core import perf as _perf

    t0 = _time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", None) or _perf.collapse_path(
            request.scope.get("path", "")
        )
        _perf.record_api(route_path, (_time.perf_counter() - t0) * 1000)
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_route.router, prefix="/api")
app.include_router(market_route.router, prefix="/api")
app.include_router(backtest_route.router, prefix="/api")
app.include_router(watchlist_route.router, prefix="/api")
app.include_router(paper_route.router, prefix="/api")
app.include_router(review_route.router, prefix="/api")
# predict 路由已删（2026-09-08 审查 P0-4：REST 5 端点全孤立）；predict 包
# 瘦成 auto-verify 库保留（review/service.maybe_auto_verify 消费预判引擎）。
app.include_router(alert_route.router, prefix="/api")
app.include_router(risk_route.router, prefix="/api")
app.include_router(news_route.router, prefix="/api")
app.include_router(theme_catalog_route.router, prefix="/api")
app.include_router(events_route.router, prefix="/api")
app.include_router(real_position_route.router, prefix="/api")
app.include_router(agent_route.router, prefix="/api")  # AI 控制台（任务中心/审计）
app.include_router(picks_route.router, prefix="/api")
app.include_router(picks_intraday_route.router, prefix="/api")
app.include_router(assistant_route.router, prefix="/api")
app.include_router(ext_data_route.router, prefix="/api")
app.include_router(notifications_route.router, prefix="/api")
app.include_router(ws_router)
