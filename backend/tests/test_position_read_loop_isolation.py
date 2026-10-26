"""Real SQLite lock waits must not suspend unrelated async market work."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
import sqlite3
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_hub
from app.api.routes import real_position
from app.models.paper import PaperPosition
from app.models.alert import AlertEvent, AlertRule
from app.models.notification_outbox import NotificationOutbox
from app.models.real_position import RealPositionOverride, RealTrade
from app.picks import exit_engine
from app.picks import position_engine


@pytest.fixture
def locked_positions(tmp_path):
    path = tmp_path / "position-read.sqlite3"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 0.6})
    for table in (RealTrade.__table__, RealPositionOverride.__table__, PaperPosition.__table__,
                  AlertRule.__table__, AlertEvent.__table__, NotificationOutbox.__table__):
        table.create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add(RealTrade(symbol="600000", name="fixture", side="buy", fill_price=10, quantity=100, fee=0))
        db.add_all([
            PaperPosition(scope="main", symbol="600000", quantity=100, frozen_today=50, cost_price=10, buy_date="20261023"),
            PaperPosition(scope="shadow", symbol="000001", quantity=200, frozen_today=0, cost_price=20),
            PaperPosition(scope="main", symbol="600001", quantity=0, frozen_today=0, cost_price=10),
        ])
        db.commit()
    started = threading.Event()
    notify_started = threading.Event()
    notify_finished = threading.Event()
    owners, closed = [], []

    @event.listens_for(engine, "before_cursor_execute")
    def observed(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            started.set()
            if "FROM alert_rule" in statement:
                notify_started.set()

    @event.listens_for(engine, "after_cursor_execute")
    def finished(_conn, _cursor, statement, _params, _context, _many):
        if "FROM alert_rule" in statement:
            notify_finished.set()

    @event.listens_for(engine, "handle_error")
    def failed(context):
        if "FROM alert_rule" in (context.statement or ""):
            notify_finished.set()

    @contextmanager
    def factory():
        owners.append(threading.get_ident())
        with sessions() as db:
            try:
                yield db
            finally:
                closed.append(threading.get_ident())

    blocker = sqlite3.connect(path)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        yield SimpleNamespace(factory=factory, blocker=blocker, started=started, notify_started=notify_started,
                              notify_finished=notify_finished, owners=owners, closed=closed, sessions=sessions)
    finally:
        blocker.rollback()
        blocker.close()
        engine.dispose()


async def _prove_loop_can_reply(started, reading):
    assert await asyncio.to_thread(started.wait, 2), "the real SQLite SELECT was not attempted"
    api = FastAPI()

    @api.get("/heartbeat")
    async def heartbeat():
        return {"ready": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://isolated") as client:
        response = await client.get("/heartbeat")
    assert response.json() == {"ready": True}
    # The lock remains held here. A completed read means the event loop could
    # only reply after SQLite's timeout, which is the production regression.
    assert not reading.done(), "unrelated async work waited for the SQLite lock timeout"


@pytest.mark.parametrize("release_before_timeout", [True, False])
def test_real_positions_sqlite_wait_keeps_other_async_requests_alive(locked_positions, monkeypatch,
                                                                   release_before_timeout):
    lock = locked_positions
    monkeypatch.setattr(real_position, "_sf", lock.factory)
    loop_thread = threading.get_ident()

    async def run():
        api = FastAPI()
        api.include_router(real_position.router, prefix="/api")
        api.dependency_overrides[get_hub] = lambda: SimpleNamespace()

        async def no_quotes(_hub, symbols):
            assert symbols == ["600000"]
            assert len(lock.closed) == len(lock.owners)  # Session closed before quote await.
            return {}

        monkeypatch.setattr(real_position, "_quotes_for", no_quotes)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://isolated") as client:
            reading = asyncio.create_task(client.get("/api/real/positions"))
            try:
                await _prove_loop_can_reply(lock.started, reading)
                if release_before_timeout:
                    lock.blocker.rollback()
                    response = await reading
                    assert response.status_code == 200
                    item = response.json()["data"]["items"][0]
                    assert item["symbol"] == "600000" and item["quantity"] == 100
                    assert item["avg_cost"] == 10 and item["last_price"] == 10
                else:
                    with pytest.raises(OperationalError, match="database is locked"):
                        await reading
            finally:
                lock.blocker.rollback()
                if not reading.done():
                    await reading
                elif not reading.cancelled():
                    reading.exception()

    asyncio.run(run())
    assert lock.owners and all(owner != loop_thread for owner in lock.owners)
    assert lock.closed == lock.owners


@pytest.mark.parametrize("release_before_timeout", [True, False])
def test_exit_paper_position_wait_keeps_loop_alive_and_risk_order(locked_positions, monkeypatch,
                                                               release_before_timeout):
    lock = locked_positions
    order = []
    monkeypatch.setattr(exit_engine, "load_plan", lambda: {"peaks": {}, "exits": []})
    monkeypatch.setattr(exit_engine, "save_plan", lambda _plan: order.append("saved"))
    monkeypatch.setattr(exit_engine, "_real_positions", lambda: {})
    monkeypatch.setattr(exit_engine, "_picks_combos", lambda: {})
    monkeypatch.setattr(exit_engine, "_notify", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(exit_engine, "_PAPER_READ", {"state": "unknown", "failures": 0})
    monkeypatch.setattr(exit_engine, "_REAL_READ", {"state": "empty", "failures": 0})
    monkeypatch.setattr(exit_engine, "_NOTIFIED", set())

    async def settle():
        assert not lock.started.is_set()
        order.append("settled")

    async def forbidden_order(*_args, **_kwargs):
        pytest.fail("a read lock or missing quote must never create an order")

    engine = SimpleNamespace(_sf=lock.factory, scope="main", settle_t1=settle, place_order=forbidden_order)
    state = SimpleNamespace(paper=engine, snapshot_service=None)
    loop_thread = threading.get_ident()

    async def run():
        reading = asyncio.create_task(exit_engine.evaluate_once(state))
        try:
            await _prove_loop_can_reply(lock.started, reading)
            if release_before_timeout:
                lock.blocker.rollback()
            fired = await reading
            if release_before_timeout:
                assert fired == []
                assert exit_engine._PAPER_READ["state"] == "ok"
            else:
                assert len(fired) == 1 and fired[0]["action"] == "degraded"
                assert exit_engine._PAPER_READ["state"] == "failed"
                assert "database is locked" in exit_engine._PAPER_READ["reason"]
        finally:
            lock.blocker.rollback()
            if not reading.done():
                await reading
            elif not reading.cancelled():
                reading.exception()

    asyncio.run(run())
    assert order == ["settled", "saved"]
    assert lock.owners and all(owner != loop_thread for owner in lock.owners)
    assert lock.closed == lock.owners


def test_paper_read_keeps_scope_positive_positions_and_t1_available(locked_positions):
    lock = locked_positions
    lock.blocker.rollback()
    positions = asyncio.run(asyncio.to_thread(exit_engine._read_paper_positions, lock.factory, "main"))
    assert positions == [{"symbol": "600000", "quantity": 100, "available": 50,
                          "cost_price": 10, "buy_date": "20261023"}]
    assert lock.closed == lock.owners


def test_exit_real_position_wait_is_off_loop_and_keeps_read_failure_visible(locked_positions, monkeypatch):
    from app.core import db as core_db

    lock = locked_positions
    monkeypatch.setattr(core_db, "get_session_factory", lambda: lock.factory)
    monkeypatch.setattr(exit_engine, "_read_paper_positions", lambda *_args: [])
    monkeypatch.setattr(exit_engine, "load_plan", lambda: {"peaks": {}, "exits": []})
    monkeypatch.setattr(exit_engine, "save_plan", lambda _plan: None)
    monkeypatch.setattr(exit_engine, "_picks_combos", lambda: {})
    monkeypatch.setattr(exit_engine, "_notify", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(exit_engine, "_PAPER_READ", {"state": "unknown", "failures": 0})
    monkeypatch.setattr(exit_engine, "_REAL_READ", {"state": "unknown", "failures": 0})
    monkeypatch.setattr(exit_engine, "_NOTIFIED", set())

    async def settle():
        return None

    state = SimpleNamespace(paper=SimpleNamespace(_sf=lock.factory, scope="main", settle_t1=settle),
                            snapshot_service=None)
    loop_thread = threading.get_ident()

    async def run():
        reading = asyncio.create_task(exit_engine.evaluate_once(state))
        try:
            await _prove_loop_can_reply(lock.started, reading)
            fired = await reading
            assert any(item["action"] == "degraded" for item in fired)
            assert exit_engine._REAL_READ["state"] == "failed"
            assert "database is locked" in exit_engine._REAL_READ["reason"]
        finally:
            lock.blocker.rollback()
            if not reading.done():
                await reading
            elif not reading.cancelled():
                reading.exception()

    asyncio.run(run())
    assert lock.owners and all(owner != loop_thread for owner in lock.owners)
    assert lock.closed == lock.owners


def test_degraded_notification_second_sqlite_wait_keeps_loop_alive_and_failure_retryable(locked_positions,
                                                                                      monkeypatch):
    from app.picks import source_events

    lock = locked_positions
    monkeypatch.setattr(source_events, "get_session_factory", lambda: lock.factory)
    monkeypatch.setattr(exit_engine, "load_plan", lambda: {"peaks": {}, "exits": []})
    monkeypatch.setattr(exit_engine, "save_plan", lambda _plan: None)
    monkeypatch.setattr(exit_engine, "_real_positions", lambda: {})
    monkeypatch.setattr(exit_engine, "_picks_combos", lambda: {})
    monkeypatch.setattr(exit_engine, "_PAPER_READ", {"state": "unknown", "failures": 0})
    monkeypatch.setattr(exit_engine, "_REAL_READ", {"state": "empty", "failures": 0})
    monkeypatch.setattr(exit_engine, "_NOTIFIED", set())

    async def settle():
        return None

    state = SimpleNamespace(paper=SimpleNamespace(_sf=lock.factory, scope="main", settle_t1=settle),
                            snapshot_service=None)
    loop_thread = threading.get_ident()

    async def run():
        reading = asyncio.create_task(exit_engine.evaluate_once(state))
        try:
            # The first paper SELECT has already timed out. This is the second,
            # real AlertRule SELECT entered by the unchanged degraded notification.
            await _prove_loop_can_reply(lock.notify_started, reading)
            assert not lock.notify_finished.is_set(), "heartbeat waited for the degraded-notification SQL timeout"
            fired = await reading
            assert any(item["action"] == "degraded" for item in fired)
            assert exit_engine._PAPER_READ["state"] == "failed"
            assert not exit_engine._NOTIFIED  # Failed persistence must remain retryable.
        finally:
            lock.blocker.rollback()
            if not reading.done():
                await reading
            elif not reading.cancelled():
                reading.exception()

    asyncio.run(run())
    assert lock.owners and all(owner != loop_thread for owner in lock.owners)
    assert lock.closed == lock.owners


def test_degraded_notification_receipt_retries_same_event_before_notified(locked_positions, monkeypatch):
    from app.picks import morning_brief, source_events

    lock = locked_positions
    lock.blocker.rollback()
    monkeypatch.setattr(source_events, "get_session_factory", lambda: lock.factory)
    monkeypatch.setattr(exit_engine, "load_plan", lambda: {"peaks": {}, "exits": []})
    monkeypatch.setattr(exit_engine, "save_plan", lambda _plan: None)
    monkeypatch.setattr(exit_engine, "_real_positions", lambda: {})
    monkeypatch.setattr(exit_engine, "_picks_combos", lambda: {})
    monkeypatch.setattr(exit_engine, "_REAL_READ", {"state": "empty", "failures": 0})
    monkeypatch.setattr(exit_engine, "_PAPER_READ", {"state": "unknown", "failures": 0})
    monkeypatch.setattr(exit_engine, "_NOTIFIED", set())
    projection = {"ok": False, "calls": []}

    def missing(_factory, _scope):
        raise RuntimeError("fixture read unavailable")

    def append(_date, alert):
        assert not exit_engine._NOTIFIED  # Loop must wait for the actual receipt.
        projection["calls"].append((threading.get_ident(), alert["meta"]["event_id"]))
        return projection["ok"]

    monkeypatch.setattr(exit_engine, "_read_paper_positions", missing)
    monkeypatch.setattr(morning_brief, "append_alert", append)
    monkeypatch.setattr(morning_brief, "load_brief", lambda _date: {"alerts": []})

    async def settle():
        return None

    state = SimpleNamespace(paper=SimpleNamespace(_sf=lock.factory, scope="main", settle_t1=settle),
                            snapshot_service=None)
    loop_thread = threading.get_ident()

    async def run():
        first = await exit_engine.evaluate_once(state)
        assert first[0]["action"] == "degraded" and not exit_engine._NOTIFIED
        projection["ok"] = True
        second = await exit_engine.evaluate_once(state)
        assert second[0]["action"] == "degraded" and len(exit_engine._NOTIFIED) == 1
        await exit_engine.evaluate_once(state)
        assert len(projection["calls"]) == 2

    asyncio.run(run())
    with lock.sessions() as db:
        events = db.query(AlertEvent).all()
        assert len(events) == 1
        assert json.loads(events[0].snapshot)["brief_projection"] == "completed"
        assert db.query(NotificationOutbox).count() == 0  # Original SILENT policy.
    assert len({receipt for _owner, receipt in projection["calls"]}) == 1
    assert all(owner != loop_thread for owner, _receipt in projection["calls"])


def test_filled_exit_projection_finishes_before_plan_save_without_reselling(locked_positions, tmp_path, monkeypatch):
    from app.picks import morning_brief, source_events

    lock = locked_positions
    lock.blocker.rollback()
    monkeypatch.setattr(source_events, "get_session_factory", lambda: lock.factory)
    monkeypatch.setattr(morning_brief, "append_alert", lambda *_args: True)
    monkeypatch.setattr(exit_engine, "_real_positions", lambda: {})
    monkeypatch.setattr(exit_engine, "_picks_combos", lambda: {})
    monkeypatch.setattr(exit_engine, "_PAPER_READ", {"state": "unknown", "failures": 0})
    monkeypatch.setattr(exit_engine, "_REAL_READ", {"state": "empty", "failures": 0})
    monkeypatch.setattr(exit_engine, "_NOTIFIED", set())
    monkeypatch.setattr(position_engine, "_PLAN_DIR", tmp_path / "filled-plans")
    plan = {"date": exit_engine.beijing_now().date().isoformat(), "peaks": {}, "exits": [], "decisions": []}
    position_engine.save_plan(plan)
    saved, orders = [], []

    def save(value):
        position_engine.save_plan(value)
        saved.append(deepcopy(position_engine.load_plan()))

    monkeypatch.setattr(exit_engine, "save_plan", save)

    async def settle():
        return None

    async def place(symbol, side, price, quantity):
        orders.append((symbol, side, price, quantity))
        return SimpleNamespace(status="filled", id=114)

    state = SimpleNamespace(
        paper=SimpleNamespace(_sf=lock.factory, scope="main", settle_t1=settle, place_order=place),
        snapshot_service=SimpleNamespace(snapshot=[{"symbol": "600000", "price": 9, "change_pct": -6}]),
    )

    async def run():
        fired = await exit_engine.evaluate_once(state)
        assert any(row["action"] == "exit" for row in fired)
        assert await exit_engine.reconcile_paper_exit_events_async() == 0

    asyncio.run(run())
    assert orders == [("600000", "sell", 9, 50)]
    assert saved[0]["exits"][0]["order_id"] == 114 and not saved[0]["exits"][0].get("event_projected")
    assert saved[-1]["exits"][0]["order_id"] == 114
    assert saved[-1]["exits"][0]["event_projected"] is True
    with lock.sessions() as db:
        assert db.query(AlertEvent).count() == 1
        assert db.query(NotificationOutbox).count() == 0


@pytest.mark.parametrize("window", ["read", "notification"])
def test_new_async_wait_does_not_overwrite_other_plan_writer(locked_positions, tmp_path, monkeypatch, window):
    lock = locked_positions
    monkeypatch.setattr(position_engine, "_PLAN_DIR", tmp_path / "plans")
    day = exit_engine.beijing_now().date().isoformat()
    position_engine.save_plan({"date": day, "peaks": {}, "decisions": [], "exits": []})
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(exit_engine, "_real_positions", lambda: {})
    monkeypatch.setattr(exit_engine, "_picks_combos", lambda: {})
    monkeypatch.setattr(exit_engine, "_PAPER_READ", {"state": "unknown", "failures": 0})
    monkeypatch.setattr(exit_engine, "_REAL_READ", {"state": "empty", "failures": 0})
    monkeypatch.setattr(exit_engine, "_NOTIFIED", set())

    def notify(*_args, **_kwargs):
        entered.set()
        assert release.wait(3)
        return True

    if window == "notification":
        def missing(*_args):
            raise RuntimeError("fixture read failed")
        monkeypatch.setattr(exit_engine, "_read_paper_positions", missing)
        monkeypatch.setattr(exit_engine, "_notify", notify)
    else:
        monkeypatch.setattr(exit_engine, "_notify", lambda *_args, **_kwargs: False)

    async def settle():
        return None

    state = SimpleNamespace(paper=SimpleNamespace(_sf=lock.factory, scope="main", settle_t1=settle),
                            snapshot_service=None)

    async def run():
        reading = asyncio.create_task(exit_engine.evaluate_once(state))
        try:
            event = lock.started if window == "read" else entered
            assert await asyncio.to_thread(event.wait, 2)
            assert not reading.done()
            other = position_engine.load_plan()
            other["decisions"].append({"action": "open", "symbol": "000001", "order_id": 200})
            other["peaks"]["000001"] = 23.5
            position_engine.save_plan(other)
            lock.blocker.rollback()
            release.set()
            await reading
        finally:
            lock.blocker.rollback()
            release.set()
            if not reading.done():
                await reading
            elif not reading.cancelled():
                reading.exception()

    asyncio.run(run())
    final = position_engine.load_plan()
    assert final["decisions"] == [{"action": "open", "symbol": "000001", "order_id": 200}]
    assert final["peaks"] == {"000001": 23.5}


def test_cancel_after_filled_order_drains_failed_projection_and_keeps_order(tmp_path, locked_positions, monkeypatch):
    lock = locked_positions
    lock.blocker.rollback()
    monkeypatch.setattr(position_engine, "_PLAN_DIR", tmp_path / "cancel-plans")
    day = exit_engine.beijing_now().date().isoformat()
    position_engine.save_plan({"date": day, "peaks": {}, "decisions": [], "exits": []})
    monkeypatch.setattr(exit_engine, "_real_positions", lambda: {})
    monkeypatch.setattr(exit_engine, "_picks_combos", lambda: {})
    monkeypatch.setattr(exit_engine, "_PAPER_READ", {"state": "unknown", "failures": 0})
    monkeypatch.setattr(exit_engine, "_REAL_READ", {"state": "empty", "failures": 0})
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    orders = []

    def failing_publish(_plan, _row):
        entered.set()
        try:
            assert release.wait(3)
            raise RuntimeError("fixture publisher unavailable after fill")
        finally:
            finished.set()

    monkeypatch.setattr(exit_engine, "_publish_paper_exit", failing_publish)

    async def settle():
        return None

    async def place(*args):
        orders.append(args)
        return SimpleNamespace(status="filled", id=114)

    state = SimpleNamespace(
        paper=SimpleNamespace(_sf=lock.factory, scope="main", settle_t1=settle, place_order=place),
        snapshot_service=SimpleNamespace(snapshot=[{"symbol": "600000", "price": 9, "change_pct": -6}]),
    )

    async def run():
        reading = asyncio.create_task(exit_engine.evaluate_once(state))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert position_engine.load_plan()["exits"][0]["order_id"] == 114
            reading.cancel()
            await asyncio.sleep(0)
            assert not reading.done(), "cancellation claimed completion while publisher was still running"
            reading.cancel()  # Repeated cancellation must not kill the worker or skip its drain.
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await reading
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)
            if not reading.done():
                try:
                    await reading
                except asyncio.CancelledError:
                    pass
            elif not reading.cancelled():
                reading.exception()

    asyncio.run(run())
    assert len(orders) == 1
    final = position_engine.load_plan()
    assert final["exits"][0]["order_id"] == 114
    assert not final["exits"][0].get("event_projected")
