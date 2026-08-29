from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_hub
from app.data_quality.validator import validate_order_book
from app.schemas.market import utcnow
from app.services.quote_hub import QuoteHub

log = logging.getLogger(__name__)
router = APIRouter(tags=["market"])


def _meta(hub: QuoteHub) -> dict:
    return {
        "provider": hub.provider.name,
        "is_realtime": bool(getattr(hub.provider, "realtime", False)) and not hub.is_stale(),
        "is_stale": hub.is_stale(),
        "last_success_refresh": hub.last_success_refresh.isoformat() if hub.last_success_refresh else None,
        "generated_at": utcnow().isoformat(),
    }


@router.get("/market/sentiment")
async def market_sentiment(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    """情绪周期判定（§5.5）：阶段+温度+指标依据+置信度+误判原因+切换条件。结果缓存 60s。"""
    import time as _time

    from app.sentiment.engine import compute_sentiment
    from datetime import timedelta

    svc = request.app.state.snapshot_service
    if svc.breadth is None:
        raise HTTPException(status_code=503, detail="全市场快照尚未就绪")
    cache = getattr(request.app.state, "_sent_cache", None)
    if cache and _time.time() - cache[0] < 60:
        return cache[1]

    today = date.today()
    yesterday = today - timedelta(days=2 if today.weekday() == 0 else 1)  # 跳过周一的周末

    async def _pool(d: date) -> list:
        try:
            return await hub.provider.get_limit_up_pool(d)
        except Exception as exc:
            log.warning("sentiment pool %s failed: %s", d, exc)
            return []

    pool_today, pool_yesterday = await asyncio.gather(_pool(today), _pool(yesterday))
    result = compute_sentiment(svc.breadth, pool_today, pool_yesterday, svc.snapshot)
    payload = {
        "data": {
            **result,
            "pool_today_count": len(pool_today),
            "pool_yesterday_count": len(pool_yesterday),
        },
        "meta": _meta(hub),
    }
    request.app.state._sent_cache = (_time.time(), payload)
    return payload


@router.get("/market/breadth")
async def market_breadth(request: Request) -> dict:
    """市场宽度：涨跌家数、涨跌停家数、两市成交额（来源：新浪全市场快照）。"""
    svc = request.app.state.snapshot_service
    payload = svc.breadth_payload()
    if payload["breadth"] is None:
        raise HTTPException(status_code=503, detail="全市场快照尚未就绪（冷启动抓取约需数秒）")
    return {"data": payload, "meta": _meta(request.app.state.hub)}


@router.get("/market/overview")
async def market_overview(hub: QuoteHub = Depends(get_hub)) -> dict:
    """指数行情 + 两市成交额合计。市场宽度/情绪等指标按开发顺序在后续阶段接入。"""
    indices = hub.get_indices()
    total_amount = sum(q.amount or 0 for q in indices if q.market in {"SH", "SZ"})
    return {
        "data": {
            "indices": [q.model_dump(mode="json") for q in indices],
            "total_amount": round(total_amount, 2),
        },
        "meta": _meta(hub),
    }


@router.get("/quotes")
async def quotes(
    symbols: str | None = Query(default=None, description="逗号分隔的股票代码"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    wanted = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    data = hub.get_quotes(wanted)
    return {"data": [q.model_dump(mode="json") for q in data], "meta": _meta(hub)}


@router.get("/quotes/{symbol}")
async def quote(
    symbol: str,
    source: str | None = Query(default=None, description="指定数据源（如 tencent，用于补估值字段）"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    if source:
        chain = hub.provider if hasattr(hub.provider, "providers") else None
        target = next((p for p in (chain.providers if chain else [hub.provider]) if p.name == source), None)
        if target is None:
            raise HTTPException(status_code=400, detail=f"source {source} 不在链中")
        try:
            from app.data_quality.validator import validate_quote

            q = await target.get_quote(symbol)
            if q is not None:
                return {"data": validate_quote(q).model_dump(mode="json"), "meta": _meta(hub)}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{source} 行情失败：{exc}")
    found = hub.get_quotes([symbol])
    if not found:
        # 非自选股：实时经 Provider 链拉取（不进缓存，质量校验照常）
        import logging as _log

        from app.data_quality.validator import validate_quote

        try:
            live = await hub.provider.get_quote(symbol)
        except Exception as exc:
            _log.getLogger(__name__).warning("live quote %s failed: %s", symbol, exc)
            live = None
        if live is None:
            raise HTTPException(status_code=404, detail=f"{symbol} 无行情（缓存与数据源均未命中）")
        return {"data": validate_quote(live).model_dump(mode="json"), "meta": _meta(hub)}
    return {"data": found[0].model_dump(mode="json"), "meta": _meta(hub)}


async def _kline_payload(hub: QuoteHub, symbol: str, timeframe: str, limit: int, start, end) -> dict:
    try:
        bars = await hub.provider.get_kline(symbol, timeframe, start, end)
    except Exception as exc:
        log.warning("kline failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=502, detail=f"K线数据源失败：{exc}")
    bars = bars[-limit:]
    return {
        "data": {
            "symbol": symbol,
            "timeframe": timeframe,
            "bars": [b.model_dump(mode="json") for b in bars],
        },
        "meta": _meta(hub),
    }


@router.get("/kline/{symbol}")
async def kline(
    symbol: str,
    timeframe: str = Query(default="1d", description="1m/5m/15m/30m/60m/1d/1w"),
    limit: int = Query(default=250, ge=1, le=1000),
    start: date | None = None,
    end: date | None = None,
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    from datetime import datetime, timezone as tz

    start_dt = datetime(start.year, start.month, start.day, tzinfo=tz.utc) if start else None
    end_dt = datetime(end.year, end.month, end.day, 23, 59, tzinfo=tz.utc) if end else None
    return await _kline_payload(hub, symbol, timeframe, limit, start_dt, end_dt)


@router.get("/order-book/{symbol}")
async def order_book(symbol: str, hub: QuoteHub = Depends(get_hub)) -> dict:
    try:
        ob = await hub.provider.get_order_book(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"盘口数据源失败：{exc}")
    if ob is None:
        raise HTTPException(status_code=404, detail=f"{symbol} 无盘口数据")
    validate_order_book(ob)
    return {"data": ob.model_dump(mode="json"), "meta": _meta(hub)}


@router.get("/trades/{symbol}")
async def trades(symbol: str, limit: int = Query(default=50, ge=1, le=200), hub: QuoteHub = Depends(get_hub)) -> dict:
    try:
        rows = await hub.provider.get_trades(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"逐笔数据源失败：{exc}")
    rows = rows[-limit:]
    return {"data": [t.model_dump(mode="json") for t in rows], "meta": _meta(hub)}


@router.get("/minute-line/{symbol}")
async def minute_line(symbol: str, hub: QuoteHub = Depends(get_hub)) -> dict:
    """当日 1 分钟分时（价格/成交量/累计成交额）。逐笔成交不可用时，这是盘中细粒度的替代口径。"""
    try:
        points = await hub.provider.get_minute_line(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"分时数据源失败：{exc}")
    return {"data": {"symbol": symbol, "points": points}, "meta": _meta(hub)}


async def _default_trade_date_async(hub) -> date:
    """最近交易日：优先官方交易日历（ths，缓存 24h），失败回退周末规则。"""
    import time as _time

    cache = getattr(hub, "_tdays_cache", None)
    days: list[str] | None = None
    if cache and _time.time() - cache[0] < 86400:
        days = cache[1]
    if days is None:
        for p in hub.providers if hasattr(hub, "providers") else [hub.provider]:
            if hasattr(p, "get_trading_days"):
                try:
                    days = await p.get_trading_days()
                    setattr(hub, "_tdays_cache", (_time.time(), days))
                    break
                except Exception:
                    continue
    if days:
        today = date.today().strftime("%Y%m%d")
        past = [d for d in days if d <= today]
        if past:
            latest = past[-1]
            return date(int(latest[:4]), int(latest[4:6]), int(latest[6:]))
    d = date.today()
    return {5: d - timedelta(days=1), 6: d - timedelta(days=2)}.get(d.weekday(), d)


def _default_trade_date() -> date:
    """周末回退规则（交易日历不可用时的兜底）。"""
    d = date.today()
    return {5: d - timedelta(days=1), 6: d - timedelta(days=2)}.get(d.weekday(), d)


@router.get("/limit-up")
async def limit_up(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，默认最近交易日"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    trade_date = date.fromisoformat(date_str) if date_str else await _default_trade_date_async(hub)
    try:
        records = await hub.provider.get_limit_up_pool(trade_date)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"涨停池数据源失败：{exc}")
    records.sort(key=lambda r: (r.consecutive_boards or 0), reverse=True)
    return {
        "data": {"trade_date": trade_date.isoformat(), "pool": [r.model_dump(mode="json") for r in records]},
        "meta": _meta(hub),
    }


@router.get("/longhu")
async def longhu(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，默认最近交易日（T-1 盘后披露）"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    trade_date = date.fromisoformat(date_str) if date_str else await _default_trade_date_async(hub)
    try:
        records = await hub.provider.get_longhu_records(trade_date)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"龙虎榜数据源失败：{exc}")
    return {
        "data": {"trade_date": trade_date.isoformat(), "records": [r.model_dump(mode="json") for r in records]},
        "meta": _meta(hub),
    }


@router.get("/boards")
async def boards(
    type: str = Query(default="hangye", description="hangye(行业) | concept(概念)"),
    request: Request = None,
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """板块排行：涨跌幅/成交额/领涨股（新浪闪电排行，一次请求全量）。结果缓存 60s。"""
    import time as _time

    key = f"_boards_cache_{type}"
    cache = getattr(request.app.state, key, None)
    if cache and _time.time() - cache[0] < 60:
        return cache[1]
    try:
        rows = await hub.provider.get_board_rankings(type)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"板块数据源失败：{exc}")
    rows.sort(key=lambda r: (r.get("change_pct") or 0), reverse=True)
    payload = {"data": {"type": type, "boards": rows}, "meta": _meta(hub)}
    setattr(request.app.state, key, (_time.time(), payload))
    return payload


@router.get("/longhu/{symbol}")
async def longhu_detail(
    symbol: str,
    date_str: str | None = Query(default=None, alias="date"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """个股龙虎榜：当日席位明细（买5/卖5+类型识别）+ 上榜历史（含 T+1/3/5/10 表现）。"""
    trade_date = date.fromisoformat(date_str) if date_str else _default_trade_date()

    async def _detail():
        try:
            return await hub.provider.get_longhu_detail(symbol, trade_date)
        except Exception as exc:
            log.warning("longhu detail %s: %s", symbol, exc)
            return {"symbol": symbol, "trade_date": trade_date.isoformat(), "buy_seats": [], "sell_seats": [], "empty": True}

    detail, history = await asyncio.gather(_detail(), hub.provider.get_longhu_history(symbol))
    win = [h for h in history if (h.get("after_5d") is not None)]
    stats = {
        "count": len(history),
        "avg_after_5d": round(sum(h["after_5d"] for h in win) / len(win), 2) if win else None,
        "win_rate_5d": round(sum(1 for h in win if h["after_5d"] > 0) / len(win), 3) if win else None,
    }
    return {"data": {"detail": detail, "history": history, "stats": stats}, "meta": _meta(hub)}


@router.get("/capital-flow/{symbol}")
async def capital_flow(
    symbol: str,
    days: int = Query(default=30, ge=1, le=100),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """个股资金流（新浪口径：主力=超大单+大单；按单笔成交额四级拆分，见页面口径说明）。"""
    try:
        rows = await hub.provider.get_capital_flow(symbol, days)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"资金流数据源失败：{exc}")
    streak = 0
    for r in rows:  # rows 按日期倒序
        if (r.get("net_main") or 0) > 0:
            streak += 1
        else:
            break
    return {
        "data": {
            "symbol": symbol,
            "days": len(rows),
            "flow": rows,
            "streak_in": streak,
            "definition": "主力净流入 = 超大单净额 + 大单净额（新浪按单笔成交金额划分：≥50万股或100万元视为大单级别，具体阈值为新浪口径，属估算数据非交易所披露）",
        },
        "meta": _meta(hub),
    }


@router.get("/financials/{symbol}")
async def financials(symbol: str, periods: int = Query(default=8, ge=1, le=20), hub: QuoteHub = Depends(get_hub)) -> dict:
    """财务摘要（东财业绩报表：营收/净利/同比/毛利率/ROE/EPS，按报告期倒序）。"""
    try:
        rows = await hub.provider.get_financials(symbol, periods)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"财务数据源失败：{exc}")
    return {"data": {"symbol": symbol, "periods": rows}, "meta": _meta(hub)}


@router.get("/limit-break")
async def limit_break(
    date_str: str | None = Query(default=None, alias="date"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """炸板池（涨停后开板未回封）。"""
    trade_date = date.fromisoformat(date_str) if date_str else await _default_trade_date_async(hub)
    try:
        rows = await hub.provider.get_limit_break_pool(trade_date)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"炸板池数据源失败：{exc}")
    return {"data": {"trade_date": trade_date.isoformat(), "pool": [r.model_dump(mode="json") for r in rows]}, "meta": _meta(hub)}


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=20), hub: QuoteHub = Depends(get_hub)) -> dict:
    from app.data_providers.mock import MockProvider

    try:
        items = await hub.provider.search(q)
    except Exception as exc:
        log.warning("search failed: %s", exc)
        items = []
    if not items:
        fallback = MockProvider()
        items = await fallback.search(q)
    return {"data": [i.model_dump() for i in items], "meta": _meta(hub)}
