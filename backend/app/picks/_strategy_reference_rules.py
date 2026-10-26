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
BASE_RULES = {
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
EXTRA_RULES = {
    "rsi_reversal": ("RSI超卖反转", "前日简单14期RSI<=30，当日RSI>30，且收盘高于前日。"),
    "shrink_pullback": ("缩量回踩", "MA20不低于前日；收盘>=MA20且距MA10或MA20不超过2%；当日量<前5日均量的0.8倍。"),
    "volume_breakout": ("放量突破", "收盘突破前20日最高价；当日成交额>=前5日均额的1.5倍。"),
    "l1_l2_volume_resonance": ("L1-L2量价共振", "L1为阳线实体>=开盘3%、量>=前5日均量2倍；12至30交易日前出现L1；L2量>=前60日均量3倍，下影>=实体2倍且收盘位于当日区间上半部。"),
    "one_yang_three_yin": ("一阳夹三阴", "T-3阳线实体>=开盘5%；随后三根阴线实体各<=开盘3%，三日均量小于阳线量，三日最低价不破阳线开盘。"),
    "w_bottom_breakout": ("W底突破", "60日内已确认局部双低点，间隔>=10日、低点价差<=5%、其间反弹>=3%；第二低点至少已有2日右侧确认；放量>=前5日均量1.5倍，收盘>MA5/10/20且距20日最低价<=15%。"),
    "bottom_volume": ("底部放量", "相对60交易日前收盘下跌>=20%；阳线实体>=开盘3%，成交量>=前20日均量2倍；此前20日未出现同一条件。"),
    "box_oscillation": ("箱体震荡", "前20日最高/最低振幅<=15%；当日阳线且收盘位于原箱体下方20%区间内、未跌破箱底。"),
}
RULES = {**BASE_RULES, **{key: {"version": f"ashare-{key.replace('_', '-')}-v1", "name": name,
    "definition": definition, "dependencies": "已完成日线；复权OHLC、实际成交量与原币成交额按本条件读取。",
    "supplement": "本系统独立冻结的研究假设，非TSP完整原版；单组参数，无自动寻优；不晋级选股或执行。"}
    for key, (name, definition) in EXTRA_RULES.items()}}
BASE_DEFINITION_DIGEST = hashlib.sha256(json.dumps(
    {"min_bars": MIN_BARS, "rules": BASE_RULES}, ensure_ascii=False, sort_keys=True,
    separators=(",", ":")).encode()).hexdigest()
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


def _extra_verdicts(history, day, common, digest, input_start, count, warnings=(), sessions=None, seen=()):
    """Frozen independent hypotheses; every condition uses only the observed prefix."""
    result = {}
    # Parse each observed row once for this shared signal day. The eight methods
    # then slice numeric arrays rather than revalidating six fields per window.
    series = {key: [row.get(key) for row in history]
              for key in ('close', 'open', 'high', 'low', 'volume', 'turnover')}
    bad_candles = [None in (op, hi, lo, cl) or not lo <= min(op, cl) <= max(op, cl) <= hi
        for op, hi, lo, cl in zip(series['open'], series['high'], series['low'], series['close'])]
    ma_shared = {n: math.fsum(value/n for value in series['close'][-n:])
                 for n in (5, 10, 20) if len(history) >= n and all(value is not None for value in series['close'][-n:])}
    requirements = {"rsi_reversal": (16, False, False, False), "shrink_pullback": (21, False, True, False),
        "volume_breakout": (21, True, False, True), "l1_l2_volume_resonance": (61, True, True, False),
        "one_yang_three_yin": (4, True, True, False), "w_bottom_breakout": (60, True, True, False),
        "bottom_volume": (81, True, True, False), "box_oscillation": (21, True, False, False)}
    for key, (window, needs_ohlc, needs_volume, needs_amount) in requirements.items():
        reasons, metrics = list(common), {}
        rows = history[-window:]
        if len(rows) < window:
            reasons.append("insufficient_rule_history")
        c = series['close'][-window:]
        if any(value is None for value in c):
            reasons.append("invalid_or_missing_close")
        o, h, low, v, amount = (series[name][-window:] for name in ('open', 'high', 'low', 'volume', 'turnover'))
        if needs_ohlc and any(bad_candles[-window:]):
            reasons.append("invalid_or_missing_adjusted_ohlc")
        if needs_volume and any(value is None for value in v):
            reasons.append("invalid_or_missing_volume")
        if needs_amount and any(value is None for value in amount):
            reasons.append("invalid_or_missing_turnover")
        if sessions is not None:
            stop = bisect_right(sessions, day)
            if any(session >= input_start and session not in seen for session in sessions[max(0, stop-window):stop]):
                reasons.append("missing_market_sessions")
            if not stop or sessions[stop-1] != day:
                reasons.append("requested_session_not_in_calendar")
        conditions = []
        if not reasons:
            def mean(values):
                return math.fsum(value/len(values) for value in values)

            def condition(name, met, value=None, reference=None):
                conditions.append(_condition(name, bool(met), value, reference))

            ma = ma_shared if len(c) >= 20 else {}
            metrics = {"close": c[-1], **{f"ma{n}": value for n, value in ma.items()}}
            if key == "rsi_reversal":
                def rsi(values):
                    differences = [b-a for a, b in zip(values, values[1:])]
                    gain = math.fsum(max(value, 0) for value in differences)
                    loss = math.fsum(max(-value, 0) for value in differences)
                    return None if gain == loss == 0 else 100.0 if loss == 0 else 100 - 100 / (1+gain/loss)
                prior, current = rsi(c[-16:-1]), rsi(c[-15:])
                if prior is None or current is None:
                    reasons.append("flat_rsi_undefined")
                else:
                    metrics.update(previous_rsi14=prior, rsi14=current)
                    condition("rsi14_crosses_above_30", prior <= 30 < current, current, 30)
                    condition("close_above_previous", c[-1] > c[-2], c[-1], c[-2])
            elif key == "shrink_pullback":
                base = mean(v[-6:-1])
                if base <= 0:
                    reasons.append("volume_baseline_zero")
                else:
                    distance = min(abs(c[-1]/ma[n]-1) for n in (10, 20))
                    metrics.update(volume_ratio5=v[-1]/base, support_distance=distance)
                    condition("ma20_not_declining", ma[20] >= mean(c[-21:-1]))
                    condition("close_at_or_above_ma20", c[-1] >= ma[20], c[-1], ma[20])
                    condition("distance_to_ma10_or_ma20_lte_2pct", distance <= .02, distance, .02)
                    condition("volume_below_previous5_08", v[-1]/base < .8, v[-1]/base, .8)
            elif key == "volume_breakout":
                base = mean(amount[-6:-1])
                if base <= 0:
                    reasons.append("turnover_baseline_zero")
                else:
                    metrics.update(turnover_ratio5=amount[-1]/base, previous_high20=max(h[:-1]))
                    condition("close_breaks_previous20_high", c[-1] > max(h[:-1]), c[-1], max(h[:-1]))
                    condition("turnover_above_previous5_15", amount[-1]/base >= 1.5, amount[-1]/base, 1.5)
            elif key == "l1_l2_volume_resonance":
                base = mean(v[:-1])
                if base <= 0:
                    reasons.append("volume_baseline_zero")
                else:
                    l1 = any(c[i]/o[i]-1 >= .03 and mean(v[i-5:i]) > 0
                             and v[i]/mean(v[i-5:i]) >= 2 for i in range(len(rows)-31, len(rows)-12))
                    body, wick = abs(c[-1]-o[-1]), min(c[-1], o[-1])-low[-1]
                    metrics.update(volume_ratio60=v[-1]/base, lower_wick=wick, candle_body=body)
                    condition("prior_l1_12_to_30_sessions", l1)
                    condition("volume_above_previous60_3", v[-1]/base >= 3, v[-1]/base, 3)
                    condition("lower_wick_above_twice_body", body > 0 and wick >= 2*body, wick, 2*body)
                    condition("close_in_upper_half", c[-1] >= (h[-1]+low[-1])/2)
            elif key == "one_yang_three_yin":
                condition("first_body_above_5pct", c[0]/o[0]-1 >= .05, c[0]/o[0]-1, .05)
                condition("three_small_bearish_bodies", all(0 < (op-cl)/op <= .03 for op, cl in zip(o[1:], c[1:])))
                condition("three_volume_contracts", mean(v[1:]) < v[0], mean(v[1:]), v[0])
                condition("three_lows_hold_first_open", min(low[1:]) >= o[0], min(low[1:]), o[0])
            elif key == "w_bottom_breakout":
                troughs = [i for i in range(2, len(rows)-2) if low[i] < min(low[i-2:i]+low[i+1:i+3])]
                pairs = [(a, b) for a in troughs for b in troughs if b-a >= 10 and abs(low[b]/low[a]-1) <= .05
                         and max(h[a+1:b])/max(low[a], low[b])-1 >= .03]
                base = mean(v[-6:-1])
                if base <= 0:
                    reasons.append("volume_baseline_zero")
                else:
                    metrics.update(volume_ratio5=v[-1]/base, distance_to_low20=c[-1]/min(low[-20:])-1)
                    condition("confirmed_double_bottom", bool(pairs))
                    condition("close_above_ma5_10_20", all(c[-1] > ma[n] for n in (5, 10, 20)))
                    condition("distance_to_low20_lte_15pct", metrics["distance_to_low20"] <= .15, metrics["distance_to_low20"], .15)
                    condition("volume_above_previous5_15", v[-1]/base >= 1.5, v[-1]/base, 1.5)
            elif key == "bottom_volume":
                def signal(i):
                    base = mean(v[i-20:i])
                    return c[i]/c[i-60]-1 <= -.2 and c[i]/o[i]-1 >= .03 and base > 0 and v[i]/base >= 2
                base = mean(v[-21:-1])
                if base <= 0:
                    reasons.append("volume_baseline_zero")
                else:
                    metrics.update(return60=c[-1]/c[-61]-1, volume_ratio20=v[-1]/base)
                    condition("return60_lte_minus20pct", metrics["return60"] <= -.2, metrics["return60"], -.2)
                    condition("positive_body_above_3pct", c[-1]/o[-1]-1 >= .03, c[-1]/o[-1]-1, .03)
                    condition("volume_above_previous20_2", v[-1]/base >= 2, v[-1]/base, 2)
                    condition("first_in_previous20", not any(signal(i) for i in range(60, 80)))
            elif key == "box_oscillation":
                top, bottom = max(h[:-1]), min(low[:-1])
                if top == bottom:
                    reasons.append("flat_box_undefined")
                else:
                    metrics.update(box_top=top, box_bottom=bottom, box_amplitude=top/bottom-1,
                                   box_position=(c[-1]-bottom)/(top-bottom))
                    condition("box20_amplitude_lte_15pct", top/bottom-1 <= .15, top/bottom-1, .15)
                    condition("close_in_lower20pct", 0 <= metrics["box_position"] <= .2, metrics["box_position"], .2)
                    condition("positive_body", c[-1] > o[-1], c[-1], o[-1])
        if any(isinstance(value, float) and not math.isfinite(value) for value in metrics.values()):
            reasons.append("non_finite_feature")
        metrics = {name: None if isinstance(value, float) and not math.isfinite(value) else value for name, value in metrics.items()}
        if reasons:
            conditions = [_condition("input_valid", None)]
        result[key] = {"key": key, "version": RULES[key]["version"], "definition_digest": DEFINITION_DIGEST,
            "trade_date": day, "state": "unknown" if reasons else "matched" if conditions and all(c["met"] for c in conditions) else "not_matched",
            "reason_codes": list(dict.fromkeys(reasons)), "data_warnings": list(warnings), "conditions": conditions,
            "metrics": metrics, "input_digest": digest, "input_start": input_start, "input_rows": count,
            "scope": "manual_price_research_only", "score_contribution": False, "execution_eligible": False}
    return result


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
    closes, volumes, history = deque(maxlen=21), deque(maxlen=6), deque(maxlen=91)
    seen, observations = set(), set()
    ema12 = ema26 = dea = None
    ema_valid = True
    input_start, count, last = None, 0, None
    digest, extra_digest = hashlib.sha256(), hashlib.sha256()
    first_output = next((index for index, row in enumerate(selected) if since is None or row['date'] >= since), len(selected))
    extra_start = max(0, first_output-90)
    for index, raw in enumerate(selected):
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
        extra_digest.update(json.dumps([day, close, volume, quality, suspended,
            *[raw.get(key) for key in ('open', 'high', 'low', 'turnover')]],
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
        if index >= extra_start:
            history.append({'date': day, 'close': close, 'volume': volume,
                **{key: _positive(raw.get(key)) for key in ('open', 'high', 'low')},
                'turnover': _volume(raw.get('turnover'))})
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
            current_reasons = {key: list(common) for key in BASE_RULES}
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
            verdicts = _verdicts(day, metrics, current_reasons, digest.hexdigest(), input_start, count, warnings)
            verdicts.update(_extra_verdicts(list(history), day, common, extra_digest.hexdigest(), input_start, count, warnings,
                sessions, seen))
            yield day, verdicts
    if last != through:
        why = {key: [*reasons, "missing_requested_session"] for key in BASE_RULES}
        verdicts = _verdicts(through, {}, why, digest.hexdigest(), input_start, count)
        verdicts.update(_extra_verdicts([], through, [*reasons, "missing_requested_session"], digest.hexdigest(), input_start, count))
        yield through, verdicts


def evaluate_reference_rules(bars, *, through: str, expected_sessions=None) -> dict:
    """Final requested-day verdict, never a silent fallback to a newer/older day."""
    result = None
    for _day_key, verdicts in iter_reference_rules(bars, through=through, since=through,
                                                   expected_sessions=expected_sessions):
        result = verdicts
    return result
