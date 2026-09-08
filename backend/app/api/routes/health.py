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
    # LLM 网关体检（2026-09-06）：只回上次结果，不在这里触发真实调用——
    # 否则轮询该端点就等于反复烧额度。要立刻体检走 GET /api/system/llm-probe?force=1
    probe = getattr(request.app.state, "llm_probe", None)
    payload["llm_gateway"] = probe.snapshot(cached=True) if probe is not None else {"state": "not_started"}
    return payload


@router.get("/system/llm-probe")
async def llm_probe(request: Request, force: bool = False) -> dict:
    """LLM 网关体检（2026-09-06）：把「额度不足」与「网关失败」分开暴露。

    - 默认：回上次体检结果（带 cached 标记），零成本、不阻塞
    - `force=1`：发一次真实最小调用（约 $0.0006、1~3s，走 to_thread 不堵
      事件循环），用于充值后/改配置后立刻确认是否恢复

    `last_failure_kind=quota` 就是该充值了；`gateway_error` 充钱也没用。
    """
    probe = getattr(request.app.state, "llm_probe", None)
    if probe is None:
        return {"state": "not_started"}
    return await probe.probe_once(force=force)


@router.get("/system/caches")
async def system_caches() -> dict:
    """进程内 TTL 缓存观测（P0-5 统一缓存层）：命中率/容量/逐出。

    逐出数持续上涨 = 键空间在膨胀（"缓存键漂移"类隐患的信号）；
    命中率异常低 = 缓存可能没起作用。实例挂在 app.state/服务单例上，
    随其回收自动退出注册表（弱引用）。
    """
    from app.core.ttl_cache import live_caches

    return {"caches": live_caches()}


# /system/provider-capabilities 端点已删（2026-09-08 审查 P0-4：前端零调用）。
# 能力注册表本身保留（app/services/provider_capabilities.py）——它是
# provider 方法面的代码级真值 + 单点风险清单（防腐化测试锚定），P2 告警
# 扩点时直接消费，无需经 HTTP。


@router.get("/system/metrics")
async def system_metrics() -> dict:
    """性能基线（策略进化 P1 方向4）：API p95 延迟 / DuckDB 慢查询 / 同步时长趋势。

    内存环形缓冲读时聚合，零持久化；进程重启即清零（基线观测语义，
    不是审计账本）。同步历史由 sync_marketdb.py 落盘 data/marketdb/sync_history.json。
    """
    from pathlib import Path

    from app.core.perf import api_metrics, duck_slow_queries, read_sync_history

    # backend/data/marketdb（本文件在 app/api/routes/ 下，parents[3]=backend）
    db_path = Path(__file__).resolve().parents[3] / "data" / "marketdb" / "market.duckdb"
    return {
        "api": api_metrics(),
        "duck_slow": duck_slow_queries(),
        "sync_history": read_sync_history(db_path),
    }


@router.get("/system/marketdb-quality")
async def marketdb_quality() -> dict:
    """marketdb 质量门报告（sync_marketdb.py 每次同步后落盘）。

    available=False = 尚未跑过带质量门的同步（文件不存在）——显式降级，
    不臆造「质量正常」。
    """
    import json
    from pathlib import Path

    report_file = Path(__file__).resolve().parents[3] / "data" / "marketdb" / "quality_report.json"
    try:
        return json.loads(report_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"available": False, "reason": "尚未生成质量报告（等待下一次同步）"}
    except Exception as exc:  # noqa: BLE001  损坏文件显式报错，不冒充正常
        return {"available": False, "reason": f"质量报告读取失败：{exc}"}
