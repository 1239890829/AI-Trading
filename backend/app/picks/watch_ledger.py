"""盘中跟踪台账（猎场系统性升级批次 A）。

需求映射：
- 需求 7 持久化保留：盘中筛选出的个股**首见即登记**，当日唯一（ UniqueConstraint ），
  不会因后续筛选条件变化被移除；收盘后与每日精选合并展示（merged_into_picks）。
- 需求 8 入选依据可追溯：first_seen_at + reason（题材催化/资金异动/技术形态/
  龙头角色 JSON）。
- 需求 9 入场点位与收盘比对：entry_price（入选时价格）与 close_price 自动比对
  → pnl_pct。
- 需求 10 复盘与统计：settle_day 逐股 verdict（success/fail/flat）+ 当日/累计
  胜率统计。
- 需求 11 历史可查：get_day / get_history。

纪律：登记用 INSERT OR IGNORE（首见优先，不覆盖已有 reason）；清算幂等
（settled 行跳过）；字段缺失显式 None，不臆造。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.db import get_session_factory, utcnow
from app.models.watch_ledger import WatchLedger

log = logging.getLogger(__name__)


def record_sighting(
    *,
    trade_date: str,
    symbol: str,
    name: str = "",
    layer: str = "today_strongest",
    source_theme: str = "",
    reason: dict | None = None,
    is_leader: bool = False,
    boards: int = 0,
    entry_price: float | None = None,
    entry_time: str = "",
    session_factory=None,
) -> dict | None:
    """首见登记（幂等）：当日同股只记一条，已存在则返回 None。"""
    sf = session_factory or get_session_factory()
    with sf() as db:
        exist = db.execute(
            select(WatchLedger).where(
                WatchLedger.trade_date == trade_date, WatchLedger.symbol == symbol
            )
        ).scalars().first()
        if exist is not None:
            return None
        row = WatchLedger(
            trade_date=trade_date, symbol=symbol, name=name or "",
            layer=layer or "today_strongest", source_theme=source_theme or "",
            reason=json.dumps(reason or {}, ensure_ascii=False),
            is_leader=1 if is_leader else 0, boards=boards or 0,
            entry_price=entry_price, entry_time=entry_time or "",
            status="tracking",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _dump(row)


def settle_day(trade_date: str, close_by_symbol: dict[str, float], session_factory=None) -> dict:
    """收盘清算：逐 tracking 行比对 entry_price vs close_price → pnl + verdict。

    verdict 判定（收盘口径，简单可解释）：
    - close ≥ entry → success（入选后到收盘未亏）
    - close < entry 但亏损 ≤2% → flat（基本持平，区分轻微回撤与选错）
    - 亏损 >2% → fail
    幂等：status=settled 的行跳过。close 缺失的行保持 tracking（数据缺失显式，
    下轮清算补）。
    """
    sf = session_factory or get_session_factory()
    settled, skipped_no_close = 0, 0
    with sf() as db:
        rows = db.execute(
            select(WatchLedger).where(
                WatchLedger.trade_date == trade_date, WatchLedger.status == "tracking"
            )
        ).scalars().all()
        for row in rows:
            close = close_by_symbol.get(row.symbol)
            if close is None or not row.entry_price:
                skipped_no_close += 1
                continue
            pnl = round((close - row.entry_price) / row.entry_price * 100, 2)
            if pnl >= 0:
                verdict, vr = "success", "入选后至收盘未亏"
            elif pnl >= -2.0:
                verdict, vr = "flat", f"轻微回撤 {pnl:+.2f}%（≤2% 记持平）"
            else:
                verdict, vr = "fail", f"收盘亏损 {pnl:+.2f}%（>2%）"
            row.close_price = close
            row.pnl_pct = pnl
            row.verdict = verdict
            row.verdict_reason = vr
            row.status = "settled"
            row.updated_at = utcnow()
            settled += 1
        db.commit()
    log.info("watch ledger settled %s: %d settled, %d no-close", trade_date, settled, skipped_no_close)
    return {"trade_date": trade_date, "settled": settled, "skipped_no_close": skipped_no_close}


def day_stats(trade_date: str, session_factory=None) -> dict:
    """当日统计：总数/已清算/成功/失败/持平/胜率。"""
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(WatchLedger).where(WatchLedger.trade_date == trade_date)
        ).scalars().all()
    settled = [r for r in rows if r.verdict]
    success = sum(1 for r in settled if r.verdict == "success")
    fail = sum(1 for r in settled if r.verdict == "fail")
    flat = sum(1 for r in settled if r.verdict == "flat")
    judged = success + fail
    return {
        "trade_date": trade_date,
        "total": len(rows),
        "settled": len(settled),
        "tracking": len(rows) - len(settled),
        "success": success, "fail": fail, "flat": flat,
        "win_rate": round(success / judged, 4) if judged else None,
        "avg_pnl_pct": round(sum(r.pnl_pct or 0 for r in settled) / len(settled), 2) if settled else None,
    }


def history_stats(days: int = 30, session_factory=None) -> list[dict]:
    """近 N 个自然日逐日统计（需求 11：收盘后仍可查）。"""
    sf = session_factory or get_session_factory()
    cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
    with sf() as db:
        dates = db.execute(
            select(WatchLedger.trade_date).where(WatchLedger.trade_date >= cutoff)
            .group_by(WatchLedger.trade_date).order_by(WatchLedger.trade_date.desc())
        ).scalars().all()
    return [day_stats(d, sf) for d in dates]


def get_day(trade_date: str, session_factory=None) -> list[dict]:
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(WatchLedger).where(WatchLedger.trade_date == trade_date)
            .order_by(WatchLedger.is_leader.desc(), WatchLedger.boards.desc(), WatchLedger.id.asc())
        ).scalars().all()
        return [_dump(r) for r in rows]


def get_history(limit_days: int = 10, session_factory=None) -> list[dict]:
    """历史台账（近 N 个有记录的交易日，含统计）。"""
    sf = session_factory or get_session_factory()
    with sf() as db:
        dates = db.execute(
            select(WatchLedger.trade_date).group_by(WatchLedger.trade_date)
            .order_by(WatchLedger.trade_date.desc()).limit(limit_days)
        ).scalars().all()
    return [{"trade_date": d, "stats": day_stats(d, sf), "rows": get_day(d, sf)} for d in dates]


def _dump(row: WatchLedger) -> dict:
    def _j(raw: str | None) -> Any:
        try:
            return json.loads(raw) if raw else {}
        except Exception:  # noqa: BLE001
            return {}

    return {
        "id": row.id, "trade_date": row.trade_date, "symbol": row.symbol,
        "name": row.name, "layer": row.layer, "source_theme": row.source_theme,
        "reason": _j(row.reason), "is_leader": bool(row.is_leader),
        "boards": row.boards, "entry_price": row.entry_price, "entry_time": row.entry_time,
        "status": row.status, "close_price": row.close_price, "pnl_pct": row.pnl_pct,
        "verdict": row.verdict, "verdict_reason": row.verdict_reason,
        "merged_into_picks": bool(row.merged_into_picks),
    }
