"""每日精选 API（CONTEXT.md: Daily Picks 域）。

- GET  /api/picks/today           当日组合（从库读；不自动生成——生成较重，POST 触发）
- POST /api/picks/generate        生成今日组合（写鉴权；收盘后复盘管线或手动触发）
- GET  /api/picks/history         历史组合（近 N 日）
- GET  /api/picks/review?date=    复盘日志
- POST /api/picks/review/generate 对最近组合生成/刷新复盘（写鉴权）
- GET  /api/picks/meta            走坏原因分布（周末权重微调建议的输入）

数据编排全部复用既有管线：候选池（活跃事件标的池 + 涨停池 + 热股榜）→ 五维评分
（情绪=情绪引擎 / 消息=EventCard 方向 / 技术=score_stock / 基本面=财务 /
资金=资金流+快照）→ 加权合成 + 一票否决 → 换股门槛 → 持久化。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.api.deps import get_hub, require_write_token
from app.core.db import get_session_factory
from app.events.store import EventStore
from app.market import trade_calendar as tc
from app.market.tech_score import score_stock, sma
from app.picks.echelon import classify_echelon_role, score_echelon
from app.picks.engine import (
    MAX_PICKS,
    REPLACE_THRESHOLD,
    apply_replacement_threshold,
    build_buy_range,
    classify_failure,
    review_entry_quality,
    score_capital,
    score_fundamental,
    score_news,
    score_sentiment,
    score_tech,
    synthesize,
)
from app.picks.gate import apply_gate_to_picks, evaluate_stand_aside
from app.picks.regime import detect_regime, earnings_event_ratio, weights_for
from app.picks.risk import build_invalidations, exit_discipline, risk_tier_of, stop_loss_reference
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
    """腾讯批量快照（与个股行情同源），50 只/批。返回 symbol→Quote。"""
    composite = hub.provider if hasattr(hub.provider, "providers") else None
    target = next(
        (p for p in (composite.providers if composite else [hub.provider]) if p.name == "tencent"),
        hub.provider,
    )
    out: dict[str, Any] = {}
    for i in range(0, len(symbols), 50):
        try:
            for q in await target.get_quotes(symbols[i : i + 50]):
                out[q.symbol] = q
        except Exception as exc:
            log.warning("picks quotes batch %s failed: %s", i // 50, exc)
    return out


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


def _parse_meta(raw: str | None) -> dict:
    """组合 meta（权重/炒作阶段/空仓闸门）解析。损坏时返回空字典而不是 500。"""
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _active_event_hits(store: EventStore, symbol: str) -> tuple[int, int, str | None, str | None]:
    """该标的的活跃事件方向命中：(利好数, 利空数, 主事件标题, 主方向文案)。"""
    bull = bear = 0
    top_title = top_dir = None
    try:
        for row in store.list_events(active_only=True, limit=30):
            for d in store.directions_of(row.id):
                hit = (d.target_type == "symbol" and d.target == symbol) or (
                    d.target_type == "theme"
                )  # theme 方向对该标的的传导第一版不逐股判定（见 basis），symbol 直击为主
                if d.target_type == "theme":
                    continue  # 题材方向的个股传导第一版不计入单股消息分（防过度外推）
                if not hit:
                    continue
                if d.direction == 1:
                    bull += d.strength
                elif d.direction == -1:
                    bear += d.strength
                if top_title is None and d.direction != 0:
                    top_title = row.title
                    top_dir = "利好" if d.direction == 1 else "利空"
    except Exception as exc:
        log.warning("picks event hits %s failed: %s", symbol, exc)
    return bull, bear, top_title, top_dir


@router.post("/generate")
async def generate_picks(request: Request, hub: QuoteHub = Depends(get_hub), _: None = Depends(require_write_token)) -> dict:
    """生成今日组合（T 日收盘后跑，产出 T+1 组合；重复生成覆盖当日行）。"""
    store = _store(request)
    svc = getattr(request.app.state, "theme_catalog", None)
    today = date.today().isoformat()

    # ① 候选池
    candidates = await _candidate_pool(hub, store, svc, request)

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
    deep.sort(key=lambda c: -c["_prio"])
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
    gate = evaluate_stand_aside(
        phase=market_phase,
        promotion_1to2=(sent.get("promotion") or {}).get("promo_1to2"),
        break_rate=break_rate,
        limit_down=limit_down,
        prev_zt_median_pct=(sent.get("prev_perf") or {}).get("median_pct"),
        phase_unreliable=bool(sent.get("phase_unreliable")),
    )
    if gate["stand_aside"]:
        log.warning("picks gate triggered (%s): %s", gate["level"], "；".join(gate["reasons"]))

    # ④ 逐只深度评分（并发；每只独立异常兜底）
    sem = asyncio.Semaphore(CONCURRENCY)

    async def _score_one(c: dict) -> dict | None:
        sym = c["symbol"]
        async with sem:
            sub: dict[str, float] = {}
            bases: dict[str, str] = {}
            # 技术（防飞刀口径 score_stock）
            try:
                bars = await hub.provider.get_kline(sym, "1d", None, None)
                dicts = [b.model_dump() if hasattr(b, "model_dump") else dict(b) for b in bars][-250:]
                s_tech, b_tech = score_tech(score_stock(dicts))
            except Exception:
                dicts = []
                s_tech, b_tech = 50.0, "K线数据缺失，中性"
            # 出场纪律的输入：ATR（止损宽度）与均线（失效条件参照）
            atr_pct = _atr_pct(dicts)
            ma5 = _ma_value(dicts, 5)
            ma10 = _ma_value(dicts, 10)
            sub["tech"], bases["tech"] = s_tech, b_tech
            # 消息
            bull, bear, top_title, top_dir = _active_event_hits(store, sym)
            sub["news"], bases["news"] = score_news(bull, bear, top_title, top_dir)
            # 基本面
            pe = rev = None
            try:
                fin = await hub.provider.get_financials(sym, 4)
                if fin:
                    latest = fin[0] if isinstance(fin, list) else fin
                    d_ = latest if isinstance(latest, dict) else getattr(latest, "__dict__", {})
                    pe = d_.get("pe_ttm")
                    rev = d_.get("revenue_yoy")
            except Exception:
                pass
            sub["fundamental"], bases["fundamental"] = score_fundamental(pe, rev)
            # 资金
            net_inflow = None
            try:
                flow = await hub.provider.get_capital_flow(sym, 5)
                if flow:
                    last = flow[-1] if isinstance(flow, list) else flow
                    d_ = last if isinstance(last, dict) else getattr(last, "__dict__", {})
                    net_inflow = d_.get("net_amount")
            except Exception:
                pass
            sub["capital"], bases["capital"] = score_capital(net_inflow, None, on_lhb=False)
            # 情绪（全局相位；题材涨家占比第一版缺省）
            sub["sentiment"], bases["sentiment"] = score_sentiment(market_phase, None)

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

            score, vetoes = synthesize(sub, weights=weights)
            return {
                "symbol": sym, "name": c["name"], "price": c["price"], "change_pct": c["change_pct"],
                "score": score, "sub_scores": sub, "bases": bases, "vetoes": vetoes,
                "related_events": [top_title] if top_title else [],
                "echelon_role": role,
                "echelon_basis": bases["echelon"],
                "theme": theme_name,
                "theme_stage": theme_ctx["stage"],
                "atr_pct": atr_pct,
                "ma5": ma5,
                "ma10": ma10,
            }

    results = await asyncio.gather(*[_score_one(c) for c in deep])
    ranked = [r for r in results if r is not None]
    ranked.sort(key=lambda r: -r["score"])

    # ⑤ 换股门槛（昨日组合）
    prev_symbols: list[str] = []
    with _db() as db:
        from app.models.daily_pick import DailyPickSet

        rows = db.execute(select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(1)).scalars().all()
        if rows:
            try:
                prev_symbols = [i["symbol"] for i in json.loads(rows[0].items)]
            except Exception:
                prev_symbols = []
    kept, replaced = apply_replacement_threshold(prev_symbols, ranked)

    # ⑥ 卡片组装（含风险档位与出场纪律参考）+ 空仓闸门处理 + 持久化
    items = []
    for k in kept:
        role = k.get("echelon_role") or ""
        tier = risk_tier_of(role)
        ma5, ma10 = k.get("ma5"), k.get("ma10")
        # 买入范围的技术位收敛：均线在现价下方作支撑、上方作压力
        support = min([v for v in (ma5, ma10) if v and v < k["price"]], default=None)
        resistance = max([v for v in (ma5, ma10) if v and v > k["price"]], default=None)
        items.append(
            {
                "symbol": k["symbol"],
                "name": k["name"],
                "price": k["price"],
                "change_pct": k["change_pct"],
                "score": k["score"],
                "sub_scores": k["sub_scores"],
                "bases": k["bases"],
                "vetoes": k["vetoes"],
                "buy_range": build_buy_range(k["price"], support, resistance),
                "echelon_role": role,
                "echelon_basis": k.get("echelon_basis", ""),
                "theme": k.get("theme"),
                "theme_stage": k.get("theme_stage"),
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
            }
        )
    items = apply_gate_to_picks(items, gate)
    meta = {
        "weights": weights,
        "regime": regime,
        "gate": gate,
        "replace_threshold": REPLACE_THRESHOLD,
        "market_phase": market_phase,
        "candidate_count": len(candidates),
        "deep_dives": len(deep),
        "max_picks": MAX_PICKS,
        "market_pct": market_pct,
        "limit_up_count": len(lu_ctx["records"]),
        "market_max_boards": lu_ctx["market_max_boards"],
        "generated_at": datetime.now().isoformat(),
    }
    with _db() as db:
        from app.models.daily_pick import DailyPickSet

        row = db.execute(select(DailyPickSet).where(DailyPickSet.date == today)).scalar_one_or_none()
        if row is None:
            row = DailyPickSet(
                date=today,
                items=json.dumps(items, ensure_ascii=False),
                meta=json.dumps(meta, ensure_ascii=False),
                replaced=json.dumps(replaced, ensure_ascii=False),
            )
            db.add(row)
        else:
            row.items = json.dumps(items, ensure_ascii=False)
            row.meta = json.dumps(meta, ensure_ascii=False)
            row.replaced = json.dumps(replaced, ensure_ascii=False)
        db.commit()
    return {"data": {"date": today, "items": items, "replaced": replaced, "meta": meta}, "meta": {}}


@router.get("/today")
async def today_picks() -> dict:
    today = date.today().isoformat()
    with _db() as db:
        from app.models.daily_pick import DailyPickSet

        row = db.execute(select(DailyPickSet).where(DailyPickSet.date == today)).scalar_one_or_none()
        if row is None:
            row = db.execute(select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(1)).scalar_one_or_none()
            if row is None:
                return {"data": {"date": None, "items": [], "meta": None, "note": "尚未生成组合：POST /api/picks/generate（或等收盘管线）"}, "meta": {}}
            return {
                "data": {
                    "date": row.date,
                    "items": json.loads(row.items),
                    "stale": row.date != today,
                    "meta": _parse_meta(row.meta),  # 炒作阶段与空仓闸门状态（前端横幅需要）
                },
                "meta": {},
            }
        return {
            "data": {
                "date": row.date,
                "items": json.loads(row.items),
                "replaced": json.loads(row.replaced or "[]"),
                "meta": _parse_meta(row.meta),
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
    """对最近一份组合生成/刷新复盘（表现日 = 今天；组合 T-1 生成、T 日持有）。"""
    with _db() as db:
        from app.models.daily_pick import DailyPickSet

        row = db.execute(select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(1)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="尚无组合可复盘")
    try:
        items = json.loads(row.items)
    except Exception:
        items = []
    if not items:
        raise HTTPException(status_code=404, detail="组合为空")

    # 大盘基准：上证当日涨跌幅
    market_pct = None
    try:
        for q in hub.get_indices():
            if q.symbol == "000001":
                market_pct = q.change_pct
    except Exception:
        pass

    # 持有期市场相位（情绪误判判定需要）
    review_phase = None
    try:
        from app.services.market_context import compute_market_sentiment

        review_phase = (
            await compute_market_sentiment(hub, request.app.state.snapshot_service) or {}
        ).get("phase")
    except Exception as exc:
        log.warning("picks review: sentiment failed: %s", exc)

    reviews = []
    symbols = [i["symbol"] for i in items]
    quotes = await _batch_quotes(hub, symbols)
    for it in items:
        sym = it["symbol"]
        q = quotes.get(sym)
        change = q.change_pct if q is not None else None
        excess = round(change - market_pct, 2) if (change is not None and market_pct is not None) else (change or 0.0)
        # 买点质量：把「选错了」与「选对了但买点不对」分开，否则迭代方向会被污染
        entry = review_entry_quality(
            buy_range=it.get("buy_range"),
            day_open=q.open if q is not None else None,
            day_high=q.high if q is not None else None,
            day_low=q.low if q is not None else None,
            day_close=q.price if q is not None else None,
            observation_only=bool(it.get("observation_only")),
        )
        category, note = classify_failure(
            excess_pct=excess, entry=entry, market_phase=review_phase
        )
        # 买点质量并入 note：不额外加列（克制新增），但复盘必须能看到这段证据。
        # 闸门日也会带上——"本就不建议出手"本身就是需要留档的结论。
        note = f"{note}；{entry['basis']}"
        verdict = {"missed": "flat", "entry_bad": "bad", "sentiment_misread": "bad",
                   "logic_failed": "bad", "gone_well": "good"}.get(category, "flat")
        reviews.append(
            {
                "date": row.date, "symbol": sym, "name": it.get("name"),
                "verdict": verdict,
                "reason_category": category,
                "excess_pct": excess, "note": note,
                "entry": entry,
                "market_phase": review_phase,
            }
        )

    with _db() as db:
        from app.models.daily_pick import DailyPickReview

        # ⚠️ 不能用 db.merge：新对象主键为 None，merge 会走 INSERT 而非 UPDATE，
        # 重复复盘即撞 (date, symbol) 唯一约束。按业务键查询后更新才是正解。
        for r in reviews:
            row_r = db.execute(
                select(DailyPickReview).where(
                    DailyPickReview.date == r["date"],
                    DailyPickReview.symbol == r["symbol"],
                )
            ).scalar_one_or_none()
            if row_r is None:
                db.add(
                    DailyPickReview(
                        date=r["date"], symbol=r["symbol"], name=r.get("name"),
                        verdict=r["verdict"], reason_category=r["reason_category"],
                        excess_pct=r["excess_pct"], note=r["note"],
                    )
                )
            else:
                row_r.name = r.get("name")
                row_r.verdict = r["verdict"]
                row_r.reason_category = r["reason_category"]
                row_r.excess_pct = r["excess_pct"]
                row_r.note = r["note"]
        db.commit()
    return {"data": {"date": row.date, "reviews": reviews, "market_pct": market_pct}, "meta": {}}


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
    """元结论：走坏原因分布（周末权重微调建议的输入；权重变更需人工确认）。"""
    with _db() as db:
        from sqlalchemy import func

        from app.models.daily_pick import DailyPickReview

        rows = db.execute(
            select(DailyPickReview.reason_category, func.count(DailyPickReview.id)).group_by(DailyPickReview.reason_category)
        ).all()
    return {"data": {"reason_distribution": {r[0]: r[1] for r in rows}, "note": "分布供周末权重微调建议参考；权重变更需人工确认"}, "meta": {}}
