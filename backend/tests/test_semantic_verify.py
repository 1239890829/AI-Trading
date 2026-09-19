"""Universal semantic verifier: bounded claim/evidence support only."""
from __future__ import annotations

import pytest

import app.core.semantic_verify as sv


def test_select_verifiable_claims_is_bounded_and_deduped():
    text = (
        "这是一些一般性说明。"
        "公司已签署重大算力服务合同。"
        "公司已签署重大算力服务合同。"
        "预计项目将在明年完成。"
        "我认为这值得关注。"
        "营收同比增长20%。"
    )
    out = sv.select_verifiable_claims(text, max_claims=2)
    assert out == ["公司已签署重大算力服务合同", "预计项目将在明年完成"]


def test_compact_evidence_dedupes_and_caps():
    out = sv.compact_evidence(["  A  ", "A", "B" * 20], max_chars=5, max_items=4)
    assert out == ["A", "BBBB"]


def test_verify_claims_skips_without_claims_or_evidence(monkeypatch):
    monkeypatch.setattr(sv, "evaluate", lambda *_a, **_k: pytest.fail("no network"))
    assert sv.verify_claims([], ["e"])["reason"] == "no_claims"
    assert sv.verify_claims(["c"], [])["reason"] == "no_evidence"


def test_verify_claims_builds_three_way_questions(monkeypatch):
    seen = {}

    def fake(state, questions, *, purpose):
        seen.update(state=state, questions=questions, purpose=purpose)
        return {
            "ok": True,
            "model": "jev-test",
            "usage": {"input_tokens": 33},
            "latency_ms": 5,
            "answers": {
                "claim_0": {
                    "type": "choice",
                    "choice": "supported",
                    "confidence": 0.91,
                    "probabilities": {
                        "supported": 0.91,
                        "contradicted": 0.02,
                        "insufficient": 0.07,
                    },
                },
                "claim_1": {
                    "type": "choice",
                    "choice": "insufficient",
                    "confidence": 0.77,
                    "probabilities": {
                        "supported": 0.12,
                        "contradicted": 0.11,
                        "insufficient": 0.77,
                    },
                },
            },
        }

    monkeypatch.setattr(sv, "evaluate", fake)
    out = sv.verify_claims(["合同已生效", "利润大幅增长"], ["公告：合同已生效"])
    assert out["ok"] is True
    assert out["counts"] == {"supported": 1, "contradicted": 0, "insufficient": 1}
    assert seen["purpose"] == "semantic_verifier"
    assert set(seen["questions"]) == {"claim_0", "claim_1"}
    assert seen["state"]["evidence"] == ["公告：合同已生效"]


def test_verify_claims_rejects_malformed_answer(monkeypatch):
    monkeypatch.setattr(
        sv,
        "evaluate",
        lambda *_a, **_k: {
            "ok": True,
            "answers": {"claim_0": {"choice": "maybe", "confidence": 0.9}},
        },
    )
    out = sv.verify_claims(["合同已生效"], ["合同已生效"])
    assert out["ok"] is False
    assert out["reason"] == "invalid_answer_value"



def test_verify_claims_rejects_probability_range_or_sum(monkeypatch):
    monkeypatch.setattr(
        sv,
        "evaluate",
        lambda *_a, **_k: {
            "ok": True,
            "answers": {
                "claim_0": {
                    "choice": "supported",
                    "confidence": 0.9,
                    "probabilities": {
                        "supported": 0.9,
                        "contradicted": 0.2,
                        "insufficient": 0.1,
                    },
                }
            },
        },
    )
    out = sv.verify_claims(["合同已生效"], ["合同已生效"])
    assert out["ok"] is False
    assert out["reason"] == "invalid_probabilities"
