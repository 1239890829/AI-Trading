"""板块与竞价：板块列表、涨速榜、竞价（快照 / 基准 / 溢价）。

（自 `market.py` 切出，2026-09-15 IMP-005 批 3。**只搬位置，未改逻辑**：
分片正文与原文件对应定义逐字相同。跨分片共用的信封辅助在 `market_envelope`。）
"""

from __future__ import annotations

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.deps import get_hub
from app.core.ttl_cache import cache_on
from app.schemas.envelope import (
    AuctionBenchmarkItem,
    AuctionSnapshot,
    Envelope,
)
from app.services.quote_enrich import fetch_quotes_list
from app.services.quote_hub import QuoteHub
from app.services.market_snapshot import (
    default_trade_date,
)
from app.services.speed_sampler import SpeedSampler
from app.core.bjtime import beijing_today

router = APIRouter(tags=["market"])

from app.api.routes.market_envelope import (
    meta_payload,
)


@router.get("/auction/{symbol}", response_model=Envelope[AuctionSnapshot])
async def auction(symbol: str, stage: str = Query(default="final", description="final 终态 / live 实时"), hub: QuoteHub = Depends(get_hub)) -> dict:
    """集合竞价快照（ths 官方）：竞价价/涨跌幅/量/量比/未匹配量。

    非竞价时段返回最近一次终态；data_status 标识就绪状态，客户端据此决定展示策略。
    """
    try:
        rows = await hub.provider.get_auction_snapshot([symbol], stage=stage)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"竞价数据源失败：{exc}")
    if not rows:
        raise HTTPException(status_code=404, detail=f"{symbol} 无竞价数据")
    return {"data": rows[0], "meta": meta_payload(hub)}


@router.get("/auction-benchmark", response_model=Envelope[list[AuctionBenchmarkItem]])
async def auction_benchmark(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，默认当日（Asia/Shanghai）"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """短线风向标竞价基准（按日，含题材 tags）——新题材预判与竞价联动验证的数据面。"""
    d = date.fromisoformat(date_str) if date_str else beijing_today()
    try:
        rows = await hub.provider.get_auction_benchmark(d)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"竞价基准数据源失败：{exc}")
    return {"data": rows, "meta": meta_payload(hub)}


@router.get("/auction-premium")
async def auction_premium(
    request: Request,
    date_str: str | None = Query(default=None, alias="date", description="溢价观察日 YYYY-MM-DD，默认最近交易日"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """竞价溢价比因子（system-review §4.2 P0）：昨日涨停股今日竞价溢价分布。

    口径 = 今日竞价开盘价 / 昨日涨停封板价（昨收即封板价，溢价≈ths auction_pct）。
    <3% 一日游风险区、≥5% 抢筹；竞价缺失 = unknown 单列，绝不冒充 0。
    结果缓存 60s；数据面失败折进 caveats 显式降级，不抛 502。
    """
    from app.market.auction_premium import collect_premium

    asof = date.fromisoformat(date_str) if date_str else await default_trade_date(hub)
    cache = cache_on(request.app.state, "market.auction_premium", 60, maxsize=4)
    hit, payload = cache.get(asof)
    if hit:
        return payload
    payload = {"data": await collect_premium(hub, asof), "meta": meta_payload(hub)}
    cache.set(asof, payload)
    return payload


@router.get("/boards")
async def boards(
    type: str = Query(default="hangye", description="hangye(行业) | concept(概念)"),
    request: Request = None,
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """板块排行：涨跌幅/成交额/领涨股（新浪闪电排行，一次请求全量）。结果缓存 60s。"""
    cache = cache_on(request.app.state, "market.boards", 60, maxsize=4)
    hit, payload = cache.get(type)
    if hit:
        return payload
    try:
        rows = await hub.provider.get_board_rankings(type)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"板块数据源失败：{exc}")
    rows.sort(key=lambda r: (r.get("change_pct") or 0), reverse=True)
    payload = {"data": {"type": type, "boards": rows}, "meta": meta_payload(hub)}
    cache.set(type, payload)
    return payload


def _speed_sampler(request: Request) -> SpeedSampler:
    if not hasattr(request.app.state, "speed_sampler"):
        request.app.state.speed_sampler = SpeedSampler()
    return request.app.state.speed_sampler


@router.get("/speed-rank")
async def speed_rank(
    request: Request,
    theme: str | None = Query(default=None, description="官方题材代码（88xxxx.TI），与 symbols 二选一"),
    symbols: str | None = Query(default=None, description="逗号分隔标的列表（≤200，优先于 theme）"),
    limit: int = Query(default=20, ge=1, le=50),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """板块/题材成分股 5 分钟涨速榜。

    口径（行业通行）：**涨速 = (当前价 − 5 分钟前价) / 5 分钟前价 × 100%**——
    同花顺/东财/通达信行情列表"涨速"列均为此口径；东财 clist f22 同源实测对照。
    ths 官方 API 无涨速数值字段（飙升榜/热股榜为热度排名），故基于腾讯批量快照自算。

    采样为惰性模式：本端点每次调用写入一批采样，前端 30s 轮询自然把历史攒到
    5 分钟窗口。历史不足的标的返回 sampled=false（前端显示"采样中"），
    绝不拿当日涨跌幅冒充涨速。
    """
    sampler = _speed_sampler(request)
    theme_name: str | None = None
    if symbols:
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()][:200]
    elif theme:
        svc = getattr(request.app.state, "theme_catalog", None)
        if svc is None:
            raise HTTPException(status_code=503, detail="题材目录服务未启用（缺 THS key）")
        sym_list = [m.symbol for m in svc.get_members(theme)][:200]
        th = next((t for t in svc.get_catalog() if t.code == theme), None)
        theme_name = th.name if th else theme
        if not sym_list:
            return {
                "data": {"theme": theme, "theme_name": theme_name, "window": "5m",
                         "items": [], "note": "题材成分尚未同步，稍后再试"},
                "meta": meta_payload(hub),
            }
    else:
        raise HTTPException(status_code=400, detail="theme 与 symbols 至少给一个")

    quotes = await fetch_quotes_list(hub, sym_list)
    prices = {q.symbol: q.price for q in quotes}
    sampler.record(prices)

    items = []
    for q in quotes:
        sp, span = sampler.speed(q.symbol)
        items.append(
            {
                "symbol": q.symbol,
                "name": q.name,
                "price": q.price,
                "change_pct": q.change_pct,
                "speed": sp,
                "sampled": sp is not None,
                "sample_span_sec": None if sp is not None else round(span),
            }
        )
    # 已有完整采样的按涨速降序在前；采样不足的按跨度降序垫底（尽快变可用）
    items.sort(key=lambda r: (not r["sampled"], -(r["speed"] or 0)))
    return {
        "data": {
            "theme": theme,
            "theme_name": theme_name,
            "window": "5m",
            "basis": "涨速 = 最近 5 分钟涨跌幅（同花顺行情口径）",
            "items": items[:limit],
        },
        "meta": meta_payload(hub),
    }
