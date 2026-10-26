"""Frozen manual rules have no score, execution or future-data consumer."""
from copy import deepcopy
from datetime import date, timedelta
import json
import math

import pytest

from app.picks._strategy_reference_rules import (
    DEFINITION_DIGEST, RULES, evaluate_reference_rules, iter_reference_rules,
    reference_rule_metadata, is_reference_stock_code,
)


def bars(n=60, *, last=110, volume=300):
    start = date(2025, 1, 1)
    return [{"date": (start + timedelta(days=i)).isoformat(),
             "close": last if i == n - 1 else 100.0,
             "volume": volume if i == n - 1 else 100.0} for i in range(n)]


def evaluate(rows, **kwargs):
    return evaluate_reference_rules(rows, through=rows[-1]["date"], **kwargs)


def test_three_explicit_conditions_and_previous_five_volume_denominator():
    result = evaluate(bars())
    assert set(result) == set(RULES)
    assert all(value["state"] == "matched" for value in result.values())
    for value in result.values():
        assert value["definition_digest"] == DEFINITION_DIGEST
        assert value["score_contribution"] is False and value["execution_eligible"] is False
        assert value["input_rows"] == 60 and all(c["met"] is True for c in value["conditions"])
    metrics = result["ma_golden_cross"]["metrics"]
    assert metrics["volume_ma5_previous"] == 100 and metrics["volume_ratio5"] == 3
    assert metrics["ma5"] == 102 and metrics["ma10"] == 101 and metrics["ma20"] == 100.5


def test_non_crossing_and_equal_volume_are_not_passing():
    assert all(value["state"] == "not_matched" for value in evaluate(bars(last=100)).values())
    result = evaluate(bars(volume=100))
    assert result["ma_golden_cross"]["state"] == "not_matched"
    assert result["macd_golden_cross"]["state"] == "matched"
    assert result["ma_bullish"]["state"] == "matched"


def test_macd_uses_existing_ema_seed_and_periods():
    from app.market.tech_score import ema

    rows = bars(90)
    for i, row in enumerate(rows):
        row["close"] = 100 + math.sin(i / 7) * 4
    closes = [row["close"] for row in rows]
    dif = [a - b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    dea = ema(dif, 9)
    result = evaluate(rows)["macd_golden_cross"]
    assert result["metrics"]["dif"] == pytest.approx(dif[-1])
    assert result["metrics"]["dea"] == pytest.approx(dea[-1])
    assert (result["state"] == "matched") == (dif[-2] <= dea[-2] and dif[-1] > dea[-1])


def test_future_append_does_not_change_any_requested_prefix_or_mutate_input():
    rows = bars()
    before = deepcopy(rows)
    expected = evaluate(rows)
    future = [{"date": "2099-01-01", "close": float("nan"), "volume": -1, "quality_issue": "future_only"}]
    actual = evaluate_reference_rules(rows + future, through=rows[-1]["date"])
    assert actual == expected and rows == before
    prefix = list(iter_reference_rules(rows + future, through=rows[-1]["date"], since=rows[-1]["date"]))
    assert prefix == [(rows[-1]["date"], expected)]


def test_scaling_prices_or_volume_does_not_change_rule_decisions():
    original = evaluate(bars())
    changed = bars()
    for row in changed:
        row["close"] *= 2
        row["volume"] *= 10
    scaled = evaluate(changed)
    assert {k: v["state"] for k, v in original.items()} == {k: v["state"] for k, v in scaled.items()}
    assert scaled["ma_golden_cross"]["metrics"]["volume_ratio5"] == 3
    assert scaled["ma_golden_cross"]["input_digest"] != original["ma_golden_cross"]["input_digest"]


@pytest.mark.parametrize("field,value,reason", [
    ("close", None, "invalid_or_missing_close"), ("close", 0, "invalid_or_missing_close"),
    ("close", -1, "invalid_or_missing_close"), ("close", True, "invalid_or_missing_close"),
    ("close", float("nan"), "invalid_or_missing_close"), ("close", float("inf"), "invalid_or_missing_close"),
    ("volume", None, "invalid_or_missing_volume"),
    ("volume", float("nan"), "invalid_or_missing_volume"),
    ("suspended", True, "suspended"), ("suspended", 0, "invalid_suspension_flag"),
    ("quality_issue", "adjusted_price_discontinuity", "source_quality_issue"),
])
def test_bad_or_suspended_inputs_are_unknown_not_zero_or_false(field, value, reason):
    rows = bars(); rows[-1][field] = value
    keys = ["ma_golden_cross"] if field == "volume" else list(RULES)
    for key in keys:
        verdict = evaluate(rows)[key]
        assert verdict["state"] == "unknown" and reason in verdict["reason_codes"]
        assert verdict["conditions"][0]["met"] is None


def test_invalid_and_duplicate_or_unordered_dates_are_named_unknown():
    for bad in ("2025-02-30", None, "20250101"):
        rows = bars(); rows.insert(10, {**rows[10], "date": bad})
        assert "invalid_date" in evaluate(rows)["ma_bullish"]["reason_codes"]
    rows = bars(); rows.insert(10, deepcopy(rows[10]))
    assert "duplicate_date" in evaluate(rows)["ma_bullish"]["reason_codes"]
    rows = bars(); rows[10], rows[11] = rows[11], rows[10]
    assert "dates_not_ascending" in evaluate(rows)["ma_bullish"]["reason_codes"]


def test_missing_requested_day_and_sessions_are_not_filled_or_guessed():
    rows = bars(80)
    assert "missing_requested_session" in evaluate_reference_rules(rows, through="2025-12-31")["ma_bullish"]["reason_codes"]
    sessions = [row["date"] for row in rows]
    del rows[70]
    assert "missing_market_sessions" in evaluate(rows, expected_sessions=sessions)["ma_bullish"]["reason_codes"]
    assert "requested_session_not_in_calendar" in evaluate(bars(), expected_sessions=[])["ma_bullish"]["reason_codes"]
    assert all(value["state"] == "unknown" for value in evaluate(bars(59)).values())
    assert all(value["state"] == "unknown" for value in evaluate_reference_rules([], through="2025-01-01").values())


def test_derived_overflow_cannot_escape_as_json_nan_or_infinity():
    rows = bars()
    for row in rows[:-1]:
        row["volume"] = 1e-308
    rows[-1]["volume"] = 1e308
    result = evaluate(rows)
    assert result["ma_golden_cross"]["state"] == "unknown"
    assert "non_finite_feature" in result["ma_golden_cross"]["reason_codes"]
    assert result["macd_golden_cross"]["state"] == result["ma_bullish"]["state"] == "matched"
    json.dumps(result, allow_nan=False)


def test_metadata_is_a_copy_and_bad_calendar_is_rejected():
    metadata = reference_rule_metadata(); metadata.clear()
    assert set(reference_rule_metadata()) == set(RULES)
    assert "5日分母" in RULES["ma_golden_cross"]["supplement"]
    with pytest.raises(ValueError):
        evaluate_reference_rules(bars(), through="invalid")
    with pytest.raises(ValueError):
        evaluate(bars(), expected_sessions=["2025-01-02", "2025-01-01"])


@pytest.mark.parametrize("code", ["000001.SZ", "300001.SZ", "600000.SH", "688001.SH", "920001.BJ", "430001.BJ"])
def test_explicit_shared_stock_scope_accepts_real_code_shapes(code):
    assert is_reference_stock_code(code)


@pytest.mark.parametrize("code", ["000001.SH", "399001.SZ", "510300.SH", "159001.SZ", "A.SH", None, "600000.SH-extra"])
def test_explicit_shared_stock_scope_rejects_indexes_funds_and_bad_shapes(code):
    assert not is_reference_stock_code(code)


@pytest.mark.parametrize("volume", [0, None, float("nan")])
def test_old_volume_does_not_poison_unrelated_price_or_current_volume_features(volume):
    rows = bars(90); rows[3]["volume"] = volume
    result = evaluate(rows)
    assert all(value["state"] == "matched" for value in result.values())
    assert all(value["data_warnings"] for value in result.values())


def test_current_volume_and_zero_denominator_have_only_their_real_dependencies():
    rows = bars(volume=0)
    result = evaluate(rows)
    assert result["macd_golden_cross"]["state"] == result["ma_bullish"]["state"] == "matched"
    assert result["ma_golden_cross"]["state"] == "not_matched"
    assert result["ma_golden_cross"]["metrics"]["volume_ratio5"] == 0
    assert "zero_volume_not_execution_evidence" in result["ma_golden_cross"]["data_warnings"]
    rows = bars()
    for row in rows[-6:-1]:
        row["volume"] = 0
    result = evaluate(rows)
    assert "volume_baseline_zero" in result["ma_golden_cross"]["reason_codes"]
    assert result["ma_bullish"]["state"] == "matched"


def test_price_prefix_gap_still_blocks_ema_but_finite_ma_uses_its_own_window():
    rows = bars(90); rows[3]["close"] = None
    result = evaluate(rows)
    assert result["macd_golden_cross"]["state"] == "unknown"
    assert result["ma_bullish"]["state"] == result["ma_golden_cross"]["state"] == "matched"
    assert "price_prefix_contains_missing_values" in result["ma_bullish"]["data_warnings"]
    rows = bars(); rows[-21]["close"] = None
    result = evaluate(rows)
    assert result["ma_bullish"]["state"] == "matched"
    assert result["ma_golden_cross"]["state"] == result["macd_golden_cross"]["state"] == "unknown"
    rows[-20]["close"] = None
    assert evaluate(rows)["ma_bullish"]["state"] == "unknown"


def test_explicit_quality_and_current_suspension_are_never_washed_green():
    rows = bars(90); rows[0]["quality_issue"] = "known_adjustment_discontinuity"
    assert all(value["state"] == "unknown" and "source_quality_issue" in value["reason_codes"] for value in evaluate(rows).values())
    rows = bars(90); rows[-1]["suspended"] = True
    assert all(value["state"] == "unknown" and "suspended" in value["reason_codes"] for value in evaluate(rows).values())
