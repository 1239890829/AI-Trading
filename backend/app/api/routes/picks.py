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
from app.market.tech_score import score_stock
from app.picks.engine import (
    MAX_PICKS,
    REPLACE_THRESHOLD,
    WEIGHTS,
    apply_replacement_threshold,
    build_buy_range,
    classify_review,
    score_capital,
    score_fundamental,
    score_news,
    score_sentiment,
    score_tech,
    synthesize,
)
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

    # ③ 全局情绪相位（一次）
    market_phase = None
    try:
        from app.services.market_context import compute_market_sentiment

        sent = await compute_market_sentiment(hub, request.app.state.snapshot_service)
        market_phase = sent.get("phase")
    except Exception as exc:
        log.warning("picks sentiment failed: %s", exc)

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
                s_tech, b_tech = 50.0, "K线数据缺失，中性"
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
            score, vetoes = synthesize(sub)
            bull_ev, bear_ev, top_t, top_d = _active_event_hits(store, sym)
            return {
                "symbol": sym, "name": c["name"], "price": c["price"], "change_pct": c["change_pct"],
                "score": score, "sub_scores": sub, "bases": bases, "vetoes": vetoes,
                "related_events": [top_t] if top_t else [],
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

    # ⑥ 卡片组装 + 持久化
    items = []
    for k in kept:
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
                "buy_range": build_buy_range(k["price"], None, None),
                "themes": [],
                "related_events": k["related_events"],
            }
        )
    meta = {
        "weights": WEIGHTS,
        "replace_threshold": REPLACE_THRESHOLD,
        "market_phase": market_phase,
        "candidate_count": len(candidates),
        "deep_dives": len(deep),
        "max_picks": MAX_PICKS,
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
                return {"data": {"date": None, "items": [], "note": "尚未生成组合：POST /api/picks/generate（或等收盘管线）"}, "meta": {}}
            return {"data": {"date": row.date, "items": json.loads(row.items), "stale": row.date != today}, "meta": {}}
        return {"data": {"date": row.date, "items": json.loads(row.items), "replaced": json.loads(row.replaced or "[]")}, "meta": {}}


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

    reviews = []
    symbols = [i["symbol"] for i in items]
    quotes = await _batch_quotes(hub, symbols)
    for it in items:
        sym = it["symbol"]
        q = quotes.get(sym)
        change = q.change_pct if q is not None else None
        excess = round(change - market_pct, 2) if (change is not None and market_pct is not None) else (change or 0.0)
        verdict, note = classify_review(excess)
        reviews.append(
            {
                "date": row.date, "symbol": sym, "name": it.get("name"),
                "verdict": verdict,
                "reason_category": {"good": "gone_well", "flat": "gone_well", "bad": "logic_failed"}[verdict],
                "excess_pct": excess, "note": note,
            }
        )

    with _db() as db:
        from app.models.daily_pick import DailyPickReview

        for r in reviews:
            db.merge(
                DailyPickReview(
                    date=r["date"], symbol=r["symbol"], name=r.get("name"),
                    verdict=r["verdict"], reason_category=r["reason_category"],
                    excess_pct=r["excess_pct"], note=r["note"],
                )
            )
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
