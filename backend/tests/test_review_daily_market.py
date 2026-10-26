"""Completed local daily bars can fill finite facts without live-data promotion."""
from datetime import date, datetime, timedelta
import hashlib
import json

import duckdb
import pytest

from app.core.bjtime import BJ_TZ
from app.review import daily_market as daily


DAY, PREVIOUS = date(2026, 10, 26), date(2026, 10, 23)
NOW = datetime(2026, 10, 26, 18, 45, tzinfo=BJ_TZ)
CALENDAR = [date(2026, 10, 22), PREVIOUS, DAY]


def _ms(day):
    return int(datetime(day.year, day.month, day.day, tzinfo=BJ_TZ).timestamp() * 1000)


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _meta(path, *, at="2026-10-26T16:31:32+08:00", status="ok", issues=None):
    with duckdb.connect(str(path), read_only=True) as con:
        maximum, total = con.execute("SELECT max(date_ms),count(*) FROM daily_k").fetchone()
        adj_rows = con.execute("SELECT count(*) FROM daily_k_adj").fetchone()[0]
    (path.parent / "quality_report.json").write_text(json.dumps({
        "available": True, "status": status, "error_count": 0, "synced_at": at,
        "issues": issues or [], "sync": {"daily": {"dmax": maximum, "total": total},
                                          "adj": {"rows": adj_rows}},
    }))
    (path.parent / "sync_state.json").write_text(json.dumps({"last_sync_date": "20261026", "at": at}))


@pytest.fixture
def warehouse(tmp_path):
    path = tmp_path / "market.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE daily_k(thscode VARCHAR,date_ms BIGINT,close_price DOUBLE,volume DOUBLE,turnover DOUBLE)")
        con.execute("CREATE TABLE daily_k_adj(thscode VARCHAR,date_ms BIGINT,close_adj DOUBLE)")
        # Raw before/after shows a corporate action; only the aligned adjusted
        # sequence may be compared. Last row has no adjacent prior bar.
        con.executemany("INSERT INTO daily_k VALUES (?,?,?,?,?)", [
            ("600001.SH", _ms(PREVIOUS), 20, 1000, 2000),
            ("600001.SH", _ms(DAY), 11, 1000, 3000),
            ("000001.SZ", _ms(DAY), 8, 1000, 4000),
            ("300001.SZ", _ms(DAY), 5, 0, 0),
            ("920001.BJ", _ms(DAY), 10, 1000, None),
        ])
        con.executemany("INSERT INTO daily_k_adj VALUES (?,?,?)", [
            ("600001.SH", _ms(PREVIOUS), 10), ("600001.SH", _ms(DAY), 11),
            ("000001.SZ", _ms(PREVIOUS), 10), ("000001.SZ", _ms(DAY), 8),
            ("300001.SZ", _ms(PREVIOUS), 5), ("300001.SZ", _ms(DAY), 5),
            ("920001.BJ", _ms(DAY), 10),
        ])
    _meta(path)
    return path


def collect(path, **extra):
    return daily.collect_daily_breadth(DAY, trade_days=CALENDAR, db_path=path, now=NOW, **extra)


def test_adjusted_basis_calendar_coverage_and_real_zero(warehouse):
    before = _hash(warehouse)
    value, proof, meta = collect(warehouse)
    assert proof.state == "partial" and proof.source_trade_date == DAY.isoformat()
    assert proof.source_as_of is None and proof.received_at == "2026-10-26T16:31:32+08:00"
    assert value["coverage"] == {"verified_count": 3, "universe_count": 4, "missing_count": 1}
    assert (value["up"], value["down"], value["flat"]) == (1, 1, 1)
    assert "复权" in value["basis"] and "非官方实时" in value["basis"]
    assert value["total_amount"] == 7000
    assert meta["amount_basis"]["valid_count"] == 3 and meta["amount_basis"]["missing_count"] == 1
    assert meta["previous_trade_date"] == "2026-10-23"
    assert meta["missing_reasons"] == {"missing_previous_adjacent_bar": 1}
    assert "limit_up" not in value and "limit_down" not in value and "suspended" not in value
    assert "15:00" in proof.reason and "未知" in meta["source_clock_semantics"]
    assert _hash(warehouse) == before


def test_read_only_connection_and_source_hashes(warehouse, monkeypatch):
    actual, seen = duckdb.connect, []

    def connect(path, **kwargs):
        seen.append(kwargs)
        return actual(path, **kwargs)

    monkeypatch.setattr(duckdb, "connect", connect)
    value, _, meta = collect(warehouse)
    assert value and seen == [{"read_only": True}]
    assert len(meta["source_hashes"]["daily_inputs"]) == 64
    assert "database" not in meta["source_hashes"]
    assert len(meta["source_version"]) == 64


@pytest.mark.parametrize("which", ["daily_k", "daily_k_adj"])
def test_duplicate_day_key_is_not_counted_twice(warehouse, which):
    with duckdb.connect(str(warehouse)) as con:
        con.execute(f"INSERT INTO {which} SELECT * FROM {which} WHERE thscode='600001.SH' AND date_ms=? LIMIT 1", [_ms(DAY)])
    _meta(warehouse)
    value, proof, meta = collect(warehouse)
    assert value == {} and proof.state == "unavailable" and "重复" in meta["unavailable_reason"]


def test_other_day_and_wrong_market_identity_are_not_relabelled(warehouse):
    with duckdb.connect(str(warehouse)) as con:
        con.execute("INSERT INTO daily_k VALUES ('600002.SZ', ?,10,100,9000)", [_ms(DAY)])
        con.execute("INSERT INTO daily_k VALUES ('600003.SH', ?,10,100,9000)", [_ms(PREVIOUS)])
    _meta(warehouse)
    value, _, meta = collect(warehouse)
    assert value["total"] == 4 and meta["excluded_non_stock"] == 1
    assert value["total_amount"] == 7000


@pytest.mark.parametrize("calendar,now", [
    ([PREVIOUS], NOW), ([DAY], NOW), (CALENDAR + [DAY], NOW),
    ([PREVIOUS, DAY, "2026-10-27"], NOW),
    (CALENDAR, NOW.replace(hour=15)), (CALENDAR, NOW - timedelta(days=1)),
])
def test_no_weekday_guess_or_unfinished_day_admission(warehouse, calendar, now):
    value, proof, _ = daily.collect_daily_breadth(DAY, trade_days=calendar, db_path=warehouse, now=now)
    assert value == {} and proof.state == "unavailable"


@pytest.mark.parametrize("target,field,value", [
    ("quality_report.json", "status", "fail"), ("quality_report.json", "error_count", 1),
    ("quality_report.json", "synced_at", "2026-10-26T15:00:00+08:00"),
    ("quality_report.json", "synced_at", "2026-10-27T16:30:00+08:00"),
    ("sync_state.json", "last_sync_date", "20261023"),
    ("sync_state.json", "at", "2026-10-23T16:30:00+08:00"),
])
def test_running_clock_does_not_replace_successful_source_sync(warehouse, target, field, value):
    path = warehouse.parent / target
    doc = json.loads(path.read_text())
    doc[field] = value
    path.write_text(json.dumps(doc))
    got, proof, _ = collect(warehouse)
    assert got == {} and proof.state == "unavailable"


def test_known_adjusted_warning_and_invalid_fields_keep_denominator(warehouse):
    with duckdb.connect(str(warehouse)) as con:
        con.execute("UPDATE daily_k SET close_price=-1 WHERE thscode='000001.SZ'")
    _meta(warehouse, status="warn", issues=[{"severity": "warn", "check": "daily_k_adj.single_day_jump",
                                          "sample": [["600001.SH", _ms(DAY), 99]]}])
    value, proof, meta = collect(warehouse)
    assert proof.state == "partial"
    assert value["coverage"] == {"verified_count": 1, "universe_count": 4, "missing_count": 3}
    assert meta["missing_reasons"]["invalid_raw_bar"] == 1
    assert meta["missing_reasons"]["source_quality_warning"] == 1
    assert value["total_amount"] == 3000


def test_mid_read_source_change_is_not_a_mixed_version_result(warehouse, monkeypatch):
    original = daily._read_rows

    def change(*args):
        result = original(*args)
        with duckdb.connect(str(warehouse)) as con:
            con.execute("UPDATE daily_k_adj SET close_adj=100 WHERE thscode='600001.SH'")
        return result

    monkeypatch.setattr(daily, "_read_rows", change)
    value, proof, meta = collect(warehouse)
    assert value == {} and proof.state == "unavailable" and "版本发生变化" in meta["unavailable_reason"]


def test_quality_source_counts_cannot_certify_another_db_version(warehouse):
    with duckdb.connect(str(warehouse)) as con:
        con.execute("DELETE FROM daily_k_adj WHERE thscode='600001.SH'")
    value, proof, meta = collect(warehouse)
    assert value == {} and proof.state == "unavailable" and "版本不符" in meta["unavailable_reason"]


def test_no_day_bar_does_not_fallback_to_previous_session(warehouse):
    with duckdb.connect(str(warehouse)) as con:
        con.execute("DELETE FROM daily_k WHERE date_ms=?", [_ms(DAY)])
    _meta(warehouse)
    value, proof, _ = collect(warehouse)
    assert value == {} and proof.state == "unavailable"
