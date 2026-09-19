"""Jev semantic evidence verifier.

This module answers one narrow question only: does the supplied evidence support
the supplied claim? It never retrieves facts, never changes trading logic, and
never treats model confidence as factual truth.

The assistant integration is shadow-only by default. Deterministic grounding
(number support / trading-advice scope) remains the first gate.
"""
from __future__ import annotations

import re
from typing import Iterable

from app.core.jev_client import evaluate

VERDICTS = {
    "supported": "The supplied evidence directly supports the claim without adding material facts.",
    "contradicted": "The supplied evidence directly conflicts with a material part of the claim.",
    "insufficient": "The evidence does not establish or refute the claim strongly enough.",
}

_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
_FACT_MARKER_RE = re.compile(
    r"(\d|已|将|预计|同比|环比|签署|公告|发布|公布|显示|称|表示|完成|获得|"
    r"中标|合作|增长|下降|上涨|下跌|达到|涉及|发生|没有|未|政策|数据|"
    r"订单|合同|利润|营收|产量|销量|持仓|涨停|跌停|资金)"
)


def select_verifiable_claims(
    text: str,
    *,
    max_claims: int = 4,
    max_claim_chars: int = 320,
) -> list[str]:
    """Deterministically extract a small set of factual-looking claims.

    This is intentionally conservative: explanatory/opinion-only sentences are
    skipped unless they contain a factual marker. It is not an LLM extraction
    step, so selecting claims itself costs no Jev tokens.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in _SPLIT_RE.split(str(text or "")):
        claim = re.sub(
            r"^\s*(?:(?:[-*•>#]+)\s*|(?:\d+[.)、])\s*)?",
            "",
            raw,
        ).strip()
        claim = re.sub(r"\s+", " ", claim)
        if len(claim) < 8 or not _FACT_MARKER_RE.search(claim):
            continue
        claim = claim[:max_claim_chars]
        if claim in seen:
            continue
        seen.add(claim)
        out.append(claim)
        if len(out) >= max(1, int(max_claims)):
            break
    return out


def compact_evidence(
    evidence_texts: Iterable[str],
    *,
    max_chars: int = 12_000,
    max_items: int = 12,
) -> list[str]:
    """Deduplicate and cap evidence before any external model call."""
    out: list[str] = []
    seen: set[str] = set()
    used = 0
    for raw in evidence_texts:
        text = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        remaining = max_chars - used
        if remaining <= 0:
            break
        text = text[:remaining]
        out.append(text)
        used += len(text)
        if len(out) >= max_items:
            break
    return out

def verify_claims(
    claims: list[str],
    evidence_texts: Iterable[str],
    *,
    purpose: str = "semantic_verifier",
    max_evidence_chars: int = 12_000,
) -> dict:
    """Verify bounded claims against caller-supplied evidence in one Jev request."""
    clean_claims = [str(c).strip() for c in claims if str(c).strip()]
    evidence = compact_evidence(evidence_texts, max_chars=max_evidence_chars)
    if not clean_claims:
        return {"ok": False, "skipped": True, "reason": "no_claims", "items": []}
    if not evidence:
        return {"ok": False, "skipped": True, "reason": "no_evidence", "items": []}

    questions: dict[str, dict] = {}
    for idx, _claim in enumerate(clean_claims):
        questions[f"claim_{idx}"] = {
            "type": "choice",
            "instructions": {
                "task": f"Judge whether claims[{idx}] is supported by the supplied evidence.",
                "rules": [
                    "Use only the supplied evidence; do not rely on world knowledge.",
                    "Supported means the evidence establishes the material claim, not merely a related topic.",
                    "Contradicted means a material part conflicts with the evidence.",
                    "If the evidence is partial, ambiguous, or silent, choose insufficient.",
                    "Do not judge whether the source itself is true; judge evidence-to-claim support only.",
                ],
            },
            "criteria": VERDICTS,
        }

    result = evaluate(
        {"claims": clean_claims, "evidence": evidence},
        questions,
        purpose=purpose,
    )
    if not result.get("ok"):
        return {
            "ok": False,
            "skipped": bool(result.get("skipped")),
            "reason": result.get("reason") or "jev_unavailable",
            "items": [],
            "latency_ms": result.get("latency_ms"),
        }

    answers = result.get("answers") or {}
    items: list[dict] = []
    for idx, claim in enumerate(clean_claims):
        answer = answers.get(f"claim_{idx}")
        if not isinstance(answer, dict):
            return {"ok": False, "reason": "invalid_answer_shape", "items": []}
        verdict = str(answer.get("choice") or "")
        confidence = answer.get("confidence")
        probabilities = answer.get("probabilities")
        if verdict not in VERDICTS or not isinstance(confidence, (int, float)):
            return {"ok": False, "reason": "invalid_answer_value", "items": []}
        if not isinstance(probabilities, dict) or set(probabilities) != set(VERDICTS):
            return {"ok": False, "reason": "invalid_probabilities", "items": []}
        try:
            probs = {k: float(probabilities[k]) for k in VERDICTS}
        except (TypeError, ValueError):
            return {"ok": False, "reason": "invalid_probabilities", "items": []}
        if (
            not 0.0 <= float(confidence) <= 1.0
            or any(not 0.0 <= value <= 1.0 for value in probs.values())
            or abs(sum(probs.values()) - 1.0) > 0.001
        ):
            return {"ok": False, "reason": "invalid_probabilities", "items": []}
        items.append(
            {
                "claim": claim,
                "verdict": verdict,
                "confidence": float(confidence),
                "probabilities": probs,
            }
        )

    counts = {name: 0 for name in VERDICTS}
    for item in items:
        counts[item["verdict"]] += 1
    return {
        "ok": True,
        "model": result.get("model"),
        "usage": result.get("usage") or {},
        "latency_ms": result.get("latency_ms"),
        "counts": counts,
        "items": items,
    }
