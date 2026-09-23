"""TypeSafe/Jev 统一适配层：安全外发、typed response、失败降级与 telemetry。"""
from __future__ import annotations

import httpx
import pytest

import app.core.jev_client as jc
from app.core.config import settings


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    jc._reset_metrics_for_tests()
    monkeypatch.setattr(settings, "jev_enabled", True)
    monkeypatch.setattr(settings, "jev_base_url", "https://typesafe.test")
    monkeypatch.setattr(settings, "jev_model", "jev-test")
    monkeypatch.setattr(settings, "jev_timeout_seconds", 1.0)
    monkeypatch.setattr(settings, "jev_max_state_chars", 20_000)
    monkeypatch.setattr(settings, "agent_model_max_timeout_seconds", 180.0)
    monkeypatch.setattr(settings, "agent_model_max_retries", 2)
    monkeypatch.setattr(settings, "agent_model_max_input_chars", 250_000)
    monkeypatch.setattr(settings, "agent_model_max_output_chars", 150_000)
    monkeypatch.setattr(settings, "jev_usage_log_enabled", False)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)


def _questions():
    return {
        "route": {
            "type": "choice",
            "instructions": "Choose a route.",
            "criteria": {"a": "route a", "b": "route b"},
        }
    }


def _response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(
        status, json=body, request=httpx.Request("POST", "https://typesafe.test/v1/systemone")
    )
def test_missing_key_skips_without_network(monkeypatch):
    monkeypatch.setattr(jc, "_post", lambda *a, **k: pytest.fail("network must not run"))
    out = jc.evaluate({"x": 1}, _questions(), purpose="unit")
    assert out == {"ok": False, "skipped": True, "reason": "not_configured"}
    assert jc.metrics_snapshot()["skipped"] == 1


def test_success_returns_typed_response_and_records_usage(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    seen = {}

    def fake(url, key, payload, timeout):
        seen.update(url=url, key=key, payload=payload, timeout=timeout)
        return _response(200, {
            "model": "jev-test",
            "answers": {"route": {
                "type": "choice", "choice": "a", "confidence": 0.9,
                "probabilities": {"a": 0.9, "b": 0.1},
            }},
            "usage": {"input_tokens": 123, "output_tokens": 7},
        })

    monkeypatch.setattr(jc, "_post", fake)
    out = jc.evaluate({"ticket": "hello"}, _questions(), purpose="routing")
    assert out["ok"] is True and out["answers"]["route"]["choice"] == "a"
    assert seen["url"] == "https://typesafe.test/v1/systemone"
    assert seen["key"] == "test-only-key"
    metrics = jc.metrics_snapshot()
    assert metrics["ok"] == 1 and metrics["input_tokens"] == 123
    assert metrics["purposes"]["routing"]["calls"] == 1


@pytest.mark.parametrize("state", [
    {"api_key": "not-even-a-real-key"},
    {"nested": {"authorization": "Bearer x"}},
    {"text": "credential " + "apikey_" + ("a" * 40)},
])
def test_sensitive_state_is_rejected_before_network(monkeypatch, state):
    monkeypatch.setenv("JEV_API_KEY", "test-only-key")
    monkeypatch.setattr(jc, "_post", lambda *a, **k: pytest.fail("network must not run"))
    out = jc.evaluate(state, _questions(), purpose="security")
    assert out["ok"] is False and out["skipped"] is True
    assert "sensitive" in out["reason"] or "secret_like" in out["reason"]
def test_state_size_cap_is_fail_closed(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    monkeypatch.setattr(settings, "jev_max_state_chars", 20)
    monkeypatch.setattr(jc, "_post", lambda *a, **k: pytest.fail("network must not run"))
    out = jc.evaluate({"text": "x" * 100}, _questions(), purpose="size")
    assert out["reason"].startswith("state_too_large:")
    assert out["skipped"] is True


def test_transient_status_retries_then_succeeds(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    calls = iter([
        _response(429, {"error": "rate"}),
        _response(503, {"error": "busy"}),
        _response(200, {
            "model": "jev-test",
            "answers": {"route": {
                "type": "choice", "choice": "b", "confidence": 0.8,
                "probabilities": {"a": 0.2, "b": 0.8},
            }},
            "usage": {"input_tokens": 20},
        }),
    ])
    count = {"n": 0}

    def fake(*_a, **_k):
        count["n"] += 1
        return next(calls)

    monkeypatch.setattr(jc, "_post", fake)
    monkeypatch.setattr(jc.time, "sleep", lambda _s: None)
    out = jc.evaluate({"x": 1}, _questions(), purpose="retry")
    assert out["ok"] is True and out["answers"]["route"]["choice"] == "b"
    assert count["n"] == 3 and out["attempts"] == 3


def test_answer_shape_mismatch_fails_without_response_body(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    monkeypatch.setattr(jc, "_post", lambda *_a, **_k: _response(
        200, {"answers": {"other": {"type": "noul", "noul": 0.5}}}
    ))
    out = jc.evaluate({"x": 1}, _questions(), purpose="shape")
    assert out["ok"] is False
    assert out["reason"] == "answer_shape_mismatch"
    assert "other" not in str(out)


def test_http_failure_exposes_status_only(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    monkeypatch.setattr(jc, "_post", lambda *_a, **_k: _response(
        401, {"error": "echo secret text here"}
    ))
    out = jc.evaluate({"x": 1}, _questions(), purpose="http")
    assert out["ok"] is False and out["reason"] == "http_401"
    assert "secret text" not in str(out)



def test_comparison_metrics_store_no_payload():
    jc.record_comparison("triage", "notify", "notify", confidence=0.8)
    jc.record_comparison("triage", "ignore", "notify", confidence=0.6)
    row = jc.metrics_snapshot()["comparisons"]["triage"]
    assert row["total"] == 2 and row["agree"] == 1 and row["disagree"] == 1
    assert row["avg_confidence"] == 0.7


def test_status_snapshot_discloses_presence_not_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "super-secret-test-value")
    snap = jc.status_snapshot()
    assert snap["state"] == "ready"
    assert snap["model"] == "jev-test"
    assert "super-secret-test-value" not in str(snap)
    assert "api_key" not in str(snap).lower()


def test_system_providers_exposes_zero_cost_jev_status(client):
    body = client.get("/api/system/providers").json()
    assert "jev" in body
    assert body["jev"]["state"] in {"disabled", "not_configured", "ready"}
    assert "metrics" in body["jev"]
    text = str(body["jev"]).lower()
    assert "api_key" not in text and "authorization" not in text



def test_usage_receipt_persists_metadata_not_model_text(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(settings, "jev_usage_log_enabled", True)
    monkeypatch.setattr(settings, "jev_usage_log_path", str(path))

    monkeypatch.setattr(jc, "_post", lambda *_a, **_k: _response(
        200,
        {
            "model": "jev-test-actual",
            "answers": {"route": {
                "type": "choice", "choice": "a", "confidence": 0.9,
                "probabilities": {"a": 0.9, "b": 0.1},
            }},
            "usage": {"input_tokens": 321, "output_tokens": 9},
        },
    ))
    secret_context = "THIS_CONTEXT_MUST_NEVER_APPEAR_IN_USAGE_RECEIPT"
    out = jc.evaluate({"text": secret_context}, _questions(), purpose="receipt-test")
    assert out["ok"] is True

    line = path.read_text()
    assert secret_context not in line
    assert "Choose a route" not in line
    row = __import__("json").loads(line)
    assert row["purpose"] == "receipt-test"
    assert row["status"] == "ok"
    assert row["model"] == "jev-test-actual"
    assert row["input_tokens"] == 321 and row["output_tokens"] == 9


def test_usage_receipt_failure_never_breaks_jev_result(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    monkeypatch.setattr(settings, "jev_usage_log_enabled", True)
    # A directory path cannot be opened as an append-only file.
    monkeypatch.setattr(settings, "jev_usage_log_path", str(tmp_path))
    monkeypatch.setattr(jc, "_post", lambda *_a, **_k: _response(
        200,
        {
            "model": "jev-test",
            "answers": {"route": {
                "type": "choice", "choice": "b", "confidence": 0.8,
                "probabilities": {"a": 0.2, "b": 0.8},
            }},
            "usage": {"input_tokens": 10},
        },
    ))
    out = jc.evaluate({"x": 1}, _questions(), purpose="receipt-io-failure")
    assert out["ok"] is True and out["answers"]["route"]["choice"] == "b"


def test_retry_budget_can_disable_jev_retries(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    monkeypatch.setattr(settings, "agent_model_max_retries", 0)
    count = {"n": 0}
    def fake(*_a, **_k):
        count["n"] += 1
        return _response(503, {"error": "busy"})
    monkeypatch.setattr(jc, "_post", fake)
    out = jc.evaluate({"x": 1}, _questions(), purpose="retry-budget")
    assert out["ok"] is False and out["attempts"] == 1 and count["n"] == 1


def test_jev_timeout_above_agent_policy_fails_before_network(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    monkeypatch.setattr(settings, "jev_timeout_seconds", 181.0)
    monkeypatch.setattr(settings, "agent_model_max_timeout_seconds", 180.0)
    monkeypatch.setattr(jc, "_post", lambda *a, **k: pytest.fail("network must not run"))
    out = jc.evaluate({"x": 1}, _questions(), purpose="timeout-budget")
    assert out["skipped"] is True and out["reason"].startswith("timeout_budget_exceeded:")


def test_durable_usage_sink_receives_metadata_only(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    monkeypatch.setattr(jc, "_post", lambda *_a, **_k: _response(
        200,
        {
            "model": "jev-sink",
            "answers": {"route": {
                "type": "choice", "choice": "a", "confidence": 0.9,
                "probabilities": {"a": 0.9, "b": 0.1},
            }},
            "usage": {"input_tokens": 31, "output_tokens": 4},
        },
    ))
    seen = []
    jc.set_usage_sink(seen.append)
    try:
        secret_context = "SINK_MUST_NOT_CONTAIN_THIS_CONTEXT"
        out = jc.evaluate({"text": secret_context}, _questions(), purpose="sink-test")
        assert out["ok"] is True
        assert len(seen) == 1
        row = seen[0]
        assert row["purpose"] == "sink-test"
        assert row["status"] == "ok" and row["model"] == "jev-sink"
        assert row["usage"] == {"input_tokens": 31, "output_tokens": 4}
        assert row["attempts"] == 1 and row["input_chars"] > 0
        body = str(row)
        assert secret_context not in body and "Choose a route" not in body
    finally:
        jc.set_usage_sink(None)


def test_global_usage_sink_receives_exactly_one_metadata_receipt(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    seen = []
    jc.set_usage_sink(seen.append)
    monkeypatch.setattr(jc, "_post", lambda *_a, **_k: _response(
        200, {
            "model": "jev-test-actual",
            "answers": {"route": {
                "type": "choice", "choice": "a", "confidence": 0.9,
                "probabilities": {"a": 0.9, "b": 0.1},
            }},
            "usage": {"input_tokens": 12, "output_tokens": 3},
        },
    ))
    out = jc.evaluate({"ticket": "hello"}, _questions(), purpose="sink-test")
    assert out["ok"] is True
    assert len(seen) == 1
    receipt = seen[0]
    assert receipt["purpose"] == "sink-test" and receipt["status"] == "ok"
    assert receipt["usage"] == {"input_tokens": 12, "output_tokens": 3}
    assert receipt["attempts"] == 1 and receipt["input_chars"] > 0
    assert "ticket" not in str(receipt) and "hello" not in str(receipt)


def test_global_usage_sink_failure_invalidates_successful_jev_result(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    def broken(_receipt):
        raise RuntimeError("db unavailable")
    jc.set_usage_sink(broken)
    monkeypatch.setattr(jc, "_post", lambda *_a, **_k: _response(
        200, {
            "model": "jev-test",
            "answers": {"route": {
                "type": "choice", "choice": "b", "confidence": 0.8,
                "probabilities": {"a": 0.2, "b": 0.8},
            }},
            "usage": {"input_tokens": 10, "output_tokens": 2},
        },
    ))
    out = jc.evaluate({"x": 1}, _questions(), purpose="sink-fail")
    assert out["ok"] is False
    assert out["reason"] == "usage_accounting_failed"


def test_jev_agent_input_budget_blocks_before_network(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    monkeypatch.setattr(settings, "jev_max_state_chars", 100_000)
    monkeypatch.setattr(settings, "agent_model_max_input_chars", 20)
    monkeypatch.setattr(jc, "_post", lambda *a, **k: pytest.fail("input budget must block before network"))
    out = jc.evaluate({"text": "x" * 30}, _questions(), purpose="input-budget")
    assert out["skipped"] is True and out["reason"].startswith("input_budget_exceeded:")


def test_jev_agent_output_budget_rejects_response_before_adoption(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    monkeypatch.setattr(settings, "agent_model_max_output_chars", 10)
    monkeypatch.setattr(jc, "_post", lambda *_a, **_k: _response(
        200, {
            "model": "jev-test",
            "answers": {"route": {
                "type": "choice", "choice": "a", "confidence": 0.9,
                "probabilities": {"a": 0.9, "b": 0.1},
            }},
            "usage": {"input_tokens": 2, "output_tokens": 1},
            "padding": "x" * 100,
        },
    ))
    out = jc.evaluate({"x": 1}, _questions(), purpose="output-budget")
    assert out["ok"] is False and out["reason"].startswith("output_budget_exceeded:")



def test_runtime_decision_trace_keeps_typed_output_only(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    monkeypatch.setattr(jc, "_post", lambda *_a, **_k: _response(
        200,
        {
            "model": "jev-trace",
            "answers": {"route": {
                "type": "choice", "choice": "a", "confidence": 0.91,
                "probabilities": {"a": 0.91, "b": 0.09},
            }},
            "usage": {"input_tokens": 12, "output_tokens": 3},
        },
    ))
    private_input = "THIS_STATE_TEXT_MUST_NOT_ENTER_TRACE"
    out = jc.evaluate({"text": private_input}, _questions(), purpose="trace-test")
    assert out["ok"] is True

    traces = jc.recent_decision_traces(limit=1)
    assert len(traces) == 1
    trace = traces[0]
    assert trace["purpose"] == "trace-test"
    assert trace["status"] == "ok"
    assert trace["model"] == "jev-trace"
    assert trace["answers"]["route"]["choice"] == "a"
    assert trace["answers"]["route"]["probabilities"]["a"] == pytest.approx(0.91)
    body = str(trace)
    assert private_input not in body
    assert "Choose a route" not in body
    assert "route a" not in body


def test_runtime_decision_trace_records_skip_without_payload(monkeypatch):
    monkeypatch.setattr(jc, "_post", lambda *a, **k: pytest.fail("network must not run"))
    out = jc.evaluate({"text": "NO_TRACE_PAYLOAD"}, _questions(), purpose="trace-skip")
    assert out["skipped"] is True

    trace = jc.recent_decision_traces(limit=1)[0]
    assert trace["purpose"] == "trace-skip"
    assert trace["status"] == "skipped"
    assert trace["reason"] == "not_configured"
    assert trace["answers"] == {}
    assert "NO_TRACE_PAYLOAD" not in str(trace)


def test_agent_jev_overview_is_read_only_and_path_redacted(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jev_usage_log_path", str(tmp_path / "private" / "usage.jsonl"))
    body = client.get("/api/agent/jev/overview?limit=5").json()["data"]
    assert body["trace_scope"] == "runtime_only"
    assert "status" in body and "historical_usage" in body and "recent_decisions" in body
    assert "path" not in body["historical_usage"]
    assert "state、用户正文" in body["privacy"]
    assert "assistant_tools" in body["fallbacks"]
