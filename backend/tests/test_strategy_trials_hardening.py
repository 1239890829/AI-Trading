from __future__ import annotations

import duckdb
import pytest

from app.research import strategy_trials as st


def test_trial_family_is_digest_sealed_and_keeps_invalid_denominator():
    evidence = st.trial_family_evidence([
        {"label": "good", "n": 400, "excess": 1.0, "std": 4.0},
        {"label": "invalid", "n": 1, "excess": None, "std": None},
    ], selected_label="good")
    assert evidence["trials_total"] == 2 and evidence["trials_valid"] == 1
    assert evidence["accounted"] is False and evidence["evidence_digest"]


def test_trial_family_requires_unique_labels():
    with pytest.raises(ValueError):
        st.trial_family_evidence([
            {"label": "dup", "n": 10, "excess": 1, "std": 2},
            {"label": "dup", "n": 10, "excess": 1, "std": 2},
        ], selected_label="dup")


def test_overlap_count_identity_is_validated_and_sealed():
    with pytest.raises(ValueError):
        st.overlap_evidence_from_counts(
            target_label="a", target_n=10,
            comparisons=[{"incumbent": "b", "incumbent_n": 5,
                          "intersection_n": 6, "union_n": 10}],
        )
    evidence = st.overlap_evidence_from_counts(
        target_label="a", target_n=10,
        comparisons=[{"incumbent": "b", "incumbent_n": 10,
                      "intersection_n": 10, "union_n": 10}],
    )
    assert evidence["exact_duplicate"] is True and evidence["evidence_digest"]


def test_signal_overlap_computes_exact_event_sets():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE sigv(thscode VARCHAR, date_ms BIGINT, a BOOLEAN, b BOOLEAN)")
    con.execute("INSERT INTO sigv VALUES ('A',1,true,true),('B',1,true,false),('C',1,false,true)")
    evidence = st.signal_overlap_evidence(con, target_label="a", target_cond="a", incumbents={"b": "b"})
    row = evidence["comparisons"][0]
    assert (row["target_n"], row["incumbent_n"], row["intersection_n"], row["union_n"]) == (2, 2, 1, 3)
    assert evidence["exact_duplicate"] is False
