from __future__ import annotations

import pytest

from app.research import strategy_experiments as se
from app.research import strategy_verify as sv


def _sealed(kind: str) -> dict:
    if kind == "ablation":
        payload = {
            "evidence_version": 1, "kind": "leave_one_component_out",
            "dataset": {"table": "sigv", "rows": 10, "trade_days": 2, "min_date_ms": 1, "max_date_ms": 2},
            "where": "x", "horizon": 5, "cost_bps": 35.0,
            "return_identity": sv.RETURN_IDENTITY_REFERENCE_PROXY,
            "production_promotion_eligible": False,
        }
    else:
        payload = {
            "evidence_version": 1, "kind": "champion_challenger_same_basis",
            "dataset": {"table": "sigv", "rows": 10, "trade_days": 2, "min_date_ms": 1, "max_date_ms": 2},
            "where": "x", "horizon": 5, "cost_bps": 35.0,
            "return_identity": sv.RETURN_IDENTITY_REFERENCE_PROXY, "same_basis": True,
            "champion": {"label": "c", "condition": "a", "metrics": {}},
            "challenger": {"label": "h", "condition": "b", "metrics": {}},
            "promotion_basis_eligible": False,
        }
    return {**payload, "evidence_digest": se._digest(payload)}


def test_research_experiment_is_sealed_and_never_promotable():
    out = se.build_research_experiment(
        subject="s", hypothesis="h", ablation=_sealed("ablation"), comparison=_sealed("comparison")
    )
    assert len(out["experiment_id"]) == 64
    assert out["automatic_promotion"] is False
    assert out["production_promotion_eligible"] is False
    assert out["state"] == "research_observed_reference_only"


def test_research_experiment_requires_same_basis():
    comparison = _sealed("comparison")
    comparison["horizon"] = 3
    comparison["evidence_digest"] = se._digest({k: v for k, v in comparison.items() if k != "evidence_digest"})
    with pytest.raises(ValueError, match="basis"):
        se.build_research_experiment(
            subject="s", hypothesis="h", ablation=_sealed("ablation"), comparison=comparison
        )


def test_experiment_save_is_idempotent_but_never_overwrites(tmp_path):
    out = se.build_research_experiment(
        subject="s", hypothesis="h", ablation=_sealed("ablation"), comparison=_sealed("comparison")
    )
    path, created = se.save_experiment(out, root=tmp_path)
    assert created is True
    assert se.save_experiment(out, root=tmp_path) == (path, False)
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="不同证据"):
        se.save_experiment(out, root=tmp_path)
