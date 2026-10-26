"""Local explicit rebind: no model, real environment, source or business DB."""
from copy import deepcopy
from datetime import timedelta
import fcntl
import hashlib
import json
import os
import stat
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import beijing_now
from app.core.config import settings
from app.models.agent import AgentResourceUsage, AgentTask
from app.models.watchlist import Base
from app.services import _tradingagents_authorization as owner, tradingagents_research as research


@pytest.fixture
def env(tmp_path, monkeypatch):
    now = beijing_now()
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    root = tmp_path / "research"
    root.mkdir()
    old = {"upstream_sha": "1" * 40, "transport": {"provider": "codex_cli", "model": "gpt-6.1-sol", "effort": "medium"},
           "dependencies": {"fixed": "2" * 64}, "packet_builder": {"fixed": "3" * 64},
           "source_files": {"driver.py": "a" * 64, "tradingagents_research.py": "b" * 64,
                            "tradingagents_recovery.py": "c" * 64, "worker.py": "d" * 64}}
    current = deepcopy(old)
    current["source_files"].update({"tradingagents_research.py": "e" * 64, "tradingagents_recovery.py": "f" * 64})
    with sf() as db:
        for n in range(1, 8):
            db.add(AgentResourceUsage(id=f"{n:032x}", budget_date=now.date().isoformat(), scope="selected_research_llm",
                kind="model", purpose="original fixture", state="failed", usage_known=0,
                started_at=(now - timedelta(minutes=5)).replace(tzinfo=None)))
        db.commit()
    auth = {"version": 1, "mode": "RESEARCH_ONLY_AUTHORIZED", "consumer": "selected_stock_explanation",
            "user_authorization": "EXPLICIT", "created_at": (now - timedelta(days=1)).isoformat(),
            "valid_until": (now + timedelta(days=7)).isoformat(), "method_identity": old,
            "net_gain_verified": False, "production_eligible": False,
            "native_recovery_grants": [{"historical": "opaque expired fixture retained exactly"}],
            "unknown_usage_exception": {"version": 1, "user_authorization": "EXPLICIT", "reason": "Original named fixture permission.",
                "authorized_at": (now - timedelta(minutes=1)).isoformat(), "method_sha256": research._digest(old),
                "allowed_unknown_usage": [{"usage_id": f"{n:032x}", "budget_date": now.date().isoformat(),
                    "scope": "selected_research_llm"} for n in range(1, 8)]}}
    before = json.dumps(auth, ensure_ascii=False, indent=2).encode()
    (root / "authorization.json").write_bytes(before)
    pin = hashlib.sha256(before).hexdigest()
    env_path = tmp_path / ".env"
    env_before = ("UNRELATED_SECRET=fixture-secret-never-copy\r\nexport " + owner.PIN_KEY
                  + " = '" + pin + "' # retained comment\r\nOTHER_OPTION=untouched\r\n").encode()
    env_path.write_bytes(env_before)
    monkeypatch.setattr(settings, "tradingagents_research_enabled", True)
    monkeypatch.setattr(settings, "tradingagents_auto_research_enabled", False)
    monkeypatch.setattr(settings, "tradingagents_research_authorization_digest", pin)
    driver = SimpleNamespace(method_identity=lambda: deepcopy(current))
    value = SimpleNamespace(root=root, env_path=env_path, sf=sf, driver=driver, now=now, auth=auth,
        before=before, env_before=env_before, current=current)
    value.kwargs = {k: getattr(value, k) for k in ("root", "env_path", "sf", "driver", "now")}
    yield value
    engine.dispose()


def prepare(env):
    return owner.prepare_rebind(["tradingagents_research.py", "tradingagents_recovery.py"], **env.kwargs)


def test_default_inspection_is_read_only_and_has_no_sensitive_values(env):
    value = owner.inspect_rebind(**env.kwargs)
    assert value["state"] == "dry_run" and value["unknown_count"] == 7
    assert value["changed_sources"] == ["tradingagents_recovery.py", "tradingagents_research.py"]
    assert sorted(p.name for p in env.root.iterdir()) == ["authorization.json"]
    assert (env.root / "authorization.json").read_bytes() == env.before and env.env_path.read_bytes() == env.env_before
    assert "fixture-secret" not in json.dumps(value) and "method_sha256" not in value


def test_prepared_exact_rebind_preserves_scope_dates_unknowns_grants_and_other_env_bytes(env):
    plan = prepare(env)
    folder = env.root / "authorization-maintenance" / plan["plan_id"]
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in folder.iterdir())
    assert all(b"fixture-secret-never-copy" not in p.read_bytes() for p in folder.iterdir())
    assert (folder / "authorization.before.json").read_bytes() == env.before
    applied = owner.apply_plan(plan["plan_id"], **env.kwargs)
    assert applied["state"] == "applied" and applied["expiry_extended"] is False
    actual = json.loads((env.root / "authorization.json").read_bytes())
    expected = deepcopy(env.auth)
    expected["method_identity"] = env.current
    expected["unknown_usage_exception"]["method_sha256"] = research._digest(env.current)
    assert actual == expected
    old_pin, span = owner._pin(env.env_before)
    new_env = env.env_path.read_bytes()
    assert new_env[:span[0]] == env.env_before[:span[0]] and new_env[span[1]:] == env.env_before[span[1]:]
    assert owner._pin(new_env)[0] == hashlib.sha256((env.root / "authorization.json").read_bytes()).hexdigest() != old_pin


@pytest.mark.parametrize("mutation", ["consumer", "term", "model", "deps", "builder", "graph", "auto", "active", "new_unknown"])
def test_scope_or_observation_changes_never_become_rebind_permission(env, monkeypatch, mutation):
    if mutation in {"consumer", "term"}:
        value = deepcopy(env.auth)
        value["consumer" if mutation == "consumer" else "valid_until"] = "other" if mutation == "consumer" else (env.now - timedelta(days=1)).isoformat()
        raw = json.dumps(value).encode(); (env.root / "authorization.json").write_bytes(raw)
        pin = owner._sha(raw)
        env.env_path.write_bytes((owner.PIN_KEY + "=" + pin).encode())
        monkeypatch.setattr(settings, "tradingagents_research_authorization_digest", pin)
    elif mutation == "model": env.current["transport"]["model"] = "another-model"
    elif mutation == "deps": env.current["dependencies"]["fixed"] = "4" * 64
    elif mutation == "builder": env.current["packet_builder"]["fixed"] = "5" * 64
    elif mutation == "graph": env.current["source_files"]["worker.py"] = "6" * 64
    elif mutation == "auto": monkeypatch.setattr(settings, "tradingagents_auto_research_enabled", True)
    else:
        with env.sf() as db:
            if mutation == "active": db.add(AgentTask(id="f" * 32, type="tradingagents_research", status="queued", params="{}"))
            else: db.add(AgentResourceUsage(id="e" * 32, budget_date=env.now.date().isoformat(), scope="selected_research_llm", kind="model", purpose="unpermitted fixture", state="failed", usage_known=0, started_at=env.now.replace(tzinfo=None)))
            db.commit()
    with pytest.raises((ValueError, RuntimeError)):
        owner.inspect_rebind(**env.kwargs)
    assert not (env.root / "authorization-maintenance").exists()


@pytest.mark.parametrize("mutation", ["environment", "original", "target", "plan_term", "plan_unknown", "plan_grants"])
def test_plan_cas_rejects_every_changed_input_before_installation(env, mutation):
    plan = prepare(env)
    if mutation == "environment": env.env_path.write_bytes(env.env_before + b"NEW_SETTING=changed\n")
    elif mutation == "original": (env.root / "authorization.json").write_bytes(env.before + b" ")
    elif mutation == "target": env.current["source_files"]["tradingagents_research.py"] = "7" * 64
    else:
        path = env.root / "authorization-maintenance" / plan["plan_id"] / "plan.json"
        value = json.loads(path.read_bytes())
        if mutation == "plan_term": value["candidate"]["valid_until"] = (env.now + timedelta(days=30)).isoformat()
        elif mutation == "plan_unknown": value["candidate"]["unknown_usage_exception"]["allowed_unknown_usage"].pop()
        else: value["candidate"]["native_recovery_grants"] = []
        path.write_text(json.dumps(value))
    before = (env.root / "authorization.json").read_bytes(), env.env_path.read_bytes()
    with pytest.raises(owner.AuthorizationBlocked): owner.apply_plan(plan["plan_id"], **env.kwargs)
    assert before == ((env.root / "authorization.json").read_bytes(), env.env_path.read_bytes())


def test_environment_write_failure_rolls_back_only_our_authorization(env, monkeypatch):
    plan = prepare(env)
    original_write = owner._write
    def fail_env(path, raw, **kwargs):
        if path == env.env_path: raise OSError("isolated fixture write failure")
        return original_write(path, raw, **kwargs)
    monkeypatch.setattr(owner, "_write", fail_env)
    with pytest.raises(OSError): owner.apply_plan(plan["plan_id"], **env.kwargs)
    assert (env.root / "authorization.json").read_bytes() == env.before
    assert env.env_path.read_bytes() == env.env_before


@pytest.mark.parametrize("scope", [[], ["driver.py"], ["tradingagents_research.py"]])
def test_prepare_requires_exact_reviewed_changed_sources(env, scope):
    with pytest.raises(owner.AuthorizationBlocked, match="exact_source_review_required"):
        owner.prepare_rebind(scope, **env.kwargs)
    assert not (env.root / "authorization-maintenance").exists()


def test_duplicate_pin_or_symlink_is_not_a_maintenance_target(env):
    env.env_path.write_bytes(env.env_before + (owner.PIN_KEY + "=" + "a" * 64).encode())
    with pytest.raises(owner.AuthorizationBlocked, match="pin_line_invalid"): owner.inspect_rebind(**env.kwargs)
    env.env_path.unlink(); env.env_path.symlink_to(env.root / "authorization.json")
    with pytest.raises(owner.AuthorizationBlocked, match="path_unsafe"): owner.inspect_rebind(**env.kwargs)


@pytest.mark.parametrize("lease", ["worker", "execution", "slot", "usage"])
def test_orphan_os_lease_blocks_rebind_even_with_zero_active_database_tasks(env, lease):
    plan = prepare(env)
    if lease in {"worker", "execution"}:
        folder = env.root / "600001" if lease == "worker" else env.root / "selected-execution/600001"
        folder.mkdir(parents=True)
        path = folder / ".worker.lock"
    else:
        folder = env.root / "model-inflight"; folder.mkdir(mode=0o700)
        path = folder / ("slot-0.lock" if lease == "slot" else "a" * 32 + ".json")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(owner.AuthorizationBlocked, match="runtime_lease_held"):
            owner.inspect_rebind(**env.kwargs)
        with pytest.raises(owner.AuthorizationBlocked, match="runtime_lease_held"):
            owner.apply_plan(plan["plan_id"], **env.kwargs)
        assert (env.root / "authorization.json").read_bytes() == env.before and env.env_path.read_bytes() == env.env_before
    finally:
        os.close(fd)


def test_installation_holds_existing_runtime_admission_leases_across_both_pin_writes(env, monkeypatch):
    plan = prepare(env)
    real_write, observed = owner._write, []
    def protected_write(path, raw, **kwargs):
        if path in {env.root / "authorization.json", env.env_path}:
            for target in [env.root / "selection-claims/.worker.lock", env.root / "selected-dispatch/.worker.lock",
                           *[env.root / f"model-inflight/slot-{n}.lock" for n in range(3)]]:
                fd = os.open(target, os.O_RDONLY)
                try:
                    with pytest.raises(BlockingIOError): fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally: os.close(fd)
            observed.append(path.name)
        return real_write(path, raw, **kwargs)
    monkeypatch.setattr(owner, "_write", protected_write)
    assert owner.apply_plan(plan["plan_id"], **env.kwargs)["state"] == "applied"
    assert observed == ["authorization.json", ".env"]


def test_cli_explains_initial_analysis_stage_without_exposing_raw_recovery_errors(monkeypatch,capsys):
    from scripts.tradingagents_authorization import main
    def blocked(**kwargs):
        raise research.ResearchBlocked([{"code":"recovery_cooperative_checkpoint_invalid","message":"PRIVATE_CONTEXT_NOT_FOR_OUTPUT"}])
    monkeypatch.setattr(owner,"inspect_rebind",blocked)
    assert main([])==1
    value=json.loads(capsys.readouterr().out)
    assert value["code"]=="recovery_cooperative_checkpoint_invalid"
    assert "初始四分析师" in value["message"] and "root step 0" in value["message"]
    assert "PRIVATE_CONTEXT" not in str(value)


def test_cli_help_states_recovery_scope_and_unknown_errors_are_sanitized(monkeypatch,capsys):
    from scripts.tradingagents_authorization import main
    with pytest.raises(SystemExit) as error:main(["--help"])
    assert error.value.code==0 and "初始四分析师" in capsys.readouterr().out
    def unavailable(**kwargs):raise owner.AuthorizationBlocked("/private/DO_NOT_PRINT")
    monkeypatch.setattr(owner,"inspect_rebind",unavailable)
    assert main([])==1
    assert json.loads(capsys.readouterr().out)=={"state":"blocked","code":"authorization_maintenance_unavailable"}
