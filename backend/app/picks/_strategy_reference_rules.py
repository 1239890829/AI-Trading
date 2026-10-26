"""Frozen, price-only manual research rules; no score or execution consumer.

These are our reproducible versions of supplied ideas, not the TSP engine. EMA
uses the existing tech_score seed convention (first observed close). The volume
rule's previous-five-session denominator is our explicit additional definition.
"""
from __future__ import annotations

from collections import deque
from bisect import bisect_right
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
import re


MIN_BARS = 60
REFERENCE_STOCK_PATTERN = r"(?:(?:60|68)\d{4}\.SH|(?:00|30)\d{4}\.SZ|(?:43|83|87|92)\d{4}\.BJ)"
RULES = {
    "macd_golden_cross": {"version": "ashare-macd-12-26-9-v1", "name": "MACD金叉",
        "definition": "EMA12-EMA26为DIF，DEA=EMA9(DIF)；前日DIF<=DEA且当日DIF>DEA。",
        "dependencies": "完整有效价格前缀；不依赖成交量。",
        "supplement": "EMA从该证券首个输入收盘初始化；至少60根。柱线转正不重复计分。"},
    "ma_bullish": {"version": "ashare-ma-5-10-20-v1", "name": "均线多头",
        "definition": "当日close>MA5>MA10>MA20，均线含当日收盘。",
        "dependencies": "最近20根有效价格；不依赖成交量。",
        "supplement": "明确采用排列与价格位置，不声称取得TSP未公开的发散或其他过滤算法。"},
    "ma_golden_cross": {"version": "ashare-ma-cross-volume5-v1", "name": "均线金叉与量能确认",
        "definition": "MA5由<=跨至>MA10或MA20，且close>MA5、当日量/前5根均量>1。",
        "dependencies": "最近21根有效价格、当前和前5根真实非负成交量，分母必须为正。",
        "supplement": "5日分母且不含当日，是本系统补充定义；不声称为TSP原版。"},
}
DEFINITION_DIGEST = hashlib.sha256(json.dumps(
    {"min_bars": MIN_BARS, "rules": RULES}, ensure_ascii=False, sort_keys=True,
    separators=(",", ":")).encode()).hexdigest()


def reference_rule_metadata() -> dict:
    return deepcopy(RULES)


def is_reference_stock_code(value) -> bool:
    """One declared A-share reading scope, shared by runtime and offline study."""
    return isinstance(value, str) and re.fullmatch(REFERENCE_STOCK_PATTERN, value) is not None


def _day(raw) -> str | None:
    if not isinstance(raw, str) or len(raw) != 10:
        return None
    try:
        return raw if date.fromisoformat(raw).isoformat() == raw else None
    except ValueError:
        return None


def _positive(raw) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        return None
    try:
        return float(raw) if math.isfinite(raw) else None
    except OverflowError:
        return None


def _volume(raw) -> float | None:
    return 0.0 if type(raw) in (int, float) and raw == 0 else _positive(raw)


def _condition(key, met, value=None, reference=None):
    return {"key": key, "met": met, "value": value, "reference": reference}


def _verdicts(day, metrics, reasons, digest, input_start, count, warnings=()):
    builders = {
            "macd_golden_cross": lambda: [
                _condition("previous_dif_lte_dea", metrics["previous_dif"] <= metrics["previous_dea"], metrics["previous_dif"], metrics["previous_dea"]),
                _condition("dif_above_dea", metrics["dif"] > metrics["dea"], metrics["dif"], metrics["dea"])],
            "ma_bullish": lambda: [
                _condition("close_above_ma5", metrics["close"] > metrics["ma5"], metrics["close"], metrics["ma5"]),
                _condition("ma5_above_ma10", metrics["ma5"] > metrics["ma10"], metrics["ma5"], metrics["ma10"]),
                _condition("ma10_above_ma20", metrics["ma10"] > metrics["ma20"], metrics["ma10"], metrics["ma20"])],
            "ma_golden_cross": lambda: [
                _condition("ma5_cross_ma10_or_ma20", any(
                    metrics["previous_ma5"] <= metrics[f"previous_ma{n}"] and metrics["ma5"] > metrics[f"ma{n}"] for n in (10, 20))),
                _condition("close_above_ma5", metrics["close"] > metrics["ma5"], metrics["close"], metrics["ma5"]),
                _condition("volume_above_previous5", metrics["volume_ratio5"] > 1, metrics["volume_ratio5"], 1.0)],
    }
    checks = {key: [_condition("input_valid", None)] if reasons[key] else build() for key, build in builders.items()}
    return {key: {"key": key, "version": RULES[key]["version"], "definition_digest": DEFINITION_DIGEST,
                  "trade_date": day, "state": "unknown" if reasons[key] else "matched" if all(c["met"] for c in conditions) else "not_matched",
                  "reason_codes": list(dict.fromkeys(reasons[key])), "data_warnings": list(dict.fromkeys(warnings)), "conditions": conditions,
                  "metrics": dict(metrics), "input_digest": digest, "input_start": input_start, "input_rows": count,
                  "scope": "manual_price_research_only", "score_contribution": False, "execution_eligible": False}
            for key, conditions in checks.items()}


def iter_reference_rules(bars, *, through: str, since: str | None = None, expected_sessions=None):
    """One ordered prefix pass shared by all three frozen hypotheses.

    ``close`` is the reader's consistently adjusted close; ``volume`` is actual
    same-unit volume. Explicit quality_issue/suspended data are quarantined. A
    supplied market-session list checks the most recent 60 sessions, without
    calling a calendar or inferring holidays from natural-day gaps.
    """
    if _day(through) is None or since is not None and _day(since) is None:
        raise ValueError("through/since must be exact ISO dates")
    sessions = None
    if expected_sessions is not None:
        sessions = list(expected_sessions)
        if any(_day(day) is None for day in sessions) or sessions != sorted(set(sessions)):
            raise ValueError("expected_sessions must be ordered unique ISO dates")
    selected, reasons = [], []
    for raw in bars:
        day = _day(raw.get("date")) if isinstance(raw, dict) else None
        if day is None:
            reasons.append("invalid_date")
        elif day <= through:
            selected.append(raw)
    closes, volumes = deque(maxlen=21), deque(maxlen=6)
    seen, observations = set(), set()
    ema12 = ema26 = dea = None
    ema_valid = True
    input_start, count, last = None, 0, None
    digest = hashlib.sha256()
    for raw in selected:
        day = raw["date"]
        if last is not None and day <= last:
            reasons.append("duplicate_date" if day == last or day in seen else "dates_not_ascending")
        last = day
        seen.add(day)
        input_start = input_start or day
        count += 1
        close, volume = _positive(raw.get("close")), _volume(raw.get("volume"))
        quality = raw.get("quality_issue")
        suspended = raw.get("suspended")
        if close is None:
            observations.add("price_prefix_contains_missing_values")
        if volume is None:
            observations.add("volume_prefix_contains_missing_values")
        elif volume == 0:
            observations.add("volume_prefix_contains_real_zero")
        if suspended is True:
            observations.add("prefix_contains_declared_suspension")
        digest.update(json.dumps([day, close, volume, quality, suspended],
                                 ensure_ascii=False, sort_keys=True, default=str).encode())
        if quality:
            reasons.append("source_quality_issue")
        previous_dif = None if ema12 is None or not ema_valid else ema12 - ema26
        previous_dea = dea if ema_valid else None
        if close is None:
            ema_valid = False  # no invented EMA continuation across an unknown price
        if ema_valid:
            ema12 = close if ema12 is None else close * (2 / 13) + ema12 * (11 / 13)
            ema26 = close if ema26 is None else close * (2 / 27) + ema26 * (25 / 27)
            dif = ema12 - ema26
            dea = dif if dea is None else dif * 0.2 + dea * 0.8
        else:
            dif = None
        closes.append(close)
        volumes.append(volume)
        if since is None or day >= since:
            values, observed_volumes = list(closes), list(volumes)
            previous_volumes = observed_volumes[-6:-1]
            volume_base = (math.fsum(v / 5 for v in previous_volumes)
                           if len(previous_volumes) == 5 and all(v is not None for v in previous_volumes) else None)
            metrics = {"close": close, "volume": volume, "dif": dif, "dea": dea if ema_valid else None,
                       "previous_dif": previous_dif, "previous_dea": previous_dea,
                       "volume_ma5_previous": volume_base,
                       "volume_ratio5": volume / volume_base if volume_base and volume is not None else None}
            for n in (5, 10, 20):
                metrics[f"ma{n}"] = (math.fsum(v / n for v in values[-n:])
                                      if len(values) >= n and all(v is not None for v in values[-n:]) else None)
                previous_window = values[-n - 1:-1]
                metrics[f"previous_ma{n}"] = (math.fsum(v / n for v in previous_window)
                    if len(previous_window) == n and all(v is not None for v in previous_window) else None)
            common = list(reasons)
            if count < MIN_BARS:
                common.append("insufficient_history")
            if suspended is True:
                common.append("suspended")
            elif suspended is not None and type(suspended) is not bool:
                common.append("invalid_suspension_flag")
            current_reasons = {key: list(common) for key in RULES}
            if not ema_valid:
                current_reasons["macd_golden_cross"].append("invalid_or_missing_close")
            if len(values) < 20 or any(v is None for v in values[-20:]):
                current_reasons["ma_bullish"].append("invalid_or_missing_close")
            if len(values) < 21 or any(v is None for v in values[-21:]):
                current_reasons["ma_golden_cross"].append("invalid_or_missing_close")
            if len(observed_volumes) < 6 or any(v is None for v in observed_volumes):
                current_reasons["ma_golden_cross"].append("invalid_or_missing_volume")
            elif volume_base == 0:
                current_reasons["ma_golden_cross"].append("volume_baseline_zero")
            if sessions is not None:
                stop = bisect_right(sessions, day)
                for key, window in (("macd_golden_cross", MIN_BARS), ("ma_bullish", 20), ("ma_golden_cross", 21)):
                    recent = sessions[max(0, stop - window):stop]
                    if any(session >= input_start and session not in seen for session in recent):
                        current_reasons[key].append("missing_market_sessions")
                    if not stop or sessions[stop - 1] != day:
                        current_reasons[key].append("requested_session_not_in_calendar")
            dependencies = {"macd_golden_cross": ("dif", "dea", "previous_dif", "previous_dea"),
                "ma_bullish": ("close", "ma5", "ma10", "ma20"),
                "ma_golden_cross": ("close", "ma5", "ma10", "ma20", "previous_ma5", "previous_ma10", "previous_ma20", "volume_ratio5")}
            for key, fields in dependencies.items():
                if any(isinstance(metrics[field], float) and not math.isfinite(metrics[field]) for field in fields):
                    current_reasons[key].append("non_finite_feature")
            metrics = {key: None if isinstance(value, float) and not math.isfinite(value) else value for key, value in metrics.items()}
            warnings = sorted(observations) + (["selected_volume_unknown_not_execution_evidence"] if volume is None else
                        ["zero_volume_not_execution_evidence"] if volume == 0 else [])
            yield day, _verdicts(day, metrics, current_reasons, digest.hexdigest(), input_start, count, warnings)
    if last != through:
        why = {key: [*reasons, "missing_requested_session"] for key in RULES}
        yield through, _verdicts(through, {}, why, digest.hexdigest(), input_start, count)


def evaluate_reference_rules(bars, *, through: str, expected_sessions=None) -> dict:
    """Final requested-day verdict, never a silent fallback to a newer/older day."""
    result = None
    for _day_key, verdicts in iter_reference_rules(bars, through=through, since=through,
                                                   expected_sessions=expected_sessions):
        result = verdicts
    return result
