"""RSH-026 legacy full-funnel D0 outcome recovery.

Dry-run is the default.  ``--apply`` is required for writes and creates a
consistent SQLite backup before the first mutation.  The tool never calls an
external market-data provider: optional D0 close recovery uses an exact-date,
local marketdb only.

Typical flow after deploying/migrating the current code::

    .venv/bin/python scripts/backfill_opportunity_outcomes.py --db data/ashare.db
    .venv/bin/python scripts/backfill_opportunity_outcomes.py \
        --db data/ashare.db --marketdb data/marketdb/market.duckdb --apply

Run with the application/schedulers stopped.  Decision snapshots and already
labeled outcomes are append-only and are never rewritten by this recovery.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections.abc import Iterable
from datetime import date, datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.bjtime import BJ_TZ  # noqa: E402
from app.data_providers.ths import to_thscode  # noqa: E402
from app.picks.opportunity_learning import (  # noqa: E402
    backfill_missing_outcome_identities,
    label_trade_date,
    pending_symbols,
)

REQUIRED_OUTCOME_COLUMNS = {
    "snapshot_id", "horizon", "state", "fill_state", "path_state", "path_version",
}
SELECTED_SQL = """(
    (s.stage = 'rank' AND s.decision = 'ranked') OR
    (s.stage = 'notification' AND s.decision IN ('eligible','notified','suppressed'))
)"""


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _status(path: Path, dates: Iterable[str] = ()) -> dict:
    wanted = tuple(sorted(set(dates)))
    with _ro(path) as con:
        rev = con.execute("select version_num from alembic_version").fetchone()
        where, params = "", []
        if wanted:
            where = " where s.trade_date in (%s)" % ",".join("?" for _ in wanted)
            params = list(wanted)
        snapshots = con.execute(
            "select count(*) from opportunity_decision_snapshot s" + where, params
        ).fetchone()[0]
        outcome_join = (
            " from opportunity_outcome_label o join opportunity_decision_snapshot s "
            "on s.snapshot_id=o.snapshot_id where o.horizon='d0_close'"
        )
        if wanted:
            outcome_join += " and s.trade_date in (%s)" % ",".join("?" for _ in wanted)
        outcomes = con.execute("select count(*)" + outcome_join, params).fetchone()[0]
        missing_sql = """
            select count(*) from opportunity_decision_snapshot s
            where not exists (
                select 1 from opportunity_outcome_label o
                where o.snapshot_id=s.snapshot_id and o.horizon='d0_close'
            )
        """
        selected_missing_sql = missing_sql + " and " + SELECTED_SQL
        if wanted:
            clause = " and s.trade_date in (%s)" % ",".join("?" for _ in wanted)
            missing_sql += clause
            selected_missing_sql += clause
        missing = con.execute(missing_sql, params).fetchone()[0]
        selected_missing = con.execute(selected_missing_sql, params).fetchone()[0]
        pending_fill_sql = (
            "select count(*)" + outcome_join +
            " and o.state='pending' and o.labeled_at is null and o.fill_state='ok' and o.reason='等待收盘价' and " + SELECTED_SQL
        )
        pending_fill_ok = con.execute(pending_fill_sql, params).fetchone()[0]
        by_date_sql = """
            select s.trade_date,
                   count(*) as snapshots,
                   sum(case when exists (
                       select 1 from opportunity_outcome_label o
                       where o.snapshot_id=s.snapshot_id and o.horizon='d0_close'
                   ) then 1 else 0 end) as outcome_attached
            from opportunity_decision_snapshot s
        """
        by_params: list[str] = []
        if wanted:
            by_date_sql += " where s.trade_date in (%s)" % ",".join("?" for _ in wanted)
            by_params = list(wanted)
        by_date_sql += " group by s.trade_date order by s.trade_date"
        by_date = [
            {"trade_date": d, "snapshots": n, "outcomes": o, "missing": n - o}
            for d, n, o in con.execute(by_date_sql, by_params)
        ]
    return {
        "alembic_revision": rev[0] if rev else None,
        "snapshot_rows": snapshots,
        "d0_outcome_rows": outcomes,
        "missing_d0_outcomes": missing,
        "selected_missing_d0_outcomes": selected_missing,
        "legacy_pending_fill_ok": pending_fill_ok,
        "by_date": by_date,
    }


def _pending_symbols_ro(path: Path, trade_date: str) -> set[str]:
    """Read-only recovery surface before identities are inserted.

    A snapshot needs a D0 close lookup when its identity is missing or when the
    existing D0 row is still pending/deferred.  This query intentionally uses
    only legacy columns so dry-run also works before the newest path migration.
    """
    with _ro(path) as con:
        rows = con.execute(
            """
            select distinct s.symbol
            from opportunity_decision_snapshot s
            left join opportunity_outcome_label o
              on o.snapshot_id=s.snapshot_id and o.horizon='d0_close'
            where s.trade_date=?
              and (o.id is null or o.state in ('pending','deferred'))
            """,
            (trade_date,),
        ).fetchall()
    return {str(row[0]) for row in rows if row and row[0]}


def _assert_schema_ready(path: Path) -> None:
    with _ro(path) as con:
        cols = {row[1] for row in con.execute("pragma table_info(opportunity_outcome_label)")}
    missing = sorted(REQUIRED_OUTCOME_COLUMNS - cols)
    if missing:
        raise SystemExit(
            "数据库 schema 尚未升级到当前 RSH-026 代码；缺列=" + ",".join(missing) +
            "。先对该数据库执行 alembic upgrade head，再运行恢复。"
        )


def _backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = path.with_name(path.name + f".bak-rsh026-{stamp}")
    src = sqlite3.connect(str(path))
    dst = sqlite3.connect(str(out))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return out


def _session_factory(path: Path):
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    return sessionmaker(bind=engine, expire_on_commit=False), engine


def _marketdb_closes(path: Path, trade_date: str, symbols: set[str]) -> tuple[dict[str, float], dict]:
    import duckdb

    d = date.fromisoformat(trade_date)
    target_ms = int(datetime.combine(d, time.min, tzinfo=BJ_TZ).timestamp() * 1000)
    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute(
            "select thscode, close_price from daily_k where date_ms=?", [target_ms]
        ).fetchall()
    finally:
        con.close()
    requested = {to_thscode(symbol): symbol for symbol in symbols}
    closes: dict[str, float] = {}
    for thscode, close in rows:
        symbol = requested.get(str(thscode or ""))
        if symbol is None or close is None:
            continue
        value = float(close)
        if math.isfinite(value) and value > 0:
            closes[symbol] = value
    missing = sorted(symbols - set(closes))
    return closes, {
        "trade_date": trade_date,
        "requested_symbols": len(symbols),
        "marketdb_closes": len(closes),
        "coverage": round(len(closes) / len(symbols), 4) if symbols else None,
        "missing_symbols": missing[:20],
        "missing_count": len(missing),
    }




def _has_planned_writes(before: dict, marketdb_preflight: list[dict]) -> bool:
    """Whether --apply can mutate rows; avoids giant no-op backups."""
    if int(before.get("missing_d0_outcomes") or 0) > 0:
        return True
    if int(before.get("legacy_pending_fill_ok") or 0) > 0:
        return True
    return any(int(row.get("marketdb_closes") or 0) > 0 for row in marketdb_preflight)

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, type=Path, help="SQLite ashare.db path")
    p.add_argument("--marketdb", type=Path, help="Optional local market.duckdb for exact-date D0 closes")
    p.add_argument("--date", action="append", default=[], help="Limit recovery to YYYY-MM-DD; repeatable")
    p.add_argument("--batch-size", type=int, default=2000)
    p.add_argument("--apply", action="store_true", help="Actually write; otherwise dry-run only")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    db = args.db.expanduser().resolve()
    marketdb = args.marketdb.expanduser().resolve() if args.marketdb else None
    if not db.is_file():
        raise SystemExit(f"SQLite database not found: {db}")
    if marketdb is not None and not marketdb.is_file():
        raise SystemExit(f"marketdb not found: {marketdb}")
    for raw in args.date:
        date.fromisoformat(raw)

    before = _status(db, args.date)
    dates = args.date or [row["trade_date"] for row in before["by_date"]]
    marketdb_preflight = []
    if marketdb is not None:
        for d in dates:
            symbols = _pending_symbols_ro(db, d)
            _closes, coverage = _marketdb_closes(marketdb, d, symbols)
            marketdb_preflight.append(coverage)
    print(json.dumps({
        "mode": "apply" if args.apply else "dry_run",
        "before": before,
        "marketdb_preflight": marketdb_preflight,
    }, ensure_ascii=False, indent=2))
    if not args.apply:
        print("DRY-RUN: no database writes performed. Add --apply after reviewing the counts.")
        return 0

    _assert_schema_ready(db)
    if not _has_planned_writes(before, marketdb_preflight):
        print(json.dumps({
            "noop": True,
            "backup": None,
            "reason": "no identity, legacy-fill, or local-close writes are currently available",
        }, ensure_ascii=False, indent=2))
        return 0
    backup = _backup(db)
    sf, engine = _session_factory(db)
    try:
        recovered = backfill_missing_outcome_identities(
            sf, trade_dates=args.date or None, batch_size=args.batch_size,
        )
        close_results = []
        if marketdb is not None:
            for d in dates:
                symbols = pending_symbols(d, sf, include_deferred=True)
                closes, coverage = _marketdb_closes(marketdb, d, symbols)
                labeled = label_trade_date(
                    d, closes, sf, include_deferred=True, batch_size=args.batch_size
                )
                close_results.append({**coverage, "label_result": labeled})
        after = _status(db, args.date)
    finally:
        engine.dispose()

    print(json.dumps({
        "backup": str(backup),
        "identity_recovery": recovered,
        "marketdb_recovery": close_results,
        "after": after,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
