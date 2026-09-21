from __future__ import annotations

import duckdb
import pytest

from app.research import strategy_trials as st


def _trial(trial_id: str, label: str, condition: str, *, n: int, excess: float, std: float) -> dict:
    return {
        "trial_id": trial_id,
        "label": label,
        "condition": condition,
        "train": {"n": n, "excess": excess, "std": std},
    }


def test_trial_family_records_full_denominator_and_bonferroni():
    out = st.trial_family_evidence([
        _trial("t-a", "a", "chg > 1", n=400, excess=0.8, std=2.0),
        _trial("t-b", "b", "chg > 2", n=400, excess=0.2, std=2.0),
        _trial("t-c", "c", "chg > 3", n=400, excess=-0.1, std=2.0),
    ], selected_trial_id="t-a")
    assert out["trials_total"] == out["trials_valid"] == 3
    assert out["accounted"] is True
    assert out["method"] == st.BONFERRONI_METHOD
    assert len(out["trials"]) == 3
    assert isinstance(out["evidence_digest"], str) and len(out["evidence_digest"]) == 64
    selected = next(row for row in out["trials"] if row["trial_id"] == "t-a")
    assert selected["condition"] == "chg > 1"
    assert selected["p_adjusted"] >= selected["p_raw"]
    assert out["selected_p_adjusted"] == selected["p_adjusted"]


def test_trial_family_keeps_invalid_attempt_in_denominator():
    out = st.trial_family_evidence([
        _trial("good", "good", "TRUE", n=300, excess=0.5, std=1.0),
        _trial("broken", "broken", "FALSE", n=1, excess=1.0, std=0.0),
    ], selected_trial_id="good")
    assert out["trials_total"] == 2 and out["trials_valid"] == 1
    assert out["accounted"] is False
    assert next(row for row in out["trials"] if row["trial_id"] == "broken")["p_raw"] is None


def test_trial_family_rejects_unknown_selection():
    with pytest.raises(ValueError, match="selected_trial_id"):
        st.trial_family_evidence(
            [_trial("t-a", "a", "TRUE", n=20, excess=0.1, std=1.0)],
            selected_trial_id="missing",
        )


def _overlap_con():
    con = duckdb.connect()
    con.execute("CREATE TABLE sigv(thscode VARCHAR, date_ms BIGINT, a INTEGER, b INTEGER)")
    con.executemany("INSERT INTO sigv VALUES (?, ?, ?, ?)", [
        ("A", 1, 1, 1),
        ("B", 1, 1, 0),
        ("C", 1, 0, 1),
        ("A", 2, 1, 1),
    ])
    return con


def test_signal_overlap_detects_exact_duplicate_without_threshold_guess():
    con = _overlap_con()
    try:
        out = st.signal_overlap_evidence(
            con, target_label="target", target_cond="a=1",
            incumbents={"same": "a=1", "partial": "b=1"},
        )
    finally:
        con.close()
    assert out["checked"] is True and out["exact_duplicate"] is True
    same = next(row for row in out["comparisons"] if row["incumbent"] == "same")
    partial = next(row for row in out["comparisons"] if row["incumbent"] == "partial")
    assert same["exact_duplicate"] is True and same["jaccard"] == 1.0
    assert partial["exact_duplicate"] is False
    assert 0 < partial["jaccard"] < 1


def test_signal_overlap_requires_named_incumbent():
    con = _overlap_con()
    try:
        with pytest.raises(ValueError, match="incumbent"):
            st.signal_overlap_evidence(
                con, target_label="target", target_cond="a=1", incumbents={},
            )
    finally:
        con.close()
