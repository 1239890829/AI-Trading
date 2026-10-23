"""Pinned, single-owner continuation of one existing native research run.

The maintenance request selects opaque identities only. The existing server
authorization, task runner, selection claims and per-call budget remain owners.
No import, preflight or ordinary read starts a worker or collects new inputs.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from sqlalchemy import select

from app.models.agent import AgentResourceUsage, AgentTask
from app.services import agent_budget
from app.services import tradingagents_research as research
from app.events.company_state import atomic_json
from app.services.company_evidence import worker_lease

HEX32 = re.compile(r"[0-9a-f]{32}")
HEX64 = re.compile(r"[0-9a-f]{64}")
MAX_BYTES = 16 * 1024 * 1024
LINEAGE_KEYS = {"version", "source_task_id", "source_run_id", "recovery_task_id", "source_method_sha256",
    "recovery_method_sha256", "reused_original_outputs", "new_call_from", "new_call_to", "inherited_unknown_usage_count"}
GRANT_KEYS = {"version", "grant_id", "user_authorization", "authorized_at", "valid_until", "source_task_id",
    "symbol", "run_id", "source_method_identity", "continuation_method_identity", "compatibility", "binding"}
BINDING_KEYS = {"selection", "source_claim_sha256", "packet_sha256", "config_sha256", "staged_code_sha256",
    "manifest_sha256", "worker_terminal_sha256", "checkpoint", "call_counter", "outputs", "usage_receipts",
    "prefixes", "completed_roles", "expected_pending_role"}
HISTORY_KEYS = {"grant_id", "grant_sha256", "task_id", "method_identity", "call_from", "call_to", "claim_sha256"}
MAX_RECOVERY_HISTORY = 8  # Bounded artifact parsing, not a model-call allowance.
PARALLEL_ANALYSTS = {"Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst"}
PARALLEL_BINDING_KEYS = (BINDING_KEYS - {"expected_pending_role"}) | {"expected_pending_roles", "pending_message_fingerprints"}
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
PRELAUNCH_TAIL_KEYS = {"version", "call_id", "source_task_error_sha256", "source_execution_sha256",
    "source_model_usage_facts_sha256", "model_receipts_prefix", "first_broker_error_record_sha256"}
# The reviewed historical chain persisted the counter before reserve, serialized
# calls through finish, and could not launch a model before the atomic start gate.
# No other old or future implementation inherits this missing-receipt exception.
PRELAUNCH_SOURCE = {
    "driver.py": "939e0de30999ac2b6e93b947e581e82f5e430a1134a2b51c5af8748cafb09b9d",
    "agent_budget.py": "5b292c8d675f71e97a86116b6e3250c7a15af8e2bde34ec6492875105c519923",
    "codex_transport.py": "1876b45f0636e4305fb1df97ea2306134aacdc338a357b20e88e3cb0ad205101",
}
PRELAUNCH_UNKNOWN_MESSAGE_SHA = "34d06a06ea8588936d4206fb06ae1d378e72478ff047424e72b659b084ca05f2"
# A distinct reviewed failure: PR325 checks the complete wire input before
# reserve/start. This identity does not authorize a future implementation or
# turn a missing call into a usage receipt. Each continuation needs its own grant.
INPUT_PRELAUNCH_SOURCE = {
    "driver.py": "af48f1ce70b37e474e2359daa25eb789ea092693e32147298d6f7d6f6e84c6ee",
    "agent_budget.py": "5b292c8d675f71e97a86116b6e3250c7a15af8e2bde34ec6492875105c519923",
    "codex_transport.py": "033af2af08a2c0aca013fb9be301e65bb74a7965a0a4ad6a59a23632bfd48970",
    "transport_process.py": "ff2812c801be847b6c6e83b0edf109f82fb7fe65ea9077e4473597437af4e592",
}
INPUT_PRELAUNCH_TAIL_KEYS = PRELAUNCH_TAIL_KEYS | {"input_chars", "input_limit_chars", "pending_message_fingerprints"}
INPUT_PRELAUNCH_NATIVE_ERROR_SHA = "b746f1c7ca6beccb3cee95a7d4770cf3b68a0ee60fc8f0413fe0cf4766f5e7a2"
# The pinned upstream wrap_up appends this exact user message *after* the
# checkpoint messages. Do not accept a fresh one-message report request.
INPUT_PRELAUNCH_WRAP_UP_SHA = "a6a32b933c43bd17a43fa277e12d9ba9eb480d87008d2289b35c67efe14c0790"
FROZEN_PACKET_OWNER_PAIRS = {"services/quote_hub.py": {
    "old": "8ef2086885ae17cf52a5c720705251a2b956b91f401ca8604b655182053308bf",
    "new": "5d970afc948ea70e8d5ef3cc6a1efe68d2a3df09d3044fc1c92c405c864db0b0",
}, "data_providers/tencent.py": {
    "old": "69d6380eab0e4ffc228d08391a8a815bfb37e304ee9cac70271d04ed58bc3a43",
    "new": "32699b46cc03f0038e8e9d4969461bd336d9465c0363f984f8dca56b0b828aa9",
}}
# Reviewed 256cae58 usage contract migration: strict cache-write accounting and
# bounded observations; no graph/process/timeout-proof change. Not a blanket
# permission to migrate any future version of this transport component.
USAGE_OBSERVATION_TRANSITION = (
    "9281aeaf1fc31d8b668392d99388f755043bbb191c5e27e39d4e3c1c053da3d2",
    "d88bdea9ecc8f9a854f02d92ed448d08fe3995074d4f130d805ab4d88aca7881",
)
# These reviewed host-only changes preserve the native graph, model, frozen
# inputs and tools. They do not migrate a grant or authorize another call.
# Future/reversed hashes remain incompatible, even under the same file name.
REVIEWED_COMPONENT_TRANSITIONS = {
    "transport_process.py": {
        USAGE_OBSERVATION_TRANSITION,
        ("d88bdea9ecc8f9a854f02d92ed448d08fe3995074d4f130d805ab4d88aca7881",
         "ff2812c801be847b6c6e83b0edf109f82fb7fe65ea9077e4473597437af4e592"),
    },
    "tradingagents_source_gate.py": {
        ("862dd58539f17698a8feabf04218acb41681f7381350007e6b630692d0fbbafc",
         "0655bee3b5138cb354e69ee9c0c8cf9f7d627c58898952a2140a7237e14d7f83"),
        ("0655bee3b5138cb354e69ee9c0c8cf9f7d627c58898952a2140a7237e14d7f83",
         "5554522f2f9b601f3e91443459eb6874c65466b88ca57bad9728029ebace6604"),
    },
}


def _blocked(code):
    return research.ResearchBlocked([{"code": code, "message": "原生恢复条件不满足；保留原任务、原答及用量记录，未自动重试。"}])


def _id(value):
    if not isinstance(value, str) or not HEX32.fullmatch(value):
        raise ValueError("recovery identity must be lowercase 32-hex")
    return value


def _hash(value):
    return isinstance(value, str) and bool(HEX64.fullmatch(value))


def _safe(path):
    path = Path(path)
    root = research.ROOT
    if path != root and root not in path.parents:
        raise _blocked("recovery_path_invalid")
    current = path
    while True:
        if current.is_symlink():
            raise _blocked("recovery_symlink_refused")
        if current == root:
            break
        current = current.parent
    return path


def _body(path, *, limit=MAX_BYTES):
    with _safe(path).open("rb") as handle:
        body = handle.read(limit + 1)
    if len(body) > limit:
        raise _blocked("recovery_artifact_exceeds_bound")
    return body


def _sha(path):
    # Raw checkpoint files can exceed the JSON bound. Hash in fixed chunks;
    # logical checkpoint row counts and all decoded metadata remain bounded.
    result = hashlib.sha256()
    with _safe(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _json(path):
    value = json.loads(_body(path))
    if not isinstance(value, dict):
        raise _blocked("recovery_artifact_invalid")
    return value


def _compatibility(grant, identity):
    old, new, compatibility = (grant[key] for key in ("source_method_identity", "continuation_method_identity", "compatibility"))
    if (not isinstance(old, dict) or new != identity or not isinstance(compatibility, dict)
            or compatibility.get("kind") not in {"reviewed_host_diagnostics_and_recovery_v1", "reviewed_frozen_packet_owner_v1"}
            or set(compatibility) != ({"kind", "source_hash_pairs", "packet_owner_hash_pairs"}
                if compatibility["kind"] == "reviewed_frozen_packet_owner_v1" else {"kind", "source_hash_pairs"})):
        raise _blocked("recovery_method_incompatible")
    old_base, new_base = deepcopy(old), deepcopy(new)
    old_files, new_files = old_base.pop("source_files", None), new_base.pop("source_files", None)
    if compatibility["kind"] == "reviewed_frozen_packet_owner_v1":
        # Reviewed current presentation/search owners do not change this run's
        # frozen packet. No owner pair permits recollection or future hashes.
        binding = grant.get("binding")
        old_packet, new_packet = old_base.get("packet_builder"), new_base.get("packet_builder")
        old_owners = old_packet.get("owner_sources") if isinstance(old_packet, dict) else None
        new_owners = new_packet.get("owner_sources") if isinstance(new_packet, dict) else None
        if (not isinstance(binding, dict) or not isinstance(binding.get("input_prelaunch_tail"), dict)
                or any(not _hash(binding.get(key)) for key in ("packet_sha256", "config_sha256", "staged_code_sha256", "manifest_sha256"))
                or not isinstance(old_owners, dict) or not isinstance(new_owners, dict)):
            raise _blocked("recovery_frozen_packet_owner_incompatible")
        owner_changes = {key for key in old_owners.keys() | new_owners.keys() if old_owners.get(key) != new_owners.get(key)}
        owner_pairs = {key: {"old": old_owners.get(key), "new": new_owners.get(key)} for key in owner_changes}
        if (not owner_changes or not owner_changes <= FROZEN_PACKET_OWNER_PAIRS.keys()
                or compatibility["packet_owner_hash_pairs"] != owner_pairs
                or any(pair != FROZEN_PACKET_OWNER_PAIRS[key] for key, pair in owner_pairs.items())):
            raise _blocked("recovery_frozen_packet_owner_incompatible")
        for key, pair in owner_pairs.items():
            old_owners[key] = pair["new"]
    if not isinstance(old_files, dict) or not isinstance(new_files, dict) or old_base != new_base:
        raise _blocked("recovery_graph_model_or_dependency_drift")
    allowed = {"driver.py", "codex_transport.py", "tradingagents_research.py", "tradingagents_recovery.py"}
    changed = {key for key in old_files.keys() | new_files.keys() if old_files.get(key) != new_files.get(key)}
    pairs = {key: {"old": old_files.get(key), "new": new_files.get(key)} for key in changed}
    reviewed_transitions = all((old_files.get(key), new_files.get(key)) in
        REVIEWED_COMPONENT_TRANSITIONS.get(key, ()) for key in changed - allowed)
    if (not changed or not reviewed_transitions or compatibility["source_hash_pairs"] != pairs
            or any(not _hash(v) for v in new_files.values()) or any(not _hash(v) for v in old_files.values())):
        raise _blocked("recovery_component_mapping_drift")
    # Names never grant compatibility. Exact old/new pairs are server-pinned;
    # all staged graph/codec/formula and model/dependency identities stay equal.
    return research._digest(old), research._digest(new)


def _checkpoint_files(dbpath):
    files = {}
    for suffix in ("", "-wal", "-shm"):
        path = _safe(Path(str(dbpath) + suffix))
        files[suffix or "db"] = _sha(path) if path.exists() else None
    return files


@contextmanager
def _checkpoint_reader(run, symbol, *, offline=None):
    """Immutable reads require a proved, terminated native failure with no sidecars."""
    dbpath = _safe(run / "graph/cache/checkpoints" / (symbol + ".db"))
    def unchanged():
        if (not isinstance(offline, dict) or set(offline) != {"grant", "session_factory"}
                or not isinstance(offline["grant"], dict) or not callable(offline["session_factory"])):
            raise _blocked("recovery_offline_checkpoint_unproven")
        grant = offline["grant"]
        binding = _binding(grant["binding"])
        files = binding["checkpoint"].get("files")
        input_tail = "input_prelaunch_tail" in binding
        if (not (_first_parallel_failure(binding, grant) or input_tail) or grant.get("symbol") != symbol
                or not isinstance(files, dict) or set(files) != {"db", "-wal", "-shm"}
                or not _hash(files["db"]) or files["-wal"] is not None or files["-shm"] is not None):
            raise _blocked("recovery_offline_checkpoint_unproven")
        if input_tail:
            _input_prelaunch_tail(grant, run, _history_segments(grant, run, offline["session_factory"]), offline["session_factory"])
        if _source_task(grant["source_task_id"], symbol, offline["session_factory"]) != "failed":
            raise _blocked("recovery_offline_checkpoint_unproven")
        terminal = _json(run / "worker-terminal.json")
        if (_sha(run / "worker-terminal.json") != binding["worker_terminal_sha256"]
                or type(terminal.get("exit_code")) is not int or terminal["exit_code"] == 0
                or _json(run / "execution.json").get("state") not in {"needs_native_recovery", "canceled"}
                or _checkpoint_files(dbpath) != files):
            raise _blocked("recovery_offline_checkpoint_drift")
    if offline is not None:
        unchanged()
    uri = dbpath.absolute().as_uri() + "?mode=ro" + ("&immutable=1" if offline is not None else "")
    connection = sqlite3.connect(uri, uri=True, timeout=2)
    try:
        connection.execute("PRAGMA query_only=ON")
        yield connection
    finally:
        connection.close()
        if offline is not None:
            unchanged()


def checkpoint_snapshot(run, symbol, *, offline=None):
    """Logical SQLite state includes WAL; raw db/WAL/SHM hashes are retained too."""
    folder = _safe(run / "graph/cache/checkpoints")
    dbpath = _safe(folder / (symbol + ".db"))
    if not dbpath.is_file():
        raise _blocked("recovery_checkpoint_missing")
    with _checkpoint_reader(run, symbol, offline=offline) as connection:
        root_rows = connection.execute("SELECT thread_id,checkpoint_id,checkpoint,metadata FROM checkpoints "
            "WHERE checkpoint_ns='' ORDER BY checkpoint_id DESC LIMIT 2").fetchall()
        if not root_rows:
            raise _blocked("recovery_checkpoint_missing")
        thread, checkpoint_id, _, metadata = root_rows[0]
        rows = connection.execute("SELECT checkpoint_ns,checkpoint_id,parent_checkpoint_id,type,checkpoint,metadata "
            "FROM checkpoints WHERE thread_id=? ORDER BY checkpoint_ns,checkpoint_id LIMIT 8193", (thread,)).fetchall()
        writes = connection.execute("SELECT checkpoint_ns,checkpoint_id,task_id,task_path,idx,channel,type,value "
            "FROM writes WHERE thread_id=? ORDER BY checkpoint_ns,checkpoint_id,task_id,idx LIMIT 8193", (thread,)).fetchall()
        if len(rows) > 8192 or len(writes) > 8192:
            raise _blocked("recovery_checkpoint_exceeds_bound")
        def scalars(row):
            return [hashlib.sha256(v).hexdigest() if isinstance(v, bytes) else v for v in row]
        logical = research._digest({"checkpoints": [scalars(row) for row in rows], "writes": [scalars(row) for row in writes]})
        step = json.loads(metadata).get("step")
    return {"thread_id": thread, "checkpoint_id": checkpoint_id, "step": step,
            "logical_sha256": logical, "files": _checkpoint_files(dbpath)}


def _source_task(source_task_id, symbol, sf):
    with sf() as db:
        task = db.get(AgentTask, source_task_id)
        if (task is None or task.type != "tradingagents_research" or task.status not in {"failed", "canceled"}
                or json.loads(task.params) != {"symbol": symbol} or task.finished_at is None):
            raise _blocked("recovery_source_task_invalid")
        return task.status


def _grant(source_task_id, run_id, app, sf):
    admission, blockers = research._quality(app, session_factory=sf)
    if blockers or admission.get("state") != "research_only_authorized":
        raise _blocked("recovery_not_authorized")
    artifact, actual = research._read_bound(_safe(research.ROOT / "authorization.json"))
    if actual != admission.get("artifact_sha256"):
        raise _blocked("recovery_authorization_drift")
    grants = artifact.get("native_recovery_grants")
    if not isinstance(grants, list) or not 0 < len(grants) <= 16:
        raise _blocked("recovery_grant_missing")
    matching = [value for value in grants if isinstance(value, dict)
                and value.get("source_task_id") == source_task_id and value.get("run_id") == run_id]
    if len(matching) != 1:
        raise _blocked("recovery_grant_ambiguous_or_missing")
    grant = matching[0]
    if (set(grant) != GRANT_KEYS or type(grant["version"]) is not int or grant["version"] != 1
            or grant["user_authorization"] != "EXPLICIT" or not isinstance(grant["symbol"], str)
            or not re.fullmatch(r"[0-9]{6}", grant["symbol"])):
        raise _blocked("recovery_grant_invalid")
    _id(grant["grant_id"])
    now = research._now()
    if not research._clock(grant["authorized_at"]) <= now < research._clock(grant["valid_until"]) <= research._clock(artifact["valid_until"]):
        raise _blocked("recovery_grant_expired")
    driver = research._driver(app)
    identity = driver.method_identity()
    old_method, method = _compatibility(grant, identity)
    if method != admission["method_sha256"]:
        raise _blocked("recovery_current_method_drift")
    try:
        agent_budget.ensure_model_usage_known(sf, consumer_scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
            allowed_unknown_usage=research._allowed_unknown_usage(admission))
    except agent_budget.BudgetError:
        raise _blocked("usage_unknown") from None
    return grant, admission, driver, old_method, method


def _binding(value):
    if (not isinstance(value, dict) or set(value) - {"recovery_history", "prelaunch_tail", "input_prelaunch_tail", "input_wrapup_message_fingerprints"} not in (BINDING_KEYS, PARALLEL_BINDING_KEYS)
            or any(not isinstance(value.get(key), dict) for key in ("selection", "outputs", "usage_receipts", "prefixes", "checkpoint"))):
        raise _blocked("recovery_binding_invalid")
    if "input_prelaunch_tail" in value and ("prelaunch_tail" in value or "expected_pending_roles" in value):
        raise _blocked("recovery_input_prelaunch_tail_invalid")
    if "input_wrapup_message_fingerprints" in value:
        wire = value["input_wrapup_message_fingerprints"]
        tail = value.get("input_prelaunch_tail")
        raw = tail.get("pending_message_fingerprints") if isinstance(tail, dict) else None
        # A separately reviewed grant binds the pinned upstream _as_text wire
        # representation. The raw checkpoint/tail is never rewritten to match it.
        if (not isinstance(raw, list) or not isinstance(wire, list) or not 4 <= len(wire) <= 129
                or len(wire) != len(raw) + 1 or any(not _hash(row) for row in wire)
                or wire[-1] != INPUT_PRELAUNCH_WRAP_UP_SHA):
            raise _blocked("recovery_input_wrapup_binding_invalid")
    if "expected_pending_roles" in value:
        roles, fingerprints = value["expected_pending_roles"], value["pending_message_fingerprints"]
        if (not isinstance(roles, list) or len(roles) != 4 or any(not isinstance(r, str) for r in roles)
                or set(roles) != PARALLEL_ANALYSTS or value["completed_roles"] != []
                or value["prefixes"].get("role-outputs.jsonl") != {"bytes": 0, "sha256": EMPTY_SHA}
                or not isinstance(fingerprints, dict) or set(fingerprints) != PARALLEL_ANALYSTS
                or any(not isinstance(rows, list) or not 1 <= len(rows) <= 128 or any(not _hash(v) for v in rows)
                       for rows in fingerprints.values())):
            raise _blocked("recovery_parallel_binding_invalid")
    return value


def _stream_body(run, name, binding):
    try:
        return _body(run / name)
    except FileNotFoundError:
        # Only the explicitly frozen empty streams of a zero-final parallel
        # checkpoint may be absent. Permission/IO/symlink errors still escape.
        if ("expected_pending_roles" in binding and name in {"role-outputs.jsonl", "source-audit.jsonl"}
                and binding["prefixes"].get(name) == {"bytes": 0, "sha256": EMPTY_SHA}):
            return b""
        raise


def _prefixes(run, expected, binding=None):
    if not isinstance(expected, dict) or set(expected) != {"role-outputs.jsonl", "source-audit.jsonl", "graph/node-trace.jsonl"}:
        raise _blocked("recovery_prefix_invalid")
    for name, prefix in expected.items():
        body = _stream_body(run, name, binding) if binding is not None else _body(run / name)
        length = prefix.get("bytes") if isinstance(prefix, dict) else None
        if (type(length) is not int or length < 0 or length > len(body)
                or hashlib.sha256(body[:length]).hexdigest() != prefix.get("sha256")):
            raise _blocked("recovery_original_prefix_drift")


def _history_segments(grant, run, sf=None):
    """Verify a small, explicitly granted chain; each old claim stays its owner.

    Archived grants are the existing recovery history, not a new registry. A
    segment may contain no calls when a consumed recovery failed before launch.
    """
    binding = _binding(grant["binding"])
    history = binding.get("recovery_history", [])
    if not isinstance(history, list) or len(history) > MAX_RECOVERY_HISTORY:
        raise _blocked("recovery_history_invalid")
    root_method = research._digest(grant["source_method_identity"])
    current_method = research._digest(grant["continuation_method_identity"])
    counter = binding["call_counter"]
    if type(counter) is not int or not 0 < counter <= 4096:
        raise _blocked("recovery_counter_invalid")
    segments, seen_methods, seen_tasks, seen_grants = [], {root_method, current_method}, {grant["source_task_id"]}, {grant["grant_id"]}
    previous_end = None
    for index, entry in enumerate(history):
        if (not isinstance(entry, dict) or set(entry) != HISTORY_KEYS
                or not isinstance(entry["method_identity"], dict)
                or not _hash(entry["claim_sha256"]) or not _hash(entry["grant_sha256"])
                or type(entry["call_from"]) is not int or type(entry["call_to"]) is not int
                or not 1 < entry["call_from"] <= 4097 or not entry["call_from"] - 1 <= entry["call_to"] <= counter):
            raise _blocked("recovery_history_invalid")
        task_id, grant_id = _id(entry["task_id"]), _id(entry["grant_id"])
        method = research._digest(entry["method_identity"])
        if method in seen_methods or task_id in seen_tasks or grant_id in seen_grants:
            raise _blocked("recovery_history_duplicate")
        seen_methods.add(method); seen_tasks.add(task_id); seen_grants.add(grant_id)
        if previous_end is None:
            segments.append({"task_id": grant["source_task_id"], "method_sha256": root_method,
                             "call_from": 1, "call_to": entry["call_from"] - 1})
        elif entry["call_from"] != previous_end + 1:
            raise _blocked("recovery_history_range_drift")
        archive = _safe(run / "recovery-history" / grant_id)
        old = _json(archive / "grant.json")
        if (set(old) != GRANT_KEYS or type(old.get("version")) is not int or old["version"] != 1
                or old.get("user_authorization") != "EXPLICIT" or old.get("grant_id") != grant_id
                or research._digest(old) != entry["grant_sha256"]
                or any(old.get(key) != grant.get(key) for key in ("source_task_id", "symbol", "run_id", "source_method_identity"))):
            raise _blocked("recovery_history_grant_drift")
        old_root, old_method = _compatibility(old, entry["method_identity"])
        prior = _binding(old["binding"])
        if (old_root != root_method or old_method != method
                or prior.get("recovery_history", []) != history[:index]
                or type(prior["call_counter"]) is not int or prior["call_counter"] != entry["call_from"] - 1
                or any(prior[key] != binding[key] for key in ("selection", "source_claim_sha256", "packet_sha256", "config_sha256", "staged_code_sha256"))
                or prior.get("prelaunch_tail") != binding.get("prelaunch_tail")
                or prior.get("input_prelaunch_tail") != binding.get("input_prelaunch_tail")
                or prior.get("input_wrapup_message_fingerprints") is not None
                    and prior["input_wrapup_message_fingerprints"] != binding.get("input_wrapup_message_fingerprints")
                or index > 0 and "input_prelaunch_tail" in binding and "input_wrapup_message_fingerprints" not in prior
                or prior["usage_receipts"] != {key: value for key, value in binding["usage_receipts"].items() if int(key) < entry["call_from"]}
                or prior["outputs"] != {key: value for key, value in binding["outputs"].items() if int(key) < entry["call_from"]}
                or _sha(archive / "worker-terminal.json") != prior["worker_terminal_sha256"]
                or _sha(archive / "graph-run-manifest.json") != prior["manifest_sha256"]):
            raise _blocked("recovery_history_binding_drift")
        start = _json(archive / "start.json")
        if start != {"source_task_id": grant["source_task_id"], "recovery_task_id": task_id,
                     "grant_sha256": entry["grant_sha256"], "checkpoint": prior["checkpoint"], "counter": prior["call_counter"]}:
            raise _blocked("recovery_history_start_drift")
        _prefixes(run, prior["prefixes"], prior)
        path = _safe(research._selection_claim(binding["selection"], method))
        claim = _json(path)
        info = _recovery_info(claim)
        if (_sha(path) != entry["claim_sha256"] or claim.get("state") != "needs_recovery"
                or type(claim.get("version")) is not int or claim["version"] != 1
                or claim.get("selection") != binding["selection"] or claim.get("method_sha256") != method
                or claim.get("task_id") != task_id or info["grant_sha256"] != entry["grant_sha256"]
                or info["source_task_id"] != grant["source_task_id"] or info["run_id"] != grant["run_id"]
                or info["source_method_sha256"] != root_method or info["source_claim_sha256"] != binding["source_claim_sha256"]):
            raise _blocked("recovery_history_claim_drift")
        if sf is not None or "input_prelaunch_tail" in binding:
            with (sf or research.get_session_factory())() as db:
                task = db.get(AgentTask, task_id)
                expected = {"symbol": grant["symbol"], "native_recovery": {"source_task_id": grant["source_task_id"],
                    "run_id": grant["run_id"], "grant_sha256": entry["grant_sha256"], "dispatch_owner": info["dispatch_owner"]}}
                if (task is None or task.type != "tradingagents_research" or task.status not in {"failed", "canceled"}
                        or task.finished_at is None or json.loads(task.params) != expected):
                    raise _blocked("recovery_history_task_drift")
                if "input_prelaunch_tail" in binding:
                    # This tail can inherit only failures before a new reserve,
                    # never a paid/unknown continuation disguised as an empty range.
                    error = json.loads(task.error) if isinstance(task.error, str) else None
                    usage = db.execute(select(AgentResourceUsage.id).where(
                        AgentResourceUsage.task_id == task_id, AgentResourceUsage.kind == "model").limit(1)).first()
                    if (task.status != "failed" or entry["call_from"] != counter + 1 or entry["call_to"] != counter
                            or not isinstance(error, dict) or error.get("code") != "ResearchBlocked" or usage is not None):
                        raise _blocked("recovery_input_prelaunch_history_drift")
        segments.append({"task_id": task_id, "method_sha256": method,
                         "call_from": entry["call_from"], "call_to": entry["call_to"]})
        previous_end = entry["call_to"]
    if history:
        if previous_end != counter:
            raise _blocked("recovery_history_range_drift")
    else:
        segments.append({"task_id": grant["source_task_id"], "method_sha256": root_method, "call_from": 1, "call_to": counter})
    return segments


def _prelaunch_tail(grant, run, segments, sf=None):
    """Verify one explicitly pinned, never-launched original source tail.

    This is not a usage receipt or zero-cost accounting. Counter and missing
    files stay untouched. Readers recheck the same DB facts and original archive
    without depending on a still-live authorization or on the mutable execution.
    """
    binding = grant["binding"]
    if "prelaunch_tail" not in binding:
        return None
    tail, origin = binding["prelaunch_tail"], segments[0]
    if (not isinstance(tail, dict) or set(tail) != PRELAUNCH_TAIL_KEYS
            or type(tail.get("version")) is not int or tail["version"] != 1
            or type(tail.get("call_id")) is not int or not 1 < tail["call_id"] == origin["call_to"] <= 4096
            or origin["call_from"] != 1 or origin["task_id"] != grant["source_task_id"]
            or any(not _hash(tail.get(key)) for key in PRELAUNCH_TAIL_KEYS - {"version", "call_id", "model_receipts_prefix"})
            or any(grant["source_method_identity"]["source_files"].get(key) != value for key, value in PRELAUNCH_SOURCE.items())
            or str(tail["call_id"]) in binding["outputs"] or str(tail["call_id"]) in binding["usage_receipts"]
            or any(_safe(run / folder / (str(tail["call_id"]) + ".json")).exists() for folder in ("model-outputs", "usage-receipts"))):
        raise _blocked("recovery_prelaunch_tail_invalid")
    history = binding.get("recovery_history", [])
    first_id = history[0]["grant_id"] if history else grant["grant_id"]
    expected_grant_sha = history[0]["grant_sha256"] if history else research._digest(grant)
    archive = _safe(run / "recovery-history" / _id(first_id))
    if archive.exists():
        original = _json(archive / "grant.json")
        start = _json(archive / "start.json")
        if (research._digest(original) != expected_grant_sha or original["binding"].get("prelaunch_tail") != tail
                or original["source_task_id"] != grant["source_task_id"] or original["run_id"] != grant["run_id"]
                or original["source_method_identity"] != grant["source_method_identity"]
                or start.get("source_task_id") != grant["source_task_id"] or start.get("grant_sha256") != expected_grant_sha
                or start.get("counter") != origin["call_to"] or start.get("checkpoint") != original["binding"]["checkpoint"]):
            raise _blocked("recovery_prelaunch_archive_drift")
        _id(start.get("recovery_task_id"))
        execution = archive / "execution.json"
        original_binding = _binding(original["binding"])
    else:
        if history:
            raise _blocked("recovery_prelaunch_archive_drift")
        execution, original_binding = run / "execution.json", binding
    if ("expected_pending_roles" not in original_binding or original_binding["completed_roles"] != []
            or original_binding["call_counter"] != tail["call_id"]
            or _sha(execution) != tail["source_execution_sha256"]
            or _json(execution).get("state") != "needs_native_recovery" or _json(execution).get("error_kind") != "BudgetError"):
        raise _blocked("recovery_prelaunch_execution_drift")

    source_receipts = {number: _json(run / "usage-receipts" / (number + ".json"))
                       for number in binding["usage_receipts"] if int(number) < tail["call_id"]}
    if (set(source_receipts) != {str(n) for n in range(1, tail["call_id"])}
            or not set(source_receipts) <= set(binding["outputs"])):
        raise _blocked("recovery_prelaunch_source_usage_drift")
    expected_ids = {_id(row.get("usage_id")) for row in source_receipts.values()}
    with (sf or research.get_session_factory())() as db:
        task = db.get(AgentTask, grant["source_task_id"])
        rows = db.execute(select(AgentResourceUsage).where(AgentResourceUsage.task_id == grant["source_task_id"],
            AgentResourceUsage.kind == "model").order_by(AgentResourceUsage.id).limit(4097)).scalars().all()
        facts = [{column.name: (value.isoformat() if isinstance(value, datetime) else value)
                  for column in AgentResourceUsage.__table__.columns for value in (getattr(row, column.name),)} for row in rows]
        error = json.loads(task.error) if task and isinstance(task.error, str) and len(task.error) <= MAX_BYTES else None
        if (task is None or task.type != "tradingagents_research" or task.status != "failed" or task.finished_at is None
                or json.loads(task.params) != {"symbol": grant["symbol"]} or not isinstance(error, dict)
                or error.get("code") != "BudgetError" or not isinstance(error.get("message"), str)
                or hashlib.sha256(error["message"].encode()).hexdigest() != PRELAUNCH_UNKNOWN_MESSAGE_SHA
                or hashlib.sha256(task.error.encode()).hexdigest() != tail["source_task_error_sha256"]
                or len(rows) > 4096 or len(rows) != len(expected_ids) or {row.id for row in rows} != expected_ids
                or research._digest(facts) != tail["source_model_usage_facts_sha256"]
                or any(row.started_at is None or row.finished_at is None or row.state != "succeeded"
                       or row.scope != agent_budget.SCOPE_SELECTED_RESEARCH_LLM for row in rows)):
            raise _blocked("recovery_prelaunch_source_usage_drift")

    prefix = tail["model_receipts_prefix"]
    if (not isinstance(prefix, dict) or set(prefix) != {"bytes", "sha256"} or type(prefix.get("bytes")) is not int
            or not 0 < prefix["bytes"] <= MAX_BYTES or not _hash(prefix.get("sha256"))):
        raise _blocked("recovery_prelaunch_stream_drift")
    with _safe(run / "graph/model-receipts.jsonl").open("rb") as handle:
        body = handle.read(prefix["bytes"])
    if len(body) != prefix["bytes"] or hashlib.sha256(body).hexdigest() != prefix["sha256"]:
        raise _blocked("recovery_prelaunch_stream_drift")
    records = [json.loads(line) for line in body.splitlines() if line.strip()]
    error_index = next((i for i, row in enumerate(records) if isinstance(row, dict) and row.get("status") == "broker_error"), -1)
    if error_index < 1:
        raise _blocked("recovery_prelaunch_stream_drift")
    first_error = records[error_index]
    if (research._digest(first_error) != tail["first_broker_error_record_sha256"]
            or first_error.get("role") not in PARALLEL_ANALYSTS or first_error.get("call_id") is not None
            or first_error.get("error_type") != "{'kind': 'BudgetError'}"
            or {row.get("call_id") for row in records[:error_index] if isinstance(row, dict)} != {int(n) for n in source_receipts}
            or len(records[:error_index]) != len(source_receipts)):
        raise _blocked("recovery_prelaunch_stream_drift")
    for index, row in enumerate(records):
        if not isinstance(row, dict) or row.get("case_id") != grant["symbol"] or row.get("arm") != "ta-luna":
            raise _blocked("recovery_prelaunch_stream_drift")
        if index < error_index:
            number = row.get("call_id")
            if (type(number) is not int or row.get("status") != "returned" or str(number) not in source_receipts
                    or row.get("role") != source_receipts[str(number)].get("role")
                    or row.get("response_sha256") != _json(run / "model-outputs" / (str(number) + ".json")).get("output_sha256")):
                raise _blocked("recovery_prelaunch_stream_drift")
        elif index > error_index and (row.get("status") != "broker_error" or row.get("call_id") is not None
                                     or row.get("error_type") != "{'kind': 'DriverUnavailable'}"):
            raise _blocked("recovery_prelaunch_stream_drift")
    return tail["call_id"]


def _input_prelaunch_tail(grant, run, segments, sf=None):
    """Prove one pre-reserve input rejection after three completed analysts.

    No missing slot is priced, refunded, declared known or repaired. Whole-source
    DB closure plus the pinned check-before-reserve implementation proves that
    this *declared final slot* never reserved or launched. Paid answers stay put.
    """
    binding = grant["binding"]
    tail = binding.get("input_prelaunch_tail")
    if tail is None:
        return None
    counter = binding.get("call_counter")
    fingerprints = tail.get("pending_message_fingerprints") if isinstance(tail, dict) else None
    if (not isinstance(tail, dict) or set(tail) != INPUT_PRELAUNCH_TAIL_KEYS or type(tail.get("version")) is not int or tail["version"] != 1
            or type(counter) is not int or not 3 < counter <= 4096 or type(tail.get("call_id")) is not int or tail["call_id"] != counter
            or binding.get("expected_pending_role") != "Market Analyst" or "expected_pending_roles" in binding or "prelaunch_tail" in binding
            or any(grant["source_method_identity"]["source_files"].get(key) != value for key, value in INPUT_PRELAUNCH_SOURCE.items())
            or not segments or segments[0] != {"task_id": grant["source_task_id"], "method_sha256": research._digest(grant["source_method_identity"]), "call_from": 1, "call_to": counter}
            or any(row["call_from"] != counter + 1 or row["call_to"] != counter for row in segments[1:])
            or any(not _hash(tail.get(key)) for key in PRELAUNCH_TAIL_KEYS - {"version", "call_id", "model_receipts_prefix"})
            or type(tail.get("input_chars")) is not int or type(tail.get("input_limit_chars")) is not int
            or not 0 < tail["input_limit_chars"] < tail["input_chars"] <= MAX_BYTES
            or not isinstance(fingerprints, list) or not 3 <= len(fingerprints) <= 128 or any(not _hash(value) for value in fingerprints)
            or set(binding["outputs"]) != {str(number) for number in range(1, counter)} or set(binding["usage_receipts"]) != set(binding["outputs"])
            or any(_safe(run / folder / (str(counter) + ".json")).exists() for folder in ("model-outputs", "usage-receipts"))):
        raise _blocked("recovery_input_prelaunch_tail_invalid")
    completed = binding.get("completed_roles")
    if (not isinstance(completed, list) or len(completed) != 3 or any(not isinstance(row, dict) for row in completed)
            or {row.get("role") for row in completed} != PARALLEL_ANALYSTS - {"Market Analyst"}):
        raise _blocked("recovery_input_prelaunch_roles_invalid")
    history = binding.get("recovery_history", [])
    if history and "input_wrapup_message_fingerprints" not in binding:
        raise _blocked("recovery_input_prelaunch_history_drift")
    first_id = history[0]["grant_id"] if history else grant["grant_id"]
    first_sha = history[0]["grant_sha256"] if history else research._digest(grant)
    archive = _safe(run / "recovery-history" / _id(first_id))
    original_binding = binding
    if archive.exists():
        original = _json(archive / "grant.json")
        original_binding = _binding(original["binding"])
        start = _json(archive / "start.json")
        if (research._digest(original) != first_sha or original_binding.get("input_prelaunch_tail") != tail
                or any(original.get(key) != grant.get(key) for key in ("source_task_id", "run_id", "source_method_identity"))
                or start != {"source_task_id": grant["source_task_id"], "recovery_task_id": _id(start.get("recovery_task_id")),
                    "grant_sha256": first_sha, "checkpoint": original_binding["checkpoint"], "counter": counter}):
            raise _blocked("recovery_input_prelaunch_archive_drift")
        execution, terminal = archive / "execution.json", archive / "worker-terminal.json"
    else:
        if history:
            raise _blocked("recovery_input_prelaunch_archive_drift")
        execution, terminal = run / "execution.json", run / "worker-terminal.json"
    status = _json(execution)
    worker = _json(terminal)
    if (_sha(execution) != tail["source_execution_sha256"] or status.get("state") != "needs_native_recovery"
            or status.get("error_kind") != "BudgetError" or status.get("automatic_retry") is not False
            or _sha(terminal) != original_binding["worker_terminal_sha256"] or type(worker.get("exit_code")) is not int or worker["exit_code"] == 0):
        raise _blocked("recovery_input_prelaunch_execution_drift")
    for index, entry in enumerate(history):
        # Each archived execution is the state immediately before that grant.
        # The following archive (or current run before dispatch) preserves the
        # preceding zero-call failure without rewriting its consumed grant.
        next_grant_id = history[index + 1]["grant_id"] if index + 1 < len(history) else grant["grant_id"]
        next_archive = _safe(run / "recovery-history" / _id(next_grant_id))
        failure_root = next_archive if next_archive.exists() else run
        failure = _json(failure_root / "execution.json")
        next_binding = (_binding(_json(next_archive / "grant.json")["binding"])
                        if next_archive.exists() else binding)
        worker = _json(failure_root / "worker-terminal.json")
        if (failure.get("state") != "needs_native_recovery" or failure.get("error_kind") != "ResearchBlocked"
                or failure.get("recovery_task_id") != entry["task_id"] or failure.get("automatic_retry") is not False
                or _sha(failure_root / "worker-terminal.json") != next_binding["worker_terminal_sha256"]
                or type(worker.get("exit_code")) is not int or worker["exit_code"] == 0):
            raise _blocked("recovery_input_prelaunch_history_drift")
    from app.services.tradingagents_native.transport_process import project_usage_evidence
    receipts, finals, expected_ids = {}, [], set()
    for number in range(1, counter):
        key = str(number)
        usage, output = (_json(run / folder / (key + ".json")) for folder in ("usage-receipts", "model-outputs"))
        value = output.get("structured_output")
        projected = project_usage_evidence(usage.get("usage"))[0]
        if (_sha(run / "usage-receipts" / (key + ".json")) != binding["usage_receipts"][key]
                or _sha(run / "model-outputs" / (key + ".json")) != binding["outputs"][key]
                or usage.get("usage_known") is not True or projected is None or usage.get("terminal_kind") != "typed_response"
                or usage.get("task_id") != grant["source_task_id"] or usage.get("method_sha256") != segments[0]["method_sha256"]
                or type(usage.get("call_id")) is not int or usage["call_id"] != number or type(output.get("call_id")) is not int or output["call_id"] != number
                or output.get("role") not in PARALLEL_ANALYSTS or usage.get("role") != output["role"] or not isinstance(value, dict)
                or output.get("output_sha256") != research._digest(value) or usage.get("output_sha256") != output["output_sha256"]
                or "tool_calls" in value and not isinstance(value["tool_calls"], list)):
            raise _blocked("recovery_input_prelaunch_source_usage_drift")
        uid = _id(usage.get("usage_id"))
        if uid in expected_ids:
            raise _blocked("recovery_input_prelaunch_source_usage_drift")
        expected_ids.add(uid)
        receipts[uid] = (usage, projected)
        if not value.get("tool_calls"):
            finals.append({"role": output["role"], "call_id": number, "output_sha256": output["output_sha256"]})
    if finals != completed:
        raise _blocked("recovery_input_prelaunch_roles_invalid")
    with (sf or research.get_session_factory())() as db:
        task = db.get(AgentTask, grant["source_task_id"])
        rows = db.execute(select(AgentResourceUsage).where(AgentResourceUsage.task_id == grant["source_task_id"], AgentResourceUsage.kind == "model")
            .order_by(AgentResourceUsage.id).limit(4097)).scalars().all()
        facts = [{column.name: (value.isoformat() if isinstance(value, datetime) else value) for column in AgentResourceUsage.__table__.columns for value in (getattr(row, column.name),)} for row in rows]
        error = json.loads(task.error) if task and isinstance(task.error, str) and len(task.error) <= MAX_BYTES else None
        expected_message = f"模型输入 {tail['input_chars']} 字符超过 Agent 上限 {tail['input_limit_chars']}"
        if (task is None or task.type != "tradingagents_research" or task.status != "failed" or task.finished_at is None
                or json.loads(task.params) != {"symbol": grant["symbol"]} or not isinstance(error, dict) or error.get("code") != "BudgetError"
                or error.get("message") != expected_message or hashlib.sha256(task.error.encode()).hexdigest() != tail["source_task_error_sha256"]
                or len(rows) != counter - 1 or {row.id for row in rows} != expected_ids or research._digest(facts) != tail["source_model_usage_facts_sha256"]):
            raise _blocked("recovery_input_prelaunch_source_usage_drift")
        for row in rows:
            usage, projected = receipts[row.id]
            if (row.state != "succeeded" or row.usage_known != 1 or row.started_at is None or row.finished_at is None or row.finished_at < row.started_at
                    or row.scope != agent_budget.SCOPE_SELECTED_RESEARCH_LLM or row.budget_date != row.started_at.date().isoformat()
                    or row.purpose != "tradingagents." + usage["role"].replace(" ", "_").lower() or row.provider != grant["source_method_identity"]["transport"]["provider"]
                    or row.model != grant["source_method_identity"]["transport"]["model"] or row.attempts != 1 or row.max_retries != 0
                    or row.input_tokens != projected["input_tokens"] or row.output_tokens != projected["output_tokens"]):
                raise _blocked("recovery_input_prelaunch_source_usage_drift")
    prefix = tail["model_receipts_prefix"]
    if (not isinstance(prefix, dict) or set(prefix) != {"bytes", "sha256"} or type(prefix.get("bytes")) is not int
            or not 0 < prefix["bytes"] <= MAX_BYTES or not _hash(prefix.get("sha256"))):
        raise _blocked("recovery_input_prelaunch_stream_drift")
    body = _body(run / "graph/model-receipts.jsonl")[:prefix["bytes"]]
    if len(body) != prefix["bytes"] or hashlib.sha256(body).hexdigest() != prefix["sha256"]:
        raise _blocked("recovery_input_prelaunch_stream_drift")
    records = [json.loads(line) for line in body.splitlines() if line.strip()]
    if len(records) != counter:
        raise _blocked("recovery_input_prelaunch_stream_drift")
    returned = {row.get("call_id"): row for row in records[:-1] if isinstance(row, dict)}
    if len(returned) != counter - 1 or set(returned) != set(range(1, counter)):
        raise _blocked("recovery_input_prelaunch_stream_drift")
    for usage, _ in receipts.values():
        row = returned[usage["call_id"]]
        if (row.get("case_id") != grant["symbol"] or row.get("arm") != "ta-luna" or row.get("status") != "returned"
                or row.get("role") != usage["role"] or row.get("response_sha256") != usage["output_sha256"]):
            raise _blocked("recovery_input_prelaunch_stream_drift")
    failed = records[-1]
    if (not isinstance(failed, dict) or research._digest(failed) != tail["first_broker_error_record_sha256"]
            or failed.get("case_id") != grant["symbol"] or failed.get("arm") != "ta-luna" or failed.get("status") != "broker_error"
            or failed.get("call_id") is not None or failed.get("role") != "Market Analyst" or failed.get("error_type") != "{'kind': 'BudgetError'}"):
        raise _blocked("recovery_input_prelaunch_stream_drift")
    return counter


def _first_parallel_failure(binding, grant):
    return (isinstance(grant, dict) and grant.get("user_authorization") == "EXPLICIT"
        and type(binding.get("call_counter")) is int and binding["call_counter"] == 1
        and binding.get("outputs") == {} and set(binding.get("usage_receipts", {})) == {"1"}
        and binding.get("completed_roles") == [] and binding.get("recovery_history", []) == []
        and "prelaunch_tail" not in binding and "input_prelaunch_tail" not in binding and "expected_pending_roles" in binding
        and set(binding["expected_pending_roles"]) == PARALLEL_ANALYSTS
        and type(binding["checkpoint"].get("step")) is int and binding["checkpoint"]["step"] == 0
        and all(binding["prefixes"].get(name) == {"bytes": 0, "sha256": EMPTY_SHA}
                for name in ("role-outputs.jsonl", "source-audit.jsonl")))


def _first_failure_receipt(run, grant, sf=None):
    """One bound failed call is not a saved answer or a fresh-graph license."""
    usage = _json(run / "usage-receipts/1.json")
    diagnostic = usage.get("transport_diagnostic")
    order = diagnostic.get("event_order") if isinstance(diagnostic, dict) else None
    shapes = [(row.get("event_type"), row.get("item_type")) for row in order
              if isinstance(row, dict)] if isinstance(order, list) else []
    if (usage.get("usage_known") is not False or usage.get("usage") is not None
            or usage.get("output_sha256") is not None or usage.get("role") not in PARALLEL_ANALYSTS
            or not isinstance(usage.get("usage_id"), str) or not HEX32.fullmatch(usage["usage_id"])
            or usage.get("terminal_kind") != "unknown_transport_failure" or not isinstance(diagnostic, dict)
            or diagnostic.get("kind") != "codex_typed_terminal_failure" or diagnostic.get("failure_stage") != "terminal"
            or type(diagnostic.get("exit_code")) is not int or diagnostic["exit_code"] != 1
            or type(diagnostic.get("turn_count")) is not int or diagnostic["turn_count"] != 0
            or type(diagnostic.get("turn_started_count")) is not int or diagnostic["turn_started_count"] != 1
            or diagnostic.get("final_file_present") is not False or diagnostic.get("refusal_present") is not False
            or diagnostic.get("event_order_complete") is not True or diagnostic.get("explicit_error_codes") != []
            or diagnostic.get("unknown_error_codes") != [] or not isinstance(order, list) or len(shapes) != len(order)
            or not 4 <= len(shapes) <= 16 or shapes[0] != ("thread.started", "none")
            or shapes[-3:] != [("turn.started", "none"), ("error", "none"), ("turn.failed", "none")]
            or any(shape != ("item.completed", "error") for shape in shapes[1:-3])):
        raise _blocked("recovery_initial_failure_receipt_invalid")
    if sf is not None:
        with sf() as db:
            row = db.get(AgentResourceUsage, usage["usage_id"])
            original_ids = list(db.scalars(select(AgentResourceUsage.id).where(
                AgentResourceUsage.task_id == grant["source_task_id"], AgentResourceUsage.kind == "model").limit(2)))
            if (row is None or row.task_id != grant["source_task_id"] or row.kind != "model"
                    or row.scope != agent_budget.SCOPE_SELECTED_RESEARCH_LLM or row.started_at is None
                    or row.state != "failed" or row.usage_known or original_ids != [usage["usage_id"]]):
                raise _blocked("recovery_initial_failure_usage_drift")
    return usage


def _frozen_records(run, binding, segments, *, grant=None, sf=None):
    counter = binding["call_counter"]
    outputs, usages = binding["outputs"], binding["usage_receipts"]
    gap = (_input_prelaunch_tail(grant, run, segments, sf) if "input_prelaunch_tail" in binding
           else _prelaunch_tail(grant, run, segments, sf)) if grant is not None else None
    if ("prelaunch_tail" in binding or "input_prelaunch_tail" in binding) and gap is None:
        raise _blocked("recovery_prelaunch_tail_invalid")
    first_failure = _first_parallel_failure(binding, grant)
    if (not isinstance(outputs, dict) or not isinstance(usages, dict) or not outputs and not first_failure
            or set(usages) != {str(n) for n in range(1, counter + 1) if n != gap} or not set(outputs) <= set(usages)):
        raise _blocked("recovery_original_index_invalid")
    if first_failure:
        _first_failure_receipt(run, grant, sf)
    for folder, expected in (("model-outputs", outputs), ("usage-receipts", usages)):
        actual = set()
        path = _safe(run / folder)
        paths = () if first_failure and folder == "model-outputs" and not path.exists() else path.iterdir()
        for count, path in enumerate(paths, 1):
            if count > 4096 or not re.fullmatch(r"[1-9][0-9]{0,3}\.json", path.name) or path.is_symlink():
                raise _blocked("recovery_original_index_invalid")
            if int(path.stem) <= counter:
                actual.add(path.stem)
        if actual != set(expected):
            raise _blocked("recovery_original_index_invalid")
        for number, expected_hash in expected.items():
            if not _hash(expected_hash) or _sha(run / folder / (number + ".json")) != expected_hash:
                raise _blocked("recovery_original_drift")
    for segment in segments:
        for number in range(segment["call_from"], segment["call_to"] + 1):
            if number == gap:
                continue
            usage = _json(run / "usage-receipts" / (str(number) + ".json"))
            if (usage.get("task_id") != segment["task_id"] or usage.get("method_sha256") != segment["method_sha256"]
                    or type(usage.get("call_id")) is not int or usage["call_id"] != number):
                raise _blocked("recovery_original_usage_identity_drift")


def _load_context(source_task_id, run_id, app, sf, *, initial=True):
    _id(source_task_id); _id(run_id)
    grant, admission, driver, old_method, method = _grant(source_task_id, run_id, app, sf)
    binding, symbol = _binding(grant["binding"]), grant["symbol"]
    _source_task(source_task_id, symbol, sf)
    run = _safe(research.ROOT / symbol / run_id)
    packet = _json(run / "packet.json")
    source_claim = _safe(research._selection_claim(binding["selection"], old_method))
    source = _json(source_claim)
    if (_sha(source_claim) != binding["source_claim_sha256"] or source.get("state") != "needs_recovery"
            or source.get("task_id") != source_task_id or source.get("selection") != binding["selection"]
            or source.get("method_sha256") != old_method or binding["selection"].get("symbol") != symbol):
        raise _blocked("recovery_selection_drift")
    fixed = {"packet.json": "packet_sha256", "config.json": "config_sha256", "staged-code.json": "staged_code_sha256",
             "graph/run-manifest.json": "manifest_sha256"}
    for name, key in fixed.items():
        if not _hash(binding[key]) or _sha(run / name) != binding[key]:
            raise _blocked("recovery_frozen_input_drift")
    if packet.get("ticker") != symbol or packet.get("case_id") != symbol:
        raise _blocked("recovery_packet_identity_drift")
    if _json(run / "config.json") != {"approved_model_identity": grant["continuation_method_identity"]["transport"]}:
        raise _blocked("recovery_native_config_drift")
    staged = _json(run / "staged-code.json")
    from app.services.tradingagents_driver import _worker_bodies
    if staged != {name: hashlib.sha256(body).hexdigest() for name, body in _worker_bodies().items()}:
        raise _blocked("recovery_staged_code_drift")
    for name, expected in staged.items():
        if not re.fullmatch(r"[a-z_]+\.py", name) or not _hash(expected) or _sha(run / "code" / name) != expected:
            raise _blocked("recovery_staged_code_drift")
    counter = binding["call_counter"]
    segments = _history_segments(grant, run, sf)
    _frozen_records(run, binding, segments, grant=grant, sf=sf)
    _prefixes(run, binding["prefixes"], binding)
    completed = [json.loads(line)["peer"] for line in _stream_body(run, "role-outputs.jsonl", binding).splitlines() if line.strip()]
    original_roles = [{key: peer[key] for key in ("role", "call_id", "output_sha256")}
                      for peer in completed if peer["call_id"] <= counter]
    pending = binding.get("expected_pending_roles", [binding.get("expected_pending_role")])
    if (original_roles != binding["completed_roles"] or any(role not in research.ROLES for role in pending)
            or any(row["role"] in pending for row in original_roles)):
        raise _blocked("recovery_pending_or_completed_roles_drift")
    if initial:
        _initial_snapshot(run, binding, symbol, packet, admission, source_grant=grant, sf=sf)
    return {"grant": grant, "grant_sha256": research._digest(grant), "admission": admission, "driver": driver,
            "old_method": old_method, "method": method, "run": run, "packet": packet, "binding": binding,
            "source_claim": source_claim, "source": source, "segments": segments, "session_factory": sf,
            "claim": _safe(research._selection_claim(binding["selection"], method))}


def _initial_snapshot(run, binding, symbol, packet, admission=None, *, source_grant=None, sf=None):
    counter = binding["call_counter"]
    if (_json(run / "call-counter.json") != {"reserved": counter}
            or _sha(run / "worker-terminal.json") != binding["worker_terminal_sha256"]
            or type(_json(run / "worker-terminal.json").get("exit_code")) is not int
            or _json(run / "execution.json").get("state") not in {"needs_native_recovery", "canceled"}):
        raise _blocked("recovery_checkpoint_or_terminal_drift")
    files = binding["checkpoint"].get("files")
    if (not isinstance(files, dict) or set(files) != {"db", "-wal", "-shm"}
            or not _hash(files["db"]) or any(value is not None and not _hash(value) for value in files.values())):
        raise _blocked("recovery_checkpoint_or_terminal_drift")
    offline = ({"grant": source_grant, "session_factory": sf}
               if (_first_parallel_failure(binding, source_grant) or "input_prelaunch_tail" in binding)
               and files.get("-wal") is None and files.get("-shm") is None else None)
    if checkpoint_snapshot(run, symbol, offline=offline) != binding["checkpoint"]:
        raise _blocked("recovery_checkpoint_or_terminal_drift")
    manifest = _json(run / "graph/run-manifest.json")
    expected_thread = hashlib.sha256((symbol + ":" + packet["trade_date"] + ":" + manifest["config_signature"]).encode()).hexdigest()[:16]
    if binding["checkpoint"].get("thread_id") != expected_thread:
        raise _blocked("recovery_checkpoint_thread_drift")
    if "input_prelaunch_tail" in binding:
        if any(int(path.stem) > counter for folder in ("model-outputs", "usage-receipts")
               for path in _safe(run / folder).iterdir()):
            raise _blocked("recovery_input_prelaunch_history_drift")
        _input_prelaunch_checkpoint(run, symbol, binding, offline=offline)
    if "expected_pending_roles" in binding:
        # No placeholder files are created for an answer that never arrived.
        # This applies only before dispatch; later physical calls may append
        # outputs numbered after the frozen counter without changing the root.
        if binding["outputs"] == {}:
            outputs = _safe(run / "model-outputs")
            if outputs.exists() and next(outputs.iterdir(), None) is not None:
                raise _blocked("recovery_parallel_original_output_drift")
        for name in ("role-outputs.jsonl", "source-audit.jsonl"):
            if binding["prefixes"].get(name) == {"bytes": 0, "sha256": EMPTY_SHA} and _stream_body(run, name, binding):
                raise _blocked("recovery_parallel_original_role_stream_drift")
        historical = (admission or {}).get("unknown_usage_exception", {}).get("allowed_unknown_usage", ())
        _parallel_checkpoint(run, symbol, binding, historical, offline=offline)


def _input_prelaunch_checkpoint(run, symbol, binding, *, offline=None):
    """Three native analyst results survive; only Market wrap_up is pending."""
    if binding["checkpoint"].get("step") != 0:
        raise _blocked("recovery_input_prelaunch_checkpoint_invalid")
    thread, root_id = (binding["checkpoint"][key] for key in ("thread_id", "checkpoint_id"))
    reports = {"Sentiment Analyst": "sentiment_report", "News Analyst": "news_report", "Fundamentals Analyst": "fundamentals_report"}
    counts = {role: sum(_json(run / "model-outputs" / (number + ".json"))["role"] == role for number in binding["outputs"]) for role in PARALLEL_ANALYSTS}
    with _checkpoint_reader(run, symbol, offline=offline) as connection:
        namespaces = connection.execute("SELECT checkpoint_ns,MAX(checkpoint_id) FROM checkpoints WHERE thread_id=? AND checkpoint_ns<>'' GROUP BY checkpoint_ns LIMIT 5", (thread,)).fetchall()
        if len(namespaces) != 4:
            raise _blocked("recovery_input_prelaunch_checkpoint_invalid")
        errors = connection.execute("SELECT task_path,value FROM writes WHERE thread_id=? AND checkpoint_ns='' AND checkpoint_id=? AND channel='__error__' LIMIT 2", (thread, root_id)).fetchall()
        if (len(errors) != 1 or errors[0][0] != "~__pregel_pull, Market Analyst"
                or not isinstance(errors[0][1], bytes) or hashlib.sha256(errors[0][1]).hexdigest() != INPUT_PRELAUNCH_NATIVE_ERROR_SHA):
            raise _blocked("recovery_input_prelaunch_checkpoint_invalid")
        found = set()
        for namespace, checkpoint_id in namespaces:
            role, separator, suffix = namespace.partition(":")
            row = connection.execute("SELECT metadata FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?", (thread, namespace, checkpoint_id)).fetchone()
            meta = json.loads(row[0])
            expected_step = 2 * counts.get(role, 0) - (0 if role == "Market Analyst" else 1)
            if (not separator or not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", suffix)
                    or role not in PARALLEL_ANALYSTS or role in found or not isinstance(meta, dict) or meta.get("parents") != {"": root_id}
                    or type(meta.get("step")) is not int or not 0 < meta["step"] == expected_step):
                raise _blocked("recovery_input_prelaunch_checkpoint_invalid")
            errors = connection.execute("SELECT task_path,value FROM writes WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=? AND channel='__error__' LIMIT 2", (thread, namespace, checkpoint_id)).fetchall()
            if role == "Market Analyst":
                if (len(errors) != 1 or errors[0][0] != "~__pregel_pull, wrap_up" or not isinstance(errors[0][1], bytes)
                        or hashlib.sha256(errors[0][1]).hexdigest() != INPUT_PRELAUNCH_NATIVE_ERROR_SHA
                        or connection.execute("SELECT 1 FROM writes WHERE thread_id=? AND checkpoint_ns='' AND checkpoint_id=? AND task_path=? AND channel='market_report' LIMIT 1", (thread, root_id, "~__pregel_pull, Market Analyst")).fetchone()):
                    raise _blocked("recovery_input_prelaunch_checkpoint_invalid")
            elif errors or not connection.execute("SELECT 1 FROM writes WHERE thread_id=? AND checkpoint_ns='' AND checkpoint_id=? AND task_path=? AND channel=? LIMIT 1", (thread, root_id, "~__pregel_pull, " + role, reports[role])).fetchone():
                raise _blocked("recovery_input_prelaunch_checkpoint_invalid")
            found.add(role)


def _parallel_checkpoint(run, symbol, binding, allowed_unknown_usage=(), *, offline=None):
    """Validate the one supported zero-final native fan-out, without loading context."""
    if binding["checkpoint"].get("step") != 0:
        raise _blocked("recovery_parallel_checkpoint_invalid")
    thread, root_id = (binding["checkpoint"][key] for key in ("thread_id", "checkpoint_id"))
    with _checkpoint_reader(run, symbol, offline=offline) as connection:
        namespaces = connection.execute("SELECT checkpoint_ns,MAX(checkpoint_id) FROM checkpoints "
            "WHERE thread_id=? AND checkpoint_ns<>'' GROUP BY checkpoint_ns LIMIT 5", (thread,)).fetchall()
        if len(namespaces) != 4:
            raise _blocked("recovery_parallel_checkpoint_invalid")
        found = set()
        for namespace, checkpoint_id in namespaces:
            role, separator, suffix = namespace.partition(":")
            metadata = connection.execute("SELECT metadata FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                (thread, namespace, checkpoint_id)).fetchone()
            meta = json.loads(metadata[0])
            if (not separator or not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", suffix)
                    or role not in PARALLEL_ANALYSTS or role in found
                    or not isinstance(meta, dict) or meta.get("parents") != {"": root_id}
                    or type(meta.get("step")) is not int or meta["step"] not in (0, 2)
                    or not connection.execute("SELECT 1 FROM writes WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=? "
                        "AND task_path='~__pregel_pull, agent' AND channel='__error__' LIMIT 1", (thread, namespace, checkpoint_id)).fetchone()
                    or not connection.execute("SELECT 1 FROM writes WHERE thread_id=? AND checkpoint_ns='' AND checkpoint_id=? "
                        "AND task_path=? AND channel='__error__' LIMIT 1", (thread, root_id, "~__pregel_pull, " + role)).fetchone()):
                raise _blocked("recovery_parallel_checkpoint_invalid")
            found.add(role)
    # These facts came from _grant -> _quality's exact-method, ID/date/scope
    # validation and the shared ensure_model_usage_known gate. Use the full
    # historical ID/date/scope facts to bind the original failed call or reuse
    # an old typed answer; physical reserve/start still receive the separate
    # active-today projection.
    permitted_unknown = {fact["usage_id"] for fact in allowed_unknown_usage
                         if fact["scope"] == agent_budget.SCOPE_SELECTED_RESEARCH_LLM}
    if not binding["outputs"]:
        usage = _json(run / "usage-receipts/1.json")
        if usage.get("usage_id") not in permitted_unknown:
            raise _blocked("recovery_initial_failure_not_authorized")
    for number in binding["outputs"]:
        output = _json(run / "model-outputs" / (number + ".json"))
        value = output.get("structured_output")
        usage = _json(run / "usage-receipts" / (number + ".json"))
        reusable_unknown = (usage.get("usage_known") is False and usage.get("usage_id") in permitted_unknown
                            and usage.get("terminal_kind") == "typed_response" and isinstance(value, dict)
                            and usage.get("output_sha256") == output.get("output_sha256") == research._digest(value))
        if (output.get("role") not in PARALLEL_ANALYSTS or type(output.get("call_id")) is not int or output["call_id"] != int(number)
                or not isinstance(value, dict) or not isinstance(value.get("tool_calls"), list) or not value["tool_calls"]
                or output.get("output_sha256") != research._digest(value)
                or not (usage.get("usage_known") is True or reusable_unknown)
                or len(binding["pending_message_fingerprints"][output["role"]]) < 3):
            raise _blocked("recovery_parallel_original_proposal_invalid")


def _pending_guard(binding):
    first = True
    pending = set(binding.get("expected_pending_roles", []))
    def check(request, counter):
        nonlocal first
        role = request.get("role")
        if "input_prelaunch_tail" in binding and role in {row["role"] for row in binding["completed_roles"]}:
            raise _blocked("recovery_completed_role_replay_refused")
        if first and counter != binding["call_counter"]:
            raise _blocked("recovery_fresh_or_pending_role_mismatch")
        if "expected_pending_roles" not in binding:
            if first and role != binding["expected_pending_role"]:
                raise _blocked("recovery_fresh_or_pending_role_mismatch")
            if first and "input_prelaunch_tail" in binding:
                expected = binding.get("input_wrapup_message_fingerprints", [
                    *binding["input_prelaunch_tail"]["pending_message_fingerprints"], INPUT_PRELAUNCH_WRAP_UP_SHA])
                messages = request.get("messages")
                if (not isinstance(messages, list) or len(messages) < len(expected)
                        or [research._digest(row) for row in messages[-len(expected):]] != expected):
                    raise _blocked("recovery_checkpoint_messages_drift")
        elif role in pending:
            expected, messages = binding["pending_message_fingerprints"][role], request.get("messages")
            if (not isinstance(messages, list) or len(messages) < len(expected)
                    or [research._digest(row) for row in messages[-len(expected):]] != expected):
                raise _blocked("recovery_checkpoint_messages_drift")
            pending.remove(role)
        elif first or role not in PARALLEL_ANALYSTS and pending:
            raise _blocked("recovery_fresh_or_pending_role_mismatch")
        first = False
    return check


def _context(source_task_id, run_id, app, sf, *, initial=True):
    try:
        return _load_context(source_task_id, run_id, app, sf, initial=initial)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        raise _blocked("recovery_artifact_invalid") from None


def _recovery_info(claim):
    value = claim.get("native_recovery")
    if (not isinstance(value, dict) or set(value) != {"grant_sha256", "dispatch_owner", "source_task_id", "run_id", "source_method_sha256", "source_claim_sha256"}
            or any(not isinstance(value.get(key), str) or not HEX32.fullmatch(value[key]) for key in ("dispatch_owner", "source_task_id", "run_id"))
            or any(not _hash(value.get(key)) for key in ("grant_sha256", "source_method_sha256", "source_claim_sha256"))):
        raise _blocked("recovery_claim_invalid")
    return value


def _owned(context, owner, task_id=None):
    claim = _json(context["claim"])
    recovery = _recovery_info(claim)
    if (type(claim.get("version")) is not int or claim["version"] != 1
            or claim.get("method_sha256") != context["method"] or claim.get("selection") != context["binding"]["selection"]
            or recovery.get("grant_sha256") != context["grant_sha256"] or recovery.get("dispatch_owner") != owner
            or recovery.get("source_task_id") != context["grant"]["source_task_id"]
            or recovery.get("run_id") != context["grant"]["run_id"]
            or recovery.get("source_method_sha256") != context["old_method"]
            or recovery.get("source_claim_sha256") != context["binding"]["source_claim_sha256"]
            or task_id is not None and claim.get("task_id") != task_id):
        raise _blocked("recovery_owner_drift")
    return claim


def _dispatch(context):
    with worker_lease(research.ROOT / "selection-claims"):
        records = research._selection_records(context["binding"]["selection"])
        segments = _history_segments(context["grant"], context["run"])
        prior_methods = {row["method_sha256"] for row in segments}
        if (_sha(context["source_claim"]) != context["binding"]["source_claim_sha256"]
                or {row["method_sha256"] for row in records if row["method_sha256"] in prior_methods} != prior_methods):
            raise _blocked("recovery_selection_drift")
        others = [row for row in records if row["method_sha256"] not in prior_methods]
        if others:
            info = _recovery_info(others[0]) if len(others) == 1 else {}
            if (len(others) == 1 and others[0].get("method_sha256") == context["method"]
                    and info.get("grant_sha256") == context["grant_sha256"]
                    and info.get("source_task_id") == context["grant"]["source_task_id"]
                    and info.get("run_id") == context["grant"]["run_id"]
                    and info.get("source_claim_sha256") == context["binding"]["source_claim_sha256"]):
                return _owned(context, info["dispatch_owner"]), False
            raise _blocked("recovery_selection_already_owned")
        _initial_snapshot(context["run"], context["binding"], context["grant"]["symbol"], context["packet"], context["admission"],
                          source_grant=context["grant"], sf=context["session_factory"])
        owner = uuid.uuid4().hex
        claim = {"version": 1, "selection": context["binding"]["selection"], "method_sha256": context["method"],
            "state": "recovery_dispatching", "task_id": None, "updated_at": research._now().isoformat(),
            "native_recovery": {"grant_sha256": context["grant_sha256"], "dispatch_owner": owner,
                "source_task_id": context["grant"]["source_task_id"], "run_id": context["grant"]["run_id"],
                "source_method_sha256": context["old_method"], "source_claim_sha256": context["binding"]["source_claim_sha256"]}}
        atomic_json(context["claim"], claim)
        return claim, True


def _advance(context, owner, state, *, task_id=None, result_sha256=None):
    with worker_lease(research.ROOT / "selection-claims"):
        claim = _owned(context, owner)
        previous = claim["state"]
        if state in {"recovery_queued", "recovery_dispatch_unknown"}:
            if previous != "recovery_dispatching":
                return False
        elif state == "recovering":
            if previous not in {"recovery_dispatching", "recovery_queued"} or claim.get("task_id") not in {None, task_id}:
                raise _blocked("recovery_grant_consumed")
        elif previous != "recovering" or claim.get("task_id") != task_id:
            raise _blocked("recovery_owner_drift")
        claim.update(state=state, task_id=task_id or claim.get("task_id"), updated_at=research._now().isoformat())
        if result_sha256 is not None:
            claim.update(run_id=context["grant"]["run_id"], result_sha256=result_sha256)
        atomic_json(context["claim"], claim)
        return True


async def create_recovery(source_task_id, run_id, app):
    from app.services import agent_tasks
    from app.services.tradingagents_driver import drain_thread
    sf = research.get_session_factory()
    context = await agent_tasks._db_thread(_context, source_task_id, run_id, app, sf, initial=False)
    claim, created = await agent_tasks._db_thread(_dispatch, context)
    if not created:
        task_id = claim.get("task_id")
        if task_id:
            row = await agent_tasks._db_thread(agent_tasks.get_task, task_id)
            if row is not None:
                return row
        raise _blocked("recovery_already_dispatched")
    owner = claim["native_recovery"]["dispatch_owner"]
    params = {"symbol": context["grant"]["symbol"], "native_recovery": {"source_task_id": source_task_id,
              "run_id": run_id, "grant_sha256": context["grant_sha256"], "dispatch_owner": owner}}
    try:
        task = await agent_tasks.create_task_async("tradingagents_research", params, created_by="maintenance")
        await agent_tasks._db_thread(_advance, context, owner, "recovery_queued", task_id=task["id"])
        return task
    except BaseException as error:
        await drain_thread(_advance, context, owner, "recovery_dispatch_unknown",
                                     task_id=getattr(error, "task_id", None))
        raise


def _authorizer(context, params, task_id, app, sf):
    current = _context(params["source_task_id"], params["run_id"], app, sf, initial=False)
    if (current["method"] != context["method"] or current["grant_sha256"] != params["grant_sha256"]
            or current["admission"]["artifact_sha256"] != context["admission"]["artifact_sha256"]):
        raise _blocked("recovery_runtime_authorization_drift")
    claim = _owned(current, params["dispatch_owner"], task_id)
    if claim["state"] != "recovering":
        raise _blocked("recovery_owner_drift")
    return research._allowed_unknown_usage(current["admission"])


def public_lineage(value):
    if (not isinstance(value, dict) or set(value) != LINEAGE_KEYS or type(value.get("version")) is not int or value["version"] != 1
            or any(not isinstance(value.get(key), str) or not HEX32.fullmatch(value[key]) for key in ("source_task_id", "recovery_task_id", "source_run_id"))
            or any(not _hash(value.get(key)) for key in ("source_method_sha256", "recovery_method_sha256"))
            or any(type(value.get(key)) is not int or not 0 <= value[key] <= 10000 for key in
                   ("reused_original_outputs", "new_call_from", "new_call_to", "inherited_unknown_usage_count"))
            or value["new_call_from"] < 1 or value["new_call_to"] < value["new_call_from"]):
        raise _blocked("recovery_lineage_invalid")
    return dict(value)


def completed_recovery(records, binding, method):
    try:
        return _completed_recovery(records, binding, method)
    except (OSError, ValueError, TypeError, KeyError):
        raise _blocked("recovery_completion_drift") from None


def _completed_recovery(records, binding, method):
    current = [row for row in records if row.get("method_sha256") == method]
    if len(current) != 1 or current[0].get("state") != "completed":
        raise _blocked("selection_needs_recovery")
    claim = current[0]
    info = _recovery_info(claim)
    run = _safe(research._folder(binding["symbol"]) / _id(claim["run_id"]))
    result, actual = research._read_bound(run / "result.json")
    lineage = public_lineage(result.get("recovery_lineage"))
    if "recovery_grant_id" in result:
        grant = _json(run / "recovery-history" / _id(result["recovery_grant_id"]) / "grant.json")
        if (set(grant) != GRANT_KEYS or type(grant.get("version")) is not int or grant["version"] != 1
                or grant.get("user_authorization") != "EXPLICIT" or research._digest(grant) != info["grant_sha256"]
                or grant["grant_id"] != result["recovery_grant_id"] or grant["run_id"] != claim["run_id"]
                or grant["symbol"] != binding["symbol"] or grant["source_task_id"] != lineage["source_task_id"]
                or grant["binding"]["selection"] != binding):
            raise _blocked("recovery_completion_drift")
        old_method, current_method = _compatibility(grant, grant["continuation_method_identity"])
        if (current_method != method or old_method != lineage["source_method_sha256"]
                or lineage["new_call_from"] != grant["binding"]["call_counter"] + 1
                or lineage["reused_original_outputs"] != len(grant["binding"]["outputs"])):
            raise _blocked("recovery_completion_drift")
        segments = _history_segments(grant, run)
        _frozen_records(run, grant["binding"], segments, grant=grant)
        if lineage["inherited_unknown_usage_count"] != sum(
                _json(run / "usage-receipts" / (number + ".json")).get("usage_known") is not True
                for number in grant["binding"]["usage_receipts"]):
            raise _blocked("recovery_completion_drift")
        expected_methods = {row["method_sha256"] for row in segments} | {method}
        if len(records) != len(expected_methods) or {row["method_sha256"] for row in records} != expected_methods:
            raise _blocked("recovery_completion_drift")
    elif len(records) != 2:
        # Legacy first recoveries predate the internal archive pointer.
        raise _blocked("recovery_completion_drift")
    sources = [row for row in records if row["method_sha256"] == lineage["source_method_sha256"]]
    if len(sources) != 1:
        raise _blocked("recovery_completion_drift")
    source = sources[0]
    path = research._selection_claim(binding, source["method_sha256"])
    if (actual != claim.get("result_sha256") or lineage["recovery_method_sha256"] != method
            or lineage["source_method_sha256"] != source["method_sha256"] or source.get("state") != "needs_recovery"
            or source.get("task_id") != lineage["source_task_id"] or claim.get("task_id") != lineage["recovery_task_id"]
            or _sha(path) != info.get("source_claim_sha256") or not _hash(info.get("grant_sha256"))
            or info.get("run_id") != lineage["source_run_id"] or claim.get("run_id") != lineage["source_run_id"]
            or info.get("source_task_id") != lineage["source_task_id"] or info.get("source_method_sha256") != lineage["source_method_sha256"]):
        raise _blocked("recovery_completion_drift")
    return claim


def _final_evidence(context, task_id, final):
    run, binding = context["run"], context["binding"]
    last = _json(run / "call-counter.json").get("reserved")
    if (type(last) is not int or not binding["call_counter"] < last <= 4096
            or not isinstance(final, dict) or any(not isinstance(final.get(key), str) or not final[key].strip() for key in research.REPORTS)):
        raise _blocked("recovery_completion_incomplete")
    new_outputs = {}
    for number in range(binding["call_counter"] + 1, last + 1):
        usage = _json(run / "usage-receipts" / (str(number) + ".json"))
        output = _json(run / "model-outputs" / (str(number) + ".json"))
        structured = output.get("structured_output")
        if (not isinstance(structured, dict) or type(output.get("call_id")) is not int or output["call_id"] != number
                or output.get("role") not in research.ROLES or output["role"] != usage.get("role")
                or output.get("output_sha256") != research._digest(structured)
                or output["output_sha256"] != usage.get("output_sha256")):
            raise _blocked("recovery_new_output_drift")
        new_outputs[number] = output
        if (usage.get("task_id") != task_id or usage.get("method_sha256") != context["method"]
                or type(usage.get("call_id")) is not int or usage["call_id"] != number
                or usage.get("usage_known") is not True or not isinstance(usage.get("usage"), dict)
                or not _safe(run / "model-outputs" / (str(number) + ".json")).is_file()):
            raise _blocked("recovery_new_usage_incomplete")
    peers = [json.loads(row)["peer"] for row in _body(run / "role-outputs.jsonl").splitlines() if row.strip()]
    traces = [json.loads(row) for row in _body(run / "graph/node-trace.jsonl").splitlines() if row.strip()]
    for peer in peers:
        number = peer.get("call_id")
        if type(number) is not int or not 1 <= number <= last:
            raise _blocked("recovery_role_output_drift")
        if number > binding["call_counter"]:
            output = new_outputs[number]
            if (peer.get("role") != output["role"] or peer.get("output_sha256") != output["output_sha256"]
                    or output["structured_output"].get("tool_calls")):
                raise _blocked("recovery_role_output_drift")
    if (len(peers) != len(research.ROLES) or len({row["call_id"] for row in peers}) != len(peers)
            or {row["role"] for row in peers} != set(research.ROLES)
            or not any(row.get("node") == "Memory Log" and row.get("event") == "end" for row in traces)):
        raise _blocked("recovery_completion_incomplete")
    return last


async def run_recovery(rec, params, app):
    from app.services.tradingagents_driver import drain_thread, file_sha, source_input_identity
    if (not isinstance(params, dict) or set(params) != {"symbol", "native_recovery"}
            or not isinstance(params["native_recovery"], dict)
            or set(params["native_recovery"]) != {"source_task_id", "run_id", "grant_sha256", "dispatch_owner"}):
        raise _blocked("recovery_task_params_invalid")
    supplied = params["native_recovery"]
    _id(supplied["dispatch_owner"])
    sf = research.get_session_factory()
    await rec.checkpoint_async()
    with worker_lease(research.ROOT / "selected-execution"):
        context = await drain_thread(_context, supplied["source_task_id"], supplied["run_id"], app, sf)
        if context["grant_sha256"] != supplied["grant_sha256"] or context["grant"]["symbol"] != params["symbol"]:
            raise _blocked("recovery_task_grant_drift")
        with worker_lease(research._folder(params["symbol"])):
            try:
                await drain_thread(_advance, context, supplied["dispatch_owner"], "recovering", task_id=rec.task_id)
                run, binding, grant = (context[key] for key in ("run", "binding", "grant"))
                history = _safe(run / "recovery-history" / grant["grant_id"])
                history.mkdir(parents=True, exist_ok=False)
                for name in ("execution.json", "worker-terminal.json", "graph/run-manifest.json"):
                    (history / name.replace("/", "-")).write_bytes(_body(run / name))
                atomic_json(history / "grant.json", grant)
                atomic_json(history / "start.json", {"source_task_id": grant["source_task_id"], "recovery_task_id": rec.task_id,
                    "grant_sha256": context["grant_sha256"], "checkpoint": binding["checkpoint"], "counter": binding["call_counter"]})
                pending_guard = _pending_guard(binding)
                def authorize():
                    return _authorizer(context, supplied, rec.task_id, app, sf)
                def before_request(request, counter):
                    authorize()
                    pending_guard(request, counter)
                final = await context["driver"]._execute(rec=rec, packet=context["packet"], run=run,
                    budget=agent_budget, identity=grant["continuation_method_identity"], max_calls=None,
                    budget_scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM, usage_authorizer=authorize,
                    recovery_guard=before_request)
                await rec.checkpoint_async()
                authorize()
                last = _final_evidence(context, rec.task_id, final)
                original_unknown = sum(_json(run / "usage-receipts" / (number + ".json")).get("usage_known") is not True
                                       for number in binding["usage_receipts"])
                lineage = public_lineage({"version": 1, "source_task_id": grant["source_task_id"], "recovery_task_id": rec.task_id,
                    "source_run_id": grant["run_id"], "source_method_sha256": context["old_method"], "recovery_method_sha256": context["method"],
                    "reused_original_outputs": len(binding["outputs"]), "new_call_from": binding["call_counter"] + 1,
                    "new_call_to": last, "inherited_unknown_usage_count": original_unknown})
                audits = [json.loads(row) for row in _body(run / "source-audit.jsonl").splitlines() if row.strip()]
                review = context["driver"]._review_summary(audits)
                result = {"version": 1, "state": "completed", "symbol": params["symbol"], "run_id": grant["run_id"],
                    "generated_at": research._now().isoformat(), "as_of": context["packet"]["as_of"], "upstream_sha": research.UPSTREAM_SHA,
                    "input_sha256": research._digest(context["packet"]), "method_sha256": context["method"],
                    "source_input_sha256": source_input_identity(context["packet"]), "verification": research.VERIFICATION,
                    "production_eligible": False, "reports": {key: final[key] for key in research.REPORTS},
                    "review_state": "finite_failed_requires_review" if review["finite_failures"] else "unreviewed",
                    "review_summary": review, "selection": binding["selection"], "authorization_mode": "RESEARCH_ONLY_AUTHORIZED",
                    "net_gain_verified": False, "recovery_lineage": lineage, "recovery_grant_id": grant["grant_id"]}
                atomic_json(run / "result.json", result)
                result_sha = file_sha(run / "result.json")
                atomic_json(research._folder(params["symbol"]) / "latest.json", {"run_id": grant["run_id"], "result_sha256": result_sha})
                atomic_json(run / "execution.json", {"state": "completed", "completed_at": research._now().isoformat(),
                    "recovery_task_id": rec.task_id, "source_task_id": grant["source_task_id"], "quality": "unverified"})
                await drain_thread(_advance, context, supplied["dispatch_owner"], "completed", task_id=rec.task_id, result_sha256=result_sha)
                return {"kind": "tradingagents_research", "state": "completed", "symbol": params["symbol"],
                    "run_id": grant["run_id"], "source_task_id": grant["source_task_id"], "recovery_lineage": lineage,
                    "production_eligible": False}
            except BaseException as error:
                # Cancellation may arrive while the CAS drains. Never rewrite
                # another owner or roll a durable completed claim backward.
                claim = _owned(context, supplied["dispatch_owner"])
                if claim["state"] == "recovering" and claim.get("task_id") == rec.task_id:
                    await drain_thread(_advance, context, supplied["dispatch_owner"], "needs_recovery", task_id=rec.task_id)
                    diagnostic = {"state": "needs_native_recovery", "error_kind": type(error).__name__,
                        "finished_at": research._now().isoformat(), "recovery_task_id": rec.task_id, "automatic_retry": False}
                    if (isinstance(error, research.ResearchBlocked) and isinstance(error.code, str)
                            and re.fullmatch(r"[a-z][a-z0-9_]{0,99}", error.code)):
                        diagnostic["error_code"] = error.code
                    atomic_json(context["run"] / "execution.json", diagnostic)
                raise
