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
                  "generated_at": NOW.isoformat(), "as_of": (NOW - timedelta(seconds=1)).isoformat(),
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
    assert not list(research.ROOT.glob("*/selection-*.json"))


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
