"""做 T 信号决策链：记录 → 执行关联 → 30 分钟窗口结算与错误归因。

三段式生命周期（docs/minute-chart-plan.md 模块 5）：
1. **触发即记录**：signals 接口产出越过阈值的信号时落库，(symbol, trigger_ts) 去重
   ——引擎的 as_of 前缀属性保证同一信号重算结果稳定，去重键可靠。
2. **执行自动关联**：结算时查 paper 成交（同 symbol、方向匹配、落在信号窗口内），
   第一笔成交即视为"按信号执行"——不要求前端显式传 signal_id，链路自动闭环。
3. **窗口结算**：触发 +30 分钟，用分时数据结算最优/最差价差与三分类 outcome
   （correct：偏向方向最优价差 ≥ 8bp=2×成本；wrong：反向不利价差 ≥ 8bp 且无有利价差；
   invalid：窗口内未达阈值——消耗了注意力但没有钱；expired：数据不足不判定）。
   错误归因用 leave-one-out：剔除哪个指标会翻转/跌出信号区，那个就是主因。

结算采用**惰性触发**（读决策列表时顺手结算到期记录）而非后台定时器——
单用户场景下等价且少一个常驻任务；与方案的差异已在此注明。

生产接线（2026-09-10 P1-24）：本模块此前**零生产引用**（只有单测 import）。
现由 `scan_and_settle_today()` 挂进既有 15:35 盘后复盘调度（`review_intraday.
intraday_review_scheduler` 的收盘分支，与题材热度/板块资金快照同一位置、
独立 try/except、当日幂等），扫描当日盘中跟踪台账标的 → 记录 → 结算。
`GET /api/market/minute-decisions` 读列表时也会顺手结算（惰性路径）。
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.models.paper import PaperOrder
from app.review.models import MinuteDecisionRow
from app.core.bjtime import BJ_OFFSET  # S2-8 时区收敛

log = logging.getLogger(__name__)

WINDOW_MINUTES = 30
THRESHOLD_PCT = 0.08  # 8bp ≈ 2×（佣金+印花税+滑点），方案 4.3 的"有效"门槛
TOTAL_W = 0.90        # 可计算指标权重和（turnover 恒降级，见引擎）

SCAN_DIR = Path(__file__).resolve().parents[2] / "data" / "minute_decisions"

_SIDE = {"低吸偏向": "buy", "高抛偏向": "sell"}


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _trade_date_of(ts: str) -> str:
    return (datetime.fromisoformat(ts) + BJ_OFFSET).strftime("%Y%m%d")


def record_signals(session_factory, symbol: str, signals: list[dict]) -> int:
    """触发即记录；(symbol, trigger_ts) 去重。返回新增条数。"""
    if not signals:
        return 0
    db = session_factory()
    added = 0
    try:
        existing = set(db.execute(
            select(MinuteDecisionRow.trigger_ts).where(MinuteDecisionRow.symbol == symbol)
        ).scalars())
        for s in signals:
            ts = s["ts"]
            if ts in existing:
                continue
            db.add(MinuteDecisionRow(
                decision_id=f"MD-{_trade_date_of(ts)}-{symbol}-{ts[11:16]}{ts[14:16]}",
                symbol=symbol, trade_date=_trade_date_of(ts), trigger_ts=ts,
                signal_price=s["signal_price"], bias=s["bias"], score=s["score"],
                confidence=s.get("confidence", "medium"),
                triggered=json.dumps(s.get("triggered", []), ensure_ascii=False),
                invalidate_condition=s.get("invalidate_condition", ""),
            ))
            existing.add(ts)
            added += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return added


def _window_points(points: list[dict], trigger: datetime) -> tuple[list[dict], str]:
    """信号窗口（trigger, trigger+30min] 与状态机。

    - ``pending``：窗口未走完（末点时间还在窗口内且未收盘）→ 未到期，不结算；
    - ``ready``：窗口完整落在数据范围内且 bar 数足够 → 正常结算；
      或收盘截断但已有 ≥10 根 bar → 用现有数据尽力结算；
    - ``expired``：窗口已过但 bar 数不足（盘中数据缺失）或收盘截断且几乎无数据
      → 信息不足不判定。
    """
    end = trigger + timedelta(minutes=WINDOW_MINUTES)
    win = [p for p in points if trigger < _parse(p["ts"]) <= end]
    last_ts = _parse(points[-1]["ts"]) if points else None
    if not points or last_ts is None:
        return win, "pending"
    last_bj = (last_ts + BJ_OFFSET).strftime("%H:%M")
    session_over = last_bj >= "15:00"
    if last_ts >= end:  # 末点恰好落在窗口终点也算窗口完整
        return win, ("ready" if len(win) >= 10 else "expired")
    if session_over:
        return win, ("ready" if len(win) >= 10 else "expired")
    return win, "pending"


def _attribute(triggered: list[dict], score: float) -> dict:
    """leave-one-out 错误归因：剔除哪个指标会翻转结论/跌出信号区。"""
    primary, worst_wo = None, None
    for h in triggered:
        score_wo = (score * TOTAL_W - h["weight"] * h["direction"]) / TOTAL_W
        pivotal = (score_wo * score < 0) or abs(score_wo) < 0.5
        if worst_wo is None or abs(score_wo) < abs(worst_wo):
            worst_wo, primary = score_wo, h
        if pivotal:
            return {
                "primary_cause": h["key"], "primary_name": h.get("name", h["key"]),
                "score_without": round(score_wo, 3), "pivotal": True,
                "counter_evidence": f"剔除「{h.get('name', h['key'])}」后 score={score_wo:.2f}，"
                                    f"信号{'翻转' if score_wo * score < 0 else '跌出信号区'}",
            }
    return {
        "primary_cause": primary["key"] if primary else None,
        "primary_name": primary.get("name", "") if primary else "",
        "score_without": round(worst_wo, 3) if worst_wo is not None else None,
        "pivotal": False,
        "counter_evidence": "无单一指标起决定作用——组合整体失效或市况逆风",
    }


def settle_decision(row: MinuteDecisionRow, points: list[dict], fills: list[PaperOrder]) -> bool:
    """结算单条决策。返回是否已终结（correct/wrong/invalid/expired）。"""
    if row.outcome is not None:
        return True
    trigger = _parse(row.trigger_ts)
    win, state = _window_points(points, trigger)
    if state == "pending":
        return False
    if state == "expired":
        row.outcome, row.settled_at = "expired", datetime.now(timezone.utc)
        return True

    prices = [p["price"] for p in win]
    best, worst = max(prices), min(prices)
    row.best_price, row.worst_price = best, worst
    if row.bias == "低吸偏向":
        row.optimal_spread_pct = round((best - row.signal_price) / row.signal_price * 100, 3)
        adverse = (worst - row.signal_price) / row.signal_price * 100
    else:
        row.optimal_spread_pct = round((row.signal_price - worst) / row.signal_price * 100, 3)
        adverse = (best - row.signal_price) / row.signal_price * 100

    # 执行链自动关联：窗口内同 symbol、方向匹配的首笔 paper 成交
    side = _SIDE.get(row.bias)
    for o in fills:
        if o.symbol != row.symbol or o.side != side or o.status != "filled":
            continue
        ots = o.created_at if o.created_at.tzinfo else o.created_at.replace(tzinfo=timezone.utc)
        if trigger < ots <= trigger + timedelta(minutes=WINDOW_MINUTES):
            row.executed = 1
            row.executed_price = o.filled_price or o.price
            row.realized_spread_pct = round(
                (row.executed_price - row.signal_price) / row.signal_price * 100, 3
            )
            break

    if row.optimal_spread_pct >= THRESHOLD_PCT:
        row.outcome = "correct"
    elif adverse <= -THRESHOLD_PCT:
        row.outcome = "wrong"
    else:
        row.outcome = "invalid"

    if row.outcome in ("wrong", "invalid"):
        triggered = json.loads(row.triggered or "[]")
        row.error_attribution = json.dumps(
            _attribute(triggered, row.score), ensure_ascii=False
        )
    else:
        row.error_attribution = json.dumps(
            {"primary_cause": None, "counter_evidence": "信号方向正确，无需归因"},
            ensure_ascii=False,
        )
    row.settled_at = datetime.now(timezone.utc)
    return True


def settle_due(session_factory, fetch_points, symbol: str | None = None) -> int:
    """惰性结算：所有"触发已超 30 分钟"的 open 记录。返回结算条数。"""
    now = datetime.now(timezone.utc)
    db = session_factory()
    try:
        q = select(MinuteDecisionRow).where(MinuteDecisionRow.outcome.is_(None))
        if symbol:
            q = q.where(MinuteDecisionRow.symbol == symbol)
        rows = db.execute(q).scalars().all()
    finally:
        db.close()
    settled = 0
    cache: dict[str, list[dict]] = {}
    for row in rows:
        due = now >= _parse(row.trigger_ts) + timedelta(minutes=WINDOW_MINUTES)
        if not due:
            continue
        if row.symbol not in cache:
            cache[row.symbol] = fetch_points(row.symbol) or []
        db = session_factory()
        try:
            fills = db.execute(select(PaperOrder)).scalars().all()
            fresh = db.get(MinuteDecisionRow, row.id)
            if fresh is None or fresh.outcome is not None:
                continue
            if settle_decision(fresh, cache[row.symbol], fills):
                settled += 1
            db.commit()
        except Exception:
            db.rollback()
            log.exception("settle decision %s failed", row.decision_id)
        finally:
            db.close()
    return settled


def list_decisions(session_factory, symbol: str | None = None, limit: int = 50) -> list[dict]:
    db = session_factory()
    try:
        q = select(MinuteDecisionRow).order_by(MinuteDecisionRow.id.desc()).limit(limit)
        if symbol:
            q = select(MinuteDecisionRow).where(MinuteDecisionRow.symbol == symbol) \
                .order_by(MinuteDecisionRow.id.desc()).limit(limit)
        rows = db.execute(q).scalars().all()
        return [{
            "decision_id": r.decision_id, "symbol": r.symbol, "trade_date": r.trade_date,
            "trigger_ts": r.trigger_ts, "signal_price": r.signal_price,
            "bias": r.bias, "score": r.score, "confidence": r.confidence,
            "triggered": json.loads(r.triggered or "[]"),
            "invalidate_condition": r.invalidate_condition,
            "executed": bool(r.executed), "executed_price": r.executed_price,
            "realized_spread_pct": r.realized_spread_pct,
            "best_price": r.best_price, "worst_price": r.worst_price,
            "optimal_spread_pct": r.optimal_spread_pct,
            "outcome": r.outcome,
            "error_attribution": json.loads(r.error_attribution) if r.error_attribution else None,
        } for r in rows]
    finally:
        db.close()


# ---------------------------------------------------------------- 生产接线（P1-24）


def record_from_points(
    session_factory,
    symbol: str,
    points: list[dict],
    *,
    yesterday_vol: float | None = None,
    daily_vol_pct: float | None = None,
) -> dict:
    """分时序列 → 信号 → 落库（引擎前缀稳定性保证重算幂等，去重键 (symbol, trigger_ts)）。

    返回 ``{"computed", "recorded", "degraded"}``；``degraded`` 原样透传引擎的降级原因
    （缺昨日量/缺波动率等），供调用方如实标注而不是假装满配。
    """
    from app.market.minute_signals import compute_minute_signals

    out = compute_minute_signals(
        points, yesterday_vol=yesterday_vol, daily_vol_pct=daily_vol_pct
    )
    # 引擎返回的已是 dict（内部统一 model_dump 过），此处不再二次转换——
    # 曾经的 .model_dump() 假设会让整条扫描链 AttributeError（2026-09-10 实测）。
    signals = list(out["signals"])
    return {
        "computed": len(signals),
        "recorded": record_signals(session_factory, symbol, signals),
        "degraded": list(out.get("degraded") or []),
    }


def tdx_points(symbol: str) -> list[dict]:
    """结算/扫描用的分时来源：TDX 直连 m1（同步阻塞，调用方负责丢线程池）。

    失败返回 []（**不抛**）——结算侧会按「数据不足」判定，下一轮补；
    绝不拿别家口径或空数据硬凑（三态纪律）。
    """
    try:
        from app.market.minute_backfill import tdx_minute_line_fallback

        return tdx_minute_line_fallback(symbol) or []
    except Exception as exc:
        log.warning("minute points unavailable for %s: %s", symbol, exc)
        return []


def _tracked_symbols(session_factory, trade_date: str) -> list[str]:
    """当日盘中跟踪台账标的（唯一来源）。

    台账为空 → 返回 []，调用方跳过本轮（**不回落自选/全市场**：那会把「没跟踪」
    变成「凭空产生信号」，样本口径就脏了）。
    """
    with contextlib.suppress(Exception):
        from app.picks.watch_ledger import get_day

        out: list[str] = []
        for r in get_day(trade_date, session_factory) or []:
            sym = r.get("symbol")
            if sym and sym not in out:
                out.append(sym)
        return out
    return []


def _scan_marker(trade_date: str) -> Path:
    return SCAN_DIR / f"scan-{trade_date}.json"


def scan_and_settle_today(app_state, *, session_factory=None) -> dict:
    """盘后一次性闭环：扫描今日跟踪标的 → 记录信号 → 惰性结算。

    **当日幂等**（落 `data/minute_decisions/scan-YYYYMMDD.json`，失败不写标记 →
    下一 tick 重试）；台账为空则本轮直接跳过且**不写标记**（当天晚些补台账仍会被扫到）。

    为什么放在盘后而不是盘中实时：引擎是**前缀稳定**的——用全天分时重放，产出的
    信号集合与盘中逐拍实时产出的完全一致（这正是 record_signals 敢于按
    ``(symbol, trigger_ts)`` 去重的前提）。盘后一次性扫 = 同样的样本、零常驻任务、
    零盘中额外行情配额。
    """
    from app.core.db import get_session_factory
    from app.core.bjtime import beijing_today

    sf = session_factory or get_session_factory()
    tdate = beijing_today().isoformat()
    marker = _scan_marker(tdate)
    if marker.exists():
        return {"skipped": "already_scanned", "trade_date": tdate}

    symbols = _tracked_symbols(sf, tdate)
    if not symbols:
        return {"skipped": "no_tracked_symbols", "trade_date": tdate}

    scanned = recorded = 0
    degraded: list[str] = []
    for sym in symbols:
        points = tdx_points(sym)
        if not points:
            continue
        out = record_from_points(sf, sym, points)
        scanned += 1
        recorded += out["recorded"]
        degraded += out["degraded"]
    settled = settle_due(sf, tdx_points)

    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {"trade_date": tdate, "symbols": symbols, "scanned": scanned,
             "recorded": recorded, "settled": settled,
             "degraded": sorted(set(degraded))},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tmp.replace(marker)
    log.info(
        "minute decisions scan %s: %d 标的 / 新增 %d 条 / 结算 %d 条", tdate, scanned, recorded, settled
    )
    return {"trade_date": tdate, "symbols": len(symbols), "scanned": scanned,
            "recorded": recorded, "settled": settled, "degraded": sorted(set(degraded))}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "WINDOW_MINUTES",
    "THRESHOLD_PCT",
    "record_signals",
    "settle_decision",
    "settle_due",
    "list_decisions",
    "record_from_points",
    "tdx_points",
    "scan_and_settle_today",
    "SCAN_DIR",
]
