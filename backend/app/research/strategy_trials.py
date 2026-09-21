"""IMP-020 trial-family and signal-overlap evidence.

Offline research evidence only. This module seals the complete attempt
denominator and exact symbol-by-date overlap; it never promotes a strategy.
"""
from __future__ import annotations

import hashlib
import json
import math
from statistics import NormalDist
from typing import Iterable

EVIDENCE_VERSION = 1
BONFERRONI_METHOD = "bonferroni_one_sided_normal_approx"
SINGLE_TRIAL_METHOD = "single_preregistered_trial"


def _digest(payload: dict) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def trial_family_evidence(
    trials: Iterable[dict], *, selected_trial_id: str, alpha: float = 0.05,
) -> dict:
    """Record every tried rule and mechanically apply the family correction."""
    a = _finite(alpha)
    if a is None or not 0 < a < 1:
        raise ValueError("alpha 必须是 (0,1) 内有限数")
    raw = list(trials)
    if not raw:
        raise ValueError("trial family 不能为空")
    total = len(raw)
    method = SINGLE_TRIAL_METHOD if total == 1 else BONFERRONI_METHOD
    rows: list[dict] = []
    ids: set[str] = set()
    labels: set[str] = set()
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"trial[{idx}] 必须是对象")
        trial_id = str(item.get("trial_id") or "").strip()
        label = str(item.get("label") or trial_id).strip()
        condition = str(item.get("condition") or "").strip()
        if not trial_id or trial_id in ids or not label or label in labels or not condition:
            raise ValueError("trial_id/label 必须唯一且 condition 非空")
        ids.add(trial_id)
        labels.add(label)
        train = item.get("train")
        if not isinstance(train, dict):
            raise ValueError("每个 trial 必须带 train 统计")
        n = _finite(train.get("n"))
        excess = _finite(train.get("excess"))
        std = _finite(train.get("std"))
        valid = bool(
            n is not None and n >= 2 and n.is_integer()
            and excess is not None and std is not None and std > 0
        )
        t_value = excess / (std / math.sqrt(n)) if valid else None
        p_raw = 1.0 - NormalDist().cdf(t_value) if t_value is not None else None
        multiplier = total if method == BONFERRONI_METHOD else 1
        p_adjusted = None if p_raw is None else min(p_raw * multiplier, 1.0)
        rows.append({
            "trial_id": trial_id,
            "label": label,
            "condition": condition,
            "train": {
                "n": int(n) if n is not None and n.is_integer() else n,
                "excess": excess,
                "std": std,
            },
            "t": None if t_value is None else round(t_value, 8),
            "p_raw": None if p_raw is None else round(p_raw, 12),
            "p_adjusted": None if p_adjusted is None else round(p_adjusted, 12),
            "survives_alpha": bool(p_adjusted is not None and p_adjusted <= a),
        })
    selected = next((row for row in rows if row["trial_id"] == selected_trial_id), None)
    if selected is None:
        raise ValueError("selected_trial_id 必须唯一属于 trial family")
    valid_trials = sum(row["p_raw"] is not None for row in rows)
    payload = {
        "evidence_version": EVIDENCE_VERSION,
        "method": method,
        "alpha": a,
        "trials_total": total,
        "trials_valid": valid_trials,
        "selected_trial_id": selected_trial_id,
        "selected_label": selected["label"],
        "selected_p_adjusted": selected["p_adjusted"],
        "selected_survives_alpha": selected["survives_alpha"],
        "accounted": valid_trials == total,
        "trials": rows,
    }
    return {**payload, "evidence_digest": _digest(payload)}


def overlap_evidence_from_counts(
    *,
    target_label: str,
    target_condition: str,
    target_n: int,
    comparisons: Iterable[dict],
    where: str = "TRUE",
    table: str = "sigv",
) -> dict:
    """Seal exact symbol-by-date overlap counts computed by a trusted query."""
    if not isinstance(target_n, int) or isinstance(target_n, bool) or target_n <= 0:
        raise ValueError("target_n 必须是正整数")
    clean: list[dict] = []
    for raw in comparisons:
        incumbent = str(raw.get("incumbent") or "").strip()
        condition = str(raw.get("condition") or "").strip()
        incumbent_n = int(raw.get("incumbent_n") or 0)
        intersection = int(raw.get("intersection_n") or 0)
        union = int(raw.get("union_n") or 0)
        if not incumbent or not condition or min(incumbent_n, intersection, union) < 0:
            raise ValueError("overlap comparator 证据无效")
        if intersection > min(target_n, incumbent_n) or union < max(target_n, incumbent_n):
            raise ValueError("overlap 计数违反集合恒等式")
        clean.append({
            "incumbent": incumbent,
            "condition": condition,
            "target_n": target_n,
            "incumbent_n": incumbent_n,
            "intersection_n": intersection,
            "union_n": union,
            "jaccard": None if union == 0 else round(intersection / union, 8),
            "target_containment": round(intersection / target_n, 8),
            "exact_duplicate": bool(
                target_n == incumbent_n == intersection and target_n > 0
            ),
        })
    if not clean:
        raise ValueError("至少需要一个 incumbent comparator")
    payload = {
        "evidence_version": EVIDENCE_VERSION,
        "method": "exact_signal_day",
        "target": str(target_label),
        "target_condition": str(target_condition),
        "table": str(table),
        "where": str(where),
        "candidate_n": target_n,
        "checked": True,
        "comparisons": clean,
        "exact_duplicate": any(row["exact_duplicate"] for row in clean),
    }
    return {**payload, "evidence_digest": _digest(payload)}


def signal_overlap_evidence(
    con, *, target_label: str, target_cond: str, incumbents: dict[str, str],
    where: str = "TRUE", table: str = "sigv",
) -> dict:
    """Compute exact signal-day overlap against named incumbent rules."""
    if not target_label or not target_cond:
        raise ValueError("target_label/target_cond 不能为空")
    if not incumbents:
        raise ValueError("至少需要一个 incumbent 才能声明 overlap_checked")
    target_n = int(con.execute(
        f"SELECT count(*) FROM {table} WHERE ({where}) AND ({target_cond})"
    ).fetchone()[0] or 0)
    comparisons: list[dict] = []
    for label, cond in incumbents.items():
        if not label or not cond:
            raise ValueError("incumbent label/condition 不能为空")
        incumbent_n, intersection, union = con.execute(
            f"""
            SELECT
              count(*) FILTER (WHERE ({cond})),
              count(*) FILTER (WHERE ({target_cond}) AND ({cond})),
              count(*) FILTER (WHERE ({target_cond}) OR ({cond}))
            FROM {table}
            WHERE ({where})
            """
        ).fetchone()
        comparisons.append({
            "incumbent": str(label),
            "condition": str(cond),
            "incumbent_n": int(incumbent_n or 0),
            "intersection_n": int(intersection or 0),
            "union_n": int(union or 0),
        })
    return overlap_evidence_from_counts(
        target_label=target_label,
        target_condition=target_cond,
        target_n=target_n,
        comparisons=comparisons,
        where=where,
        table=table,
    )
