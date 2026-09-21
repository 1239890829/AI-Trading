"""IMP-020 research trial-family and signal-overlap evidence.

Offline evidence only: this module records what was tried and whether event sets
duplicate an incumbent. It never owns strategy promotion.
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
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def trial_family_evidence(
    trials: Iterable[dict], *, selected_label: str, alpha: float = 0.05,
) -> dict:
    """Record the full trial denominator and a reproducible multiplicity correction."""
    a = _finite(alpha)
    if a is None or not 0 < a < 1:
        raise ValueError("alpha 必须是 (0,1) 内有限数")
    raw = list(trials)
    if not raw:
        raise ValueError("trial family 不能为空")
    total = len(raw)
    rows: list[dict] = []
    labels: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("每个 trial 必须是对象")
        label = str(item.get("label") or "").strip()
        if not label or label in labels:
            raise ValueError("每个 trial 必须有唯一非空 label")
        labels.add(label)
        n = _finite(item.get("n"))
        excess = _finite(item.get("excess"))
        std = _finite(item.get("std"))
        valid = bool(n is not None and n >= 2 and n.is_integer()
                     and excess is not None and std is not None and std > 0)
        t_value = excess / (std / math.sqrt(n)) if valid else None
        p_raw = 1.0 - NormalDist().cdf(t_value) if t_value is not None else None
        rows.append({
            "label": label,
            "condition": str(item.get("condition") or ""),
            "n": int(n) if n is not None and n.is_integer() else n,
            "excess": excess,
            "std": std,
            "t": None if t_value is None else round(t_value, 8),
            "p_raw": None if p_raw is None else round(p_raw, 12),
        })
    if selected_label not in labels:
        raise ValueError("selected_label 必须属于 trial family")
    method = SINGLE_TRIAL_METHOD if total == 1 else BONFERRONI_METHOD
    for row in rows:
        p_raw = row["p_raw"]
        row["p_adjusted"] = None if p_raw is None else round(min(float(p_raw) * total, 1.0), 12)
        row["survives_alpha"] = bool(
            row["p_adjusted"] is not None and row["p_adjusted"] <= a
        )
    selected = next(row for row in rows if row["label"] == selected_label)
    valid_trials = sum(row["p_raw"] is not None for row in rows)
    payload = {
        "evidence_version": EVIDENCE_VERSION,
        "method": method,
        "alpha": a,
        "trials_total": total,
        "trials_valid": valid_trials,
        "selected_label": selected_label,
        "selected_p_adjusted": selected["p_adjusted"],
        "selected_survives_alpha": selected["survives_alpha"],
        "accounted": valid_trials == total,
        "trials": rows,
    }
    return {**payload, "evidence_digest": _digest(payload)}


def overlap_evidence_from_counts(
    *, target_label: str, target_n: int, comparisons: Iterable[dict],
    where: str = "TRUE", table: str = "sigv",
) -> dict:
    """Seal exact signal-event set counts; useful for tests and persisted probes."""
    target_n = int(target_n)
    clean: list[dict] = []
    for row in comparisons:
        incumbent = str(row.get("incumbent") or "").strip()
        incumbent_n = int(row.get("incumbent_n") or 0)
        intersection_n = int(row.get("intersection_n") or 0)
        union_n = int(row.get("union_n") or 0)
        if not incumbent or min(incumbent_n, intersection_n, union_n) < 0:
            raise ValueError("overlap comparator 证据无效")
        if intersection_n > min(target_n, incumbent_n) or union_n < max(target_n, incumbent_n):
            raise ValueError("overlap 计数违反集合恒等式")
        jaccard = intersection_n / union_n if union_n else None
        containment = intersection_n / target_n if target_n else None
        exact_duplicate = bool(target_n > 0 and target_n == incumbent_n == intersection_n)
        clean.append({
            "incumbent": incumbent,
            "target_n": target_n,
            "incumbent_n": incumbent_n,
            "intersection_n": intersection_n,
            "union_n": union_n,
            "jaccard": None if jaccard is None else round(jaccard, 8),
            "target_containment": None if containment is None else round(containment, 8),
            "exact_duplicate": exact_duplicate,
        })
    if target_n <= 0 or not clean:
        raise ValueError("overlap 检查需要非空 candidate 与至少一个 comparator")
    payload = {
        "evidence_version": EVIDENCE_VERSION,
        "method": "exact_signal_day",
        "target": str(target_label),
        "table": str(table),
        "where": str(where),
        "checked": True,
        "comparisons": clean,
        "exact_duplicate": any(row["exact_duplicate"] for row in clean),
    }
    return {**payload, "evidence_digest": _digest(payload)}


def signal_overlap_evidence(
    con, *, target_label: str, target_cond: str, incumbents: dict[str, str],
    where: str = "TRUE", table: str = "sigv",
) -> dict:
    """Compare symbol×date signal identities against named incumbents."""
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
        incumbent_n, intersection_n, union_n = con.execute(
            f"""
            SELECT
              count(*) FILTER (WHERE ({cond})) AS incumbent_n,
              count(*) FILTER (WHERE ({target_cond}) AND ({cond})) AS intersection_n,
              count(*) FILTER (WHERE ({target_cond}) OR ({cond})) AS union_n
            FROM {table}
            WHERE ({where})
            """
        ).fetchone()
        comparisons.append({
            "incumbent": str(label),
            "incumbent_n": int(incumbent_n or 0),
            "intersection_n": int(intersection_n or 0),
            "union_n": int(union_n or 0),
        })
    return overlap_evidence_from_counts(
        target_label=target_label, target_n=target_n, comparisons=comparisons,
        where=where, table=table,
    )
