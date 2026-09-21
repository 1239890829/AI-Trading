from __future__ import annotations

import duckdb
import pytest

from app.research import strategy_trials as st


def test_trial_family_records_full_denominator_and_bonferroni():
    out = st.trial_family_evidence([
        {"label": "a", "n": 400, "excess": 0.8, "std": 2.0},
        {"label": "b", "n": 400, "excess": 0.2, "std": 2.0},
        {"label": "c", "n": 400, "excess": -0.1, "std": 2.0},
    ], selected_label="a")
    assert out["trials_total"] == out["trials_valid"] == 3
    assert out["accounted"] is True
    assert out["method"] == st.BONFERRONI_METHOD
    assert len(out["trials"]) == 3
    selected = next(row for row in out["trials"] if row["label"] == "a")
    assert selected["p_adjusted"] >= selected["p_raw"]
    assert out["selected_p_adjusted"] == selected["p_adjusted"]


def test_trial_family_keeps_invalid_attempt_in_denominator():
    out = st.trial_family_evidence([
        {"label": "good", "n": 300, "excess": 0.5, "std": 1.0},
        {"label": "broken", "n": 1, "excess": 1.0, "std": 0.0},
    ], selected_label="good")
    assert out["trials_total"] == 2 and out["trials_valid"] == 1
    assert out["accounted"] is False
    assert next(row for row in out["trials"] if row["label"] == "broken")["p_raw"] is None


def test_trial_family_rejects_unknown_selection():
    with pytest.raises(ValueError, match="selected_label"):
        st.trial_family_evidence(
            [{"label": "a", "n": 20, "excess": 0.1, "std": 1.0}],
            selected_label="missing",
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


def test_current_trial_manifest_is_explicit_and_digest_sealed():
    out = st.trial_family_evidence(
        [{"trial_id": "t1", "label": "a", "condition": "chg > 0",
          "train": {"n": 400, "excess": 0.8, "std": 2.0}}],
        selected_trial_id="t1",
    )
    assert out["manifest_complete"] is True
    assert out["selected_trial_id"] == "t1"
    assert len(out["evidence_digest"]) == 64


def test_legacy_flat_trial_helper_remains_readable_but_not_current_manifest():
    out = st.trial_family_evidence(
        [{"label": "legacy", "n": 300, "excess": 0.5, "std": 1.0}],
        selected_label="legacy",
    )
    assert out["manifest_complete"] is False
    assert out["accounted"] is True


def test_overlap_count_identity_is_validated():
    with pytest.raises(ValueError):
        st.overlap_evidence_from_counts(
            target_label="candidate", target_condition="x=1", target_n=10,
            comparisons=[{"incumbent": "old", "condition": "y=1",
                          "incumbent_n": 5, "intersection_n": 6, "union_n": 10}],
        )


def test_overlap_evidence_is_digest_sealed():
    out = st.overlap_evidence_from_counts(
        target_label="candidate", target_condition="x=1", target_n=10,
        comparisons=[{"incumbent": "old", "condition": "y=1",
                      "incumbent_n": 5, "intersection_n": 2, "union_n": 13}],
    )
    assert out["checked"] is True and len(out["evidence_digest"]) == 64
