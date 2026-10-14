"""Health explains expected pauses without changing raw freshness or business data."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.routes.health import health
from app.schemas.market import Quality, Quote, QuoteDisplaySession


def context(*, closed=True):
    now = datetime.fromisoformat("2026-10-14T19:35:00+08:00" if closed else "2026-10-14T10:15:00+08:00")
    session = QuoteDisplaySession(phase="closed" if closed else "continuous", trade_date=now.date(),
                                  as_of=now, trade_day_result=True).model_copy(update={
                                      "expected_source_date": now.date(),
                                      "expected_source_after": now.replace(hour=14, minute=59, second=50) if closed else None,
                                  })
    stamp = now.replace(hour=15, minute=0) if closed else now - timedelta(seconds=1)
    quote = Quote(symbol="600519", market="SH", price=100, prev_close=100, source="offline-health",
                  data_timestamp=stamp, received_at=now,
                  quality=Quality.stale if closed else Quality.high,
                  quality_reasons=["market_closed"] if closed else [])
    index = quote.model_copy(deep=True, update={"symbol": "000001"})
    domains = {name: {
        "requested": True, "in_flight": False, "last_attempt": now.isoformat(),
        "last_success_refresh": now.isoformat(), "last_error": None, "last_error_type": None,
        "consecutive_failures": 0, "freshness": {"state": "ready", "as_of": now.isoformat()},
        "source_oldest_as_of": stamp.isoformat(), "source_stale_count": int(closed),
        "source_degraded_count": 0, "source_time_unknown_count": 0,
        "missing_count": 0, "rejected_count": 0, "coverage": 1.0,
    } for name in ("indices", "quotes")}
    snapshot = {"freshness": {"state": "ready", "as_of": now.isoformat()},
                "consecutive_failures": 0, "last_error": None, "consecutive_save_failures": 0,
                "last_save_error": None, "snapshot_source": "sina", "rows": 5500}
    hub = SimpleNamespace(
        provider=SimpleNamespace(name="offline-health"), poll_interval=1, stale_after=10,
        last_success_refresh=now, last_attempt=now, consecutive_failures=0, last_error=None,
        quotes={quote.symbol: quote}, indices={index.symbol: index},
        _domains={"indices": SimpleNamespace(requested_symbols=(index.symbol,)),
                  "quotes": SimpleNamespace(requested_symbols=(quote.symbol,))},
        is_stale=lambda: closed, index_batch=lambda: {"expected_count": 1, "coverage": 1.0, "missing_symbols": []},
        source_rejections=lambda: {"quotes": {"count": 0}, "indices": {"count": 0}},
        refresh_domains=lambda: deepcopy(domains), _display_session=lambda: session,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        snapshot_service=SimpleNamespace(breadth_payload=lambda: deepcopy(snapshot)))))
    return hub, request, session, domains, snapshot


def read(hub, request):
    return asyncio.run(health(request, hub))


def codes(assessment):
    return {issue["code"] for issue in assessment["reasons"]}


def test_closed_source_age_explained_without_upgrading_raw_stale_or_mutating_quotes():
    hub, request, _session, domains, snapshot = context()
    before = {key: q.model_dump(mode="json") for key, q in hub.quotes.items()}
    body = read(hub, request)
    assert body["status"] == "degraded" and body["is_stale"] is True
    assert body["assessment"]["state"] == "expected_pause"
    assert body["assessment"]["raw_stale_preserved"] is True
    assert body["assessment"]["read_completion_is_source_time"] is False
    assert body["assessment"]["pause_scope"] == "requested_quote_domains"
    assert body["assessment"]["snapshot_source_clock"] == "unverified"
    assert body["refresh_domains"] == domains and body["market_snapshot"] == snapshot
    assert {key: q.model_dump(mode="json") for key, q in hub.quotes.items()} == before


def test_live_current_source_is_ready_and_does_not_change_raw_status():
    hub, request, *_ = context(closed=False)
    body = read(hub, request)
    assert body["status"] == "ok" and body["is_stale"] is False
    assert body["assessment"]["state"] == "ready"


@pytest.mark.parametrize("change,expected", [
    ({"consecutive_failures": 1, "last_error": "provider offline"}, "refresh_failed"),
    ({"missing_count": 1, "coverage": 0.5}, "batch_incomplete"),
    ({"rejected_count": 1}, "source_rejected"),
    ({"source_degraded_count": 1}, "source_degraded"),
])
def test_actual_domain_failure_outranks_confirmed_closed(change, expected):
    hub, request, _session, domains, _snapshot = context()
    domains["quotes"].update(change)
    assessment = read(hub, request)["assessment"]
    assert assessment["state"] == "fault"
    assert expected in codes(assessment)


@pytest.mark.parametrize("reason", ["refresh_failed", "batch_missing", "source_time_regress_ignored",
                                    "source_invalid_ignored", "source_identity_duplicate_ignored"])
def test_closed_label_cannot_cover_cache_rejection_even_when_summary_is_inconsistent(reason):
    hub, request, *_ = context()
    hub.quotes["600519"].quality_reasons = [reason]
    assessment = read(hub, request)["assessment"]
    assert assessment["state"] == "fault"
    assert "source_quality_issue" in codes(assessment)


@pytest.mark.parametrize("quality", [Quality.low, Quality.medium, Quality.invalid])
def test_low_or_invalid_source_never_qualifies_as_expected_pause(quality):
    hub, request, *_ = context()
    hub.quotes["600519"].quality = quality
    assert read(hub, request)["assessment"]["state"] == "fault"


@pytest.mark.parametrize("defect", ["missing_source_time", "future_source_time", "unknown_source_date",
                                    "old_source_date", "unknown_calendar", "contradictory_phase"])
def test_insufficient_or_contradictory_closed_evidence_is_unknown(defect):
    hub, request, session, _domains, _snapshot = context()
    if defect == "missing_source_time":
        hub.quotes["600519"].data_timestamp = None
    elif defect == "future_source_time":
        hub.quotes["600519"].data_timestamp = session.as_of + timedelta(seconds=1)
    elif defect == "unknown_source_date":
        hub._display_session = lambda: session.model_copy(update={"expected_source_date": None})
    elif defect == "old_source_date":
        hub.quotes["600519"].data_timestamp -= timedelta(days=1)
    elif defect == "unknown_calendar":
        hub._display_session = lambda: session.model_copy(update={"phase": "unknown", "trade_day_result": None})
    else:
        hub._display_session = lambda: session.model_copy(update={"as_of": session.as_of.replace(hour=10)})
    assert read(hub, request)["assessment"]["state"] == "unknown"


def test_no_first_success_is_unknown_not_an_invented_timeout():
    hub, request, _session, domains, _snapshot = context()
    domains["quotes"].update(last_success_refresh=None, last_attempt=None, freshness={"state": "unavailable"})
    hub.quotes.clear()
    assessment = read(hub, request)["assessment"]
    assert assessment["state"] == "unknown"
    assert "read_unavailable" in codes(assessment)
    assert "refresh_failed" not in codes(assessment)


def test_completion_clock_stale_is_unknown_without_proof_of_a_request_failure():
    hub, request, _session, domains, _snapshot = context()
    domains["indices"]["freshness"] = {"state": "stale"}
    assessment = read(hub, request)["assessment"]
    assert assessment["state"] == "unknown"
    assert "read_completion_stale" in codes(assessment)
    assert "refresh_failed" not in codes(assessment)


def test_unknown_source_clock_remains_unknown_when_age_summary_is_degraded():
    hub, request, _session, domains, _snapshot = context()
    quote = hub.quotes["600519"]
    quote.data_timestamp = None
    quote.quality_reasons = ["source_time_unknown"]
    domains["quotes"].update(source_time_unknown_count=1, source_degraded_count=1)
    assessment = read(hub, request)["assessment"]
    assert assessment["state"] == "unknown"
    assert "source_time_unknown" in codes(assessment)


def test_stale_read_and_real_failure_can_coexist_without_hiding_the_failure():
    hub, request, _session, domains, _snapshot = context()
    domains["quotes"].update(consecutive_failures=1, last_error="read failed", freshness={"state": "stale"})
    assessment = read(hub, request)["assessment"]
    assert assessment["state"] == "fault"
    assert {"refresh_failed", "read_completion_stale"} <= codes(assessment)


def test_live_source_stale_is_a_data_problem_even_if_current_read_completed():
    hub, request, session, domains, _snapshot = context(closed=False)
    hub.quotes["600519"].data_timestamp = session.as_of - timedelta(seconds=11)
    domains["quotes"].update(source_stale_count=1)
    assessment = read(hub, request)["assessment"]
    assert assessment["state"] == "fault"
    assert "source_quote_stale" in codes(assessment)


@pytest.mark.parametrize("phase,minute", [("auction", 20), ("pre_open_gap", 27)])
def test_index_waiting_before_continuous_session_is_unknown_without_a_proven_failure(phase, minute):
    hub, request, session, domains, snapshot = context()
    now = datetime.fromisoformat(f"2026-10-14T09:{minute}:00+08:00")
    hub._display_session = lambda: session.model_copy(update={
        "as_of": now, "phase": phase, "expected_source_after": None,
    })
    for quote in [*hub.quotes.values(), *hub.indices.values()]:
        quote.data_timestamp = now.replace(minute=15)
        quote.quality_reasons = ["quote_age_exceeded"]
    for observed in domains.values():
        observed.update(last_attempt=now.isoformat(), last_success_refresh=now.isoformat(),
                        freshness={"state": "ready", "as_of": now.isoformat()})
    snapshot["freshness"] = {"state": "ready", "as_of": now.isoformat()}
    assessment = read(hub, request)["assessment"]
    assert assessment["state"] == "unknown"
    assert "awaiting_observation" in codes(assessment)
    assert "source_quote_stale" not in codes(assessment)
    domains["indices"].update(consecutive_failures=1, last_error="index request failed")
    assert read(hub, request)["assessment"]["state"] == "fault"


def test_missing_cached_quote_cannot_become_normal_pause_when_summary_claims_complete():
    hub, request, *_ = context()
    hub.quotes.clear()
    assessment = read(hub, request)["assessment"]
    assert assessment["state"] == "fault"
    assert "source_value_missing" in codes(assessment)


@pytest.mark.parametrize("minute", ["09:30:00", "14:59:49"])
def test_same_day_but_missing_close_tail_is_unknown_even_without_request_failure(minute):
    hub, request, *_ = context()
    hub.quotes["600519"].data_timestamp = datetime.fromisoformat(f"2026-10-14T{minute}+08:00")
    assessment = read(hub, request)["assessment"]
    assert assessment["state"] == "unknown"
    assert "source_tail_unconfirmed" in codes(assessment)
    assert "refresh_failed" not in codes(assessment)


@pytest.mark.parametrize("tail", [None, "2026-10-14T14:59:50", "2026-10-14T20:00:00+08:00"])
def test_missing_naive_or_future_pause_tail_cannot_authorize_normal_pause(tail):
    hub, request, session, *_ = context()
    hub._display_session = lambda: session.model_copy(update={
        "expected_source_after": datetime.fromisoformat(tail) if tail else None,
    })
    assessment = read(hub, request)["assessment"]
    assert assessment["state"] == "unknown"
    assert "source_tail_unconfirmed" in codes(assessment)


def test_exact_existing_freshness_boundary_is_not_replaced_with_an_extra_window():
    hub, request, session, *_ = context()
    hub.quotes["600519"].data_timestamp = session.expected_source_after
    assert read(hub, request)["assessment"]["state"] == "expected_pause"


def test_midday_old_quote_is_not_called_normal_lunch_or_a_confirmed_fault():
    hub, request, session, domains, snapshot = context()
    now = datetime.fromisoformat("2026-10-14T12:00:00+08:00")
    hub._display_session = lambda: session.model_copy(update={
        "as_of": now, "expected_source_after": now.replace(hour=11, minute=29, second=50),
    })
    for quote in [*hub.quotes.values(), *hub.indices.values()]:
        quote.data_timestamp = now.replace(hour=11, minute=30)
    hub.quotes["600519"].data_timestamp = now.replace(hour=10)
    for observed in domains.values():
        observed.update(last_attempt=now.isoformat(), last_success_refresh=now.isoformat(),
                        freshness={"state": "ready", "as_of": now.isoformat()})
    snapshot["freshness"] = {"state": "ready", "as_of": now.isoformat()}
    assessment = read(hub, request)["assessment"]
    assert assessment["state"] == "unknown"
    assert "source_tail_unconfirmed" in codes(assessment)
    hub.quotes["600519"].data_timestamp = now.replace(hour=11, minute=30)
    assert read(hub, request)["assessment"]["state"] == "expected_pause"


@pytest.mark.parametrize("change", [
    {"consecutive_failures": 1, "last_error": "read offline"},
    {"consecutive_save_failures": 1, "last_save_error": "disk full"},
    {"freshness": {"state": "degraded"}, "snapshot_source": "quote_fallback"},
])
def test_snapshot_failure_is_not_hidden_by_expected_quote_pause(change):
    hub, request, _session, _domains, snapshot = context()
    snapshot.update(change)
    assessment = read(hub, request)["assessment"]
    assert assessment["state"] == "fault"


def test_unrequested_old_cache_does_not_create_a_fault():
    hub, request, _session, domains, _snapshot = context()
    hub.quotes["600036"] = hub.quotes["600519"].model_copy(deep=True, update={
        "symbol": "600036", "data_timestamp": datetime.fromisoformat("2020-01-01T15:00:00+08:00"),
        "quality": Quality.invalid,
    })
    assert read(hub, request)["assessment"]["state"] == "expected_pause"
    domains["quotes"]["requested"] = False
    hub._domains["quotes"].requested_symbols = ()
    assert read(hub, request)["assessment"]["state"] == "expected_pause"


def test_health_does_not_invoke_provider_or_visible_quote_reads():
    hub, request, *_ = context()
    def forbidden(*args, **kwargs):
        raise AssertionError("health must use already observed cache facts")
    hub.provider.get_quotes = forbidden
    hub.provider.get_indices = forbidden
    hub.get_quotes = forbidden
    hub.get_indices = forbidden
    hub.refresh = forbidden
    assert read(hub, request)["assessment"]["state"] == "expected_pause"


def test_expected_source_date_is_a_calendar_input_not_the_host_date():
    hub, request, session, domains, snapshot = context()
    friday = date(2026, 10, 16)
    saturday = datetime.fromisoformat("2026-10-17T12:00:00+08:00")
    hub._display_session = lambda: session.model_copy(update={
        "trade_date": saturday.date(), "as_of": saturday, "trade_day_result": False,
        "expected_source_date": friday,
        "expected_source_after": datetime.fromisoformat("2026-10-16T14:59:50+08:00"),
    })
    for quote in [*hub.quotes.values(), *hub.indices.values()]:
        quote.data_timestamp = datetime.fromisoformat("2026-10-16T15:00:00+08:00")
    for observed in domains.values():
        observed.update(last_attempt=saturday.isoformat(), last_success_refresh=saturday.isoformat(),
                        freshness={"state": "ready", "as_of": saturday.isoformat()})
    snapshot["freshness"] = {"state": "ready", "as_of": saturday.isoformat()}
    assert read(hub, request)["assessment"]["state"] == "expected_pause"
