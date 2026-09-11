"""每日精选 API（CONTEXT.md: Daily Picks 域）。

- GET  /api/picks/today           当日组合（从库读；不自动生成——生成较重，POST 触发）
- POST /api/picks/generate        生成今日组合（写鉴权；收盘后复盘管线或手动触发）
- GET  /api/picks/history         历史组合（近 N 日）
- GET  /api/picks/review?date=    复盘日志
- POST /api/picks/review/generate 对最近组合生成/刷新复盘（写鉴权）
- GET  /api/picks/meta            走坏原因分布（周末权重微调建议的输入）

数据编排全部复用既有管线：候选池（活跃事件标的池 + 涨停池 + 热股榜）→ 五维评分
（情绪=情绪引擎 / 消息=EventCard 方向 / 技术=score_stock / 基本面=财务 /
资金=资金流+快照）→ 加权合成 + 一票否决 → **入选门槛（MIN_PICK_SCORE，不够格不凑数）**
→ 换股门槛 → 持久化。

名单长度声明（2026-09-10）：**盘前选择不是"每天 5 只"**。MAX_PICKS 是容量上限，
实际只数 = 当日达到入选门槛的标的数（可能 0~5，弱市就该更短）。语义上与盘中跟踪的
`top_watch_stocks`（unknown/低不入选）对齐，只是筛选依据换成六维综合分。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.api.deps import get_hub, require_write_token
from app.core.db import get_session_factory
from app.market.trading_status import beijing_now
from app.events.store import EventStore
from app.market import trade_calendar as tc
from app.market.tech_score import score_stock, sma
from app.market.chip import get_chip_service
from app.picks.chip_signal import chip_basis_text, evaluate_chip_signal
from app.picks.echelon import classify_echelon_role, score_echelon
from app.picks.meta_confidence import classify_confidence
from app.picks.rps import get_rps_service
from app.picks.engine import (
    MAX_PICKS,
    apply_replacement_threshold,
    build_buy_range,
    effective_limits,
    score_capital,
    score_fundamental,
    score_news,
    score_sentiment,
    event_weight,
    score_tech,
    synthesize,
)
from app.picks.gate import apply_gate_to_picks, evaluate_stand_aside
from app.picks.halt_risk import BENCHMARK_INDEX, assess, benchmark_symbol, board_of, risk_labels, veto_reasons
from app.picks.regime import detect_regime, earnings_event_ratio, weights_for
from app.picks.style_router import apply_style_offsets, route_style, style_note
from app.picks.risk import build_invalidations, exit_discipline, risk_tier_of, stop_loss_reference
from app.services.quote_enrich import fill_valuation
from app.services.quote_hub import QuoteHub

log = logging.getLogger(__name__)
router = APIRouter(prefix="/picks", tags=["daily-picks"])

CANDIDATE_CAP = 40      # 候选池上限（深度评分前）
DEEP_DIVE_CAP = 24      # 深度评分上限（每只要拉 K 线/财务/资金流）
CONCURRENCY = 6


def _store(request: Request) -> EventStore:
    return request.app.state.event_store


def _db():
    # Session 实例（支持 with 自动 close）；sessionmaker 本身不支持 with（AGENTS.md 6.3）
    return get_session_factory()()


async def _batch_quotes(hub: QuoteHub, symbols: list[str]) -> dict[str, Any]:
    """腾讯批量快照（与个股行情同源），50 只/批。返回 symbol→Quote。
    2026-09-07 R3 收口：实现单点在 quote_enrich.fetch_quotes_batched。"""
    from app.services.quote_enrich import fetch_quotes_batched

    return await fetch_quotes_batched(hub, symbols)


async def _candidate_pool(hub: QuoteHub, store: EventStore, svc, request: Request) -> list[dict]:
    """候选池 = 活跃事件标的池 ∪ 当日涨停池 ∪ 热股榜 top，去重剔 ST，cap 40。

    三路来源天然覆盖「突发消息引发的极端盘面」：事件池是消息源，
    涨停池是大幅拉升的极端表现，热股榜是关注度信号。
    """
    symbols: dict[str, dict] = {}
    name_to_code = {t.name: t.code for t in svc.get_catalog(limit=1000)} if svc else {}

    # ① 活跃事件：symbol 方向直接收；theme 方向反查官方成分（cap 30/题材）
    try:
        for row in store.list_events(active_only=True, limit=30):
            for d in store.directions_of(row.id):
                if d.target_type == "symbol" and d.target.isdigit() and len(d.target) == 6:
                    symbols.setdefault(d.target, {"from": "event", "prio": 1})
                elif d.target_type == "theme" and svc is not None:
                    code = name_to_code.get(d.target)
                    if not code:
                        continue
                    for m in svc.get_members(code)[:30]:
                        symbols.setdefault(m.symbol, {"from": "event_theme", "prio": 1})
    except Exception as exc:
        log.warning("picks candidate: events failed: %s", exc)

    # ② 当日涨停池（突发大幅拉升的极端表现）
    try:
        days = await tc.trading_days(hub.provider)
        td = tc.last_trade_date(days)
        if td:
            for r in await hub.provider.get_limit_up_pool(td):
                symbols.setdefault(r.symbol, {"from": "limit_up", "prio": 1})
    except Exception as exc:
        log.warning("picks candidate: limit-up pool failed: %s", exc)

    # ③ 热股榜 top 20（关注度信号，B1 同源）
    try:
        for s in (await hub.provider.get_hot_stock_list("day"))[:20]:
            symbols.setdefault(s["symbol"], {"from": "hot", "prio": 0})
    except Exception as exc:
        log.warning("picks candidate: hot list failed: %s", exc)

    out = []
    for sym, meta in symbols.items():
        if not sym.isdigit() or len(sym) != 6:
            continue
        out.append({"symbol": sym, **meta})
        if len(out) >= CANDIDATE_CAP:
            break
    return out


async def _limit_up_context(hub: QuoteHub) -> dict:
    """涨停板生态上下文（联合研判的题材级证据）。

    返回：
    - records: {symbol: {连板数/炸板数/首封时间/流通市值/涨停原因}}
    - market_max_boards: 全市场最高连板（判定"空间板"必需）
    - themes: {题材标签: {symbols, max_boards, changes, levels}}（题材天梯健康度输入）
    """
    out: dict[str, Any] = {"records": {}, "market_max_boards": 0, "themes": {}}
    try:
        days = await tc.trading_days(hub.provider)
        td = tc.last_trade_date(days)
        if not td:
            return out
        pool = await hub.provider.get_limit_up_pool(td)
    except Exception as exc:
        log.warning("picks echelon: limit-up pool failed: %s", exc)
        return out

    from app.services.theme_service import parse_theme_tags

    boards_all: list[int] = []
    for r in pool:
        boards = r.consecutive_boards or 1
        boards_all.append(boards)
        out["records"][r.symbol] = {
            "consecutive_boards": boards,
            "break_count": r.break_count,
            "first_seal_time": r.first_seal_time,
            "float_market_cap": r.float_market_cap,
            "amount": r.amount,
            "reason": r.reason,
            "change_pct": r.change_pct,
        }
        for tag in parse_theme_tags(r.reason):
            t = out["themes"].setdefault(
                tag, {"symbols": [], "max_boards": 0, "changes": [], "levels": {}}
            )
            t["symbols"].append(r.symbol)
            t["max_boards"] = max(t["max_boards"], boards)
            if r.change_pct is not None:
                t["changes"].append(r.change_pct)
            t["levels"][boards] = t["levels"].get(boards, 0) + 1
    out["market_max_boards"] = max(boards_all) if boards_all else 0
    return out


def _theme_benchmark(
    *, svc, lu_ctx: dict, symbol: str, market_pct: float | None
) -> tuple[float | None, str | None]:
    """个股所属题材的当日基准涨幅（优先最强题材，匹配不到回退大盘）。

    回退大盘是诚实降级：宁可用确定性高的弱基准，也不臆造题材归属。
    """
    if svc is None:
        return market_pct, None
    try:
        themes = svc.get_official_for_symbol(symbol)
    except Exception:
        return market_pct, None
    best: float | None = None
    best_name: str | None = None
    for t in themes or []:
        name = t.get("theme_name")
        stat = (lu_ctx.get("themes") or {}).get(name)
        if not stat or not stat.get("changes"):
            continue
        avg = sum(stat["changes"]) / len(stat["changes"])
        if best is None or avg > best:
            best, best_name = avg, name
    if best is not None:
        return best, best_name
    return market_pct, None


def _atr_pct(bars: list[dict]) -> float | None:
    """ATR14 / 最新收盘（百分数）。样本不足或字段缺失返回 None（不臆造波动率）。"""
    if len(bars) < 15:
        return None
    trs: list[float] = []
    prev_close: float | None = None
    for b in bars[-15:]:
        h, l, c = b.get("high"), b.get("low"), b.get("close")
        if h is None or l is None or c is None:
            return None
        if prev_close is not None:
            trs.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
        prev_close = c
    if len(trs) < 14 or not prev_close:
        return None
    return round(sum(trs[-14:]) / 14 / prev_close * 100, 2)


def _ma_value(bars: list[dict], n: int) -> float | None:
    """n 日均线最新值（失效条件的客观参照）。"""
    closes = [b.get("close") for b in bars]
    if len(closes) < n or any(c is None for c in closes):
        return None
    series = sma([float(c) for c in closes], n)
    last = series[-1] if series else None
    return round(last, 2) if last is not None else None


def _primary_theme_of(lu_ctx: dict, symbol: str, fallback: str | None = None) -> str | None:
    """个股的主题材（取其在涨停池中所属的最强题材；非涨停股走 fallback）。"""
    best: str | None = None
    best_boards = -1
    for tag, st in (lu_ctx.get("themes") or {}).items():
        if symbol in st["symbols"] and st["max_boards"] > best_boards:
            best, best_boards = tag, st["max_boards"]
    return best or fallback


def _theme_stage_of(lu_ctx: dict, theme_name: str | None) -> dict:
    """题材天梯健康度。题材不可得时返回空上下文（不臆造阶段）。"""
    st = (lu_ctx.get("themes") or {}).get(theme_name) if theme_name else None
    if not st:
        return {"stage": None, "completeness": None, "adjust": 1.0, "stage_basis": []}
    from app.picks.echelon import theme_ladder_health

    return theme_ladder_health(
        limit_up_count=len(st["symbols"]),
        max_boards=st["max_boards"],
        reopen_rate=0.0,  # 封板率需炸板池逐题材统计，第一版不细分（basis 已标注）
        levels=st["levels"],
    )


def _prev_combo_symbols() -> list[str]:
    """上一份组合的成员（换股门槛与 carryover 都依赖它）。"""
    with _db() as db:
        from app.models.daily_pick import DailyPickSet

        row = db.execute(
            select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(1)
        ).scalar_one_or_none()
        if not row:
            return []
        try:
            return [i["symbol"] for i in json.loads(row.items)]
        except Exception:
            return []


def _parse_meta(raw: str | None) -> dict:
    """组合 meta（权重/炒作阶段/空仓闸门）解析。损坏时返回空字典而不是 500。"""
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _build_event_hits_index(store: EventStore) -> dict[str, tuple[float, float, str | None, str | None, int]]:
    """一次遍历活跃事件 → 按 symbol 索引消息命中（评审 B1）。

    返回 {symbol: (利好强度和, 利空强度和, 主事件标题, 主方向文案, 关联数)}。
    强度和已乘 `event_weight(source_tier, certainty)`（选股 2.0 §3）：
    tier1 官方落地政策 ≈ 25 条 tier5 自媒体传闻的权重，消息面不再被
    同质化的条数淹没。list_events 已 selectinload 预加载方向行（detached
    后仍可安全访问），索引构建零额外查询——原实现每候选股重复全量扫事件表
    （24 只深评 × 每次约 31 次查询）；关联数含 direction=0（"来源关联、
    方向待判"也是证据，丢掉它会让消息面对有新闻但无方向词的标的显示"无命中"）。
    """
    index: dict[str, dict] = {}
    try:
        for row in store.list_events(active_only=True, limit=30):
            w = event_weight(row.source_tier, row.certainty)
            for d in row.directions:
                if d.target_type != "symbol" or not d.target:
                    continue  # 题材方向的个股传导第一版不计入单股消息分（防过度外推）
                agg = index.setdefault(
                    d.target,
                    {"bull": 0, "bear": 0, "linked": 0, "top_title": None, "top_dir": None, "pending_title": None},
                )
                agg["linked"] += 1
                if d.direction == 1:
                    agg["bull"] += d.strength * w
                elif d.direction == -1:
                    agg["bear"] += d.strength * w
                if agg["top_title"] is None and d.direction != 0:
                    agg["top_title"] = row.title
                    agg["top_dir"] = "利好" if d.direction == 1 else "利空"
                if agg["pending_title"] is None and d.direction == 0:
                    agg["pending_title"] = row.title
    except Exception as exc:
        log.warning("picks event index failed: %s", exc)
        return {}
    out: dict[str, tuple[int, int, str | None, str | None, int]] = {}
    for sym, agg in index.items():
        # 无方向词时给出关联标题（证据可见）
        out[sym] = (agg["bull"], agg["bear"], agg["top_title"] or agg["pending_title"], agg["top_dir"], agg["linked"])
    return out


async def _prefetch_index_bars(hub: QuoteHub) -> dict[str, list[dict]]:
    """预取各板块基准指数日 K（偏离值计算的分母）。

    ⚠️ 必须在并发评分**之前**一次性取完复用：24 只候选股各拉一次指数 = 请求量
    翻 5 倍，而腾讯源有熔断（实测连续请求直接 502「熔断冷却中 19s」），
    会把整个选股流程拖垮。指数当日不变，取一次足够。

    取不到时对应板块留空列表——`assess` 会把偏离值类规则降级为「不可评」
    并显式标注，绝不拿个股涨幅冒充偏离值。
    """
    # key 用**指数代码**而非板块名：查询侧是 `index_bars.get(benchmark_symbol(board, sym))`，
    # 而 benchmark_symbol 返回的是代码。用板块名作 key 会全部 miss → 偏离值恒为 None
    # （静默降级成"指数数据缺失"，看不出是 key 写错）。
    out: dict[str, list[dict]] = {}
    for board, sym in BENCHMARK_INDEX.items():
        try:
            bars = await hub.provider.get_kline(sym, "1d", None, None)
            out[sym] = [b.model_dump() if hasattr(b, "model_dump") else dict(b) for b in bars][-250:]
        except Exception as exc:
            log.warning("picks: index kline failed (%s): %s", sym, exc)
            out[sym] = []
    return out


async def _deep_score_candidates(
    deep: list[dict],
    *,
    hub: QuoteHub,
    svc,
    lu_ctx: dict,
    market_pct: float | None,
    market_phase: str | None,
    quotes: dict,
    weights: dict,
    event_hits_index: dict[str, tuple[float, float, str | None, str | None, int]],
    concurrency: int,
    promo_percentile: float | None = None,
    index_bars: dict[str, list[dict]] | None = None,
    style: dict | None = None,
) -> list[dict]:
    """④ 逐只深度评分（并发；每只独立异常兜底）。

    从 generate_picks 拆出（全项目审查 T6：主函数 320 行 → 流水线编排 +
    本函数）。输入候选已带 name/price/change_pct/amount。

    :param index_bars: 各板块基准指数日 K（`_prefetch_index_bars` 预取），
        供停牌核查/异动风险评估计算偏离值。
    """
    sem = asyncio.Semaphore(concurrency)
    index_bars = index_bars or {}
    # RPS 全市场截面（一次查询、服务内当日缓存；marketdb 未建/陈旧 → {} →
    # score_stock 的 rps 维自动取中性 0.5，不臆造分位）。同步 DuckDB 查询
    # 放线程池，不占事件循环。
    rps_svc = get_rps_service()
    rps_map = await asyncio.to_thread(rps_svc.snapshot)
    # 三态诚实：RPS 为空时把**真实原因**带到卡片依据里（缺仓 / 陈旧 / 窗口不足
    # 语义完全不同；统一写"仓未建"会让"仓在但停更 6 个交易日"被误读为没数据源）
    rps_note = None
    if not rps_map:
        fr = await asyncio.to_thread(rps_svc.freshness)
        if fr.get("stale"):
            rps_note = (f"RPS 数据陈旧（库内最新 {fr['latest']}，滞后 {fr['lag']} 个交易日），"
                        "中性处理——修复：scripts/sync_marketdb.py")
        elif not fr.get("available"):
            rps_note = (f"RPS 未覆盖（{fr.get('reason') or 'marketdb 仓未建/未回补'}），"
                        "中性处理")

    async def _score_one(c: dict) -> dict | None:
        sym = c["symbol"]
        async with sem:
            sub: dict[str, float] = {}
            bases: dict[str, str] = {}
            # 技术（防飞刀口径 score_stock，v3 含 RPS 横截面）
            try:
                bars = await hub.provider.get_kline(sym, "1d", None, None)
                dicts = [b.model_dump() if hasattr(b, "model_dump") else dict(b) for b in bars][-250:]
                s_tech, b_tech = score_tech(score_stock(dicts, rps=rps_map.get(sym), rps_note=rps_note))
            except Exception:
                dicts = []
                s_tech, b_tech = 50.0, "K线数据缺失，中性"
            # 出场纪律的输入：ATR（止损宽度）与均线（失效条件参照）
            atr_pct = _atr_pct(dicts)
            ma5 = _ma_value(dicts, 5)
            ma10 = _ma_value(dicts, 10)
            sub["tech"], bases["tech"] = s_tech, b_tech
            # 消息（B1：查预构建索引，O(1)——不再逐候选扫事件表）
            bull, bear, top_title, top_dir, linked = event_hits_index.get(sym, (0, 0, None, None, 0))
            sub["news"], bases["news"] = score_news(bull, bear, top_title, top_dir)
            if bull == bear == 0 and linked:
                # 有关联但无方向词：诚实说"命中了但待判"，而不是"无命中"
                bases["news"] = (
                    f"命中 {linked} 条关联事件（标题无方向词，方向待判），消息面中性；"
                    f"最近：「{(top_title or '')[:40]}」"
                )
            # 基本面：成长性/盈利质量来自财务报告（营收增速、净利同比、ROE、毛利率——
            # normalizer 早已提取这四个字段，2026-09-01 起评分全部消费），估值来自行情快照
            rev = None
            profit = None
            roe_v = None
            gm = None
            try:
                fin = await hub.provider.get_financials(sym, 4)
                if fin:
                    latest = fin[0] if isinstance(fin, list) else fin
                    d_ = latest if isinstance(latest, dict) else getattr(latest, "__dict__", {})
                    rev = d_.get("revenue_yoy")
                    profit = d_.get("profit_yoy")
                    roe_v = d_.get("roe")
                    gm = d_.get("gross_margin")
            except Exception as exc:
                log.warning("picks financials %s failed: %s", sym, exc)
            # ⚠️ PE 需要现价，财务报告里本来就没有（此前从 financials 取 pe_ttm → 恒 None）。
            # 估值应取自行情快照；链首 ths 不带该字段，用 fill_valuation 从腾讯补。
            pe = None
            q_snap = quotes.get(sym)
            if q_snap is not None:
                if q_snap.pe_ttm is None:
                    q_snap = await fill_valuation(hub.provider, q_snap)
                pe = q_snap.pe_ttm if q_snap is not None else None
            sub["fundamental"], bases["fundamental"] = score_fundamental(
                pe, rev, profit_yoy=profit, roe=roe_v, gross_margin=gm
            )
            # 资金
            net_inflow = None
            try:
                flow = await hub.provider.get_capital_flow(sym, 5)
                if flow:
                    last = flow[-1] if isinstance(flow, list) else flow
                    d_ = last if isinstance(last, dict) else getattr(last, "__dict__", {})
                    # ⚠️ 新浪资金流字段名是 net_main（主力净额，元），不是 net_amount。
                    # 此前写错字段名 → 恒为 None → 资金面永远显示"数据缺失"，
                    # 被静默降级掩盖成了"数据源问题"（2026-08-31 修复）。
                    net_inflow = d_.get("net_main")
            except Exception as exc:
                log.warning("picks capital flow %s failed: %s", sym, exc)
            sub["capital"], bases["capital"] = score_capital(net_inflow, None, on_lhb=False)
            # 情绪（全局相位；题材涨家占比第一版缺省；promo 历史分位为接力环境修正，
            # 选股 2.0 §3——分位来自 P0-3b 校准库，缺失时不修正、basis 如实呈现）
            sub["sentiment"], bases["sentiment"] = score_sentiment(
                market_phase, None, promo_percentile=promo_percentile
            )

            # 梯队（第六维）：个股在题材天梯中的地位 × 题材阶段，联合读取。
            # 没有这一维，退潮期的最后一棒会和发酵期的真龙头拿同样分。
            benchmark, theme_name = _theme_benchmark(
                svc=svc, lu_ctx=lu_ctx, symbol=sym, market_pct=market_pct
            )
            excess = (
                round(c["change_pct"] - benchmark, 2)
                if c["change_pct"] is not None and benchmark is not None
                else None
            )
            theme_name = _primary_theme_of(lu_ctx, sym, theme_name)
            theme_ctx = _theme_stage_of(lu_ctx, theme_name)
            lu = lu_ctx["records"].get(sym)
            if lu:
                role, role_basis = classify_echelon_role(
                    is_limit_up=True,
                    consecutive_boards=lu["consecutive_boards"],
                    theme_max_boards=(
                        (lu_ctx["themes"].get(theme_name) or {}).get("max_boards")
                        or lu["consecutive_boards"]
                    ),
                    market_max_boards=lu_ctx["market_max_boards"],
                    float_market_cap=lu["float_market_cap"],
                    first_seal_time=lu["first_seal_time"],
                    break_count=lu["break_count"],
                )
            else:
                role, role_basis = classify_echelon_role(
                    is_limit_up=False,
                    float_market_cap=None,  # 快照无流通市值字段，不臆造（诚实降级为同步/领涨）
                    excess_pct=excess,
                )
            s_ech, b_ech = score_echelon(
                role=role,
                stage=theme_ctx["stage"],
                completeness=theme_ctx["completeness"],
            )
            sub["echelon"] = s_ech
            bases["echelon"] = (
                f"{role_basis}；{b_ech}"
                + (f"；题材「{theme_name}」" if theme_name else "；未匹配到题材（按个股独立评估）")
            )

            # 停牌核查 / 异动风险（docs/halt-check-risk-analysis.md 第一批）：
            # 只依据已取到的个股日 K + 预取的指数日 K，零新增数据源。
            # 红线进 veto（×0.4 重罚并显式记录），黄线在合成后按扣分扣减。
            # ⚠️ R3（当前停牌）暂不接：picks 流程没有可靠的 trading_status 来源，
            # 硬猜会把"当日无成交"误判成停牌。接口已留，第二批接入（见模块 docstring）。
            halt = assess(
                symbol=sym,
                name=c.get("name"),
                bars=dicts,
                index_bars=index_bars.get(benchmark_symbol(board_of(sym, c.get("name")), sym), []),
            )
            # 筹码形态（P1 派发/吸筹规则化）：marketdb CYQ 近似 × 量价组合。
            # 只留痕不进权重——先在理由中积累样本，滚动验证胜率后再议进权重
            # （strategy-evolution-plan §方向2）。DuckDB 同步查询丢线程池；
            # ChipService 内置 TTLCache（30min），候选重复不重复查库。
            chip = await asyncio.to_thread(get_chip_service().distribution, sym)
            chip_sig = evaluate_chip_signal(chip, dicts)
            bases["chip"] = chip_basis_text(chip_sig, chip)
            score, vetoes = synthesize(sub, weights=weights, vetoes=veto_reasons(halt))
            if halt["penalty"]:
                score = round(max(0.0, score - halt["penalty"]), 1)
            # meta 置信层（P1 规则版）：综合分+相位+筹码+红线 → 三档置信
            # （替代 gate 二值跳变的统一置信语言；gate 保留为兜底）。
            # 相位维度扩展（审查 §4.1）：当日风格路由结果挂进置信理由留痕。
            confidence = classify_confidence(
                score=score,
                sub_scores=sub,
                phase=market_phase,
                chip_signal=chip_sig.get("signal"),
                halt_penalty=halt.get("penalty") or 0.0,
                veto_count=len(vetoes),
                style_note=style_note(style),
            )
            return {
                "symbol": sym, "name": c["name"], "price": c["price"], "change_pct": c["change_pct"],
                "halt_risk": halt,
                "halt_risk_labels": risk_labels(halt),
                # 估值此前**只用于基本面打分，没有透出到卡片**——选股页因此永远看不到 PE，
                # 而个股详情页有（走 /api/quotes 的 fill_valuation）。同一标的两个口径不一致。
                "pe_ttm": pe,
                "pb": getattr(q_snap, "pb", None) if q_snap is not None else None,
                "score": score, "sub_scores": sub, "bases": bases, "vetoes": vetoes,
                "related_events": [top_title] if top_title else [],
                "echelon_role": role,
                "echelon_basis": bases["echelon"],
                "theme": theme_name,
                "theme_stage": theme_ctx["stage"],
                # 连板高度（gate 可跟判据的第一要素；非涨停股 None，不臆造）
                "boards": lu["consecutive_boards"] if lu else None,
                "atr_pct": atr_pct,
                "ma5": ma5,
                "ma10": ma10,
                # 筹码信号（派发警示/启动观察，None=未触发）与三档置信
                "chip_signal": chip_sig,
                "confidence": confidence,
            }

    results = await asyncio.gather(*[_score_one(c) for c in deep])
    return [r for r in results if r is not None]


def _assemble_card(k: dict) -> dict:
    """⑥ 单只入选标的的卡片组装（风险档位 + 买入范围 + 出场纪律 + 失效条件）。"""
    role = k.get("echelon_role") or ""
    tier = risk_tier_of(role)
    ma5, ma10 = k.get("ma5"), k.get("ma10")
    # 买入范围的技术位收敛：均线在现价下方作支撑、上方作压力
    support = min([v for v in (ma5, ma10) if v and v < k["price"]], default=None)
    resistance = max([v for v in (ma5, ma10) if v and v > k["price"]], default=None)
    return {
        "symbol": k["symbol"],
        "name": k["name"],
        "price": k["price"],
        "change_pct": k["change_pct"],
        # 估值透出（可能为 None：数据源未提供，前端按"暂无+原因"展示，不臆造）
        "pe_ttm": k.get("pe_ttm"),
        "pb": k.get("pb"),
        "score": k["score"],
        "sub_scores": k["sub_scores"],
        "bases": k["bases"],
        "vetoes": k["vetoes"],
        "buy_range": build_buy_range(k["price"], support, resistance),
        "echelon_role": role,
        "echelon_basis": k.get("echelon_basis", ""),
        "theme": k.get("theme"),
        "theme_stage": k.get("theme_stage"),
        "boards": k.get("boards"),
        "risk_tier": tier,
        "stop_loss": stop_loss_reference(
            price=k["price"], tier=tier, atr_pct=k.get("atr_pct")
        ),
        "exit_discipline": exit_discipline(tier),
        "invalidations": build_invalidations(
            role=role,
            tier=tier,
            theme_stage=k.get("theme_stage"),
            ma_value=(ma5 if tier in ("龙头博弈", "情绪低位") else (ma10 or ma5)),
            event_titles=k["related_events"],
        ),
        "themes": [k["theme"]] if k.get("theme") else [],
        "related_events": k["related_events"],
        # 停牌核查 / 异动风险（第一批：R1/R2 红线 + Y1/Y2/Y3 黄线 + P1/P2 仓位约束）
        "halt_risk": k.get("halt_risk"),
        "halt_risk_labels": k.get("halt_risk_labels") or [],
        # 筹码信号（派发警示/启动观察）+ meta 三档置信（规则版）
        "chip_signal": k.get("chip_signal"),
        "confidence": k.get("confidence"),
    }


@router.post("/generate")
async def generate_picks(request: Request, hub: QuoteHub = Depends(get_hub), _: None = Depends(require_write_token)) -> dict:
    """生成今日组合（T 日收盘后跑，产出 T+1 组合；重复生成覆盖当日行）。"""
    store = _store(request)
    svc = getattr(request.app.state, "theme_catalog", None)
    today = date.today().isoformat()

    # ① 候选池
    candidates = await _candidate_pool(hub, store, svc, request)

    # ①a 昨日组合成员兜底纳入（carryover）：
    # 组合稳定性要求 incumbent 有"被重新评估的权利"——否则一只票今天没涨停、
    # 没上热榜、事件又过期，就会被静默踢出，组合天天大换血（跨日回放实测：
    # 纯涨停股候选池下日均换手 60%）。纳入后它仍要重新评分，分数不够照样被换，
    # 只是不再因为"没进榜"而消失。
    prev_symbols = _prev_combo_symbols()
    have = {c["symbol"] for c in candidates}
    for s in prev_symbols:
        if s not in have:
            candidates.append({"symbol": s, "from": "carryover", "prio": 1})
    carryover_set = set(prev_symbols) - have

    # ①b 涨停板生态上下文（梯队地位判定的题材级证据）
    lu_ctx = await _limit_up_context(hub)

    # ①c 市场基准（上证当日涨跌幅）：题材基准匹配不到时的诚实回退
    market_pct = None
    try:
        ov = await hub.provider.get_market_overview()
        for i in getattr(ov, "indices", None) or []:
            if getattr(i, "symbol", "") in ("000001", "sh000001"):
                market_pct = getattr(i, "change_pct", None)
    except Exception as exc:
        log.warning("picks: market overview failed: %s", exc)

    # ② 批量快照 + 预筛（剔 ST/退/无行情）
    quotes = await _batch_quotes(hub, [c["symbol"] for c in candidates])
    deep: list[dict] = []
    for c in candidates:
        q = quotes.get(c["symbol"])
        if q is None or q.price is None or q.price <= 0:
            continue
        name = q.name or ""
        if "ST" in name.upper() or "退" in name:
            continue
        c.update({"name": name, "price": q.price, "change_pct": q.change_pct, "amount": q.amount})
        c["_prio"] = c.get("prio", 0) * 1000 + (q.change_pct or 0)
        deep.append(c)
    # 昨日成员优先进入深度评估：它们已经有仓位逻辑在身，不该因涨幅不高被截断
    deep.sort(key=lambda c: (-(1 if c["symbol"] in carryover_set else 0), -c["_prio"]))
    deep = deep[:DEEP_DIVE_CAP]

    # ③ 全局情绪（一次）
    market_phase = None
    sent: dict = {}
    try:
        from app.services.market_context import compute_market_sentiment

        sent = await compute_market_sentiment(hub, request.app.state.snapshot_service) or {}
        market_phase = sent.get("phase")
    except Exception as exc:
        log.warning("picks sentiment failed: %s", exc)

    if market_phase is None:
        # 2026-09-09：相位缺失会让 style_routing 不路由——当日评分丢掉风格偏移，
        # 且写入 meta 后定格全天。最常见原因是快照 breadth 未就绪（含刚重启补跑），
        # 等一拍再取一次，比让全天评分裸奔便宜得多；仍失败则诚实留 None。
        await asyncio.sleep(5)
        try:
            sent = await compute_market_sentiment(hub, request.app.state.snapshot_service) or {}
            market_phase = sent.get("phase")
        except Exception as exc:  # noqa: BLE001
            log.warning("picks sentiment retry failed: %s", exc)

    # ③b 炒作阶段（Regime）：财报日历 + 业绩事件密度 → 六维权重表。
    # 业绩空窗期必须把基本面权重让给情绪与题材梯队，否则系统性错过妖股。
    try:
        ev_texts = [e.title for e in store.list_events(active_only=True, limit=30)]
    except Exception:
        ev_texts = []
    regime = detect_regime(
        today=date.today(),
        earnings_ratio=earnings_event_ratio(ev_texts) if ev_texts else None,
        event_count=len(ev_texts),
    )
    weights = weights_for(regime["regime"])
    # ③b' 相位→风格路由（审查报告 §4.1）：在 regime 基础权重上按市场情绪相位
    # 做当日微调（叠加不替代）；偏移表配置可覆盖，路由结果随 meta 留痕。
    style = route_style(market_phase)
    weights = apply_style_offsets(weights, style["offsets"])

    # ③c 空仓闸门：情绪转弱时主动提示规避（红线 3：只提示，不下指令）
    break_rate = None
    try:
        days = await tc.trading_days(hub.provider)
        td = tc.last_trade_date(days)
        if td:
            breaks = await hub.provider.get_limit_break_pool(td)
            n_zt, n_br = len(lu_ctx["records"]), len(breaks or [])
            break_rate = round(n_br / max(n_zt + n_br, 1), 3)
    except Exception as exc:
        log.warning("picks gate: break pool failed: %s", exc)
    limit_down = None
    try:
        limit_down = (request.app.state.snapshot_service.breadth or {}).get("limit_down")
    except Exception:
        limit_down = None

    # 当日分位（2026-09-10 修正 + P1-31）：
    # ①**必须用当日实测值算分位**——`sent.calibration.percentile` 是「历史库最后一行」
    #   的分位，而库里最后一行是上一个交易日（backfill 刻意不回补今天），拿它当
    #   "今天的分位"等于每天用昨天的位置描述今天，库一停更就变成用上个月的位置；
    # ②闸门（晋级率/炸板率）与情绪面评分共用同一处计算，避免两个口径各自漂移。
    # 样本不足/值缺失 → None，闸门自动回落绝对经验值并在理由里写明（不静默）。
    promo_1to2 = (sent.get("promotion") or {}).get("promo_1to2")
    promo_pctl = None
    break_pctl = None
    try:
        from app.sentiment import metric_history

        promo_pctl = (metric_history.percentile_of_value("promo_1to2", promo_1to2) or {}).get("percentile")
        break_pctl = (metric_history.percentile_of_value("break_rate", break_rate) or {}).get("percentile")
    except Exception as exc:
        log.warning("picks percentile_of_value failed: %s", exc)

    gate = evaluate_stand_aside(
        phase=market_phase,
        promotion_1to2=promo_1to2,
        promotion_1to2_pctl=promo_pctl,
        break_rate=break_rate,
        break_rate_pctl=break_pctl,
        limit_down=limit_down,
        prev_zt_median_pct=(sent.get("prev_perf") or {}).get("median_pct"),
        phase_unreliable=bool(sent.get("phase_unreliable")),
    )
    if gate["stand_aside"]:
        log.warning("picks gate triggered (%s): %s", gate["level"], "；".join(gate["reasons"]))

    # ③d 消息命中索引（B1）：一次遍历活跃事件按 symbol 建索引——
    # 原实现每候选股在并发任务里重复全量扫事件表（24×~31 次同步查询）
    # ③e 基准指数日 K（停牌核查/异动的偏离值分母）：一次性预取，供全部候选复用。
    # 24 只候选各拉一次会触发腾讯熔断，必须在这里取完。
    index_bars = await _prefetch_index_bars(hub)

    # ③d 消息命中索引（B1）：一次遍历活跃事件按 symbol 建索引——
    # 原实现每候选股在并发任务里重复全量扫事件表（24×~31 次同步查询）
    event_hits_index = _build_event_hits_index(store)

    # 晋级率历史分位（选股 2.0 §3）：**当日值**在历史样本中的位置（见上方 ③c 说明）。
    # 库样本不足时为空 → 情绪面修正项自动缺席，basis 如实呈现。
    promo_pct = promo_pctl

    # ④ 逐只深度评分（并发；T6 拆分至 _deep_score_candidates）
    ranked = await _deep_score_candidates(
        deep,
        hub=hub,
        svc=svc,
        lu_ctx=lu_ctx,
        market_pct=market_pct,
        market_phase=market_phase,
        quotes=quotes,
        weights=weights,
        event_hits_index=event_hits_index,
        concurrency=CONCURRENCY,
        promo_percentile=promo_pct,
        index_bars=index_bars,
        style=style,
    )
    ranked.sort(key=lambda r: -r["score"])

    # ⑤ 入选门槛 + 换股门槛（昨日组合；prev_symbols 已在 ①a 载入，此处不重复查库）
    #    入选门槛（MIN_PICK_SCORE）保证「够格几只就是几只」，MAX_PICKS 只是容量上限。
    #    三档阈值默认取运行时生效值（控制台参数白名单 P1-15 的覆盖层优先）。
    kept, replaced = apply_replacement_threshold(prev_symbols, ranked)
    limits = effective_limits()

    # ⑤a 落选者落库（消融验证 P3 数据地基，2026-09-01 用户批准启动）：
    # 深评过但未进组合的候选（分数不够/门槛拦截/上限截断），精简摘要 + tech 分——
    # 30 个交易日积累后，tech-only 对照回放回答「六维组合是否优于单维筛选」
    kept_symbols = {k["symbol"] for k in kept}
    rejected = [
        {
            "symbol": r["symbol"],
            "name": r["name"],
            "score": r["score"],
            "tech": (r.get("sub_scores") or {}).get("tech"),
            "rank": i + 1,
        }
        for i, r in enumerate(ranked)
        if r["symbol"] not in kept_symbols
    ][:20]

    # ⑤b 出列留痕（2026-09-10）：昨日成员没进今日名单的，分两种归因——
    # ① 跌破入选门槛（质量下滑，有分数可证）② 未入选（掉出候选池/被更强候选换掉/名额截断）。
    # 不写这条，「名单变短」在复盘里就成了无解释的数字变化。
    score_of = {r["symbol"]: r.get("score") for r in ranked}
    swapped_out = {r["out"] for r in replaced}
    removed = []
    for sym in prev_symbols:
        if sym in kept_symbols or sym in swapped_out:
            continue
        sc = score_of.get(sym)
        if sc is not None and sc < limits["min_pick_score"]:
            reason = f"跌破入选门槛（综合分 {sc} < {limits['min_pick_score']}）"
        elif sc is None:
            reason = "掉出候选池（今日未进入深度评分）"
        else:
            reason = "未入选（被更强候选换掉或容量截断）"
        removed.append({"symbol": sym, "score": sc, "reason": reason})

    # ⑥ 卡片组装（含风险档位与出场纪律参考）+ 空仓闸门处理 + 持久化
    items = [_assemble_card(k) for k in kept]
    items = apply_gate_to_picks(items, gate)
    meta = {
        "weights": weights,
        "regime": regime,
        "style_routing": style,
        "gate": gate,
        "replace_threshold": limits["replace_threshold"],
        "max_swaps_per_day": limits["max_swaps_per_day"],
        "market_phase": market_phase,
        "candidate_count": len(candidates),
        "deep_dives": len(deep),
        # max_picks 是容量上限、min_pick_score 是入选门槛：两者共同决定
        # 「今日名单 = 达到门槛者，最多 5 只」，不是「每天凑满 5 只」（2026-09-10）
        "max_picks": MAX_PICKS,
        "min_pick_score": limits["min_pick_score"],
        "kept_count": len(items),
        "removed": removed,
        "market_pct": market_pct,
        "limit_up_count": len(lu_ctx["records"]),
        "market_max_boards": lu_ctx["market_max_boards"],
        "generated_at": beijing_now().isoformat(),
    }
    with _db() as db:
        from app.models.daily_pick import DailyPickSet

        row = db.execute(select(DailyPickSet).where(DailyPickSet.date == today)).scalar_one_or_none()
        rejected_json = json.dumps(rejected, ensure_ascii=False)
        if row is None:
            row = DailyPickSet(
                date=today,
                items=json.dumps(items, ensure_ascii=False),
                meta=json.dumps(meta, ensure_ascii=False),
                replaced=json.dumps(replaced, ensure_ascii=False),
                rejected=rejected_json,
            )
            db.add(row)
        else:
            row.items = json.dumps(items, ensure_ascii=False)
            row.meta = json.dumps(meta, ensure_ascii=False)
            row.replaced = json.dumps(replaced, ensure_ascii=False)
            row.rejected = rejected_json
        db.commit()
    return {"data": {"date": today, "items": items, "replaced": replaced, "meta": meta}, "meta": {}}


async def _live_style_routing(request: Request, hub: QuoteHub, stored: dict | None) -> dict:
    """相位→风格路由按**读取时刻**重算（60s 缓存）。

    2026-09-09 修：style_routing 原本只在组合生成时算一次并持久化进 meta，
    全天定格——重启补跑若赶上全市场快照未就绪（breadth=None → CalendarUnavailable），
    phase=None 会被静默落库，「相位缺失·未路由」挂一整天（09-09 实测如此）。
    风格路由语义是「当日微调」，本就该用实时相位；生成时刻快照保留在
    meta.market_phase 作对照，用 phase_source 标明来源（三态纪律：来源显式，
    未知不伪装成「均衡」）。

    缓存名独立（不复用 market.sentiment）：那边 payload 是 {data,meta} 信封结构，
    复用会因 build 产物结构不同互相污染；这里 60s 一次上游，开销可忽略。
    """
    from app.core.ttl_cache import cache_on
    from app.picks.style_router import route_style
    from app.services.market_context import CalendarUnavailable, compute_market_sentiment

    cache = cache_on(request.app.state, "picks.live_sentiment", 60, maxsize=1)

    async def _build() -> dict:
        return await compute_market_sentiment(hub, request.app.state.snapshot_service)

    try:
        _, sent = await cache.get_or_set((), _build)
    except CalendarUnavailable as exc:
        out = dict(stored or route_style(None))
        out["phase_source"] = "unavailable"
        out["phase_note"] = f"实时相位不可用（{exc}），显示生成时刻快照"
        return out
    except Exception as exc:  # noqa: BLE001  读时重算失败是降级不是故障——保留快照，不覆盖成未知
        log.warning("live style routing failed: %s", exc)
        out = dict(stored or route_style(None))
        out["phase_source"] = "unavailable"
        out["phase_note"] = f"实时相位计算失败（{exc}），显示生成时刻快照"
        return out

    live = route_style(sent.get("phase"))
    live["phase_source"] = "live" if live.get("routed") else "unknown_phase"
    return live


@router.get("/today")
async def today_picks(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    today = date.today().isoformat()
    with _db() as db:
        from app.models.daily_pick import DailyPickSet

        row = db.execute(select(DailyPickSet).where(DailyPickSet.date == today)).scalar_one_or_none()
        if row is None:
            row = db.execute(select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(1)).scalar_one_or_none()
            if row is None:
                return {"data": {"date": None, "items": [], "meta": None, "note": "尚未生成组合：POST /api/picks/generate（或等收盘管线）"}, "meta": {}}
            meta = _parse_meta(row.meta)  # 炒作阶段与空仓闸门状态（前端横幅需要）
            meta["style_routing"] = await _live_style_routing(request, hub, meta.get("style_routing"))
            return {
                "data": {
                    "date": row.date,
                    "items": json.loads(row.items),
                    "stale": row.date != today,
                    "meta": meta,
                },
                "meta": {},
            }
        meta = _parse_meta(row.meta)
        meta["style_routing"] = await _live_style_routing(request, hub, meta.get("style_routing"))
        return {
            "data": {
                "date": row.date,
                "items": json.loads(row.items),
                "replaced": json.loads(row.replaced or "[]"),
                "meta": meta,
            },
            "meta": {},
        }


@router.get("/history")
async def history(limit: int = Query(default=10, ge=1, le=60)) -> dict:
    with _db() as db:
        from app.models.daily_pick import DailyPickSet

        rows = db.execute(select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(limit)).scalars().all()
        out = []
        for r in rows:
            items = json.loads(r.items)
            out.append(
                {
                    "date": r.date,
                    "symbols": [i.get("symbol") for i in items],
                    "score_avg": round(sum(i.get("score", 0) for i in items) / max(len(items), 1), 1),
                }
            )
        return {"data": out, "meta": {}}


# ---------------------------------------------------------------- 选股复盘


@router.post("/review/generate")
async def generate_review(request: Request, hub: QuoteHub = Depends(get_hub), _: None = Depends(require_write_token)) -> dict:
    """对最近一份组合生成/刷新复盘（表现日 = 今天；组合 T-1 生成、T 日持有）。

    核心逻辑在 app.picks.daily_review.generate_daily_review（与 15:30 全局
    复盘前置步共用同一条代码路径，2026-09-04 抽出——见该模块 docstring）。
    """
    from app.picks.daily_review import generate_daily_review

    try:
        result = await generate_daily_review(hub, request.app.state.snapshot_service, get_session_factory())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"data": result, "meta": {}}


@router.get("/review")
async def list_reviews(date_str: str | None = Query(default=None, alias="date"), limit: int = Query(default=60, ge=1, le=200)) -> dict:
    with _db() as db:
        from app.models.daily_pick import DailyPickReview

        q = select(DailyPickReview).order_by(DailyPickReview.date.desc(), DailyPickReview.symbol)
        if date_str:
            q = select(DailyPickReview).where(DailyPickReview.date == date_str).order_by(DailyPickReview.symbol)
        rows = db.execute(q.limit(limit)).scalars().all()
        return {
            "data": [
                {"date": r.date, "symbol": r.symbol, "name": r.name, "verdict": r.verdict,
                 "reason_category": r.reason_category, "excess_pct": r.excess_pct, "note": r.note}
                for r in rows
            ],
            "meta": {},
        }


@router.get("/meta")
async def picks_meta() -> dict:
    """元结论：走坏原因分布 + 按梯队角色的胜率分布。

    角色胜率是回答「能不能按题材抓妖」的直接证据：龙头/补涨/滞涨各自的
    实际胜率与平均超额，比任何主观判断都硬。样本不足时如实标注。
    """
    with _db() as db:
        from sqlalchemy import func

        from app.models.daily_pick import DailyPickReview, DailyPickSet

        rows = db.execute(
            select(DailyPickReview.reason_category, func.count(DailyPickReview.id)).group_by(DailyPickReview.reason_category)
        ).all()
        reviews = db.execute(
            select(DailyPickReview).order_by(DailyPickReview.date.desc()).limit(300)
        ).scalars().all()
        sets = db.execute(
            select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(90)
        ).scalars().all()

    # (date, symbol) → 梯队角色（角色存在组合 items JSON 里）
    role_of: dict[tuple[str, str], str] = {}
    for s in sets:
        try:
            for item in json.loads(s.items):
                if item.get("echelon_role"):
                    role_of[(s.date, item["symbol"])] = item["echelon_role"]
        except Exception:
            continue

    agg: dict[str, dict] = {}
    for r in reviews:
        role = role_of.get((r.date, r.symbol))
        if role is None:
            continue  # 该条复盘早于梯队维度上线（8-31 前），角色未知不硬凑
        a = agg.setdefault(role, {"count": 0, "good": 0, "bad": 0, "flat": 0, "excess_sum": 0.0})
        a["count"] += 1
        a[r.verdict if r.verdict in ("good", "bad") else "flat"] += 1
        a["excess_sum"] += r.excess_pct or 0.0
    role_performance = []
    for role, a in sorted(agg.items(), key=lambda kv: -kv[1]["count"]):
        role_performance.append(
            {
                "role": role,
                "count": a["count"],
                "good": a["good"],
                "bad": a["bad"],
                "flat": a["flat"],
                "win_rate": round(a["good"] / a["count"] * 100, 1),
                "avg_excess": round(a["excess_sum"] / a["count"], 2),
            }
        )
    return {
        "data": {
            "reason_distribution": {r[0]: r[1] for r in rows},
            "role_performance": role_performance,
            "note": "分布与角色胜率供周末权重微调建议参考；权重变更需人工确认",
        },
        "meta": {},
    }


# ---------------------------------------------------------------- 执行闸门与影子持仓（picks-intraday-fusion-assessment P0-A/P0-B，2026-09-04）


@router.get("/execution-gate")
async def execution_gate(request: Request, date: str | None = Query(default=None)) -> dict:
    """最新（或指定）组合成员的 9:25 竞价执行闸门三态判定。

    gap ≥ 9.5% 禁买（一字/超高开，历史胜率 12%）/ 5~9.5% 观察 / ≤-5% 异常复核 /
    其余可执行；竞价数据缺失 = unknown，绝不冒充可买。60s 缓存（竞价口径 9:25 后不再变）。
    """
    from app.core.config import settings
    from app.core.ttl_cache import cache_on
    from app.picks.execution_gate import collect_execution_gate

    hub = get_hub(request)
    cache = cache_on(request.app.state, "picks.execution_gate", 60, maxsize=2)
    _, payload = await cache.get_or_set(
        ("execution-gate", date),
        lambda: collect_execution_gate(
            hub, get_session_factory(),
            pick_date=date,
            block_ge=settings.picks_gate_block_gap,
            observe_ge=settings.picks_gate_observe_gap,
            anomaly_le=settings.picks_gate_anomaly_gap,
        ),
    )
    return {"data": payload, "meta": {}}


@router.get("/shadow")
async def shadow_state(request: Request) -> dict:
    """影子持仓账户状态（scope=shadow，独立于交易页签的 main 账户）。"""
    runner = getattr(request.app.state, "paper_shadow", None)
    if runner is None:
        return {"data": {"enabled": False, "note": "影子持仓未启用（ASHARE_PICKS_SHADOW_ENABLED）"}, "meta": {}}
    return {"data": {"enabled": True, **runner.state()}, "meta": {}}


@router.get("/signal-health")
async def picks_signal_health() -> dict:
    """信号健康度（方向1×5 反馈环）：每日精选命中记录的滚动胜率 + CUSUM 下漂。

    status: ok | warning | drift | insufficient（样本 <10 组合日，显式不判 ok）| error。
    预警已接线（通知中心 + 自动 action_items）。
    **本端点只覆盖「每日精选组合」一级**；跨策略键的评估见 `/strategy-health`。
    """
    from app.picks.signal_health import collect_signal_health

    payload = collect_signal_health(get_session_factory())
    return {"data": payload, "meta": {}}


@router.get("/strategy-health")
async def picks_strategy_health() -> dict:
    """**策略级**健康度（P1-37/P1-38）：逐策略键独立评估，不合并。

    与 `/signal-health` 的关系：后者是「每日精选组合」一级的视图（保持向后兼容），
    本端点是登记册全量策略键的视图——含 `intraday_watch`（盘中跟踪）等此前
    不在监控视野内的策略。

    status: ok | warning | drift | insufficient | thin | no_pipeline | error | unknown。
    ⚠️ `insufficient`/`thin`/`no_pipeline` 都是「判不出」，**不是 ok 也不是失效**。
    ⚠️ 每条带 `basis`：`market_neutral`（已扣同日市场均值的超额 → 测 alpha 衰减）
    与 `absolute`（绝对收益 → 只测策略自身是否变差），**两者不可互相解释**。
    """
    from app.picks.strategy_registry import collect_all_strategy_health

    payload = collect_all_strategy_health(get_session_factory())
    return {"data": payload, "meta": {}}


@router.get("/strategy-registry")
async def picks_strategy_registry() -> dict:
    """策略登记册全量条目（含已否决者，便于追溯"有哪些策略、各自什么状态"）。

    与 `docs/strategy-registry.md` §1 总表一致（由测试守卫）。
    """
    from app.picks.strategy_registry import list_strategy_keys

    return {"data": {"strategies": list_strategy_keys()}, "meta": {}}
