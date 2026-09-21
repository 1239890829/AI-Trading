"""RSH-026 legacy outcome recovery tool regressions."""
from __future__ import annotations

import sqlite3
from datetime import datetime

import duckdb

from app.core.bjtime import BJ_TZ
from scripts.backfill_opportunity_outcomes import _has_planned_writes, _marketdb_closes, _pending_symbols_ro


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=BJ_TZ).timestamp() * 1000)


def test_marketdb_recovery_is_exact_date_and_market_suffix_agnostic(tmp_path):
    path = tmp_path / "market.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("""
            create table daily_k(
                thscode varchar, date_ms bigint, open_price double, high_price double,
                low_price double, close_price double, volume double, turnover double
            )
        """)
        con.executemany(
            "insert into daily_k values (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("600001.SH", _ms("2026-09-16"), 10, 11, 9, 10.5, 1, 1),
                ("920001.BJ", _ms("2026-09-16"), 20, 21, 19, 20.5, 1, 1),
                # Same symbol next day must never leak into the target-day recovery.
                ("600001.SH", _ms("2026-09-17"), 99, 100, 98, 99.5, 1, 1),
            ],
        )
    finally:
        con.close()

    closes, meta = _marketdb_closes(
        path, "2026-09-16", {"600001", "920001", "000001"}
    )
    assert closes == {"600001": 10.5, "920001": 20.5}
    assert meta["requested_symbols"] == 3
    assert meta["marketdb_closes"] == 2
    assert meta["coverage"] == 0.6667
    assert meta["missing_symbols"] == ["000001"]


def test_pending_symbols_ro_includes_missing_pending_and_deferred_only(tmp_path):
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    try:
        con.executescript(
            """
            create table opportunity_decision_snapshot(
                snapshot_id text primary key, trade_date text, symbol text
            );
            create table opportunity_outcome_label(
                id integer primary key, snapshot_id text, horizon text, state text
            );
            insert into opportunity_decision_snapshot values
                ('s1','2026-09-16','600001'),
                ('s2','2026-09-16','600002'),
                ('s3','2026-09-16','600003'),
                ('s4','2026-09-16','600004');
            insert into opportunity_outcome_label values
                (1,'s2','d0_close','pending'),
                (2,'s3','d0_close','deferred'),
                (3,'s4','d0_close','labeled');
            """
        )
        con.commit()
    finally:
        con.close()

    assert _pending_symbols_ro(path, "2026-09-16") == {
        "600001", "600002", "600003"
    }


def test_marketdb_recovery_rejects_nonpositive_or_nonfinite_close(tmp_path):
    path = tmp_path / "market.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("create table daily_k(thscode varchar, date_ms bigint, close_price double)")
        con.executemany(
            "insert into daily_k values (?, ?, ?)",
            [
                ("600001.SH", _ms("2026-09-16"), 0.0),
                ("600002.SH", _ms("2026-09-16"), float("nan")),
                ("600003.SH", _ms("2026-09-16"), 8.8),
            ],
        )
    finally:
        con.close()

    closes, meta = _marketdb_closes(
        path, "2026-09-16", {"600001", "600002", "600003"}
    )
    assert closes == {"600003": 8.8}
    assert meta["missing_count"] == 2


def test_noop_backup_gate_only_opens_for_real_writes():
    clean = {"missing_d0_outcomes": 0, "legacy_pending_fill_ok": 0}
    assert _has_planned_writes(clean, []) is False
    assert _has_planned_writes(clean, [{"marketdb_closes": 0}]) is False
    assert _has_planned_writes({**clean, "missing_d0_outcomes": 1}, []) is True
    assert _has_planned_writes({**clean, "legacy_pending_fill_ok": 1}, []) is True
    assert _has_planned_writes(clean, [{"marketdb_closes": 1}]) is True
