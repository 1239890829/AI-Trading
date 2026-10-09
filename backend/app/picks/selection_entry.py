"""Read original selection entry facts without selecting, notifying, or trading."""
from __future__ import annotations

import json
import math
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.bjtime import BJ_TZ, to_beijing
from app.core.db import get_session_factory
from app.core.freshness import DEFAULT_FRESH_WITHIN_SECONDS, MAX_FUTURE_SKEW_SECONDS
from app.models.opportunity_learning import OpportunityDecisionSnapshot as Snapshot

CONTRACT = "selection-entry-v1"
log = logging.getLogger(__name__)


def _object(value) -> dict:
    try:
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if not positive or value > 0 else None


def _aware(value):
    try:
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return to_beijing(value).isoformat() if value.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def missing_entry(source: str, reason="没有已记录的首次入选证据，未用现价或读取时间补填") -> dict:
    return {"state": "missing", "selected_at": None, "reference_price": None,
            "reference_change_pct": None, "quote_as_of": None, "source": source,
            "source_version": None, "semantics": "observation_only_not_fill", "reason": reason}


def _daily_quote_reason(audit: dict, quote_as_of: str | None, trade_date: str, recorded_at: datetime) -> str | None:
    """Judge the original quote at entry time, never at a later page read.

    Reuse the quote contract's quality set, 60s freshness and allowed future skew.
    A post-close record can consume that day's close, using 15:00 as its reference.
    """
    if not quote_as_of:
        return "原报价源时间缺失或没有明确时区"
    if not audit.get("source"):
        return "原报价来源缺失"
    if audit.get("quality") not in {"high", "medium"}:
        return "原报价质量缺失或不可信"
    quote = datetime.fromisoformat(quote_as_of)
    if quote.date().isoformat() != trade_date:
        return "原报价日期与首次入选日不同"
    reference = recorded_at.replace(hour=15, minute=0, second=0, microsecond=0) if recorded_at.hour >= 15 else recorded_at
    age = (reference - quote).total_seconds()
    if age < -MAX_FUTURE_SKEW_SECONDS:
        return "原报价超出允许的未来时钟偏差"
    if age > DEFAULT_FRESH_WITHIN_SECONDS:
        return "原报价在首次记录时已超出新鲜窗口"
    return None


def daily_entries(items, meta: dict, *, previous_meta, previous_items, trade_date: str,
                  recorded_at: datetime) -> dict:
    """Preserve observed joins, with partial evidence for untrusted original quotes.

    Quote problems never erase the actual entry clock or rewrite the reported price.
    This projection does not select stocks, publish notifications or allow execution.
    """
    previous = _object(previous_meta).get("selection_entries")
    entries = dict(previous) if isinstance(previous, dict) else {}
    legacy_symbols = {str(item.get("symbol") or "") for item in previous_items or [] if isinstance(item, dict)}
    generated = _aware(meta.get("generated_at"))
    clock = to_beijing(recorded_at)
    for item in items or []:
        if not isinstance(item, dict) or not item.get("symbol"):
            continue
        symbol = str(item["symbol"])
        if symbol in entries:
            continue
        generated_clock = datetime.fromisoformat(generated) if generated else None
        if (symbol in legacy_symbols or clock.date().isoformat() != trade_date or not generated_clock
                or generated_clock.date().isoformat() != trade_date
                or generated_clock > clock + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS)):
            entries[symbol] = missing_entry("daily_generation", "原首次加入未记录；后续重生成不倒填历史")
            continue
        audit = _object(item.get("quote_audit"))
        quote_as_of = _aware(audit.get("data_timestamp"))
        price = _number(item.get("price"), positive=True)
        pct = _number(item.get("change_pct"))
        reason = ("原参考价缺失或无效" if price is None else
                  _daily_quote_reason(audit, quote_as_of, trade_date, clock) or
                  ("原版本缺失" if not meta.get("selection_version") else None))
        ready = reason is None
        entries[symbol] = {"state": "recorded" if ready else "partial", "selected_at": clock.isoformat(),
            "reference_price": price, "reference_change_pct": pct, "quote_as_of": quote_as_of,
            "source": "daily_generation", "source_version": meta.get("selection_version"),
            "semantics": "observation_only_not_fill", "reason": f"首次加入已记录；{reason}" if reason else None}
    return entries


def attach_daily_entries(items: list[dict], meta: dict) -> list[dict]:
    entries = _object(meta).get("selection_entries")
    entries = entries if isinstance(entries, dict) else {}
    return [{**item, "selection_entry": _object(entries.get(str(item.get("symbol") or ""))) or missing_entry("daily_generation")}
            for item in items if isinstance(item, dict)]


def intraday_entries(trade_date: str, symbols, *, session_factory=None) -> dict:
    """First committed rank in its actual display capacity, not the earliest rejected source clock."""
    symbols = sorted({str(symbol) for symbol in symbols if symbol})
    if not symbols:
        return {}
    try:
        start = datetime.fromisoformat(trade_date).replace(tzinfo=BJ_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return {}
    sf = session_factory or get_session_factory()
    def field(path):
        return case((func.json_valid(Snapshot.evidence) == 1, func.json_extract(Snapshot.evidence, path)), else_=None)
    ranked = select(Snapshot.id, func.row_number().over(partition_by=Snapshot.symbol,
        order_by=(Snapshot.created_at.asc(), Snapshot.id.asc())).label("position")).where(
        Snapshot.trade_date == trade_date, Snapshot.symbol.in_(symbols), Snapshot.scenario == "intraday_opportunity",
        Snapshot.stage == "rank", Snapshot.decision == "ranked",
        Snapshot.created_at >= start, Snapshot.created_at < start + timedelta(days=1),
        field("$.selection_entry_contract") == CONTRACT,
        field("$.within_display_capacity") == 1, Snapshot.rank >= 1,
        Snapshot.rank <= field("$.effective_limit")).subquery()
    with sf() as db:
        rows = db.scalars(select(Snapshot).join(ranked, ranked.c.id == Snapshot.id).where(ranked.c.position == 1)).all()
    entries = {}
    for row in rows:
        evidence = _object(row.evidence)
        # Database created_at is UTC; as_of is the existing Beijing-naive snapshot contract.
        recorded = row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at
        quote = row.as_of.replace(tzinfo=BJ_TZ) if row.as_of.tzinfo is None else row.as_of
        price = _number(row.entry_price, positive=True)
        complete = price is not None and row.data_state == "ready"
        entries[row.symbol] = {"state": "recorded" if complete else "partial",
            "selected_at": to_beijing(recorded).isoformat(), "reference_price": price,
            "reference_change_pct": _number(evidence.get("change_pct")), "quote_as_of": to_beijing(quote).isoformat(),
            "source": "intraday_rank_archive", "source_version": row.run_id,
            "semantics": "observation_only_not_fill", "reason": None if complete else "首次加入已记录，原参考价缺项或归档数据降级"}
    return entries


def attach_intraday_entries(data: dict, *, session_factory=None) -> None:
    stocks = [stock for theme in data.get("themes") or [] for key in ("stocks", "participants")
              for stock in theme.get(key) or []]
    reason = None
    try:
        entries = intraday_entries(str(data.get("trade_date") or ""), [stock.get("symbol") for stock in stocks],
                                   session_factory=session_factory)
    except SQLAlchemyError:
        log.exception("intraday first-entry projection unavailable; current candidates remain readable")
        entries, reason = {}, "首次入选证据读取失败；未用当前报价补填"
    fallback = missing_entry("intraday_rank_archive", reason) if reason else missing_entry("intraday_rank_archive")
    for stock in stocks:
        stock["selection_entry"] = entries.get(str(stock.get("symbol") or "")) or dict(fallback)
