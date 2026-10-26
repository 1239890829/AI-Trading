"""Closed-session observations remain historical facts, never fresh live quotes."""
from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest

from app.core.bjtime import BJ_TZ
from app.review.provenance import quote_evidence


DAY = date(2026, 10, 26)
NOW = datetime(2026, 10, 26, 18, 45, tzinfo=BJ_TZ)


def closed_quote():
    return {
        "source": "tencent", "quality": "stale", "quality_reasons": ["market_closed"],
        "data_timestamp": "2026-10-26T16:14:00+08:00", "received_at": NOW.isoformat(),
        "display_session": {
            "phase": "closed", "trade_date": DAY.isoformat(), "trade_day_result": True,
            "as_of": NOW.isoformat(), "expected_source_date": DAY.isoformat(),
            "expected_source_after": "2026-10-26T14:59:50+08:00",
        },
    }


def test_closed_source_observation_requires_explicit_report_opt_in():
    value = closed_quote()
    before = deepcopy(value)
    assert quote_evidence(value, DAY, NOW).state == "unverified"
    actual = quote_evidence(value, DAY, NOW, allow_closed_observation=True)
    assert actual.state == "partial"
    assert actual.source_as_of == "2026-10-26T16:14:00+08:00"
    assert actual.source_trade_date == DAY.isoformat()
    assert "闭市" in actual.reason and "实时" in actual.reason and "收盘价" in actual.reason
    assert value == before and value["quality"] == "stale"


@pytest.mark.parametrize("reasons", [[], ["off_session"], ["quote_age_exceeded"],
    ["market_closed", "refresh_failed"], ["market_closed", "index_batch_missing"],
    ["market_closed", "source_quality_low"], "market_closed", None])
def test_any_unexplained_or_failed_quality_stays_rejected(reasons):
    value = closed_quote()
    value["quality_reasons"] = reasons
    assert quote_evidence(value, DAY, NOW, allow_closed_observation=True).state == "unverified"


@pytest.mark.parametrize("change", [
    {"phase": "continuous"}, {"phase": "auction"}, {"trade_day_result": None},
    {"trade_day_result": False}, {"trade_date": "2026-10-23"},
    {"expected_source_date": "2026-10-23"}, {"expected_source_after": None},
    {"expected_source_after": "2026-10-26T17:00:00+08:00"},
    {"expected_source_after": "2026-10-23T14:59:50+08:00"},
    {"as_of": "2026-10-26T16:00:00+08:00"}, {"as_of": "2026-10-26T19:00:00+08:00"},
    {"as_of": "2026-10-26T18:45:00"},
])
def test_closed_calendar_or_tail_evidence_cannot_be_guessed(change):
    value = closed_quote()
    value["display_session"].update(change)
    assert quote_evidence(value, DAY, NOW, allow_closed_observation=True).state == "unverified"


@pytest.mark.parametrize("session", [None, {}, "closed"])
def test_absent_or_malformed_session_is_not_a_closed_observation(session):
    value = closed_quote()
    value["display_session"] = session
    assert quote_evidence(value, DAY, NOW, allow_closed_observation=True).state == "unverified"


def test_lunch_or_early_stopped_quote_does_not_become_a_closing_fact():
    value = closed_quote()
    value["data_timestamp"] = "2026-10-26T11:30:00+08:00"
    value["display_session"]["expected_source_after"] = "2026-10-26T11:29:50+08:00"
    assert quote_evidence(value, DAY, NOW, allow_closed_observation=True).state == "unverified"
    lunch = NOW.replace(hour=12)
    value["display_session"]["as_of"] = lunch.isoformat()
    assert quote_evidence(value, DAY, lunch, allow_closed_observation=True).state == "unverified"


def test_other_day_unknown_or_future_clock_still_fail_closed():
    value = closed_quote()
    assert quote_evidence(value, DAY - timedelta(days=3), NOW,
                          allow_closed_observation=True).state == "mismatch"
    value["data_timestamp"] = None
    assert quote_evidence(value, DAY, NOW, allow_closed_observation=True).state == "unverified"
    value["data_timestamp"] = (NOW + timedelta(minutes=1)).isoformat()
    assert quote_evidence(value, DAY, NOW, allow_closed_observation=True).state == "mismatch"


def test_native_quote_session_model_is_read_without_mutation():
    from app.schemas.market import Quote

    raw = closed_quote()
    value = Quote(symbol="000001", market="SH", price=3000, change_pct=0, **raw)
    before = value.model_dump()
    assert quote_evidence(value, DAY, NOW, allow_closed_observation=True).state == "partial"
    assert value.model_dump() == before
