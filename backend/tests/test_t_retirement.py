"""做 T 退役边界：活动入口不再触发专属引擎，通用分时保持可用。"""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import ModuleType, SimpleNamespace
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_hub
from app.api.routes.market_quotes import router
from app.assistant.tools import ToolCall, ToolContext, run_tool
from app.assistant.tools.registry import available_tools, tool_manifest
from app.core.bjtime import BJ_TZ


@pytest.mark.parametrize("path", [
    "/api/market/minute-signals/600519",
    "/api/market/minute-decisions?limit=5",
])
def test_retired_t_routes_are_absent_without_source_or_database_calls(path, monkeypatch):
    from app.core import db

    def forbidden():
        raise RuntimeError("retired route reached provider or database")

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_hub] = forbidden
    monkeypatch.setattr(db, "get_session_factory", forbidden)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get(path).status_code == 404
        assert not any("minute-signals" in p or "minute-decisions" in p
                       for p in client.get("/openapi.json").json()["paths"])


def test_retired_t_tool_is_unregistered_and_generic_minute_remains_available():
    def forbidden_database():
        pytest.fail("retired tool read or wrote a database")

    ctx = ToolContext(provider=object(), session_factory=forbidden_database)
    assert "minute_decisions" not in available_tools(ctx)
    assert "minute_decisions" not in tool_manifest()
    assert "minute" in available_tools(ctx)
    receipts = []
    asyncio.run(run_tool(ToolCall("minute_decisions"), ctx, cache=None, receipts=receipts))
    assert receipts == [{"tool": None, "state": "unregistered"}]


def test_close_review_keeps_shared_snapshots_and_never_scans_t(monkeypatch):
    from app.market import board_flow
    from app.picks import heat_history, review_intraday

    stop = asyncio.Event()
    calls = []

    async def heat(_app):
        calls.append("heat")

    async def skyrocket(_app):
        calls.append("skyrocket")

    async def board_snapshot():
        calls.append("board")
        stop.set()

    def t_scan(*_args):
        calls.append("retired_t")
        return {"recorded": 0, "settled": 0}

    monkeypatch.setattr(review_intraday, "beijing_now", lambda: datetime(
        2026, 10, 13, 15, 36, tzinfo=BJ_TZ))
    monkeypatch.setattr(review_intraday, "load_brief", lambda _day: None)
    monkeypatch.setattr(heat_history, "record_daily_heat", heat)
    monkeypatch.setattr(heat_history, "record_daily_skyrocket", skyrocket)
    monkeypatch.setattr(board_flow, "snapshot_daily_if_closed", board_snapshot)
    retired_module = ModuleType("app.market.minute_decisions")
    retired_module.scan_and_settle_today = t_scan
    monkeypatch.setitem(sys.modules, retired_module.__name__, retired_module)
    asyncio.run(review_intraday.intraday_review_scheduler(SimpleNamespace(), stop=stop))
    assert calls == ["heat", "skyrocket", "board"]


def test_generic_minute_route_and_historical_model_are_preserved():
    from app.review.models import MinuteDecisionRow

    assert "/minute-line/{symbol}" in {r.path for r in router.routes}
    assert MinuteDecisionRow.__tablename__ == "minute_decisions"
