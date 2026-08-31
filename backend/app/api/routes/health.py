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


@router.get("/system/caches")
async def system_caches() -> dict:
    """进程内 TTL 缓存观测（P0-5 统一缓存层）：命中率/容量/逐出。

    逐出数持续上涨 = 键空间在膨胀（"缓存键漂移"类隐患的信号）；
    命中率异常低 = 缓存可能没起作用。实例挂在 app.state/服务单例上，
    随其回收自动退出注册表（弱引用）。
    """
    from app.core.ttl_cache import live_caches

    return {"caches": live_caches()}
