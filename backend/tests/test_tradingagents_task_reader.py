"""Exact task/run reads use isolated files and DB; no model or recovery admission."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.models.agent import AgentResourceUsage, AgentTask
from app.models.watchlist import Base
from app.services import tradingagents_research as research


NOW = datetime(2026, 11, 13, 15, 30, tzinfo=BJ_TZ)
SYMBOL, TASK, RUN = "600276", "a" * 32, "b" * 32
LATEST_READER = research._latest


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def forbidden(*args, **kwargs):
    pytest.fail("saved task reading must not run current admission, collection or models")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'tasks.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    monkeypatch.setattr(research, "ROOT", tmp_path / "reports")
    monkeypatch.setattr(research, "get_session_factory", lambda: sf)
    monkeypatch.setattr(research, "_now", lambda: NOW)
    for name in ("preflight", "require_start", "_driver", "_latest", "_read_task_state", "selected_bindings",
                 "_materialize_completed_summary"):
        monkeypatch.setattr(research, name, forbidden)
    from app.services import agent_budget, company_evidence, selected_model_lease
    from app.services import _tradingagents_recovery_view, _tradingagents_summary, tradingagents_recovery
    for name in ("reserve", "start", "budget_status", "ensure_model_usage_known"):
        monkeypatch.setattr(agent_budget, name, forbidden)
    monkeypatch.setattr(company_evidence, "run_company_evidence", forbidden)
    monkeypatch.setattr(selected_model_lease, "authorization", forbidden)
    monkeypatch.setattr(_tradingagents_recovery_view, "projection", forbidden)
    monkeypatch.setattr(tradingagents_recovery, "_context", forbidden)
    monkeypatch.setattr(_tradingagents_summary, "materialize", forbidden)
    yield SimpleNamespace(sf=sf, root=research.ROOT, tmp=tmp_path)
    engine.dispose()


def archive(env, *, run=RUN, receipt="attempt", **changes):
    folder = env.root / SYMBOL / run
    packet = {"case_id": "isolated-fixture", "as_of": (NOW - timedelta(days=1)).isoformat()}
    result = {
        "version": 1, "state": "completed", "symbol": SYMBOL, "run_id": run,
        "generated_at": (NOW - timedelta(hours=1)).isoformat(), "as_of": packet["as_of"],
        "upstream_sha": research.UPSTREAM_SHA, "input_sha256": research._digest(packet),
        "source_input_sha256": "e" * 64, "method_sha256": "d" * 64,
        "verification": research.VERIFICATION, "production_eligible": False,
        "review_state": "unreviewed", "review_summary": {"finite_failures": 0, "peer_review_required": 7,
            "source_verified": False, "independent_human_gold": False},
        "reports": {key: f"{run}: {key} 未验证研究，不构成买卖建议。" for key in research.REPORTS},
        "context": "private-fixture-must-not-leak", "host_path": "private-fixture-path", **changes,
    }
    digest = put(folder / "result.json", result)
    put(folder / "packet.json", packet)
    key = research._digest({"input": result.get("source_input_sha256", result["input_sha256"]),
                            "method": result["method_sha256"]})
    attempt = folder.parent / ("attempt-" + key + ".json")
    claim = {"run_id": run, "input_sha256": result["input_sha256"], "method_sha256": result["method_sha256"],
             "source_input_sha256": result.get("source_input_sha256", result["input_sha256"]), "result_sha256": digest}
    if receipt == "attempt":
        put(attempt, claim)
    elif receipt == "pointer":
        put(folder.parent / "latest.json", {"run_id": run, "result_sha256": digest})
    return SimpleNamespace(folder=folder, result=result, digest=digest, attempt=attempt, claim=claim)


def task(env, *, identity=TASK, type_="tradingagents_research", status="succeeded", symbol=SYMBOL, ref=None, params=None):
    with env.sf() as db:
        db.add(AgentTask(id=identity, type=type_, status=status,
            params=json.dumps(params if params is not None else {"symbol": symbol}),
            result_ref=json.dumps(ref if ref is not None else {"kind": "tradingagents_research", "symbol": symbol, "run_id": RUN}),
            created_at=NOW.replace(tzinfo=None), finished_at=NOW.replace(tzinfo=None)))
        db.commit()


def snapshot(env):
    files = {str(p.relative_to(env.root)): p.read_bytes() for p in env.root.rglob("*") if p.is_file()}
    with env.sf() as db:
        rows = [(r.id, r.status, r.params, r.result_ref, r.error) for r in db.scalars(select(AgentTask))]
        usage = list(db.scalars(select(AgentResourceUsage.id)))
    return files, rows, usage


def read(env):
    return research.read_task_view(SYMBOL, TASK, session_factory=env.sf)


def test_old_task_reads_original_run_despite_new_pointer_and_task_without_mutation(env):
    old = archive(env)
    # Different content/method has a separate native attempt owner.
    new = archive(env, run="c" * 32, method_sha256="f" * 64)
    put(new.folder.parent / "latest.json", {"run_id": "c" * 32, "result_sha256": new.digest})
    task(env)
    task(env, identity="f" * 32, status="running", ref={"symbol": SYMBOL, "run_id": "c" * 32})
    before = snapshot(env)
    view = read(env)
    assert view["saved_result_state"] == "available" and view["latest"]["run_id"] == RUN
    assert view["latest"]["reports"] == old.result["reports"]
    assert view["requested_task_id"] == view["task"]["id"] == TASK
    assert view["task"]["status"] == "succeeded" and view["task"]["result_run_id"] == RUN
    assert view["executable"] is False and view["startup_conditions_not_checked"] is True
    assert view["budget"] is None and view["required_model_slots"] is None
    assert view["admission"] == {"state": "not_checked", "method_sha256": None}
    assert view["recovery"] == {"state": "not_checked", "source_task_id": None, "run_id": None, "blockers": []}
    assert view["latest"]["production_eligible"] is False
    assert "private-fixture" not in json.dumps(view) and "context" not in view["latest"]
    assert snapshot(env) == before


@pytest.mark.parametrize("case", ["missing", "type", "symbol", "invalid_id", "bad_params"])
def test_missing_or_foreign_task_does_not_read_an_archive(env, monkeypatch, case):
    if case != "missing":
        task(env, type_="review" if case == "type" else "tradingagents_research",
             symbol="600150" if case == "symbol" else SYMBOL, params=[] if case == "bad_params" else None)
    monkeypatch.setattr(research, "_folder", forbidden)
    with pytest.raises(LookupError):
        research.read_task_view(SYMBOL, "../latest" if case == "invalid_id" else TASK, session_factory=env.sf)


@pytest.mark.parametrize("status", ["queued", "running", "failed", "canceled", "needs_confirm"])
def test_non_success_does_not_borrow_a_report_or_recovery_state(env, monkeypatch, status):
    task(env, status=status)
    monkeypatch.setattr(research, "_folder", forbidden)
    value = read(env)
    assert value["latest"] is None and value["saved_result_state"] == "unavailable"
    assert value["task"]["id"] == TASK and value["task"]["status"] == status


@pytest.mark.parametrize("ref", [None, [], "bad", {}, {"symbol": SYMBOL, "run_id": RUN},
    {"kind": "other", "symbol": SYMBOL, "run_id": RUN},
    {"kind": "tradingagents_research", "symbol": "600150", "run_id": RUN},
    {"kind": "tradingagents_research", "symbol": SYMBOL, "run_id": "../latest"}])
def test_bad_ref_is_unavailable_without_archive_lookup(env, monkeypatch, ref):
    task(env)
    with env.sf() as db:
        db.get(AgentTask, TASK).result_ref = json.dumps(ref)
        db.commit()
    monkeypatch.setattr(research, "_folder", forbidden)
    assert read(env)["saved_result_state"] == "unavailable"


@pytest.mark.parametrize("fault", ["no_receipt", "tamper", "attempt_hash", "attempt_run", "attempt_method",
    "conflicting_pointer", "oversize", "malformed_result", "wrong_result_run"])
def test_archive_or_publication_drift_never_falls_back(env, fault):
    item = archive(env, receipt="none" if fault == "no_receipt" else "attempt")
    task(env)
    if fault == "tamper":
        (item.folder / "result.json").write_bytes((item.folder / "result.json").read_bytes() + b" ")
    elif fault.startswith("attempt_"):
        field = {"attempt_hash": "result_sha256", "attempt_run": "run_id", "attempt_method": "method_sha256"}[fault]
        put(item.attempt, {**item.claim, field: "9" * (32 if field == "run_id" else 64)})
    elif fault == "conflicting_pointer":
        put(item.folder.parent / "latest.json", {"run_id": RUN, "result_sha256": "9" * 64})
    elif fault == "oversize":
        (item.folder / "result.json").write_bytes(b" " * (research.MAX_FILE_BYTES + 1))
    elif fault == "malformed_result":
        (item.folder / "result.json").write_text("{broken", encoding="utf-8")
    elif fault == "wrong_result_run":
        digest = put(item.folder / "result.json", {**item.result, "run_id": "9" * 32})
        put(item.attempt, {**item.claim, "result_sha256": digest})
    view = read(env)
    assert view["latest"] is None and view["saved_result_state"] == "unavailable"


@pytest.mark.parametrize("change", [
    {"symbol": "600150"}, {"state": "running"}, {"production_eligible": True}, {"reports": {}},
    {"verification": "verified"}, {"review_state": "verified"}, {"review_summary": {}},
    {"generated_at": (NOW + timedelta(seconds=1)).isoformat()},
    {"as_of": (NOW + timedelta(days=1)).isoformat()}, {"as_of": "2026-11-13T10:00:00"},
])
def test_shared_report_validation_is_not_weakened_by_a_consistent_hash(env, change):
    archive(env, **change)
    task(env)
    assert read(env)["saved_result_state"] == "unavailable"


def test_exact_current_pointer_remains_a_legacy_publication_receipt(env):
    archive(env, receipt="pointer")
    task(env)
    assert read(env)["saved_result_state"] == "available"


def test_existing_latest_reader_keeps_pointer_and_digest_semantics(env):
    original = archive(env, receipt="pointer")
    assert LATEST_READER(SYMBOL)["reports"] == original.result["reports"]
    (original.folder / "result.json").write_bytes((original.folder / "result.json").read_bytes() + b" ")
    assert LATEST_READER(SYMBOL) is None
    newer = archive(env, run="c" * 32, receipt="pointer", method_sha256="f" * 64)
    assert LATEST_READER(SYMBOL)["run_id"] == newer.result["run_id"]


def test_malformed_persisted_ref_is_unavailable(env):
    task(env)
    with env.sf() as db:
        db.get(AgentTask, TASK).result_ref = "{broken"
        db.commit()
    assert read(env)["saved_result_state"] == "unavailable"


def test_legacy_attempt_uses_input_hash_when_source_identity_field_is_absent(env):
    item = archive(env, receipt="none")
    item.result.pop("source_input_sha256")
    digest = put(item.folder / "result.json", item.result)
    key = research._digest({"input": item.result["input_sha256"], "method": item.result["method_sha256"]})
    put(item.folder.parent / ("attempt-" + key + ".json"), {"run_id": RUN,
        "input_sha256": item.result["input_sha256"], "method_sha256": item.result["method_sha256"], "result_sha256": digest})
    task(env)
    assert read(env)["saved_result_state"] == "available"


def test_same_stock_reuse_keeps_selected_task_even_when_native_owner_is_different(env):
    item = archive(env)
    put(item.folder / "execution.json", {"state": "completed", "symbol": SYMBOL, "run_id": RUN, "task_id": "9" * 32})
    task(env, ref={"kind": "tradingagents_research", "symbol": SYMBOL, "run_id": RUN, "reused_identical_selection": True})
    view = read(env)
    assert view["saved_result_state"] == "available" and view["task"]["id"] == TASK
    assert view["task"]["reused_identical_selection"] is True


@pytest.mark.parametrize("damage", [False, True])
def test_twelve_roles_use_the_exact_archive_and_corruption_only_degrades_roles(env, damage):
    item = archive(env)
    packet = json.loads((item.folder / "packet.json").read_text(encoding="utf-8"))
    rows = []
    for call, role in enumerate(research.ROLES, 1):
        output = {research.ROLE_FIELDS.get(role, ("content",))[0]: f"original {role}"}
        digest = research._digest(output)
        rows.append({"peer": {"role": role, "call_id": call, "output": output, "accepted": True,
            "state": "accepted", "case_id": packet["case_id"], "arm": "ta-luna", "output_sha256": digest}})
        put(item.folder / "model-outputs" / f"{call}.json", {"call_id": call, "role": role,
            "structured_output": {} if damage and call == 1 else output, "output_sha256": digest})
    (item.folder / "role-outputs.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    task(env)
    view = read(env)
    assert view["saved_result_state"] == "available" and len(view["latest"]["reports"]) == 7
    assert view["latest"]["role_projection_state"] == ("unavailable" if damage else "available")
    assert len(view["latest"]["roles"]) == (0 if damage else 12)


@pytest.mark.parametrize("fault", ["none", "hash", "lineage", "completion", "version", "receipt_time"])
def test_recovered_run_uses_completed_selection_receipt_and_existing_lineage_validator(env, monkeypatch, fault):
    from app.services import tradingagents_recovery as recovery

    binding = {"symbol": SYMBOL, "trade_date": "2026-11-12", "selected_at": (NOW - timedelta(days=1)).isoformat(),
               "source": "daily_generation", "source_version": "frozen-fixture"}
    binding["selection_key"] = research._digest(binding)
    lineage = {"version": 1, "source_task_id": "9" * 32, "source_run_id": RUN, "recovery_task_id": TASK,
        "source_method_sha256": "e" * 64, "recovery_method_sha256": "d" * 64,
        "reused_original_outputs": 1, "new_call_from": 2, "new_call_to": 2, "inherited_unknown_usage_count": 0}
    if fault == "lineage":
        lineage["source_run_id"] = "8" * 32
    item = archive(env, receipt="none", authorization_mode="RESEARCH_ONLY_AUTHORIZED", net_gain_verified=False,
                   selection=binding, recovery_lineage=lineage)
    # Same-method native failure may have a valid attempt identity but no final digest.
    put(item.attempt, {k: v for k, v in item.claim.items() if k != "result_sha256"})
    claim = {"version": 1, "selection": binding, "method_sha256": "d" * 64, "state": "completed",
        "task_id": TASK, "run_id": RUN, "result_sha256": "9" * 64 if fault == "hash" else item.digest,
        "updated_at": NOW.isoformat()}
    if fault == "version":
        claim["version"] = True
    elif fault == "receipt_time":
        claim["updated_at"] = "2026-11-13T15:30:00"
    put(research._selection_claim(binding, "d" * 64), claim)
    task(env, params={"symbol": SYMBOL, "native_recovery": {"run_id": RUN, "source_task_id": "9" * 32}})
    checked = []

    def completed(records, selected, method):
        checked.append((records, selected, method))
        if fault == "completion":
            raise research.ResearchBlocked([{"code": "recovery_completion_drift", "message": "fixture denied"}])
        return claim

    monkeypatch.setattr(recovery, "completed_recovery", completed)
    view = read(env)
    assert view["saved_result_state"] == ("available" if fault == "none" else "unavailable")
    assert bool(checked) is (fault not in {"hash", "version", "receipt_time"})
    if checked:
        assert checked[0][1:] == (binding, "d" * 64)


@pytest.mark.parametrize("target", ["run", "result", "attempt"])
def test_symlinked_saved_evidence_is_unavailable(env, target):
    item = archive(env)
    task(env)
    source = {"run": item.folder, "result": item.folder / "result.json", "attempt": item.attempt}[target]
    destination = env.tmp / ("moved-" + source.name)
    source.rename(destination)
    source.symlink_to(destination, target_is_directory=target == "run")
    assert read(env)["saved_result_state"] == "unavailable"


def test_real_route_requires_auth_and_only_exact_new_path_has_ordinary_read_capability(env, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core import device_access as access
    from app.core.config import settings

    monkeypatch.setattr(access, "STORE_PATH", env.tmp / "paired.sqlite3")
    monkeypatch.setattr(settings, "api_token", "fixture-service")
    store = access.get_device_store()
    origin = "http://device.test:3000"
    admin, _ = store.bootstrap(store.bootstrap_code(), "fixture-admin", origin)
    mobile, pending = store.start("fixture-reader", origin)
    store.manage(admin, origin, pending["id"], "approve")
    headers = {"X-API-Token": "fixture-service", access.BROWSER_HEADER: "1", access.SESSION_HEADER: mobile,
               access.ORIGIN_HEADER: origin}
    archive(env)
    task(env)
    client = TestClient(app)  # No lifespan worker or network call.
    path = f"/api/agent/research/tradingagents/{SYMBOL}/tasks/{TASK}"
    assert client.get(path).status_code == 401
    response = client.get(path + "?run_id=" + "9" * 32 + "&check_recovery=true&saved_only=false", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["latest"]["run_id"] == RUN
    assert response.json()["data"]["requested_task_id"] == TASK
    assert client.get(path.replace(TASK, "f" * 32), headers=headers).status_code == 404
    assert client.get(f"/api/agent/research/tradingagents/600150/tasks/{TASK}", headers=headers).status_code == 404
    assert client.get(f"/api/agent/research/tradingagents/{SYMBOL}/recovery", headers=headers).status_code == 403
    # The explicit task reader cannot grant read access to an adjacent template.
    assert client.get(f"/api/agent/tasks/{TASK}", headers=headers).status_code == 403
    with env.sf() as db:
        db.get(AgentTask, TASK).status = "failed"
        db.commit()
    unavailable = client.get(path, headers=headers)
    assert unavailable.status_code == 200
    assert unavailable.json()["data"]["saved_result_state"] == "unavailable"
    assert unavailable.json()["data"]["latest"] is None
