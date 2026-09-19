"""Research-only Jev quant labels stay bounded and non-executable."""
from __future__ import annotations

import pytest

import app.research.jev_shadow as js


def _fake_choice(choice: str, criteria: dict):
    return {
        "ok": True, "model": "jev-test",
        "answers": {"choice": {
            "type": "choice", "choice": choice, "confidence": 0.91,
            "probabilities": {k: (0.91 if k == choice else 0.09 / max(1, len(criteria) - 1)) for k in criteria},
        }},
        "usage": {"input_tokens": 20}, "latency_ms": 2,
    }


def test_pick_archetype_is_bounded_to_declared_taxonomy(monkeypatch):
    seen = {}
    def fake(state, questions, purpose):
        seen["criteria"] = questions["choice"]["criteria"]
        return _fake_choice("restart", seen["criteria"])
    monkeypatch.setattr(js, "evaluate", fake)
    out = js.classify_pick_archetype({"phase": "修复", "prior_wave": True})
    assert out["ok"] is True and out["choice"] == "restart"
    assert seen["criteria"] == js.PICK_ARCHETYPES


def test_single_candidate_tactic_uses_zero_model_calls(monkeypatch):
    monkeypatch.setattr(js, "evaluate", lambda *_a, **_k: pytest.fail("single candidate is deterministic"))
    out = js.choose_bounded_tactic({"signal": "low"}, {"hold": "保持观察"})
    assert out["choice"] == "hold"
    assert out["model"] == "deterministic_single_candidate"


def test_tactic_router_cannot_invent_action(monkeypatch):
    candidates = {"hold": "不动作", "low_absorb": "低吸候选，仅模拟/影子"}
    monkeypatch.setattr(
        js, "evaluate", lambda state, questions, purpose: _fake_choice("low_absorb", questions["choice"]["criteria"])
    )
    out = js.choose_bounded_tactic({"minute_score": -0.7}, candidates)
    assert out["choice"] in candidates and out["choice"] == "low_absorb"


def test_review_failure_uses_fixed_failure_taxonomy(monkeypatch):
    monkeypatch.setattr(
        js, "evaluate", lambda state, questions, purpose: _fake_choice("entry", questions["choice"]["criteria"])
    )
    out = js.classify_review_failure({"selected": True, "entry": "chased"})
    assert out["choice"] == "entry"
    assert set(js.REVIEW_FAILURES) >= {"selection", "entry", "data", "execution", "unknown"}
