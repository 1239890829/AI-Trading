from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health as health_route
from app.api.routes import market as market_route
from app.api.routes import watchlist as watchlist_route
from app.core.config import settings
from app.core.db import get_engine, get_session_factory
from app.data_providers import build_provider
from app.models.watchlist import Base
from app.repositories.watchlist_repo import WatchlistRepository
from app.services.quote_hub import QuoteHub
from app.websocket.routes import router as ws_router

logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(get_engine())
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

    poller = asyncio.create_task(hub.run(), name="quote-poller")
    try:
        await hub.refresh()  # 冷启动立即填充，接口首次调用即有数据
    except Exception:
        log.exception("initial refresh failed; serving stale/empty until next cycle")
    yield
    poller.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await poller
    with contextlib.suppress(Exception):
        await provider.aclose()


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_route.router, prefix="/api")
app.include_router(market_route.router, prefix="/api")
app.include_router(watchlist_route.router, prefix="/api")
app.include_router(ws_router)
