"""行情与 K 线：快照/单票、K 线、盘口、成交、分时、火花线与盘中信号/决策。

（自 `market.py` 切出，2026-09-15 IMP-005 批 3。**只搬位置，未改逻辑**：
分片正文与原文件对应定义逐字相同。跨分片共用的信封辅助在 `market_envelope`。）
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.deps import get_hub
from app.core.ttl_cache import cache_on
from app.data_quality.validator import validate_order_book
from app.schemas.envelope import (
    Envelope,
    KlinePayload,
    MinuteDecisionsPayload,
    MinuteLinePayload,
    MinuteSignalsPayload,
    SparklineItem,
    SparklinePayload,
)
from app.market.trade_calendar import trading_days
from app.market.trading_status import bar_date, resolve_trading_status
from app.schemas.market import (
    OrderBook,
    Quote,
    Trade,
    TradingStatus,
    TradingStatusInfo,
    utcnow,
)
from app.services.quote_hub import QuoteHub
from app.core.bjtime import beijing_now

router = APIRouter(tags=["market"])
log = logging.getLogger("app.api.routes.market")

from app.api.routes.market_envelope import (
    meta_payload,
)


@router.get("/sparkline", response_model=Envelope[SparklinePayload])
async def sparkline(
    request: Request,
    hub: QuoteHub = Depends(get_hub),
    symbols: str = Query(description="逗号分隔的 6 位代码，最多 50 只"),
    days: int = Query(default=30, ge=10, le=90),
    period: str = Query(default="daily", description="daily=近 N 日日K收盘 | minute=当日 1 分钟分时价格"),
) -> dict:
    """自选列表迷你走势（retro #9）：批量收盘序列（日K 或当日分时，二选一）。

    period=daily（默认）：近 N 日 TDX 日K收盘，进程内缓存 5 分钟（日K 级别无需更短）。
    period=minute：当日 1 分钟分时价格（2026-09-07 用户要求列表迷你图展示当日分时），
    与 /minute-line 同源同口径（composite 腾讯主源 + TDX 备源），盘外返回最近
    交易日全天序列；缓存 60s（分钟级数据，前端 60s 轮询打缓存兜底）。
    单只拉取失败直接跳过（不臆造）。
    """
    syms = [s.strip().zfill(6) for s in symbols.split(",") if s.strip()]
    syms = [s for s in syms if s.isdigit() and len(s) == 6][:50]
    if not syms:
        raise HTTPException(status_code=400, detail="symbols 非法")
    if period not in ("daily", "minute"):
        raise HTTPException(status_code=400, detail="period 仅支持 daily/minute")

    cache = cache_on(request.app.state, "market.sparkline", 300 if period == "daily" else 60, maxsize=64)
    key = (tuple(syms), days if period == "daily" else 0, period)
    hit, payload = cache.get(key)
    if hit:
        # model_copy 标注 cached，不改共享缓存对象
        return {"data": payload.model_copy(update={"cached": True}), "meta": meta_payload(hub)}

    if period == "minute":
        items = await _minute_sparkline_items(hub, syms)
    else:
        from app.market.tdx_kline import tdx_daily_bars

        # TDX 日K 是同步文件读（评审 B4）：50 只串行最坏 50 次磁盘 IO 卡事件循环，
        # 丢线程池并行（Semaphore 限 8，避免一次性打开过多 TDX 文件句柄）
        sem = asyncio.Semaphore(8)

        async def load_one(sym: str) -> SparklineItem | None:
            async with sem:
                try:
                    bars = await asyncio.to_thread(tdx_daily_bars, sym, days + 2)
                except Exception as exc:
                    log.warning("sparkline %s failed: %s", sym, exc)
                    return None
            closes = [b["close"] for b in (bars or [])][-days:]
            if len(closes) < 5 or not closes[0]:
                return None
            return SparklineItem(
                symbol=sym,
                closes=closes,
                period_change_pct=round((closes[-1] / closes[0] - 1) * 100, 2),
            )

        items = [it for it in await asyncio.gather(*(load_one(s) for s in syms)) if it is not None]

    payload = SparklinePayload(items=items)
    cache.set(key, payload)
    return {"data": payload, "meta": meta_payload(hub)}


async def _minute_sparkline_items(hub, syms: list[str]) -> list[SparklineItem]:
    """minute 模式：逐只当日分时价格序列（composite 自带腾讯主源 + TDX 备源）。

    Semaphore 限 8：自选集合整批并发直打腾讯分时端点，N 大时可能触发 WAF，
    与日K 模式的并发纪律一致；单只失败跳过（不臆造），主源异常由 composite 切源。
    """
    sem = asyncio.Semaphore(8)

    async def load_one(sym: str) -> SparklineItem | None:
        async with sem:
            try:
                points = await hub.provider.get_minute_line(sym)
            except Exception as exc:
                log.warning("sparkline(minute) %s failed: %s", sym, exc)
                return None
        closes = [p["price"] for p in points if p.get("price") is not None]
        if len(closes) < 5 or not closes[0]:
            return None
        # 口径说明：minute 模式的 period_change_pct 是「相对当日开盘」的变动
        # （daily 模式才是区间涨跌）；前端列表行定色用的是行情 change_pct（vs 昨收），
        # 不消费该字段——两口径不混用。
        return SparklineItem(
            symbol=sym,
            closes=closes,
            period_change_pct=round((closes[-1] / closes[0] - 1) * 100, 2),
        )

    return [it for it in await asyncio.gather(*(load_one(s) for s in syms)) if it is not None]


@router.get("/quotes", response_model=Envelope[list[Quote]])
async def quotes(
    symbols: str | None = Query(default=None, description="逗号分隔的股票代码"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    wanted = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    data = hub.get_quotes(wanted)
    return {"data": [q.model_dump(mode="json") for q in data], "meta": meta_payload(hub)}


@router.get("/quotes/{symbol}", response_model=Envelope[Quote])
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
                return {"data": validate_quote(q).model_dump(mode="json"), "meta": meta_payload(hub)}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{source} 行情失败：{exc}")
    # P1-4（2026-09-11）：原来串 `fill_valuation(fill_limit_prices(...))` 会让同一
    # symbol 连发两次腾讯 HTTP（两个函数各自 get_quote，而腾讯快照无缓存）。
    # enrich_quote 取一次数补两类字段，字段映射与原函数共用同一份实现。
    from app.services.quote_enrich import enrich_quote

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
        # ths 快照缺涨跌停价，从腾讯补齐——撮合与前端拒单提示都依赖它；
        # 同理缺 pe/pb/市值（2026-09-01：详情行情条 PE 不再缺失）
        live = await enrich_quote(hub.provider, live)
        return {"data": validate_quote(live).model_dump(mode="json"), "meta": meta_payload(hub)}
    # 缓存命中：先拷贝再补价，enrich_quote 是原地修改，不能动共享缓存对象
    cached = await enrich_quote(hub.provider, found[0].model_copy())
    return {"data": cached.model_dump(mode="json"), "meta": meta_payload(hub)}


async def _trading_status_payload(hub: QuoteHub, bars, timeframe: str) -> dict | None:
    """停牌判定载荷。仅日线有值；判定本身零额外网络请求（bars 已在手上）。

    日历拉取失败时**照常返回 unknown + 原因**，不吞掉异常、不回退成 trading——
    静默退化会让前端把"不知道"渲染成"正常交易"。
    """
    if timeframe != "1d":
        return None
    if not bars:
        return TradingStatusInfo(
            status=TradingStatus.unknown,
            reason="无日K数据（数据源不可用，或该标的尚未上市/已退市）",
        ).model_dump(mode="json")
    try:
        days = await trading_days(hub.provider)
    except Exception as exc:
        log.warning("trading calendar unavailable for status: %s", exc)
        return TradingStatusInfo(
            status=TradingStatus.unknown, reason=f"交易日历不可用：{exc}",
        ).model_dump(mode="json")
    info = resolve_trading_status(
        [bar_date(b.ts) for b in bars], days, now=beijing_now()
    )
    return info.model_dump(mode="json")


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
            "trading_status": await _trading_status_payload(hub, bars, timeframe),
        },
        "meta": meta_payload(hub),
    }


@router.get("/kline/{symbol}", response_model=Envelope[KlinePayload])
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


@router.get("/order-book/{symbol}", response_model=Envelope[OrderBook])
async def order_book(symbol: str, hub: QuoteHub = Depends(get_hub)) -> dict:
    try:
        ob = await hub.provider.get_order_book(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"盘口数据源失败：{exc}")
    if ob is None:
        raise HTTPException(status_code=404, detail=f"{symbol} 无盘口数据")
    validate_order_book(ob)
    return {"data": ob.model_dump(mode="json"), "meta": meta_payload(hub)}


@router.get("/trades/{symbol}", response_model=Envelope[list[Trade]])
async def trades(symbol: str, limit: int = Query(default=50, ge=1, le=200), hub: QuoteHub = Depends(get_hub)) -> dict:
    try:
        rows = await hub.provider.get_trades(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"逐笔数据源失败：{exc}")
    rows = rows[-limit:]
    return {"data": [t.model_dump(mode="json") for t in rows], "meta": meta_payload(hub)}


@router.get("/minute-line/{symbol}", response_model=Envelope[MinuteLinePayload])
async def minute_line(symbol: str, hub: QuoteHub = Depends(get_hub)) -> dict:
    """当日 1 分钟分时（价格/成交量/累计成交额）。逐笔成交不可用时，这是盘中细粒度的替代口径。

    主源腾讯失败时降级 TDX 直连 m1 取最新交易日段（仅裸 6 位股票码；先例：
    2026-08-31 腾讯 WAF 封禁分时图真断过）。`vr_baseline_5m`：精确量比基线
    （最近 5 个完整交易日逐 5min 槽同期累计量均值，来自 TDX 落地历史）——
    前端量比优先用精确口径（cum_i / (baseline[slot]/5)），缺失时回退近似口径。
    """
    try:
        points = await hub.provider.get_minute_line(symbol)
    except Exception as exc:
        from app.market.minute_backfill import tdx_minute_line_fallback

        try:
            points = await asyncio.to_thread(tdx_minute_line_fallback, symbol)
            log.warning("minute-line 主源失败，TDX 备源接管 %s（主源错误：%s）", symbol, exc)
        except Exception as tdx_exc:
            raise HTTPException(
                status_code=502,
                detail=f"分时数据源失败：主源 {exc}；TDX 备源 {tdx_exc}",
            ) from tdx_exc
    baseline = None
    try:
        from app.market.minute_backfill import load_vr_baseline

        # Parquet 读是同步阻塞（评审 B5）：与同文件 themes 路由 986 行同一纪律——
        # 必须丢线程池，否则卡死事件循环
        baseline = await asyncio.to_thread(load_vr_baseline, symbol)
    except Exception as exc:
        log.warning("vr baseline failed for %s: %s", symbol, exc)
    return {"data": {"symbol": symbol, "points": points, "vr_baseline_5m": baseline}, "meta": meta_payload(hub)}


@router.get("/market/minute-signals/{symbol}", response_model=Envelope[MinuteSignalsPayload])
async def minute_signals(symbol: str, hub: QuoteHub = Depends(get_hub)) -> dict:
    """做 T 偏向信号（分钟级）+ **触发即记录**到决策库（P1-24 接线）。

    记录是**幂等日志副作用**：引擎前缀稳定 ⇒ 同一根 bar 重算结果相同，
    去重键 ``(symbol, trigger_ts)`` 保证重复请求不产生第二条 —— 因此这里
    用 GET 读写同一资源是可接受的（原模块 docstring 的设计即如此：
    "触发即记录：signals 接口产出越过阈值的信号时落库"）。

    ``degraded`` 如实透传降级原因（缺昨日量 → 量比条件降级；缺波动率 →
    阈值不自适应）。**不补假数据**：宁可降级并标注，不用别的口径顶上。
    """
    from app.core.db import get_session_factory
    from app.market import minute_decisions as md
    from app.market.minute_signals import compute_minute_signals

    try:
        points = await hub.provider.get_minute_line(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"分时数据源失败：{exc}") from exc

    out = compute_minute_signals(points)
    signals = list(out["signals"])  # 引擎已 dump 成 dict，勿再 .model_dump()（2026-09-10 实测踩坑）
    # 只在信号非空时才动库（空列表不会产生写入，省一次连接与事务）
    recorded = (
        await asyncio.to_thread(md.record_signals, get_session_factory(), symbol, signals)
        if signals
        else 0
    )
    return {
        "data": {
            "symbol": symbol,
            "signals": signals,
            "observed": out.get("observed", 0),
            "degraded": list(out.get("degraded") or []),
            "recorded": recorded,
            "basis": out.get("basis") or {},
        },
        "meta": meta_payload(hub),
    }


@router.get("/market/minute-decisions", response_model=Envelope[MinuteDecisionsPayload])
async def minute_decisions(
    symbol: str | None = Query(None, description="按标的过滤；缺省=全部"),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """做 T 决策库（记录 → 结算 → 错误归因）。读时**惰性结算**到期的 open 记录。

    惰性结算的口径：只在读取时推进，不另起常驻任务（原模块设计如此）；
    盘后还有一次批量结算挂在 15:35 复调度的收盘分支（`scan_and_settle_today`），
    保证"没人看页面时样本也会累积"。
    """
    from app.core.db import get_session_factory
    from app.market import minute_decisions as md

    sf = get_session_factory()
    settled = 0
    # 收盘后才有完整窗口可结算；盘中也允许（到期的那部分能算）
    if beijing_now().hour >= 12:
        with contextlib.suppress(Exception):
            settled = await asyncio.to_thread(md.settle_due, sf, md.tdx_points, symbol)
    items = await asyncio.to_thread(md.list_decisions, sf, symbol, limit)
    outcomes: dict[str, int] = {}
    for it in items:
        key = it.get("outcome") or "open"
        outcomes[key] = outcomes.get(key, 0) + 1
    return {
        "data": {
            "items": items,
            "settled": settled,
            "outcomes": outcomes,
            "open_count": outcomes.get("open", 0),
            "note": (
                "结算门槛 8bp（≈2×交易成本）；correct=偏向方向最优价差达标，"
                "wrong=反向不利价差达标，invalid=窗口内未达任何阈值（消耗注意力但没钱），"
                "expired=窗口数据不足不判定。错误归因用 leave-one-out（剔除哪个指标会翻转结论）。"
            ),
        },
        "meta": {"generated_at": utcnow().isoformat()},
    }
