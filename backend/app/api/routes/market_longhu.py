"""龙虎榜与热度：龙虎榜（列表 / 详情 / 题材足迹）、飙升榜、人气榜趋势。

（自 `market.py` 切出，2026-09-15 IMP-005 批 3。**只搬位置，未改逻辑**：
分片正文与原文件对应定义逐字相同。跨分片共用的信封辅助在 `market_envelope`。）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, time as dt_time, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.deps import get_hub
from app.core.ttl_cache import cache_on
from app.schemas.envelope import (
    Envelope,
    LongHuPayload,
)
from app.market.trade_calendar import prev_trade_date, trading_days
from app.services.quote_hub import QuoteHub
from app.services.market_snapshot import (
    default_trade_date,
)
from app.core.bjtime import beijing_now, beijing_today

router = APIRouter(tags=["market"])
log = logging.getLogger("app.api.routes.market")

from app.api.routes.market_envelope import (
    meta_payload,
    dated_meta,
    latest_trade_date,
)


async def _prev_trade_date_async(hub, before: date) -> date | None:
    """before 之前的最近交易日（**走日历唯一入口 `trading_days`**；日历不可用退周末规则）。

    ⚠️ 原实现自建了与 `market_snapshot.default_trade_date` **共用**的 24h 日历缓存
    （`cache_on` 以 `(holder, name)` 为键挂在 hub 上 ⇒ 两处各写各的 TTL 实际只有**首次**
    生效）。那份缓存的失效形态见 `market_snapshot.default_trade_date` 的 docstring。
    2026-09-14 收口：**日历缓存只留 `trade_calendar` 一处**。
    """
    try:
        days = await trading_days(hub.provider)
        got = prev_trade_date(days, before) if days else None
        if got is not None:
            return got
    except Exception:  # noqa: BLE001  日历不可用不该让调用方 500
        log.warning("_prev_trade_date_async: trading calendar unavailable", exc_info=True)
    cand = before - timedelta(days=1)
    if cand.weekday() == 6:  # 周日
        cand -= timedelta(days=2)
    elif cand.weekday() == 5:  # 周六
        cand -= timedelta(days=1)
    return cand


@router.get("/market/heat/skyrocket")
async def market_skyrocket(
    request: Request,
    period: str = Query(default="day", description="统计周期：day / hour"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """飙升榜（ths 独有）——「正在变热」的更早信号，排名逻辑与热股榜不同。

    60s TTL：榜单一次拉齐 + 前端内存排序，轮询不回源。
    """
    if period not in ("day", "hour"):
        raise HTTPException(status_code=422, detail="period 仅允许 day / hour")

    cache = cache_on(request.app.state, "market.heat.skyrocket", 60, maxsize=2)
    key = (period,)

    async def _build() -> dict:
        try:
            rows = await hub.provider.get_skyrocket_list(period)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"飙升榜数据源失败：{exc}") from exc
        return {"rows": rows[:100], "period": period}

    _, payload = await cache.get_or_set(key, _build)
    return {"data": payload, "meta": meta_payload(hub)}


@router.get("/market/heat/rank-trend")
async def market_hot_rank_trend(
    request: Request,
    symbol: str = Query(description="6 位股票代码"),
    days: int = Query(default=30, ge=1, le=365, description="回看自然日数（官方窗口 ≤1 年）"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """单股热榜排名走势（ths 独有，官方服务器侧即历史库）。

    区间内未上榜的日期正常缺失；空集 = 该股区间内从未上榜（合法语义，note 说明）。
    """
    sym = symbol.strip()
    if not (len(sym) == 6 and sym.isdigit()):
        raise HTTPException(status_code=422, detail="symbol 须为 6 位股票代码")

    end = beijing_today()
    start = end - timedelta(days=days - 1)
    cache = cache_on(request.app.state, "market.heat.rank-trend", 300, maxsize=64)
    key = (sym, days)

    async def _build() -> dict:
        try:
            points = await hub.provider.get_hot_rank_trend(sym, start, end)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"排名走势数据源失败：{exc}") from exc
        return {
            "symbol": sym,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "points": points,
            "note": None if points else f"{sym} 在 {start.isoformat()}~{end.isoformat()} 未上榜（非故障）",
        }

    _, payload = await cache.get_or_set(key, _build)
    return {"data": payload, "meta": meta_payload(hub)}


@router.get("/longhu", response_model=Envelope[LongHuPayload])
async def longhu(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，默认最近交易日（T-1 盘后披露）"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    trade_date = date.fromisoformat(date_str) if date_str else await default_trade_date(hub)
    # 龙虎榜收盘后 ~17:00 才披露：当日 17:00 前且未显式指定日期时直接回退上一交易日。
    # 不先打当日"必空"请求——空结果会喂熔断器（四源全体进入冷却），拖累整条链。
    now_bj = beijing_now()
    if date_str is None and trade_date == now_bj.date() and now_bj.time().replace(tzinfo=None) < dt_time(17, 0):
        prev = await _prev_trade_date_async(hub, trade_date)
        if prev is not None:
            trade_date = prev
    try:
        records = await hub.provider.get_longhu_records(trade_date)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"龙虎榜数据源失败：{exc}")
    return {
        "data": {"trade_date": trade_date.isoformat(), "records": [r.model_dump(mode="json") for r in records]},
        "meta": await dated_meta(hub, trade_date),
    }


@router.get("/market/longhu/theme-trail")
async def longhu_theme_trail(
    request: Request,
    days: int = Query(default=5, ge=1, le=10, description="回看交易日数（≤10 控限流）"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """龙虎榜跨日题材轨迹（B3，官方场景 16 方法学）——资金近 N 日在题材间的轮动。

    - 仅日榜（range_days=1）参与聚合，三日榜跨口径绝不混用；
    - 概念等分守恒（官方建议口径，非真实拆分——note 显式标注）；
    - 逐日并发拉取（N≤10），单日失败降级跳过并在 degraded 显式列出；
    - 300s TTL：榜单 T-1 披露后不变，重复请求不回源。
    """
    import asyncio as _asyncio
    import bisect as _bisect

    from app.market.trade_calendar import trading_days
    from app.services.longhu_trail import aggregate_concept_trail

    cache = cache_on(request.app.state, "market.longhu.theme-trail", 300, maxsize=4)
    key = (days,)
    hit, payload = cache.get(key)
    if hit:
        return {"data": payload, "meta": meta_payload(hub)}

    anchor = await default_trade_date(hub)
    cal = await trading_days(hub.provider)
    i = _bisect.bisect_right(cal, anchor)
    window = cal[max(0, i - days) : i]  # 升序近 N 个交易日（含最近已披露日）
    if not window:
        raise HTTPException(status_code=503, detail="交易日历不可用，无法定位区间")

    results = await _asyncio.gather(
        *(hub.provider.get_longhu_records(d) for d in window), return_exceptions=True
    )
    daily: list[tuple[date, list]] = []
    degraded: list[str] = []
    for d, res in zip(window, results):
        if isinstance(res, BaseException):
            degraded.append(f"{d.isoformat()}: {res}")
        else:
            daily.append((d, res))
    if not daily:
        raise HTTPException(status_code=502, detail=f"龙虎榜近 {days} 日全部拉取失败：{degraded}")

    payload = {
        "days": [d.isoformat() for d, _ in daily],
        "trail": aggregate_concept_trail(daily),
        "degraded": degraded,
        "note": "概念等分守恒口径（单股净额按概念数均摊，非真实拆分）；仅统计日榜（range_days=1）",
    }
    cache.set(key, payload)
    return {"data": payload, "meta": meta_payload(hub)}


@router.get("/longhu/{symbol}")
async def longhu_detail(
    symbol: str,
    date_str: str | None = Query(default=None, alias="date"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """个股龙虎榜：当日席位明细（买5/卖5+类型识别）+ 上榜历史（含 T+1/3/5/10 表现）。"""
    trade_date = date.fromisoformat(date_str) if date_str else await latest_trade_date(hub)

    async def _detail():
        try:
            return await hub.provider.get_longhu_detail(symbol, trade_date)
        except Exception as exc:
            log.warning("longhu detail %s: %s", symbol, exc)
            return {"symbol": symbol, "trade_date": trade_date.isoformat(), "buy_seats": [], "sell_seats": [], "empty": True}

    async def _history():
        """历史榜单独立兜底（S2-12 冒烟实测修）。

        原先这里是 `gather(_detail(), hub.provider.get_longhu_history(symbol))`——
        detail 有 try/except、**history 裸调用**，两侧健壮性不对称：
        只要 provider 缺 `get_longhu_history`（切换 provider / 降级到无该能力的源），
        `AttributeError` 就穿透到路由层变 **500**；而且 gather 一失败，
        `_detail()` 的协程**从未被 await**（RuntimeWarning: coroutine was never awaited）。
        两路各自兜底后，单路失败不再拖垮整条，也不再有悬空协程。
        """
        try:
            return await hub.provider.get_longhu_history(symbol)
        except Exception as exc:
            log.warning("longhu history %s: %s", symbol, exc)
            return []

    detail, history = await asyncio.gather(_detail(), _history())
    win = [h for h in history if (h.get("after_5d") is not None)]
    stats = {
        "count": len(history),
        "avg_after_5d": round(sum(h["after_5d"] for h in win) / len(win), 2) if win else None,
        "win_rate_5d": round(sum(1 for h in win if h["after_5d"] > 0) / len(win), 3) if win else None,
    }
    return {"data": {"detail": detail, "history": history, "stats": stats}, "meta": meta_payload(hub)}
