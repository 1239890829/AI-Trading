"""Append-only IMP-020 research experiment evidence ledger.

This is deliberately separate from ``AgentExperiment``: that model guards already-applied
production parameter changes, while this module records offline research comparisons only.
Repeated execution of the same frozen experiment is idempotent and never increases confidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.research import strategy_verify as sv

EXPERIMENT_VERSION = 1
EXPERIMENT_DIR = Path(__file__).resolve().parents[2] / "data" / "research" / "experiments"


def _canonical(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def build_research_experiment(
    *, subject: str, hypothesis: str, ablation: dict, comparison: dict,
) -> dict:
    """Create a sealed same-basis research experiment envelope; never a promotion action."""
    if not subject.strip() or not hypothesis.strip():
        raise ValueError("subject/hypothesis 不能为空")
    if ablation.get("kind") != "leave_one_component_out":
        raise ValueError("缺 leave-one-component-out evidence")
    if comparison.get("kind") != "champion_challenger_same_basis" or comparison.get("same_basis") is not True:
        raise ValueError("缺 same-basis Champion/Challenger evidence")
    for evidence in (ablation, comparison):
        raw = {k: v for k, v in evidence.items() if k != "evidence_digest"}
        if evidence.get("evidence_digest") != _digest(raw):
            raise ValueError("comparison evidence digest 无效")
        if evidence.get("return_identity") != sv.RETURN_IDENTITY_REFERENCE_PROXY:
            raise ValueError("research experiment 只能使用 reference proxy")
        if evidence.get("production_promotion_eligible") is not False and evidence.get("promotion_basis_eligible") is not False:
            raise ValueError("research evidence 不得拥有 production promotion 权限")
    basis_keys = ("dataset", "where", "horizon", "cost_bps", "return_identity")
    for key in basis_keys:
        if ablation.get(key) != comparison.get(key):
            raise ValueError(f"ablation/comparison basis 不一致：{key}")
    identity = {
        "subject": subject.strip(),
        "hypothesis": hypothesis.strip(),
        "dataset": comparison["dataset"],
        "where": comparison["where"],
        "horizon": comparison["horizon"],
        "cost_bps": comparison["cost_bps"],
        "return_identity": comparison["return_identity"],
        "champion": {
            "label": comparison["champion"]["label"],
            "condition": comparison["champion"]["condition"],
        },
        "challenger": {
            "label": comparison["challenger"]["label"],
            "condition": comparison["challenger"]["condition"],
        },
    }
    experiment_id = _digest({"version": EXPERIMENT_VERSION, "identity": identity})
    payload = {
        "experiment_version": EXPERIMENT_VERSION,
        "experiment_id": experiment_id,
        "identity": identity,
        "ablation": ablation,
        "comparison": comparison,
        "state": "research_observed_reference_only",
        "review_required": True,
        "automatic_promotion": False,
        "production_promotion_eligible": False,
    }
    return {**payload, "evidence_digest": _digest(payload)}


def save_experiment(evidence: dict, *, root: Path | None = None) -> tuple[Path, bool]:
    """Append-only/idempotent save. Same identity + different evidence is a hard conflict."""
    payload = {k: v for k, v in evidence.items() if k != "evidence_digest"}
    if evidence.get("evidence_digest") != _digest(payload):
        raise ValueError("experiment evidence digest 无效")
    experiment_id = str(evidence.get("experiment_id") or "")
    if len(experiment_id) != 64:
        raise ValueError("experiment_id 无效")
    directory = root or EXPERIMENT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{experiment_id}.json"
    encoded = json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != encoded:
            raise ValueError("同 experiment_id 已存在不同证据，拒绝覆盖")
        return path, False
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(encoded, encoding="utf-8")
    tmp.replace(path)
    return path, True


def load_experiment(experiment_id: str, *, root: Path | None = None) -> dict | None:
    path = (root or EXPERIMENT_DIR) / f"{experiment_id}.json"
    if not path.exists():
        return None
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
