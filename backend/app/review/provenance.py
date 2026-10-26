"""Date-bound review facts. Receipt time never substitutes for a source date."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from math import isfinite
from typing import Any

from app.core.bjtime import BJ_TZ
from app.review.schemas import SectionProvenance


def source_time(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    return value.astimezone(BJ_TZ)


def finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def date_key(value: Any) -> str | None:
    raw = str(value or "")
    if len(raw) == 8 and raw.isdigit():
        raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def evidence(day: date, *, state: str = "unavailable", source: str | None = None,
             as_of: Any = None, received: Any = None, source_day: Any = None,
             reason: str = "") -> SectionProvenance:
    stamp, receipt = source_time(as_of), source_time(received)
    return SectionProvenance(
        state=state, source=source,
        source_as_of=stamp.isoformat() if stamp else None,
        received_at=receipt.isoformat() if receipt else None,
        source_trade_date=date_key(source_day), requested_trade_date=day.isoformat(),
        reason=reason,
    )


def _closed_report_observation(raw: dict, day: date, now: datetime, stamp: datetime) -> bool:
    """Recognize the Hub's confirmed close, without upgrading live quote quality."""
    reasons = raw.get("quality_reasons")
    if (not isinstance(reasons, list) or not reasons
            or not all(isinstance(reason, str) for reason in reasons)
            or "market_closed" not in reasons
            or not set(reasons) <= {"market_closed", "off_session"}):
        return False
    session = raw.get("display_session")
    if not isinstance(session, dict):
        session = vars(session) if hasattr(session, "__dict__") else {}
    observed, tail, checked = (source_time(session.get("as_of")),
                               source_time(session.get("expected_source_after")), source_time(now))
    close = datetime(day.year, day.month, day.day, 15, tzinfo=BJ_TZ)
    return bool(
        checked and observed and tail and session.get("phase") == "closed"
        and session.get("trade_day_result") is True
        and date_key(session.get("trade_date")) == day.isoformat()
        and date_key(session.get("expected_source_date")) == day.isoformat()
        and checked.date() == day and observed.date() == day and tail.date() == day
        and close <= checked and close <= stamp
        and tail <= close and tail <= stamp <= observed <= checked + timedelta(seconds=30)
    )


def quote_evidence(quote: Any, day: date, now: datetime, *,
                   allow_closed_observation: bool = False) -> SectionProvenance:
    raw = quote if isinstance(quote, dict) else vars(quote)
    stamp = source_time(raw.get("data_timestamp"))
    source = raw.get("source")
    quality = getattr(raw.get("quality"), "value", raw.get("quality"))
    state, reason = "verified", "源日期与复盘日期一致；数值仅代表原源时点，不补成收盘价"
    if not stamp or not source:
        state, reason = "unverified", "缺来源或完整源时间；接收时间不能证明行情日期"
    elif stamp.date() != day:
        state, reason = "mismatch", "报价源日期与复盘日期不同，未进入当日事实"
    elif stamp > now + timedelta(seconds=30):
        state, reason = "mismatch", "报价源时刻在采集时刻之后，未进入当日事实"
    elif quality not in {"high", "medium"}:
        if (allow_closed_observation and quality == "stale"
                and _closed_report_observation(raw, day, now, stamp)):
            state, reason = "partial", "日历已确认闭市；保留同日收盘后源观测及原stale质量，不当作实时行情或官方收盘价"
        else:
            state, reason = "unverified", "报价质量不可用于有效复盘事实"
    elif quality == "medium":
        state, reason = "partial", "源日期匹配，报价自身质量降级；保留原质量与时点"
    return evidence(day, state=state, source=str(source) if source else None,
                    as_of=stamp, received=raw.get("received_at"),
                    source_day=stamp.date().isoformat() if stamp else None, reason=reason)


def collect_sector_boards(day: date) -> tuple[dict, SectionProvenance]:
    """Only the exact owner's saved day; no network, fallback day or new store."""
    from app.market import board_flow

    saved = board_flow._read_store_memo(board_flow._DAILY_STORE, "daily") or {}
    row = (saved.get("days") or {}).get(day.isoformat())
    if not isinstance(row, dict):
        return {"industry": [], "concept": []}, evidence(
            day, source="board_flow.daily", reason="原板块owner无该日存档；未以当前榜或前日榜回填")
    result: dict[str, list] = {"industry": [], "concept": []}
    for kind in result:
        for item in (row.get("boards") or {}).get(kind, [])[:50]:
            if not isinstance(item, list) or len(item) < 5:
                continue
            result[kind].append({"board_code": str(item[0]), "name": str(item[1]),
                                 "main_net_yi": finite(item[2]), "change_pct": finite(item[4])})
    # Old owner files bind the saved day, but do not preserve each row's source clock.
    return result, evidence(day, state="unverified", source="board_flow.daily",
                            source_day=day.isoformat(),
                            reason="原owner该日Top50资金排序存档，非全板块涨跌榜；旧存档缺逐行源钟，未核为当日源事实")
