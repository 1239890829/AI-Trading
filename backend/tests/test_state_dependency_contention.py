"""Pure state dependencies must not queue behind synchronous endpoint work."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import anyio.to_thread
import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI

from app.api.deps import get_watchlist_repository, require_api_token
from app.api.routes import market_quotes, picks, theme_catalog
from app.core.config import settings


@asynccontextmanager
async def _full_worker_pool():
    limiter = anyio.to_thread.current_default_thread_limiter()
    # Borrow the existing capacity without changing it or starting 40 threads.
    borrowers = [object() for _ in range(int(limiter.total_tokens))]
    for borrower in borrowers:
        limiter.acquire_on_behalf_of_nowait(borrower)
    try:
        assert limiter.available_tokens == 0
        yield limiter
    finally:
        for borrower in borrowers:
            limiter.release_on_behalf_of(borrower)


async def _completed_or_queued(reading, limiter):
    # The discriminator is completion versus actual limiter queue admission,
    # not a small wall-clock latency threshold. Timeout is only a deadlock guard.
    async with asyncio.timeout(2):
        while not reading.done() and limiter.statistics().tasks_waiting == 0:
            await asyncio.sleep(0)


def _state_app(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "api_token", "isolated-state-test")
    api = FastAPI()
    api.include_router(market_quotes.router, prefix="/api", dependencies=[Depends(require_api_token)])

    class Hub:
        provider = SimpleNamespace(name="isolated", realtime=False)
        last_success_refresh = None
        reads = 0

        def get_quotes(self, symbols):
            assert self is api.state.hub
            assert symbols == ["600519"]
            self.reads += 1
            return []

        def is_stale(self):
            return True

    hub, repo = Hub(), object()
    api.state.hub = hub
    api.state.watchlist_repo = repo
    probe = APIRouter()

    @probe.get("/repository-state")
    async def repository_state(repository=Depends(get_watchlist_repository)):
        # Production watchlist handlers perform synchronous repository I/O and
        # still require the worker pool. This async consumer isolates its getter.
        return {"same_object": repository is repo}

    api.include_router(probe, prefix="/api", dependencies=[Depends(require_api_token)])
    return api, hub


@pytest.mark.parametrize("path", ["/api/quotes?symbols=600519", "/api/repository-state"])
def test_state_read_completes_while_existing_worker_capacity_is_fully_borrowed(monkeypatch, path):
    api, hub = _state_app(monkeypatch)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://isolated") as client:
            reading = None
            try:
                async with _full_worker_pool() as limiter:
                    reading = asyncio.create_task(client.get(path, headers={"X-API-Token": "isolated-state-test"}))
                    await _completed_or_queued(reading, limiter)
                    assert reading.done(), "pure app.state getter queued behind the saturated AnyIO worker pool"
                    response = reading.result()
                    assert response.status_code == 200
                    if path.startswith("/api/quotes"):
                        assert response.json()["data"] == [] and hub.reads == 1
                    else:
                        assert response.json() == {"same_object": True}
                    assert limiter.statistics().tasks_waiting == 0
                    assert limiter.available_tokens == 0  # No token was released to make the read pass.
            finally:
                if reading is not None:
                    await asyncio.wait_for(reading, 2)

    asyncio.run(run())


@pytest.mark.parametrize("path", ["/api/quotes?symbols=600519", "/api/repository-state"])
@pytest.mark.parametrize("headers", [{}, {"X-API-Token": "wrong"}])
def test_saturated_state_read_still_rejects_missing_or_wrong_token(monkeypatch, path, headers):
    api, hub = _state_app(monkeypatch)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://isolated") as client:
            reading = None
            try:
                async with _full_worker_pool() as limiter:
                    reading = asyncio.create_task(client.get(path, headers=headers))
                    await _completed_or_queued(reading, limiter)
                    assert reading.done(), "authorization must reject before a state dependency can queue"
                    assert reading.result().status_code == 401
                    assert hub.reads == 0 and limiter.statistics().tasks_waiting == 0
            finally:
                if reading is not None:
                    await asyncio.wait_for(reading, 2)

    asyncio.run(run())


def test_execution_gate_direct_state_read_keeps_hub_identity(monkeypatch):
    from app.picks import execution_gate

    api = FastAPI()
    api.include_router(picks.router, prefix="/api")
    hub, session_factory = object(), object()
    api.state.hub = hub
    monkeypatch.setattr(picks, "get_session_factory", lambda: session_factory)

    async def collect(actual_hub, actual_factory, **kwargs):
        assert actual_hub is hub and actual_factory is session_factory
        assert kwargs["pick_date"] == "2026-10-23"
        return {"pick_date": "2026-10-23", "items": []}

    monkeypatch.setattr(execution_gate, "collect_execution_gate", collect)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://isolated") as client:
            response = await client.get("/api/picks/execution-gate?date=2026-10-23")
            assert response.status_code == 200
            assert response.json()["data"] == {"pick_date": "2026-10-23", "items": []}

    asyncio.run(run())


def test_stock_themes_direct_state_read_keeps_market_alignment(monkeypatch):
    api = FastAPI()
    api.include_router(theme_catalog.router, prefix="/api")
    hub = SimpleNamespace(get_indices=lambda: [SimpleNamespace(symbol="000001", change_pct=1.2)])
    api.state.hub = hub
    api.state.snapshot_service = SimpleNamespace(snapshot=[{"symbol": "600519", "change_pct": 2.0}])

    async def changes(_codes):
        return {"885001.TI": 0.5}

    async def attribution(*_args):
        return []

    svc = SimpleNamespace(
        catalog_size=lambda: 1,
        get_official_for_symbol=lambda _symbol: [{"theme_code": "885001.TI", "theme_name": "测试题材"}],
        day_changes=changes,
        member_symbols_bulk=lambda _codes: {"885001.TI": ["600519"]},
    )
    api.dependency_overrides[theme_catalog.get_service] = lambda: svc
    monkeypatch.setattr(theme_catalog, "_attribution_for_symbol", attribution)
    from app.services import theme_catalog_service

    def alignment(members, observations, market_change):
        assert members == ["600519"] and observations == {"600519": 2.0}
        assert market_change == 1.2
        return 1.0

    monkeypatch.setattr(theme_catalog_service, "direction_alignment", alignment)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://isolated") as client:
            response = await client.get("/api/themes/stock/600519")
            assert response.status_code == 200
            assert response.json()["data"]["official"][0]["theme_align_1d"] == 1.0

    asyncio.run(run())
