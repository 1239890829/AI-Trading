from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import backtest as backtest_route
from app.api.routes import health as health_route
from app.api.routes import market as market_route
from app.api.routes import news as news_route
from app.api.routes import paper as paper_route
from app.api.routes import predict as predict_route
from app.api.routes import review as review_route
from app.api.routes import watchlist as watchlist_route
from app.api.routes import alert as alert_route
from app.api.routes import risk as risk_route
from app.api.routes import theme_catalog as theme_catalog_route
from app.api.routes import events as events_route
from app.api.routes import picks as picks_route
from app.api.routes import picks_intraday as picks_intraday_route
from app.api.routes import real_position as real_position_route
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
)

logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


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
        methodology_version=settings.review_methodology_version,
    )
    app.state.review = review_svc

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

    try:
        await hub.refresh()  # 冷启动立即填充，接口首次调用即有数据
        await risk_engine.refresh()
    except Exception:
        log.exception("initial refresh failed; serving stale/empty until next cycle")
    yield
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
    if review_intraday_task is not None:
        review_intraday_stop.set()
    if review_task is not None:
        review_stop.set()
    with contextlib.suppress(asyncio.CancelledError):
        await poller
    with contextlib.suppress(asyncio.CancelledError):
        await snapshotter
    with contextlib.suppress(asyncio.CancelledError):
        await matcher
    with contextlib.suppress(asyncio.CancelledError):
        await alert_feeder
    with contextlib.suppress(asyncio.CancelledError):
        await risk_task
    if review_task is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await review_task
    if premarket_task is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await premarket_task
    if watcher_task is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await watcher_task
    if review_intraday_task is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await review_intraday_task
    with contextlib.suppress(Exception):
        await provider.aclose()
    if app.state.theme_catalog is not None:
        with contextlib.suppress(Exception):
            await app.state.theme_catalog.aclose()


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

# 统一错误契约：所有错误响应形态 {detail, code}（技术评审 B1）
from app.core.errors import register_error_handlers  # noqa: E402

register_error_handlers(app)

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
app.include_router(predict_route.router, prefix="/api")
app.include_router(alert_route.router, prefix="/api")
app.include_router(risk_route.router, prefix="/api")
app.include_router(news_route.router, prefix="/api")
app.include_router(theme_catalog_route.router, prefix="/api")
app.include_router(events_route.router, prefix="/api")
app.include_router(real_position_route.router, prefix="/api")
app.include_router(picks_route.router, prefix="/api")
app.include_router(picks_intraday_route.router, prefix="/api")
app.include_router(ws_router)
