"""Selected-stock research authorization and repeat prevention; no live model/source."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.agent import AgentResourceUsage, AgentTask
from app.models.daily_pick import DailyPickSet
from app.models.opportunity_learning import OpportunityDecisionRun as Run, OpportunityDecisionSnapshot as Snapshot
from app.models.watchlist import Base
from app.services import agent_budget as budget
from app.services import tradingagents_research as research

NOW = datetime.fromisoformat("2026-10-21T10:00:00+08:00")


class Driver:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def method_identity(self):
        return {"upstream_sha": research.UPSTREAM_SHA, "native_full_graph": True,
                "transport": {"provider": "codex_cli", "model": "gpt-6.1-sol", "effort": "medium"}}

    async def run_accounted(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("fixture source failed before model")
        symbol = kwargs["params"]["symbol"]
        run_id = "a" * 32
        result = {"version": 1, "state": "completed", "symbol": symbol, "run_id": run_id,
                  "generated_at": research._now().isoformat(), "as_of": (research._now() - timedelta(seconds=1)).isoformat(),
                  "upstream_sha": research.UPSTREAM_SHA, "input_sha256": "b" * 64,
                  "method_sha256": research._digest(self.method_identity()), "verification": research.VERIFICATION,
                  "selection": kwargs["selection_binding"], "authorization_mode": "RESEARCH_ONLY_AUTHORIZED", "net_gain_verified": False,
                  "production_eligible": False, "reports": {key: "complete fixture original" for key in research.REPORTS},
                  "review_state": "unreviewed", "review_summary": {"finite_failures": 0, "peer_review_required": 1,
                  "source_verified": False, "independent_human_gold": False}}
        folder = research.ROOT / symbol / run_id
        folder.mkdir(parents=True)
        body = json.dumps(result).encode()
        (folder / "result.json").write_bytes(body)
        (folder.parent / "latest.json").write_text(json.dumps({"run_id": run_id, "result_sha256": hashlib.sha256(body).hexdigest()}))
        return {"state": "completed", "symbol": symbol, "run_id": run_id, "production_eligible": False}


class Recorder:
    task_id = "c" * 32

    def __init__(self):
        self.steps = []

    async def checkpoint_async(self):
        pass

    def checkpoint(self):
        pass

    async def add_async(self, **kwargs):
        self.steps.append(kwargs)


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    monkeypatch.setattr(research, "ROOT", tmp_path / "reports")
    monkeypatch.setattr(research, "_now", lambda: NOW)
    monkeypatch.setattr(research, "get_session_factory", lambda: sf)
    monkeypatch.setattr(budget, "beijing_now", lambda: NOW)
    monkeypatch.setattr(budget, "beijing_now_naive", lambda: NOW.replace(tzinfo=None))
    monkeypatch.setattr(budget, "get_session_factory", lambda: sf)
    monkeypatch.setattr(settings, "tradingagents_research_enabled", True)
    monkeypatch.setattr(settings, "tradingagents_research_authorization_digest", "", raising=False)
    monkeypatch.setattr(settings, "tradingagents_research_admission_digest", "")
    monkeypatch.setattr(settings, "agent_daily_llm_budget", 0)
    driver = Driver()
    app = SimpleNamespace(state=SimpleNamespace(tradingagents_research_driver=driver))
    value = SimpleNamespace(sf=sf, app=app, driver=driver)
    yield value
    engine.dispose()


def authorize(env, monkeypatch, **changes):
    artifact = {"version": 1, "mode": "RESEARCH_ONLY_AUTHORIZED", "consumer": "selected_stock_explanation",
                "user_authorization": "EXPLICIT", "net_gain_verified": False, "production_eligible": False,
                "method_identity": env.driver.method_identity(), "created_at": (NOW - timedelta(minutes=1)).isoformat(),
                "valid_until": (NOW + timedelta(days=1)).isoformat(), **changes}
    research.ROOT.mkdir(exist_ok=True)
    path = research.ROOT / "authorization.json"
    path.write_text(json.dumps(artifact))
    monkeypatch.setattr(settings, "tradingagents_research_authorization_digest", hashlib.sha256(path.read_bytes()).hexdigest(), raising=False)


def daily(env, symbols=("600001",)):
    from app.services.selection_notifications import daily_selection_version
    at = NOW - timedelta(minutes=5)
    entries = {symbol: {"state": "recorded", "selected_at": at.isoformat(), "source_version": "first-daily",
                        "source": "daily_generation"} for symbol in symbols}
    with env.sf() as db:
        items = [{"symbol": symbol} for symbol in symbols]
        db.add(DailyPickSet(date=NOW.date().isoformat(), items=json.dumps(items),
                            meta=json.dumps({"generated_at": at.isoformat(), "selection_version": daily_selection_version(NOW.date().isoformat(), at.isoformat(), items),
                                             "selection_entries": entries}),
                            created_at=at.astimezone(timezone.utc).replace(tzinfo=None)))
        db.commit()


def intraday(env, name="run-A", seconds=0, state="ready"):
    at = NOW.replace(tzinfo=None) - timedelta(minutes=2) + timedelta(seconds=seconds)
    with env.sf() as db:
        db.add(Run(run_id=name, trade_date=NOW.date().isoformat(), as_of=at, scenario="intraday_opportunity",
                   strategy_version="fixture", feature_version="fixture", evidence_digest="d" * 64, data_state=state))
        for symbol, rank, decision, shown in [("600001", 1, "ranked", True), ("600002", 2, "ranked", False),
                                              ("600003", None, "rejected", False)]:
            db.add(Snapshot(snapshot_id=f"{name}-{symbol}", run_id=name, trade_date=NOW.date().isoformat(),
                            as_of=at, scenario="intraday_opportunity", stage="rank", symbol=symbol,
                            rank=rank, decision=decision, data_state=state, strategy_version="fixture", feature_version="fixture",
                            created_at=at.replace(tzinfo=NOW.tzinfo).astimezone(timezone.utc).replace(tzinfo=None),
                            evidence=json.dumps({"selection_entry_contract": "selection-entry-v1",
                                                 "effective_limit": 1, "within_display_capacity": shown})))
        db.commit()


def test_selected_research_is_unlimited_but_still_billed_and_shared_unknown_stops_it(env):
    for _ in range(24):
        row = budget.reserve(budget.SCOPE_SELECTED_RESEARCH_LLM, purpose="tradingagents.fixture", kind="model", session_factory=env.sf)
        assert row["slot"] is None
        budget.start(row["id"], env.sf)
        budget.finish(row["id"], state="succeeded", usage={"input_tokens": 3, "output_tokens": 2}, session_factory=env.sf)
    assert budget.budget_status(env.sf)["selected_research_used"] == 24
    assert budget.budget_status(env.sf)["llm_used"] == 0
    with pytest.raises(budget.BudgetError, match="每日预算为 0"):
        budget.reserve(budget.SCOPE_AUTONOMY_LLM, purpose="other", kind="model", session_factory=env.sf)
    row = budget.reserve(budget.SCOPE_SELECTED_RESEARCH_LLM, purpose="tradingagents.fixture", kind="model", session_factory=env.sf)
    budget.start(row["id"], env.sf)
    with pytest.raises(budget.BudgetError) as error:
        budget.reserve(budget.SCOPE_ALERT_TRIAGE_LLM, purpose="other", kind="model", session_factory=env.sf)
    assert error.value.code == "usage_unknown"


def test_true_selection_excludes_participants_and_keeps_first_identity_across_new_quotes(env):
    daily(env, ("600001", "600004"))
    intraday(env)
    first = research.selected_bindings(env.sf)
    assert set(first) == {"600001", "600004"}
    assert first["600001"]["source"] == "daily_generation"
    intraday(env, "run-B", seconds=1)
    assert research.selected_bindings(env.sf)["600001"]["selection_key"] == first["600001"]["selection_key"]
    with env.sf() as db:
        row = db.scalar(select(DailyPickSet).where(DailyPickSet.date == NOW.date().isoformat()))
        row.items = "[]"
        meta = json.loads(row.meta)
        from app.services.selection_notifications import daily_selection_version
        meta["selection_version"] = daily_selection_version(row.date, meta["generated_at"], [])
        row.meta = json.dumps(meta)
        db.commit()
    assert research.selected_bindings(env.sf)["600001"]["selection_key"] == first["600001"]["selection_key"]
    intraday(env, "run-C", seconds=2, state="degraded")
    assert research.selected_bindings(env.sf) == {}


def test_research_only_authorization_does_not_claim_quality_or_require_22_slots(env, monkeypatch):
    daily(env)
    authorize(env, monkeypatch)
    view = research.preflight("600001", env.app)
    assert view["executable"] is True
    assert view["admission"]["state"] == "research_only_authorized"
    assert view["admission"]["net_gain_verified"] is False
    assert view["production_eligible"] is False
    assert view["required_model_slots"] is None and view["budget"]["limit"] is None
    assert "not_selected" in {row["code"] for row in research.preflight("600002", env.app)["blockers"]}
    authorize(env, monkeypatch, net_gain_verified=True)
    assert research.preflight("600001", env.app)["executable"] is False


def test_same_selection_never_restarts_after_new_quotes_or_failure(env, monkeypatch):
    from app.services import agent_tasks
    monkeypatch.setattr(agent_tasks, "_db_thread", lambda fn, *args, **kwargs: asyncio.to_thread(fn, *args, **kwargs))
    daily(env)
    authorize(env, monkeypatch)
    result = asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    assert result["state"] == "completed" and env.driver.calls == 1
    again = asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    assert again["reused_identical_selection"] is True and env.driver.calls == 1
    with env.sf() as db:
        row = db.scalar(select(DailyPickSet).where(DailyPickSet.date == NOW.date().isoformat()))
        row.items = json.dumps([{"symbol": "600001", "price": 99}])
        meta = json.loads(row.meta)
        from app.services.selection_notifications import daily_selection_version
        meta["selection_version"] = daily_selection_version(row.date, meta["generated_at"], json.loads(row.items))
        row.meta = json.dumps(meta)
        db.commit()
    asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    assert env.driver.calls == 1
    env.driver.fail = True
    daily_key = research.ROOT / "600001"
    for p in daily_key.glob("selection-*.json"):
        data = json.loads(p.read_text()); data["state"] = "failed"; p.write_text(json.dumps(data))
    with pytest.raises(research.ResearchBlocked):
        asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    assert env.driver.calls == 1


def test_selected_scheduler_does_not_queue_another_task_or_retry_unknown(env, monkeypatch):
    from app.services import agent_tasks
    daily(env)
    authorize(env, monkeypatch)
    queued = []

    async def create(kind, params, **kwargs):
        queued.append((kind, deepcopy(params)))
        return {"id": "e" * 32}

    monkeypatch.setattr(agent_tasks, "create_task_async", create)
    asyncio.run(research.scheduler_tick(env.app))
    asyncio.run(research.scheduler_tick(env.app))
    assert queued == [("tradingagents_research", {"symbol": "600001"})]
    assert env.driver.calls == 0
    with env.sf() as db:
        assert db.scalars(select(AgentResourceUsage)).all() == []
        assert db.scalars(select(AgentTask)).all() == []


def test_active_task_does_not_lose_unstarted_selection_at_midnight(env, monkeypatch):
    from app.services import agent_tasks
    daily(env, ("600001", "600004"))
    authorize(env, monkeypatch, valid_until=(NOW + timedelta(days=2)).isoformat())
    original = deepcopy(research.selected_bindings(env.sf))
    with env.sf() as db:
        db.add(AgentTask(id="active", type="tradingagents_research", status="running", params='{"symbol":"600009"}'))
        db.commit()
    queued = []
    async def create(kind, params, **kwargs):
        queued.append(deepcopy(params))
        with env.sf() as db:
            db.add(AgentTask(id="e" * 32, type=kind, status="queued", params=json.dumps(params),
                             created_by=kwargs["created_by"], created_at=research._now().replace(tzinfo=None)))
            db.commit()
        return {"id": "e" * 32}
    monkeypatch.setattr(agent_tasks, "create_task_async", create)
    asyncio.run(research.scheduler_tick(env.app))
    claims = sorted(research.ROOT.glob("*/selection-*.json"))
    assert len(claims) == 2 and not queued
    assert all(json.loads(path.read_text())["state"] == "pending" for path in claims)
    tomorrow = NOW + timedelta(days=1)
    monkeypatch.setattr(research, "_now", lambda: tomorrow)
    monkeypatch.setattr(budget, "beijing_now", lambda: tomorrow)
    assert research.selected_bindings(env.sf) == {}
    with env.sf() as db:
        db.get(AgentTask, "active").status = "succeeded"
        db.commit()
    asyncio.run(research.scheduler_tick(env.app))
    assert queued == [{"symbol": "600001"}]
    # An ordinary symbol request still has no permission to start history.
    with pytest.raises(research.ResearchBlocked) as blocked:
        research.require_start({"symbol": "600001"}, env.app)
    assert blocked.value.code == "not_selected"
    rec = Recorder()
    rec.task_id = "e" * 32
    result = asyncio.run(research.run_research(rec, {"symbol": "600001"}, env.app))
    assert result["state"] == "completed" and env.driver.calls == 1
    saved = research.read_view("600001", env.app)["latest"]
    assert saved["selection"] == original["600001"]
    assert saved["selection"]["trade_date"] == NOW.date().isoformat()
    assert saved["as_of"] == (tomorrow - timedelta(seconds=1)).isoformat()
    assert saved["generated_at"] == tomorrow.isoformat()


def test_dispatch_lease_does_not_prevent_read_only_selection_intent_capture(env, monkeypatch):
    from app.services.company_evidence import worker_lease
    daily(env)
    authorize(env, monkeypatch)
    with worker_lease(research.ROOT / "selected-dispatch"):
        asyncio.run(research.scheduler_tick(env.app))
    path = next((research.ROOT / "600001").glob("selection-*.json"))
    assert json.loads(path.read_text())["state"] == "pending"
    assert env.driver.calls == 0
    with env.sf() as db:
        assert not db.scalars(select(AgentResourceUsage)).all()
        assert not db.scalars(select(AgentTask)).all()


@pytest.mark.parametrize("change", ["disable", "authorization", "receipt", "method", "cancel", "wrong_creator", "wrong_task"])
def test_pending_original_selection_does_not_bypass_current_hard_gates(env, monkeypatch, change):
    from app.services import agent_tasks
    from app.services.company_evidence import worker_lease
    daily(env)
    authorize(env, monkeypatch, valid_until=(NOW + timedelta(days=2)).isoformat())
    with worker_lease(research.ROOT / "selected-dispatch"):
        asyncio.run(research.scheduler_tick(env.app))
    binding = research.selected_bindings(env.sf)["600001"]
    method = research._digest(env.driver.method_identity())
    path = research._selection_claim(binding, method)
    research._write_selection_claim(path, binding, method, "dispatching", dispatch_owner="original-owner")
    tomorrow = NOW + timedelta(days=1)
    monkeypatch.setattr(research, "_now", lambda: tomorrow)
    with env.sf() as db:
        db.add(AgentTask(id=Recorder.task_id, type="tradingagents_research", status="running", params='{"symbol":"600001"}',
                         created_by="scheduler", created_at=NOW.replace(tzinfo=None)))
        db.commit()
    if change == "disable":
        monkeypatch.setattr(settings, "tradingagents_research_enabled", False)
    elif change == "authorization":
        monkeypatch.setattr(settings, "tradingagents_research_authorization_digest", "f" * 64)
    elif change == "receipt":
        with env.sf() as db:
            row = db.scalar(select(DailyPickSet))
            meta = json.loads(row.meta)
            meta["selection_entries"]["600001"]["reference_price"] = 999
            row.meta = json.dumps(meta)
            db.commit()
    elif change == "method":
        old = env.driver.method_identity()
        monkeypatch.setattr(env.driver, "method_identity", lambda: {**old, "revision": "not-migrated"})
        authorize(env, monkeypatch, created_at=NOW.isoformat(), valid_until=(tomorrow + timedelta(days=1)).isoformat())
    elif change in {"cancel", "wrong_creator"}:
        with env.sf() as db:
            task = db.get(AgentTask, Recorder.task_id)
            if change == "cancel":
                task.cancel_requested_at = tomorrow.replace(tzinfo=None)
            else:
                task.created_by = "user"
            db.commit()
    rec = Recorder()
    if change == "wrong_task":
        rec.task_id = "d" * 32
    monkeypatch.setattr(agent_tasks, "create_task_async", lambda *a, **kw: pytest.fail("No fresh old-selection task"))
    before = path.read_bytes()
    with pytest.raises((research.ResearchBlocked, ValueError)):
        asyncio.run(research.run_research(rec, {"symbol": "600001"}, env.app))
    assert env.driver.calls == 0 and path.read_bytes() == before


def test_pending_intent_does_not_create_model_usage_when_new_unknown_blocks_dispatch(env, monkeypatch):
    from app.services import agent_tasks
    daily(env)
    authorize(env, monkeypatch)
    row = budget.reserve(budget.SCOPE_SELECTED_RESEARCH_LLM, purpose="tradingagents.existing", kind="model", session_factory=env.sf)
    budget.start(row["id"], env.sf)
    monkeypatch.setattr(agent_tasks, "create_task_async", lambda *a, **kw: pytest.fail("Unknown blocks dispatch"))
    asyncio.run(research.scheduler_tick(env.app))
    claim = json.loads(next((research.ROOT / "600001").glob("selection-*.json")).read_text())
    assert claim["state"] == "pending" and env.driver.calls == 0
    with env.sf() as db:
        rows = db.scalars(select(AgentResourceUsage)).all()
        assert len(rows) == 1 and rows[0].id == row["id"] and not rows[0].usage_known


@pytest.mark.parametrize("creator,late", [("scheduler", True), ("user", False), ("user", True)])
def test_real_driver_keeps_original_selection_but_checks_pending_owner_before_each_call(env, monkeypatch, tmp_path, creator, late):
    from app.services import tradingagents_driver as driver
    daily(env)
    authorize(env, monkeypatch, valid_until=(NOW + timedelta(days=2)).isoformat())
    binding = research.selected_bindings(env.sf)["600001"]
    assert research.require_start({"symbol": "600001"}, env.app)["selection"] == binding
    identity = env.driver.method_identity()
    method = research._digest(identity)
    research._capture_pending(binding, method, env.sf)
    path = research._selection_claim(binding, method)
    research._write_selection_claim(path, binding, method, "dispatching", dispatch_owner="scheduler-owner")
    research._write_selection_claim(path, binding, method, "running", task_id=Recorder.task_id)
    tomorrow = NOW + timedelta(days=1) if late else NOW
    monkeypatch.setattr(research, "_now", lambda: tomorrow)
    monkeypatch.setattr(driver, "beijing_now", lambda: tomorrow)
    if late:
        with pytest.raises(research.ResearchBlocked) as ordinary:
            research.require_start({"symbol": "600001"}, env.app)
        assert ordinary.value.code == "not_selected"
    with env.sf() as db:
        db.add(AgentTask(id=Recorder.task_id, type="tradingagents_research", status="running", params='{"symbol":"600001"}',
                         created_by=creator, created_at=NOW.replace(tzinfo=None)))
        db.commit()
    facts = {"symbol": "600001", "task": "isolated later research fixture"}
    packet = {"case_id": "fixture", "ticker": "600001", "trade_date": tomorrow.date().isoformat(),
              "as_of": (tomorrow - timedelta(seconds=1)).isoformat(), "facts": facts,
              "evidence_sha256": driver.digest(facts), "identity": {"company_name": "公开夹具公司"},
              "market_data": {}, "upstream_sha": research.UPSTREAM_SHA}
    captured = []
    def build(symbol, at, app):
        captured.append(at)
        return packet
    native = driver.NativeTradingAgentsDriver(upstream_dir=tmp_path, python=tmp_path / "unused",
        dependencies=tmp_path, transport=None, packet_builder=build)
    monkeypatch.setattr(native, "method_identity", lambda: identity)
    monkeypatch.setattr(env.app.state, "tradingagents_research_driver", native)
    async def skip(*args, **kwargs):
        return args[0] if args else None
    monkeypatch.setattr(native, "_prepare_selected_company", skip)
    monkeypatch.setattr(native, "_prepare_indicators", skip)
    checked = []
    async def graph(**kwargs):
        checked.append(kwargs["usage_authorizer"]())
        assert kwargs["packet"]["trade_date"] == tomorrow.date().isoformat()
        with env.sf() as db:
            if creator == "scheduler":
                db.get(AgentTask, Recorder.task_id).cancel_requested_at = tomorrow.replace(tzinfo=None)
            else:
                row = db.scalar(select(DailyPickSet))
                meta = json.loads(row.meta)
                meta["selection_entries"]["600001"]["reference_price"] = 999
                row.meta = json.dumps(meta)
            db.commit()
        kwargs["usage_authorizer"]()  # The real driver-supplied gate must stop, before any reserve/infer.
        pytest.fail("Canceled or changed original intent cannot generate a response")
    monkeypatch.setattr(native, "_execute", graph)
    with pytest.raises(research.ResearchBlocked, match="已取消" if creator == "scheduler" else "来源无法核实"):
        asyncio.run(native.run_accounted(rec=Recorder(), params={"symbol": "600001"}, app=env.app,
            budget=budget, store_root=tmp_path / "native-reports", required_model_slots=None,
            verification=research.VERIFICATION, selection_binding=binding))
    assert captured == [tomorrow] and checked == [()]
    assert binding["trade_date"] == NOW.date().isoformat()
    with env.sf() as db:
        assert not db.scalars(select(AgentResourceUsage)).all()


@pytest.mark.parametrize("mutation", ["owner_changed", "intent_removed", "claim_deleted"])
def test_consumed_intent_cannot_fall_back_before_native_driver_start(env, monkeypatch, tmp_path, mutation):
    from app.services import agent_tasks, tradingagents_driver as driver
    monkeypatch.setattr(agent_tasks, "_db_thread", lambda fn, *args, **kwargs: asyncio.to_thread(fn, *args, **kwargs))
    daily(env)
    authorize(env, monkeypatch)
    binding = research.selected_bindings(env.sf)["600001"]
    identity = env.driver.method_identity()
    method = research._digest(identity)
    research._capture_pending(binding, method, env.sf)
    claim = research._selection_claim(binding, method)
    original_intent = deepcopy(json.loads(claim.read_text())["research_intent"])
    with env.sf() as db:
        db.add(AgentTask(id=Recorder.task_id, type="tradingagents_research", status="running", params='{"symbol":"600001"}',
                         created_by="user", created_at=NOW.replace(tzinfo=None)))
        db.commit()
    facts = {"symbol": "600001", "task": "isolated consumed-intent fixture"}
    packet = {"case_id": "fixture", "ticker": "600001", "trade_date": NOW.date().isoformat(),
              "as_of": NOW.isoformat(), "facts": facts, "evidence_sha256": driver.digest(facts),
              "identity": {"company_name": "公开夹具公司"}, "market_data": {}, "upstream_sha": research.UPSTREAM_SHA}
    source_calls, graph_calls, received_intents = [], [], []
    def build(*args):
        source_calls.append("packet")
        return packet
    native = driver.NativeTradingAgentsDriver(upstream_dir=tmp_path, python=tmp_path / "unused",
        dependencies=tmp_path, transport=None, packet_builder=build)
    monkeypatch.setattr(native, "method_identity", lambda: identity)
    async def company(*args, **kwargs):
        source_calls.append("company")
    async def indicators(packet, **kwargs):
        return packet
    async def graph(**kwargs):
        graph_calls.append(kwargs["usage_authorizer"]())
        pytest.fail("A consumed intent cannot disappear into ordinary current-day admission")
    monkeypatch.setattr(native, "_prepare_selected_company", company)
    monkeypatch.setattr(native, "_prepare_indicators", indicators)
    monkeypatch.setattr(native, "_execute", graph)
    original_run = native.run_accounted
    async def changed_before_native(**kwargs):
        received_intents.append(deepcopy(kwargs.get("expected_selection_intent")))
        consumed = json.loads(claim.read_text())
        assert consumed["state"] == "running" and consumed["task_id"] == Recorder.task_id
        if mutation == "claim_deleted":
            claim.unlink()
        else:
            if mutation == "owner_changed":
                consumed["task_id"] = "d" * 32
            else:
                consumed.pop("research_intent")
            claim.write_text(json.dumps(consumed))
        return await original_run(**kwargs)
    monkeypatch.setattr(native, "run_accounted", changed_before_native)
    monkeypatch.setattr(env.app.state, "tradingagents_research_driver", native)
    with pytest.raises((research.ResearchBlocked, driver.DriverUnavailable), match="selected_intent_changed_before_start|来源无法核实"):
        asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    assert received_intents == [original_intent]
    assert source_calls == graph_calls == []
    with env.sf() as db:
        assert not db.scalars(select(AgentResourceUsage)).all()


@pytest.mark.parametrize("changes", [
    {"valid_until": (NOW - timedelta(seconds=1)).isoformat()},
    {"created_at": (NOW + timedelta(seconds=1)).isoformat()},
    {"consumer": "ranking"}, {"production_eligible": True},
    {"method_identity": {"upstream_sha": research.UPSTREAM_SHA}},
])
def test_research_authorization_cannot_grant_effect_or_drift(env, monkeypatch, changes):
    daily(env)
    authorize(env, monkeypatch, **changes)
    view = research.preflight("600001", env.app)
    assert not view["executable"]
    assert "research_not_authorized" in {row["code"] for row in view["blockers"]}
    assert view["budget"]["limit"] is None
    assert view["production_eligible"] is False


def test_current_daily_version_and_entry_time_are_actual_selection_proof(env):
    daily(env)
    with env.sf() as db:
        row = db.scalar(select(DailyPickSet))
        meta = json.loads(row.meta)
        meta["selection_version"] = "stale-version"
        row.meta = json.dumps(meta)
        db.commit()
    assert research.selected_bindings(env.sf) == {}
    intraday(env)
    with env.sf() as db:
        row = db.scalar(select(Snapshot).where(Snapshot.symbol == "600001"))
        value = json.loads(row.evidence)
        value["within_display_capacity"] = "true"  # A string is not the producer's true flag.
        row.evidence = json.dumps(value)
        db.commit()
    assert research.selected_bindings(env.sf) == {}


def test_failed_selection_keeps_unknown_claim_and_does_not_automatically_pay_again(env, monkeypatch):
    from app.services import agent_tasks
    daily(env)
    authorize(env, monkeypatch)
    env.driver.fail = True
    with pytest.raises(RuntimeError, match="fixture source failed"):
        asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    claim = next((research.ROOT / "600001").glob("selection-*.json"))
    assert json.loads(claim.read_text())["state"] == "needs_recovery"
    monkeypatch.setattr(agent_tasks, "create_task_async", lambda *a, **kw: pytest.fail("No unknown retry"))
    asyncio.run(research.scheduler_tick(env.app))
    with pytest.raises(research.ResearchBlocked, match="已有执行或失败/未知"):
        asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    assert env.driver.calls == 1


def _prior_method_claim(env, monkeypatch, state, *, binding=None, patch=None, wrong_path=False):
    binding = deepcopy(binding or research.selected_bindings(env.sf)["600001"])
    method = research._digest(env.driver.method_identity())
    path = research._selection_claim(binding, method)
    value = {"version": 1, "selection": binding, "method_sha256": method, "state": state,
             "task_id": "old-task", "updated_at": NOW.isoformat(), **(patch or {})}
    path = path.with_name("selection-" + "f" * 64 + ".json") if wrong_path else path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    identity = {**env.driver.method_identity(), "repair_revision": "new-method"}
    monkeypatch.setattr(env.driver, "method_identity", lambda: identity)
    authorize(env, monkeypatch)
    return path, before


@pytest.mark.parametrize("entry", ["scheduler", "direct"])
@pytest.mark.parametrize("state", ["started", "running", "completed", "needs_recovery", "unknown", "queued", "dispatching", "dispatch_unknown"])
def test_new_method_cannot_reissue_any_prior_claim_for_the_same_selection(env, monkeypatch, state, entry):
    from app.services import agent_tasks
    daily(env)
    authorize(env, monkeypatch)
    path, before = _prior_method_claim(env, monkeypatch, state)
    queued = []
    async def create(*args, **kwargs):
        queued.append(True)
        return {"id": "e" * 32}
    monkeypatch.setattr(agent_tasks, "create_task_async", create)
    if entry == "scheduler":
        asyncio.run(research.scheduler_tick(env.app))
    else:
        with pytest.raises(research.ResearchBlocked) as caught:
            asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
        assert caught.value.code == "selection_needs_recovery"
    assert not queued and env.driver.calls == 0
    assert path.read_bytes() == before
    assert list(path.parent.glob("selection-*.json")) == [path]


@pytest.mark.parametrize("entry", ["scheduler", "direct"])
@pytest.mark.parametrize("patch,wrong_path", [
    ({"selection": {"selection_key": "a" * 64}}, False),
    ({"method_sha256": "not-a-hash"}, False),
    ({"version": 99}, False), ({"state": "invented_finished"}, False),
    ({"updated_at": "2026-10-21T10:00:00"}, False),
    ({"updated_at": (NOW + timedelta(seconds=1)).isoformat()}, False),
    ({"task_id": {"forged": True}}, False), ({"dispatch_owner": []}, False),
    ({}, True),
])
def test_invalid_prior_claim_cannot_be_ignored_to_regenerate_a_paid_selection(env, monkeypatch, entry, patch, wrong_path):
    from app.services import agent_tasks
    daily(env)
    authorize(env, monkeypatch)
    path, before = _prior_method_claim(env, monkeypatch, "needs_recovery", patch=patch, wrong_path=wrong_path)
    queued = []
    async def create(*args, **kwargs):
        queued.append(True)
        return {"id": "e" * 32}
    monkeypatch.setattr(agent_tasks, "create_task_async", create)
    if entry == "scheduler":
        asyncio.run(research.scheduler_tick(env.app))
    else:
        with pytest.raises(research.ResearchBlocked):
            asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    assert not queued and env.driver.calls == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("entry", ["scheduler", "direct"])
@pytest.mark.parametrize("storage", ["invalid_json", "symlink", "oversized"])
def test_unreadable_prior_claim_preserves_unknown_instead_of_triggering_another_method(env, monkeypatch, entry, storage):
    from app.services import agent_tasks
    daily(env)
    authorize(env, monkeypatch)
    path, before = _prior_method_claim(env, monkeypatch, "needs_recovery")
    if storage == "symlink":
        target = research.ROOT / "retained-original.json"
        target.write_bytes(before)
        path.unlink()
        path.symlink_to(target)
    else:
        path.write_text("{" if storage == "invalid_json" else " " * 16_385)
    stored = path.read_bytes()
    async def create(*args, **kwargs):
        pytest.fail("an unreadable claim cannot authorize another task")
    monkeypatch.setattr(agent_tasks, "create_task_async", create)
    if entry == "scheduler":
        asyncio.run(research.scheduler_tick(env.app))
    else:
        with pytest.raises(research.ResearchBlocked) as caught:
            asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
        assert caught.value.code == "selection_needs_recovery"
    assert path.read_bytes() == stored and env.driver.calls == 0


def test_execution_rechecks_other_method_claims_inside_its_running_cas(env, monkeypatch):
    daily(env)
    authorize(env, monkeypatch)
    original = research._execution_prior
    def delayed(binding, method):
        prior = original(binding, method)
        old_method = research._digest({"old_method": True})
        path = research._selection_claim(binding, old_method)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "selection": binding, "method_sha256": old_method,
                                   "state": "running", "task_id": "old-task", "updated_at": NOW.isoformat()}))
        return prior
    monkeypatch.setattr(research, "_execution_prior", delayed)
    with pytest.raises(research.ResearchBlocked):
        asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    assert env.driver.calls == 0
    assert len(list((research.ROOT / "600001").glob("selection-*.json"))) == 1


def test_a_valid_adjacent_selection_does_not_block_the_current_selection(env, monkeypatch):
    daily(env)
    authorize(env, monkeypatch)
    binding = deepcopy(research.selected_bindings(env.sf)["600001"])
    binding["source_version"] = "different-first-selection"
    binding["selected_at"] = (NOW - timedelta(minutes=6)).isoformat()
    binding["selection_key"] = research._digest({key: binding[key] for key in (
        "symbol", "trade_date", "selected_at", "source", "source_version")})
    path, before = _prior_method_claim(env, monkeypatch, "needs_recovery", binding=binding)
    result = asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    assert result["state"] == "completed" and env.driver.calls == 1
    assert path.read_bytes() == before


def test_old_completed_report_remains_readable_but_is_not_reissued_as_the_new_method(env, monkeypatch):
    daily(env)
    authorize(env, monkeypatch)
    asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    old = research.read_view("600001", env.app)["latest"]
    identity = {**env.driver.method_identity(), "repair_revision": "new-method"}
    monkeypatch.setattr(env.driver, "method_identity", lambda: identity)
    authorize(env, monkeypatch)
    latest = research.read_view("600001", env.app)["latest"]
    assert latest == old and latest["method_sha256"] != research._digest(identity)
    with pytest.raises(research.ResearchBlocked):
        asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    assert env.driver.calls == 1


def test_selected_scheduler_observes_durable_task_and_os_owner(env, monkeypatch):
    from app.services import agent_tasks
    from app.services.company_evidence import worker_lease
    daily(env)
    authorize(env, monkeypatch)
    monkeypatch.setattr(agent_tasks, "create_task_async", lambda *a, **kw: pytest.fail("No second owner"))
    with worker_lease(research.ROOT / "selected-dispatch"):
        asyncio.run(research.scheduler_tick(env.app))
    with env.sf() as db:
        db.add(AgentTask(id="owner", type="tradingagents_research", status="running", params='{"symbol":"600004"}'))
        db.commit()
    asyncio.run(research.scheduler_tick(env.app))
    claims = list(research.ROOT.glob("*/selection-*.json"))
    assert len(claims) == 1 and json.loads(claims[0].read_text())["state"] == "pending"


def test_completed_reader_keeps_original_selection_and_asof(env, monkeypatch):
    daily(env)
    authorize(env, monkeypatch)
    asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    latest = research.read_view("600001", env.app)["latest"]
    assert latest["authorization_mode"] == "RESEARCH_ONLY_AUTHORIZED"
    assert latest["net_gain_verified"] is False
    assert latest["selection"] == research.selected_bindings(env.sf)["600001"]
    assert latest["as_of"] == (NOW - timedelta(seconds=1)).isoformat()
    assert latest["generated_at"] == NOW.isoformat()


@pytest.mark.parametrize("expired", [False, True])
def test_authorized_consumer_suppresses_only_old_automatic_company_selector(env, monkeypatch, expired):
    from app.services import company_evidence
    daily(env)
    authorize(env, monkeypatch, **({"valid_until": (NOW - timedelta(seconds=1)).isoformat()} if expired else {}))
    monkeypatch.setattr(settings, "company_evidence_enabled", True)
    monkeypatch.setattr(company_evidence, "_model_admission", lambda *a: pytest.fail("No duplicate automatic selector"))
    asyncio.run(company_evidence.scheduler_tick(env.app))


def test_free_company_capture_reuses_current_profile_and_preserves_source_failure(env, monkeypatch, tmp_path):
    from app.events import company_impact, company_sources, company_state
    from app.services import tradingagents_driver as driver
    monkeypatch.setattr(company_impact, "ROOT", tmp_path / "company")
    monkeypatch.setattr(driver, "beijing_now", lambda: NOW)
    saved, captured = [], []
    current = {"status": "ready", "documents": [{"evidence": "public fixture"}],
               "extractor": company_sources.EXTRACTOR, "cache_until": (NOW + timedelta(hours=1)).isoformat()}
    monkeypatch.setattr(driver, "read_profile", lambda _: current)
    def collect(symbol, **kwargs):
        captured.append(symbol)
        return {"status": "ready", "documents": [{"evidence": "public fixture"}]}
    monkeypatch.setattr(company_sources, "collect_company", collect)
    monkeypatch.setattr(company_state, "put_profile", saved.append)
    asyncio.run(driver.NativeTradingAgentsDriver._prepare_selected_company("600001", Recorder()))
    assert not captured and not saved
    current["cache_until"] = (NOW - timedelta(seconds=1)).isoformat()
    asyncio.run(driver.NativeTradingAgentsDriver._prepare_selected_company("600001", Recorder()))
    assert captured == ["600001"] and len(saved) == 1
    monkeypatch.setattr(company_sources, "collect_company", lambda *a, **kw: {
        "status": "failed", "documents": [], "missing": ["公司披露来源失败：ReadTimeout"]})
    rec = Recorder()
    with pytest.raises(driver.DriverUnavailable, match="selected_company_source_unavailable"):
        asyncio.run(driver.NativeTradingAgentsDriver._prepare_selected_company("600001", rec))
    assert rec.steps[-1]["ok"] is False
    assert json.loads(rec.steps[-1]["output_summary"])["missing"] == ["公司披露来源失败：ReadTimeout"]
    assert len(saved) == 1 and env.driver.calls == 0


def test_real_native_driver_uses_free_preparation_and_complete_graph_scope(env, monkeypatch, tmp_path):
    from app.services import tradingagents_driver as driver
    daily(env)
    identity = env.driver.method_identity()
    stages = []
    facts = {"symbol": "600001", "candidate_news": [], "task": "工程夹具不代表市场证据"}
    packet = {"case_id": "selected-fixture", "ticker": "600001", "trade_date": NOW.date().isoformat(),
              "as_of": NOW.isoformat(), "facts": facts, "evidence_sha256": driver.digest(facts),
              "identity": {"company_name": "公开夹具公司"}, "market_data": {}, "upstream_sha": research.UPSTREAM_SHA}
    native = driver.NativeTradingAgentsDriver(upstream_dir=tmp_path / "upstream", python=tmp_path / "python",
        dependencies=tmp_path / "deps", transport=SimpleNamespace(), packet_builder=lambda *args: deepcopy(packet))
    monkeypatch.setattr(native, "method_identity", lambda: identity)
    monkeypatch.setattr(driver, "beijing_now", lambda: NOW)
    async def company(*args):
        stages.append("free_company")
    async def indicators(value, **kwargs):
        stages.append("indicators")
        return value
    async def execute(**kwargs):
        assert kwargs["max_calls"] is None
        assert kwargs["budget_scope"] == budget.SCOPE_SELECTED_RESEARCH_LLM
        stages.append("original_graph")
        (kwargs["run"] / "source-audit.jsonl").write_text("")
        return {key: "complete engineering report" for key in research.REPORTS}
    monkeypatch.setattr(native, "_prepare_selected_company", company)
    monkeypatch.setattr(native, "_prepare_indicators", indicators)
    monkeypatch.setattr(native, "_execute", execute)
    env.driver = native
    env.app.state.tradingagents_research_driver = native
    authorize(env, monkeypatch)
    result = asyncio.run(research.run_research(Recorder(), {"symbol": "600001"}, env.app))
    assert stages == ["free_company", "indicators", "original_graph"]
    assert result["production_eligible"] is False
    stored = json.loads((research.ROOT / "600001" / result["run_id"] / "result.json").read_text())
    assert stored["selection"] == research.selected_bindings(env.sf)["600001"]
    assert set(stored["reports"]) == set(research.REPORTS)
    assert stored["net_gain_verified"] is False and stored["review_state"] == "unreviewed"


def test_actual_call_accounting_keeps_tokens_after_22_selected_calls(env, tmp_path):
    from app.services import tradingagents_driver as driver
    class Typed:
        def method_identity(self):
            return {"provider": "fixture", "model": "fixture", "effort": "medium"}
        def infer(self, messages, schema, *, mark_launch, **kwargs):
            mark_launch()
            return {"structured_output": {"content": "engineering", "tool_calls": []},
                    "usage": {"input_tokens": 7, "output_tokens": 3}}
    native = driver.NativeTradingAgentsDriver(upstream_dir=tmp_path / "upstream", python=tmp_path / "python",
        dependencies=tmp_path / "deps", transport=Typed())
    async def run():
        for number in range(24):
            await native._infer_accounted(Recorder(), budget, [{"role": "user", "content": "fixture"}],
                {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]},
                "Market Analyst", str(number), budget_scope=budget.SCOPE_SELECTED_RESEARCH_LLM)
    asyncio.run(run())
    with env.sf() as db:
        rows = db.scalars(select(AgentResourceUsage)).all()
        assert len(rows) == 24
        assert all(row.scope == budget.SCOPE_SELECTED_RESEARCH_LLM and row.slot is None
                   and row.state == "succeeded" and row.usage_known == 1
                   and row.input_tokens == 7 and row.output_tokens == 3 for row in rows)


@pytest.mark.parametrize("type_,authorized,expected", [("review", True, 600),
    ("tradingagents_research", False, 600), ("tradingagents_research", True, 1800)])
def test_selected_graph_has_distinct_bounded_walltime(env, monkeypatch, type_, authorized, expected):
    from time import monotonic
    from app.services import agent_tasks
    seen = []
    monkeypatch.setattr(settings, "agent_task_timeout_seconds", 600)
    monkeypatch.setattr(settings, "tradingagents_research_timeout_seconds", 1800)
    monkeypatch.setattr(settings, "tradingagents_research_authorization_digest", "a" * 64 if authorized else "")
    class Step:
        def __init__(self, task_id, *, deadline_monotonic):
            seen.append(deadline_monotonic - monotonic())
        async def checkpoint_async(self):
            pass
    async def lifecycle(*args, **kwargs):
        return False
    async def audit(*args, **kwargs):
        pass
    async def handler(*args):
        return {"fixture": True}
    monkeypatch.setattr(agent_tasks, "_StepRecorder", Step)
    monkeypatch.setattr(agent_tasks, "_lifecycle_db", lifecycle)
    monkeypatch.setattr(agent_tasks, "_task_audit", audit)
    monkeypatch.setitem(agent_tasks._HANDLERS, type_, handler)
    asyncio.run(agent_tasks._execute("timeout-fixture", type_, {}))
    assert len(seen) == 1 and expected - 1 < seen[0] <= expected


@pytest.mark.parametrize("failed", [False, True])
def test_dispatch_absent_cas_keeps_manual_execution_that_won_the_delayed_write(env, monkeypatch, failed):
    from app.services import agent_tasks
    daily(env)
    authorize(env, monkeypatch)
    env.driver.fail = failed
    original_thread = agent_tasks._db_thread
    intercepted, queued = [], []
    async def delayed(operation, *args, **kwargs):
        if operation is research._write_selection_claim and args[3] == "dispatching" and not intercepted:
            intercepted.append(True)
            try:
                await research.run_research(Recorder(), {"symbol": "600001"}, env.app)
            except RuntimeError:
                if not failed:
                    raise
        return await original_thread(operation, *args, **kwargs)
    async def create(*args, **kwargs):
        queued.append(True)
        return {"id": "e" * 32}
    monkeypatch.setattr(agent_tasks, "_db_thread", delayed)
    monkeypatch.setattr(agent_tasks, "create_task_async", create)
    asyncio.run(research.scheduler_tick(env.app))
    claim = json.loads(next((research.ROOT / "600001").glob("selection-*.json")).read_text())
    assert queued == []
    assert claim["state"] == ("needs_recovery" if failed else "completed")
    assert claim["task_id"] == Recorder.task_id
    if not failed:
        assert claim["run_id"] == "a" * 32 and len(claim["result_sha256"]) == 64
    assert env.driver.calls == 1


@pytest.mark.parametrize("queue_callback_failed", [False, True])
def test_late_dispatch_callback_never_overwrites_the_running_owner(env, monkeypatch, queue_callback_failed):
    from app.services import agent_tasks
    daily(env)
    authorize(env, monkeypatch)
    original_run = env.driver.run_accounted
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        owners = []
        async def delayed_driver(**kwargs):
            started.set()
            await release.wait()
            return await original_run(**kwargs)
        async def create(*args, **kwargs):
            owners.append(asyncio.create_task(research.run_research(Recorder(), {"symbol": "600001"}, env.app)))
            await started.wait()
            if queue_callback_failed:
                raise RuntimeError("queue callback failed after execution started")
            return {"id": Recorder.task_id}
        monkeypatch.setattr(env.driver, "run_accounted", delayed_driver)
        monkeypatch.setattr(agent_tasks, "create_task_async", create)
        try:
            if queue_callback_failed:
                with pytest.raises(RuntimeError, match="queue callback failed"):
                    await research.scheduler_tick(env.app)
            else:
                await research.scheduler_tick(env.app)
            claim_path = next((research.ROOT / "600001").glob("selection-*.json"))
            running = json.loads(claim_path.read_text())
            assert running["state"] == "running" and running["task_id"] == Recorder.task_id
        finally:
            release.set()
            await asyncio.gather(*owners)
        completed = json.loads(claim_path.read_text())
        assert completed["state"] == "completed" and completed["run_id"] == "a" * 32
        assert len(completed["result_sha256"]) == 64
    asyncio.run(run())
    assert env.driver.calls == 1


def test_only_same_unstarted_dispatch_owner_can_advance_its_claim(env):
    daily(env)
    binding = research.selected_bindings(env.sf)["600001"]
    method = research._digest(env.driver.method_identity())
    path = research._selection_claim(binding, method)
    assert research._write_selection_claim(path, binding, method, "dispatching", dispatch_owner="owner-A") is True
    original = path.read_bytes()
    assert research._write_selection_claim(path, binding, method, "dispatching", dispatch_owner="owner-B") is False
    assert research._write_selection_claim(path, binding, method, "dispatch_unknown", dispatch_owner="owner-B") is False
    assert path.read_bytes() == original
    assert research._write_selection_claim(path, binding, method, "dispatch_unknown", dispatch_owner="owner-A") is True
    assert json.loads(path.read_text())["state"] == "dispatch_unknown"


def original_unknown(env):
    row = budget.reserve(budget.SCOPE_SELECTED_RESEARCH_LLM, purpose="tradingagents.original",
                         kind="model", session_factory=env.sf)
    budget.start(row["id"], env.sf)
    return {"usage_id": row["id"], "budget_date": NOW.date().isoformat(),
            "scope": budget.SCOPE_SELECTED_RESEARCH_LLM}


def continuation(env, fact, **changes):
    return {"version": 1, "user_authorization": "EXPLICIT", "reason": "Explicitly retain original unknown and continue new selections.",
            "authorized_at": NOW.isoformat(), "method_sha256": research._digest(env.driver.method_identity()),
            "allowed_unknown_usage": [fact], **changes}


def usage_facts(env):
    with env.sf() as db:
        return {row.id: {column.name: getattr(row, column.name) for column in row.__table__.columns}
                for row in db.scalars(select(AgentResourceUsage))}


def test_pinned_continuation_permits_only_new_selected_research_without_rewriting_unknown(env, monkeypatch):
    from app.services import agent_tasks
    daily(env)
    fact = original_unknown(env)
    before = usage_facts(env)
    authorize(env, monkeypatch)
    assert "usage_unknown" in {row["code"] for row in research.preflight("600001", env.app)["blockers"]}
    authorize(env, monkeypatch, unknown_usage_exception=continuation(env, fact))
    assert research.preflight("600001", env.app)["executable"] is True
    queued = []
    async def create(*args, **kwargs):
        queued.append(args)
        return {"id": "e" * 32}
    monkeypatch.setattr(agent_tasks, "create_task_async", create)
    asyncio.run(research.scheduler_tick(env.app))
    assert queued == [("tradingagents_research", {"symbol": "600001"})]
    assert usage_facts(env) == before
    with pytest.raises(ValueError):
        research.validate_params({"symbol": "600001", "allowed_unknown_usage": [fact]})
    # A second unknown was never authorized, including within the same scope.
    row = budget.reserve(budget.SCOPE_SELECTED_RESEARCH_LLM, purpose="tradingagents.new",
                         kind="model", session_factory=env.sf, allowed_unknown_usage=[fact])
    budget.start(row["id"], env.sf, allowed_unknown_usage=[fact])
    assert "usage_unknown" in {row["code"] for row in research.preflight("600001", env.app)["blockers"]}


@pytest.mark.parametrize("change", [
    {"method_sha256": "f" * 64}, {"user_authorization": "IMPLICIT"}, {"reason": " "},
    {"authorized_at": (NOW + timedelta(seconds=1)).isoformat()}, {"extra": True},
    {"allowed_unknown_usage": []},
    {"allowed_unknown_usage": [{"usage_id": "f" * 32, "budget_date": "2026-10-21", "scope": "selected_research_llm"}]},
    {"allowed_unknown_usage": [{"usage_id": "f" * 32, "budget_date": "2026-10-21", "scope": "autonomy_llm"}]},
])
def test_continuation_is_explicit_exact_method_and_original_row_bound(env, monkeypatch, change):
    daily(env)
    fact = original_unknown(env)
    before = usage_facts(env)
    authorize(env, monkeypatch, unknown_usage_exception=continuation(env, fact, **change))
    view = research.preflight("600001", env.app)
    assert not view["executable"]
    assert "research_not_authorized" in {row["code"] for row in view["blockers"]}
    assert usage_facts(env) == before


def test_continuation_preserves_recovered_unknown_and_expires_without_selflocking_grant(env, monkeypatch):
    daily(env)
    fact = original_unknown(env)
    with env.sf() as db:
        db.get(AgentResourceUsage, fact["usage_id"]).state = "failed"
        db.commit()
    authorize(env, monkeypatch, unknown_usage_exception=continuation(env, fact), valid_until=(NOW + timedelta(days=30)).isoformat())
    admission, blockers = research._quality(env.app)
    assert not blockers and research._allowed_unknown_usage(admission) == (fact,)
    next_day = NOW + timedelta(days=1)
    monkeypatch.setattr(research, "_now", lambda: next_day)
    admission, blockers = research._quality(env.app)
    assert not blockers and research._allowed_unknown_usage(admission) == ()
    monkeypatch.setattr(research, "_now", lambda: NOW)
    # Represent separately verified receipt reconciliation, not a retry of
    # finish: terminal lifecycle calls correctly cannot rewrite an old row.
    with env.sf() as db:
        row = db.get(AgentResourceUsage, fact["usage_id"])
        row.usage_known, row.input_tokens, row.output_tokens = 1, 7, 3
        db.commit()
    admission, blockers = research._quality(env.app)
    assert not blockers and research._allowed_unknown_usage(admission) == ()


def test_selected_driver_graph_entry_carries_the_same_exact_server_authorizer(env, monkeypatch, tmp_path):
    from app.services import tradingagents_driver as driver
    daily(env)
    fact = original_unknown(env)
    authorize(env, monkeypatch, unknown_usage_exception=continuation(env, fact))
    identity = env.driver.method_identity()
    at = (NOW - timedelta(seconds=1)).isoformat()
    facts = {"symbol": "600001", "task": "offline role fixture"}
    packet = {"case_id": "fixture", "ticker": "600001", "trade_date": NOW.date().isoformat(),
              "as_of": at, "facts": facts, "evidence_sha256": driver.digest(facts),
              "identity": {"company_name": "公开夹具公司"}, "market_data": {}, "upstream_sha": research.UPSTREAM_SHA}
    native = driver.NativeTradingAgentsDriver(upstream_dir=tmp_path, python=tmp_path / "unused",
        dependencies=tmp_path, transport=None, packet_builder=lambda *args: packet)
    monkeypatch.setattr(native, "method_identity", lambda: identity)
    monkeypatch.setattr(env.app.state, "tradingagents_research_driver", native)
    async def skip(*args, **kwargs):
        return args[0] if args else None
    monkeypatch.setattr(native, "_prepare_selected_company", skip)
    monkeypatch.setattr(native, "_prepare_indicators", skip)
    seen = []
    async def graph(**kwargs):
        assert kwargs["budget_scope"] == budget.SCOPE_SELECTED_RESEARCH_LLM
        assert kwargs["max_calls"] is None
        seen.append(kwargs["usage_authorizer"]())
        # A correctly re-pinned different authorization cannot replace the
        # grant used to open this graph, even when its method remains equal.
        authorize(env, monkeypatch, unknown_usage_exception=continuation(env, fact), created_at=(NOW - timedelta(seconds=3)).isoformat())
        with pytest.raises(research.ResearchBlocked):
            kwargs["usage_authorizer"]()
        (kwargs["run"] / "source-audit.jsonl").write_text("")
        return {key: "Complete original role fixture" for key in research.REPORTS}
    monkeypatch.setattr(native, "_execute", graph)
    binding = research.selected_bindings(env.sf)["600001"]
    result = asyncio.run(native.run_accounted(rec=Recorder(), params={"symbol": "600001"}, app=env.app,
        budget=budget, store_root=tmp_path / "reports-out", required_model_slots=None,
        verification=research.VERIFICATION, selection_binding=binding))
    assert result["state"] == "completed" and seen == [(fact,)]
    assert list(usage_facts(env)) == [fact["usage_id"]]


@pytest.mark.parametrize("mutation", [None, "before_reserve", "revoke", "method", "new_pin", "new_unknown"])
def test_real_driver_continuation_rechecks_server_pin_before_reserve_and_atomic_start(env, monkeypatch, tmp_path, mutation):
    from app.services import tradingagents_driver as driver
    from app.services.tradingagents_native.cli_codec import build_cli_schema
    daily(env)
    fact = original_unknown(env)
    authorize(env, monkeypatch, unknown_usage_exception=continuation(env, fact))
    before = usage_facts(env)[fact["usage_id"]]
    identity_sha = research._digest(env.driver.method_identity())
    pinned_sha = settings.tradingagents_research_authorization_digest
    authorize_call = lambda: research.authorized_unknown_usage(env.app, method_sha256=identity_sha, authorization_sha256=pinned_sha)
    calls, authorization_checks = [], []
    def checked():
        authorization_checks.append(True)
        return authorize_call()
    class Typed:
        def method_identity(self):
            return env.driver.method_identity()["transport"]
        def infer(self, messages, schema, *, mark_launch, timeout, role=None):
            if mutation == "revoke":
                (research.ROOT / "authorization.json").write_text("{}")
            elif mutation == "method":
                monkeypatch.setattr(env.driver, "method_identity", lambda: {"upstream_sha": research.UPSTREAM_SHA, "changed": True})
            elif mutation == "new_pin":
                authorize(env, monkeypatch, unknown_usage_exception=continuation(env, fact), created_at=(NOW - timedelta(seconds=3)).isoformat())
            elif mutation == "new_unknown":
                other = budget.reserve(budget.SCOPE_SELECTED_RESEARCH_LLM, purpose="tradingagents.concurrent",
                    kind="model", session_factory=env.sf, allowed_unknown_usage=[fact])
                budget.start(other["id"], env.sf, allowed_unknown_usage=[fact])
            mark_launch()
            calls.append(True)
            return {"structured_output": {"content": "isolated complete-role original", "tool_calls": []},
                    "usage": {"input_tokens": 17, "output_tokens": 8}, "codex_terminal": {"type": "turn.completed", "turn_count": 1}}
    native = driver.NativeTradingAgentsDriver(upstream_dir=tmp_path, python=tmp_path / "unused",
        dependencies=tmp_path, transport=Typed())
    path = tmp_path / "usage-receipts/1.json"
    if mutation == "before_reserve":
        (research.ROOT / "authorization.json").write_text("{}")
    async def run():
        return await native._infer_accounted(Recorder(), budget, [{"role": "user", "content": "offline fixture"}],
            build_cli_schema(), "Bull Researcher", 1, budget_scope=budget.SCOPE_SELECTED_RESEARCH_LLM,
            usage_receipt_path=path, method_sha256=identity_sha, usage_authorizer=checked)
    if mutation is None:
        assert asyncio.run(run())["call_id"] == 1
        assert calls == [True] and path.exists()
        added = [row for key, row in usage_facts(env).items() if key != fact["usage_id"]]
        assert len(added) == 1 and added[0]["state"] == "succeeded" and added[0]["usage_known"] == 1
        assert added[0]["input_tokens"] == 17 and added[0]["output_tokens"] == 8
    else:
        with pytest.raises((research.ResearchBlocked, budget.BudgetError)):
            asyncio.run(run())
        assert calls == [] and not path.exists()
        assert all(row["state"] == "started" for row in usage_facts(env).values())
    assert len(authorization_checks) == (1 if mutation == "before_reserve" else 2)
    assert usage_facts(env)[fact["usage_id"]] == before
