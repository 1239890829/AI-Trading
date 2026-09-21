from __future__ import annotations

import duckdb
import pytest

from app.research import strategy_compare as sc
from app.research import strategy_verify as sv


def _con():
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE sigv(
          thscode VARCHAR, date_ms BIGINT, fwd5 DOUBLE, mfwd5 DOUBLE,
          at_limit DOUBLE, chg DOUBLE, a INTEGER, b INTEGER, c INTEGER
        )
    """)
    rows = []
    for day in range(1, 11):
        rows += [
            (f"A{day}", day, 2.0, 0.5, 0.0, 3.0, 1, 1, 0),
            (f"B{day}", day, 0.2, 0.5, 0.0, 1.0, 1, 0, 1),
            (f"C{day}", day, -0.5, 0.5, 0.0, -1.0, 0, 1, 1),
        ]
    con.executemany("INSERT INTO sigv VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return con


def test_leave_one_out_ablation_freezes_basis_and_reports_marginal_effect():
    con = _con()
    try:
        out = sc.leave_one_out_ablation(
            con,
            label="rule",
            components={"a": "a=1", "b": "b=1"},
            where="TRUE",
            cfg=sv.VerifyConfig(cost_bps=35),
            horizon=5,
        )
    finally:
        con.close()
    assert out["kind"] == "leave_one_component_out"
    assert out["dataset"]["rows"] == 30
    assert len(out["leave_one_out"]) == 2
    assert out["cost_bps"] == 35
    assert out["return_identity"] == sv.RETURN_IDENTITY_REFERENCE_PROXY
    assert out["production_promotion_eligible"] is False
    assert len(out["evidence_digest"]) == 64
    assert all(row["metrics"]["cost_bps"] == 35 for row in out["leave_one_out"])


def test_champion_challenger_is_same_basis_but_never_auto_promotion():
    con = _con()
    try:
        out = sc.champion_challenger_evidence(
            con,
            champion_label="incumbent",
            champion_cond="a=1",
            challenger_label="challenger",
            challenger_cond="b=1",
            where="TRUE",
            cfg=sv.VerifyConfig(cost_bps=35),
            horizon=5,
            champion_is_production=False,
        )
    finally:
        con.close()
    assert out["same_basis"] is True
    assert out["champion_is_production"] is False
    assert out["promotion_basis_eligible"] is False
    assert out["return_identity"] == sv.RETURN_IDENTITY_REFERENCE_PROXY
    assert out["signal_overlap"]["checked"] is True
    assert out["dataset"]["trade_days"] == 10
    assert len(out["evidence_digest"]) == 64


def test_champion_challenger_refuses_same_identity():
    con = _con()
    try:
        with pytest.raises(ValueError, match="不同具名身份"):
            sc.champion_challenger_evidence(
                con,
                champion_label="same",
                champion_cond="a=1",
                challenger_label="same",
                challenger_cond="b=1",
                where="TRUE",
                cfg=sv.VerifyConfig(cost_bps=35),
                horizon=5,
                champion_is_production=False,
            )
    finally:
        con.close()
