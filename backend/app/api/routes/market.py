from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_hub
from app.core.ttl_cache import cache_on
from app.data_quality.validator import validate_order_book
from app.schemas.envelope import (
    AuctionBenchmarkItem,
    AuctionSnapshot,
    AdjustmentEvent,
    BreadthData,
    Envelope,
    KlinePayload,
    LimitUpPoolPayload,
    LongHuPayload,
    MinuteLinePayload,
    OverviewPayload,
    SentimentHistoryItem,
    SentimentHistoryPayload,
    SentimentPayload,
    SparklineItem,
    SparklinePayload,
    ThemeBoardPayload,
)
from app.schemas.market import (
    OrderBook,
    Quote,
    SymbolSearchItem,
    Trade,
    utcnow,
)
from app.services.quote_hub import QuoteHub
from app.services.speed_sampler import SpeedSampler
from app.services.dragon_service import apply_position_with_5d
from app.services.theme_catalog_service import official_multi_day_changes

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


@router.get("/market/sentiment", response_model=Envelope[SentimentPayload])
async def market_sentiment(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    """情绪周期判定（§5.5）：阶段+温度+指标依据+置信度+误判原因+切换条件+次日验证项。

    计算逻辑在 `app.services.market_context.compute_market_sentiment`，
    与复盘 Agent 共用同一实现——口径只有一个，避免两边漂移。
    结果缓存 60s。
    """
    from app.services.market_context import CalendarUnavailable, compute_market_sentiment

    svc = request.app.state.snapshot_service
    cache = cache_on(request.app.state, "market.sentiment", 60, maxsize=1)

    async def _build() -> dict:
        try:
            result = await compute_market_sentiment(hub, svc)
        except CalendarUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"data": result, "meta": _meta(hub)}

    _, payload = await cache.get_or_set((), _build)
    return payload


_sent_hist_backfilled = {"done": False}


@router.get("/market/ladder-check")
async def ladder_check(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    """B4 数据源自证：ths 连板天梯 seal_nextday 交叉验证自算晋级率。

    可验 2进3 与高位存活（首板不在天梯，1进2 不可验）。逐日给出
    match/drift/insufficient，drift 说明两日池拼接逻辑有问题（服务端同时 warning）。
    结果缓存 10 分钟（数据日频更新）。
    """
    from app.sentiment.ladder_check import run_ladder_check

    cache = cache_on(request.app.state, "sentiment.ladder_check", 600, maxsize=1)
    hit, payload = cache.get(())
    if hit:
        return payload
    try:
        data = await run_ladder_check(hub)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"天梯交叉验证失败：{exc}") from exc
    payload = {"data": data, "meta": _meta(hub)}
    cache.set((), payload)
    return payload


@router.get("/sparkline", response_model=Envelope[SparklinePayload])
async def sparkline(
    request: Request,
    hub: QuoteHub = Depends(get_hub),
    symbols: str = Query(description="逗号分隔的 6 位代码，最多 50 只"),
    days: int = Query(default=30, ge=10, le=90),
) -> dict:
    """自选列表迷你走势（retro #9）：批量 TDX 日K 收盘序列。

    进程内缓存 5 分钟（日K 级别无需更短）；单只拉取失败直接跳过（不臆造）。
    """
    syms = [s.strip().zfill(6) for s in symbols.split(",") if s.strip()]
    syms = [s for s in syms if s.isdigit() and len(s) == 6][:50]
    if not syms:
        raise HTTPException(status_code=400, detail="symbols 非法")

    from app.market.tdx_kline import tdx_daily_bars

    cache = cache_on(request.app.state, "market.sparkline", 300, maxsize=64)
    key = (tuple(syms), days)
    hit, payload = cache.get(key)
    if hit:
        # model_copy 标注 cached，不改共享缓存对象
        return {"data": payload.model_copy(update={"cached": True}), "meta": _meta(hub)}

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
    return {"data": payload, "meta": _meta(hub)}


@router.get("/market/sentiment-history", response_model=Envelope[SentimentHistoryPayload])
async def market_sentiment_history(
    request: Request,
    hub: QuoteHub = Depends(get_hub),
    days: int = Query(default=10, ge=2, le=60),
) -> dict:
    """情绪周期序列（retro #17）：近 N 个交易日情绪判定 + 周期起点定位。

    数据三路合一（口径全部来自 compute_market_sentiment，与实时端点一致）：
    ① 复盘报告回填（进程内一次，幂等只补缺）；② 惰性补录——交易日 15:00 后
    缺当日记录则现算落库；③ 历史表已有数据。已存在的日期不覆盖。
    """
    from app.core.db import get_session_factory
    from app.market.sentiment_history import (
        backfill_from_reports,
        get_history,
        locate_cycle,
        upsert_if_absent,
    )

    sf = get_session_factory()

    # ① 历史报告回填（进程内只跑一次；表空且无报告时零成本）
    backfilled = 0
    if not _sent_hist_backfilled["done"]:
        try:
            backfilled = backfill_from_reports(sf)
        except Exception:
            log.warning("sentiment history backfill failed", exc_info=True)
        _sent_hist_backfilled["done"] = True

    # ② 惰性补录：交易日 15:05 后缺当日记录 → 现算落库
    now_bj = datetime.utcnow() + timedelta(hours=8)
    today_key = now_bj.strftime("%Y%m%d")
    try:
        from app.market.trade_calendar import trading_days

        days_list = await trading_days(hub.provider)
        is_trade_day = now_bj.date() in days_list
    except Exception:
        is_trade_day = now_bj.weekday() < 5
    if is_trade_day and (now_bj.hour, now_bj.minute) >= (15, 5):
        try:
            from app.services.market_context import compute_market_sentiment

            existing = {h["trade_date"] for h in get_history(sf, days=days)}
            if today_key not in existing:
                result = await compute_market_sentiment(hub, request.app.state.snapshot_service)
                entry = {
                    "trade_date": today_key,
                    "phase": result.get("phase") or "分歧",
                    "temperature": result.get("temperature"),
                    "confidence": result.get("confidence"),
                    "phase_unreliable": bool(result.get("phase_unreliable")),
                    "source": "live",
                    "detail": result,
                }
                if upsert_if_absent(sf, entry):
                    backfilled += 1
        except Exception:
            log.warning("sentiment history lazy capture failed", exc_info=True)

    # ③ 序列 + 周期定位
    history = get_history(sf, days=days)
    cycle = locate_cycle(history)
    payload = SentimentHistoryPayload(
        items=[SentimentHistoryItem(**h) for h in history],
        cycle=cycle,
        backfilled=backfilled,
        notes=[
            "序列自功能上线起积累；复盘报告里已有的历史判定会自动回填",
            "周期起点 = 最近一次 强(回暖/升温/高潮)·中(分歧)·弱(退潮/冰点) 分段切换日",
        ],
    )
    return {"data": payload, "meta": _meta(hub)}


@router.get("/market/breadth", response_model=Envelope[BreadthData])
async def market_breadth(request: Request) -> dict:
    """市场宽度：涨跌家数、涨跌停家数、两市成交额（来源：新浪全市场快照）。"""
    svc = request.app.state.snapshot_service
    payload = svc.breadth_payload()
    if payload["breadth"] is None:
        raise HTTPException(status_code=503, detail="全市场快照尚未就绪（冷启动抓取约需数秒）")
    return {"data": payload, "meta": _meta(request.app.state.hub)}


@router.get("/market/heatmap")
async def market_heatmap(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    """A 股云图载荷：全市场快照 × 行业映射（TDX HY）→ 分组 treemap 数据。

    - 面积权重 = 流通市值（快照 nmc）；颜色 = 当日涨跌幅；
    - 组内仅保留流通市值 Top 12，其余并入「其他(n只)」聚合块（市值加权涨跌幅）；
    - 行业映射 24h 缓存，TDX 不可用时个股归「未分类」并在 industry_coverage 标注覆盖率。
    """
    svc = request.app.state.snapshot_service
    rows = svc.snapshot or []
    if not rows:
        raise HTTPException(status_code=503, detail="全市场快照尚未就绪（冷启动抓取约需数秒）")

    cache = cache_on(request.app.state, "market.heatmap", 60, maxsize=1)
    hit, payload = cache.get(())
    if hit:
        return payload

    from app.services.heatmap_service import build_heatmap, get_industry_map_async

    industry_map = await get_industry_map_async()
    data = build_heatmap(rows, industry_map)
    payload = {"data": data, "meta": _meta(hub)}
    cache.set((), payload)
    return payload


@router.get("/market/overview", response_model=Envelope[OverviewPayload])
async def market_overview(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    """指数行情 + 两市成交额合计。

    成交额口径（2026-09-01 修正，对标同花顺）：原实现把 indices 里 SH/SZ 全部
    指数的成交额求和——沪深300/中证1000/创业板指与上证指数/深证成指成分互相
    重叠，重复求和导致总额虚高 40%+。同花顺口径 = 沪市全市场 + 深市全市场，
    与全市场快照求和一致（compute_breadth.total_amount 同源），故改用快照。
    快照未就绪（冷启动数秒）时诚实返回 null，前端显示 --。"""
    indices = hub.get_indices()
    total_amount = None
    snap = getattr(request.app.state, "snapshot_service", None)
    if snap is not None and snap.snapshot:
        total_amount = round(sum(r.get("amount") or 0 for r in snap.snapshot), 2)
    return {
        "data": {
            "indices": [q.model_dump(mode="json") for q in indices],
            "total_amount": total_amount,
        },
        "meta": _meta(hub),
    }


@router.get("/quotes", response_model=Envelope[list[Quote]])
async def quotes(
    symbols: str | None = Query(default=None, description="逗号分隔的股票代码"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    wanted = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    data = hub.get_quotes(wanted)
    return {"data": [q.model_dump(mode="json") for q in data], "meta": _meta(hub)}


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
                return {"data": validate_quote(q).model_dump(mode="json"), "meta": _meta(hub)}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{source} 行情失败：{exc}")
    from app.services.quote_enrich import fill_limit_prices, fill_valuation

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
        live = await fill_valuation(hub.provider, await fill_limit_prices(hub.provider, live))
        return {"data": validate_quote(live).model_dump(mode="json"), "meta": _meta(hub)}
    # 缓存命中：先拷贝再补价，fill_limit_prices 是原地修改，不能动共享缓存对象
    cached = await fill_valuation(
        hub.provider, await fill_limit_prices(hub.provider, found[0].model_copy())
    )
    return {"data": cached.model_dump(mode="json"), "meta": _meta(hub)}


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
    return {"data": ob.model_dump(mode="json"), "meta": _meta(hub)}


@router.get("/trades/{symbol}", response_model=Envelope[list[Trade]])
async def trades(symbol: str, limit: int = Query(default=50, ge=1, le=200), hub: QuoteHub = Depends(get_hub)) -> dict:
    try:
        rows = await hub.provider.get_trades(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"逐笔数据源失败：{exc}")
    rows = rows[-limit:]
    return {"data": [t.model_dump(mode="json") for t in rows], "meta": _meta(hub)}


@router.get("/minute-line/{symbol}", response_model=Envelope[MinuteLinePayload])
async def minute_line(symbol: str, hub: QuoteHub = Depends(get_hub)) -> dict:
    """当日 1 分钟分时（价格/成交量/累计成交额）。逐笔成交不可用时，这是盘中细粒度的替代口径。

    `vr_baseline_5m`：精确量比基线（最近 5 个完整交易日逐 5min 槽同期累计量均值，
    来自 TDX 落地历史）——前端量比优先用精确口径（cum_i / (baseline[slot]/5)），
    缺失时回退近似口径。
    """
    try:
        points = await hub.provider.get_minute_line(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"分时数据源失败：{exc}")
    baseline = None
    try:
        from app.market.minute_backfill import load_vr_baseline

        # Parquet 读是同步阻塞（评审 B5）：与同文件 themes 路由 986 行同一纪律——
        # 必须丢线程池，否则卡死事件循环
        baseline = await asyncio.to_thread(load_vr_baseline, symbol)
    except Exception as exc:
        log.warning("vr baseline failed for %s: %s", symbol, exc)
    return {"data": {"symbol": symbol, "points": points, "vr_baseline_5m": baseline}, "meta": _meta(hub)}


async def _default_trade_date_async(hub) -> date:
    """最近交易日：优先官方交易日历（ths，缓存 24h），失败回退周末规则。"""
    cache = cache_on(hub, "provider.trading_days", 86400, maxsize=1)
    hit, days = cache.get("days")
    if not hit:
        for p in hub.providers if hasattr(hub, "providers") else [hub.provider]:
            if hasattr(p, "get_trading_days"):
                try:
                    got = await p.get_trading_days()
                    if got:  # 失败/空结果不缓存，下次请求换源重试
                        days = got
                        cache.set("days", days)
                        break
                except Exception:
                    continue
    if days:
        now = datetime.now()
        today_str = now.strftime("%Y%m%d")
        past = [d for d in days if d <= today_str]
        if past:
            latest = past[-1]
            # 盘前（<09:15）当日涨停池/龙虎榜尚未形成，数据源返回的其实是
            # 最近收盘的池——日期必须一并回溯，否则"内容 8-31、日期标 9-1"
            # （2026-09-01 00:24 实测：86 只池内容为 8-31 收盘、trade_date 标 09-01）。
            if latest == now.date().strftime("%Y%m%d") and now.time().replace(tzinfo=None) < dt_time(9, 15) and len(past) >= 2:
                latest = past[-2]
            return date(int(latest[:4]), int(latest[4:6]), int(latest[6:]))
    d = date.today()
    return {5: d - timedelta(days=1), 6: d - timedelta(days=2)}.get(d.weekday(), d)


def _default_trade_date() -> date:
    """周末回退规则（交易日历不可用时的兜底）。"""
    d = date.today()
    return {5: d - timedelta(days=1), 6: d - timedelta(days=2)}.get(d.weekday(), d)


@router.get("/limit-up", response_model=Envelope[LimitUpPoolPayload])
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


@router.get("/longhu", response_model=Envelope[LongHuPayload])
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
    return {"data": rows[0], "meta": _meta(hub)}


@router.get("/auction-benchmark", response_model=Envelope[list[AuctionBenchmarkItem]])
async def auction_benchmark(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，默认当日（Asia/Shanghai）"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """短线风向标竞价基准（按日，含题材 tags）——新题材预判与竞价联动验证的数据面。"""
    d = date.fromisoformat(date_str) if date_str else date.today()
    try:
        rows = await hub.provider.get_auction_benchmark(d)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"竞价基准数据源失败：{exc}")
    return {"data": rows, "meta": _meta(hub)}


@router.get("/adjustment-events/{symbol}", response_model=Envelope[list[AdjustmentEvent]])
async def adjustment_events(
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """复权事件流（现金分红/送股，单只）——回测前复权修正的数据面。"""
    try:
        rows = await hub.provider.get_adjustment_events(symbol, start, end)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"复权事件数据源失败：{exc}")
    return {
        "data": [{"ex_date": e["ex_date"].isoformat(), "dividend": e["dividend"], "bonus": e["bonus"]} for e in rows],
        "meta": _meta(hub),
    }


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
    payload = {"data": {"type": type, "boards": rows}, "meta": _meta(hub)}
    cache.set(type, payload)
    return payload


# ---------------------------------------------------------------- 涨速榜（指数/题材详情"涨速"标签）


def _speed_sampler(request: Request) -> SpeedSampler:
    if not hasattr(request.app.state, "speed_sampler"):
        request.app.state.speed_sampler = SpeedSampler()
    return request.app.state.speed_sampler


async def _batch_quotes(hub: QuoteHub, symbols: list[str]) -> list[Quote]:
    """批量快照，腾讯直取（涨速只认这一条价格链），50 只/请求分批。

    不走 composite 全链：ths 批量失败一次纯属浪费一跳，且涨速口径要求
    价格源单一——腾讯快照与前端个股行情同源。
    """
    composite = hub.provider if hasattr(hub.provider, "providers") else None
    target = next(
        (p for p in (composite.providers if composite else [hub.provider]) if p.name == "tencent"),
        hub.provider,
    )
    out: list[Quote] = []
    for i in range(0, len(symbols), 50):
        batch = symbols[i : i + 50]
        try:
            out.extend(await target.get_quotes(batch))
        except Exception as exc:
            log.warning("speed-rank batch %s failed: %s", i // 50, exc)
    return out


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
                "meta": _meta(hub),
            }
    else:
        raise HTTPException(status_code=400, detail="theme 与 symbols 至少给一个")

    quotes = await _batch_quotes(hub, sym_list)
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
        "meta": _meta(hub),
    }


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


@router.get("/limit-break", response_model=Envelope[LimitUpPoolPayload])
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


@router.get("/company/{symbol}")
async def company(symbol: str, hub: QuoteHub = Depends(get_hub)) -> dict:
    """公司资料：简介/行业/主营业务（东财 F10）。"""
    try:
        profile = await hub.provider.get_company_profile(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"公司资料数据源失败：{exc}")
    return {"data": profile, "meta": _meta(hub)}


@router.get("/announcements/{symbol}")
async def announcements(
    symbol: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=30),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """个股公告（东财，title/date/类型/原文链接）。进程内缓存 60s（切股回看不闪加载）。"""
    cache = cache_on(request.app.state, "market.announcements", 60, maxsize=512)
    key = (symbol, limit)
    hit, cached = cache.get(key)
    if hit:
        return {"data": cached, "meta": {**_meta(hub), "cached": True}}
    try:
        rows = await hub.provider.get_announcements(symbol, limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"公告数据源失败：{exc}")
    data = {"symbol": symbol, "items": rows}
    cache.set(key, data)
    return {"data": data, "meta": _meta(hub)}


@router.get("/news/{symbol}")
async def news(
    symbol: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=30),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """个股相关新闻（东财资讯检索，含正文摘要）。进程内缓存 60s（技术债 #4）。"""
    cache = cache_on(request.app.state, "market.news", 60, maxsize=512)
    key = (symbol, limit)
    hit, cached = cache.get(key)
    if hit:
        return {"data": cached, "meta": {**_meta(hub), "cached": True}}
    try:
        rows = await hub.provider.get_news(symbol, limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"新闻数据源失败：{exc}")
    data = {"symbol": symbol, "items": rows}
    cache.set(key, data)
    return {"data": data, "meta": _meta(hub)}


@router.get("/search", response_model=Envelope[list[SymbolSearchItem]])
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


# ---------------------------------------------------------------- 题材梯队看板


def _load_snapshot_map(request: Request, trade_date: date | None = None) -> dict[str, dict]:
    """读指定交易日（默认最新）的全市场快照 → ``symbol -> {"change_pct": ...}``。

    接力赚钱效应（昨日涨停股今日溢价）需要覆盖全市场的当日涨跌幅，
    逐只拉行情太慢，快照 Parquet 是现成的数据底座。读不到就返回空（溢价指标降级为 None）。

    **必须按 trade_date 取，不能永远取最新一份**：溢价问的是「该交易日的涨跌幅」，
    拿最新快照（例如周六回看上周五，快照目录却是周六）会在非交易日或回看历史日期时
    把错误的涨跌幅当成溢价——数字照样出得来，但结论是错的，属于「错了也看不出来」。
    找不到当天目录时，退到不晚于该日期的最近一份，并记 warning。
    """
    try:
        from app.services.parquet_store import read_latest_in_dir

        svc = getattr(request.app.state, "snapshot_service", None)
        base = Path(getattr(svc, "parquet_dir", "")) / "snapshots" if svc else None
        if base is None or not Path(base).exists():
            return {}
        day_dirs = sorted((p for p in Path(base).iterdir() if p.is_dir()), reverse=True)

        chosen: Path | None = None
        if trade_date is not None:
            want = trade_date.strftime("%Y%m%d")
            exact = base / want
            if exact.is_dir() and sorted(exact.glob("*.parquet")):
                chosen = exact
            else:
                # 退到不晚于目标日期的最近一份
                for d in day_dirs:
                    if d.name <= want and sorted(d.glob("*.parquet")):
                        chosen = d
                        log.warning(
                            "snapshot for %s not found, falling back to %s "
                            "(溢价口径可能偏移)", want, d.name,
                        )
                        break
        if chosen is None:
            for d in day_dirs:
                if sorted(d.glob("*.parquet")):
                    chosen = d
                    break
        if chosen is None:
            return {}

        # 取该日目录里最新一份**可读**的快照：损坏文件会被跳过而不是让整个端点 502
        read = read_latest_in_dir(chosen, columns=["symbol", "change_pct"])
        if not read.ok:
            log.warning("snapshot unreadable for %s: %s", chosen, read.error)
            return {}
        df = read.df
        out: dict[str, dict] = {}
        for sym, pct in zip(df["symbol"].to_list(), df["change_pct"].to_list()):
            if sym is None:
                continue
            out[str(sym).zfill(6)] = {"change_pct": pct}
        return out
    except Exception as exc:  # 快照缺失不应让看板整体失败
        log.warning("snapshot map unavailable: %s", exc)
        return {}


async def _verify_board_multi_day(request: Request, board_payload: dict) -> None:
    """T3/B3（linkage-design §3.5）：用同花顺官方板块 K 线交叉验证/替换
    板块 3/5/10 日涨跌幅（东财字段序推断值）。

    - 题材名（ths 体系）直接映射官方概念目录 → 板块 K 线 → 重算涨跌幅
    - 官方可得 → 覆盖 chg_3d/5d/10d + multi_day_verified=true（推断值保留在 *_inferred 供审计）
    - 不可用（无目录服务/题材不在目录/拉取失败）→ 保留推断值 + false，caveats 如实说明
    - 缓存命中的 payload 已验证过则直接跳过，避免每请求重复拉 K 线
    """
    svc = getattr(request.app.state, "theme_catalog", None)
    if svc is None:
        return
    cards = board_payload.get("themes") or []
    if any(((c.get("board") or {}).get("multi_day_verified")) for c in cards):
        return

    name_to_code = {t.name: t.code for t in svc.get_catalog(limit=1000)}
    targets: dict[str, list[tuple[dict, dict]]] = {}  # 目录代码 → (卡片, board dict) 列表
    for card in cards:
        board = card.get("board")
        if not board:
            continue
        code = name_to_code.get(card.get("theme") or "") or name_to_code.get(board.get("name") or "")
        if code:
            targets.setdefault(code, []).append((card, board))
    if not targets:
        return

    sem = asyncio.Semaphore(4)
    bars_by_code: dict[str, list[dict]] = {}

    async def _fetch(code: str) -> None:
        async with sem:
            try:
                bars_by_code[code] = await svc.fetch_board_bars(code)
            except Exception as exc:  # noqa: BLE001 - 单板块失败保留推断值
                log.warning("board bars %s unavailable: %s", code, exc)

    await asyncio.gather(*(_fetch(c) for c in targets))

    verified = 0
    position_upgraded = 0
    for code, pairs in targets.items():
        bars = bars_by_code.get(code)
        if not bars:
            continue
        official = official_multi_day_changes(bars)
        for card, board in pairs:
            for n in (3, 5, 10):
                key = f"chg_{n}d"
                if official.get(key) is not None:
                    board[f"{key}_inferred"] = board.get(key)
                    board[key] = official[key]
            board["multi_day_verified"] = all(
                official.get(f"chg_{n}d") is not None for n in (3, 5, 10)
            )
            if board["multi_day_verified"]:
                board["multi_day_source"] = "ths_official_kline"
                verified += 1
            # P1-6：官方 5 日涨幅到位后，把持续性评估的位置判定从 active_days
            # 代理升级为真实区间涨幅（chg_5d 不可得时保持代理口径，不硬凑）。
            persistence = card.get("persistence")
            if official.get("chg_5d") is not None and persistence:
                apply_position_with_5d(persistence, official["chg_5d"])
                position_upgraded += 1

    if verified or position_upgraded:
        caveats = board_payload.setdefault("caveats", [])
        card_total = sum(len(v) for v in targets.values())
        for i, text in enumerate(caveats):
            if "board_multi_day_verified" in text:
                caveats[i] = (
                    f"板块 3/5/10 日涨跌幅已用同花顺官方板块 K 线交叉验证"
                    f"（{verified}/{card_total} 张卡片），持续性评估位置判定同步升级"
                    f"（{position_upgraded} 张）；未命中的题材保留东财字段序推断"
                )
                break


def _attach_official_flags(request: Request, themes_list: list[dict]) -> None:
    """L5（linkage-design §3.2）：给看板梯队成员标注是否为 THS 官方成分。

    纯展示增强：题材目录服务不可用/目录为空时静默跳过（无徽标 ≠ 非成分，
    前端不得把缺徽标当负面信号）。
    """
    svc = getattr(request.app.state, "theme_catalog", None)
    if svc is None or svc.catalog_size() == 0:
        return
    try:
        name_to_code = {t.name: t.code for t in svc.get_catalog(limit=1000)}
        members_by_code: dict[str, set[str]] = {}
        for card in themes_list:
            code = name_to_code.get(card.get("theme") or "")
            if not code:
                continue
            if code not in members_by_code:
                members_by_code[code] = {m.symbol for m in svc.get_members(code)}
            official = members_by_code[code]
            for it in card.get("ladder") or []:
                it["official"] = it.get("symbol") in official
    except Exception:  # noqa: BLE001 - 徽标失败不影响看板
        log.exception("attach official flags failed")


@router.get("/themes", response_model=Envelope[ThemeBoardPayload])
async def themes(
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，默认最近交易日"),
    min_boards: int = Query(default=0, ge=0, le=20, description="仅保留最高连板 ≥ 该值的题材"),
    min_count: int = Query(
        default=2, ge=1, le=50,
        description="仅保留涨停家数 ≥ 该值的题材；默认 2 以滤掉个股独立行情",
    ),
    sort: str = Query(default="strength", description="strength(综合强度) | boards(连板高度) | count(涨停家数)"),
    limit: int = Query(default=30, ge=1, le=100),
    request: Request = None,
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """题材梯队看板：涨停池按题材容器重组，输出连板天梯 + 强度指标 + 阶段判断。

    与 /api/limit-up 的区别：涨停池是平铺列表，本接口以题材为容器，
    给出「梯队是否成建制、资金是否持续」的结构化结论。结果缓存 60s。
    """
    if sort not in ("strength", "boards", "count"):
        raise HTTPException(status_code=400, detail="sort 仅支持 strength / boards / count")
    trade_date = date.fromisoformat(date_str) if date_str else await _default_trade_date_async(hub)

    cache = cache_on(request.app.state, "market.themes", 60, maxsize=16)
    hit, payload = cache.get(trade_date)
    if not hit:
        from app.services.theme_service import build_theme_board

        # 读 Parquet 是同步阻塞调用，必须丢到线程池，否则会卡住事件循环
        # （曾导致整个服务无响应，连 /api/health 都超时）。
        try:
            board = await build_theme_board(
                hub.provider,
                trade_date,
                snapshot_map=await asyncio.to_thread(_load_snapshot_map, request, trade_date),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        payload = {"data": board, "meta": _meta(hub)}
        cache.set(trade_date, payload)

    # T3/B3：官方板块 K 线交叉验证（缓存的 payload 已验证过时内部直接跳过）
    await _verify_board_multi_day(request, payload["data"])

    themes_list = list(payload["data"]["themes"])
    _attach_official_flags(request, themes_list)
    if min_boards > 0:
        themes_list = [t for t in themes_list if t["performance"]["max_boards"] >= min_boards]
    if min_count > 1:
        themes_list = [t for t in themes_list if t["performance"]["limit_up_count"] >= min_count]
    if sort == "boards":
        themes_list.sort(key=lambda t: (-t["performance"]["max_boards"], -t["strength_score"]))
    elif sort == "count":
        themes_list.sort(key=lambda t: (-t["performance"]["limit_up_count"], -t["strength_score"]))

    out = dict(payload["data"])
    out["themes"] = themes_list[:limit]
    out["filters"] = {"sort": sort, "min_boards": min_boards, "min_count": min_count, "limit": limit}
    return {"data": out, "meta": payload["meta"]}
