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
from app.api.routes import screener as screener_route
from app.api.routes import watchlist as watchlist_route
from app.api.routes import alert as alert_route
from app.api.routes import risk as risk_route
from app.api.routes import theme_catalog as theme_catalog_route
from app.core.config import settings
from app.core.db import get_engine, get_session_factory
from app.data_providers import build_provider
from app.market.alert_engine import AlertEngine
from app.models.alert import AlertEvent, AlertRule
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
from app.services.screener_service import ScreenerService
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
    app.state.screener_service = ScreenerService(parquet_dir=Path(settings.parquet_dir))

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
app.include_router(screener_route.router, prefix="/api")
app.include_router(watchlist_route.router, prefix="/api")
app.include_router(paper_route.router, prefix="/api")
app.include_router(review_route.router, prefix="/api")
app.include_router(predict_route.router, prefix="/api")
app.include_router(alert_route.router, prefix="/api")
app.include_router(risk_route.router, prefix="/api")
app.include_router(news_route.router, prefix="/api")
app.include_router(theme_catalog_route.router, prefix="/api")
app.include_router(ws_router)
