"""Exact, read-only saved research for a server-owned task result reference."""
from __future__ import annotations

import json
import re

from app.models.agent import AgentTask


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _receipt_hash(research, symbol, run_id, result, actual):
    """Use existing publication receipts; never manufacture a new archive pin.

    A later pointer is irrelevant. An unfinished native attempt has no final
    digest, including legitimate same-method recoveries. Every receipt that
    does declare this completed result must agree with its bytes and identity.
    """
    folder = research._folder(symbol)
    hashes = []

    def remember(value):
        if not _hash(value) or value != actual:
            raise ValueError("saved result receipt drift")
        hashes.append(value)

    try:
        pointer = research._read_bound(folder / "latest.json", limit=4096)[0]
    except (OSError, ValueError, TypeError):
        pointer = {}
    if pointer.get("run_id") == run_id:
        remember(pointer.get("result_sha256"))

    method, packet = result.get("method_sha256"), result.get("input_sha256")
    source = result.get("source_input_sha256", packet)
    if not all(_hash(value) for value in (method, packet, source)):
        raise ValueError("saved result identity unavailable")
    key = research._digest({"input": source, "method": method})
    attempt = folder / ("attempt-" + key + ".json")
    if attempt.exists() or attempt.is_symlink():
        claim = research._read_bound(attempt, limit=16_384)[0]
        if (claim.get("run_id") != run_id or claim.get("method_sha256") != method
                or claim.get("input_sha256") != packet
                or claim.get("source_input_sha256", claim.get("input_sha256")) != source):
            raise ValueError("saved attempt identity drift")
        if "result_sha256" in claim:
            remember(claim["result_sha256"])

    if "authorization_mode" in result:
        binding = result.get("selection")
        if not isinstance(binding, dict) or binding.get("symbol") != symbol:
            raise ValueError("saved selection identity unavailable")
        selected = research._selection_claim(binding, method)
        if selected.exists() or selected.is_symlink():
            claim = research._read_bound(selected, limit=16_384)[0]
            if (type(claim.get("version")) is not int or claim["version"] != 1
                    or claim.get("selection") != binding or claim.get("method_sha256") != method
                    or claim.get("state") != "completed" or claim.get("run_id") != run_id):
                raise ValueError("saved selection completion drift")
            selected_at, updated_at = research._clock(binding.get("selected_at")), research._clock(claim.get("updated_at"))
            if (selected_at.astimezone(research._now().tzinfo).date().isoformat() != binding.get("trade_date")
                    or not selected_at <= updated_at <= research._now()):
                raise ValueError("saved selection receipt time drift")
            for key in ("task_id", "dispatch_owner"):
                value = claim.get(key)
                if value is not None and (not isinstance(value, str) or not 0 < len(value) <= 160):
                    raise ValueError("saved selection receipt owner drift")
            remember(claim.get("result_sha256"))
    if not hashes:
        raise ValueError("saved result publication receipt unavailable")
    return hashes[0]


def read(symbol, task_id, *, session_factory=None):
    from app.services import tradingagents_research as research

    research.validate_params({"symbol": symbol})
    if not isinstance(task_id, str) or re.fullmatch(r"[0-9a-f]{32}", task_id) is None:
        raise LookupError("research task not found")
    sf = session_factory or research.get_session_factory()
    with sf() as db:
        task = db.get(AgentTask, task_id)
        try:
            params = json.loads(task.params) if task is not None else None
        except (ValueError, TypeError):
            params = None
        if (task is None or task.type != "tradingagents_research"
                or not isinstance(params, dict) or params.get("symbol") != symbol):
            raise LookupError("research task not found")
        public_task = research._public_task(task)
        try:
            ref = json.loads(task.result_ref) if task.result_ref else None
        except (ValueError, TypeError):
            ref = None

    state = {
        "symbol": symbol, "requested_task_id": task_id,
        "enabled": bool(research.settings.tradingagents_research_enabled),
        "executable": False, "startup_conditions_not_checked": True, "blockers": [],
        "budget": None, "required_model_slots": None,
        "admission": {"state": "not_checked", "method_sha256": None},
        "verification": research.VERIFICATION, "production_eligible": False,
        "flow": {"roles": list(research.ROLES), "reports": list(research.REPORTS), "upstream_sha": research.UPSTREAM_SHA},
        "task": public_task, "latest": None, "saved_result_state": "unavailable",
        "recovery": {"state": "not_checked", "source_task_id": None, "run_id": None, "blockers": []},
    }
    run_id = public_task.get("result_run_id")
    if (public_task["status"] != "succeeded" or not isinstance(ref, dict)
            or ref.get("kind") != "tradingagents_research" or ref.get("symbol") != symbol
            or ref.get("run_id") != run_id or run_id is None):
        return state
    try:
        folder = research._folder(symbol) / run_id
        result, actual = research._read_bound(folder / "result.json")
        expected = _receipt_hash(research, symbol, run_id, result, actual)
        saved = research._completed_result(symbol, run_id, result, actual, expected)
        if saved is not None:
            roles, projection = research._role_projection(folder, saved)
            saved["roles"], saved["role_projection_state"] = roles, projection
            state.update(latest=saved, saved_result_state="available")
    except (OSError, ValueError, TypeError, KeyError):
        pass  # Missing/corrupt evidence is unavailable; never guess another run.
    return state
