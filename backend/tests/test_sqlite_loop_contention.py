"""File-backed SQLite contention: real driver locks, isolated sessions, no network."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.market.alert_engine import AlertEngine
from app.models.notification_outbox import NotificationOutbox
from app.models.paper import PaperPosition
from app.models.watchlist import Base
from app.paper.engine import PaperTradingEngine
from app.repositories.alert_repo import AlertRepository
from app.repositories.watchlist_repo import WatchlistRepository


@pytest.fixture
def sqlite_rig(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'contention.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    traces = []
    @event.listens_for(engine, "connect")
    def trace(conn, _):
        conn.set_trace_callback(lambda sql: traces.append({
            "sql": sql, "time": time.perf_counter(), "in_transaction": conn.in_transaction,
        }))
    # The seed connection predates the connect listener.
    with engine.connect() as conn:
        raw = conn.connection.driver_connection
        raw.set_trace_callback(lambda sql: traces.append({
            "sql": sql, "time": time.perf_counter(), "in_transaction": raw.in_transaction,
        }))
        journal = conn.scalar(text("PRAGMA journal_mode"))
        busy = conn.scalar(text("PRAGMA busy_timeout"))
    yield engine, sf, traces, {"journal_mode": journal, "busy_timeout_ms": busy}
    engine.dispose()


def _receipt(name, data):
    # Opt-in ignored evidence path; normal CI keeps artifacts inside tmp only.
    import os
    output = os.environ.get("ASHARE_CONTENTION_EVIDENCE_DIR")
    if output:
        path = Path(output)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{name}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.mark.parametrize("path", ["settle_t1", "first_account_order"])
def test_calendar_wait_does_not_hold_a_real_sqlite_write_lock(sqlite_rig, monkeypatch, path):
    import app.paper.engine as paper
    from app.schemas.market import Quote
    engine, sf, traces, settings = sqlite_rig
    monkeypatch.setattr(paper, "_today", lambda: "20261014")
    with sf() as db:
        db.add(PaperPosition(scope="main", symbol="600000", quantity=100,
                             frozen_today=100, cost_price=10, buy_date="20261013"))
        db.commit()
    seen = {}
    workers = []
    async def calendar():
        # A separate independent Session must really BEGIN/UPDATE/COMMIT while
        # the calendar owner is suspended; a logical Session flag is insufficient.
        seen["driver_transaction_at_calendar"] = traces[-1]["in_transaction"]
        def competing_writer():
            started = time.perf_counter()
            with sf() as db:
                db.execute(text("BEGIN IMMEDIATE"))
                db.execute(text("UPDATE paper_position SET cost_price=10.1 WHERE symbol='600000'"))
                db.commit()
            return time.perf_counter() - started
        worker = asyncio.create_task(asyncio.to_thread(competing_writer))
        workers.append(worker)
        try:
            seen["writer_seconds"] = await asyncio.wait_for(asyncio.shield(worker), .15)
            seen["writer_blocked_during_calendar"] = False
        except asyncio.TimeoutError:
            seen["writer_blocked_during_calendar"] = True
        return ["20261013", "20261014"]
    async def quote(_symbol):
        return Quote(symbol="600000", price=10, limit_up_price=11, limit_down_price=9, source="offline")
    paper_engine = PaperTradingEngine(sf, quote, calendar)
    async def run():
        if path == "settle_t1":
            assert await paper_engine.settle_t1() == 1
        else:
            order = await paper_engine.place_order("600000", "sell", 10, 100)
            assert order.status == "filled", order.reason
        await asyncio.gather(*workers)
    asyncio.run(run())
    _receipt(f"calendar-{path}", {**settings, **seen, "trace": traces})
    assert seen["writer_blocked_during_calendar"] is False
    assert seen["driver_transaction_at_calendar"] is False
    assert any(row["sql"] == "BEGIN IMMEDIATE" for row in traces)
    assert any(row["sql"] == "COMMIT" for row in traces)


@pytest.mark.parametrize("stage", ["reconcile", "claim", "finish"])
def test_alert_sqlite_lock_wait_keeps_event_loop_responsive(sqlite_rig, monkeypatch, stage):
    engine, sf, traces, settings = sqlite_rig
    repo = AlertRepository(sf)
    rule = repo.create_rule(name="offline", channels=["feishu"], enabled=True)
    service = AlertEngine(repo, WatchlistRepository(sf))
    now = service._now_ms()
    expires = now - 1 if stage == "reconcile" else now + 60_000
    repo.record_trigger(rule.id, "600000", 11, 10, outbox_target="offline", now_ms=now, expires_at_ms=expires)
    monkeypatch.setattr(service, "_delivery_block", lambda *_: None)
    lock_ready = Event()
    lock_done = Event()
    timings = {}
    def lock_writer():
        with sf() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            db.execute(text("UPDATE alert_rule SET name=name"))
            lock_ready.set()
            time.sleep(.25)
            db.commit()
        lock_done.set()
    pool = ThreadPoolExecutor(max_workers=1)
    def start_lock():
        future = pool.submit(lock_writer)
        assert lock_ready.wait(2)
        return future
    sent = []
    async def send_result(*_):
        sent.append(True)
        if stage == "finish":
            start_lock()
        return SimpleNamespace(outcome="accepted", reason="offline_accepted")
    service._registry = SimpleNamespace(get=lambda _channel: SimpleNamespace(send_result=send_result))
    if stage != "finish":
        start_lock()
    async def run():
        beats = []
        finished = False
        async def heartbeat():
            while not finished:
                beats.append(time.perf_counter())
                await asyncio.sleep(.01)
        task = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        started = time.perf_counter()
        try:
            await service._deliver_pending()
        finally:
            timings["delivery_seconds"] = time.perf_counter() - started
            finished = True
            await task
            timings["heartbeat_count"] = len(beats)
            timings["heartbeat_during_lock"] = sum(started <= beat <= started + .20 for beat in beats)
    try:
        asyncio.run(run())
    finally:
        pool.shutdown(wait=True)
    with sf() as db:
        state = db.query(NotificationOutbox).one().state
    _receipt(f"alert-{stage}", {**settings, **timings, "state": state, "sent_count": len(sent), "trace": traces})
    assert lock_done.is_set()
    assert state == ("expired" if stage == "reconcile" else "accepted")
    assert len(sent) == (0 if stage == "reconcile" else 1)
    assert timings["heartbeat_during_lock"] >= 5


@pytest.mark.parametrize("stage", ["claim", "finish"])
def test_cancel_waits_for_real_sqlite_transaction_before_returning(sqlite_rig, monkeypatch, stage):
    _engine, sf, traces, settings = sqlite_rig
    repo = AlertRepository(sf)
    rule = repo.create_rule(name="offline-cancel", channels=["feishu"], enabled=True)
    service = AlertEngine(repo, WatchlistRepository(sf))
    now = service._now_ms()
    repo.record_trigger(rule.id, "600000", 11, 10, outbox_target="offline", now_ms=now,
                        expires_at_ms=now + 300_000)
    monkeypatch.setattr(service, "_delivery_block", lambda *_: None)
    ready, release, entered, drained = Event(), Event(), Event(), Event()
    sent = []
    def hold_writer():
        with sf() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            db.execute(text("UPDATE alert_rule SET name=name"))
            ready.set()
            assert release.wait(3), "test did not release its SQLite lock"
            db.commit()
    pool = ThreadPoolExecutor(max_workers=1)
    def lock():
        pool.submit(hold_writer)
        assert ready.wait(2)
    method = getattr(repo.outbox, stage)
    def real_transaction(*args):
        entered.set()
        try:
            return method(*args)
        finally:
            drained.set()
    monkeypatch.setattr(repo.outbox, stage, real_transaction)
    async def send_result(*_):
        sent.append(True)
        if stage == "finish":
            lock()
        return SimpleNamespace(outcome="accepted", reason="offline_accepted")
    service._registry = SimpleNamespace(get=lambda _channel: SimpleNamespace(send_result=send_result))
    if stage == "claim":
        lock()
    observed = {}
    async def run():
        task = asyncio.create_task(service._deliver_pending())
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(.001)
            task.cancel()
            await asyncio.sleep(.02)
            observed["first_cancel_returned_before_drain"] = task.done()
            task.cancel()
            await asyncio.sleep(.02)
            observed["second_cancel_returned_before_drain"] = task.done()
            assert not drained.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert drained.is_set()
    try:
        asyncio.run(run())
    finally:
        release.set()
        pool.shutdown(wait=True)
    with sf() as db:
        row = db.query(NotificationOutbox).one()
        observed["state_after_cancel"] = row.state
        observed["send_started"] = row.send_started_at_ms is not None
    _receipt(f"cancel-{stage}", {**settings, **observed, "sent_count": len(sent), "trace": traces})
    assert observed["first_cancel_returned_before_drain"] is False
    assert observed["second_cancel_returned_before_drain"] is False
    if stage == "claim":
        assert row.state == "leased" and row.send_started_at_ms is None and not sent
        repo.outbox.reconcile(row.lease_until_ms + 1)
        with sf() as db:
            assert db.query(NotificationOutbox).one().state == "pending"
    else:
        assert row.state == "accepted" and row.send_started_at_ms is not None and len(sent) == 1
        asyncio.run(service._deliver_pending())
        assert len(sent) == 1, "accepted delivery must not be retried after cancellation"


def test_begin_send_lock_wait_cannot_send_an_intent_that_expired_while_waiting(sqlite_rig, monkeypatch):
    _engine, sf, traces, settings = sqlite_rig
    repo = AlertRepository(sf)
    rule = repo.create_rule(name="offline-expiry", channels=["feishu"], enabled=True)
    service = AlertEngine(repo, WatchlistRepository(sf))
    now = service._now_ms()
    repo.record_trigger(rule.id, "600000", 11, 10, outbox_target="offline", now_ms=now,
                        expires_at_ms=now + 150)
    ready = Event()
    sent = []
    pool = ThreadPoolExecutor(max_workers=1)
    def hold_writer():
        with sf() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            db.execute(text("UPDATE alert_rule SET name=name"))
            ready.set()
            time.sleep(.25)
            db.commit()
    def recheck(*_):
        if not ready.is_set():
            pool.submit(hold_writer)
            assert ready.wait(2)
        return None
    monkeypatch.setattr(service, "_delivery_block", recheck)
    async def send_result(*_):
        sent.append(service._now_ms())
        return SimpleNamespace(outcome="accepted", reason="offline_accepted")
    service._registry = SimpleNamespace(get=lambda _channel: SimpleNamespace(send_result=send_result))
    try:
        asyncio.run(service._deliver_pending())
    finally:
        pool.shutdown(wait=True)
    with sf() as db:
        row = db.query(NotificationOutbox).one()
    _receipt("begin-send-expired-in-lock", {**settings, "expires_at_ms": row.expires_at_ms,
        "sent_at_ms": sent, "state": row.state, "reason": row.reason, "trace": traces})
    assert not sent
    assert row.state == "expired" and row.reason == "send_window_closed"


@pytest.mark.parametrize("change, expected", [
    ("source_age", "quote_not_fresh"),
    ("rule_disabled", "event_or_rule_removed_or_disabled"),
    ("condition_lost", "condition_no_longer_met"),
    ("source_unavailable", "source_recheck_unavailable"),
    ("expired_and_source_age", "send_window_closed"),
    ("unchanged", "offline_accepted"),
])
def test_begin_send_rechecks_current_eligibility_after_real_sqlite_lock(sqlite_rig, monkeypatch, change, expected):
    import app.core.freshness as freshness

    engine, sf, traces, settings = sqlite_rig
    repo = AlertRepository(sf)
    source_event = change == "source_unavailable"
    rule = repo.create_rule(
        name="__source_system_health_anomaly__" if source_event else "offline-recheck",
        condition_type="source_event" if source_event else "price_above",
        channels=["feishu"], enabled=True, scope="all" if source_event else "symbols",
        symbols=["600000"], threshold=10)
    service = AlertEngine(repo, WatchlistRepository(sf))
    clock = [datetime.fromisoformat("2026-10-14T09:35:09+08:00")]
    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz is not None else clock[0].replace(tzinfo=None)
    monkeypatch.setattr(freshness, "datetime", ClockDateTime)
    monkeypatch.setattr(service, "_now_ms", lambda: int(clock[0].timestamp() * 1000))
    monkeypatch.setattr("app.market.trade_calendar.in_trading_window", lambda: True)
    service._fresh_within = 10
    quote = {"symbol": "600000", "price": 11, "source": "offline", "quality": "high",
             "data_timestamp": clock[0] - timedelta(seconds=9)}
    service.update_quotes({"600000": quote})
    now = service._now_ms()
    if source_event:
        snapshot = {"kind": "system_health_anomaly", "source_id": "offline-health",
                    "source_version": "v1", "trade_date": "20261014",
                    "health_resolution": {"state": "active", "active_issues": ["offline"]}}
        repo.record_trigger_once(
            rule.id, "600000", 11, 10, dedup_key="post-lock-source", snapshot=snapshot,
            outbox_target="offline", now_ms=now, expires_at_ms=now + 60_000,
            outbox_intent={"kind": "source_event", "source_kind": "system_health_anomaly",
                           "source_id": "offline-health", "source_version": "v1",
                           "trade_date": "20261014", "policy": "anomaly"})
    else:
        repo.record_trigger(rule.id, "600000", 11, 10, snapshot=quote,
                            outbox_target="offline", now_ms=now, expires_at_ms=now + 10_000)
    ready, release, send_update_waiting = Event(), Event(), Event()
    sent, checked = [], []
    original_check = service._delivery_block
    def check(*args):
        result = original_check(*args)
        checked.append(result)
        return result
    monkeypatch.setattr(service, "_delivery_block", check)
    @event.listens_for(engine, "before_cursor_execute")
    def begin_send_dispatched(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("UPDATE notification_outbox SET send_started_at_ms="):
            send_update_waiting.set()
    def hold_writer():
        with sf() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            sql = "UPDATE alert_rule SET enabled=0" if change == "rule_disabled" else "UPDATE alert_rule SET name=name"
            db.execute(text(sql))
            if source_event:
                db.execute(text("UPDATE alert_event SET snapshot=:snapshot"),
                           {"snapshot": json.dumps({**snapshot, "health_resolution": {}})})
            ready.set()
            assert release.wait(3), "test did not release its SQLite lock"
            db.commit()
    pool = ThreadPoolExecutor(max_workers=1)
    original_begin = repo.outbox.begin_send
    def begin_send(*args, **kwargs):
        pool.submit(hold_writer)
        assert ready.wait(2)
        return original_begin(*args, **kwargs)
    monkeypatch.setattr(repo.outbox, "begin_send", begin_send)
    async def send_result(*_):
        sent.append(service._now_ms())
        return SimpleNamespace(outcome="accepted", reason="offline_accepted")
    service._registry = SimpleNamespace(get=lambda _channel: SimpleNamespace(
        delivery_target=lambda: "offline", send_result=send_result))
    async def run():
        delivery = asyncio.create_task(service._deliver_pending())
        try:
            async with asyncio.timeout(2):
                while not send_update_waiting.is_set():
                    await asyncio.sleep(.001)
            assert checked == [None], "the first qualification must really pass before lock contention"
            # Advance only the business clock. The real SQLite writer and event
            # loop keep their own clocks, so the lock wait is not simulated.
            await asyncio.sleep(.03)
            if change in {"source_age", "expired_and_source_age"}:
                clock[0] += timedelta(seconds=11 if change == "expired_and_source_age" else 3)
            elif change == "condition_lost":
                service.update_quotes({"600000": {**quote, "price": 9}})
        finally:
            release.set()
        await delivery
    try:
        asyncio.run(run())
    finally:
        release.set()
        pool.shutdown(wait=True)
    with sf() as db:
        row = db.query(NotificationOutbox).one()
    _receipt(f"begin-send-recheck-{change}", {**settings, "checked": checked,
        "remaining_intent_ms": row.expires_at_ms - service._now_ms(),
        "sent_count": len(sent), "state": row.state, "reason": row.reason, "trace": traces})
    if change != "expired_and_source_age":
        assert row.expires_at_ms > service._now_ms(), "this must exercise source eligibility, not intent expiry"
    assert len(sent) == int(change == "unchanged")
    expected_state = ("expired" if change == "expired_and_source_age" else
                      "accepted" if change == "unchanged" else "leased" if source_event else "suppressed")
    assert row.state == expected_state
    assert row.reason == expected
    if source_event:
        assert row.send_started_at_ms is None, "unavailable recheck must retain a genuinely pre-send lease"
        with sf() as db:
            db.execute(text("UPDATE alert_event SET snapshot=:snapshot"), {"snapshot": json.dumps(snapshot)})
            db.commit()
        clock[0] += timedelta(seconds=31)
        repo.outbox.reconcile(service._now_ms())
        monkeypatch.setattr(repo.outbox, "begin_send", original_begin)
        asyncio.run(service._deliver_pending())
        with sf() as db:
            retried = db.query(NotificationOutbox).one()
        assert retried.state == "accepted" and len(sent) == 1


@pytest.mark.parametrize("outcome", ["reject", "raise", "accept"])
def test_begin_send_callback_preserves_conservative_recovery(sqlite_rig, outcome):
    _engine, sf, traces, settings = sqlite_rig
    repo = AlertRepository(sf)
    rule = repo.create_rule(name="offline-callback", channels=["feishu"], enabled=True)
    now = AlertEngine._now_ms()
    event_row = repo.record_trigger(rule.id, "600000", 11, 10, outbox_target="offline",
                                   now_ms=now, expires_at_ms=now + 300_000)
    lease = repo.outbox.claim(repo.outbox.pending_ids()[0], now)
    calls = []
    def check():
        # Real independent Sessions read the same SQLite file after the send
        # UPDATE's lock wait. They must not retain a cross-thread Session.
        assert repo.get_event(event_row.id).rule_id == repo.get_rule(rule.id).id
        calls.append(outcome)
        if outcome == "raise":
            raise ValueError("offline recheck failure")
        return outcome == "accept"
    if outcome == "raise":
        with pytest.raises(ValueError, match="offline recheck failure"):
            repo.outbox.begin_send(lease, now, pre_send_check=check)
    else:
        assert repo.outbox.begin_send(lease, now, pre_send_check=check) == (outcome == "accept")
    with sf() as db:
        row = db.query(NotificationOutbox).one()
    assert calls == [outcome]
    assert (row.send_started_at_ms is not None) == (outcome != "reject")
    if outcome == "reject":
        assert repo.outbox.defer(lease, "source_recheck_unavailable", now)
        repo.outbox.reconcile(now + 30_001)
    else:
        repo.outbox.reconcile(lease.lease_until_ms + 1)
    with sf() as db:
        state = db.query(NotificationOutbox).one().state
    _receipt(f"begin-send-callback-{outcome}", {**settings, "state": state, "trace": traces})
    assert state == ("pending" if outcome == "reject" else "unknown")


def test_cancel_drains_post_lock_recheck_and_keeps_rejected_send_unstarted(sqlite_rig, monkeypatch):
    _engine, sf, traces, settings = sqlite_rig
    repo = AlertRepository(sf)
    rule = repo.create_rule(name="offline-recheck-cancel", channels=["feishu"], enabled=True)
    service = AlertEngine(repo, WatchlistRepository(sf))
    now = service._now_ms()
    repo.record_trigger(rule.id, "600000", 11, 10, outbox_target="offline",
                        now_ms=now, expires_at_ms=now + 300_000)
    entered, release, drained = Event(), Event(), Event()
    checks, sent = [], []
    def check(*_):
        checks.append(True)
        if len(checks) == 1:
            return None
        entered.set()
        try:
            assert release.wait(3), "test did not release its post-lock recheck"
            return "source_recheck_unavailable"
        finally:
            drained.set()
    monkeypatch.setattr(service, "_delivery_block", check)
    async def send_result(*_):
        sent.append(True)
        return SimpleNamespace(outcome="accepted", reason="offline_accepted")
    service._registry = SimpleNamespace(get=lambda _channel: SimpleNamespace(send_result=send_result))
    observed = {}
    async def run():
        task = asyncio.create_task(service._deliver_pending())
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(.001)
            task.cancel()
            await asyncio.sleep(.02)
            observed["first_cancel_returned_before_drain"] = task.done()
            task.cancel()
            await asyncio.sleep(.02)
            observed["second_cancel_returned_before_drain"] = task.done()
            assert not drained.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert drained.is_set()
    asyncio.run(run())
    with sf() as db:
        row = db.query(NotificationOutbox).one()
    assert row.state == "leased" and row.send_started_at_ms is None and not sent
    repo.outbox.reconcile(row.lease_until_ms + 1)
    with sf() as db:
        assert db.query(NotificationOutbox).one().state == "pending"
    _receipt("begin-send-recheck-cancel", {**settings, **observed, "sent_count": len(sent), "trace": traces})
    assert observed == {"first_cancel_returned_before_drain": False,
                        "second_cancel_returned_before_drain": False}


def test_static_pool_presend_reads_cannot_erase_committed_send_boundary():
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    repo = AlertRepository(sf)
    rule = repo.create_rule(name="offline-static-pool", channels=["feishu"], enabled=True)
    now = AlertEngine._now_ms()
    triggered = repo.record_trigger(rule.id, "600000", 11, 10, outbox_target="offline",
                                    now_ms=now, expires_at_ms=now + 300_000)
    lease = repo.outbox.claim(repo.outbox.pending_ids()[0], now)
    def check():
        return repo.get_event(triggered.id) is not None and repo.get_rule(rule.id) is not None
    try:
        assert repo.outbox.begin_send(lease, now, pre_send_check=check)
        with sf() as db:
            started = db.query(NotificationOutbox).one().send_started_at_ms
        # Simulate losing the process after external I/O could have started.
        # This must never be reclaimed as an unsent intent on the shared connection.
        repo.outbox.reconcile(lease.lease_until_ms + 1)
        with sf() as db:
            state = db.query(NotificationOutbox).one().state
        _receipt("begin-send-static-pool", {"send_started_at_ms": started, "state": state})
        assert started == now
        assert state == "unknown"
    finally:
        engine.dispose()


def test_presend_rejection_cannot_reset_a_lease_reconciled_after_send_started(sqlite_rig):
    _engine, sf, traces, settings = sqlite_rig
    repo = AlertRepository(sf)
    rule = repo.create_rule(name="offline-lease-race", channels=["feishu"], enabled=True)
    now = AlertEngine._now_ms()
    repo.record_trigger(rule.id, "600000", 11, 10, outbox_target="offline",
                        now_ms=now, expires_at_ms=now + 300_000)
    lease = repo.outbox.claim(repo.outbox.pending_ids()[0], now)
    def rejected_after_other_owner_reconciles():
        # Fault injection at the callback boundary: another reconciler has
        # already conservatively finalized this committed started lease.
        repo.outbox.reconcile(lease.lease_until_ms + 1)
        return False
    with pytest.raises(RuntimeError, match="pre-send recheck lease changed"):
        repo.outbox.begin_send(lease, now, pre_send_check=rejected_after_other_owner_reconciles)
    with sf() as db:
        row = db.query(NotificationOutbox).one()
    _receipt("begin-send-recheck-lease-race", {**settings, "state": row.state,
        "send_started_at_ms": row.send_started_at_ms, "trace": traces})
    assert row.state == "unknown" and row.send_started_at_ms == now
    assert repo.outbox.pending_ids() == []
