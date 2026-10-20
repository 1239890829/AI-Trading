"""Lossless shared view of original frozen-input indicator calculations.

This module only arranges values. It imports no optional math package and does
not collect data, calculate an indicator, run a model or write state.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json

INDICATORS = (
    "close_50_sma", "close_200_sma", "close_10_ema", "macd", "macds", "macdh",
    "rsi", "boll", "boll_ub", "boll_lb", "atr", "vwma", "mfi",
)
MINIMUM = dict(zip(INDICATORS, (50, 200, 10, 26, 35, 35, 14, 20, 20, 20, 14, 14, 14)))


def _digest(value):
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode()).hexdigest()


def build_latest_session_projection(ohlcv, indicator_rows):
    """Name one actual historical session; do not invent a current quote.

    Close comes from the raw OHLCV row. Indicators retain their exact strings
    or None on that same row. Missing last-day values are never forward filled.
    """
    if len(ohlcv) != len(indicator_rows):
        raise ValueError("latest_session_history_alignment_invalid")
    if not ohlcv:
        return {"session": None, "frozen_history_close": None,
                "indicators": dict.fromkeys(INDICATORS)}
    raw, values = ohlcv[-1], indicator_rows[-1]
    if len(values) != 14 or values[0] != raw["Date"]:
        raise ValueError("latest_session_history_alignment_invalid")
    return {"session": raw["Date"], "frozen_history_close": raw["Close"],
            "indicators": dict(zip(INDICATORS, values[1:]))}


def build_indicator_table(packet, indicator_results, formula):
    """Keep exact native strings and nulls in one fixed full-history table.

    ``indicator_results`` maps every fixed name to the corresponding native
    ``FrozenEvidence.frozen_indicator`` result or None. Original provenance is
    retained; source observation clocks remain in the parent frozen packet.
    """
    market = packet.get("market_data") or {}
    rows = market.get("ohlcv", [])
    if not isinstance(rows, list) or len(rows) > 300:
        raise ValueError("bounded_frozen_ohlcv_required")
    if any(not isinstance(row, dict) or not isinstance(row.get("Date"), str) for row in rows):
        raise ValueError("dated_frozen_ohlcv_required")
    days = [row["Date"] for row in rows]
    if days != sorted(set(days)) or any(date.fromisoformat(day).isoformat() != day for day in days):
        raise ValueError("unique_ordered_session_labels_required")
    trade_date = packet["trade_date"]
    if (date.fromisoformat(trade_date).isoformat() != trade_date or any(day > trade_date for day in days)
            or (days and (date.fromisoformat(trade_date) - date.fromisoformat(days[0])).days > 400)):
        raise ValueError("indicator_dates_outside_frozen_cutoff")
    if not isinstance(indicator_results, dict) or set(indicator_results) != set(INDICATORS):
        raise ValueError("all_fixed_indicator_results_required")
    if not isinstance(formula, dict):
        raise ValueError("formula_identity_required")
    raw_sha = _digest(rows)
    provenance_sha = _digest(market.get("history_provenance"))
    values, metadata = {}, {}
    for name in INDICATORS:
        result = indicator_results[name]
        minimum = MINIMUM[name]
        if result is None:
            values[name] = {}
            metadata[name] = {"minimum_observations": minimum,
                              "initialization_rows": len(rows), "available": False}
            continue
        if (not isinstance(result, dict) or result.get("indicator") != name
                or result.get("initialization_rows") != len(rows)
                or result.get("minimum_observations") != minimum
                or result.get("frozen_ohlcv_sha256") != raw_sha
                or result.get("history_provenance_sha256") != provenance_sha):
            raise ValueError("native_indicator_identity_mismatch")
        mapping = result.get("values")
        if (not isinstance(mapping, dict) or not set(mapping).issubset(days[minimum - 1:])
                or any(not isinstance(value, str) or not value or value == "N/A" for value in mapping.values())):
            raise ValueError("native_indicator_values_outside_scope")
        values[name] = deepcopy(mapping)
        metadata[name] = {key: deepcopy(value) for key, value in result.items() if key != "values"}
    table_rows = [[day] + [values[name].get(day) for name in INDICATORS] for day in days]
    table = {
        "schema": "original-ta-indicators-lossless-table-v1",
        "columns": ["session", *INDICATORS], "rows": table_rows,
        "formula": deepcopy(formula), "indicators": metadata,
        "input_ohlcv_sha256": raw_sha,
        "requested_coverage_days": 400, "actual_rows": len(days),
        "actual_first_session": days[0] if days else None,
        "actual_last_session": days[-1] if days else None,
        "trade_date": trade_date,
        "source_publication_clock": None, "source_verified": False,
        "no_new_source_facts": True,
        "latest_session_projection": build_latest_session_projection(rows, table_rows),
    }
    # Reject non-JSON formula/provenance without changing any native value.
    _digest(table)
    return table
