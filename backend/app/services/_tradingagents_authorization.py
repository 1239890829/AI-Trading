"""Explicit local maintenance of an existing selected-research authorization.

This operator cannot create a consumer, extend a term, start a task or change a
model. Separately explicit retained-unknown permission binds one terminal row;
it never clears that row. Every prepared plan binds the observed installation.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from sqlalchemy import func, select

from app.core.bjtime import beijing_now
from app.core.config import settings
from app.models.agent import AgentTask

HOST_SOURCES = {"driver.py", "tradingagents_research.py", "tradingagents_recovery.py", "agent_budget.py"}
# PR 347 changed only the minute/auction path in this complete provider module.
# StoredCompanyPacketBuilder uses its unchanged get_kline path and cached Hub
# quotes, never get_minute_line. Both complete blobs and all other module AST
# nodes were reviewed: common AST SHA-256 (excluding only _mark_opening_anchor
# and TencentProvider.get_minute_line) is
# e7a8c6c8fca612f13191f720940e47be30a30a9dfe9a7be75c3735e3ebd7efa1.
# This exact forward pair belongs to local explicit rebind only. It neither
# migrates a recovery grant nor permits future/reverse/other owner revisions.
REVIEWED_PACKET_OWNER_PAIRS = {"data_providers/tencent.py": (
    "32699b46cc03f0038e8e9d4969461bd336d9465c0363f984f8dca56b0b828aa9",
    "73af8b2ff111c04b57deda0dc3432c678815d0c2cef0c884f6f022ac51ea1af5",
), "services/quote_hub.py": (
    # Reviewed market admission fix and connection-local subscription metadata.
    # Cached get_quotes/present_quote AST is unchanged. The visible copy also
    # retains degraded-source reasons when source age becomes stale. Provider
    # low/stale stays ineligible; medium keeps its reasons while still fresh.
    # This exact forward pair preserves scope, cutoffs and all old grants.
    "5d970afc948ea70e8d5ef3cc6a1efe68d2a3df09d3044fc1c92c405c864db0b0",
    "c4386ec9cd0d10e0a5e3931e4ee0046cfff48b4b35e70ae99a3cccee8f5273ab",
)}
PIN_KEY = "ASHARE_TRADINGAGENTS_RESEARCH_AUTHORIZATION_DIGEST"
PIN_LINE = re.compile(rb"(?im)^[ \t]*(?:export[ \t]+)?" + PIN_KEY.encode()
                      + rb"[ \t]*=[ \t]*(['\"]?)([0-9a-f]{64})\1[ \t]*(?:\#[^\r\n]*)?(?:\r?\n|$)")
PLAN_ID = re.compile(r"[0-9a-f]{32}")


class AuthorizationBlocked(ValueError):
    """Stable code only; callers never print files, environment values or paths."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _read(path: Path, limit: int = 2_500_000) -> bytes:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise AuthorizationBlocked("authorization_path_unsafe")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_size > limit:
        raise AuthorizationBlocked("authorization_file_unsafe")
    raw = path.read_bytes()
    after = path.lstat()
    if len(raw) > limit or (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise AuthorizationBlocked("authorization_file_changed")
    return raw


def _pin(raw: bytes) -> tuple[str, tuple[int, int]]:
    from io import StringIO
    from dotenv.parser import parse_stream

    fields = [b for b in parse_stream(StringIO(raw.decode())) if (b.key or "").upper() == PIN_KEY]
    matches = list(PIN_LINE.finditer(raw))
    if len(fields) != 1 or fields[0].error or len(matches) != 1:
        raise AuthorizationBlocked("authorization_pin_line_invalid")
    match = matches[0]
    value = match.group(2).decode()
    if fields[0].value != value:
        raise AuthorizationBlocked("authorization_pin_line_invalid")
    return value, match.span(2)


def _no_active(sf) -> None:
    with sf() as db:
        if db.scalar(select(func.count()).select_from(AgentTask).where(AgentTask.status.in_(("queued", "running")))):
            raise AuthorizationBlocked("authorization_tasks_active")


@contextmanager
def _runtime_locks(root: Path, *, create=False):
    """Hold the runtime's existing worker and model leases, never a second admission lock."""
    fds = []
    try:
        workers = [root / "selection-claims", root / "selected-dispatch"]
        for base in (root, root / "selected-execution"):
            if not base.exists():
                continue
            children = list(base.iterdir())
            if len(children) > 4096:
                raise AuthorizationBlocked("authorization_lock_range_exceeded")
            workers.extend(path for path in children if re.fullmatch(r"[0-9]{6}", path.name))
        paths = []
        for folder in workers:
            if folder.is_symlink():
                raise AuthorizationBlocked("authorization_path_unsafe")
            if create:
                folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            if folder.exists() and (create or (folder / ".worker.lock").exists()):
                paths.append(folder / ".worker.lock")
        model = root / "model-inflight"
        if model.is_symlink():
            raise AuthorizationBlocked("authorization_path_unsafe")
        if create:
            model.mkdir(mode=0o700, exist_ok=True)
        if model.exists():
            if stat.S_IMODE(model.stat().st_mode) != 0o700:
                raise AuthorizationBlocked("authorization_lease_permissions_invalid")
            leaves = list(model.iterdir())
            if len(leaves) > 4096:
                raise AuthorizationBlocked("authorization_lock_range_exceeded")
            if any(not re.fullmatch(r"(?:slot-[0-2]\.lock|[0-9a-f]{32}\.json)", p.name) for p in leaves):
                raise AuthorizationBlocked("authorization_lease_identity_invalid")
            paths.extend(leaves)
            if create:
                paths.extend(model / f"slot-{n}.lock" for n in range(3))
        for path in sorted(set(paths)):
            flags = (os.O_RDWR | os.O_CREAT if create else os.O_RDONLY) | os.O_NOFOLLOW
            fd = os.open(path, flags, 0o600)
            fds.append(fd)
            info = os.fstat(fd)
            current = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                    or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
                raise AuthorizationBlocked("authorization_lease_identity_invalid")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AuthorizationBlocked("authorization_runtime_lease_held") from None
        yield
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _owner(root=None, env_path=None, sf=None, driver=None):
    from app.services import tradingagents_research as research
    from app.services.tradingagents_driver import driver_from_server_manifest

    root = Path(root) if root is not None else research.ROOT
    env_path = Path(env_path) if env_path is not None else Path(__file__).resolve().parents[2] / ".env"
    sf = sf or research.get_session_factory()
    driver = driver or driver_from_server_manifest(root / "runtime.json", enabled=settings.tradingagents_research_enabled)
    return root, env_path, sf, driver


def _reviewed_packet_owner_changes(old: dict, new: dict) -> list[str]:
    """Normalize only reviewed exact owner pairs before full method equality."""
    old_packet, new_packet = old.get("packet_builder"), new.get("packet_builder")
    if not isinstance(old_packet, dict) or not isinstance(new_packet, dict):
        return []  # The full identity comparison below still rejects differences.
    old_owners, new_owners = old_packet.get("owner_sources"), new_packet.get("owner_sources")
    if not isinstance(old_owners, dict) or not isinstance(new_owners, dict):
        return []
    changed = sorted(key for key in old_owners.keys() | new_owners.keys() if old_owners.get(key) != new_owners.get(key))
    if not changed:
        return []
    if (old_owners.keys() != new_owners.keys()
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in [*old_owners.values(), *new_owners.values()])
            or any((old_owners.get(key), new_owners.get(key)) != REVIEWED_PACKET_OWNER_PAIRS.get(key) for key in changed)):
        raise AuthorizationBlocked("authorization_unreviewed_packet_owner_changed")
    for key in changed:
        old_owners[key] = new_owners[key]
    return ["packet_builder.owner_sources." + key for key in changed]


def _candidate(raw: bytes, identity: dict, sf, now: datetime, *, retained_authorization=None) -> tuple[dict, list[str]]:
    from app.services import tradingagents_research as research

    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get("version") != 1
            or value.get("mode") != "RESEARCH_ONLY_AUTHORIZED"
            or value.get("consumer") != "selected_stock_explanation" or value.get("user_authorization") != "EXPLICIT"
            or value.get("net_gain_verified") is not False or value.get("production_eligible") is not False
            or not research._clock(value.get("created_at")) <= now < research._clock(value.get("valid_until"))
            or not isinstance(value.get("native_recovery_grants"), list)):
        raise AuthorizationBlocked("authorization_scope_or_term_invalid")
    old, new = deepcopy(value["method_identity"]), deepcopy(identity)
    old_files, new_files = old.pop("source_files", None), new.pop("source_files", None)
    owner_changes = _reviewed_packet_owner_changes(old, new)
    if not isinstance(old_files, dict) or not isinstance(new_files, dict) or old != new:
        raise AuthorizationBlocked("authorization_method_scope_changed")
    changed = sorted(key for key in old_files.keys() | new_files.keys() if old_files.get(key) != new_files.get(key))
    if (not set(changed) <= HOST_SOURCES or old_files.keys() != new_files.keys()
            or any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in [*old_files.values(), *new_files.values()])):
        raise AuthorizationBlocked("authorization_unreviewed_source_changed")
    exception = value.get("unknown_usage_exception")
    checked = None
    if exception is not None:
        checked = research._unknown_usage_exception(exception, research._digest(value["method_identity"]), sf)
    retained = deepcopy(value.get("retained_unknown_authorizations", []))
    research._retained_unknown_usage(retained, sf, now=now)
    if retained_authorization is not None:
        research._retained_unknown_usage([retained_authorization], sf, now=now)
        if (retained_authorization.get("source_authorization_sha256") != _sha(raw)
                or retained_authorization.get("authorized_method_sha256") != research._digest(identity)
                or retained_authorization.get("source_usage", {}).get("budget_date") != now.date().isoformat()
                or any(row["source_usage"]["usage_id"] == retained_authorization.get("source_usage", {}).get("usage_id") for row in retained)):
            raise AuthorizationBlocked("authorization_retained_usage_permission_drift")
        retained.append(deepcopy(retained_authorization))
    projected = research._retained_unknown_usage(retained, sf, now=now)
    from app.services import agent_budget
    agent_budget.ensure_model_usage_known(sf, consumer_scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
        allowed_unknown_usage=research._allowed_unknown_usage({"unknown_usage_exception": checked or {}, "retained_unknown_usage": projected}))
    candidate = deepcopy(value)
    candidate["method_identity"] = deepcopy(identity)
    if retained_authorization is not None:
        candidate["retained_unknown_authorizations"] = retained
    if exception is not None:
        candidate["unknown_usage_exception"]["method_sha256"] = research._digest(identity)
    return candidate, sorted([*changed, *owner_changes])


def _snapshot(*, root=None, env_path=None, sf=None, driver=None, now=None, locks_held=False,
              retained_request=None, retained_authorization=None):
    root, env_path, sf, driver = _owner(root, env_path, sf, driver)
    if not settings.tradingagents_research_enabled or settings.tradingagents_auto_research_enabled:
        raise AuthorizationBlocked("authorization_manual_mode_required")
    raw, env = _read(root / "authorization.json"), _read(env_path)
    pin, span = _pin(env)
    if pin != _sha(raw) or pin != settings.tradingagents_research_authorization_digest:
        raise AuthorizationBlocked("authorization_pin_mismatch")
    with (nullcontext() if locks_held else _runtime_locks(root)):
        _no_active(sf)
        identity = driver.method_identity()
        now = now or beijing_now()
        if retained_request is not None:
            if retained_authorization is not None:
                raise AuthorizationBlocked("authorization_retained_usage_request_invalid")
            retained_authorization = _retained_permission(retained_request, raw, identity, sf, now)
        candidate, changed = _candidate(raw, identity, sf, now, retained_authorization=retained_authorization)
    return root, env_path, sf, driver, raw, env, span, candidate, changed


def inspect_rebind(**kwargs) -> dict:
    *_, candidate, changed = _snapshot(**kwargs)
    return {"state": "dry_run", "changed_sources": changed, "already_bound": not changed,
            "consumer": candidate["consumer"], "valid_until": candidate["valid_until"],
            "unknown_count": len(candidate.get("unknown_usage_exception", {}).get("allowed_unknown_usage", [])),
            "retained_unknown_count": len(candidate.get("retained_unknown_authorizations", [])),
            "existing_grant_count": len(candidate["native_recovery_grants"]), "automatic_dispatch": False}


def _write(path: Path, raw: bytes, *, mode=0o600) -> None:
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _store_plan(root, raw, env, span, candidate, changed, *, kind="rebind", extra=None):
    parent = root / "authorization-maintenance"
    if parent.is_symlink():
        raise AuthorizationBlocked("authorization_path_unsafe")
    parent.mkdir(mode=0o700, exist_ok=True)
    if stat.S_IMODE(parent.stat().st_mode) != 0o700:
        raise AuthorizationBlocked("authorization_plan_permissions_invalid")
    plan_id = uuid.uuid4().hex
    folder = parent / plan_id
    folder.mkdir(mode=0o700)
    # Only the authorization and its old pin are recoverable copies. Never
    # copy the environment or any other key from it into a plan or backup.
    plan = {"version": 1, "kind": kind, "env_sha256": _sha(env), "authorization_sha256": _sha(raw),
            "old_pin_line": PIN_KEY + "=" + env[span[0]:span[1]].decode(),
            "reviewed_sources": changed, "candidate": candidate, **(extra or {})}
    _write(folder / "authorization.before.json", raw)
    _write(folder / "plan.json", _json_bytes(plan))
    return {"state": "prepared", "plan_id": plan_id, "changed_sources": changed,
            "unknown_count": len(candidate.get("unknown_usage_exception", {}).get("allowed_unknown_usage", []))}


def prepare_rebind(reviewed_sources, **kwargs) -> dict:
    root, _env_path, _sf, _driver, raw, env, span, candidate, changed = _snapshot(**kwargs)
    if not changed or set(reviewed_sources) != set(changed):
        raise AuthorizationBlocked("authorization_exact_source_review_required")
    return _store_plan(root, raw, env, span, candidate, changed)


def _retained_permission(request, raw, identity, sf, now):
    from app.services import agent_budget, tradingagents_research as research
    from app.models.agent import AgentResourceUsage

    if (not isinstance(request, dict) or set(request) != {"source_usage", "reason"}
            or not isinstance(request["reason"], str) or not 0 < len(request["reason"].strip()) <= 2000):
        raise AuthorizationBlocked("authorization_explicit_retained_permission_required")
    fact = request["source_usage"]
    if (not isinstance(fact, dict) or set(fact) != {"usage_id", "budget_date", "scope", "purpose", "state"}
            or not isinstance(fact["usage_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", fact["usage_id"])
            or fact["scope"] != agent_budget.SCOPE_AUTONOMY_LLM or fact["budget_date"] != now.date().isoformat()):
        raise AuthorizationBlocked("authorization_retained_usage_source_invalid")
    with sf() as db:
        row = db.get(AgentResourceUsage, fact["usage_id"])
        if row is None:
            raise AuthorizationBlocked("authorization_retained_usage_source_invalid")
        source_sha = research._digest(agent_budget.dump(row))
    agent_budget.validate_unknown_usage_exceptions([{**fact, "source_row_sha256": source_sha}])
    permission = {"version": 1, "user_authorization": "EXPLICIT", "consumer": "selected_stock_explanation", "dispatch": "manual",
                  "reason": request["reason"], "authorized_at": now.isoformat(), "source_authorization_sha256": _sha(raw),
                  "authorized_method_sha256": research._digest(identity), "source_usage": fact, "source_row_sha256": source_sha}
    research._retained_unknown_usage([permission], sf, now=now)
    return permission


def prepare_retained_unknown(source_usage, reason, reviewed_sources, **kwargs) -> dict:
    """Only an operator's new explicit instruction can add this named history."""
    root, _, _, _, raw, env, span, candidate, changed = _snapshot(
        **kwargs, retained_request={"source_usage": source_usage, "reason": reason})
    if set(reviewed_sources) != set(changed):
        raise AuthorizationBlocked("authorization_exact_source_review_required")
    permission = candidate["retained_unknown_authorizations"][-1]
    return _store_plan(root, raw, env, span, candidate, changed, kind="retained_unknown",
                       extra={"retained_authorization": permission})


def _source_rebind_plan(plan_id, identity, *, root=None, expected=None, current=None):
    """Read one applied local rebind, never discover history or accept a path."""
    from app.services import tradingagents_research as research

    if not isinstance(plan_id, str) or not PLAN_ID.fullmatch(plan_id):
        raise AuthorizationBlocked("authorization_source_plan_invalid")
    parent = (Path(root) if root is not None else research.ROOT) / "authorization-maintenance"
    folder = parent / plan_id
    if any(path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o700 for path in (parent, folder)):
        raise AuthorizationBlocked("authorization_source_plan_invalid")
    raw, before, applied = (_read(folder / name) for name in ("plan.json", "authorization.before.json", "applied.json"))
    if any(stat.S_IMODE((folder / name).stat().st_mode) != 0o600 for name in ("plan.json", "authorization.before.json", "applied.json")):
        raise AuthorizationBlocked("authorization_source_plan_invalid")
    plan, source = json.loads(raw), json.loads(before)
    target = plan.get("candidate")
    proof = {"plan_id": plan_id, "plan_sha256": _sha(raw), "authorization_before_sha256": _sha(before),
             "applied_sha256": _sha(applied)}
    desired = deepcopy(source)
    desired["method_identity"] = deepcopy(identity)
    if desired.get("unknown_usage_exception") is not None:
        desired["unknown_usage_exception"]["method_sha256"] = research._digest(identity)
    old = source.get("method_identity", {})
    old_files, new_files = old.get("source_files", {}), identity.get("source_files", {})
    changes = {key for key in old_files.keys() | new_files.keys() if old_files.get(key) != new_files.get(key)}
    old_base, new_base = deepcopy(old), deepcopy(identity)
    old_base.pop("source_files", None); new_base.pop("source_files", None)
    if (plan.get("version") != 1 or plan.get("kind") != "rebind"
            or plan.get("authorization_sha256") != _sha(before)
            or plan.get("reviewed_sources") != ["tradingagents_recovery.py"]
            or json.loads(applied) != {"version": 1, "state": "applied", "restart_required": True}
            or changes != {"tradingagents_recovery.py"} or old_files.keys() != new_files.keys()
            or old_base != new_base or target != desired
            or current is not None and current != target or expected is not None and proof != expected):
        raise AuthorizationBlocked("authorization_source_plan_drift")
    return deepcopy(old), proof


def prepare_grant(source_task_id, run_id, *, cooperative_prelaunch_tail=False, source_rebind_plan=None, **kwargs) -> dict:
    from app.services.tradingagents_recovery import prepare_cooperative_grant

    root, _env_path, sf, driver, raw, env, span, candidate, changed = _snapshot(**kwargs)
    if changed:
        raise AuthorizationBlocked("authorization_rebind_required_first")
    if len(candidate["native_recovery_grants"]) >= 16:
        raise AuthorizationBlocked("authorization_grant_history_full")
    now = kwargs.get("now") or beijing_now()
    until = min(now + timedelta(hours=2), datetime.fromisoformat(candidate["valid_until"]))
    identity = driver.method_identity()
    if source_rebind_plan is not None and not cooperative_prelaunch_tail:
        raise AuthorizationBlocked("authorization_source_plan_requires_tail_mode")
    source, proof = identity, None
    if source_rebind_plan is not None:
        source, proof = _source_rebind_plan(source_rebind_plan, identity, root=root, current=candidate)
    grant = prepare_cooperative_grant(source_task_id, run_id, identity, grant_id=uuid.uuid4().hex,
        authorized_at=now.isoformat(), valid_until=until.isoformat(), sf=sf,
        **({"prelaunch_tail": True, "source_identity": source, "source_rebind_proof": proof, "driver": driver}
           if cooperative_prelaunch_tail else {}))
    if any(g.get("source_task_id") == source_task_id and g.get("run_id") == run_id for g in candidate["native_recovery_grants"]):
        raise AuthorizationBlocked("authorization_grant_already_present")
    candidate["native_recovery_grants"].append(grant)
    target = {"source_task_id": source_task_id, "run_id": run_id}
    if cooperative_prelaunch_tail:
        target.update(cooperative_prelaunch_tail=True, source_rebind_plan=source_rebind_plan)
    return _store_plan(root, raw, env, span, candidate, changed, kind="cooperative_grant", extra={"grant_target": target})


def apply_plan(plan_id: str, **kwargs) -> dict:
    if not isinstance(plan_id, str) or not PLAN_ID.fullmatch(plan_id):
        raise AuthorizationBlocked("authorization_plan_id_invalid")
    root, env_path, sf, driver = _owner(kwargs.get("root"), kwargs.get("env_path"), kwargs.get("sf"), kwargs.get("driver"))
    parent = root / "authorization-maintenance"
    if parent.is_symlink() or stat.S_IMODE(parent.stat().st_mode) != 0o700:
        raise AuthorizationBlocked("authorization_plan_permissions_invalid")
    fd = os.open(parent / "operator.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    locks = ExitStack()
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.enter_context(_runtime_locks(root, create=True))
        folder = parent / plan_id
        plan = json.loads(_read(folder / "plan.json"))
        before = _read(folder / "authorization.before.json")
        if plan.get("version") != 1 or plan.get("kind") not in {"rebind", "cooperative_grant", "retained_unknown"} or _sha(before) != plan.get("authorization_sha256"):
            raise AuthorizationBlocked("authorization_plan_invalid")
        if plan["kind"] == "retained_unknown" and not isinstance(plan.get("retained_authorization"), dict):
            raise AuthorizationBlocked("authorization_plan_invalid")
        _, _, _, _, raw, env, span, candidate, changed = _snapshot(
            root=root, env_path=env_path, sf=sf, driver=driver, now=kwargs.get("now"), locks_held=True,
            retained_authorization=plan.get("retained_authorization") if plan["kind"] == "retained_unknown" else None)
        if plan["kind"] == "cooperative_grant":
            from app.services.tradingagents_recovery import prepare_cooperative_grant
            proposed = plan["candidate"]["native_recovery_grants"][-1]
            target = plan["grant_target"]
            now = kwargs.get("now") or beijing_now()
            at, end = (datetime.fromisoformat(proposed[key]) for key in ("authorized_at", "valid_until"))
            if (changed or not at <= now < end or end != min(at + timedelta(hours=2), datetime.fromisoformat(candidate["valid_until"]))) :
                raise AuthorizationBlocked("authorization_grant_plan_expired_or_changed")
            identity = driver.method_identity()
            source, proof = identity, None
            tail_mode = target.get("cooperative_prelaunch_tail") is True
            if set(target) != ({"source_task_id", "run_id", "cooperative_prelaunch_tail", "source_rebind_plan"}
                               if tail_mode else {"source_task_id", "run_id"}):
                raise AuthorizationBlocked("authorization_grant_plan_expired_or_changed")
            if tail_mode and target["source_rebind_plan"] is not None:
                source, proof = _source_rebind_plan(target["source_rebind_plan"], identity, root=root, current=candidate)
            grant = prepare_cooperative_grant(target["source_task_id"], target["run_id"], identity,
                grant_id=proposed["grant_id"], authorized_at=proposed["authorized_at"], valid_until=proposed["valid_until"], sf=sf,
                **({"prelaunch_tail": True, "source_identity": source, "source_rebind_proof": proof, "driver": driver}
                   if tail_mode else {}))
            candidate["native_recovery_grants"].append(grant)
        if (raw != before or _sha(env) != plan.get("env_sha256") or plan.get("candidate") != candidate
                or plan.get("reviewed_sources") != changed or plan["kind"] == "rebind" and not changed
                or plan.get("old_pin_line") != PIN_KEY + "=" + env[span[0]:span[1]].decode()):
            raise AuthorizationBlocked("authorization_plan_drift")
        target = _json_bytes(candidate)
        new_env = env[:span[0]] + _sha(target).encode() + env[span[1]:]
        auth_path = root / "authorization.json"
        _no_active(sf)
        from app.services import tradingagents_research as research
        research._retained_unknown_usage(candidate.get("retained_unknown_authorizations", []), sf, now=kwargs.get("now") or beijing_now())
        if _read(auth_path) != raw or _read(env_path) != env or driver.method_identity() != candidate["method_identity"]:
            raise AuthorizationBlocked("authorization_installation_changed")
        _write(auth_path, target)
        try:
            if _read(env_path) != env:
                raise AuthorizationBlocked("authorization_environment_changed")
            _write(env_path, new_env, mode=stat.S_IMODE(env_path.stat().st_mode))
        except BaseException:
            # A mismatched pin already fails closed. Restore only our own
            # exact write; never overwrite another operator's changed file.
            if _read(auth_path) == target:
                _write(auth_path, raw)
            raise
        if _read(auth_path) != target or _read(env_path) != new_env:
            raise AuthorizationBlocked("authorization_installation_unknown")
        _write(folder / "applied.json", _json_bytes({"version": 1, "state": "applied", "restart_required": True}))
        return {"state": "applied", "plan_id": plan_id, "changed_sources": changed, "restart_required": True,
                "expiry_extended": False, "unknowns_added": 0, "existing_grants_changed": False,
                "retained_unknown_authorizations_added": int(plan["kind"] == "retained_unknown"),
                "new_cooperative_grants": int(plan["kind"] == "cooperative_grant")}
    except BlockingIOError:
        raise AuthorizationBlocked("authorization_operator_busy") from None
    finally:
        locks.close()
        os.close(fd)
