"""Real, file-backed read locks must not block the paper read event loop."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
from threading import Event, get_ident
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes.paper import _positions_with_live
from app.models.paper import PaperOrder, PaperPosition
from app.models.watchlist import Base
from app.paper.engine import PaperTradingEngine


@pytest.fixture
def paper_file_db(tmp_path):
    path = tmp_path / "paper-read-contention.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = []

    class TrackedSession(Session):
        def __enter__(self):
            self.test_thread = get_ident()
            sessions.append({"opened_thread": self.test_thread})
            self.test_record = sessions[-1]
            return super().__enter__()

        def __exit__(self, *args):
            try:
                return super().__exit__(*args)
            finally:
                self.test_record["closed_thread"] = get_ident()

    sf = sessionmaker(bind=engine, class_=TrackedSession, autoflush=False, expire_on_commit=False)
    with engine.connect() as conn:
        settings = {"journal_mode": conn.scalar(text("PRAGMA journal_mode")),
                    "busy_timeout_ms": conn.scalar(text("PRAGMA busy_timeout"))}
    assert settings == {"journal_mode": "delete", "busy_timeout_ms": 5000}
    with sf() as db:
        db.add_all([
            PaperOrder(scope="main", symbol="600105", side="buy", price=10, quantity=100),
            PaperOrder(scope="shadow", symbol="600127", side="buy", price=12, quantity=200),
            PaperOrder(scope="main", symbol="600127", side="buy", price=12,
                       quantity=100, status="filled"),
            PaperPosition(scope="main", symbol="600105", quantity=100, frozen_today=100,
                          cost_price=10, buy_date="20261015"),
            PaperPosition(scope="main", symbol="600127", quantity=200, frozen_today=20,
                          cost_price=12, buy_date="20261015"),
            PaperPosition(scope="shadow", symbol="600578", quantity=300,
                          cost_price=20, buy_date="20261015"),
        ])
        db.commit()
    yield path, engine, sf, sessions, settings
    engine.dispose()


def _receipt(stage, payload):
    output = os.environ.get("ASHARE_PAPER_CONTENTION_EVIDENCE_DIR")
    if output:
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"paper-read-{stage}.json").write_text(json.dumps(payload, indent=2) + "\n")


@pytest.mark.parametrize("stage", ["pending_first_read", "pending_remaining_count", "positions"])
def test_paper_read_sqlite_lock_keeps_loop_running(paper_file_db, monkeypatch, stage):
    path, sql_engine, sf, sessions, settings = paper_file_db
    lock_ready, lock_released = Event(), Event()
    sql_entered, sql_returned = Event(), Event()
    activated = Event()
    timeline = {"stage": stage}
    writer_future = None
    statements = []
    pool = ThreadPoolExecutor(max_workers=1)

    def target_sql(statement):
        normalized = " ".join(statement.upper().split())
        if stage == "positions":
            return normalized.startswith("SELECT PAPER_POSITION.")
        if stage == "pending_remaining_count":
            return normalized.startswith("SELECT COUNT(") and "FROM PAPER_ORDER" in normalized
        return normalized.startswith("SELECT PAPER_ORDER.")

    def before_sql(_conn, _cursor, statement, _parameters, _context, _many):
        if activated.is_set() and not sql_entered.is_set() and target_sql(statement):
            timeline["target_thread"] = get_ident()
            timeline["sql_entered_at"] = time.perf_counter()
            statements.append(statement)
            sql_entered.set()

    def after_sql(_conn, _cursor, statement, _parameters, _context, _many):
        if (sql_entered.is_set() and not sql_returned.is_set()
                and get_ident() == timeline["target_thread"] and target_sql(statement)):
            timeline["sql_returned_at"] = time.perf_counter()
            sql_returned.set()

    event.listen(sql_engine, "before_cursor_execute", before_sql)
    event.listen(sql_engine, "after_cursor_execute", after_sql)

    def hold_real_exclusive_lock():
        with sqlite3.connect(path) as conn:
            conn.execute("BEGIN EXCLUSIVE")
            timeline["lock_acquired_at"] = time.perf_counter()
            lock_ready.set()
            # The independent thread releases even if a synchronous regression
            # freezes the loop. No loop clock or fake database is involved.
            observed = sql_entered.wait(2)
            timeline["watchdog_released"] = not observed
            timeline["fixed_hold_started_at"] = time.perf_counter()
            time.sleep(.25)
            timeline["fixed_hold_finished_at"] = time.perf_counter()
            conn.commit()
            timeline["lock_released_at"] = time.perf_counter()
        lock_released.set()

    def start_writer():
        nonlocal writer_future
        activated.set()
        writer_future = pool.submit(hold_real_exclusive_lock)
        assert lock_ready.wait(2), "isolated writer did not acquire its real read lock"

    quote_symbols = []

    async def offline_quote(symbol):
        quote_symbols.append(symbol)
        if stage == "pending_remaining_count":
            start_writer()
        # This test never enters a fill/write transaction. Source failures keep
        # the existing pending order and still reach the final remaining count.
        raise LookupError("isolated quote unavailable")

    paper = PaperTradingEngine(sf, offline_quote)
    settlements = []

    async def already_settled():
        settlements.append(True)
        return 0

    monkeypatch.setattr(paper, "settle_t1", already_settled)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        paper=paper, hub=SimpleNamespace(get_quotes=lambda: [SimpleNamespace(symbol="600105", price=11)]))))
    session_offset = len(sessions)
    if stage != "pending_remaining_count":
        start_writer()

    async def run():
        timeline["loop_thread"] = get_ident()
        beats = []
        done = False

        async def heartbeat():
            while not done:
                now = time.perf_counter()
                beats.append({"at": now, "during_target_lock": (
                    sql_entered.is_set() and not sql_returned.is_set()
                    and lock_ready.is_set() and not lock_released.is_set())})
                await asyncio.sleep(.01)

        heartbeat_task = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        try:
            return (await _positions_with_live(request))[1] if stage == "positions" else await paper.match_pending()
        finally:
            beats.append({"at": time.perf_counter(), "during_target_lock": False})
            done = True
            await heartbeat_task
            timeline["heartbeats_during_target_lock"] = sum(b["during_target_lock"] for b in beats)
            timeline["max_heartbeat_gap_seconds"] = max(
                b["at"] - a["at"] for a, b in zip(beats, beats[1:]))
            timeline["heartbeats"] = beats

    try:
        result = asyncio.run(run())
    finally:
        pool.shutdown(wait=True)
        if writer_future is not None:
            writer_future.result()
        event.remove(sql_engine, "before_cursor_execute", before_sql)
        event.remove(sql_engine, "after_cursor_execute", after_sql)
    evidence = {**settings, **timeline, "target_sql": statements,
                "read_sessions": sessions[session_offset:], "result": result,
                "quote_symbols": quote_symbols, "settlement_calls": len(settlements)}
    _receipt(stage, evidence)
    assert sql_entered.is_set() and sql_returned.is_set() and lock_released.is_set(), evidence
    assert timeline["watchdog_released"] is False, evidence
    assert timeline["sql_returned_at"] >= timeline["fixed_hold_finished_at"], evidence
    assert timeline["heartbeats_during_target_lock"] >= 3, evidence
    assert timeline["target_thread"] != timeline["loop_thread"], evidence
    assert all(s["opened_thread"] == s["closed_thread"] for s in sessions[session_offset:]), evidence
    if stage == "positions":
        assert settlements == [True] and quote_symbols == []
        assert result == [
            {"symbol": "600105", "quantity": 100, "available": 0, "cost_price": 10,
             "last_price": 11, "pnl": 100, "pnl_pct": 10},
            {"symbol": "600127", "quantity": 200, "available": 180, "cost_price": 12,
             "last_price": None, "pnl": None, "pnl_pct": None},
        ]
    else:
        assert result == 1 and quote_symbols == ["600105"] and settlements == []
    with sf() as db:
        assert [(o.scope, o.symbol, o.status) for o in db.query(PaperOrder).order_by(PaperOrder.id)] == [
            ("main", "600105", "pending"), ("shadow", "600127", "pending"),
            ("main", "600127", "filled"),
        ]
