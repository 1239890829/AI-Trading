"""Read-time projection of the existing metric store; never a report-time input."""
from __future__ import annotations

from datetime import date, timedelta
from math import isfinite

from app.core.bjtime import beijing_now
from app.review.provenance import source_time
from app.sentiment import metric_history


def _count(value) -> int | None:
    return value if type(value) is int and 0 <= value <= 9_007_199_254_740_991 else None


def _rate(value) -> float | None:
    if type(value) not in (int, float) or not 0 <= value <= 1 or not isfinite(value):
        return None
    return float(value)


def _point(day: date, row: dict) -> dict:
    counts = {key: _count(row.get(key)) for key in ("limit_up", "n_break", "max_board")}
    rates = {key: _rate(row.get(key)) for key in ("break_rate", "promo_1to2", "promo_2to3")}
    missing = [key for key, value in {**counts, **rates}.items() if value is None]
    base = None if counts["limit_up"] is None or counts["n_break"] is None else counts["limit_up"] + counts["n_break"]
    seal = None
    if base:
        expected_break = counts["n_break"] / base
        if rates["break_rate"] is not None and abs(rates["break_rate"] - expected_break) <= 0.000051:
            seal = counts["limit_up"] / base
        else:
            missing.append("seal_rate:break_rate_missing_or_inconsistent")
    else:
        missing.append("seal_rate:no_positive_denominator")
    return {"date": day.isoformat(), **counts, **rates, "seal_rate": seal,
            "seal_base": base, "promo_1to2_base": None, "promo_2to3_base": None,
            "missing": missing}


def read_metric_history_reference(day: date) -> dict:
    """At most 30 calendar days, using only the metric owner's local read method.

    The store contains collected aggregates, not immutable original pools. Its file
    clock cannot establish what an old report knew. Missing calendar dates include
    market closures as well as missing observations; no calendar is fetched here.
    """
    now = beijing_now()
    end = min(day, now.date())
    start = end - timedelta(days=29)
    base = {
        "contract_version": "metric_history_reference_v1",
        "role": "read_time_aggregate_reference", "source": "sentiment.metric_history",
        "requested_trade_date": day.isoformat(), "window_start": start.isoformat(),
        "window_end": end.isoformat(), "read_at": now.isoformat(),
        "dataset_updated_at": None, "source_as_of": None, "raw_source_sha256": None,
        "available_at_original_report": "unknown", "state": "unavailable",
        "points": [], "unrecorded_dates": [], "invalid_rows": 0,
        "coverage": {"calendar_days": 30, "recorded_days": 0,
                     "effective_days": {key: 0 for key in ("seal_rate", "promo_1to2", "promo_2to3", "max_board")}},
        "reason": "本地汇总缺失、不可读或窗口内无有效记录；未取数、回补或重新生成。",
        "limitations": ["读取时追加的历史汇总参考，不代表原报告当时已见。",
                        "原库未保留逐日源钟和原始池哈希，采集日期不等于点时凭据。",
                        "晋级率原库未保留分子与基数，不能补算，也不是上涨概率或策略胜率。",
                        "未记录自然日包含休市与缺档，未推断交易日完整性。"],
    }
    try:
        store = metric_history.load()
    except Exception:
        return {**base, "state": "error", "reason": "本地历史汇总读取失败；原报告仍可阅读，未触发回补。"}
    rows = store.get("days") if isinstance(store, dict) else None
    if not isinstance(rows, dict):
        return base
    points, invalid = [], 0
    for key, row in rows.items():
        try:
            parsed = date.fromisoformat(key) if isinstance(key, str) and len(key) == 10 else None
        except ValueError:
            parsed = None
        if parsed is None or parsed.isoformat() != key:
            invalid += 1
            continue
        if not start <= parsed <= end:
            continue
        if not isinstance(row, dict) or row.get("date") != key:
            invalid += 1
            continue
        points.append(_point(parsed, row))
    points.sort(key=lambda value: value["date"])
    recorded = {point["date"] for point in points}
    missing_days = [(start + timedelta(days=i)).isoformat() for i in range(30)
                    if (start + timedelta(days=i)).isoformat() not in recorded]
    clock = source_time(store.get("updated_at"))
    return {**base, "state": "partial" if points else "unavailable", "points": points,
            "dataset_updated_at": clock.isoformat() if clock and clock <= now else None,
            "unrecorded_dates": missing_days, "invalid_rows": invalid,
            "coverage": {"calendar_days": 30, "recorded_days": len(points),
                         "effective_days": {key: sum(point[key] is not None for point in points)
                                            for key in base["coverage"]["effective_days"]}},
            "reason": "读取时追加的已采集历史汇总；只覆盖所选日及此前30个自然日内的有效记录。" if points else base["reason"]}
