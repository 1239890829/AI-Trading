"""Selected lists reuse saved summary state in one query, never launch machinery."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import json
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.api.routes import picks, picks_intraday
from app.models.agent import AgentTask
from app.models.daily_pick import DailyPickSet
from app.models.watchlist import Base
from app.services import tradingagents_research as research


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'selected-summary.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    monkeypatch.setattr(picks, "get_session_factory", lambda: sf)
    monkeypatch.setattr(picks_intraday, "get_session_factory", lambda: sf)
    monkeypatch.setattr(research, "get_session_factory", lambda: sf)
    monkeypatch.setattr(research, "ROOT", tmp_path / "saved")
    monkeypatch.setattr(research, "preflight", lambda *a, **kw: pytest.fail("List reads cannot inspect launch gates"))
    with sf() as db:
        for task_id, symbol, status, at in [("old", "600001", "failed", 10), ("new", "600001", "running", 11),
                                              ("other", "600002", "queued", 11), ("reference", "600009", "failed", 11)]:
            db.add(AgentTask(id=task_id, type="tradingagents_research", status=status,
                params=json.dumps({"symbol": symbol}), created_at=datetime(2026, 10, 24, at)))
        db.add(AgentTask(id="broken", type="tradingagents_research", status="failed", params="broken-json"))
        db.commit()
    queries, threads = [], []
    @event.listens_for(engine, "before_cursor_execute")
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT") and "agent_task" in statement:
            queries.append(statement)
            threads.append(threading.get_ident())
    yield SimpleNamespace(sf=sf, queries=queries, threads=threads)
    engine.dispose()


def test_bulk_summary_uses_one_task_query_and_keeps_original_last_state(env):
    summaries = research.read_saved_summaries(["600001", "600002", "600003", "600001"], session_factory=env.sf)
    assert len(env.queries) == 1 and set(summaries) == {"600001", "600002", "600003"}
    assert [summaries[s]["state"] for s in ("600001", "600002", "600003")] == ["running", "queued", "none"]
    assert all(not row["has_saved"] and row["method_compatible"] is None for row in summaries.values())
    assert all("reports" not in row and "roles" not in row for row in summaries.values())


def test_daily_read_route_attaches_only_current_items_without_mutating_storage(env, monkeypatch, client):
    today = date(2026, 10, 24)
    monkeypatch.setattr(picks, "beijing_today", lambda: today)
    async def style(*args):
        return {"phase_source": "unavailable"}
    async def gate(request, hub, meta):
        return meta
    monkeypatch.setattr(picks, "_live_style_routing", style)
    monkeypatch.setattr(picks, "_attach_gates", gate)
    original = [{"symbol": "600001", "score": 75}, {"symbol": "600002", "score": 72}]
    with env.sf() as db:
        db.add(DailyPickSet(date=today.isoformat(), items=json.dumps(original), meta="{}"))
        db.commit()
    response = client.get("/api/picks/today")
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [row["research_summary"]["state"] for row in items] == ["running", "queued"]
    assert len(env.queries) == 1 and all(t != threading.get_ident() for t in env.threads)
    with env.sf() as db:
        assert json.loads(db.scalar(select(DailyPickSet).where(DailyPickSet.date == today.isoformat())).items) == original


def test_intraday_read_route_projects_selected_items_without_reference_or_source_mutation(env, monkeypatch, client):
    from app.picks import intraday_opportunity
    source = {"items": [{"symbol": "600001"}], "reference_items": [{"symbol": "600009", "reference_only": True}]}
    original = deepcopy(source)
    async def trade_date(hub):
        return date(2026, 10, 24)
    async def build(*args):
        return {"data": {"themes": []}}
    monkeypatch.setattr(picks_intraday, "default_trade_date", trade_date)
    monkeypatch.setattr(picks_intraday, "_build_opportunities", build)
    monkeypatch.setattr(intraday_opportunity, "top_watch_stocks", lambda *a, **kw: deepcopy(source))
    response = client.get("/api/picks/intraday-top")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["items"][0]["research_summary"]["state"] == "running"
    assert data["reference_items"] == original["reference_items"] and source == original
    assert len(env.queries) == 1 and all(t != threading.get_ident() for t in env.threads)


def test_empty_selected_list_does_not_read_tasks_or_reports(env, monkeypatch):
    monkeypatch.setattr(research, "_latest", lambda *a: pytest.fail("No report read for an empty selection"))
    assert research.attach_saved_summaries([], session_factory=env.sf) == []
    assert env.queries == []


def test_bulk_task_read_failure_keeps_saved_evidence_and_explicit_unknown(env, monkeypatch):
    def unavailable():
        raise RuntimeError("isolated task reader unavailable")
    summary = research.read_saved_summaries(["600001"], session_factory=unavailable)["600001"]
    assert summary["state"] == "unknown" and summary["has_saved"] is False
    assert summary["task_state"] is None
