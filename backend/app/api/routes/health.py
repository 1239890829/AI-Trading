from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_hub
from app.core.config import settings
from app.services.quote_hub import QuoteHub

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(hub: QuoteHub = Depends(get_hub)) -> dict:
    stale = hub.is_stale()
    return {
        "status": "ok" if not stale else "degraded",
        "app": settings.app_name,
        "version": settings.version,
        "provider": hub.provider.name,
        "poll_interval_seconds": hub.poll_interval,
        "last_success_refresh": hub.last_success_refresh.isoformat() if hub.last_success_refresh else None,
        "last_attempt": hub.last_attempt.isoformat() if hub.last_attempt else None,
        "consecutive_failures": hub.consecutive_failures,
        "last_error": hub.last_error,
        "is_stale": stale,
    }
