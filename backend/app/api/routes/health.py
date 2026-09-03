from __future__ import annotations

from fastapi import APIRouter, Depends, Request

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


@router.get("/system/providers")
async def system_providers(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    """provider 链可观测（P0-A）：熔断状态/连续失败数/最后服务源/切换记录。

    熔断触发（state=open）意味着数据已自动降级到备源——这个端点把"降级"变成
    可见的：东财间歇断连、腾讯 WAF 封禁（2026-08-31）过去都只能靠事后排查。
    缓存命中统计单列在 GET /api/system/caches。
    """
    provider = hub.provider
    if hasattr(provider, "provider_health"):
        payload = provider.provider_health()
    else:
        # 单源部署（mock 或单 provider）没有链状态，给最小结构保持契约稳定
        payload = {
            "chain": provider.name,
            "providers": [{
                "name": provider.name,
                "realtime": bool(getattr(provider, "realtime", False)),
                "realtime_rank": getattr(provider, "realtime_rank", None),
                "methods": [],
            }],
            "breakers": {},
            "last_good": {},
            "switch_log": [],
        }
    from app.core.ttl_cache import live_caches

    payload["caches"] = live_caches()
    # ths 涨停原因单点哨兵（P0-B）：无实例 = 未启用/未到首拍
    sent = getattr(request.app.state, "ths_sentinel", None)
    payload["ths_reason_sentinel"] = sent.snapshot() if sent is not None else {"state": "not_started"}
    # 盘中情绪监控（sentiment P2 #14）：无实例 = 未启用/未到首拍
    mon = getattr(request.app.state, "sentiment_monitor", None)
    payload["sentiment_monitor"] = mon.snapshot() if mon is not None else {"state": "not_started"}
    return payload


@router.get("/system/caches")
async def system_caches() -> dict:
    """进程内 TTL 缓存观测（P0-5 统一缓存层）：命中率/容量/逐出。

    逐出数持续上涨 = 键空间在膨胀（"缓存键漂移"类隐患的信号）；
    命中率异常低 = 缓存可能没起作用。实例挂在 app.state/服务单例上，
    随其回收自动退出注册表（弱引用）。
    """
    from app.core.ttl_cache import live_caches

    return {"caches": live_caches()}


@router.get("/system/provider-capabilities")
async def provider_capabilities() -> dict:
    """provider 能力注册表（代码级静态真值）：谁有真实现、谁是恒空占位、谁是单点。

    与 GET /api/system/providers 分工：那边回答"运行时健康"（熔断/延迟/实时
    降级），本端点回答"代码里谁实现了什么"。single_points 是单点风险清单——
    唯一 SUPPORTED 源挂掉即无人兜底（ths 涨停原因哨兵之外可扩展的告警选点依据）。
    """
    from app.services.provider_capabilities import CAPABILITIES, LEVELS, single_point_methods

    return {
        "levels": LEVELS,
        "capabilities": CAPABILITIES,
        "single_points": single_point_methods(),
    }
