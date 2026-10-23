"""行情与 K 线：快照/单票、K 线、盘口、成交、分时、火花线。

（自 `market.py` 切出，2026-09-15 IMP-005 批 3。**只搬位置，未改逻辑**：
分片正文与原文件对应定义逐字相同。跨分片共用的信封辅助在 `market_envelope`。）
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.deps import get_hub
from app.core.ttl_cache import cache_on
from app.core.inflight_reads import read_inflight, read_policy
from app.core.config import settings
from app.data_quality.validator import validate_order_book
from app.schemas.envelope import (
    Envelope,
    KlinePayload,
    MinuteLinePayload,
    SparklineItem,
    SparklinePayload,
)
from app.market.trade_calendar import trading_days
from app.market.trading_status import bar_date, resolve_trading_status
from app.market.tdx_tick import fetch_trades_with_tdx_fallback, trades_failure_detail
from app.schemas.market import (
    OrderBook,
    Quote,
    Trade,
    TradingStatus,
    TradingStatusInfo,
)
from app.services.quote_hub import QuoteHub
from app.core.bjtime import beijing_now

router = APIRouter(tags=["market"])
log = logging.getLogger("app.api.routes.market")

from app.api.routes.market_envelope import (
    meta_payload,
)


def _index_member_quote_observations(items, snapshot_service, hub):
    """Join existing in-memory observations; never fetch or cache member prices."""
    now = datetime.now(timezone.utc)
    def clock(value):
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, str):
            try:
                result = datetime.fromisoformat(value)
            except ValueError:
                return None
        else:
            return None
        return result if result.tzinfo is not None and result <= now else None

    def number(value):
        return type(value) in (int, float) and math.isfinite(value)

    captured, state, rows = None, "unavailable", []
    if snapshot_service is not None:
        try:
            rows, captured = snapshot_service.versioned_snapshot()
            captured = clock(captured)
            freshness = snapshot_service.freshness()
            state = freshness.state if captured is not None and freshness.as_of == captured else "unknown"
        except Exception as exc:
            log.warning("member snapshot observation unavailable: %s", type(exc).__name__)
            rows, captured, state = [], None, "error"
    observed = {}
    duplicate = set()

    def observation(row, default_observed):
        price, prev, pct = row.get("price"), row.get("prev_close"), row.get("change_pct")
        source, quality = row.get("source"), row.get("quality")
        if (not number(price) or price <= 0 or not number(prev) or prev <= 0 or not number(pct)
                or not isinstance(source, str) or not source or source == "mock" or quality == "invalid"
                or quality is not None and not isinstance(quality, str)):
            return None
        supplied_clock = row.get("data_timestamp")
        source_clock = clock(supplied_clock)
        received_at = row.get("received_at")
        observed_at = clock(received_at) if received_at is not None else default_observed
        if (observed_at is None or supplied_clock is not None and source_clock is None
                or source_clock is not None and source_clock > observed_at):
            return None
        return {"change_pct": pct, "quote_source": source,
                "source_clock": source_clock.isoformat() if source_clock is not None else None,
                "quote_observed_at": observed_at.isoformat(),
                "quote_quality": "stale" if state == "stale" else quality if quality in {"high", "medium", "low", "stale"} else None}

    if captured is not None:
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol, market = row.get("symbol"), row.get("market")
            if (not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit()
                    or not isinstance(market, str) or market not in {"SH", "SZ", "BJ"}):
                continue
            key = (symbol, market)
            if key in observed:
                duplicate.add(key)
            observed[key] = observation(row, captured)
    for key in duplicate:
        observed[key] = None
    # The Hub is a passive fallback for members absent from this full-market
    # version. Unknown source clocks never justify replacing another version.
    if hub is not None:
        for quote in hub.get_quotes([item["symbol"] for item in items]):
            key = (quote.symbol, quote.market)
            if key not in observed and quote.data_timestamp is not None:
                observed[key] = observation(quote.model_dump(mode="python"), None)
    missing = {"change_pct": None, "quote_source": None, "source_clock": None,
               "quote_observed_at": None, "quote_quality": None}
    output = [{**item, **(observed.get((item["symbol"], item["market"])) or missing)} for item in items]
    known = [row for row in output if row["change_pct"] is not None]
    sources = {row["quote_source"] for row in known}
    clocks = [row["source_clock"] for row in known]
    qualities = {row["quote_quality"] for row in known}
    return output, {"known_count": len(known), "total": len(items),
                    "source": next(iter(sources)) if len(sources) == 1 else "mixed" if sources else None,
                    "source_clock": min(clocks, key=datetime.fromisoformat) if clocks and all(clocks) else None,
                    "observed_at": captured.isoformat() if captured is not None else None,
                    "quality": next(iter(qualities)) if len(qualities) == 1 and None not in qualities else "unknown",
                    "snapshot_state": state}


@router.get("/indices/{symbol}/members")
async def index_members(symbol: str, request: Request) -> dict:
    """Six displayed indices only. Cache membership, never security prices."""
    from app.market.indices import INDEX_CATALOG

    catalog = {market.lower() + code: (code + "." + market, name)
               for code, market, name in INDEX_CATALOG}
    if symbol not in catalog:
        raise HTTPException(status_code=400, detail="请选择带市场前缀的工作台指数")
    service = getattr(request.app.state, "theme_catalog", None)
    if service is None:
        raise HTTPException(status_code=503, detail="指数成分服务未就绪")
    code, name = catalog[symbol]
    cache = cache_on(request.app.state, "index.members", 6 * 3600, maxsize=len(catalog))
    try:
        hit, snapshot = await cache.get_or_set((symbol, beijing_now().date()), lambda: service.fetch_index_members(code))
    except Exception as exc:
        log.warning("index members unavailable %s: %s", symbol, type(exc).__name__)
        raise HTTPException(status_code=502, detail="指数成分读取失败，请稍后重试") from None
    items, quote_snapshot = _index_member_quote_observations(snapshot["items"],
        getattr(request.app.state, "snapshot_service", None), getattr(request.app.state, "hub", None))
    return {"data": {**snapshot, "items": items, "quote_snapshot": quote_snapshot,
                     "symbol": symbol, "name": name, "source": "ths", "cached": hit}}


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
            q = await target.get_quote(symbol)
            if q is not None:
                return {"data": hub.present_quote(q, symbol).model_dump(mode="json"), "meta": meta_payload(hub)}
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
        try:
            visible = hub.present_quote(live, symbol)
        except ValueError:
            raise HTTPException(status_code=502, detail="行情来源证券身份不匹配") from None
        return {"data": visible.model_dump(mode="json"), "meta": meta_payload(hub)}
    # 缓存命中：先拷贝再补价，enrich_quote 是原地修改，不能动共享缓存对象
    cached = await enrich_quote(hub.provider, found[0].model_copy())
    try:
        visible = hub.present_quote(cached, symbol)
    except ValueError:
        raise HTTPException(status_code=502, detail="行情来源证券身份不匹配") from None
    return {"data": visible.model_dump(mode="json"), "meta": meta_payload(hub)}


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
    async def load():
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
    key = ("chart.kline", id(hub), beijing_now().date(), symbol, timeframe, limit, start, end,
           read_policy(hub.provider, "get_kline"))
    return await read_inflight(hub.provider, key, load)


@router.get("/kline/{symbol}", response_model=Envelope[KlinePayload])
async def kline(
    symbol: str,
    timeframe: str = Query(default="1d", description="1m/5m/15m/30m/60m/1d/1w"),
    limit: int = Query(default=250, ge=1, le=1000),
    start: date | None = None,
    end: date | None = None,
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    from datetime import datetime
    from app.core.bjtime import BJ_TZ

    start_dt = datetime(start.year, start.month, start.day, tzinfo=BJ_TZ) if start else None
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=BJ_TZ) if end else None
    return await _kline_payload(hub, symbol, timeframe, limit, start_dt, end_dt)


@router.get("/order-book/{symbol}", response_model=Envelope[OrderBook])
async def order_book(symbol: str, hub: QuoteHub = Depends(get_hub)) -> dict:
    async def load():
        try:
            ob = await hub.provider.get_order_book(symbol)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"盘口数据源失败：{exc}")
        if ob is None:
            raise HTTPException(status_code=404, detail=f"{symbol} 无盘口数据")
        validate_order_book(ob)
        return {"data": ob.model_dump(mode="json"), "meta": meta_payload(hub)}
    key = ("chart.order-book", id(hub), beijing_now().date(), symbol, read_policy(hub.provider, "get_order_book"))
    return await read_inflight(hub.provider, key, load)


@router.get("/trades/{symbol}", response_model=Envelope[list[Trade]])
async def trades(symbol: str, limit: int = Query(default=50, ge=1, le=200), hub: QuoteHub = Depends(get_hub)) -> dict:
    """逐笔成交。provider 链（东财）为主源，**TDX 直连为降级备源**（`IMP-038`，2026-09-16）。

    为什么加备源：`get_trades` 此前是**单点**——链上 4 源里 ths / tencent / sina
    都是 `return []` 占位，唯一真源东财 `push2his` 被 WAF 拦（本机实测 3/3
    `ProviderError`，0.12–0.22s 快速失败），本端点恒 **502**。现由
    `app.market.tdx_tick` 兜底（单次 IPC median 20ms，复用连接）。

    状态码口径（`IMP-038` 收尾修正）：**只有真故障才 502**（`trades_failure_detail`
    非空）；两源都**没给出数据**（空 / 备源未启用）⇒ **200 + 空列表**，
    原因放 `meta.trades_detail`。"没数据"与"取数故障"是两件事，混用会让前端把
    源故障显示成"该股没有逐笔"（红线 2/3 同族）。

    ⚠️ 备源不是主源的理由见 `fetch_trades_with_tdx_fallback` 的 docstring
    （测试必须无网 + TDX 是直连旁路不进链治理 + 与既有 TDX 备源角色一致）。

    ⚠️ **口径**：TDX 给的是 **3 秒快照聚合**（非真实逐笔），东财是逐笔明细。
    实际来源由 `meta.trades_source` 与逐行 `Trade.source` 标注（tdx / eastmoney），
    前端据此显示口径——不要在 UI 上写死"逐笔"二字。

    IMP-079：链与TDX均无结果后，可启用有界腾讯HTTP分笔聚合后备；非L2，
    日期/陈旧状态来自源时间，第三路仍沿用上述故障与有效空集区分。
    """
    async def load():
        rows, source, detail = await fetch_trades_with_tdx_fallback(
            hub.provider.get_trades, symbol, limit=limit
        )
        # 只有**真故障**才 502；"两源都没给出数据"是 200 + 空列表 + 原因
        # （判据见 `trades_failure_detail`，**不是** `if detail:`——后者会把
        #  `chain: empty; tdx: disabled` 这类非故障状态也报成 502）。
        failed = trades_failure_detail(detail)
        if not rows and failed:
            raise HTTPException(status_code=502, detail=f"逐笔数据源失败：{failed}")
        rows = rows[-limit:]
        meta = meta_payload(hub)
        meta["trades_source"] = source
        if not rows:
            meta["trades_detail"] = detail or "两源均未给出数据"
        return {"data": [t.model_dump(mode="json") for t in rows], "meta": meta}
    key = ("chart.trades", id(hub), beijing_now().date(), symbol, limit,
           settings.trades_tdx_fallback_enabled, settings.trades_tencent_http_fallback_enabled,
           read_policy(hub.provider, "get_trades"))
    return await read_inflight(hub.provider, key, load)


@router.get("/minute-line/{symbol}", response_model=Envelope[MinuteLinePayload])
async def minute_line(symbol: str, hub: QuoteHub = Depends(get_hub)) -> dict:
    """当日 1 分钟分时（价格/成交量/累计成交额）。逐笔成交不可用时，这是盘中细粒度的替代口径。

    主源腾讯失败时降级 TDX 直连 m1 取最新交易日段（仅裸 6 位股票码；先例：
    2026-08-31 腾讯 WAF 封禁分时图真断过）。`vr_baseline_5m`：精确量比基线
    （最近 5 个完整交易日逐 5min 槽同期累计量均值，来自 TDX 落地历史）——
    前端量比优先用精确口径（cum_i / (baseline[slot]/5)），缺失时回退近似口径。
    """
    async def load():
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
    key = ("chart.minute-line", id(hub), beijing_now().date(), symbol, read_policy(hub.provider, "get_minute_line"))
    return await read_inflight(hub.provider, key, load)
