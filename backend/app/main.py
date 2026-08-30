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
from app.api.routes import paper as paper_route
from app.api.routes import predict as predict_route
from app.api.routes import review as review_route
from app.api.routes import screener as screener_route
from app.api.routes import watchlist as watchlist_route
from app.core.config import settings
from app.core.db import get_engine, get_session_factory
from app.data_providers import build_provider
from app.models.paper import PaperAccount, PaperOrder, PaperPosition
from app.predict.models import (  # noqa: F401  注册预判两张表
    PredictionReportRow,
    PredictionThemeRow,
)
from app.repositories.watchlist_repo import WatchlistRepository
from app.paper.engine import PaperTradingEngine
from app.review.models import (  # noqa: F401  注册复盘三张表
    ReviewActionItemRow,
    ReviewMetaInsightRow,
    ReviewReportRow,
)
from app.review.service import ReviewService, review_scheduler
from app.services.snapshot_service import MarketSnapshotService
from app.services.screener_service import ScreenerService
from app.services.quote_hub import QuoteHub
from app.market.sentiment_history import SentimentHistoryRow  # noqa: F401  注册情绪序列表
from app.websocket.routes import router as ws_router

# 显式持有引用：确保各模块的表注册进 Base.metadata，否则 create_all 不会建表
_REGISTERED_MODELS = (
    PaperAccount, PaperOrder, PaperPosition,
    ReviewReportRow, ReviewActionItemRow, ReviewMetaInsightRow,
    PredictionReportRow, PredictionThemeRow,
    SentimentHistoryRow,
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

    async def live_quote(symbol: str):
        try:
            from app.data_quality.validator import validate_quote

            q = await provider.get_quote(symbol)
            if q is None:
                return None
            # ths 快照无涨跌停价：从腾讯源补齐（撮合的涨跌停校验依赖它）
            if (q.limit_up_price is None or q.limit_down_price is None) and provider.name != "tencent":
                chain = provider.providers if hasattr(provider, "providers") else []
                tencent = next((p for p in chain if p.name == "tencent"), None)
                if tencent is not None:
                    try:
                        tq = await tencent.get_quote(symbol)
                        if tq is not None:
                            q.limit_up_price = q.limit_up_price or tq.limit_up_price
                            q.limit_down_price = q.limit_down_price or tq.limit_down_price
                    except Exception:
                        pass
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
        while True:
            try:
                await paper.match_pending()
            except Exception:
                log.exception("paper match_pending failed")
            await asyncio.sleep(5)

    matcher = asyncio.create_task(paper_matcher(), name="paper-matcher")
    try:
        await hub.refresh()  # 冷启动立即填充，接口首次调用即有数据
    except Exception:
        log.exception("initial refresh failed; serving stale/empty until next cycle")
    yield
    poller.cancel()
    snapshotter.cancel()
    matcher.cancel()
    if review_task is not None:
        review_stop.set()
    with contextlib.suppress(asyncio.CancelledError):
        await poller
    with contextlib.suppress(asyncio.CancelledError):
        await snapshotter
    with contextlib.suppress(asyncio.CancelledError):
        await matcher
    if review_task is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await review_task
    with contextlib.suppress(Exception):
        await provider.aclose()


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
app.include_router(ws_router)
