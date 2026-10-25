"""Historical reference reads never backfill or rewrite a frozen review."""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from types import SimpleNamespace as NS

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core.bjtime import BJ_TZ
from app.review import metric_reference as reference
from app.sentiment import metric_history


NOW = datetime(2026, 10, 25, 16, tzinfo=BJ_TZ)


def row(day, **changes):
    return {"date": day, "limit_up": 3, "n_break": 1, "break_rate": 0.25,
            "max_board": 2, "promo_1to2": 0.5, "promo_2to3": None, **changes}


@pytest.fixture
def local_store(monkeypatch, tmp_path):
    path = tmp_path / "sentiment_metrics.json"
    monkeypatch.setattr(metric_history, "_STORE_PATH", path)
    monkeypatch.setattr(reference, "beijing_now", lambda: NOW)

    async def forbidden(*args, **kwargs):
        raise AssertionError("a review reference must not backfill or fetch")

    monkeypatch.setattr(metric_history, "backfill", forbidden)
    return path


def save(path, days, **extra):
    path.write_text(json.dumps({"updated_at": "2026-10-25T07:00:00Z", "days": days, **extra}))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_selected_window_excludes_newer_rows_and_keeps_original_file(local_store):
    digest = save(local_store, {"2026-09-22": row("2026-09-22"),
                               "2026-09-23": row("2026-09-23"),
                               "2026-10-21": row("2026-10-21"),
                               "2026-10-22": row("2026-10-22"),
                               "2026-10-23": row("2026-10-23", max_board=99)})
    result = reference.read_metric_history_reference(date(2026, 10, 22))
    assert result["window_start"] == "2026-09-23"
    assert result["window_end"] == "2026-10-22"
    assert [point["date"] for point in result["points"]] == ["2026-09-23", "2026-10-21", "2026-10-22"]
    assert result["coverage"]["recorded_days"] == 3
    assert len(result["unrecorded_dates"]) == 27
    assert result["source_as_of"] is None and result["raw_source_sha256"] is None
    assert result["available_at_original_report"] == "unknown"
    assert result["dataset_updated_at"] == "2026-10-25T15:00:00+08:00"
    assert hashlib.sha256(local_store.read_bytes()).hexdigest() == digest


def test_zero_is_a_recorded_rate_but_zero_denominator_is_unknown(local_store):
    save(local_store, {"2026-10-22": row("2026-10-22", limit_up=0, n_break=3,
                                            break_rate=1, max_board=0, promo_1to2=0, promo_2to3=0),
                       "2026-10-23": row("2026-10-23", limit_up=0, n_break=0, break_rate=None)})
    result = reference.read_metric_history_reference(date(2026, 10, 23))
    first, second = result["points"]
    assert first["seal_rate"] == first["promo_1to2"] == first["promo_2to3"] == first["max_board"] == 0
    assert first["seal_base"] == 3
    assert second["seal_rate"] is None and second["seal_base"] == 0
    assert first["promo_1to2_base"] is None and first["promo_2to3_base"] is None
    assert result["coverage"]["effective_days"]["seal_rate"] == 1


def test_bad_dates_fields_and_conflicting_denominator_do_not_become_valid_facts(local_store):
    save(local_store, {"2026-W43-4": row("2026-W43-4"), "not-a-day": {},
                       "2026-10-20": row("2026-10-21"), "2026-10-21": [],
                       "2026-10-22": row("2026-10-22", limit_up=True, n_break=-1,
                                            break_rate=float("nan"), max_board="5", promo_1to2=True, promo_2to3=1.2),
                       "2026-10-23": row("2026-10-23", break_rate=0.9)})
    result = reference.read_metric_history_reference(date(2026, 10, 23))
    assert result["invalid_rows"] == 4
    assert len(result["points"]) == 2
    broken, conflict = result["points"]
    assert all(broken[key] is None for key in ("limit_up", "n_break", "break_rate", "max_board", "promo_1to2", "promo_2to3", "seal_rate"))
    assert conflict["seal_rate"] is None
    assert "seal_rate:break_rate_missing_or_inconsistent" in conflict["missing"]


def test_future_rows_and_future_file_clock_are_not_current_evidence(local_store):
    save(local_store, {"2026-10-25": row("2026-10-25"), "2026-10-26": row("2026-10-26")},
         updated_at="2026-10-26T07:00:00Z")
    result = reference.read_metric_history_reference(date(2026, 11, 2))
    assert result["window_end"] == "2026-10-25"
    assert [point["date"] for point in result["points"]] == ["2026-10-25"]
    assert result["dataset_updated_at"] is None


@pytest.mark.parametrize("contents", [None, "not-json", '{"days":[]}'])
def test_absent_or_corrupt_store_stays_unavailable_without_creating_it(local_store, contents):
    if contents is not None:
        local_store.write_text(contents)
    result = reference.read_metric_history_reference(date(2026, 10, 23))
    assert result["state"] == "unavailable" and result["points"] == []
    assert local_store.exists() == (contents is not None)
    if contents is not None:
        assert local_store.read_text() == contents


def test_owner_read_exception_is_partial_failure_not_failed_original_report(monkeypatch, local_store):
    def fail():
        raise OSError("unreadable")
    monkeypatch.setattr(metric_history, "load", fail)
    result = reference.read_metric_history_reference(date(2026, 10, 23))
    assert result["state"] == "error" and result["points"] == []


def test_report_read_adds_reference_outside_frozen_data_only(monkeypatch, local_store):
    from app.api.routes import review

    save(local_store, {"2026-10-22": row("2026-10-22")})
    frozen = {"trade_date": "20261022", "summary": "原判断", "data": {"market": {"sentiment": {"phase": "原阶段"}}}}
    original = deepcopy(frozen)
    app = FastAPI()
    app.include_router(review.router, prefix="/api")
    app.state.review = NS(session_factory=object())
    monkeypatch.setattr(review, "get_report", lambda *args: NS(model_dump=lambda: deepcopy(frozen)))
    with TestClient(app) as client:
        response = client.get("/api/review/reports/20261022")
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["data"] == original["data"] and result["summary"] == "原判断"
    assert result["metric_history_reference"]["requested_trade_date"] == "2026-10-22"
    assert result["metric_history_reference"]["role"] == "read_time_aggregate_reference"
    assert frozen == original
