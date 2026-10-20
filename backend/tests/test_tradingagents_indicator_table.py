"""Lossless formatting checks; synthetic calculations are not market gold."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json

import pytest

from app.services.tradingagents_native.indicator_table import (
    INDICATORS, MINIMUM, build_indicator_table,
)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@pytest.fixture
def material():
    start = date(2026, 1, 1)
    rows = [{"Date": (start + timedelta(days=i)).isoformat(), "Open": 1.0,
        "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 100.0} for i in range(60)]
    packet = {"trade_date": rows[-1]["Date"], "as_of": "2026-03-01T16:00:00+08:00",
              "market_data": {"ohlcv": rows, "history_provenance": {"source": "public_fixture", "received_at": "2026-03-01T15:00:00+08:00"}}}
    results = {}
    for name in INDICATORS:
        if MINIMUM[name] > len(rows):
            results[name] = None
            continue
        results[name] = {"indicator": name, "values": {
            row["Date"]: "1.2345678901234567890" for row in rows[MINIMUM[name] - 1:]},
            "frozen_ohlcv_sha256": digest(rows),
            "history_provenance_sha256": digest(packet["market_data"]["history_provenance"]),
            "initialization_rows": len(rows), "minimum_observations": MINIMUM[name],
            "source_publication_clock": None, "source_verified": False}
    return packet, results, {"entry": "synthetic values; no market claim"}


def test_exact_original_precision_date_alignment_and_unknowns(material):
    packet, results, formula = material
    before = deepcopy(material)
    table = build_indicator_table(packet, results, formula)
    # The compact representation must invert without rounding or losing dates.
    for index, name in enumerate(INDICATORS, 1):
        actual = {row[0]: row[index] for row in table["rows"] if row[index] is not None}
        assert actual == (results[name]["values"] if results[name] else {})
    assert table["rows"][0][1:] == [None] * 13
    assert table["rows"][-1][1] == "1.2345678901234567890"
    assert table["rows"][-1][2] is None  #200 observations unavailable.
    assert material == before
    assert table["input_ohlcv_sha256"] == digest(packet["market_data"]["ohlcv"])
    assert "as_of" not in table  #Parent packet carries its actual capture clock.


@pytest.mark.parametrize("change", ["wrong_column", "missing_column", "numeric_value", "out_of_scope_value", "pre_warmup_value"])
def test_rejects_misassigned_or_changed_values(material, change):
    packet, results, formula = material
    item = results["close_50_sma"]
    if change == "wrong_column":
        item["indicator"] = "rsi"
    elif change == "missing_column":
        results.pop("rsi")
    elif change == "numeric_value":
        item["values"][next(iter(item["values"]))] = 1.2345
    elif change == "out_of_scope_value":
        item["values"]["2026-03-02"] = "1.2345"
    else:
        item["values"][packet["market_data"]["ohlcv"][0]["Date"]] = "1.2345"
    with pytest.raises(ValueError):
        build_indicator_table(packet, results, formula)


@pytest.mark.parametrize("change", ["duplicate_date", "future_date", "unordered_date", "outside400", "undated_row"])
def test_rejects_ambiguous_or_outside_history(material, change):
    packet, results, formula = material
    rows = packet["market_data"]["ohlcv"]
    if change == "duplicate_date":
        rows[1]["Date"] = rows[0]["Date"]
    elif change == "future_date":
        rows[-1]["Date"] = "2026-03-02"
    elif change == "unordered_date":
        rows[0], rows[1] = rows[1], rows[0]
    elif change == "outside400":
        rows[0]["Date"] = "2024-01-01"
    else:
        rows[0].pop("Date")
    with pytest.raises(ValueError):
        build_indicator_table(packet, results, formula)


@pytest.mark.parametrize("change", ["ohlcv", "provenance", "initialization", "minimum"])
def test_rejects_calculations_bound_to_other_inputs(material, change):
    packet, results, formula = material
    if change == "ohlcv":
        packet["market_data"]["ohlcv"][-1]["Close"] = 9.0
    elif change == "provenance":
        packet["market_data"]["history_provenance"]["source"] = "another_source"
    elif change == "initialization":
        results["rsi"]["initialization_rows"] -= 1
    else:
        results["rsi"]["minimum_observations"] -= 1
    with pytest.raises(ValueError):
        build_indicator_table(packet, results, formula)


def test_empty_frozen_history_stays_unknown():
    packet = {"trade_date": "2026-10-21", "market_data": {"ohlcv": []}}
    table = build_indicator_table(packet, dict.fromkeys(INDICATORS), {"entry": "not run"})
    assert table["rows"] == []
    assert table["actual_first_session"] is None
    assert all(item["available"] is False for item in table["indicators"].values())


@pytest.fixture
def mixed_latest(material):
    """Public numeric confusion reproduced; other values remain synthetic."""
    packet, results, formula = material
    rows = packet["market_data"]["ohlcv"]
    previous_days = [row["Date"] for row in rows]
    first = date(2026, 10, 20) - timedelta(days=len(rows) - 1)
    for i, row in enumerate(rows):
        row["Date"] = (first + timedelta(days=i)).isoformat()
    rows[-2]["Close"], rows[-1]["Close"] = 34.46, 36.30
    packet["trade_date"] = "2026-10-21"  #Observed history still ends October20.
    packet["as_of"] = "2026-10-21T00:53:49+08:00"
    moved = dict(zip(previous_days, [row["Date"] for row in rows]))
    for result in results.values():
        if result is not None:
            result["values"] = {moved[key]: value for key, value in result["values"].items()}
            result["frozen_ohlcv_sha256"] = digest(rows)
    results["close_50_sma"]["values"][rows[-2]["Date"]] = "37.8326"
    results["close_50_sma"]["values"][rows[-1]["Date"]] = "37.6382"
    return packet, results, formula


def test_named_latest_keeps_observed_close_and_current_average_separate(mixed_latest):
    packet, results, formula = mixed_latest
    table = build_indicator_table(packet, results, formula)
    latest = table["latest_session_projection"]
    assert latest["session"] == "2026-10-20"
    assert latest["frozen_history_close"] == 36.30
    assert latest["indicators"]["close_50_sma"] == "37.6382"
    assert latest["indicators"]["close_50_sma"] != "37.8326"
    assert latest["indicators"]["close_200_sma"] is None
    #History and its exact rendered strings remain available without clipping.
    assert table["rows"][-2][1] == "37.8326"
    assert table["rows"][-1][1] == "37.6382"
    assert len(table["rows"]) == len(packet["market_data"]["ohlcv"])


def test_latest_indicator_unknown_is_not_filled_from_an_earlier_day(mixed_latest):
    packet, results, formula = mixed_latest
    results["rsi"]["values"].pop(packet["market_data"]["ohlcv"][-1]["Date"])
    latest = build_indicator_table(packet, results, formula)["latest_session_projection"]
    assert latest["indicators"]["rsi"] is None
    assert latest["session"] == "2026-10-20"
    assert latest["frozen_history_close"] == 36.30


def test_no_history_has_no_synthetic_current_session_or_close():
    packet = {"trade_date": "2026-10-21", "market_data": {"ohlcv": []}}
    latest = build_indicator_table(packet, dict.fromkeys(INDICATORS), {"entry": "not run"})["latest_session_projection"]
    assert latest["session"] is None
    assert latest["frozen_history_close"] is None
    assert set(latest["indicators"]) == set(INDICATORS)
    assert all(value is None for value in latest["indicators"].values())
