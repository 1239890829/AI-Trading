from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_hub
from app.core.config import settings
from app.services.quote_hub import QuoteHub

router = APIRouter(tags=["system"])

#: 存活探针专用 router：**全库唯一不挂凭据守卫的路由器**（挂载见 `main.py`）。
#:
#: ⚠️ **为什么必须与 `router` 分开**（2026-09-15 R22 实测教训）：凭据豁免的粒度是
#: **路由器**（`main.py` 按 router 传 `dependencies=`），而本文件同时装着 `/health`
#: 与 `/system/*`。首版把整个 `router` 豁免掉，于是 `/api/system/llm-probe`（`force=1`
#: 会**真实花钱**）、`/system/caches`、`/system/metrics`、`/system/providers`、
#: `/system/marketdb-quality`、`/system/schedulers` 六个端点被**连带放开**——
#: 全部返回 200。这是"豁免挂在错误的粒度上"的典型形态：**看起来只开了一条**，
#: 实际开了一组。由 `tests/test_auth_boundary.py` 的行为式门禁抓出（它遍历
#: OpenAPI 逐条发无凭据请求，只有逐条探测才看得见"同 router 的邻居被一起放开"）。
liveness_router = APIRouter(tags=["system"])


@liveness_router.get("/health")
async def health(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    stale = hub.is_stale()
    index_batch = hub.index_batch() if hasattr(hub, "index_batch") else None
    incomplete = bool(index_batch and index_batch["missing_symbols"])
    source_rejections = hub.source_rejections() if hasattr(hub, "source_rejections") else None
    rejected = bool(source_rejections and (
        source_rejections["quotes"]["count"] or source_rejections["indices"]["count"]
    ))
    snapshot_service = getattr(request.app.state, "snapshot_service", None)
    snapshot_health = snapshot_service.breadth_payload() if snapshot_service is not None else None
    snapshot_degraded = bool(snapshot_health and (
        (snapshot_health.get("freshness") or {}).get("state") != "ready"
        or int(snapshot_health.get("consecutive_save_failures") or 0) > 0
    ))
    return {
        "status": "degraded" if stale or incomplete or rejected or snapshot_degraded else "ok",
        "app": settings.app_name,
        "version": settings.version,
        "provider": hub.provider.name,
        "poll_interval_seconds": hub.poll_interval,
        "last_success_refresh": hub.last_success_refresh.isoformat() if hub.last_success_refresh else None,
        "last_attempt": hub.last_attempt.isoformat() if hub.last_attempt else None,
        "consecutive_failures": hub.consecutive_failures,
        "last_error": hub.last_error,
        "is_stale": stale,
        "index_batch": index_batch,
        "source_rejections": source_rejections,
        "market_snapshot": snapshot_health,
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
    # Jev 只读状态：不触发 API 调用，不回传凭据或请求正文。
    from app.core.jev_client import status_snapshot as jev_status_snapshot

    payload["jev"] = jev_status_snapshot()
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


@router.get("/system/schedulers")
async def system_schedulers(request: Request) -> dict:
    """常驻调度器可观测（S2-2）：逐任务的启用/存活/心跳/失败/重启状态。

    此前只有 evolution 暴露自己的调度状态，其余 25 个常驻任务靠日志猜——
    "任务静默死亡"两次都是事后才发现。这里把每个任务收敛成一行结构化状态：
    `state`（running/dead/restarting/disabled/…）、`last_tick`、`failures`、
    `restarts`、`last_error`。

    `heartbeat="external"` 表示该循环由自己的 `create_task` 驱动（不是注册表
    驱动），`last_tick` 恒为 null——**显式标注"没有心跳数据"，不假装有**。
    未装配注册表（精简启动 / 早期单测）时返回 `available=false` 而非空列表，
    避免"没有任务"与"看不到任务"被混为一谈。
    """
    reg = getattr(request.app.state, "schedulers", None)
    if reg is None:
        return {"available": False, "reason": "调度注册表未装配（精简启动或单测环境）",
                "schedulers": [], "counts": {}}
    return {
        "available": True,
        "counts": reg.counts(),
        "dead": reg.dead_names(),
        "schedulers": reg.snapshot(),
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
