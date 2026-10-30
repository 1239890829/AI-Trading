"""涨跌停与异动池：涨停池、跌停池、异动（全市场 / 单票）。

（自 `market.py` 切出，2026-09-15 IMP-005 批 3。**只搬位置，未改逻辑**：
分片正文与原文件对应定义逐字相同。跨分片共用的信封辅助在 `market_envelope`。）
"""

from __future__ import annotations

from app.services.source_reads import read_pool_input

import asyncio
from copy import deepcopy
import hashlib
import re
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.deps import get_hub
from app.core.ttl_cache import cache_on
from app.core.inflight_reads import LocalReadCapacityExceeded
from app.schemas.envelope import (
    AnomalyPayload,
    Envelope,
    LimitDownPoolPayload,
    LimitUpPoolPayload,
)
from app.services.quote_hub import QuoteHub
from app.core.bjtime import beijing_now
from app.services.market_snapshot import (
    PoolDateError,
    default_trade_date,
    verify_limit_down_date,
    verify_pool_date,
)

router = APIRouter(tags=["market"])

from app.api.routes.market_envelope import (
    meta_payload,
    dated_meta,
)


def _reason_observations(holder, trade_date: date, rows) -> dict:
    """Process-local source observations; never overwrite current pool facts."""
    cache = cache_on(holder, "market.limit_up_reasons", 86400, maxsize=2)
    _, saved = cache.get(trade_date)
    observations = deepcopy(saved or {})
    observed_at = beijing_now().isoformat()
    for row in rows:
        reason = row.reason
        if (row.trade_date != trade_date or not re.fullmatch(r"[0-9]{6}", row.symbol)
                or not row.source or not isinstance(reason, str) or not reason.strip()
                or len(reason) > 8192 or getattr(row.quality, "value", row.quality) == "invalid"):
            continue
        key = (row.symbol, row.source)
        digest = hashlib.sha256(reason.encode("utf-8")).hexdigest()
        if key in observations and observations[key]["content_hash"] == digest:
            continue  # A repeated read cannot move the original evidence clock.
        observations.pop(key, None)
        observations[key] = {
            "symbol": row.symbol, "trade_date": trade_date.isoformat(), "source": row.source,
            "reason": reason, "content_hash": digest, "observed_at": observed_at,
            "received_at": row.received_at.isoformat() if row.received_at else None,
        }
    while len(observations) > 4096:
        observations.pop(next(iter(observations)))
    cache.set(trade_date, observations)
    result = {}
    for (symbol, _source), observation in observations.items():
        result.setdefault(symbol, []).append(deepcopy(observation))
    return result


def _qualification_observations(holder, trade_date: date, domains: dict[str, list]) -> dict:
    """Retain affirmative same-day source identities, never current board status."""
    cache = cache_on(holder, "market.limit_qualifications", 86400, maxsize=2)
    _, saved = cache.get(trade_date)
    observations = deepcopy(saved or {})
    for kind, rows in domains.items():
        for row in rows:
            if row.get("quality") not in {"high", "medium"}:
                continue  # A successful source read does not upgrade low-quality membership.
            key = (row["symbol"], kind, row["source"])
            observations.setdefault(key, {
                "symbol": row["symbol"], "trade_date": trade_date.isoformat(),
                # A combined read must retain the original domain receipt clock.
                "kind": kind, "source": row["source"], "observed_at": row["received_at"],
            })
    while len(observations) > 4096:
        observations.pop(next(iter(observations)))
    cache.set(trade_date, observations)
    result = {}
    for (symbol, _kind, _source), value in observations.items():
        result.setdefault(symbol, []).append(deepcopy(value))
    return result


def _membership_coverage(rows, trade_date: date) -> dict:
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or row.get("trade_date") != trade_date.isoformat()
        or not isinstance(row.get("symbol"), str) or not re.fullmatch(r"[0-9]{6}", row["symbol"])
        or not row.get("source") for row in rows
    ):
        return {"state": "error", "error": "池记录日期或证券来源身份不符"}
    if any(row.get("quality") in {"invalid", "stale"} for row in rows):
        return {"state": "unknown", "error": "池记录质量不足，当前资格待核"}
    return {"state": "success", "error": None}


@router.get("/limit-up", response_model=Envelope[LimitUpPoolPayload], response_model_exclude_unset=True)
async def limit_up(
    request: Request,
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，默认最近交易日"),
    hub: QuoteHub = Depends(get_hub),
    include_breaks: bool = False,
) -> dict:
    try:
        trade_date = date.fromisoformat(date_str) if date_str else await default_trade_date(hub)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="涨停池日期格式非法，需 YYYY-MM-DD") from exc
    try:
        await verify_pool_date(hub.provider, trade_date, pool_name="涨停池")
    except PoolDateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    # 同一交易日的榜单和多个个股详情共享一次读取；缓存保留原读取 meta。
    cache = cache_on(request.app.state, "market.limit_up", 60, maxsize=8)

    async def build() -> dict:
        try:
            records = await read_pool_input(hub.provider, "get_limit_up_pool", trade_date)
        except LocalReadCapacityExceeded:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"涨停池数据源失败：{exc}") from exc
        if any(getattr(row, "trade_date", None) != trade_date for row in records):
            raise HTTPException(status_code=502, detail="涨停池记录日期与请求不一致，拒绝混日数据")
        rows = sorted(records, key=lambda r: (r.consecutive_boards or 0), reverse=True)
        return {
            "data": {"trade_date": trade_date.isoformat(), "pool": [r.model_dump(mode="json") for r in rows],
                     "reason_observations": _reason_observations(request.app.state, trade_date, rows)},
            "meta": await dated_meta(hub, trade_date),
        }

    async def read_up():
        hit, payload = await cache.get_or_set(trade_date, build)
        result = deepcopy(payload)
        result["meta"]["cached"] = hit
        return result

    if not include_breaks:
        return await read_up()

    break_cache = cache_on(request.app.state, "market.limit_break", 60, maxsize=8)

    async def build_breaks():
        method = getattr(hub.provider, "get_limit_break_pool", None)
        if method is None:
            raise NotImplementedError("当前数据链未提供炸板池")
        records = await read_pool_input(hub.provider, "get_limit_break_pool", trade_date)
        if not isinstance(records, list):
            raise ValueError("炸板池响应不是记录列表")
        rows = [row.model_dump(mode="json") for row in records]
        coverage = _membership_coverage(rows, trade_date)
        if coverage["state"] == "error":
            raise ValueError(coverage["error"])
        return rows

    async def read_breaks():
        _, rows = await break_cache.get_or_set(trade_date, build_breaks)
        return deepcopy(rows)

    async def combined():
        # Only domain reads have a TTL. Caching this assembled response would
        # restart an already cached domain's age and retain it for up to 119s.
        up, broken = await asyncio.gather(read_up(), read_breaks(), return_exceptions=True)
        for value in (up, broken):
            if isinstance(value, LocalReadCapacityExceeded):
                raise value
        if isinstance(up, BaseException):
            if isinstance(up, asyncio.CancelledError):
                raise up
            up_rows, reasons = [], _reason_observations(request.app.state, trade_date, [])
            up_coverage = {"state": "error", "error": str(getattr(up, "detail", up))}
            meta = await dated_meta(hub, trade_date)
        else:
            up_rows, reasons = up["data"]["pool"], up["data"]["reason_observations"]
            up_coverage = _membership_coverage(up_rows, trade_date)
            meta = up["meta"]
        if isinstance(broken, BaseException):
            if isinstance(broken, asyncio.CancelledError):
                raise broken
            break_rows = None
            break_coverage = {"state": "unknown" if isinstance(broken, NotImplementedError) else "error",
                              "error": str(broken)}
        else:
            break_coverage = _membership_coverage(broken, trade_date)
            break_rows = broken if break_coverage["state"] == "success" else None
        affirmative = {}
        if up_coverage["state"] == "success":
            affirmative["limit_up"] = up_rows
        if break_coverage["state"] == "success":
            affirmative["limit_break"] = break_rows
        return {"data": {
            "trade_date": trade_date.isoformat(), "pool": up_rows,
            "reason_observations": reasons, "break_pool": break_rows,
            "coverage": {"limit_up": up_coverage, "limit_break": break_coverage},
            "qualification_observations": _qualification_observations(request.app.state, trade_date, affirmative),
        }, "meta": {**meta, "qualification_scope": "same_day_source_observations_not_current_seal"}}

    return await combined()


@router.get("/limit-down", response_model=Envelope[LimitDownPoolPayload])
async def limit_down(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，默认最近交易日"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """跌停池。先核交易日身份，避免上游把休市请求回退成前一交易日。"""
    try:
        trade_date = date.fromisoformat(date_str) if date_str else await default_trade_date(hub)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="跌停池日期格式非法，需 YYYY-MM-DD") from exc
    try:
        await verify_limit_down_date(hub.provider, trade_date)
    except PoolDateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
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
