"""Official Decisions transport; no tools, generated prose, retries or ambient proxy.

Contract checked against the official Decisions create reference on 2026-10-30.
The caller owns evidence selection, durable usage and publication. A usage callback
runs before answer validation so a malformed answer cannot erase known usage.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from time import monotonic
from urllib.parse import urlsplit

import httpx

from app.core.config import DECISIONS_RESEARCH_MODEL

ENDPOINT = "https://api.openai.com/v1/decisions"
MODEL = DECISIONS_RESEARCH_MODEL
MAX_RESPONSE_BYTES = 512 * 1024
CHOICES = ("supported", "contradicted", "insufficient", "unrelated")


class DecisionsError(RuntimeError):
    """Public errors contain no response body, request, credentials or proxy URL."""

    def __init__(self, code: str):
        self.code = code
        super().__init__("Decisions research: " + code)


def validate_connection(api_key: str, proxy: str, timeout: float) -> None:
    if (not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 1024
            or any(ord(c) < 33 for c in api_key)):
        raise DecisionsError("not_configured")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
        raise DecisionsError("invalid_timeout")
    if not isinstance(proxy, str):
        raise DecisionsError("invalid_proxy")
    if proxy:
        try:
            value = urlsplit(proxy)
            if (value.scheme not in {"http", "https"} or not value.hostname
                    or value.path not in {"", "/"} or value.query or value.fragment
                    or any(ord(c) < 33 for c in proxy)):
                raise ValueError
            _ = value.port
        except ValueError:
            raise DecisionsError("invalid_proxy") from None


def validated_usage(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    try:
        fields = {key: value[key] for key in ("input_tokens", "output_tokens", "total_tokens")}
        inp = value["input_tokens_details"]
        out = value["output_tokens_details"]
        details = {"cached_tokens": inp["cached_tokens"], "cache_write_tokens": inp["cache_write_tokens"]}
        reasoning = out["reasoning_tokens"]
        if any(type(v) is not int or v < 0 for v in [*fields.values(), *details.values(), reasoning]):
            return None
        if fields["total_tokens"] != fields["input_tokens"] + fields["output_tokens"]:
            return None
        if max(details.values()) > fields["input_tokens"] or reasoning > fields["output_tokens"]:
            return None
        return {**fields, "input_tokens_details": details,
                "output_tokens_details": {"reasoning_tokens": reasoning}}
    except (KeyError, TypeError):
        return None


def _probability(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise DecisionsError("invalid_answer")
    return float(value)


def parse_answers(value: dict, names: list[str]) -> list[dict]:
    if not isinstance(value, dict) or value.get("model") != MODEL:
        raise DecisionsError("model_mismatch")
    answers = value.get("answers")
    if not isinstance(answers, list) or len(answers) != len(names):
        raise DecisionsError("answer_coverage_mismatch")
    result = []
    for name, answer in zip(names, answers):
        if not isinstance(answer, dict) or answer.get("name") != name:
            raise DecisionsError("answer_identity_mismatch")
        if answer.get("type") == "refusal":
            result.append({"name": name, "type": "refusal"})
            continue
        if answer.get("type") != "choice" or answer.get("choice") not in CHOICES:
            raise DecisionsError("invalid_answer")
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, list) or len(probabilities) != len(CHOICES):
            raise DecisionsError("invalid_probabilities")
        seen = {}
        for entry in probabilities:
            if not isinstance(entry, dict) or entry.get("value") not in CHOICES or entry["value"] in seen:
                raise DecisionsError("invalid_probabilities")
            seen[entry["value"]] = _probability(entry.get("probability"))
        if abs(sum(seen.values()) - 1) > 0.001:
            raise DecisionsError("invalid_probabilities")
        result.append({"name": name, "type": "choice", "choice": answer["choice"],
                       "confidence": _probability(answer.get("confidence")),
                       "probabilities": [{"value": option, "probability": seen[option]} for option in CHOICES]})
    return result


def create_decisions(request: dict, *, api_key: str, proxy: str = "", timeout: float = 30,
                     usage_callback=None) -> dict:
    """One bounded official request. No network occurs before request validation."""
    validate_connection(api_key, proxy, timeout)
    if not isinstance(request, dict) or request.get("model") != MODEL or set(request) != {"model", "input", "questions"}:
        raise DecisionsError("invalid_request")
    questions = request["questions"]
    if not isinstance(questions, list) or not 1 <= len(questions) <= 12:
        raise DecisionsError("invalid_request")
    names = []
    for question in questions:
        if (not isinstance(question, dict) or question.get("type") != "choice"
                or not isinstance(question.get("name"), str) or not question["name"]
                or question["name"] in names or not isinstance(question.get("instructions"), str)
                or [c.get("value") for c in question.get("choices", []) if isinstance(c, dict)] != list(CHOICES)):
            raise DecisionsError("invalid_request")
        names.append(question["name"])
    started = monotonic()

    async def fetch():
        # One event loop owns this request inside the caller's drained worker.
        # A wall deadline also covers slow chunked responses; per-read HTTP
        # timeouts alone would restart for each received chunk.
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout, proxy=proxy or None, trust_env=False,
                                         follow_redirects=False, verify=True) as session:
                async with session.stream("POST", ENDPOINT, headers={"Authorization": "Bearer " + api_key,
                                          "Content-Type": "application/json"}, json=request) as response:
                    if response.status_code != 200:
                        raise DecisionsError("http_" + str(response.status_code))
                    content = bytearray()
                    async for part in response.aiter_bytes():
                        content.extend(part)
                        if len(content) > MAX_RESPONSE_BYTES:
                            raise DecisionsError("response_too_large")
                    return content

    try:
        content = asyncio.run(fetch())
    except (httpx.TimeoutException, TimeoutError):
        raise DecisionsError("timeout") from None
    except (httpx.HTTPError, OSError):
        raise DecisionsError("transport_failed") from None
    try:
        value = json.loads(content)
    except (ValueError, UnicodeError):
        raise DecisionsError("invalid_json") from None
    usage = validated_usage(value.get("usage") if isinstance(value, dict) else None)
    if usage_callback is not None:
        usage_callback(usage)
    if usage is None:
        raise DecisionsError("usage_unknown")
    answers = parse_answers(value, names)
    return {"model": MODEL, "answers": answers, "usage": usage,
            "response_sha256": hashlib.sha256(content).hexdigest(),
            "elapsed_seconds": round(monotonic() - started, 6)}
