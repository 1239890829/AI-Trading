"""Local explicit rebind: no model, real environment, source or business DB."""
from copy import deepcopy
from datetime import timedelta
import ast
import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import beijing_now
from app.core.config import settings
from app.models.agent import AgentResourceUsage, AgentTask
from app.models.watchlist import Base
from app.services import _tradingagents_authorization as owner, tradingagents_research as research

OWNER_PATH = "data_providers/tencent.py"
OWNER_REVIEW = "packet_builder.owner_sources." + OWNER_PATH
OLD_OWNER = "32699b46cc03f0038e8e9d4969461bd336d9465c0363f984f8dca56b0b828aa9"
NEW_OWNER = "73af8b2ff111c04b57deda0dc3432c678815d0c2cef0c884f6f022ac51ea1af5"
# The old and new complete module were compared locally before implementation.
# CI has a shallow checkout: verify the current reviewed bytes and common AST,
# without fetching history or copying an obsolete provider into the repository.
UNCHANGED_OWNER_AST = "e7a8c6c8fca612f13191f720940e47be30a30a9dfe9a7be75c3735e3ebd7efa1"
HUB_OWNER_PATH = "services/quote_hub.py"
HUB_OWNER_REVIEW = "packet_builder.owner_sources." + HUB_OWNER_PATH
OLD_HUB_OWNER = "5d970afc948ea70e8d5ef3cc6a1efe68d2a3df09d3044fc1c92c405c864db0b0"
# Independent whole-module pin for the reviewed quality/diagnostic/WS revision.
NEW_HUB_OWNER = "c4386ec9cd0d10e0a5e3931e4ee0046cfff48b4b35e70ae99a3cccee8f5273ab"


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


def _replace_original(env, monkeypatch, identity):
    env.auth["method_identity"] = deepcopy(identity)
    env.auth["unknown_usage_exception"]["method_sha256"] = research._digest(identity)
    env.before = json.dumps(env.auth, ensure_ascii=False, indent=2).encode()
    (env.root / "authorization.json").write_bytes(env.before)
    pin = owner._sha(env.before)
    _, span = owner._pin(env.env_before)
    env.env_before = env.env_before[:span[0]] + pin.encode() + env.env_before[span[1]:]
    env.env_path.write_bytes(env.env_before)
    monkeypatch.setattr(settings, "tradingagents_research_authorization_digest", pin)


@pytest.fixture
def native_owner_env(env, tmp_path, monkeypatch):
    """Use the real Native method/StoredBuilder shape, without executing its graph."""
    from app.services import tradingagents_driver as native

    upstream, native_root = tmp_path / "upstream", tmp_path / "native"
    upstream.mkdir(); native_root.mkdir()
    (upstream / "a.py").write_text("isolated = True\n")
    manifest = {"upstream_sha": research.UPSTREAM_SHA,
                "source_hashes": {"a.py": native.file_sha(upstream / "a.py")}}
    (native_root / "upstream-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(native, "NATIVE", native_root)
    transport_identity = deepcopy(env.current["transport"])
    transport = SimpleNamespace(method_identity=lambda: deepcopy(transport_identity))
    driver = native.NativeTradingAgentsDriver(upstream_dir=upstream, python=Path(__file__), dependencies=tmp_path,
        transport=transport, sandbox_executable=__file__)
    dependency_identity = {"fixed": "2" * 64}
    monkeypatch.setattr(driver, "_dependency_identity", lambda: deepcopy(dependency_identity))
    packet_identity = driver.packet_builder.method_identity
    overrides = {"packet": lambda value: None}

    def current_packet():
        value = packet_identity()
        overrides["packet"](value)
        return value

    monkeypatch.setattr(driver.packet_builder, "method_identity", current_packet)
    identity = driver.method_identity()
    assert identity["packet_builder"]["owner_sources"][OWNER_PATH] == NEW_OWNER
    old = deepcopy(identity)
    old["packet_builder"]["owner_sources"][OWNER_PATH] = OLD_OWNER
    _replace_original(env, monkeypatch, old)
    env.driver, env.kwargs["driver"] = driver, driver
    env.current = identity
    env.native = SimpleNamespace(module=native, packet_overrides=overrides, transport=transport_identity,
                                 dependencies=dependency_identity)
    return env


def test_reviewed_owner_proof_matches_current_whole_module_and_unaffected_ast():
    from app.services import tradingagents_driver as native

    path = Path(native.__file__).resolve().parents[1] / OWNER_PATH
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == NEW_OWNER
    tree = ast.parse(raw)
    tree.body = [node for node in tree.body if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 or node.name != "_mark_opening_anchor"]
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TencentProvider":
            node.body = [child for child in node.body if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                         or child.name != "get_minute_line"]
    assert hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest() == UNCHANGED_OWNER_AST


@pytest.fixture
def native_hub_owner_env(native_owner_env, monkeypatch):
    env = native_owner_env
    current = env.driver.method_identity()
    assert current["packet_builder"]["owner_sources"][HUB_OWNER_PATH] == NEW_HUB_OWNER
    old = deepcopy(current)
    old["packet_builder"]["owner_sources"][HUB_OWNER_PATH] = OLD_HUB_OWNER
    _replace_original(env, monkeypatch, old)
    env.current = current
    return env


def test_actual_hub_owner_transition_preserves_scope_unknowns_grants_and_environment(native_hub_owner_env, monkeypatch):
    env = native_hub_owner_env
    assert owner.inspect_rebind(**env.kwargs)["changed_sources"] == [HUB_OWNER_REVIEW]
    with pytest.raises(owner.AuthorizationBlocked, match="exact_source_review_required"):
        owner.prepare_rebind([], **env.kwargs)
    assert (env.root / "authorization.json").read_bytes() == env.before
    assert env.env_path.read_bytes() == env.env_before

    plan = owner.prepare_rebind([HUB_OWNER_REVIEW], **env.kwargs)
    applied = owner.apply_plan(plan["plan_id"], **env.kwargs)
    assert applied["state"] == "applied" and applied["changed_sources"] == [HUB_OWNER_REVIEW]
    assert applied["restart_required"] is True and applied["expiry_extended"] is False
    assert applied["unknowns_added"] == applied["new_cooperative_grants"] == 0
    expected = deepcopy(env.auth)
    expected["method_identity"] = env.current
    expected["unknown_usage_exception"]["method_sha256"] = research._digest(env.current)
    installed = (env.root / "authorization.json").read_bytes()
    assert json.loads(installed) == expected
    assert expected["native_recovery_grants"] == env.auth["native_recovery_grants"]
    assert len(expected["unknown_usage_exception"]["allowed_unknown_usage"]) == 7
    _, span = owner._pin(env.env_before)
    environment = env.env_path.read_bytes()
    assert environment[:span[0]] == env.env_before[:span[0]]
    assert environment[span[1]:] == env.env_before[span[1]:]
    assert owner._pin(environment)[0] == hashlib.sha256(installed).hexdigest()
    monkeypatch.setattr(settings, "tradingagents_research_authorization_digest", owner._pin(environment)[0])
    assert owner.inspect_rebind(**env.kwargs)["already_bound"] is True


@pytest.mark.parametrize("reverse", [False, True])
def test_actual_hub_owner_transition_refuses_any_other_or_reversed_version(native_hub_owner_env, monkeypatch, reverse):
    env = native_hub_owner_env
    target = "f" * 64
    if reverse:
        old = deepcopy(env.current)
        _replace_original(env, monkeypatch, old)
        target = OLD_HUB_OWNER
    env.native.packet_overrides["packet"] = lambda packet: packet["owner_sources"].update({HUB_OWNER_PATH: target})
    with pytest.raises(owner.AuthorizationBlocked, match="unreviewed_packet_owner_changed"):
        owner.inspect_rebind(**env.kwargs)
    assert (env.root / "authorization.json").read_bytes() == env.before
    assert env.env_path.read_bytes() == env.env_before


def test_prepared_hub_owner_transition_rechecks_actual_module_bytes_before_cas(native_hub_owner_env, tmp_path, monkeypatch):
    env, native = native_hub_owner_env, native_hub_owner_env.native.module
    plan = owner.prepare_rebind([HUB_OWNER_REVIEW], **env.kwargs)
    path = Path(native.__file__).resolve().parents[1] / HUB_OWNER_PATH
    changed = tmp_path / "unreviewed_quote_hub.py"
    changed.write_bytes(path.read_bytes() + b"\n# unreviewed future module revision\n")
    original_sha = native.file_sha
    monkeypatch.setattr(native, "file_sha", lambda value: original_sha(changed if Path(value) == path else value))
    with pytest.raises(owner.AuthorizationBlocked, match="unreviewed_packet_owner_changed"):
        owner.apply_plan(plan["plan_id"], **env.kwargs)
    assert (env.root / "authorization.json").read_bytes() == env.before
    assert env.env_path.read_bytes() == env.env_before


def test_actual_native_identity_reviewed_owner_only_can_prepare_and_apply_without_scope_expansion(native_owner_env, monkeypatch):
    env = native_owner_env
    inspected = owner.inspect_rebind(**env.kwargs)
    assert inspected["changed_sources"] == [OWNER_REVIEW] and inspected["already_bound"] is False
    assert inspected["unknown_count"] == 7 and inspected["automatic_dispatch"] is False
    with pytest.raises(owner.AuthorizationBlocked, match="exact_source_review_required"):
        owner.prepare_rebind([], **env.kwargs)
    plan = owner.prepare_rebind([OWNER_REVIEW], **env.kwargs)
    result = owner.apply_plan(plan["plan_id"], **env.kwargs)
    assert result["changed_sources"] == [OWNER_REVIEW] and result["restart_required"] is True
    assert result["expiry_extended"] is False and result["unknowns_added"] == result["new_cooperative_grants"] == 0
    expected = deepcopy(env.auth)
    expected["method_identity"] = env.driver.method_identity()
    expected["unknown_usage_exception"]["method_sha256"] = research._digest(expected["method_identity"])
    actual = json.loads((env.root / "authorization.json").read_bytes())
    assert actual == expected  # All scope fields, dates, seven exceptions and old grants remain exact.
    _, span = owner._pin(env.env_before)
    updated = env.env_path.read_bytes()
    assert updated[:span[0]] == env.env_before[:span[0]] and updated[span[1]:] == env.env_before[span[1]:]
    folder = env.root / "authorization-maintenance" / plan["plan_id"]
    assert (folder / "authorization.before.json").read_bytes() == env.before
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in folder.iterdir())
    assert all(b"fixture-secret-never-copy" not in path.read_bytes() for path in folder.iterdir())
    # Simulate the required configuration reload, rather than treating cached
    # old settings as an already-installed authorization.
    monkeypatch.setattr(settings, "tradingagents_research_authorization_digest", owner._pin(updated)[0])
    assert owner.inspect_rebind(**env.kwargs)["already_bound"] is True


@pytest.mark.parametrize("function_name", ["get_kline", "get_quotes"])
def test_actual_native_owner_pin_rejects_unreviewed_ta_consumer_code(native_owner_env, tmp_path, monkeypatch,
                                                                   function_name):
    env, native = native_owner_env, native_owner_env.native.module
    path = Path(native.__file__).resolve().parents[1] / OWNER_PATH
    body = path.read_text()
    cls = next(node for node in ast.parse(body).body if isinstance(node, ast.ClassDef) and node.name == "TencentProvider")
    function = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name)
    lines = body.splitlines(keepends=True)
    lines.insert(function.body[0].lineno - 1, "        raise RuntimeError('unreviewed consumer change')\n")
    candidate = tmp_path / "tampered_tencent.py"
    candidate.write_text("".join(lines))
    ast.parse(candidate.read_text())  # Parse only. Never execute the mutated provider.
    original_sha = native.file_sha
    monkeypatch.setattr(native, "file_sha", lambda value: original_sha(candidate if Path(value) == path else value))
    assert env.driver.method_identity()["packet_builder"]["owner_sources"][OWNER_PATH] not in {OLD_OWNER, NEW_OWNER}
    with pytest.raises(owner.AuthorizationBlocked, match="unreviewed_packet_owner_changed"):
        owner.inspect_rebind(**env.kwargs)
    assert (env.root / "authorization.json").read_bytes() == env.before and env.env_path.read_bytes() == env.env_before


def test_actual_native_identity_owner_and_reviewed_host_changes_are_explicit_together(native_owner_env, monkeypatch):
    env = native_owner_env
    original = deepcopy(env.auth["method_identity"])
    original["source_files"]["driver.py"] = "a" * 64
    _replace_original(env, monkeypatch, original)
    assert owner.inspect_rebind(**env.kwargs)["changed_sources"] == ["driver.py", OWNER_REVIEW]
    for incomplete in (["driver.py"], [OWNER_REVIEW]):
        with pytest.raises(owner.AuthorizationBlocked, match="exact_source_review_required"):
            owner.prepare_rebind(incomplete, **env.kwargs)
    prepared = owner.prepare_rebind([OWNER_REVIEW, "driver.py"], **env.kwargs)
    assert owner.apply_plan(prepared["plan_id"], **env.kwargs)["state"] == "applied"


@pytest.mark.parametrize("mutation", ["future", "reverse", "other_owner", "extra_owner", "missing_owner",
                                    "invalid_owner", "builder_version", "builder_policy", "model", "dependency", "upstream"])
def test_actual_native_shape_rejects_every_unreviewed_nested_or_scope_change(native_owner_env, monkeypatch, mutation):
    env = native_owner_env
    if mutation == "reverse":
        original = deepcopy(env.auth["method_identity"])
        original["packet_builder"]["owner_sources"][OWNER_PATH] = NEW_OWNER
        _replace_original(env, monkeypatch, original)

    def drift(packet):
        owners = packet["owner_sources"]
        if mutation in {"future", "reverse"}:
            owners[OWNER_PATH] = "f" * 64 if mutation == "future" else OLD_OWNER
        elif mutation == "other_owner": owners["services/quote_hub.py"] = "a" * 64
        elif mutation == "extra_owner": owners["unreviewed.py"] = "a" * 64
        elif mutation == "missing_owner": owners.pop("models/event.py")
        elif mutation == "invalid_owner": owners["models/event.py"] = "not-a-sha"
        elif mutation == "builder_version": packet["version"] += 1
        elif mutation == "builder_policy": packet["implicit_collection"] = True

    env.native.packet_overrides["packet"] = drift
    if mutation == "model": env.native.transport["model"] = "other-model"
    elif mutation == "dependency": env.native.dependencies["fixed"] = "f" * 64
    elif mutation == "upstream":
        original = deepcopy(env.auth["method_identity"])
        original["upstream_sha"] = "f" * 40
        _replace_original(env, monkeypatch, original)
    with pytest.raises(owner.AuthorizationBlocked):
        owner.inspect_rebind(**env.kwargs)
    assert (env.root / "authorization.json").read_bytes() == env.before
    assert env.env_path.read_bytes() == env.env_before
    assert not (env.root / "authorization-maintenance").exists()


def test_prepared_owner_transition_rechecks_full_native_identity_before_apply(native_owner_env):
    env = native_owner_env
    plan = owner.prepare_rebind([OWNER_REVIEW], **env.kwargs)
    env.native.packet_overrides["packet"] = lambda packet: packet["owner_sources"].update({OWNER_PATH: "f" * 64})
    with pytest.raises(owner.AuthorizationBlocked):
        owner.apply_plan(plan["plan_id"], **env.kwargs)
    assert (env.root / "authorization.json").read_bytes() == env.before
    assert env.env_path.read_bytes() == env.env_before


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


def _retained_source(env, monkeypatch):
    from app.services import agent_budget
    identity = deepcopy(env.auth["method_identity"])
    identity["source_files"]["agent_budget.py"] = "4" * 64
    _replace_original(env, monkeypatch, identity)
    env.current["source_files"]["agent_budget.py"] = "5" * 64
    with env.sf() as db:
        row = AgentResourceUsage(id="8"*32, budget_date=env.now.date().isoformat(), scope="autonomy_llm",
            kind="model", purpose="agenda.fixture", state="failed", usage_known=0,
            created_at=(env.now-timedelta(minutes=10)).replace(tzinfo=None),
            started_at=(env.now-timedelta(minutes=5)).replace(tzinfo=None),
            finished_at=(env.now-timedelta(minutes=2)).replace(tzinfo=None), error_kind="LLMError")
        db.add(row); db.commit(); before = agent_budget.dump(row)
    fact = {"usage_id":before["id"], "budget_date":before["budget_date"], "scope":before["scope"],
            "purpose":before["purpose"], "state":before["state"]}
    return fact, before


def _prepare_retained(env, fact, **changes):
    return owner.prepare_retained_unknown(fact, "Explicit fixture permission: retain unknown; selected research only.",
        ["agent_budget.py", "tradingagents_research.py", "tradingagents_recovery.py"], **{**env.kwargs, **changes})


def test_explicit_retained_plan_rebinds_exact_method_without_rewriting_original_facts(env, monkeypatch):
    from app.services import agent_budget
    fact, original = _retained_source(env, monkeypatch)
    with pytest.raises(agent_budget.BudgetError, match="用量未知"):
        owner.inspect_rebind(**env.kwargs)
    prepared = _prepare_retained(env, fact)
    folder = env.root / "authorization-maintenance" / prepared["plan_id"]
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in folder.iterdir())
    assert all(b"fixture-secret-never-copy" not in p.read_bytes() for p in folder.iterdir())
    assert (env.root/"authorization.json").read_bytes() == env.before
    result = owner.apply_plan(prepared["plan_id"], **env.kwargs)
    assert result["retained_unknown_authorizations_added"] == 1 and result["unknowns_added"] == 0
    actual = json.loads((env.root/"authorization.json").read_bytes())
    expected = deepcopy(env.auth)
    expected["method_identity"] = env.current
    expected["unknown_usage_exception"]["method_sha256"] = research._digest(env.current)
    history = actual.pop("retained_unknown_authorizations")
    assert actual == expected and len(history) == 1
    assert history[0]["source_usage"] == fact and history[0]["source_row_sha256"] == research._digest(original)
    assert history[0]["source_authorization_sha256"] == owner._sha(env.before)
    assert history[0]["authorized_method_sha256"] == research._digest(env.current)
    assert research._retained_unknown_usage(history, env.sf, now=env.now) == ({**fact, "source_row_sha256": history[0]["source_row_sha256"]},)
    with env.sf() as db:
        assert agent_budget.dump(db.get(AgentResourceUsage, fact["usage_id"])) == original
    _, span = owner._pin(env.env_before)
    new_env = env.env_path.read_bytes()
    assert new_env[:span[0]] == env.env_before[:span[0]] and new_env[span[1]:] == env.env_before[span[1]:]


@pytest.mark.parametrize("mutation", ["source_state", "source_purpose", "source_provider", "source_tokens", "new_unknown",
    "source_started", "active_task", "candidate_reason", "method", "next_day"])
def test_retained_installation_rechecks_original_row_new_unknown_and_frozen_plan(env, monkeypatch, mutation):
    fact, _ = _retained_source(env, monkeypatch)
    prepared = _prepare_retained(env, fact)
    if mutation.startswith("source_") or mutation in {"new_unknown", "active_task"}:
        with env.sf() as db:
            row = db.get(AgentResourceUsage, fact["usage_id"])
            if mutation == "source_state": row.state = "unknown"
            elif mutation == "source_purpose": row.purpose = "another.fixture"
            elif mutation == "source_provider": row.provider = "drift"
            elif mutation == "source_tokens": row.input_tokens = 7
            elif mutation == "source_started": row.state = "started"; row.finished_at = None
            elif mutation == "active_task": db.add(AgentTask(id="active", type="review", status="running"))
            else: db.add(AgentResourceUsage(id="9"*32,budget_date=env.now.date().isoformat(),scope="autonomy_llm",kind="model",purpose="new.fixture",state="unknown",usage_known=0,started_at=env.now.replace(tzinfo=None)))
            db.commit()
    elif mutation == "candidate_reason":
        path=env.root/"authorization-maintenance"/prepared["plan_id"]/"plan.json"
        value=json.loads(path.read_text());value["candidate"]["retained_unknown_authorizations"][0]["reason"]="changed";path.write_text(json.dumps(value))
    elif mutation == "method": env.current["source_files"]["agent_budget.py"]="6"*64
    overrides = {"now":env.now+timedelta(days=1)} if mutation=="next_day" else {}
    with pytest.raises((ValueError, RuntimeError)):
        owner.apply_plan(prepared["plan_id"], **{**env.kwargs, **overrides})
    assert (env.root/"authorization.json").read_bytes() == env.before and env.env_path.read_bytes() == env.env_before


@pytest.mark.parametrize("field,value", [("scope","selected_research_llm"),("budget_date","2000-01-01"),
    ("purpose","different.fixture"),("state","started")])
def test_new_retained_permission_requires_exact_explicit_terminal_source(env,monkeypatch,field,value):
    fact,_=_retained_source(env,monkeypatch);fact[field]=value
    with pytest.raises((ValueError,RuntimeError)):_prepare_retained(env,fact)
    assert (env.root/"authorization.json").read_bytes()==env.before


def test_cli_named_retention_is_explicit_and_default_inspection_never_adds_permission(monkeypatch,capsys):
    from scripts.tradingagents_authorization import main
    seen=[]
    monkeypatch.setattr(owner,"inspect_rebind",lambda: {"state":"dry_run"})
    monkeypatch.setattr(owner,"prepare_retained_unknown",lambda *args: seen.append(args) or {"state":"prepared"})
    assert main([])==0 and seen==[]
    assert json.loads(capsys.readouterr().out)=={"state":"dry_run"}
    assert main(["--prepare-retained-unknown","8"*32,"--source-budget-date","2026-10-21",
        "--source-scope","autonomy_llm","--source-purpose","agenda.fixture","--source-state","failed",
        "--authorization-reason","Explicit fixture authorization.","--reviewed-source","agent_budget.py"])==0
    assert seen==[({"usage_id":"8"*32,"budget_date":"2026-10-21","scope":"autonomy_llm",
        "purpose":"agenda.fixture","state":"failed"},"Explicit fixture authorization.",["agent_budget.py"])]
    assert json.loads(capsys.readouterr().out)=={"state":"prepared"}


@pytest.mark.parametrize("args",[["--prepare-retained-unknown","8"*32],["--source-purpose","agenda.fixture"],
    ["--prepare","--authorization-reason","unbound"],["--apply","a"*32,"--source-state","failed"]])
def test_cli_incomplete_or_mixed_retained_permission_never_reaches_an_operator(monkeypatch,args):
    from scripts.tradingagents_authorization import main
    def forbidden(*args,**kwargs):raise AssertionError("No operator belongs to invalid arguments")
    monkeypatch.setattr(owner,"inspect_rebind",forbidden)
    monkeypatch.setattr(owner,"prepare_retained_unknown",forbidden)
    monkeypatch.setattr(owner,"apply_plan",forbidden)
    with pytest.raises(SystemExit) as failure:main(args)
    assert failure.value.code==2


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


@pytest.mark.parametrize("code,expected", [("usage_unknown", "usage_unknown"),
    ("/private/DO_NOT_PRINT", "authorization_maintenance_unavailable")])
def test_cli_keeps_budget_blocker_code_without_exposing_private_error(monkeypatch, capsys, code, expected):
    from scripts.tradingagents_authorization import main
    from app.services.agent_budget import BudgetError
    def blocked(**kwargs):
        raise BudgetError(code, "PRIVATE_MODEL_CONTEXT_AND_PATH")
    monkeypatch.setattr(owner, "inspect_rebind", blocked)
    assert main([]) == 1
    assert json.loads(capsys.readouterr().out) == {"state": "blocked", "code": expected}


def test_cli_tail_grant_passes_only_explicit_fixed_plan_identity(monkeypatch, capsys):
    from scripts.tradingagents_authorization import main
    calls = []
    def prepared(task, run, **kwargs):
        calls.append((task, run, kwargs)); return {"state":"prepared", "plan_id":"d"*32}
    monkeypatch.setattr(owner, "prepare_grant", prepared)
    assert main(["--prepare-grant", "a"*32, "--run", "b"*32, "--cooperative-prelaunch-tail", "--source-rebind-plan", "c"*32]) == 0
    assert calls == [("a"*32,"b"*32,{"cooperative_prelaunch_tail":True,"source_rebind_plan":"c"*32})]
    assert json.loads(capsys.readouterr().out)["state"] == "prepared"


@pytest.mark.parametrize("arguments", [["--cooperative-prelaunch-tail"], ["--source-rebind-plan","c"*32],
    ["--prepare-grant","a"*32,"--run","b"*32,"--source-rebind-plan","c"*32]])
def test_cli_tail_options_never_become_implicit_rebind_or_grant(monkeypatch, arguments):
    from scripts.tradingagents_authorization import main
    def forbidden(*args, **kwargs): pytest.fail("invalid CLI scope reached an operator")
    for name in ("prepare_grant", "prepare_rebind", "apply_plan", "inspect_rebind"):
        monkeypatch.setattr(owner, name, forbidden)
    with pytest.raises(SystemExit) as error: main(arguments)
    assert error.value.code == 2
