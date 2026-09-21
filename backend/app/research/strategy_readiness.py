"""IMP-020 production-promotion readiness evidence.

This module is read-only with respect to production state. It joins current research
verification, append-only research experiment evidence, and future actual shadow-fill
evidence into a deterministic readiness record. It never promotes or changes weights.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.research import strategy_experiments as se
from app.research import verify_registry as vr

READINESS_VERSION = 1
READINESS_DIR = Path(__file__).resolve().parents[2] / "data" / "research" / "readiness"
SHADOW_FILL_RETURN_IDENTITY = "shadow_fill_net"
STATE_BLOCKED = "blocked"
STATE_READY_FOR_HUMAN_REVIEW = "ready_for_human_review"


def _canonical(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _valid_sealed(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    raw = {k: v for k, v in payload.items() if k != "evidence_digest"}
    try:
        return payload.get("evidence_digest") == _digest(raw)
    except (TypeError, ValueError):
        return False


def _experiment_issues(strategy_key: str, experiment: dict | None) -> list[str]:
    if not isinstance(experiment, dict):
        return ["research_experiment_missing"]
    issues: list[str] = []
    try:
        se.validate_experiment(experiment)
    except ValueError:
        issues.append("research_experiment_invalid")
    identity = experiment.get("identity") if isinstance(experiment.get("identity"), dict) else {}
    if identity.get("subject") != strategy_key:
        issues.append("research_experiment_subject_mismatch")
    if experiment.get("state") != "research_observed_reference_only":
        issues.append("research_experiment_state_invalid")
    if experiment.get("automatic_promotion") is not False:
        issues.append("research_experiment_must_not_auto_promote")
    if experiment.get("production_promotion_eligible") is not False:
        issues.append("research_experiment_must_be_reference_only")
    return issues


def _verification_issues(verification: dict | None) -> list[str]:
    if not isinstance(verification, dict) or verification.get("available") is not True:
        return ["verification_missing"]
    issues: list[str] = []
    if verification.get("stale") is not False:
        issues.append("verification_stale")
    if verification.get("gate_evidence_state") != "recorded":
        issues.append("verification_gate_not_current")
    if verification.get("effective_verdict") != "pass":
        issues.append("verification_not_pass")
    if verification.get("admission_eligible_for_review") is not True:
        issues.append("research_admission_not_eligible")
    return issues


def _fill_issues(strategy_key: str, experiment: dict | None,
                 actual_fill: dict | None) -> list[str]:
    if not isinstance(actual_fill, dict):
        return ["actual_shadow_fill_missing"]
    issues: list[str] = []
    raw = {k: v for k, v in actual_fill.items() if k != "evidence_digest"}
    try:
        fill_digest_ok = actual_fill.get("evidence_digest") == _digest(raw)
    except (TypeError, ValueError):
        fill_digest_ok = False
    if not fill_digest_ok:
        issues.append("actual_shadow_fill_digest_invalid")
    if actual_fill.get("return_identity") != SHADOW_FILL_RETURN_IDENTITY:
        issues.append("actual_shadow_fill_identity_invalid")
    if actual_fill.get("source_owner") != "IMP-053":
        issues.append("actual_shadow_fill_owner_invalid")
    if actual_fill.get("execution_scope") != "hunting_shadow":
        issues.append("actual_shadow_fill_scope_invalid")
    if actual_fill.get("strategy_key") != strategy_key:
        issues.append("actual_shadow_fill_strategy_mismatch")
    exp_id = experiment.get("experiment_id") if isinstance(experiment, dict) else None
    if not exp_id or actual_fill.get("experiment_id") != exp_id:
        issues.append("actual_shadow_fill_experiment_mismatch")
    identity = (experiment.get("identity") if isinstance(experiment, dict)
                and isinstance(experiment.get("identity"), dict) else {})
    challenger = identity.get("challenger") if isinstance(identity.get("challenger"), dict) else None
    if actual_fill.get("candidate") != challenger:
        issues.append("actual_shadow_fill_candidate_mismatch")
    if actual_fill.get("cost_bps") != identity.get("cost_bps"):
        issues.append("actual_shadow_fill_cost_mismatch")
    if not str(actual_fill.get("exit_policy_id") or "").strip():
        issues.append("actual_shadow_fill_exit_policy_missing")
    fills_total = actual_fill.get("fills_total")
    closed_fills = actual_fill.get("closed_fills")
    if not isinstance(fills_total, int) or fills_total <= 0:
        issues.append("actual_shadow_fill_empty")
    elif closed_fills != fills_total:
        issues.append("actual_shadow_fill_not_fully_closed")
    if actual_fill.get("status") != "complete":
        issues.append("actual_shadow_fill_incomplete")
    for field in ("net_return_pct", "win_rate"):
        value = actual_fill.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            issues.append(f"actual_shadow_fill_{field}_missing")
    return issues



def build_readiness(
    *, strategy_key: str, verification: dict | None,
    experiment: dict | None, actual_fill: dict | None,
) -> dict:
    """Build fail-closed readiness evidence; never an approval or promotion action."""
    issues = [
        *_verification_issues(verification),
        *_experiment_issues(strategy_key, experiment),
        *_fill_issues(strategy_key, experiment, actual_fill),
    ]
    issues = list(dict.fromkeys(issues))
    state = STATE_READY_FOR_HUMAN_REVIEW if not issues else STATE_BLOCKED
    payload = {
        "readiness_version": READINESS_VERSION,
        "strategy_key": strategy_key,
        "state": state,
        "blocking_issues": issues,
        "verification": {
            "recorded_at": verification.get("recorded_at") if isinstance(verification, dict) else None,
            "effective_verdict": verification.get("effective_verdict") if isinstance(verification, dict) else None,
            "gate_evidence_state": verification.get("gate_evidence_state") if isinstance(verification, dict) else None,
        },
        "experiment_id": experiment.get("experiment_id") if isinstance(experiment, dict) else None,
        "actual_fill_digest": actual_fill.get("evidence_digest") if isinstance(actual_fill, dict) else None,
        "review_required": True,
        "automatic_promotion": False,
        "production_mutation_performed": False,
        "rollback_reopen_plan": {
            "rollback": {
                "automatic": False,
                "requires_activation_record": True,
                "restore_prior_strategy_version": True,
                "retain_failed_evidence": True,
                "triggers": ["human_reject", "post_activation_degradation", "evidence_invalidated"],
            },
            "reopen": {
                "automatic": False,
                "requires_new_evidence_version": True,
                "allowed_triggers": ["new_clean_oos", "new_actual_shadow_fill", "protocol_or_data_fix"],
                "never_rewrite_prior_result": True,
            },
        },
    }
    return {**payload, "evidence_digest": _digest(payload)}


def assess_current(strategy_key: str, *, actual_fill: dict | None = None,
                   experiment_root: Path | None = None) -> dict:
    """Read current verification + referenced experiment and assess readiness."""
    verification = vr.verification_of(strategy_key)
    record = vr.load_record(strategy_key)
    ref = record.get("research_experiment") if isinstance(record, dict) else None
    experiment_id = ref.get("experiment_id") if isinstance(ref, dict) else None
    experiment = se.load_experiment(experiment_id, root=experiment_root) if experiment_id else None
    return build_readiness(
        strategy_key=strategy_key, verification=verification,
        experiment=experiment, actual_fill=actual_fill,
    )


def save_readiness(evidence: dict, *, root: Path | None = None) -> tuple[Path, bool]:
    """Append-only/idempotent readiness artifact; never mutates production state."""
    if not _valid_sealed(evidence):
        raise ValueError("readiness evidence digest 无效")
    strategy_key = str(evidence.get("strategy_key") or "").strip()
    if not strategy_key:
        raise ValueError("strategy_key 不能为空")
    directory = root or READINESS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    identity = {
        "version": evidence.get("readiness_version"),
        "strategy_key": strategy_key,
        "verification": evidence.get("verification"),
        "experiment_id": evidence.get("experiment_id"),
        "actual_fill_digest": evidence.get("actual_fill_digest"),
    }
    rid = _digest(identity)
    path = directory / f"{strategy_key}-{rid}.json"
    encoded = json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError("同 readiness identity 已存在不同证据，拒绝覆盖")
        return path, False
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(encoded, encoding="utf-8")
    tmp.replace(path)
    return path, True


def load_readiness(path: Path) -> dict | None:
    """Read one readiness artifact and reject malformed/tampered evidence."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not _valid_sealed(data):
        return None
    if data.get("state") not in {STATE_BLOCKED, STATE_READY_FOR_HUMAN_REVIEW}:
        return None
    if data.get("automatic_promotion") is not False:
        return None
    if data.get("production_mutation_performed") is not False:
        return None
    return data


def list_readiness(strategy_key: str, *, root: Path | None = None) -> list[dict]:
    """List valid append-only readiness artifacts for one strategy."""
    directory = root or READINESS_DIR
    if not directory.exists():
        return []
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in strategy_key.strip())
    if not safe:
        return []
    rows = []
    for path in directory.glob(f"{safe}-*.json"):
        item = load_readiness(path)
        if item is not None and item.get("strategy_key") == strategy_key:
            rows.append(item)
    rows.sort(key=lambda x: (
        (x.get("verification") or {}).get("recorded_at") or "",
        x.get("evidence_digest") or "",
    ), reverse=True)
    return rows
