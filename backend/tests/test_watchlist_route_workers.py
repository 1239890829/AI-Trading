"""Watchlist SQL must not block unrelated async requests on the event loop."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import threading
from types import SimpleNamespace

from fastapi import Depends, FastAPI
import httpx
import pytest

from app.api.deps import require_api_token
from app.api.routes import watchlist
from app.core.config import settings
from app.core.errors import register_error_handlers


TOKEN = "isolated-watchlist-test"
HEADERS = {"X-API-Token": TOKEN}


def api_for(repo, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "api_token", TOKEN)
    api = FastAPI()
    register_error_handlers(api)
    api.state.watchlist_repo = repo
    api.include_router(watchlist.router, prefix="/api", dependencies=[Depends(require_api_token)])
    return api


@pytest.mark.parametrize(("method", "path", "body", "expected_status"), [
    ("GET", "/api/watchlist", None, 200),
    ("GET", "/api/watchlist/groups", None, 200),
    ("POST", "/api/watchlist", {"symbol": "600105", "name": "fixture"}, 201),
])
def test_blocked_repository_does_not_block_async_health(method, path, body, expected_status, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    worker_threads = []
    item = SimpleNamespace(symbol="600105", name="fixture", note=None, group_name="默认",
                           source="user", created_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
                           updated_at=None)

    class BlockingRepo:
        def block(self):
            worker_threads.append(threading.get_ident())
            entered.set()
            assert release.wait(5), "isolated blocking repository was not released"

        def list_items(self):
            self.block()
            return [item]

        def list_groups(self):
            self.block()
            return ["默认"]

        def add(self, *_args):
            self.block()
            return item

    api = api_for(BlockingRepo(), monkeypatch)

    @api.get("/api/health")
    async def health():
        return {"status": "ok", "repository_still_blocked": entered.is_set() and not release.is_set()}

    async def scenario():
        loop_thread = threading.get_ident()
        # The independent watchdog releases an old blocking async handler so the
        # red baseline fails a behavioral assertion instead of hanging pytest.
        watchdog = threading.Timer(2, release.set)
        watchdog.daemon = True
        watchdog.start()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                     base_url="http://isolated") as client:
            request = asyncio.create_task(client.request(method, path, json=body, headers=HEADERS))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                response = await client.get("/api/health")
                assert response.json() == {"status": "ok", "repository_still_blocked": True}
                assert not request.done(), "health must finish while the repository is blocked"
            finally:
                release.set()
                watchdog.cancel()
                result = await asyncio.wait_for(request, 3)
            assert result.status_code == expected_status
            assert worker_threads and all(worker != loop_thread for worker in worker_threads)

    asyncio.run(scenario())


def test_watchlist_worker_http_crud_auth_and_group_errors(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.models.watchlist import Base
    from app.repositories.watchlist_repo import WatchlistRepository

    engine = create_engine(f"sqlite:///{tmp_path / 'watchlist-workers.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    opened, closed = [], []

    class RecordedSession(Session):
        def __enter__(self):
            opened.append((id(self), threading.get_ident()))
            return super().__enter__()

        def __exit__(self, *args):
            result = super().__exit__(*args)
            closed.append((id(self), threading.get_ident()))
            return result

    repo = WatchlistRepository(sessionmaker(bind=engine, class_=RecordedSession,
                                           autoflush=False, expire_on_commit=False))
    api = api_for(repo, monkeypatch)

    async def scenario():
        loop_thread = threading.get_ident()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                     base_url="http://isolated") as client:
            assert (await client.get("/api/watchlist")).status_code == 401
            assert (await client.get("/api/watchlist/groups", headers={"X-API-Token": "wrong"})).status_code == 401
            assert (await client.post("/api/watchlist/groups", json={"name": "科技"})).status_code == 401
            assert opened == [], "auth must reject before SQL"
            client.headers.update(HEADERS)
            assert (await client.get("/api/watchlist")).json() == {"data": []}
            assert (await client.get("/api/watchlist/groups")).json() == {"data": []}
            assert (await client.post("/api/watchlist/groups", json={"name": "科技"})).json() == {"data": {"name": "科技"}}
            assert (await client.post("/api/watchlist/groups", json={"name": "科技"})).status_code == 409
            assert (await client.post("/api/watchlist/groups", json={"name": ""})).status_code == 422
            assert (await client.post("/api/watchlist", json={"symbol": "bad"})).status_code == 422
            added = await client.post("/api/watchlist", json={"symbol": "600105", "name": "fixture", "note": "memo", "group": "科技"})
            assert added.status_code == 201
            item = added.json()["data"]
            assert {k: item[k] for k in ("symbol", "name", "note", "group_name", "source")} == {
                "symbol": "600105", "name": "fixture", "note": "memo", "group_name": "科技", "source": "user"}
            assert item["created_at"] and item["updated_at"]
            assert (await client.post("/api/watchlist", json={"symbol": "600105"})).json()["data"] == item
            assert (await client.put("/api/watchlist/600105/group", json={"group": "新组"})).json() == {"data": {"symbol": "600105", "group": "新组"}}
            assert (await client.get("/api/watchlist/groups")).json() == {"data": ["新组", "科技"]}
            assert (await client.post("/api/watchlist/groups", json={"name": "冲突"})).status_code == 201
            assert (await client.put("/api/watchlist/groups/新组", json={"new_name": "冲突"})).status_code == 409
            assert (await client.put("/api/watchlist/groups/默认", json={"new_name": "x"})).status_code == 400
            assert (await client.put("/api/watchlist/groups/missing", json={"new_name": "x"})).status_code == 404
            assert (await client.put("/api/watchlist/groups/科技", json={"new_name": "空组"})).json() == {"data": {"old": "科技", "new": "空组"}}
            assert (await client.delete("/api/watchlist/groups/默认")).status_code == 400
            assert (await client.delete("/api/watchlist/groups/missing")).status_code == 404
            assert (await client.delete("/api/watchlist/groups/新组")).json() == {"data": {"name": "新组", "deleted": True, "members_moved_to": "默认"}}
            assert (await client.get("/api/watchlist")).json()["data"][0]["group_name"] == "默认"
            assert (await client.delete("/api/watchlist/600105")).json() == {"data": {"symbol": "600105", "removed": True}}
            assert (await client.delete("/api/watchlist/600105")).status_code == 404
            assert (await client.put("/api/watchlist/600105/group", json={"group": "x"})).status_code == 404
        assert opened and sorted(opened) == sorted(closed)
        assert all(worker != loop_thread for _, worker in opened)

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()
