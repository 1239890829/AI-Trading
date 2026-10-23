"""Real isolated SQLite locks must not replay work or manufacture task endings."""
from __future__ import annotations

import asyncio
import sqlite3
import threading

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models.agent import AgentTask
from app.models.watchlist import Base
from app.services import agent_tasks as at


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "isolated-task.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 0.01})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    monkeypatch.setattr(at, "get_session_factory", lambda: sf)
    monkeypatch.setattr(at, "_RUNNING", {})
    monkeypatch.setattr(at, "_HANDLES", {})
    monkeypatch.setattr(at, "_LIFECYCLE_LOCK_DELAYS", (0.02, 0.03, 0.05), raising=False)
    blocker = sqlite3.connect(path, timeout=0.01, check_same_thread=False)
    yield sf, blocker
    blocker.rollback()
    blocker.close()
    engine.dispose()


@pytest.mark.parametrize(("phase", "handler_start_delay"), [("before_start", 0), ("after_handler", 0), ("after_handler", 0.08)])
def test_released_exclusive_lock_eventually_saves_one_handler_result(database, monkeypatch, phase, handler_start_delay):
    _, blocker = database
    calls, loop_errors = [], []
    acquired = asyncio.Event()

    async def handler(rec, params, app):
        calls.append(rec.task_id)
        if phase == "after_handler":
            await asyncio.sleep(handler_start_delay)
            blocker.execute("BEGIN EXCLUSIVE")
            acquired.set()
        return {"kind": "fixture", "completed": True}

    monkeypatch.setitem(at._HANDLERS, "data_check", handler)

    async def scenario():
        asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: loop_errors.append(ctx))
        created = at.create_task("data_check")
        handle = at._HANDLES[created["id"]]
        if phase == "before_start":
            blocker.execute("BEGIN EXCLUSIVE")
            acquired.set()

        async def release():
            # Count the same hold duration from real acquisition. Under load,
            # task creation can precede the handler's BEGIN by more than 50ms.
            await asyncio.wait_for(acquired.wait(), timeout=2)
            await asyncio.sleep(0.05)
            blocker.rollback()

        releaser = asyncio.create_task(release())
        await asyncio.gather(handle, return_exceptions=True)
        await releaser
        return at.get_task(created["id"])

    row = asyncio.run(scenario())
    assert row["status"] == "succeeded", row
    assert row["result_ref"] == {"kind": "fixture", "completed": True}
    assert calls == [row["id"]]
    assert not at._RUNNING and not at._HANDLES and not loop_errors


def test_successful_handler_is_not_failed_when_finish_audit_throws(database, monkeypatch, caplog):
    calls = []
    original = at.record_audit

    def unavailable_audit(**kwargs):
        if kwargs["action"] == "task.finish":
            raise RuntimeError("private audit payload")
        return original(**kwargs)

    async def handler(rec, params, app):
        calls.append(rec.task_id)
        return {"kind": "fixture"}

    monkeypatch.setattr(at, "record_audit", unavailable_audit)
    monkeypatch.setitem(at._HANDLERS, "data_check", handler)

    async def scenario():
        created = at.create_task("data_check")
        await at._HANDLES[created["id"]]
        return at.get_task(created["id"])

    row = asyncio.run(scenario())
    assert row["status"] == "succeeded" and row["result_ref"] == {"kind": "fixture"}
    assert calls == [row["id"]] and not at._HANDLES
    assert "private audit payload" not in caplog.text


def test_handler_async_checkpoint_retries_lock_without_blocking_release_on_loop(database, monkeypatch):
    _, blocker = database
    calls, released = [], []

    async def handler(rec, params, app):
        calls.append(rec.task_id)
        blocker.execute("BEGIN EXCLUSIVE")

        async def release():
            await asyncio.sleep(0.05)
            blocker.rollback()
            released.append(True)

        releaser = asyncio.create_task(release())
        await rec.checkpoint_async()
        await releaser
        return {"kind": "fixture", "after_checkpoint": True}

    monkeypatch.setitem(at._HANDLERS, "data_check", handler)

    async def scenario():
        created = at.create_task("data_check")
        await at._HANDLES[created["id"]]
        return at.get_task(created["id"])

    row = asyncio.run(scenario())
    assert row["status"] == "succeeded" and row["result_ref"]["after_checkpoint"] is True
    assert calls == [row["id"]] and released == [True]


def test_late_cancel_during_terminal_write_does_not_reverse_completed_result(database, monkeypatch):
    _, blocker = database
    entered, calls = threading.Event(), []
    set_status = at._set_status

    def blocked_success(*args, **kwargs):
        if args[1] == "succeeded" and not entered.is_set():
            blocker.execute("BEGIN EXCLUSIVE")
            entered.set()
        return set_status(*args, **kwargs)

    async def handler(rec, params, app):
        calls.append(rec.task_id)
        return {"kind": "fixture", "completed": True}

    monkeypatch.setattr(at, "_set_status", blocked_success)
    monkeypatch.setitem(at._HANDLERS, "data_check", handler)

    async def scenario():
        created = at.create_task("data_check")
        handle = at._HANDLES[created["id"]]
        assert await asyncio.to_thread(entered.wait, 2)
        handle.cancel()
        await asyncio.sleep(0.005)
        assert not handle.done()
        blocker.rollback()
        await handle
        return at.get_task(created["id"])

    row = asyncio.run(scenario())
    assert row["status"] == "succeeded" and row["result_ref"]["completed"] is True
    assert calls == [row["id"]] and not at._HANDLES


def test_permanent_lock_leaves_unknown_persistence_and_releases_handles(database, monkeypatch, caplog):
    _, blocker = database
    calls = []

    async def handler(rec, params, app):
        calls.append(rec.task_id)
        return {"kind": "fixture"}

    monkeypatch.setitem(at._HANDLERS, "data_check", handler)

    async def scenario():
        created = at.create_task("data_check")
        handle = at._HANDLES[created["id"]]
        blocker.execute("BEGIN EXCLUSIVE")
        outcome = await asyncio.gather(handle, return_exceptions=True)
        blocker.rollback()
        return at.get_task(created["id"]), outcome

    row, outcome = asyncio.run(scenario())
    assert outcome == [None]  # No unhandled background exception.
    assert row["status"] == "queued" and row["finished_at"] is None
    assert not calls and not at._RUNNING and not at._HANDLES
    assert "persistence=unknown" in caplog.text and "reconcile_on_startup" in caplog.text
    assert at.reconcile_on_startup() == 1
    assert at.get_task(row["id"])["error"]["code"] == "Interrupted"


def test_cancel_during_locked_prepare_drains_db_step_then_persists_canceled(database, monkeypatch):
    _, blocker = database
    calls = []

    async def handler(rec, params, app):
        calls.append(rec.task_id)
        return {"kind": "fixture"}

    monkeypatch.setitem(at._HANDLERS, "data_check", handler)

    async def scenario():
        created = at.create_task("data_check")
        handle = at._HANDLES[created["id"]]
        blocker.execute("BEGIN EXCLUSIVE")
        await asyncio.sleep(0.005)
        handle.cancel()
        await asyncio.sleep(0.005)
        blocker.rollback()
        await asyncio.gather(handle, return_exceptions=True)
        return at.get_task(created["id"])

    row = asyncio.run(scenario())
    assert row["status"] == "canceled" and row["finished_at"] is not None
    assert not calls and not at._RUNNING and not at._HANDLES


def test_non_lock_sql_error_is_not_retried(database):
    sf, _ = database
    calls = []

    def invalid_sql():
        calls.append(1)
        with sf() as db:
            db.connection().exec_driver_sql("SELECT FROM intentionally_invalid_sql")

    async def scenario():
        with pytest.raises(OperationalError):
            await at._lifecycle_db(invalid_sql)

    asyncio.run(scenario())
    assert calls == [1]


def test_lost_task_row_cannot_be_reported_as_success(database, monkeypatch, caplog):
    sf, _ = database

    async def handler(rec, params, app):
        with sf() as db:
            db.delete(db.get(AgentTask, rec.task_id))
            db.commit()
        return {"kind": "fixture"}

    monkeypatch.setitem(at._HANDLERS, "data_check", handler)

    async def scenario():
        created = at.create_task("data_check")
        await at._HANDLES[created["id"]]
        return created["id"]

    task_id = asyncio.run(scenario())
    assert at.get_task(task_id) is None and not at._HANDLES
    assert "persistence=unknown" in caplog.text


def test_create_api_waits_for_real_sqlite_lock_off_loop_and_runs_handler_once(database, monkeypatch):
    from app.api.deps import require_write_token
    from app.api.routes.agent import router

    _, blocker = database
    calls = []
    owner_thread = threading.get_ident()

    async def handler(rec, params, app):
        calls.append((rec.task_id, threading.get_ident()))
        return {"kind": "fixture"}

    monkeypatch.setitem(at._HANDLERS, "data_check", handler)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[require_write_token] = lambda: None

    async def scenario():
        blocker.execute("BEGIN EXCLUSIVE")

        async def release():
            await asyncio.sleep(0.05)
            blocker.rollback()

        releaser = asyncio.create_task(release())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://fixture") as client:
            response = await client.post("/api/agent/tasks", json={"type": "data_check", "params": {}})
        await releaser
        if response.status_code == 200:
            task_id = response.json()["data"]["id"]
            handle = at._HANDLES.get(task_id)
            if handle:
                await handle
        return response

    response = asyncio.run(scenario())
    assert response.status_code == 200, response.text
    task_id = response.json()["data"]["id"]
    assert calls == [(task_id, owner_thread)]
    assert at.get_task(task_id)["status"] == "succeeded"


def test_async_creation_reserves_type_before_wait_and_excludes_sync_and_async_competitors(database, monkeypatch):
    sf, blocker = database
    calls, identities = [], []
    uuid4 = at.uuid.uuid4

    def identity():
        value = uuid4()
        identities.append(value.hex)
        return value

    monkeypatch.setattr(at.uuid, "uuid4", identity)

    async def handler(rec, params, app):
        calls.append(rec.task_id)
        return {"kind": "fixture"}

    monkeypatch.setitem(at._HANDLERS, "data_check", handler)

    async def scenario():
        blocker.execute("BEGIN EXCLUSIVE")
        first = asyncio.create_task(at.create_task_async("data_check"))
        await asyncio.sleep(0.005)
        with pytest.raises(RuntimeError, match="正在运行"):
            at.create_task("data_check")
        with pytest.raises(RuntimeError, match="正在运行"):
            await at.create_task_async("data_check")
        blocker.rollback()
        created = await first
        handle = at._HANDLES.get(created["id"])
        if handle:
            await handle
        return created

    created = asyncio.run(scenario())
    assert calls == identities == [created["id"]]
    with sf() as db:
        assert db.query(AgentTask).count() == 1
    assert not at._RUNNING and not at._HANDLES


def test_cancel_after_async_queue_commit_never_launches_handler(database, monkeypatch):
    sf, _ = database
    calls = []
    lifecycle_db = at._lifecycle_db

    async def cancel_after_commit(operation, *args, **kwargs):
        value = await lifecycle_db(operation, *args, **kwargs)
        if operation.__name__ == "_persist_queued_task":
            asyncio.current_task().cancel()
            await asyncio.sleep(0)
        return value

    async def handler(rec, params, app):
        calls.append(rec.task_id)
        return {"kind": "fixture"}

    monkeypatch.setattr(at, "_lifecycle_db", cancel_after_commit)
    monkeypatch.setitem(at._HANDLERS, "data_check", handler)

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await at.create_task_async("data_check")

    asyncio.run(scenario())
    with sf() as db:
        row = db.query(AgentTask).one()
        assert row.status == "canceled" and row.cancel_requested_at and row.finished_at
    assert not calls and not at._RUNNING and not at._HANDLES


def test_permanently_locked_create_api_reports_bounded_unknown_without_launch(database, monkeypatch):
    from app.api.deps import require_write_token
    from app.api.routes.agent import router

    sf, blocker = database
    calls = []

    async def handler(rec, params, app):
        calls.append(rec.task_id)
        return {"kind": "fixture"}

    monkeypatch.setitem(at._HANDLERS, "data_check", handler)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[require_write_token] = lambda: None

    async def scenario():
        blocker.execute("BEGIN EXCLUSIVE")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            response = await client.post("/api/agent/tasks", json={"type": "data_check"})
        blocker.rollback()
        return response

    response = asyncio.run(scenario())
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "task_queue_unavailable" and body["persistence"] == "unknown"
    assert len(body["task_id"]) == 32 and "SQL" not in response.text
    assert isinstance(body["detail"], str)
    assert body["task_id"] in body["detail"] and "unknown" in body["detail"] and "不要自动重发" in body["detail"]
    assert not calls and not at._RUNNING and not at._HANDLES
    with sf() as db:
        assert db.query(AgentTask).count() == 0  # Unknown during the lock is not a fake canceled row.
