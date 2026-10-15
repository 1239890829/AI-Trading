"""Real SQLite read contention on the notification HTTP coroutine, without delivery."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
from threading import Event, get_ident
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.api.routes import notifications as route
from app.core.bjtime import BJ_TZ
from app.models.watchlist import Base
from app.repositories.alert_repo import AlertRepository


@pytest.mark.parametrize("stage", ["alerts", "channels"])
@pytest.mark.parametrize("lock_timeout", [False, True], ids=["released", "failed"])
def test_notification_read_lock_does_not_stall_loop(tmp_path, monkeypatch, stage, lock_timeout):
    path = tmp_path / "notification-contention.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={
        "check_same_thread": False, "timeout": .08 if lock_timeout else 5,
    })
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    repo = AlertRepository(sf)
    rule = repo.create_rule(name="isolated explicit price", scope="symbols", symbols=["600000"],
                            channels=["in_app"], condition_type="price_above", threshold=10)
    repo.record_trigger(rule.id, "600000", 11, 10, snapshot={"name": "isolated"})
    monkeypatch.setattr(route, "beijing_now", lambda: datetime(2026, 10, 15, 13, 35, tzinfo=BJ_TZ))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(hub=None)))

    async def call():
        return await route.notifications(request, alert_limit=50, news_limit=15,
                                         news_min_score=None, repo=repo)

    baseline = asyncio.run(call())["data"]
    ready, sql_entered, sql_returned, released = Event(), Event(), Event(), Event()
    timeline = {"stage": stage, "lock_timeout": lock_timeout}
    pool = ThreadPoolExecutor(max_workers=1)
    futures = []
    active = Event()

    def target(statement):
        table = "ALERT_RULE" if stage == "alerts" else "NOTIFICATION_OUTBOX"
        return statement.lstrip().upper().startswith("SELECT ") and f"FROM {table}" in statement.upper()

    def before_sql(_conn, _cursor, statement, _params, _context, _many):
        if active.is_set() and not sql_entered.is_set() and target(statement):
            timeline.update(sql_entered_at=time.perf_counter(), sql_thread=get_ident())
            sql_entered.set()

    def after_sql(_conn, _cursor, statement, _params, _context, _many):
        if sql_entered.is_set() and not sql_returned.is_set() and target(statement):
            timeline["sql_returned_at"] = time.perf_counter()
            sql_returned.set()

    def writer():
        with sqlite3.connect(path) as db:
            db.execute("BEGIN EXCLUSIVE")
            timeline["lock_acquired_at"] = time.perf_counter()
            ready.set()
            timeline["watchdog_released"] = not sql_entered.wait(2)
            timeline["fixed_hold_started_at"] = time.perf_counter()
            time.sleep(.25)
            timeline["fixed_hold_finished_at"] = time.perf_counter()
            db.commit()
            timeline["lock_released_at"] = time.perf_counter()
        released.set()

    owner, name = (repo, "list_rules") if stage == "alerts" else (repo.outbox, "states_for_events")
    original = getattr(owner, name)

    def locked_read(*args, **kwargs):
        futures.append(pool.submit(writer))
        assert ready.wait(2), "isolated writer did not acquire its lock"
        active.set()
        try:
            return original(*args, **kwargs)
        finally:
            timeline["read_returned_at"] = time.perf_counter()

    monkeypatch.setattr(owner, name, locked_read)
    event.listen(engine, "before_cursor_execute", before_sql)
    event.listen(engine, "after_cursor_execute", after_sql)

    async def run():
        timeline["loop_thread"] = get_ident()
        beats = []
        done = False

        async def heartbeat():
            while not done:
                if sql_entered.is_set() and not sql_returned.is_set() and not released.is_set():
                    beats.append(time.perf_counter())
                await asyncio.sleep(.005)

        task = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        try:
            return await call()
        finally:
            done = True
            await task
            timeline["heartbeats_during_sql_lock"] = len(beats)

    try:
        result = asyncio.run(run())["data"]
    finally:
        pool.shutdown(wait=True)
        for future in futures:
            future.result()
        event.remove(engine, "before_cursor_execute", before_sql)
        event.remove(engine, "after_cursor_execute", after_sql)
        engine.dispose()
    timeline["errors"] = result["errors"]
    output = os.environ.get("ASHARE_NOTIFICATION_CONTENTION_EVIDENCE_DIR")
    if output:
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{stage}-{'failed' if lock_timeout else 'released'}.json").write_text(
            json.dumps(timeline, indent=2) + "\n")
    assert sql_entered.is_set() and released.is_set(), timeline
    assert timeline["watchdog_released"] is False, timeline
    assert timeline["sql_thread"] != timeline["loop_thread"], timeline
    assert timeline["heartbeats_during_sql_lock"] >= 1, timeline
    if lock_timeout:
        assert result["errors"] == {stage: "OperationalError"}
        if stage == "alerts":
            assert result["items"] == [] and result["diagnostics"] is None
        else:
            assert len(result["items"]) == 1 and result["items"][0]["channels"] is None
            expected = {**baseline["items"][0], "channels": None}
            assert result["items"][0] == expected
    else:
        assert sql_returned.is_set()
        assert timeline["sql_returned_at"] >= timeline["fixed_hold_finished_at"]
        assert result == baseline
