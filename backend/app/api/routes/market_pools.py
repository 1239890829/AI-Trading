"""涨跌停与异动池：涨停池、跌停池、异动（全市场 / 单票）。

（自 `market.py` 切出，2026-09-15 IMP-005 批 3。**只搬位置，未改逻辑**：
分片正文与原文件对应定义逐字相同。跨分片共用的信封辅助在 `market_envelope`。）
"""

from __future__ import annotations

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.deps import get_hub
from app.core.ttl_cache import cache_on
from app.schemas.envelope import (
    AnomalyPayload,
    Envelope,
    LimitDownPoolPayload,
    LimitUpPoolPayload,
)
from app.services.quote_hub import QuoteHub
from app.services.market_snapshot import (
    default_trade_date,
)

router = APIRouter(tags=["market"])

from app.api.routes.market_envelope import (
    meta_payload,
    dated_meta,
)


@router.get("/limit-up", response_model=Envelope[LimitUpPoolPayload])
async def limit_up(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，默认最近交易日"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    trade_date = date.fromisoformat(date_str) if date_str else await default_trade_date(hub)
    try:
        records = await hub.provider.get_limit_up_pool(trade_date)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"涨停池数据源失败：{exc}")
    records.sort(key=lambda r: (r.consecutive_boards or 0), reverse=True)
    return {
        "data": {"trade_date": trade_date.isoformat(), "pool": [r.model_dump(mode="json") for r in records]},
        "meta": await dated_meta(hub, trade_date),
    }


@router.get("/limit-down", response_model=Envelope[LimitDownPoolPayload])
async def limit_down(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，默认最近交易日"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """跌停池（东财 push2ex getTopicDTPool）。市场页跌停入口 → 盘面页跌停 tab 消费。"""
    trade_date = date.fromisoformat(date_str) if date_str else await default_trade_date(hub)
    try:
        records = await hub.provider.get_limit_down_pool(trade_date)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"跌停池数据源失败：{exc}")
    records.sort(key=lambda r: (r.consecutive_days or 0), reverse=True)
    return {
        "data": {"trade_date": trade_date.isoformat(), "pool": [r.model_dump(mode="json") for r in records]},
        "meta": await dated_meta(hub, trade_date),
    }


_ANOMALY_TAG_VALUES = ("LIMIT_UP", "LIMIT_DOWN", "SHARP_RISE", "SHARP_FALL", "RAPID_RALLY", "RAPID_DECLINE")


@router.get("/market/anomalies", response_model=Envelope[AnomalyPayload])
async def market_anomalies(
    request: Request,
    tags: str | None = Query(default=None, description="逗号分隔异动标签（LIMIT_UP/SHARP_RISE…），缺省=全量"),
    limit: int = Query(default=200, ge=1, le=1000),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """当日全市场异动原因（ths 独占，today-only）。

    空集是正常语义（非交易日/尚无异动），note 显式说明，绝不静默。
    60s TTL：全市场一次拉齐 + 内存排序，轮询不回源。
    """
    tag_list = [t.strip().upper() for t in (tags or "").split(",") if t.strip()]
    bad = [t for t in tag_list if t not in _ANOMALY_TAG_VALUES]
    if bad:
        raise HTTPException(status_code=422, detail=f"非法异动标签：{','.join(bad)}；允许值 {','.join(_ANOMALY_TAG_VALUES)}")

    cache = cache_on(request.app.state, "market.anomalies", 60, maxsize=8)
    key = (tuple(tag_list),)

    async def _build() -> dict:
        try:
            records = await hub.provider.get_anomaly_list(tag_list or None)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"异动数据源失败：{exc}") from exc
        return {
            "records": [r.model_dump(mode="json") for r in records[:limit]],
            "note": None if records else "当日无匹配异动记录（today-only 端点，非交易日/未产生异动属正常）",
        }

    _, payload = await cache.get_or_set(key, _build)
    return {"data": payload, "meta": meta_payload(hub)}


@router.get("/market/anomalies/stock", response_model=Envelope[AnomalyPayload])
async def market_anomalies_stock(
    request: Request,
    symbols: str = Query(description="逗号分隔 6 位代码，≤50（官方单批上限）"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """按代码批量查当日异动原因（自选股行徽标/个股详情「为什么异动」消费）。"""
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        raise HTTPException(status_code=422, detail="symbols 不能为空")
    if len(sym_list) > 50:
        raise HTTPException(status_code=422, detail="单批最多 50 个代码（官方上限）")

    cache = cache_on(request.app.state, "market.anomalies.stock", 60, maxsize=64)
    key = (tuple(sym_list),)

    async def _build() -> dict:
        try:
            records = await hub.provider.get_anomaly_stock(sym_list)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"异动数据源失败：{exc}") from exc
        return {
            "records": [r.model_dump(mode="json") for r in records],
            "note": None if records else "所查代码当日无异动记录（非故障）",
        }

    _, payload = await cache.get_or_set(key, _build)
    return {"data": payload, "meta": meta_payload(hub)}
