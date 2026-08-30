from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from pathlib import Path

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
    """情绪周期判定（§5.5）：阶段+温度+指标依据+置信度+误判原因+切换条件+次日验证项。

    计算逻辑在 `app.services.market_context.compute_market_sentiment`，
    与复盘 Agent 共用同一实现——口径只有一个，避免两边漂移。
    结果缓存 60s。
    """
    import time as _time

    from app.services.market_context import CalendarUnavailable, compute_market_sentiment

    svc = request.app.state.snapshot_service
    cache = getattr(request.app.state, "_sent_cache", None)
    if cache and _time.time() - cache[0] < 60:
        return cache[1]

    try:
        result = await compute_market_sentiment(hub, svc)
    except CalendarUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    payload = {"data": result, "meta": _meta(hub)}
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


@router.get("/minute-signals/{symbol}")
async def minute_signals(symbol: str, hub: QuoteHub = Depends(get_hub)) -> dict:
    """做 T 分时信号（docs/minute-chart-plan.md 模块 4 引擎核心）。

    只输出「偏向 + 依据 + 失效条件」（红线 3，非确定性结论）。
    as_of 严格推进：信号触发时刻 = 确认完成的那根 bar。
    阈值未经历史校准——回测（模块 4.3）跑完前置信度一律按 medium 封顶。
    """
    try:
        points = await hub.provider.get_minute_line(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"分时数据源失败：{exc}")

    prev_close = None
    yesterday_vol = None
    daily_vol_pct = None
    try:
        q = await hub.provider.get_quote(symbol)
        if q is not None:
            prev_close = q.prev_close
    except Exception:
        pass  # 昨收缺失 → 信号照常（引擎不依赖它），仅前端涨跌幅显示降级
    try:
        bars = await hub.provider.get_kline(symbol, "1d", None, None)
        # bars[-1] 盘中是今日实时 bar / 休市日是分时日本身——两种场景 bars[-2]
        # 都是"分时日的上一交易日"（与前端量比口径一致）
        if len(bars) >= 2:
            yesterday_vol = bars[-2].volume
        # 近 5 日日振幅均值（%）：偏离阈值波动率自适应的输入
        if len(bars) >= 6:
            rngs = [(b.high - b.low) / b.close * 100 for b in bars[-6:-1] if b.close]
            if rngs:
                daily_vol_pct = round(sum(rngs) / len(rngs), 3)
    except Exception:
        pass  # 缺昨日量 → 引擎内指标 4 降级并在 degraded 里标注

    from app.market.minute_signals import compute_minute_signals

    result = compute_minute_signals(points, yesterday_vol=yesterday_vol, daily_vol_pct=daily_vol_pct)
    result["symbol"] = symbol
    result["prev_close"] = prev_close  # 前端涨跌幅与坐标锚定用
    result["daily_vol_pct"] = daily_vol_pct
    result["signal_count"] = len(result["signals"])
    return {"data": result, "meta": _meta(hub)}


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


@router.get("/company/{symbol}")
async def company(symbol: str, hub: QuoteHub = Depends(get_hub)) -> dict:
    """公司资料：简介/行业/主营业务（东财 F10）。"""
    try:
        profile = await hub.provider.get_company_profile(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"公司资料数据源失败：{exc}")
    return {"data": profile, "meta": _meta(hub)}


@router.get("/announcements/{symbol}")
async def announcements(symbol: str, limit: int = Query(default=10, ge=1, le=30), hub: QuoteHub = Depends(get_hub)) -> dict:
    """个股公告（东财，title/date/类型/原文链接）。"""
    try:
        rows = await hub.provider.get_announcements(symbol, limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"公告数据源失败：{exc}")
    return {"data": {"symbol": symbol, "items": rows}, "meta": _meta(hub)}


@router.get("/news/{symbol}")
async def news(symbol: str, limit: int = Query(default=10, ge=1, le=30), hub: QuoteHub = Depends(get_hub)) -> dict:
    """个股相关新闻（东财资讯检索，含正文摘要）。"""
    try:
        rows = await hub.provider.get_news(symbol, limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"新闻数据源失败：{exc}")
    return {"data": {"symbol": symbol, "items": rows}, "meta": _meta(hub)}


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
        import polars as pl

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

        files = sorted(chosen.glob("*.parquet"))
        df = pl.read_parquet(files[-1], columns=["symbol", "change_pct"])
        out: dict[str, dict] = {}
        for sym, pct in zip(df["symbol"].to_list(), df["change_pct"].to_list()):
            if sym is None:
                continue
            out[str(sym).zfill(6)] = {"change_pct": pct}
        return out
    except Exception as exc:  # 快照缺失不应让看板整体失败
        log.warning("snapshot map unavailable: %s", exc)
        return {}


@router.get("/themes")
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
    import time as _time

    if sort not in ("strength", "boards", "count"):
        raise HTTPException(status_code=400, detail="sort 仅支持 strength / boards / count")
    trade_date = date.fromisoformat(date_str) if date_str else await _default_trade_date_async(hub)

    key = f"_themes_cache_{trade_date}"
    cache = getattr(request.app.state, key, None)
    if cache and _time.time() - cache[0] < 60:
        payload = cache[1]
    else:
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
        setattr(request.app.state, key, (_time.time(), payload))

    themes_list = list(payload["data"]["themes"])
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
