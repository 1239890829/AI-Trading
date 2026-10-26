"""Offline study uses the existing mature-return statistics and never writes its source."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

import duckdb
import pytest

from scripts import verify_reference_strategies as verification


def source(tmp_path, *, days=90, trigger=89, missing=None):
    path = tmp_path / "source.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE daily_k(thscode VARCHAR,date_ms BIGINT,volume DOUBLE)")
    con.execute("CREATE TABLE daily_k_adj(thscode VARCHAR,date_ms BIGINT,close_adj DOUBLE)")
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for code in ("000001.SZ", "600000.SH", "000001.SH"):
        for i in range(days):
            if code == "000001.SZ" and i == missing:
                continue
            stamp = int((start + timedelta(days=i)).timestamp() * 1000)
            close = 110 if code == "000001.SZ" and i == trigger else 100
            rows.append((code, stamp, close, 300 if close == 110 else 100))
    con.executemany("INSERT INTO daily_k VALUES (?,?,?)", [(c, d, v) for c, d, _p, v in rows])
    con.executemany("INSERT INTO daily_k_adj VALUES (?,?,?)", [(c, d, p) for c, d, p, _v in rows])
    con.close()
    return path, start


def test_mature_statistics_do_not_count_pending_as_zero_return_or_loss():
    counts = {key: {"matched": 2} for key in verification.RULES}
    events = [(key, 1, 1.0, 0.0) for key in counts] + [(key, 2, None, None) for key in counts]
    result = verification._summaries(events, counts)
    for row in result.values():
        assert row["metrics"]["n"] == 1
        assert row["signal_count"] == 2
        assert row["metrics"]["pending"] == 1
        assert row["metrics"]["win_rate"] == 1
        assert row["production_promotion_eligible"] is False


def test_real_source_shape_is_read_only_and_retains_all_three_attempts(tmp_path):
    path, start = source(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    through = (start + timedelta(days=89)).date().isoformat()
    report = verification.run_verification(path, through=through, window_sessions=10)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert report["trials_total"] == 3 and set(report["trial_family"]) == set(verification.RULES)
    assert report["source_symbols"] == 2 and report["source_quality"]["state"] == "unknown"
    for row in report["results"].values():
        assert row["states"]["matched"] == 1 and row["metrics"]["n"] == 0
        assert row["metrics"]["win_rate"] is None and row["metrics"]["pending"] == 1
    assert report["production_promotion_eligible"] is False
    json.dumps(report, allow_nan=False)


def test_later_database_rows_do_not_leak_into_historical_cutoff(tmp_path):
    path, start = source(tmp_path)
    through = (start + timedelta(days=84)).date().isoformat()
    report = verification.run_verification(path, through=through, window_sessions=10)
    assert report["requested_through"] == through
    assert all(row["states"].get("matched", 0) == 0 for row in report["results"].values())
    assert all(row["metrics"]["win_rate"] is None for row in report["results"].values())


def test_missing_exact_fifth_market_session_does_not_shift_exit_to_later_bar(tmp_path):
    path, start = source(tmp_path, trigger=74, missing=79)
    through = (start + timedelta(days=89)).date().isoformat()
    report = verification.run_verification(path, through=through, window_sessions=16)
    for row in report["results"].values():
        assert row["states"]["matched"] == 1
        assert row["metrics"]["pending"] == 1 and row["metrics"]["n"] == 0
    assert all(row.get("missing_market_sessions", 0) > 0 for row in report["unknown_reasons"].values())


def test_known_source_adjustment_issue_is_quarantined_and_not_an_entry(tmp_path):
    path, start = source(tmp_path)
    quality = tmp_path / "quality_report.json"
    quality.write_text(json.dumps({"status": "warn", "synced_at": "2025-04-01T16:00:00+08:00", "issues": [
        {"check": "daily_k_adj.single_day_jump", "sample": [["000001.SZ", int((start + timedelta(days=70)).timestamp() * 1000), -37]]}]}))
    report = verification.run_verification(path, through=(start + timedelta(days=89)).date().isoformat(), window_sessions=10)
    assert report["source_quality"]["known_issues"] == 1
    for key, row in report["results"].items():
        assert row["states"]["unknown"] == 10 and row["states"].get("matched", 0) == 0
        assert report["unknown_reasons"][key]["source_quality_issue"] == 10


def test_a_missing_requested_source_day_never_falls_back(tmp_path):
    path, _start = source(tmp_path)
    with pytest.raises(ValueError, match="no fallback"):
        verification.run_verification(path, through="2025-12-31")


def test_bad_forward_endpoint_is_not_mature_and_cannot_pollute_market_reference(tmp_path):
    path, start = source(tmp_path, trigger=74)
    entry = int((start + timedelta(days=74)).timestamp() * 1000)
    exit_at = int((start + timedelta(days=79)).timestamp() * 1000)
    with duckdb.connect(str(path)) as con:
        con.execute("UPDATE daily_k_adj SET close_adj=500 WHERE thscode='000001.SZ' AND date_ms=?", [exit_at])
        con.execute("UPDATE daily_k_adj SET close_adj=110 WHERE thscode='600000.SH' AND date_ms=?", [entry])
        con.execute("UPDATE daily_k SET volume=300 WHERE thscode='600000.SH' AND date_ms=?", [entry])
        con.execute("UPDATE daily_k_adj SET close_adj=120 WHERE thscode='600000.SH' AND date_ms=?", [exit_at])
    quality = tmp_path / "quality_report.json"
    quality.write_text(json.dumps({"status": "warn", "issues": [
        {"check": "daily_k_adj.single_day_jump", "sample": [["000001.SZ", exit_at, 354]]}]}))
    report = verification.run_verification(path, through=(start + timedelta(days=79)).date().isoformat(), window_sessions=6)
    for value in report["results"].values():
        metrics = value["metrics"]
        assert metrics["n"] == 1 and metrics["pending"] >= 1
        # Only the good stock's exact-date return belongs to the market mean.
        assert metrics["mean"] == pytest.approx((120 / 110 - 1) * 100 - .35, abs=.0001)
        assert metrics["excess"] == pytest.approx(-.35, abs=.0001)
    assert report["source_symbols"] == 2, "the index fixture must not enter signals or the baseline"
