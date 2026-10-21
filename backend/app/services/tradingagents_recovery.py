"""Pinned, single-owner continuation of one existing native research run.

The maintenance request selects opaque identities only. The existing server
authorization, task runner, selection claims and per-call budget remain owners.
No import, preflight or ordinary read starts a worker or collects new inputs.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from app.models.agent import AgentTask
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
            or set(compatibility) != {"kind", "source_hash_pairs"}
            or compatibility["kind"] != "reviewed_host_diagnostics_and_recovery_v1"):
        raise _blocked("recovery_method_incompatible")
    old_base, new_base = deepcopy(old), deepcopy(new)
    old_files, new_files = old_base.pop("source_files", None), new_base.pop("source_files", None)
    if not isinstance(old_files, dict) or not isinstance(new_files, dict) or old_base != new_base:
        raise _blocked("recovery_graph_model_or_dependency_drift")
    allowed = {"driver.py", "codex_transport.py", "tradingagents_research.py", "tradingagents_recovery.py"}
    changed = {key for key in old_files.keys() | new_files.keys() if old_files.get(key) != new_files.get(key)}
    pairs = {key: {"old": old_files.get(key), "new": new_files.get(key)} for key in changed}
    if (not changed or not changed <= allowed or compatibility["source_hash_pairs"] != pairs
            or any(not _hash(v) for v in new_files.values()) or any(not _hash(v) for v in old_files.values())):
        raise _blocked("recovery_component_mapping_drift")
    # Names never grant compatibility. Exact old/new pairs are server-pinned;
    # all staged graph/codec/formula and model/dependency identities stay equal.
    return research._digest(old), research._digest(new)


def checkpoint_snapshot(run, symbol):
    """Logical SQLite state includes WAL; raw db/WAL/SHM hashes are retained too."""
    folder = _safe(run / "graph/cache/checkpoints")
    dbpath = _safe(folder / (symbol + ".db"))
    if not dbpath.is_file():
        raise _blocked("recovery_checkpoint_missing")
    connection = sqlite3.connect(dbpath.absolute().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
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
    finally:
        connection.close()
    files = {}
    for suffix in ("", "-wal", "-shm"):
        path = _safe(folder / (symbol + ".db" + suffix))
        files[suffix or "db"] = _sha(path) if path.exists() else None
    return {"thread_id": thread, "checkpoint_id": checkpoint_id, "step": step,
            "logical_sha256": logical, "files": files}


def _source_task(source_task_id, symbol, sf):
    with sf() as db:
        task = db.get(AgentTask, source_task_id)
        if (task is None or task.type != "tradingagents_research" or task.status not in {"failed", "canceled"}
                or json.loads(task.params) != {"symbol": symbol} or task.finished_at is None):
            raise _blocked("recovery_source_task_invalid")


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


def _load_context(source_task_id, run_id, app, sf, *, initial=True):
    _id(source_task_id); _id(run_id)
    grant, admission, driver, old_method, method = _grant(source_task_id, run_id, app, sf)
    binding, symbol = grant["binding"], grant["symbol"]
    if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
        raise _blocked("recovery_binding_invalid")
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
    if type(counter) is not int or not 0 < counter <= 4096:
        raise _blocked("recovery_counter_invalid")
    outputs, usages = binding["outputs"], binding["usage_receipts"]
    if (not isinstance(outputs, dict) or not isinstance(usages, dict) or not outputs
            or set(usages) != {str(n) for n in range(1, counter + 1)}
            or not set(outputs) <= set(usages)):
        raise _blocked("recovery_original_index_invalid")
    for folder, expected in (("model-outputs", outputs), ("usage-receipts", usages)):
        actual = set()
        for count, path in enumerate(_safe(run / folder).iterdir(), 1):
            if count > 4096 or not re.fullmatch(r"[1-9][0-9]{0,3}\.json", path.name) or path.is_symlink():
                raise _blocked("recovery_original_index_invalid")
            if int(path.stem) <= counter:
                actual.add(path.stem)
        if actual != set(expected):
            raise _blocked("recovery_original_index_invalid")
    for mapping, folder in ((outputs, "model-outputs"), (usages, "usage-receipts")):
        for number, expected in mapping.items():
            if not _hash(expected) or _sha(run / folder / (number + ".json")) != expected:
                raise _blocked("recovery_original_drift")
    for number in usages:
        usage = _json(run / "usage-receipts" / (number + ".json"))
        if (usage.get("task_id") != source_task_id or usage.get("method_sha256") != old_method
                or usage.get("call_id") != int(number)):
            raise _blocked("recovery_original_usage_identity_drift")
    prefixes = binding["prefixes"]
    if not isinstance(prefixes, dict) or set(prefixes) != {"role-outputs.jsonl", "source-audit.jsonl", "graph/node-trace.jsonl"}:
        raise _blocked("recovery_prefix_invalid")
    for name, expected in prefixes.items():
        body = _body(run / name)
        length = expected.get("bytes") if isinstance(expected, dict) else None
        if (type(length) is not int or length < 0 or length > len(body)
                or hashlib.sha256(body[:length]).hexdigest() != expected.get("sha256")):
            raise _blocked("recovery_original_prefix_drift")
    completed = [json.loads(line)["peer"] for line in _body(run / "role-outputs.jsonl").splitlines() if line.strip()]
    original_roles = [{key: peer[key] for key in ("role", "call_id", "output_sha256")}
                      for peer in completed if peer["call_id"] <= counter]
    pending = binding["expected_pending_role"]
    if (original_roles != binding["completed_roles"] or pending not in research.ROLES
            or any(row["role"] == pending for row in original_roles)):
        raise _blocked("recovery_pending_or_completed_roles_drift")
    if initial:
        _initial_snapshot(run, binding, symbol, packet)
    return {"grant": grant, "grant_sha256": research._digest(grant), "admission": admission, "driver": driver,
            "old_method": old_method, "method": method, "run": run, "packet": packet, "binding": binding,
            "source_claim": source_claim, "source": source,
            "claim": _safe(research._selection_claim(binding["selection"], method))}


def _initial_snapshot(run, binding, symbol, packet):
    counter = binding["call_counter"]
    if (_json(run / "call-counter.json") != {"reserved": counter}
            or checkpoint_snapshot(run, symbol) != binding["checkpoint"]
            or _sha(run / "worker-terminal.json") != binding["worker_terminal_sha256"]
            or type(_json(run / "worker-terminal.json").get("exit_code")) is not int
            or _json(run / "execution.json").get("state") not in {"needs_native_recovery", "canceled"}):
        raise _blocked("recovery_checkpoint_or_terminal_drift")
    manifest = _json(run / "graph/run-manifest.json")
    expected_thread = hashlib.sha256((symbol + ":" + packet["trade_date"] + ":" + manifest["config_signature"]).encode()).hexdigest()[:16]
    if binding["checkpoint"].get("thread_id") != expected_thread:
        raise _blocked("recovery_checkpoint_thread_drift")


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
        if (_sha(context["source_claim"]) != context["binding"]["source_claim_sha256"]
                or sum(row["method_sha256"] == context["old_method"] for row in records) != 1):
            raise _blocked("recovery_selection_drift")
        others = [row for row in records if row["method_sha256"] != context["old_method"]]
        if others:
            info = _recovery_info(others[0]) if len(others) == 1 else {}
            if (len(others) == 1 and others[0].get("method_sha256") == context["method"]
                    and info.get("grant_sha256") == context["grant_sha256"]
                    and info.get("source_task_id") == context["grant"]["source_task_id"]
                    and info.get("run_id") == context["grant"]["run_id"]
                    and info.get("source_claim_sha256") == context["binding"]["source_claim_sha256"]):
                return _owned(context, info["dispatch_owner"]), False
            raise _blocked("recovery_selection_already_owned")
        _initial_snapshot(context["run"], context["binding"], context["grant"]["symbol"], context["packet"])
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
    current = [row for row in records if row.get("method_sha256") == method]
    if len(records) != 2 or len(current) != 1 or current[0].get("state") != "completed":
        raise _blocked("selection_needs_recovery")
    claim = current[0]
    info = _recovery_info(claim)
    source = next(row for row in records if row is not claim)
    path = research._selection_claim(binding, source["method_sha256"])
    result, actual = research._read_bound(_safe(research._folder(binding["symbol"]) / claim["run_id"] / "result.json"))
    lineage = public_lineage(result.get("recovery_lineage"))
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
                first = True
                def authorize():
                    return _authorizer(context, supplied, rec.task_id, app, sf)
                def before_request(request, counter):
                    nonlocal first
                    authorize()
                    if first:
                        if counter != binding["call_counter"] or request.get("role") != binding["expected_pending_role"]:
                            raise _blocked("recovery_fresh_or_pending_role_mismatch")
                        first = False
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
                    "net_gain_verified": False, "recovery_lineage": lineage}
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
                    atomic_json(context["run"] / "execution.json", {"state": "needs_native_recovery", "error_kind": type(error).__name__,
                        "finished_at": research._now().isoformat(), "recovery_task_id": rec.task_id, "automatic_retry": False})
                raise
