"""Consumer gates/readers only: fixture admissions do not establish model quality."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import beijing_now, beijing_now_naive
from app.core.config import settings
from app.models.agent import AgentResourceUsage, AgentTask
from app.models.watchlist import Base
from app.services import agent_budget as ab, agent_tasks as at
from app.services import tradingagents_research as research


class UncalledDriver:
    def method_identity(self):
        return {"upstream_sha": research.UPSTREAM_SHA, "fixture": "not-a-quality-result"}

    async def run_accounted(self, **kwargs):
        raise AssertionError("No model/graph execution belongs in gate/reader tests")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'research.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    monkeypatch.setattr(research, "get_session_factory", lambda: sf)
    monkeypatch.setattr(at, "get_session_factory", lambda: sf)
    monkeypatch.setattr(research, "ROOT", tmp_path / "reports")
    monkeypatch.setattr(settings, "tradingagents_research_enabled", False)
    monkeypatch.setattr(settings, "tradingagents_research_admission_digest", "")
    monkeypatch.setattr(settings, "agent_daily_llm_budget", 8)
    monkeypatch.setattr(settings, "agent_model_max_timeout_seconds", 180)
    app = SimpleNamespace(state=SimpleNamespace())
    return sf, app


def _pin_fixture_admission(app, monkeypatch, **changes):
    """Only simulates server configuration; never a real validation receipt."""
    app.state.tradingagents_research_driver = UncalledDriver()
    monkeypatch.setattr(settings, "tradingagents_research_enabled", True)
    artifact = {
        "version": 1, "consumer": "explicit_stock_research", "verdict": "VALIDATED",
        "net_gain_verified": True, "method_identity": app.state.tradingagents_research_driver.method_identity(),
        "evaluation_sha256": "a" * 64, "created_at": (beijing_now() - timedelta(hours=1)).isoformat(),
        "valid_until": (beijing_now() + timedelta(hours=1)).isoformat(), **changes,
    }
    path = research.ROOT / "admission.json"
    path.parent.mkdir()
    path.write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setattr(settings, "tradingagents_research_admission_digest", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def _codes(view):
    return {row["code"] for row in view["blockers"]}


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()} if root.exists() else {}


def test_bootstrap_disabled_never_loads_optional_runtime(env, monkeypatch):
    import builtins

    _, app = env
    original_import = builtins.__import__

    def no_driver(name, *args, **kwargs):
        if name == "app.services.tradingagents_driver":
            raise AssertionError("disabled runtime must not import driver")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_driver)
    research.configure_driver(app)
    assert app.state.tradingagents_research_driver is None
    assert not research.ROOT.exists()


def test_bootstrap_uses_only_fixed_server_manifest_and_preserves_baseline(env, monkeypatch):
    from app.services import tradingagents_driver

    _, app = env
    monkeypatch.setattr(settings, "tradingagents_research_enabled", True)
    seen = []
    driver = UncalledDriver()

    def configured(path, *, enabled):
        seen.append((path, enabled))
        return driver

    monkeypatch.setattr(tradingagents_driver, "driver_from_server_manifest", configured)
    research.configure_driver(app)
    assert seen == [(research.ROOT / "runtime.json", True)]
    assert app.state.tradingagents_research_driver is driver

    def unavailable(*args, **kwargs):
        raise ValueError("private runtime location")

    monkeypatch.setattr(tradingagents_driver, "driver_from_server_manifest", unavailable)
    research.configure_driver(app)
    assert app.state.tradingagents_research_driver is None
    assert "private runtime location" not in str(research.preflight("600276", app))


def test_default_reports_real_8_vs_22_conflict_without_reservations_or_folders(env):
    sf, app = env
    view = research.read_view("600276", app)
    assert view["budget"]["remaining"] == 8 and view["required_model_slots"] == 22
    assert {"disabled", "quality_not_admitted", "driver_unavailable", "budget_insufficient"} <= _codes(view)
    assert view["latest"] is None and view["task"] is None and not view["executable"]
    assert not research.ROOT.exists()
    with sf() as db:
        assert db.execute(select(AgentResourceUsage)).scalars().all() == []
        assert db.execute(select(AgentTask)).scalars().all() == []


@pytest.mark.parametrize("changes", [
    {"net_gain_verified": False}, {"verdict": "ENGINEERING_PASSED"},
    {"method_identity": {"upstream_sha": research.UPSTREAM_SHA, "fixture": "other"}},
    {"valid_until": "2020-01-01T00:00:00+08:00"}, {"created_at": "2999-01-01T00:00:00+08:00"},
    {"created_at": None}, {"valid_until": "2026-10-20T12:00:00"},
])
def test_exact_quality_receipt_not_engineering_or_wrong_version(env, monkeypatch, changes):
    _, app = env
    _pin_fixture_admission(app, monkeypatch, **changes)
    monkeypatch.setattr(settings, "agent_daily_llm_budget", 22)
    assert "quality_not_admitted" in _codes(research.preflight("600276", app))


def test_pin_content_drift_blocks_and_sufficient_gates_do_not_call_driver(env, monkeypatch):
    _, app = env
    path = _pin_fixture_admission(app, monkeypatch)
    monkeypatch.setattr(settings, "agent_daily_llm_budget", 22)
    before = _snapshot(research.ROOT)
    assert research.preflight("600276", app)["executable"] is True
    assert _snapshot(research.ROOT) == before
    path.write_text(path.read_text() + " ")
    assert "quality_not_admitted" in _codes(research.preflight("600276", app))


def test_existing_unknown_gate_is_exact_and_read_only(env, monkeypatch):
    sf, app = env
    _pin_fixture_admission(app, monkeypatch)
    monkeypatch.setattr(settings, "agent_daily_llm_budget", 22)
    row = ab.reserve(ab.SCOPE_ALERT_TRIAGE_LLM, purpose="fixture", kind="model", session_factory=sf)
    ab.start(row["id"], sf)
    before = ab.recent_usage(sf)
    assert "usage_unknown" in _codes(research.preflight("600276", app))
    assert ab.recent_usage(sf) == before
    ab.finish(row["id"], state="succeeded", usage={"input_tokens": 1, "output_tokens": 1}, session_factory=sf)
    assert "usage_unknown" not in _codes(research.preflight("600276", app))


def test_telemetry_unknown_does_not_become_new_shared_budget_gate(env, monkeypatch):
    sf, app = env
    _pin_fixture_admission(app, monkeypatch)
    monkeypatch.setattr(settings, "agent_daily_llm_budget", 22)
    ab.record_unmetered(purpose="fixture", provider="fixture", model="fixture", state="failed", session_factory=sf)
    assert research.preflight("600276", app)["executable"] is True


def test_current_remaining_not_total_limit_and_database_failure_fail_closed(env, monkeypatch):
    sf, app = env
    _pin_fixture_admission(app, monkeypatch)
    monkeypatch.setattr(settings, "agent_daily_llm_budget", 22)
    ab.reserve(ab.SCOPE_AUTONOMY_LLM, purpose="other", kind="model", session_factory=sf)
    assert research.preflight("600276", app)["budget"]["remaining"] == 21
    assert "budget_insufficient" in _codes(research.preflight("600276", app))
    monkeypatch.setattr(ab, "budget_status", lambda *args: (_ for _ in ()).throw(RuntimeError("private db path")))
    view = research.preflight("600276", app)
    assert "budget_unavailable" in _codes(view) and "private db path" not in str(view)


@pytest.mark.parametrize("params", [{}, {"symbol": "600276", "prompt": "override"}, {"symbol": 600276},
                                   {"symbol": "../../"}, {"symbol": "600276", "model": "other"}])
def test_no_user_supplied_model_path_prompt_or_admission(env, params):
    with pytest.raises(ValueError):
        research.validate_params(params)


def _stored_result(symbol="600276"):
    run_id = "b" * 32
    result = {
        "version": 1, "state": "completed", "symbol": symbol, "run_id": run_id,
        "generated_at": beijing_now().isoformat(), "as_of": (beijing_now() - timedelta(minutes=1)).isoformat(),
        "upstream_sha": research.UPSTREAM_SHA, "input_sha256": "c" * 64, "method_sha256": "d" * 64,
        "verification": research.VERIFICATION, "production_eligible": False,
        "review_state": "unreviewed", "review_summary": {"finite_failures": 0,
            "peer_review_required": 7, "source_verified": False, "independent_human_gold": False},
        "reports": {key: f"{key}，未验证研究；不构成买卖建议。" for key in research.REPORTS},
        "context": "must not escape public reader", "host_path": "private-runtime-path",
    }
    folder = research.ROOT / symbol
    path = folder / run_id / "result.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    (folder / "latest.json").write_text(json.dumps({"run_id": run_id,
        "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}))
    return path, result


def test_reads_saved_unverified_report_while_disabled_without_refresh_or_context_leak(env):
    sf, app = env
    _, result = _stored_result()
    with sf() as db:
        db.add(AgentTask(id="b" * 32, type="tradingagents_research", status="succeeded",
                         params='{"symbol":"600276"}', created_at=beijing_now_naive()))
        db.commit()
    before = _snapshot(research.ROOT)
    view = research.read_view("600276", app)
    assert view["latest"]["reports"] == result["reports"]
    assert view["latest"]["verification"] == research.VERIFICATION
    assert view["latest"]["production_eligible"] is False
    assert view["latest"]["review_summary"]["source_verified"] is False
    assert "context" not in view["latest"] and "host_path" not in view["latest"]
    assert view["task"]["id"] == result["run_id"] and view["task"]["status"] == "succeeded"
    assert _snapshot(research.ROOT) == before


@pytest.mark.parametrize("change", [
    {"review_state": "verified"}, {"review_summary": {}},
    {"review_state": "finite_failed_requires_review"},
    {"symbol": "600150"}, {"state": "running"}, {"production_eligible": True},
    {"generated_at": "2999-01-01T00:00:00+08:00"}, {"as_of": "2999-01-01T00:00:00+08:00"},
    {"generated_at": None}, {"as_of": "2026-10-20T12:00:00"},
    {"reports": {}}, {"verification": "independent_reviewed"},
])
def test_reader_rejects_cross_stock_future_partial_and_forged_verification(env, change):
    _, app = env
    path, result = _stored_result()
    result.update(change)
    path.write_text(json.dumps(result), encoding="utf-8")
    # Even an internally consistent changed hash cannot fix a wrong scope/state.
    (path.parent.parent / "latest.json").write_text(json.dumps({"run_id": result["run_id"],
        "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}))
    assert research.read_view("600276", app)["latest"] is None


def test_reader_rejects_unbound_file_change(env):
    _, app = env
    path, _ = _stored_result()
    path.write_text(path.read_text() + " ")
    assert research.read_view("600276", app)["latest"] is None


def test_task_status_is_stock_scoped_and_never_returns_raw_runtime_errors(env):
    sf, app = env
    with sf() as db:
        db.add(AgentTask(id="a" * 32, type="tradingagents_research", status="failed",
                         params='{"symbol":"600276"}', created_at=beijing_now_naive(),
                         error='{"code":"/private/host/path","message":"raw provider context"}'))
        db.add(AgentTask(id="z" * 32, type="tradingagents_research", status="running",
                         params='{"symbol":"600150"}', created_at=beijing_now_naive()))
        db.commit()
    view = research.read_view("600276", app)
    assert view["task"]["id"] == "a" * 32
    assert view["task"]["error"]["code"] == "TaskError"
    assert "private/host" not in str(view) and "raw provider context" not in str(view)


def test_existing_task_handler_rechecks_preflight_and_never_starts_model(env):
    _, app = env
    rec = SimpleNamespace(task_id="f" * 32)

    async def checkpoint():
        return None

    rec.checkpoint_async = checkpoint
    with pytest.raises(research.ResearchBlocked) as error:
        asyncio.run(research.run_research(rec, {"symbol": "600276"}, app))
    assert error.value.code == "disabled" and not research.ROOT.exists()


def test_api_read_auth_and_blocked_write_create_no_task_or_budget(env, monkeypatch):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from app.api.deps import require_api_token
    from app.api.routes.agent import router
    from app.core.device_policy import needs_maintenance

    sf, _ = env
    app = FastAPI()
    app.include_router(router, prefix="/api", dependencies=[Depends(require_api_token)])
    monkeypatch.setattr(settings, "api_token", "fixture-server-token")
    headers = {"X-API-Token": "fixture-server-token"}
    client = TestClient(app)
    route = "/api/agent/research/tradingagents/600276"
    assert client.get(route).status_code == 401
    view = client.get(route, headers=headers)
    assert view.status_code == 200 and view.json()["data"]["latest"] is None
    response = client.post("/api/agent/tasks", headers=headers,
                           json={"type": "tradingagents_research", "params": {"symbol": "600276"}})
    assert response.status_code == 409
    body = response.json()
    assert isinstance(body["detail"], str) and "22" in body["detail"]
    assert body["code"] == "tradingagents_blocked"
    assert "budget_insufficient" in {item["code"] for item in body["blockers"]}
    assert not needs_maintenance("GET", "/api/agent/research/tradingagents/{symbol}")
    assert needs_maintenance("POST", "/api/agent/tasks")
    assert needs_maintenance("GET", "/api/agent/research/tradingagents/internal-export")
    with sf() as db:
        assert db.execute(select(AgentTask)).scalars().all() == []
        assert db.execute(select(AgentResourceUsage)).scalars().all() == []
    assert not research.ROOT.exists()


def test_api_rejects_runtime_override_params_before_task_creation(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes.agent import router

    sf, _ = env
    monkeypatch.setattr(settings, "api_token", "fixture-server-token")
    app = FastAPI()
    app.include_router(router, prefix="/api")
    response = TestClient(app).post("/api/agent/tasks", headers={"X-API-Token": "fixture-server-token"},
        json={"type": "tradingagents_research", "params": {"symbol": "600276", "admission": True}})
    assert response.status_code == 422
    with sf() as db:
        assert db.execute(select(AgentTask)).scalars().all() == []


def test_saved_only_recovery_identity_is_lazy_and_explicit_check_does_not_require_current_selection(env, monkeypatch):
    from app.services import tradingagents_recovery as recovery
    sf, app = env
    task_id, run_id = "a" * 32, "b" * 32
    with sf() as db:
        db.add(AgentTask(id=task_id, type="tradingagents_research", status="canceled", params='{"symbol":"600276"}',
            created_at=beijing_now_naive(), finished_at=beijing_now_naive()))
        db.commit()
    run = research.ROOT / "600276" / run_id; run.mkdir(parents=True)
    (run / "execution.json").write_text(json.dumps({"state": "canceled", "task_id": task_id, "run_id": run_id, "symbol": "600276"}))
    monkeypatch.setattr(research, "_latest", lambda *a: None)
    monkeypatch.setattr(research, "preflight", lambda *a, **k: pytest.fail("Saved recovery reads must not require today's selection"))
    checked = []
    monkeypatch.setattr(recovery, "_context", lambda *a, **k: checked.append(a[:2]))
    view = research.read_view("600276", app, saved_only=True)
    assert view["recovery"] == {"state": "not_checked", "source_task_id": task_id, "run_id": run_id, "blockers": []}
    assert view["task"]["result_run_id"] is None and checked == []
    view = research.read_view("600276", app, saved_only=True, check_recovery=True)
    assert view["recovery"]["state"] == "available" and checked == [(task_id, run_id)]
    assert research.read_view("600150", app, saved_only=True)["recovery"]["state"] == "none"


@pytest.mark.parametrize("fault", ["legacy", "wrong_symbol", "two_runs", "grant_denied"])
def test_recovery_projection_never_guesses_latest_run_or_grants_a_denied_source(env, monkeypatch, fault):
    from app.services import tradingagents_recovery as recovery
    sf, app = env
    task_id, run_id = "a" * 32, "b" * 32
    with sf() as db:
        db.add(AgentTask(id=task_id, type="tradingagents_research", status="failed", params='{"symbol":"600276"}'))
        db.commit()
    run = research.ROOT / "600276" / run_id; run.mkdir(parents=True)
    value = {"state": "canceled", "task_id": task_id, "run_id": run_id, "symbol": "600276"}
    if fault == "legacy": value.pop("task_id")
    if fault == "wrong_symbol": value["symbol"] = "600150"
    (run / "execution.json").write_text(json.dumps(value))
    if fault == "two_runs":
        other = run.parent / ("c" * 32); other.mkdir()
        (other / "execution.json").write_text(json.dumps({**value, "run_id": other.name}))
    monkeypatch.setattr(research, "_latest", lambda *a: None)
    def denied(*a, **kw): raise research.ResearchBlocked([{"code": "recovery_grant_missing", "message": "Exact original grant required"}])
    monkeypatch.setattr(recovery, "_context", denied)
    view = research.read_view("600276", app, saved_only=True, check_recovery=True)
    assert view["recovery"]["state"] == ("blocked" if fault == "grant_denied" else "unknown")
    assert view["recovery"]["blockers"] and view["task"]["result_run_id"] is None


@pytest.mark.parametrize("status,symbol,run_id,expected", [
    ("succeeded", "600276", "b" * 32, "b" * 32), ("failed", "600276", "b" * 32, None),
    ("succeeded", "600150", "b" * 32, None), ("succeeded", "600276", "../latest", None)])
def test_report_run_belongs_to_exact_successful_task(env, status, symbol, run_id, expected):
    sf, _ = env
    with sf() as db:
        task = AgentTask(id="a" * 32, type="tradingagents_research", status=status, params='{"symbol":"600276"}',
            result_ref=json.dumps({"symbol": symbol, "run_id": run_id}))
        db.add(task); db.commit()
        assert research._public_task(task)["result_run_id"] == expected


def test_real_assembled_recovery_check_requires_maintenance_and_ordinary_query_cannot_trigger_it(env,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core import device_access as access
    monkeypatch.setattr(access,"STORE_PATH",tmp_path/'paired.sqlite3')
    monkeypatch.setattr(settings,"api_token","fixture-service")
    store=access.get_device_store();origin='http://device.test:3000'
    admin,_=store.bootstrap(store.bootstrap_code(),'电脑',origin)
    mobile,mobile_row=store.start('普通设备',origin)
    store.manage(admin,origin,mobile_row['id'],'approve')
    headers={'X-API-Token':'fixture-service',access.BROWSER_HEADER:'1',access.SESSION_HEADER:mobile,access.ORIGIN_HEADER:origin}
    observed=[]
    def read(symbol,application,*,saved_only=False,check_recovery=False,**kwargs):
        observed.append((symbol,saved_only,check_recovery))
        return {'symbol':symbol,'latest':None,'recovery':{'state':'not_checked' if not check_recovery else 'blocked',
            'source_task_id':None,'run_id':None,'blockers':[]}}
    monkeypatch.setattr(research,'read_view',read)
    probe=TestClient(app)  # Actual mounted routers/auth, without any lifespan worker.
    path='/api/agent/research/tradingagents/600276'
    response=probe.get(path+'?saved_only=true&check_recovery=true',headers=headers)
    assert response.status_code==200 and observed==[('600276',True,False)]
    assert probe.get(path+'/recovery?saved_only=true',headers=headers).status_code==403
    assert len(observed)==1
    store.manage(admin,origin,mobile_row['id'],'grant-maintenance')
    response=probe.get(path+'/recovery?saved_only=false',headers=headers)
    assert response.status_code==200 and observed[-1]==('600276',True,True)
    store.manage(admin,origin,mobile_row['id'],'end-maintenance')
    assert probe.get(path+'/recovery',headers=headers).status_code==403
    assert probe.get(path+'?saved_only=true',headers=headers).status_code==200
    assert observed[-1]==('600276',True,False) and store.authorize(admin,origin)['maintenance'] is False
