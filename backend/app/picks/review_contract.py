"""One date/version contract for daily observation and its statistics consumers."""
from __future__ import annotations

from datetime import date, datetime
import json
import math

from app.core.bjtime import BJ_TZ, beijing_today
from app.core.freshness import DEFAULT_FRESH_WITHIN_SECONDS

REVIEW_VERSION = "daily_review_v2"


def iso_day(value: str | date) -> str | None:
    try:
        if isinstance(value, date):
            return value.isoformat()
        text = str(value)
        return datetime.strptime(text, "%Y%m%d").date().isoformat() if len(text) == 8 else date.fromisoformat(text).isoformat()
    except (ValueError, TypeError):
        return None


def day_keys(value: str | date) -> tuple[str, str]:
    day = iso_day(value)
    if day is None:
        raise ValueError("复盘日期无效")
    return day, day.replace("-", "")


def object_json(value) -> dict:
    try:
        obj = json.loads(value or "{}") if isinstance(value, str) or value is None else value
        return obj if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        return {}


def generated_time(meta: dict) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(meta.get("generated_at") or "").replace("Z", "+00:00"))
        # Existing producer used the Beijing wall clock; do not infer from created_at.
        return parsed.replace(tzinfo=BJ_TZ) if parsed.tzinfo is None else parsed.astimezone(BJ_TZ)
    except (ValueError, TypeError):
        return None


def selection_version(day: str, meta: dict, items: list[dict]) -> str | None:
    from app.services.selection_notifications import daily_selection_version
    generated = generated_time(meta)
    if generated is None or generated.date().isoformat() != iso_day(day) or not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
        return None
    contract = meta.get('selection_contract')
    if contract is not None and (not isinstance(contract, dict) or contract.get('version') != 'daily_selection_v2' or
                                 contract.get('trade_date') != iso_day(day) or contract.get('generated_at') != meta.get('generated_at')):
        return None
    version = daily_selection_version(iso_day(day), meta["generated_at"], items)
    stored = meta.get("selection_version")
    return version if stored in (None, version) else None


def bound_review(context, *, day: str, meta: dict, items: list[dict], symbol: str, persisted_version: str | None) -> bool:
    c = object_json(context)
    version = selection_version(day, meta, items)
    return bool(version and c.get("contract_version") == REVIEW_VERSION and
                c.get("selection_version") == version and
                persisted_version == version and
                c.get("performance_date") == iso_day(day) and
                c.get("symbol") == symbol and any(i.get("symbol") == symbol for i in items))


def trusted_review(context, *, day: str, meta: dict, items: list[dict], symbol: str, persisted_version: str | None) -> bool:
    c = object_json(context)
    generated = generated_time(meta)
    return (bound_review(c, day=day, meta=meta, items=items, symbol=symbol, persisted_version=persisted_version) and
            c.get("eligible_for_stats") is True and c.get('observation_only') is not True and
            c.get('window_kind') == 'open_to_close' and generated is not None and
            iso_day(day) <= beijing_today().isoformat() and
            (generated.hour, generated.minute) < (9, 30) and
            c.get('window_start') == f'{iso_day(day)}T09:30:00+08:00' and
            c.get('window_end') == f'{iso_day(day)}T15:00:00+08:00' and
            _close_identity(c.get('quote'), day) and _close_identity(c.get('benchmark'), day))


def _close_identity(value, day: str) -> bool:
    if not isinstance(value, dict) or not value.get('source') or value.get('quality') not in ('high', 'medium'):
        return False
    try:
        stamp = datetime.fromisoformat(str(value.get('as_of') or '').replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return False
        close = datetime.fromisoformat(f'{iso_day(day)}T15:00:00+08:00')
        age = (close - stamp).total_seconds()
        return 0 <= age <= DEFAULT_FRESH_WITHIN_SECONDS
    except (ValueError, TypeError):
        return False


def finite_number(value) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def selection_rows_query(limit: int, *, through: str | date):
    """Choose ISO over compact per real day before limiting; exclude future sets."""
    from sqlalchemy import func, select
    from app.models.daily_pick import DailyPickSet
    _, compact = day_keys(through)
    normalized = func.replace(DailyPickSet.date, '-', '')
    days = select(func.min(DailyPickSet.date)).where(normalized <= compact).group_by(normalized).order_by(normalized.desc()).limit(limit)
    return select(DailyPickSet).where(DailyPickSet.date.in_(days)).order_by(normalized.desc())
