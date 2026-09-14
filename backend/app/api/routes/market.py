from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import date, datetime, time as dt_time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.api.deps import get_hub
from app.core.freshness import Freshness
from app.core.ttl_cache import cache_on
from app.data_providers.eastmoney import ProviderError
from app.data_quality.validator import validate_order_book
from app.market.article import ArticleFetchError, classify_url, fetch_article
from app.schemas.envelope import (
    AnomalyPayload,
    AuctionBenchmarkItem,
    AuctionSnapshot,
    BreadthData,
    Envelope,
    KlinePayload,
    LimitDownPoolPayload,
    LimitUpPoolPayload,
    LongHuPayload,
    MinuteDecisionsPayload,
    MinuteLinePayload,
    MinuteSignalsPayload,
    OverviewPayload,
    SentimentHistoryItem,
    SentimentHistoryPayload,
    SentimentPayload,
    SparklineItem,
    SparklinePayload,
    ThemeBoardPayload,
)
from app.market.normalizer import main_board
from app.market.trade_calendar import prev_trade_date, trading_days
from app.market.trading_status import bar_date, resolve_trading_status
from app.schemas.market import (
    OrderBook,
    Quote,
    SymbolSearchItem,
    Trade,
    TradingStatus,
    TradingStatusInfo,
    utcnow,
)
from app.services.quote_enrich import fetch_quotes_list
from app.services.quote_hub import QuoteHub
from app.services.market_snapshot import (
    default_trade_date,
    default_trade_date_weekend_fallback,
    load_snapshot_map,
)
from app.services.speed_sampler import SpeedSampler
from app.services.dragon_service import apply_position_with_5d
from app.services.theme_catalog_service import official_multi_day_changes
from app.core.bjtime import beijing_now, beijing_now_naive, beijing_today  # S2-8 时区收敛

log = logging.getLogger(__name__)
router = APIRouter(tags=["market"])


def _hub_freshness(hub: QuoteHub) -> dict:
    """行情链新鲜度（S2-1 契约）。**保留 `is_stale` 布尔**供既有消费方使用。

    刻意用 `getattr` 回退而非直接调 `hub.freshness()`：多处测试桩只实现了
    `is_stale`（历史接口面），强依赖新方法会把"加一个只读字段"变成破坏性改动。
    回退路径同样按 Freshness 规则派生（缺时间戳 → unknown），**不假装 ready**。
    """
    fn = getattr(hub, "freshness", None)
    if callable(fn):
        fresh = fn()
    else:
        fresh = Freshness.from_age(
            as_of=getattr(hub, "last_success_refresh", None),
            fresh_within=getattr(hub, "stale_after", 10.0) or 10.0,
            source=getattr(getattr(hub, "provider", None), "name", None),
            missing_reason="行情链未提供成功刷新时间，无法判定新鲜度",
        )
    return fresh.model_dump(mode="json")


def _meta(hub: QuoteHub) -> dict:
    """行情信封的 meta。

    `batch_coverage`（R18，2026-09-14）：上一轮批量请求的**返回覆盖率**
    （1.0 = 请求集全部返回，0.0 = 一只都没回，None = 未判定/空自选）。
    它量的是"源有没有漏返回"，与 `freshness`（量时间年龄）是两个不同的轴：
    源可能每一轮都"成功"却稳定漏掉几只，此时 Hub 级 freshness 仍 ready——
    逐标的 `Quote.quality`/`freshness()` 负责诚实，本字段提供**系统级**读数
    （例如"覆盖率从 1.0 掉到 0.92 并持续"是源侧退化的早期信号）。

    ⚠️ 刻意**不**把完整缺失清单放进每个响应：自选可达数百只，全量列表是
    纯载荷浪费。清单保留在 Hub 对象上（`hub.last_missing_symbols`）供日志
    与诊断使用，日志侧只在缺失集**变化**时打印。

    `push`（R24，2026-09-14）：WebSocket 推送链路的取证面 —— 订阅连接数、
    出站队列上限、**累计丢弃帧数**。丢弃只可能发生在出站队列满时，即客户端
    停止消费而服务端仍在按 1Hz 生产（慢网络 / 半死连接 / 已成孤儿的 writer）；
    它是"推送正在丢帧"的唯一系统级读数，也是判定"该降级了"的依据之一。
    """
    return {
        "provider": hub.provider.name,
        "is_realtime": bool(getattr(hub.provider, "realtime", False)) and not hub.is_stale(),
        "is_stale": hub.is_stale(),
        "freshness": _hub_freshness(hub),
        "batch_coverage": getattr(hub, "last_batch_coverage", None),
        "push": hub.subscriber_stats() if hasattr(hub, "subscriber_stats") else None,
        "last_success_refresh": hub.last_success_refresh.isoformat() if hub.last_success_refresh else None,
        "generated_at": utcnow().isoformat(),
    }


async def _dated_meta(hub: QuoteHub, trade_date: date) -> dict:
    """**按日期取数**的载荷专用 meta：数据日期落后于最近交易日时**必须降级**（红线 2）。

    为什么需要它（2026-09-14 实测）：`_meta()` 描述的是 **Hub 的实时健康度**，而 `data`
    的业务日期是**另一个轴**；此前二者之间**没有任何一致性校验**，于是同一响应里并存
    `data.trade_date = "2026-09-11"`、`meta.is_realtime = true`、`meta.is_stale = false`、
    `freshness.state = "ready"`、`freshness.age_seconds = 0.9` —— **过期数据拿到了最强的
    实时背书**。这正是红线 2「禁止把过期缓存冒充实盘」要禁止的形态。

    判据用**日历覆盖的最近交易日**（`_latest_trade_date` 已走日历唯一入口），
    不引入第二套日期口径。日期不落后时原样返回，不做任何额外断言。
    """
    meta = _meta(hub)
    latest = await _latest_trade_date(hub)
    if trade_date >= latest:
        return meta
    meta["is_realtime"] = False
    meta["is_stale"] = True
    fresh = dict(meta.get("freshness") or {})
    fresh["state"] = "stale"
    fresh["reason"] = f"数据日期 {trade_date.isoformat()} 早于最近交易日 {latest.isoformat()}"
    meta["freshness"] = fresh
    # 显式给出「实际日期 / 应有日期」，让前端与调试都不必猜（三态：不隐藏落后）
    meta["data_date"] = trade_date.isoformat()
    meta["expected_date"] = latest.isoformat()
    return meta


@router.get("/market/sentiment", response_model=Envelope[SentimentPayload])
async def market_sentiment(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    """情绪周期判定（§5.5）：阶段+温度+指标依据+置信度+误判原因+切换条件+次日验证项。

    计算逻辑在 `app.services.market_context.compute_market_sentiment`，
    与复盘 Agent 共用同一实现——口径只有一个，避免两边漂移。
    结果走**共享 60s 缓存槽**（P1-3：`get_cached_sentiment`，与题材相位、
    介入条件清单、猎场相位路由、事件排序、风控刷新同一槽），本端点只负责
    套上行情 meta 信封——`meta.generated_at` 是**本次响应**的时刻，与
    领域对象的计算时刻（缓存命中时可能早 60s）本就不同，不能混为一谈。
    """
    from app.services.market_context import CalendarUnavailable, get_cached_sentiment

    try:
        result = await get_cached_sentiment(request.app.state, hub)
    except CalendarUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"data": result, "meta": _meta(hub)}


#: 复盘报告回填的**进程级**一次性闸门。失败时只写 retry_after（退避重试），
#: 不置 done —— 见下面的注释（原实现把失败也当完成）。
_sent_hist_backfilled = {"done": False, "retry_after": 0.0}


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
        return {"data": payload.model_copy(update={"cached": True}), "meta": _meta(hub)}

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
    return {"data": payload, "meta": _meta(hub)}


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
    if not _sent_hist_backfilled["done"] and time.time() >= _sent_hist_backfilled["retry_after"]:
        try:
            backfilled = backfill_from_reports(sf)
            # **只有成功才置 done**：原实现无论成败都置 True，而这是进程级一次标志，
            # 一次瞬时失败（DB 忙 / 报告目录未就绪）会让历史回填在本次进程生命周期内
            # 永不重试 —— 用户看到「历史序列只有最近几天」却无从判断是本就无数据
            # 还是回填坏了（数字出得来、结论是错的，属"错了也看不出来"）。
            _sent_hist_backfilled["done"] = True
        except Exception:
            _sent_hist_backfilled["retry_after"] = time.time() + 600.0  # 退避 10 分钟
            log.warning("sentiment history backfill failed（10 分钟后重试）", exc_info=True)

    # ② 惰性补录：交易日 15:05 后缺当日记录 → 现算落库
    now_bj = beijing_now_naive()
    today_key = now_bj.strftime("%Y%m%d")
    try:
        from app.market.trade_calendar import is_trade_day_on, trading_days

        days_list = await trading_days(hub.provider)
        # 三态写法（2026-09-14，账本 §6.6 行 14）：`now.date() in days_list` 是**二态**
        # 判定，而源头日历是**尾随窗口**（不含未来日期）——双源失败 + 限频窗口下拿到
        # 旧快照（末日停在上一交易日）时，`in` 会把「**未判定**」塌缩成「**确认非交易日**」，
        # 静默跳过 15:05 的惰性补录**且无任何告警**（与 F7 六处同一反模式）。
        # 与下面的 except 分支**同口径**：真不确定时退回「工作日即交易日」的乐观判据
        # ——宁可多重算一次（`upsert_if_absent` 幂等，已有记录不会重复落），
        # 不可静默漏一天。
        state = is_trade_day_on(now_bj.date(), days_list)
        trade_day_ok = (now_bj.weekday() < 5) if state is None else state
    except Exception:
        trade_day_ok = now_bj.weekday() < 5
    if trade_day_ok and (now_bj.hour, now_bj.minute) >= (15, 5):
        try:
            from app.services.market_context import get_cached_sentiment

            existing = {h["trade_date"] for h in get_history(sf, days=days)}
            if today_key not in existing:
                # 走共享槽：落库的相位与同一时刻界面展示的相位必须同源，
                # 否则"盘后补录"会写出一个用户从没见过的相位（P1-3）。
                result = await get_cached_sentiment(request.app.state, hub)
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


@router.get("/market/turnover")
async def market_turnover(hub: QuoteHub = Depends(get_hub)) -> dict:
    """今日两市成交额（沪深口径）：实时 + 昨日同一时刻对比 + 全日估算 + 分时曲线。

    数据源与降级策略见 app/market/fund_flow.py 模块头。沪深京口径的市场总览
    total_amount 各自独立、互不冒充。
    """
    from app.market.fund_flow import get_turnover_today

    return {"data": await get_turnover_today(hub), "meta": _meta(hub)}


@router.get("/market/turnover/history")
async def market_turnover_history(
    hub: QuoteHub = Depends(get_hub),
    days: int = Query(default=10, ge=2, le=20),
) -> dict:
    """近 N 个交易日全日成交额 + vs 前一交易日增减（由近及远）。"""
    from app.market.fund_flow import get_turnover_history

    return {"data": await get_turnover_history(hub, days), "meta": _meta(hub)}


@router.get("/market/turnover/day")
async def market_turnover_day(
    hub: QuoteHub = Depends(get_hub),
    date: str = Query(description="交易日 YYYY-MM-DD"),
) -> dict:
    """指定历史交易日 vs 前一交易日：全日成交额增减 + 分时累计曲线对比。"""
    from app.market.fund_flow import get_turnover_day

    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"date 格式须为 YYYY-MM-DD：{date!r}") from exc
    return {"data": await get_turnover_day(hub, d), "meta": _meta(hub)}


@router.get("/market/fund-flow/intraday")
async def market_fund_flow_intraday() -> dict:
    """今日分钟级资金流累计曲线（沪深合计，五档；延迟约 15 分钟的东财免费口径）。

    历史日的分钟资金流数据源不提供——历史回看走 /market/fund-flow/history（日度）。
    """
    from app.market.fund_flow import get_fund_flow_intraday

    return {"data": await get_fund_flow_intraday(), "meta": {}}


@router.get("/market/fund-flow")
async def market_fund_flow() -> dict:
    """实时资金流五档净额（东财大盘口径：主力/超大/大/中/小单，沪深合计）。

    机构/游资在实时全市场数据源中不存在拆分（仅龙虎榜日度有），不提供臆造字段。
    """
    from app.market.fund_flow import get_fund_flow_realtime

    return {"data": await get_fund_flow_realtime(), "meta": {}}


@router.get("/market/fund-flow/history")
async def market_fund_flow_history(
    hub: QuoteHub = Depends(get_hub),
    days: int = Query(default=30, ge=5, le=40),
) -> dict:
    """日度资金流序列（沪深合计，由近及远；本地落盘优先，落后时拉东财补齐）。"""
    from app.market.fund_flow import get_fund_flow_history

    return {"data": await get_fund_flow_history(hub, days), "meta": _meta(hub)}


@router.get("/market/board-fund-flow")
async def market_board_fund_flow(
    kind: str = Query(default="concept", description="concept 概念 / industry 行业"),
    range: str = Query(default="intraday", description="intraday 今日 / 5d / 10d / 20d"),
) -> dict:
    """板块资金流榜（L2 唯一实现，docs/summary/architecture-design.md §2 四层级模型）。

    - intraday/5d/10d：一次翻页全量（f62 今日 / f164 5日 / f174 10日，官方字段已实测），
      前端排序筛选纯内存，切换不回源；
    - 20d 与连续流入天数：只读落盘（每日收盘后自动沉淀 Top 板块 daykline），读路径零外呼；
    - 板块 f62 是东财官方板块口径，不与个股新浪口径混算。
    """
    from app.market.board_flow import get_board_fund_flow

    if kind not in ("concept", "industry"):
        raise HTTPException(status_code=422, detail="kind 仅允许 concept / industry")
    if range not in ("intraday", "5d", "10d", "20d"):
        raise HTTPException(status_code=422, detail="range 仅允许 intraday / 5d / 10d / 20d")
    return {"data": await get_board_fund_flow(kind, range), "meta": {}}


def _valid_board_code(board_code: str) -> str | None:
    code = board_code.strip().upper()
    if code.startswith("BK") and code[2:].isdigit() and len(code) in (5, 6):
        return code
    return None


@router.get("/market/board-fund-flow/{board_code}/minute")
async def market_board_flow_minute(board_code: str) -> dict:
    """板块分钟五档资金流累计曲线（东财延迟 ~15min 口径，available=false 时显式 reason）。"""
    from app.market.board_flow import get_board_minute

    code = _valid_board_code(board_code)
    if code is None:
        raise HTTPException(status_code=422, detail=f"board_code 须为 BKxxxx 形式：{board_code!r}")
    return {"data": await get_board_minute(code), "meta": {}}


@router.get("/market/board-fund-flow/{board_code}/members")
async def market_board_flow_members(board_code: str) -> dict:
    """板块成员个股资金排行 Top20（板块内资金龙头；东财成员口径）。"""
    from app.market.board_flow import get_board_members

    code = _valid_board_code(board_code)
    if code is None:
        raise HTTPException(status_code=422, detail=f"board_code 须为 BKxxxx 形式：{board_code!r}")
    return {"data": await get_board_members(code), "meta": {}}


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
        return {"data": validate_quote(live).model_dump(mode="json"), "meta": _meta(hub)}
    # 缓存命中：先拷贝再补价，enrich_quote 是原地修改，不能动共享缓存对象
    cached = await enrich_quote(hub.provider, found[0].model_copy())
    return {"data": cached.model_dump(mode="json"), "meta": _meta(hub)}


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
    return {"data": {"symbol": symbol, "points": points, "vr_baseline_5m": baseline}, "meta": _meta(hub)}


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
        "meta": _meta(hub),
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
        "meta": await _dated_meta(hub, trade_date),
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
        "meta": await _dated_meta(hub, trade_date),
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
    return {"data": payload, "meta": _meta(hub)}


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
    return {"data": payload, "meta": _meta(hub)}


@router.get("/market/board-fund/by-symbols")
async def market_board_fund_by_symbols(
    request: Request,
    symbols: str = Query(description="逗号分隔 6 位代码，≤50"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """自选行「所属板块资金」徽标（P1-4，2026-09-10）。

    **主板块口径** = 东财 F10 ssbk 行业三级的 **L2（Ⅱ级）行**（`classify_boards` 按
    `IS_PRECISE` 分段得到行业段）——「这只股是做什么的」的最近语义层级。**不用概念段首个**：
    那是 ssbk 返回顺序里的首个概念标签，与相关性无关（2026-09-10 实测茅台→「味蕾经济」、
    平安银行→「跨境支付」，语义不成立）。无行业段时才回落概念首个并如实标注 `level`。
    板块按 **BOARD_CODE 直取**（不按名字匹配——行业三级名带罗马数字后缀「白酒Ⅱ」，
    板块榜里是「白酒」，走名字会引入歧义）。

    **板块资金** = 东财 f62，经 `theme_service.board_rows_for_codes`（L3 映射 →
    复用 board_flow 盘中 30s 缓存，**零额外上游调用**）。连续流入天数只对落盘
    Top 板块可判，其余 None（三态，区别于 0=今日净流出）。资料/板块取不到 →
    该 symbol **不出现在返回里**，绝不臆造。
    """
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        raise HTTPException(status_code=422, detail="symbols 不能为空")
    if len(sym_list) > 50:
        raise HTTPException(status_code=422, detail="单批最多 50 个代码")

    cache = cache_on(request.app.state, "market.board-fund.by-symbols", 60, maxsize=64)
    key = (tuple(sym_list),)

    async def _build() -> dict:
        # 所属板块是**低频变更**数据（成分调整才变）→ 6h 缓存，避免自选轮询反复打 F10。
        board_cache = cache_on(request.app.state, "market.board-fund.main-board", 6 * 3600, maxsize=512)
        sem = asyncio.Semaphore(8)

        async def _main_board(sym: str) -> tuple[str, dict | None]:
            hit, cached = board_cache.get(sym)
            if hit:
                return sym, cached
            async with sem:
                try:
                    profile = await hub.provider.get_company_profile(sym)
                except Exception as exc:  # noqa: BLE001 单只失败不影响其余
                    log.debug("board-fund: profile %s failed: %s", sym, exc)
                    return sym, None
            groups = profile.get("board_groups") or {}
            ref = main_board(groups, profile.get("board_codes") or {})
            board_cache.set(sym, ref)
            return sym, ref

        pairs = await asyncio.gather(*[_main_board(s) for s in sym_list])
        from app.services.theme_service import board_rows_for_codes

        rows = await board_rows_for_codes([r["code"] for _, r in pairs if r and r.get("code")])
        boards: dict[str, dict] = {}
        for sym, ref in pairs:
            row = rows.get(ref.get("code")) if ref else None
            if not row:
                continue  # 板块代码未在东财板块榜中 → 缺省，不臆造
            boards[sym] = {
                "board_name": row.get("name") or (ref or {}).get("name"),
                "board_code": row.get("board_code"),
                "kind": row.get("kind"),
                "level": (ref or {}).get("level"),
                "change_pct": row.get("change_pct"),
                "main_net_yi": row.get("main_net_yi"),
                "main_net_ratio": row.get("main_net_ratio"),
                "streak": row.get("streak"),
            }
        return {
            "boards": boards,
            "note": None if boards else "所属板块资金暂不可用（公司资料或板块列表取不到）",
        }

    _, payload = await cache.get_or_set(key, _build)
    return {"data": payload, "meta": _meta(hub)}


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
    return {"data": payload, "meta": _meta(hub)}


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
    return {"data": payload, "meta": _meta(hub)}


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
        "meta": await _dated_meta(hub, trade_date),
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
        return {"data": payload, "meta": _meta(hub)}

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
    return {"data": payload, "meta": _meta(hub)}


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
    d = date.fromisoformat(date_str) if date_str else beijing_today()
    try:
        rows = await hub.provider.get_auction_benchmark(d)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"竞价基准数据源失败：{exc}")
    return {"data": rows, "meta": _meta(hub)}


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
    payload = {"data": await collect_premium(hub, asof), "meta": _meta(hub)}
    cache.set(asof, payload)
    return payload


# /adjustment-events/{symbol} 端点已删除（2026-09-07 健康度审查 C1：全仓
# 0 引用的死端点；2026-09-08 P0-3 连带删除唯一消费方 minute_backtest 模块。
# provider 方法 get_adjustment_events 保留——复权事件流是数据能力层的
# marketdb 因子推算地基，th 端点存在即保留接入能力）。


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
        "meta": _meta(hub),
    }


async def _latest_trade_date(hub: QuoteHub) -> date:
    """最近一个**交易日**（走日历唯一入口 `trading_days`）。

    原实现直接调 `default_trade_date_weekend_fallback()`——它只处理周六/周日，
    遇节假日（如国庆假期里的工作日）会返回**当天这个非交易日**，龙虎榜必然返回空；
    用户看到"没有数据"却无法区分"确实没上榜"与"查的是非交易日"。
    日历不可用才退到周末规则（并记 warning，不静默）。
    """
    try:
        days = await trading_days(hub.provider)
        past = [d for d in days if d <= beijing_today()]
        if past:
            return max(past)
        log.warning("longhu: 交易日历无 ≤ 今日的交易日，退到周末回退规则")
    except Exception:  # noqa: BLE001  日历不可用不该让龙虎榜 500
        log.warning("longhu: 交易日历不可用，退到周末回退规则", exc_info=True)
    return default_trade_date_weekend_fallback()


@router.get("/longhu/{symbol}")
async def longhu_detail(
    symbol: str,
    date_str: str | None = Query(default=None, alias="date"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """个股龙虎榜：当日席位明细（买5/卖5+类型识别）+ 上榜历史（含 T+1/3/5/10 表现）。"""
    trade_date = date.fromisoformat(date_str) if date_str else await _latest_trade_date(hub)

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


# /limit-break 端点已删除（2026-09-07 健康度审查 C1：前端/脚本 0 引用，仅
# envelope 测试直调 handler；炸板池数据消费方——助手工具/theme_service/
# 情绪历史库——均走 provider.get_limit_break_pool，不受影响）。


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


@router.get("/news/content", response_model=Envelope[dict])
async def news_content(
    request: Request,
    url: str = Query(description="资讯原文链接（仅支持白名单域名）"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """资讯正文抓取（弹窗展示）：新闻/快讯解析文章页正文，公告走官方全文 API。

    必须注册在 /news/{symbol} 之前，否则 content 会被吞成股票代码。
    域名白名单外的 URL 直接 400（SSRF 防护 + 版权边界）。抓取/解析失败
    返回 502 与原因，前端据此降级为「摘要 + 原文链接」。
    """
    try:
        classify_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    cache = cache_on(request.app.state, "news.content", 600, maxsize=256)
    hit, cached = cache.get(url)
    if hit:
        return {"data": {**cached, "cached": True}, "meta": {**_meta(hub), "cached": True}}

    try:
        article = await fetch_article(url)
    except ArticleFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:  # 网络层异常统一收敛为可降级失败
        raise HTTPException(status_code=502, detail=f"正文抓取失败：{exc}")
    cache.set(url, article)
    return {"data": article, "meta": _meta(hub)}


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
async def search(q: str = Query(min_length=1, max_length=20), hub: QuoteHub = Depends(get_hub)):
    """代码/名称搜索（tencent→eastmoney failover，链内语义见 CompositeProvider.search）。

    - 进程内 30s TTL 缓存（挂 hub 单例，LRU 有界）：吸收中文 IME 逐字输入的
      前缀突发，同词并发单飞；空结果同样缓存（垃圾前缀不再反复打上游）。
    - 上游全挂（含熔断）→ 502，前端 role=alert 失败提示承接；
      全链"无匹配"→ 200 []，前端展示空结果提示——两种状态不再混为一谈
      （旧实现把 ProviderError 吞成 [] 再兜底 MockProvider，故障被伪装成"没搜到"）。
    - 响应带 Cache-Control: no-store：搜索结果依赖上游实时状态，禁止浏览器
      （尤其 Safari 的启发式缓存）把瞬断窗口里的空结果缓存下来反复回放。
    """
    cache = cache_on(hub, "market.search", 30, maxsize=256)
    kw = q.strip()

    async def _do() -> list[dict]:
        items = await hub.provider.search(kw)
        return [i.model_dump() for i in items]

    try:
        hit, rows = await cache.get_or_set(kw, _do)
    except ProviderError as exc:
        log.warning("search failed: %s", exc)
        raise HTTPException(status_code=502, detail="搜索数据源暂不可用，请稍后重试") from exc
    payload = {"data": rows, "meta": {**_meta(hub), "cached": hit}}
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- 题材梯队看板


async def _verify_board_multi_day(request: Request, board_payload: dict) -> None:
    """T3/B3（architecture-design §1）：用同花顺官方板块 K 线交叉验证/替换
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
    """L5（architecture-design §1）+ 09-08 簇级官方概念挂靠：见 app/services/official_match.py。

    盘面题材看板与猎场机会视图共用同一挂靠实现（成分重叠反查，非簇名精确匹配）。
    """
    from app.services.official_match import attach_official

    attach_official(request, themes_list)


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
    trade_date = date.fromisoformat(date_str) if date_str else await default_trade_date(hub)

    payload = await _theme_board_cached(request, hub, trade_date)

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


async def _theme_board_cached(request: Request, hub: QuoteHub, trade_date: date) -> dict:
    """题材看板（60s 缓存，与 /api/market/themes 共用同一缓存槽）。

    抽出来的理由：介入条件清单（/entry-checklist）也要这份 payload，重复取数等于
    把同一份重计算做两遍。`build_theme_board` 抛 RuntimeError（涨停池不可用）→ 502。
    """
    from app.services.theme_service import build_theme_board

    cache = cache_on(request.app.state, "market.themes", 60, maxsize=16)
    hit, payload = cache.get(trade_date)
    if hit:
        return payload
    # 读 Parquet 是同步阻塞调用，必须丢到线程池，否则会卡住事件循环
    # （曾导致整个服务无响应，连 /api/health 都超时）。
    try:
        board = await build_theme_board(
            hub.provider,
            trade_date,
            snapshot_map=await asyncio.to_thread(load_snapshot_map, request.app.state.snapshot_service, trade_date),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    payload = {"data": board, "meta": await _dated_meta(hub, trade_date)}
    cache.set(trade_date, payload)
    return payload


async def _market_phase_cached(request: Request, hub: QuoteHub) -> str | None:
    """当前市场相位（走共享 60s 情绪槽，见 `get_cached_sentiment`）。

    判不出来（日历不可用/计算失败）→ None = 未判定，绝不猜一个相位。
    降级姿势是**本消费方**的业务决策（相位缺失只影响市场层维度，不阻断清单），
    所以异常在这里吞、不写缓存（TTLCache 契约），`/market/sentiment` 仍照常 503。
    """
    from app.services.market_context import get_cached_sentiment

    try:
        result = await get_cached_sentiment(request.app.state, hub)
    except Exception as exc:  # noqa: BLE001  相位缺失只影响市场层维度，不阻断清单
        log.warning("entry-checklist: market phase unavailable: %s", exc)
        return None
    return (result or {}).get("phase")


@router.get("/market/entry-checklist")
async def market_entry_checklist(
    symbol: str = Query(min_length=6, max_length=12, description="裸6位代码或带后缀"),
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD，默认最近交易日"),
    request: Request = None,
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """介入条件清单（P1-13）：三层必须同时满足的信号 + 回避项 + 失效条件 + 时间窗口。

    回答「等确认往往已涨一轮，追进去又被套」——不给"明天买 X"，只给信号清单：
    市场层（环境允许不允许）→ 题材层（题材处在什么阶段）→ 个股层（封板质量够不够），
    三层都过才谈价位；同时给出「什么情况说明判断错了」的失效条件。

    输入**全部复用**已有计算：题材看板（60s 缓存，含 dragon_score/封单质量/题材阶段）
    + 情绪判定（60s 缓存，提供市场相位）。个股不在任何题材梯队时 found=False，
    只给市场层通用条件并显式标注「个股层未判定」，绝不臆造角色与封单质量。
    红线 3：输出是条件清单，不是买卖建议。
    """
    from app.services.dragon_service import entry_checklist
    from app.services.theme_service import entry_checklist_from_board

    td = date.fromisoformat(date_str) if date_str else await default_trade_date(hub)
    payload = await _theme_board_cached(request, hub, td)
    phase = await _market_phase_cached(request, hub)

    found = entry_checklist_from_board(payload["data"], symbol, market_phase=phase)
    if found is not None:
        data = {"found": True, **found, "market_phase": phase}
    else:
        data = entry_checklist(symbol=symbol, market_phase=phase)
        data.update({
            "found": False,
            "trade_date": td.isoformat(),
            "market_phase": phase,
            "note": (
                f"该标的 {td.isoformat()} 不在任何题材梯队（当日未涨停或未归入题材）"
                "→ 只给市场层通用条件，个股层的封单质量/角色/题材阶段均未判定。"
            ) + data["note"],
        })
    return {"data": data, "meta": _meta(hub)}


@router.get("/chip")
async def market_chip(
    symbol: str = Query(min_length=6, max_length=12, description="裸6位代码或带后缀"),
    full: bool = Query(default=False, description="回传全网格分布（研究用）"),
) -> dict:
    """筹码分布（CYQ 近似）：获利盘/集中度/主密集峰/支撑压力（方向2 数据基础）。

    口径：流通盘=窗口均量/1%假设换手（approx=True 恒标注）；形态稳健、
    获利盘绝对值仅供参考。marketdb 缺仓时 available=False 显式降级。
    """
    from app.market.chip import get_chip_service

    payload = get_chip_service().distribution(symbol, full=full)
    return {"data": payload, "meta": {}}


@router.get("/stock-flow")
async def market_stock_flow(symbols: str = Query(default="", description="逗号分隔6位代码，≤60 只")) -> dict:
    """个股资金流（当日累计五档净额，亿元；方向2 P1）。

    口径见 app/market/stock_flow.py：北交所无个股资金流 → 进 no_data 显式列出；
    全部失败 → items 空且 degraded 非空（绝不填 0）。
    """
    from app.market import stock_flow

    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    payload = await stock_flow.get_stock_flow(syms)
    return {"data": payload, "meta": {}}
