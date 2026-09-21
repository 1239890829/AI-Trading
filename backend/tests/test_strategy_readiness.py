from __future__ import annotations

from app.research import strategy_experiments as se
from app.research import strategy_readiness as sr


def _verification(verdict="pass"):
    return {
        "available": True,
        "stale": False,
        "gate_evidence_state": "recorded",
        "effective_verdict": verdict,
        "admission_eligible_for_review": verdict == "pass",
        "recorded_at": "2026-09-21T18:00:00",
    }


def _sealed(kind: str) -> dict:
    if kind == "ablation":
        payload = {
            "evidence_version": 1, "kind": "leave_one_component_out",
            "dataset": {"table": "sigv", "rows": 10}, "where": "x",
            "horizon": 5, "cost_bps": 35.0,
            "return_identity": "reference_close_to_close_proxy",
            "production_promotion_eligible": False,
        }
    else:
        payload = {
            "evidence_version": 1, "kind": "champion_challenger_same_basis",
            "dataset": {"table": "sigv", "rows": 10}, "where": "x",
            "horizon": 5, "cost_bps": 35.0,
            "return_identity": "reference_close_to_close_proxy", "same_basis": True,
            "champion": {"label": "old", "condition": "a", "metrics": {}},
            "challenger": {"label": "new", "condition": "b", "metrics": {}},
            "promotion_basis_eligible": False,
        }
    return {**payload, "evidence_digest": se._digest(payload)}


def _experiment():
    return se.build_research_experiment(
        subject="s", hypothesis="h",
        ablation=_sealed("ablation"), comparison=_sealed("comparison"),
    )

def _fill(exp):
    payload = {
        "evidence_version": 1,
        "strategy_key": "s",
        "experiment_id": exp["experiment_id"],
        "candidate": exp["identity"]["challenger"],
        "cost_bps": 35.0,
        "exit_policy_id": "exit-v1",
        "fills_total": 20,
        "closed_fills": 20,
        "net_return_pct": 3.2,
        "win_rate": 0.55,
        "status": "complete",
        "return_identity": sr.SHADOW_FILL_RETURN_IDENTITY,
        "source_owner": "IMP-053",
        "execution_scope": "hunting_shadow",
    }
    return {**payload, "evidence_digest": sr._digest(payload)}


def test_missing_actual_fill_fails_closed_even_when_research_passes():
    out = sr.build_readiness(
        strategy_key="s",
        verification=_verification(),
        experiment=_experiment(),
        actual_fill=None,
    )
    assert out["state"] == sr.STATE_BLOCKED
    assert "actual_shadow_fill_missing" in out["blocking_issues"]
    assert out["automatic_promotion"] is False
    assert out["production_mutation_performed"] is False


def test_observe_research_never_becomes_ready_from_good_fill():
    exp = _experiment()
    out = sr.build_readiness(
        strategy_key="s",
        verification=_verification("observe"),
        experiment=exp,
        actual_fill=_fill(exp),
    )
    assert out["state"] == sr.STATE_BLOCKED
    assert "verification_not_pass" in out["blocking_issues"]


def test_complete_same_identity_evidence_only_reaches_human_review():
    exp = _experiment()
    out = sr.build_readiness(
        strategy_key="s",
        verification=_verification(),
        experiment=exp,
        actual_fill=_fill(exp),
    )
    assert out["state"] == sr.STATE_READY_FOR_HUMAN_REVIEW
    assert out["blocking_issues"] == []
    assert out["review_required"] is True
    assert out["automatic_promotion"] is False
    assert out["rollback_reopen_plan"]["rollback"]["automatic"] is False


def test_fill_must_match_candidate_and_be_fully_closed():
    exp = _experiment()
    fill = _fill(exp)
    fill["candidate"] = {"label": "other", "condition": "z"}
    fill["closed_fills"] = 19
    raw = {k: v for k, v in fill.items() if k != "evidence_digest"}
    fill["evidence_digest"] = sr._digest(raw)
    out = sr.build_readiness(
        strategy_key="s",
        verification=_verification(),
        experiment=exp,
        actual_fill=fill,
    )
    assert "actual_shadow_fill_candidate_mismatch" in out["blocking_issues"]
    assert "actual_shadow_fill_not_fully_closed" in out["blocking_issues"]


def test_readiness_save_is_idempotent_and_append_only(tmp_path):
    exp = _experiment()
    out = sr.build_readiness(
        strategy_key="s", verification=_verification(),
        experiment=exp, actual_fill=_fill(exp),
    )
    path, created = sr.save_readiness(out, root=tmp_path)
    assert created is True
    assert sr.save_readiness(out, root=tmp_path) == (path, False)
    path.write_text("{}\n", encoding="utf-8")
    try:
        sr.save_readiness(out, root=tmp_path)
    except ValueError as exc:
        assert "不同证据" in str(exc)
    else:
        raise AssertionError("must reject conflicting readiness overwrite")


def test_fill_owner_and_scope_are_execution_owned():
    exp = _experiment()
    fill = _fill(exp)
    fill["source_owner"] = "research"
    fill["execution_scope"] = "shadow"
    raw = {k: v for k, v in fill.items() if k != "evidence_digest"}
    fill["evidence_digest"] = sr._digest(raw)
    out = sr.build_readiness(
        strategy_key="s", verification=_verification(), experiment=exp, actual_fill=fill,
    )
    assert "actual_shadow_fill_owner_invalid" in out["blocking_issues"]
    assert "actual_shadow_fill_scope_invalid" in out["blocking_issues"]


def test_readiness_load_list_rejects_tamper(tmp_path):
    exp = _experiment()
    out = sr.build_readiness(
        strategy_key="s", verification=_verification(), experiment=exp, actual_fill=_fill(exp),
    )
    path, _ = sr.save_readiness(out, root=tmp_path)
    assert sr.load_readiness(path) == out
    assert sr.list_readiness("s", root=tmp_path) == [out]
    path.write_text("{}\n", encoding="utf-8")
    assert sr.load_readiness(path) is None
    assert sr.list_readiness("s", root=tmp_path) == []
