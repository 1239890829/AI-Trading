"""Bounded read-side checks: no Session spans live awaits or blocks the loop."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
import json
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import picks as route
from app.models.daily_pick import DailyPickSet
from app.models.opportunity_learning import OpportunityDecisionSnapshot
from app.models.watchlist import Base

TODAY = date(2026, 10, 13)
REQUEST = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
HUB = object()


def _database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'today-picks.db'}")
    Base.metadata.create_all(engine)
    seed = sessionmaker(bind=engine)
    state = {"active": 0, "created": [], "closed": []}

    class TrackedSession(Session):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            state["active"] += 1
            state["created"].append(threading.get_ident())

        def close(self):
            super().close()
            state["active"] -= 1
            state["closed"].append(threading.get_ident())

    factory = sessionmaker(bind=engine, class_=TrackedSession)
    monkeypatch.setattr(route, "get_session_factory", lambda: factory)
    monkeypatch.setattr(route, "beijing_today", lambda: TODAY)
    return engine, seed, state


def _insert(seed, day, *, symbol="600127", meta=None, replaced=None):
    items = [{"symbol": symbol, "price": 7.5, "change_pct": 10.0}]
    meta = meta if meta is not None else {"weights": {"event": .2}}
    with seed() as db:
        db.add(DailyPickSet(date=day, items=json.dumps(items), meta=json.dumps(meta),
                            replaced=json.dumps(replaced or []), rejected="[]"))
        db.commit()
    return items, meta


def _live_stubs(monkeypatch, state, calls):
    async def style(request, hub, stored):
        assert state["active"] == 0, "DB Session remained open across live style await"
        calls.append(("style", stored))
        await asyncio.sleep(0)
        return {"phase_source": "live", "routed": True}

    async def gate(request, hub, stored):
        assert state["active"] == 0, "DB Session remained open across live gate await"
        calls.append(("gate", stored))
        await asyncio.sleep(0)
        return {"gate_source": "unavailable", "gate_note": "test unavailable"}

    monkeypatch.setattr(route, "_live_style_routing", style)
    monkeypatch.setattr(route, "_live_gate", gate)


@pytest.mark.parametrize("kind", ["iso", "compact", "fallback", "none"])
def test_today_read_preserves_dates_meta_entries_and_live_order(tmp_path, monkeypatch, kind):
    engine, seed, state = _database(tmp_path, monkeypatch)
    calls = []
    _live_stubs(monkeypatch, state, calls)
    original_gate = {"stand_aside": True, "reason": "stored condition"}
    entry = {"state": "partial", "selected_at": "2026-10-13T09:32:00+08:00",
             "reference_price": 7.5, "reference_change_pct": 10.0,
             "quote_as_of": None, "reason": "source clock missing"}
    meta = {"gate": original_gate, "style_routing": {"phase_source": "stored"},
            "selection_entries": {"600127": entry}, "extra": {"unknown": None}}
    day = {"iso": "2026-10-13", "compact": "20261013", "fallback": "20261012"}.get(kind)
    if day:
        _insert(seed, day, meta=meta, replaced=[{"out": "000001", "in": "600127"}])
    # Future sets never substitute for a current or historical set.
    _insert(seed, "2026-10-14", symbol="000002")
    if kind == "iso":
        _insert(seed, "20261013", symbol="000003")
    try:
        result = asyncio.run(route.today_picks(REQUEST, HUB))
        data = result["data"]
        assert state["active"] == 0
        assert all(thread != threading.get_ident() for thread in state["created"])
        assert state["created"] == state["closed"]
        if kind == "none":
            assert data["date"] is None and data["items"] == [] and data["meta"] is None
            assert calls == []
            return
        assert data["date"] == ("2026-10-12" if kind == "fallback" else "2026-10-13")
        assert data["items"][0]["symbol"] == "600127"
        assert data["items"][0]["selection_entry"] == entry
        assert data["items"][0]["execution"] is None
        assert data["meta"]["extra"] == {"unknown": None}
        assert data["meta"]["gate"] == {**original_gate, "gate_source": "stored"}
        assert data["meta"]["gate_live"]["gate_source"] == "unavailable"
        assert [name for name, _ in calls] == ["style", "gate"]
        if kind == "fallback":
            assert data["stale"] is True and "replaced" not in data
        else:
            assert "stale" not in data and data["replaced"] == [{"out": "000001", "in": "600127"}]
        with seed() as db:
            stored = db.scalar(select(DailyPickSet).where(DailyPickSet.date == day))
            assert json.loads(stored.meta) == meta
    finally:
        engine.dispose()


def test_slow_select_allows_ticker_to_advance_before_live_reads(tmp_path, monkeypatch):
    engine, seed, state = _database(tmp_path, monkeypatch)
    _insert(seed, "2026-10-13")
    async def style(*args):
        return {"phase_source": "live"}
    monkeypatch.setattr(route, "_live_style_routing", style)
    monkeypatch.setattr(route, "_live_gate", lambda *args: _gate_ready())
    db_waiting = threading.Event()
    measured = {"during": 0, "gaps_ms": []}

    @event.listens_for(engine, "before_cursor_execute")
    def slow_select(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT") and "daily_pick_set" in statement:
            db_waiting.set()
            try:
                time.sleep(.12)
            finally:
                db_waiting.clear()

    async def run():
        stop = asyncio.Event()
        ready = asyncio.Event()

        async def ticker():
            previous = time.perf_counter()
            ready.set()
            while not stop.is_set():
                await asyncio.sleep(.005)
                now = time.perf_counter()
                measured["gaps_ms"].append((now - previous) * 1000)
                previous = now
                if db_waiting.is_set():
                    measured["during"] += 1

        ticking = asyncio.create_task(ticker())
        await ready.wait()
        try:
            return await route.today_picks(REQUEST, HUB)
        finally:
            stop.set()
            await ticking

    try:
        result = asyncio.run(run())
        print(f"slow_db=120ms ticker_ticks_during_db={measured['during']} "
              f"ticker_max_gap_ms={max(measured['gaps_ms']):.2f}")
        assert measured["during"] >= 2, "slow DB SELECT blocked the event-loop ticker"
        assert result["data"]["date"] == "2026-10-13"
    finally:
        engine.dispose()


def test_execution_is_read_after_live_gate_off_loop_and_failure_keeps_entry(tmp_path, monkeypatch):
    engine, seed, state = _database(tmp_path, monkeypatch)
    _insert(seed, "2026-10-13", meta={"selection_entries": {"600127": {"state": "partial"}}})
    calls = []
    _live_stubs(monkeypatch, state, calls)
    import app.picks.opportunity_learning as learning
    original = learning.latest_notification_execution
    execution = {"decision_version": "verified-v1", "decision": "wait", "basis": "not a fill"}

    async def gate(request, hub, stored):
        assert state["active"] == 0
        calls.append(("gate", stored))
        with seed() as db:
            db.add(OpportunityDecisionSnapshot(snapshot_id="test-read", run_id="read-later",
                trade_date="2026-10-13", as_of=datetime(2026, 10, 13, 10, 0),
                scenario="notification", stage="notification", symbol="600127", decision="wait",
                strategy_version="v1", feature_version="v1",
                evidence=json.dumps({"execution_contract": execution, "dispatch": "held"})))
            db.commit()
        return {"gate_source": "live"}

    monkeypatch.setattr(route, "_live_gate", gate)

    def guarded(day, sf):
        assert threading.get_ident() != main_thread
        assert [name for name, _ in calls] == ["style", "gate"]
        return original(day, sf)

    main_thread = threading.get_ident()
    monkeypatch.setattr(learning, "latest_notification_execution", guarded)
    try:
        result = asyncio.run(route.today_picks(REQUEST, HUB))
        item = result["data"]["items"][0]
        assert item["execution"]["decision_version"] == "verified-v1"
        assert item["execution"]["dispatch"] == "held"
        assert item["execution"]["basis"] == "not a fill"
        assert item["selection_entry"] == {"state": "partial"}
        # A broken evidence read never turns the original entry into a current quote.
        monkeypatch.setattr(route, "_live_gate", lambda *args: _gate_ready())
        def failed(*args):
            raise RuntimeError("evidence DB unavailable")
        monkeypatch.setattr(learning, "latest_notification_execution", failed)
        result = asyncio.run(route.today_picks(REQUEST, HUB))
        assert result["data"]["items"][0]["execution"] is None
        assert result["data"]["items"][0]["selection_entry"] == {"state": "partial"}
    finally:
        engine.dispose()


async def _gate_ready():
    return {"gate_source": "live"}


def test_canceled_read_drains_own_session_without_starting_live_judgement(tmp_path, monkeypatch):
    engine, seed, state = _database(tmp_path, monkeypatch)
    _insert(seed, "2026-10-13")
    calls = []
    _live_stubs(monkeypatch, state, calls)
    entered = threading.Event()
    release = threading.Event()

    @event.listens_for(engine, "before_cursor_execute")
    def paused_select(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT") and "daily_pick_set" in statement:
            entered.set()
            assert release.wait(3), "test did not release its isolated DB reader"

    async def run():
        reader = asyncio.create_task(route.today_picks(REQUEST, HUB))
        try:
            while not entered.is_set():
                await asyncio.sleep(.001)
            reader.cancel()
            with pytest.raises(asyncio.CancelledError):
                await reader
            assert calls == []
            # Canceling an await does not kill a worker. It still owns and closes
            # its Session when the bounded SELECT finishes.
            assert state["active"] == 1
        finally:
            release.set()
            while state["active"]:
                await asyncio.sleep(.001)
        assert state["created"] == state["closed"]
        assert calls == []

    try:
        asyncio.run(asyncio.wait_for(run(), timeout=5))
    finally:
        release.set()
        engine.dispose()


def test_new_read_after_selection_write_does_not_reuse_previous_snapshot(tmp_path, monkeypatch):
    engine, seed, state = _database(tmp_path, monkeypatch)
    _insert(seed, "2026-10-13")
    calls = []
    _live_stubs(monkeypatch, state, calls)
    try:
        first = asyncio.run(route.today_picks(REQUEST, HUB))
        with seed() as db:
            row = db.scalar(select(DailyPickSet).where(DailyPickSet.date == "2026-10-13"))
            row.items = json.dumps([{"symbol": "000001", "price": 12.5}])
            row.meta = "{broken meta"
            db.commit()
        second = asyncio.run(route.today_picks(REQUEST, HUB))
        assert first["data"]["items"][0]["symbol"] == "600127"
        assert second["data"]["items"][0]["symbol"] == "000001"
        assert second["data"]["items"][0]["selection_entry"]["state"] == "missing"
        assert [name for name, _ in calls] == ["style", "gate", "style", "gate"]
        assert state["active"] == 0
    finally:
        engine.dispose()
