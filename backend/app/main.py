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
)

logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

# lifespan 关闭时给每个后台任务的收尾宽限（秒）。
_SHUTDOWN_GRACE_SECONDS = 10.0


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


async def _reap(task: asyncio.Task | None, *, name: str, grace: float = _SHUTDOWN_GRACE_SECONDS) -> None:
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
    risk_engine = RiskEngine(hub=hub, snapshot_service=snapshot_service, session_factory=get_session_factory())
    app.state.risk_engine = risk_engine

    # --- 题材字典/官方成分（linkage-design §3 T1）：fuyao 官方目录与成分同步 ---
    try:
        theme_catalog = ThemeCatalogService(get_session_factory())
    except RuntimeError as exc:
        # 未配置 ths key 时降级为 None：题材端点返回 503，其余功能不受影响
        log.warning("theme catalog disabled: %s", exc)
        theme_catalog = None
    app.state.theme_catalog = theme_catalog

    # --- 事件驱动（linkage-design §4 E1）：EventCard 存储/查询 ---
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
    triage_stop = asyncio.Event()
    from app.services.alert_triage import triage_loop

    triage_task = asyncio.create_task(triage_loop(triage_stop), name="alert-triage")

    # --- AI 大脑：每日进化议程（docs/evolution-brain-plan.md，交易日 15:45）---
    # 无条件挂载：autonomy 关闭时议程照常生成（仅不执行，降级为建议清单）
    evolution_stop = asyncio.Event()
    from app.services.evolution import evolution_scheduler

    evolution_task = asyncio.create_task(
        evolution_scheduler(
            app,
            stop=evolution_stop,
            run_hour=settings.agent_evolution_hour,
            run_minute=settings.agent_evolution_minute,
            check_interval_seconds=settings.review_check_interval_seconds,
        ),
        name="evolution-agenda",
    )

    review_stop = asyncio.Event()
    review_task = None
    if settings.review_scheduler_enabled:
        review_task = asyncio.create_task(
            review_scheduler(
                review_svc,
                run_hour=settings.review_run_hour,
                run_minute=settings.review_run_minute,
                check_interval_seconds=settings.review_check_interval_seconds,
                stop=review_stop,
            ),
            name="review-scheduler",
        )

    poller = asyncio.create_task(hub.run(), name="quote-poller")
    snapshotter = asyncio.create_task(snapshot_service.run(), name="market-snapshot")

    # 数据健康哨兵盘中循环（push_policy ② ANOMALY：交易时段 15 分钟一轮，
    # 新异常推飞书摘要卡——2026-09-08 推送矩阵）
    # 每日组合自动生成（09:26，幂等 by DailyPickSet——旧 09:26 automation 停用后的生成接盘者）
    picks_autogen_stop = asyncio.Event()
    picks_autogen_task = None
    if settings.picks_autogen_enabled:
        from app.picks.picks_autogen import picks_autogen_scheduler

        picks_autogen_task = asyncio.create_task(
            picks_autogen_scheduler(
                app, stop=picks_autogen_stop,
                run_hour=settings.picks_autogen_hour,
                run_minute=settings.picks_autogen_minute,
            ),
            name="picks-autogen",
        )

    data_health_stop = asyncio.Event()
    from app.services.data_health_loop import data_health_loop

    data_health_task = asyncio.create_task(data_health_loop(app, stop=data_health_stop), name="data-health-sentinel")

    # 临板雷达（KB-DEC-011）：涨停前识别与提醒，交易时段 6s 一轮，封板后不入册
    radar_stop = asyncio.Event()
    from app.picks.pre_limit_radar import pre_limit_loop

    radar_task = asyncio.create_task(pre_limit_loop(app, stop=radar_stop), name="pre-limit-radar")

    async def paper_matcher():
        # 技术债 #5：无挂单时空转降频（30s 查一次挂单表），有挂单才 5s 密集轮询
        interval = 5.0
        while True:
            try:
                pending = await paper.match_pending()
                interval = 5.0 if pending > 0 else 30.0
            except Exception:
                log.exception("paper match_pending failed")
            await asyncio.sleep(interval)

    matcher = asyncio.create_task(paper_matcher(), name="paper-matcher")

    async def alert_quotes_feeder():
        while True:
            try:
                alert_engine.update_quotes({s: q.model_dump() for s, q in hub.quotes.items()})
            except Exception:
                log.exception("alert quotes feeder failed")
            await asyncio.sleep(settings.alert_poll_interval_seconds)

    alert_feeder = asyncio.create_task(alert_quotes_feeder(), name="alert-quotes-feeder")
    alert_engine.start()

    async def risk_refresher():
        while True:
            try:
                await risk_engine.refresh()
            except Exception:
                log.exception("risk engine refresh failed")
            await asyncio.sleep(60.0)

    risk_task = asyncio.create_task(risk_refresher(), name="risk-refresher")

    async def event_collector():
        """事件采集调度（P1）：自选新闻 → 事件卡，指纹去重保证幂等。

        此前事件只有手工/半自动录入，活跃事件长期个位数，选股消息面近乎
        空转（2026-08-31 盘点）。30 分钟一轮：新闻源本身更新频率低，
        去重后重复采集只产生 duplicated 计数，无害。

        非盘中轮次（≥15:05 或 <09:15）附带把最近交易日涨停股纳入采集范围
        （R6，2026-09-01）：盘中轮次范围保持 自选∪组合∪持仓，控上游配额。
        """
        await asyncio.sleep(45)  # 启动先让目录同步/行情填充完成
        while True:
            try:
                from datetime import datetime as _dt, time as _time

                from app.api.routes.events import collect_news_events

                now = _dt.now().time()
                after_hours = now >= _time(15, 5) or now < _time(9, 15)
                stats = await collect_news_events(app.state, include_limit_up=after_hours)
                if stats.get("created"):
                    log.info("event collector: +%s 新事件（duplicated %s）", stats["created"], stats["duplicated"])
            except Exception:
                log.exception("event collector failed")
            await asyncio.sleep(1800.0)

    event_task = asyncio.create_task(event_collector(), name="event-collector")

    async def metric_history_backfiller():
        """情绪历史指标库的**增量**维护（P0-3b 分位校准的数据底座）。

        没有这个任务，库会停在首次手工回补的那天：半年后界面仍写着"按近 120
        个交易日分位校准"，实际窗口早已漂移到半年前——这正是"数字看着合理、
        结论其实是错的"那类静默失效。故必须自动跑；回补是增量的（已在库的
        日期不重拉），稳态下每轮只拉 1–2 天 × 2 个请求，配额开销可忽略。

        启动延迟 90s：让冷启动的行情/快照先填完，不和其他网络请求抢配额。
        """
        await asyncio.sleep(90)
        while True:
            try:
                from app.sentiment import metric_history

                days = await hub_trading_days()
                if not days:
                    log.warning("metric history backfill skipped: 交易日历不可用")
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
                    if stats["suspicious"]:
                        # 数据源日期回退（东财 push2ex 的前科）会污染整个分布，
                        # 剔除后宁可少样本。非 0 属异常，必须留痕。
                        log.warning(
                            "metric history: %s 天涨停池与前一日完全相同，疑似数据源日期回退，已剔除",
                            stats["suspicious"],
                        )
            except Exception:
                log.exception("metric history backfill failed")
            await asyncio.sleep(settings.sentiment_history_backfill_interval_seconds)

    metric_task = None
    if settings.sentiment_history_backfill_enabled:
        metric_task = asyncio.create_task(
            metric_history_backfiller(), name="metric-history-backfill"
        )

    # --- 盘前简报 + 盘中跟踪（选股 2.0 批次 B）---
    # 环境缓存放 state：手动单拍端点与 watcher_loop 共用同一份（避免各自重算情绪）
    app.state.picks_env_cache = {"at": 0.0, "env": None}

    premarket_stop = asyncio.Event()
    premarket_task = None
    if settings.premarket_brief_enabled:
        from app.picks.morning_brief import premarket_scheduler

        premarket_task = asyncio.create_task(
            premarket_scheduler(
                app,
                stop=premarket_stop,
                run_hour=settings.premarket_brief_hour,
                run_minute=settings.premarket_brief_minute,
                check_interval_seconds=settings.review_check_interval_seconds,
            ),
            name="premarket-brief",
        )

    watcher_stop = asyncio.Event()
    watcher_task = None
    if settings.picks_watcher_enabled:
        from app.picks.watcher import watcher_loop

        watcher_task = asyncio.create_task(watcher_loop(app, stop=watcher_stop), name="picks-watcher")

    # --- 盘中买点推送（2026-09-08 用户定稿：唯一保留的盘中飞书推送）---
    buy_point_stop = asyncio.Event()
    buy_point_task = None
    if settings.picks_buy_point_enabled:
        from app.picks.buy_point import buy_point_loop

        buy_point_task = asyncio.create_task(buy_point_loop(app, stop=buy_point_stop), name="picks-buy-point")

    # --- 盘后方向对照（选股 2.0 批次 C）：15:35 对照当日简报 + 提醒收益回填 ---
    review_intraday_stop = asyncio.Event()
    review_intraday_task = None
    if settings.picks_review_enabled:
        from app.picks.review_intraday import intraday_review_scheduler

        review_intraday_task = asyncio.create_task(
            intraday_review_scheduler(
                app,
                stop=review_intraday_stop,
                run_hour=settings.picks_review_hour,
                run_minute=settings.picks_review_minute,
                check_interval_seconds=settings.review_check_interval_seconds,
            ),
            name="picks-intraday-review",
        )

    # --- ths 涨停原因单点哨兵（P0-B）：交易时段探测 reason 非空率，缺原因即告警 ---
    ths_sentinel_stop = asyncio.Event()
    ths_sentinel_task = None
    if settings.ths_sentinel_enabled:
        from app.services.ths_sentinel import sentinel_loop

        ths_sentinel_task = asyncio.create_task(sentinel_loop(app, stop=ths_sentinel_stop), name="ths-reason-sentinel")

    # --- 盘中情绪监控（sentiment P2 #14）：高度板炸板/炸板率破位/指数急杀 → 告警 ---
    sentiment_monitor_stop = asyncio.Event()
    sentiment_monitor_task = None
    if settings.sentiment_monitor_enabled:
        from app.sentiment.intraday_monitor import sentiment_monitor_loop

        sentiment_monitor_task = asyncio.create_task(
            sentiment_monitor_loop(app, stop=sentiment_monitor_stop), name="sentiment-monitor"
        )

    # --- 影子持仓晨窗（P0-B）：09:26 竞价后按执行闸门模拟执行最新组合 ---
    shadow_stop = asyncio.Event()
    shadow_task = None
    if settings.picks_shadow_enabled and app.state.paper_shadow is not None:
        from app.picks.shadow import shadow_loop

        shadow_task = asyncio.create_task(shadow_loop(app, stop=shadow_stop), name="picks-shadow")

    # --- marketdb 盘后增量同步（RPS/tech_score 数据地基；子进程隔离 + 磁盘幂等）---
    marketdb_stop = asyncio.Event()
    marketdb_task = None
    if settings.marketdb_sync_enabled:
        if not settings.ths_api_key:
            log.warning("marketdb_sync_enabled but ths_api_key missing, scheduler not started")
        else:
            from app.market.marketdb_sync import marketdb_sync_scheduler

            marketdb_task = asyncio.create_task(
                marketdb_sync_scheduler(
                    stop=marketdb_stop,
                    run_hour=settings.marketdb_sync_hour,
                    run_minute=settings.marketdb_sync_minute,
                    check_interval_seconds=settings.marketdb_sync_check_interval_seconds,
                ),
                name="marketdb-sync",
            )

    # --- 东财 7x24 快讯流（hotspot-pipeline G1/P0①）：宏观快讯 → build_event → EventStore ---
    flash_stop = asyncio.Event()
    flash_task = None
    if settings.flash_news_enabled:
        from app.news.flash import flash_news_loop

        flash_task = asyncio.create_task(flash_news_loop(app, stop=flash_stop), name="news-flash")

    # --- LLM 网关健康探针（2026-09-06）：区分额度不足/网关失败，避免静默降级 ---
    llm_probe_stop = asyncio.Event()
    llm_probe_task = None
    if settings.llm_probe_enabled and settings.llm_probe_interval_seconds > 0:
        from app.services.llm_probe import probe_loop

        llm_probe_task = asyncio.create_task(
            probe_loop(app, stop=llm_probe_stop), name="llm-gateway-probe"
        )

    try:
        await hub.refresh()  # 冷启动立即填充，接口首次调用即有数据
        await risk_engine.refresh()
    except Exception:
        log.exception("initial refresh failed; serving stale/empty until next cycle")
    yield
    # --- 停机：先发信号（cancel + stop），再限时收割（任何单任务不得拖死关机）---
    poller.cancel()
    snapshotter.cancel()
    matcher.cancel()
    alert_feeder.cancel()
    alert_engine.stop()
    risk_task.cancel()
    event_task.cancel()
    if metric_task is not None:
        metric_task.cancel()
    if premarket_task is not None:
        premarket_stop.set()
    if watcher_task is not None:
        watcher_stop.set()
    if buy_point_task is not None:
        buy_point_stop.set()
    triage_stop.set()
    evolution_stop.set()
    data_health_stop.set()
    picks_autogen_stop.set()
    radar_stop.set()
    if ths_sentinel_task is not None:
        ths_sentinel_stop.set()
    if sentiment_monitor_task is not None:
        sentiment_monitor_stop.set()
    if shadow_task is not None:
        shadow_stop.set()
    if review_intraday_task is not None:
        review_intraday_stop.set()
    if review_task is not None:
        review_stop.set()
    if marketdb_task is not None:
        marketdb_stop.set()
    if llm_probe_task is not None:
        llm_probe_stop.set()
    if flash_task is not None:
        flash_stop.set()
    await _reap(poller, name="quote-poller")
    await _reap(snapshotter, name="market-snapshot")
    await _reap(matcher, name="paper-matcher")
    await _reap(alert_feeder, name="alert-quotes-feeder")
    await _reap(risk_task, name="risk-refresher")
    await _reap(event_task, name="event-collector")
    await _reap(metric_task, name="metric-history-backfill")
    await _reap(review_task, name="review-scheduler")
    await _reap(premarket_task, name="premarket-brief")
    await _reap(watcher_task, name="picks-watcher")
    await _reap(buy_point_task, name="picks-buy-point")
    await _reap(triage_task, name="alert-triage")
    await _reap(evolution_task, name="evolution-agenda")
    await _reap(radar_task, name="pre-limit-radar")
    if picks_autogen_task is not None:
        await _reap(picks_autogen_task, name="picks-autogen")
    await _reap(data_health_task, name="data-health-sentinel")
    await _reap(review_intraday_task, name="picks-intraday-review")
    await _reap(ths_sentinel_task, name="ths-reason-sentinel")
    await _reap(sentiment_monitor_task, name="sentiment-monitor")
    await _reap(shadow_task, name="picks-shadow")
    await _reap(marketdb_task, name="marketdb-sync")
    await _reap(llm_probe_task, name="llm-gateway-probe")
    await _reap(flash_task, name="news-flash")
    with contextlib.suppress(Exception, TimeoutError):
        await asyncio.wait_for(provider.aclose(), timeout=_SHUTDOWN_GRACE_SECONDS)
    if app.state.theme_catalog is not None:
        with contextlib.suppress(Exception, TimeoutError):
            await asyncio.wait_for(app.state.theme_catalog.aclose(), timeout=_SHUTDOWN_GRACE_SECONDS)


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
