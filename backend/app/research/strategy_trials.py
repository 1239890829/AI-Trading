"""IMP-020 trial-family and signal-overlap evidence helpers.

Offline/research-only. This module makes the trial denominator and exact signal-event
overlap reproducible; it never owns strategy promotion or production parameters.
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
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
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
    trials: Iterable[dict], *, selected_trial_id: str | None = None,
    selected_label: str | None = None, alpha: float = 0.05, method: str | None = None,
) -> dict:
    """Seal every attempted trial plus a deterministic multiplicity result."""
    a = _finite(alpha)
    if a is None or not 0 < a < 1:
        raise ValueError("alpha 必须是 (0,1) 内有限数")
    raw = list(trials)
    if not raw:
        raise ValueError("trial family 不能为空")
    total = len(raw)
    chosen = method or (SINGLE_TRIAL_METHOD if total == 1 else BONFERRONI_METHOD)
    if total == 1 and chosen != SINGLE_TRIAL_METHOD:
        raise ValueError("单一预注册试验必须使用 single_preregistered_trial")
    if total > 1 and chosen != BONFERRONI_METHOD:
        raise ValueError("多试验族当前必须使用 Bonferroni 校正")
    rows, seen = [], set()
    manifest_complete = True
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"trial[{idx}] 必须是对象")
        label = str(item.get("label") or item.get("trial_id") or "").strip()
        explicit_trial_id = str(item.get("trial_id") or "").strip()
        explicit_condition = str(item.get("condition") or "").strip()
        trial_id = explicit_trial_id or label
        condition = explicit_condition or f"legacy:{label}"
        if not trial_id or trial_id in seen or not label:
            raise ValueError("每个 trial 必须可唯一识别且有 label")
        if not explicit_trial_id or not explicit_condition or not isinstance(item.get("train"), dict):
            manifest_complete = False
        seen.add(trial_id)
        train = item.get("train") if isinstance(item.get("train"), dict) else item
        n, excess, std = _finite(train.get("n")), _finite(train.get("excess")), _finite(train.get("std"))
        valid = bool(n is not None and n >= 2 and n.is_integer() and excess is not None and std is not None and std > 0)
        t_value = excess / (std / math.sqrt(n)) if valid else None
        p_raw = 1.0 - NormalDist().cdf(t_value) if t_value is not None else None
        multiplier = total if chosen == BONFERRONI_METHOD else 1
        p_adj = None if p_raw is None else min(p_raw * multiplier, 1.0)
        rows.append({
            "trial_id": trial_id, "label": label, "condition": condition,
            "train": {"n": int(n) if n is not None and n.is_integer() else n, "excess": excess, "std": std},
            "t": None if t_value is None else round(t_value, 8),
            "p_raw": None if p_raw is None else round(p_raw, 12),
            "p_adjusted": None if p_adj is None else round(p_adj, 12),
            "survives_alpha": bool(p_adj is not None and p_adj <= a),
        })
    if selected_trial_id is None:
        matches = [row for row in rows if row["label"] == str(selected_label or "")]
        if len(matches) != 1:
            raise ValueError("selected_label 必须唯一属于 trial family")
        selected_trial_id = matches[0]["trial_id"]
    selected = next((row for row in rows if row["trial_id"] == selected_trial_id), None)
    if selected is None:
        raise ValueError("selected_trial_id 必须属于 trial family")
    valid_trials = sum(row["p_raw"] is not None for row in rows)
    payload = {
        "evidence_version": EVIDENCE_VERSION, "method": chosen, "alpha": a,
        "trials_total": total, "trials_valid": valid_trials,
        "selected_trial_id": selected_trial_id,
        "selected_p_adjusted": selected["p_adjusted"],
        "selected_survives_alpha": selected["survives_alpha"],
        "manifest_complete": manifest_complete,
        "accounted": valid_trials == total, "trials": rows,
    }
    return {**payload, "evidence_digest": _digest(payload)}


def overlap_evidence_from_counts(
    *, target_label: str, target_condition: str, target_n: int,
    comparisons: Iterable[dict], where: str = "TRUE", table: str = "sigv",
) -> dict:
    """Seal exact symbol×date overlap counts and set identities."""
    if int(target_n) <= 0:
        raise ValueError("overlap 检查需要非空 candidate")
    clean = []
    for row in comparisons:
        incumbent = str(row.get("incumbent") or "").strip()
        condition = str(row.get("condition") or "").strip()
        incumbent_n = int(row.get("incumbent_n") or 0)
        intersection_n = int(row.get("intersection_n") or 0)
        union_n = int(row.get("union_n") or 0)
        if not incumbent or not condition or min(incumbent_n, intersection_n, union_n) < 0:
            raise ValueError("overlap comparator 证据无效")
        if (intersection_n > min(target_n, incumbent_n)
                or union_n != target_n + incumbent_n - intersection_n):
            raise ValueError("overlap 计数违反集合恒等式")
        clean.append({
            "incumbent": incumbent, "condition": condition, "target_n": int(target_n),
            "incumbent_n": incumbent_n, "intersection_n": intersection_n, "union_n": union_n,
            "jaccard": None if not union_n else round(intersection_n / union_n, 8),
            "target_containment": round(intersection_n / target_n, 8),
            "exact_duplicate": bool(target_n == incumbent_n == intersection_n),
        })
    if not clean:
        raise ValueError("overlap 检查至少需要一个 comparator")
    payload = {
        "evidence_version": EVIDENCE_VERSION, "method": "exact_signal_day",
        "target": str(target_label), "target_condition": str(target_condition),
        "table": str(table), "where": str(where), "checked": True,
        "comparisons": clean, "exact_duplicate": any(row["exact_duplicate"] for row in clean),
    }
    return {**payload, "evidence_digest": _digest(payload)}


def signal_overlap_evidence(
    con, *, target_label: str, target_cond: str, incumbents: dict[str, str],
    where: str = "TRUE", table: str = "sigv",
) -> dict:
    if not target_label or not target_cond or not incumbents:
        raise ValueError("target 与 incumbents 不能为空")
    target_n = int(con.execute(f"""
        SELECT count(*) FROM (
            SELECT thscode, date_ms FROM {table}
            WHERE ({where}) AND ({target_cond}) GROUP BY thscode, date_ms
        )
    """).fetchone()[0] or 0)
    comparisons = []
    for label, cond in incumbents.items():
        if not label or not cond:
            raise ValueError("incumbent label/condition 不能为空")
        incumbent_n, intersection_n, union_n = con.execute(f"""
            WITH hits AS (
                SELECT thscode, date_ms,
                       max(CASE WHEN ({target_cond}) THEN 1 ELSE 0 END) AS target_hit,
                       max(CASE WHEN ({cond}) THEN 1 ELSE 0 END) AS incumbent_hit
                FROM {table} WHERE ({where}) GROUP BY thscode, date_ms
            )
            SELECT sum(incumbent_hit),
                   sum(CASE WHEN target_hit=1 AND incumbent_hit=1 THEN 1 ELSE 0 END),
                   sum(CASE WHEN target_hit=1 OR incumbent_hit=1 THEN 1 ELSE 0 END)
            FROM hits
        """).fetchone()
        comparisons.append({
            "incumbent": str(label), "condition": str(cond),
            "incumbent_n": int(incumbent_n or 0), "intersection_n": int(intersection_n or 0),
            "union_n": int(union_n or 0),
        })
    return overlap_evidence_from_counts(
        target_label=target_label, target_condition=target_cond, target_n=target_n,
        comparisons=comparisons, where=where, table=table,
    )
