"""SQLite contention must not freeze unrelated work on the async consumer loop."""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.models.notification_outbox import BuyPointConsumption as Progress
from app.models.opportunity_learning import OpportunityDecisionSnapshot as Decision
from app.models.watch_ledger import WatchLedger
from app.picks import buy_point_consumers as consumers


NOW = datetime(2026, 10, 26, 10, 0, tzinfo=BJ_TZ)
DAY = NOW.date().isoformat()
KEY = "isolated-buy-point"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    path = tmp_path / "consumers.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 1})
    for table in (Progress.__table__, Decision.__table__, WatchLedger.__table__):
        table.create(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(consumers, "beijing_now", lambda: NOW)
    monkeypatch.setattr("app.market.trade_calendar.in_trading_window", lambda *_: True)
    calls = []

    async def open_position(app, **kwargs):
        calls.append(kwargs)
        return {"opened": True, "accepted": True, "order_id": 42,
                "decision_id": "decision-original", "decision_version": "version-original", "reason": "filled"}

    monkeypatch.setattr("app.picks.position_engine.maybe_open", open_position)
    # The committed projection is another already-offloaded consumer. Keep this
    # fixture focused on the primary claim/action/finish transaction boundaries.
    async def no_projection(*args, **kwargs):
        pass
    monkeypatch.setattr(consumers, "_project_positions", no_projection)
    payload = {"alert": {"symbol": "600001", "name": "甲公司", "at": NOW.isoformat(), "direction": "设备",
        "text": "原始买点", "meta": {"trade_date": DAY,
        "execution_ref": {"decision_id": "decision-original", "decision_version": "version-original"},
        "selection_ref": f"daily:{DAY}:600001", "selection_source_version": "source-original"}},
        "snapshot": {"price": 10, "change_pct": 2}}

    def seed(consumer="watch", state="pending", *, attempts=0, expiry=None):
        with sf() as db:
            db.add(Progress(event_key=KEY, event_id=1, consumer=consumer, payload=json.dumps(payload, ensure_ascii=False),
                state=state, reason="queued", attempts=attempts,
                expires_at_ms=expiry if expiry is not None else int(time.time() * 1000) + 60_000,
                updated_at_ms=int(time.time() * 1000)))
            if consumer == "position":
                contract = {"decision_id": "decision-original", "decision_version": "version-original",
                    "gate_decision": "passed", "executable_snapshot": {"state": "ready", "price": 10},
                    "selection": {"ref": f"daily:{DAY}:600001", "source_version": "source-original"}}
                db.add(Decision(snapshot_id="s" * 40, run_id="isolated", trade_date=DAY,
                    as_of=NOW.replace(tzinfo=None), scenario="buy_point", stage="notification", symbol="600001",
                    decision="eligible", strategy_version="isolated", feature_version="isolated",
                    evidence=json.dumps({"execution_contract": contract})))
            db.commit()

    yield SimpleNamespace(path=path, engine=engine, sf=sf, calls=calls, payload=payload, seed=seed)
    engine.dispose()


def _matches(phase, statement):
    sql = " ".join(statement.lower().split())
    return {
        "pending_read": sql.startswith("select buy_point_consumption.event_id"),
        "reclaim": sql.startswith("update buy_point_consumption") and "attempts=" not in sql and "payload=" not in sql
            and "buy_point_consumption.updated_at_ms <" in sql,
        "claim": sql.startswith("update buy_point_consumption") and "attempts=" in sql,
        "finish": sql.startswith("update buy_point_consumption") and "buy_point_consumption.updated_at_ms =" in sql,
        "sighting": "from watch_ledger" in sql,
        "latest": "from opportunity_decision_snapshot" in sql,
    }[phase]


def _contend(rig, phase, *, hold=.2):
    """Acquire a real independent EXCLUSIVE transaction just before target SQL."""
    state = {"hit": False, "ticks": 0, "ticks_while_locked": 0}
    connection = sqlite3.connect(rig.path, check_same_thread=False)
    released = threading.Event()
    threads = []

    def before_cursor(conn, cursor, statement, parameters, context, executemany):
        if state["hit"] or not _matches(phase, statement):
            return
        state["hit"] = True
        connection.execute("BEGIN EXCLUSIVE")
        before = state["ticks"]

        def release():
            time.sleep(hold)
            state["ticks_while_locked"] = state["ticks"] - before
            connection.rollback()
            released.set()

        thread = threading.Thread(target=release)
        threads.append(thread)
        thread.start()

    event.listen(rig.engine, "before_cursor_execute", before_cursor)

    async def run():
        stop = asyncio.Event()

        async def heartbeat():
            while not stop.is_set():
                state["ticks"] += 1
                await asyncio.sleep(.005)

        pulse = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        try:
            await consumers.consume_pending(None, rig.sf)
        finally:
            stop.set()
            await pulse

    def finish():
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
        event.remove(rig.engine, "before_cursor_execute", before_cursor)
        connection.close()
        assert state["hit"], f"target SQL not reached: {phase}"
        assert released.is_set()

    return state, run, finish


@pytest.mark.parametrize("phase", ["pending_read", "reclaim", "claim", "finish", "sighting", "latest"])
def test_busy_sqlite_keeps_heartbeat_and_original_consumer_result(rig, phase):
    consumer = "position" if phase == "latest" else "watch"
    rig.seed(consumer)
    state, run, finish = _contend(rig, phase)
    try:
        asyncio.run(run())
    finally:
        finish()
    assert state["ticks_while_locked"] >= 3, f"event loop stalled during SQLite {phase}"
    with rig.sf() as db:
        row = db.get(Progress, (KEY, consumer))
        assert (row.state, row.attempts) == ("done", 1)
        saved = json.loads(row.payload)
        assert saved["alert"] == rig.payload["alert"] and saved["snapshot"] == rig.payload["snapshot"]
        if consumer == "watch":
            assert saved == rig.payload
            sighting = db.query(WatchLedger).one()
            assert (sighting.symbol, sighting.entry_price, sighting.entry_time) == ("600001", 10, "10:00:00")
            assert json.loads(sighting.reason) == {"kind": "buy_point", "text": "原始买点",
                "decision_id": "decision-original", "decision_version": "version-original"}
            assert rig.calls == []
        else:
            assert saved["execution_result"]["order_id"] == 42
            assert saved["paper_projection"] == {"state": "pending"}
            assert len(rig.calls) == 1
            assert rig.calls[0]["selection_context"] == {"selection_ref": f"daily:{DAY}:600001",
                "source_version": "source-original", "decision_id": "decision-original",
                "decision_version": "version-original", "consumer_event_key": KEY, "expires_at_ms": row.expires_at_ms}
    asyncio.run(consumers.consume_pending(None, rig.sf))
    assert len(rig.calls) == (1 if consumer == "position" else 0)


@pytest.mark.parametrize("phase", ["pending_read", "claim", "finish"])
def test_busy_timeout_preserves_state_and_never_repeats_uncertain_paper_action(rig, phase):
    rig.seed("position")
    rig.engine.dispose()
    event.listen(rig.engine, "connect", lambda connection, record: connection.execute("PRAGMA busy_timeout=50"))
    state, run, finish = _contend(rig, phase)
    try:
        with pytest.raises(OperationalError, match="database is locked"):
            asyncio.run(run())
    finally:
        finish()
    assert state["ticks_while_locked"] >= 3
    with rig.sf() as db:
        row = db.get(Progress, (KEY, "position"))
        assert (row.state, row.attempts) == (("running", 1) if phase == "finish" else ("pending", 0))
    if phase == "finish":
        asyncio.run(consumers.consume_pending(None, rig.sf))
        assert len(rig.calls) == 1


def test_finish_keeps_atomic_order_identity_and_claim_cas(rig, monkeypatch):
    rig.seed("position")

    async def atomic_order(app, **kwargs):
        rig.calls.append(kwargs)
        with rig.sf() as db:
            row = db.get(Progress, (KEY, "position"))
            payload = json.loads(row.payload)
            payload["execution_result"] = {"order_id": 99, "accepted": True, "reason": "atomic-original"}
            row.payload = json.dumps(payload)
            db.commit()
        return {"opened": True, "order_id": 42, "reason": "wrapper-result"}

    monkeypatch.setattr("app.picks.position_engine.maybe_open", atomic_order)
    asyncio.run(consumers.consume_pending(None, rig.sf))
    with rig.sf() as db:
        row = db.get(Progress, (KEY, "position"))
        assert row.state == "done" and row.reason == "wrapper-result"
        assert json.loads(row.payload)["execution_result"] == {
            "opened": True, "order_id": 99, "reason": "atomic-original", "accepted": True}


def test_newer_claim_cannot_be_finished_by_old_consumer(rig, monkeypatch):
    rig.seed("position")

    async def changed_owner(app, **kwargs):
        with rig.sf() as db:
            row = db.get(Progress, (KEY, "position"))
            row.updated_at_ms += 1
            row.reason = "new-owner"
            db.commit()
        return {"opened": False, "reason": "old-wrapper"}

    monkeypatch.setattr("app.picks.position_engine.maybe_open", changed_owner)
    asyncio.run(consumers.consume_pending(None, rig.sf))
    with rig.sf() as db:
        row = db.get(Progress, (KEY, "position"))
        assert row.state == "running" and row.reason == "new-owner" and json.loads(row.payload) == rig.payload


def test_expired_position_and_unknown_interrupted_action_are_not_retried(rig):
    rig.seed("position", expiry=int(time.time() * 1000) - 1)
    asyncio.run(consumers.consume_pending(None, rig.sf))
    with rig.sf() as db:
        row = db.get(Progress, (KEY, "position"))
        assert (row.state, row.reason) == ("expired", "original_action_expired")
        row.state, row.updated_at_ms = "running", int(time.time() * 1000) - 120_001
        db.commit()
    asyncio.run(consumers.consume_pending(None, rig.sf))
    with rig.sf() as db:
        row = db.get(Progress, (KEY, "position"))
        assert (row.state, row.reason) == ("unknown", "interrupted_consumer")
    assert rig.calls == []


@pytest.mark.parametrize("phase", ["claim", "finish"])
def test_cancelled_coroutine_does_not_claim_to_stop_inflight_database_write(rig, monkeypatch, phase):
    rig.seed("position")
    entered, release, drained = threading.Event(), threading.Event(), threading.Event()
    original = getattr(consumers, f"_{phase}")

    def tracked(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            drained.set()

    def before_cursor(conn, cursor, statement, parameters, context, executemany):
        if _matches(phase, statement):
            entered.set()
            assert release.wait(timeout=3), "isolated database worker was not released"

    monkeypatch.setattr(consumers, f"_{phase}", tracked)
    event.listen(rig.engine, "before_cursor_execute", before_cursor)

    async def cancel_at_write():
        task = asyncio.create_task(consumers.consume_pending(None, rig.sf))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not drained.is_set(), "coroutine cancellation must not pretend the worker stopped"
        finally:
            release.set()
            assert await asyncio.to_thread(drained.wait, 3)

    try:
        asyncio.run(cancel_at_write())
    finally:
        release.set()
        event.remove(rig.engine, "before_cursor_execute", before_cursor)
    with rig.sf() as db:
        row = db.get(Progress, (KEY, "position"))
        assert row.attempts == 1
        if phase == "claim":
            assert row.state == "running" and rig.calls == [] and json.loads(row.payload) == rig.payload
        else:
            assert row.state == "done" and len(rig.calls) == 1
            assert json.loads(row.payload)["execution_result"]["order_id"] == 42
    asyncio.run(consumers.consume_pending(None, rig.sf))
    assert len(rig.calls) == (1 if phase == "finish" else 0)


def test_two_consumers_that_read_same_pending_key_only_execute_one_action(rig, monkeypatch):
    rig.seed("position")
    original = consumers._pending_keys
    both_read = threading.Barrier(2, timeout=3)
    read_keys = []

    def both_pending(*args, **kwargs):
        keys = original(*args, **kwargs)
        read_keys.append(keys)
        both_read.wait()
        return keys

    monkeypatch.setattr(consumers, "_pending_keys", both_pending)

    async def concurrent():
        await asyncio.gather(consumers.consume_pending(None, rig.sf), consumers.consume_pending(None, rig.sf))

    asyncio.run(concurrent())
    assert read_keys == [[(KEY, "position")], [(KEY, "position")]] and len(rig.calls) == 1
    with rig.sf() as db:
        row = db.get(Progress, (KEY, "position"))
        assert (row.state, row.attempts) == ("done", 1)
        assert json.loads(row.payload)["execution_result"]["order_id"] == 42
