"""Offline process fixtures; no CLI, provider, network, or model starts."""
import json
import signal
import subprocess

import pytest

from app.services.tradingagents_native import transport_process as process


def stream(usage=None):
    return "\n".join(json.dumps(row) for row in [
        {"type": "thread.started", "thread_id": "fixture"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "not retained"}},
        {"type": "turn.completed", "usage": usage or {
            "input_tokens": 17, "cached_input_tokens": 7, "output_tokens": 3}},
    ])


def timed_out(monkeypatch, stdout, *, kill=False, gone=False):
    class Child:
        pid = 123456
        returncode = None
        calls = 0

        def communicate(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1 or (kill and self.calls == 2):
                raise subprocess.TimeoutExpired("fixture", kwargs.get("timeout"))
            self.returncode = -signal.SIGKILL if kill else -signal.SIGTERM
            return stdout, "fixture stderr is hash-only"

    child, signals = Child(), []
    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: child)
    def send(pid, sig):
        signals.append(sig)
        if gone:
            raise ProcessLookupError
    monkeypatch.setattr(process.os, "killpg", send)
    with pytest.raises(process.TransportError) as raised:
        process.launch(["offline-fixture"], "private prompt must not be retained", env={}, cwd=".", timeout=1)
    return raised.value, child, signals


def test_timeout_drains_and_preserves_only_verified_final_usage(monkeypatch):
    error, child, signals = timed_out(monkeypatch, stream())
    assert error.usage == {"input_tokens": 17, "cached_input_tokens": 7, "output_tokens": 3}
    assert error.diagnostic["kind"] == "timeout"
    assert error.diagnostic["timed_out"] and error.diagnostic["local_drain_complete"]
    assert error.diagnostic["exit_code"] == -signal.SIGTERM
    assert error.final_usage_verified and child.calls == 2 and signals == [signal.SIGTERM]
    saved = json.dumps(error.diagnostic)
    assert "private prompt" not in saved and "not retained" not in saved and "fixture stderr" not in saved


@pytest.mark.parametrize("usage", [
    {"input_tokens": 152010, "output_tokens": 288, "cached_input_tokens": 75776,
     "cache_write_input_tokens": 0, "reasoning_output_tokens": 77},
    {"input_tokens": 85219, "output_tokens": 1067, "cached_input_tokens": 0,
     "cache_write_input_tokens": 0, "reasoning_output_tokens": 0},
    {"input_tokens": 17, "output_tokens": 3, "cached_input_tokens": 7,
     "cache_creation_input_tokens": 2, "cache_write_input_tokens": 3},
])
def test_real_terminal_usage_contract_survives_timeout(monkeypatch, usage):
    # First two rows retain the numeric shapes observed in existing CLI LAB
    # metadata. The nonzero write row is a boundary fixture, not billing proof.
    error, _, _ = timed_out(monkeypatch, stream(usage))
    assert error.final_usage_verified and error.usage == usage


def test_invalid_terminal_usage_keeps_numeric_observation_without_text(monkeypatch):
    usage = {"input_tokens": 17, "output_tokens": 3, "cached_input_tokens": 18,
             "PRIVATE_CONTEXT_MUST_NOT_BE_SAVED": "PRIVATE_VALUE_MUST_NOT_BE_SAVED"}
    error, _, _ = timed_out(monkeypatch, stream(usage))
    assert error.usage is None and not error.final_usage_verified
    observed = error.usage_observation
    assert observed["numeric_fields"] == {"input_tokens": 17, "output_tokens": 3, "cached_input_tokens": 18}
    assert observed["projection_rejection"] == "unsupported_usage_field"
    assert observed["unrecognized_fields"][0]["value_type"] == "string"
    assert "PRIVATE_" not in json.dumps(error.diagnostic) + json.dumps(observed)


def test_observation_is_bounded_and_does_not_copy_unknown_field_names():
    usage = {"PRIVATE_KEY_" + str(n): "PRIVATE_VALUE_" + str(n) for n in range(100)}
    usage.update(input_tokens=17, output_tokens=3)
    projected, observed = process.project_usage_evidence(usage)
    assert projected is None and observed["projection_rejection"] == "usage_field_count_exceeds_bound"
    assert observed["numeric_fields"] == {"input_tokens": 17, "output_tokens": 3}
    assert observed["omitted_field_count"] > 0 and not observed["raw_complete"]
    assert (len(observed["field_types"]) + len(observed["unrecognized_fields"])
            + observed["omitted_field_count"] == observed["field_count"])
    assert len(observed["unrecognized_fields"]) <= 16
    assert "PRIVATE_" not in json.dumps(observed) and len(json.dumps(observed)) < 5000


def test_non_utf8_field_name_cannot_discard_terminal_accounting_observation():
    projected, observed = process.project_usage_evidence({"input_tokens": 17, "output_tokens": 3, "\ud800": 2})
    assert projected is None and observed["projection_rejection"] == "unsupported_usage_field"
    assert observed["numeric_fields"] == {"input_tokens": 17, "output_tokens": 3}
    assert len(json.dumps(observed, allow_nan=False)) < 5000


@pytest.mark.parametrize("usage,reason", [
    (None, "missing_usage"), ([], "nonobject_usage"),
    ({"output_tokens": 3}, "missing_required_token_count"),
    ({"input_tokens": "17", "output_tokens": 3}, "noninteger_token_count"),
    ({"input_tokens": 17, "output_tokens": True}, "noninteger_token_count"),
    ({"input_tokens": 17, "output_tokens": 1.5}, "noninteger_token_count"),
    ({"input_tokens": -1, "output_tokens": 3}, "token_count_out_of_range"),
    ({"input_tokens": 2**63, "output_tokens": 3}, "token_count_out_of_range"),
    ({"input_tokens": 17, "output_tokens": 3, "cache_write_input_tokens": 18}, "cache_tokens_exceed_input"),
    ({"input_tokens": 17, "output_tokens": 3, "cached_input_tokens": 7,
      "cache_creation_input_tokens": 7, "cache_write_input_tokens": 4}, "cache_tokens_exceed_input"),
    ({"input_tokens": 17, "output_tokens": 3, "reasoning_output_tokens": 4}, "reasoning_tokens_exceed_output"),
])
def test_projection_rejection_is_specific_and_never_repairs_usage(usage, reason):
    projected, observed = process.project_usage_evidence(usage)
    assert projected is None and observed["projection_rejection"] == reason
    assert len(json.dumps(observed, allow_nan=False)) < 5000


@pytest.mark.parametrize("kill,gone", [(True, False), (False, True)])
def test_timeout_kill_or_exit_race_still_drains(monkeypatch, kill, gone):
    error, child, signals = timed_out(monkeypatch, stream(), kill=kill, gone=gone)
    assert error.usage["output_tokens"] == 3 and error.diagnostic["local_drain_complete"]
    assert error.diagnostic["kill_requested"] is kill
    assert child.calls == (3 if kill else 2)
    assert signals == ([signal.SIGTERM, signal.SIGKILL] if kill else [signal.SIGTERM])


@pytest.mark.parametrize("body", [
    "", "plain prose", "[]", '{"type":"turn.started"}',
    stream().rsplit("\n", 1)[0], stream()[:-1], stream() + '\n{"type":',
    stream() + "\n" + stream(),
    stream().replace('"turn.started"', '"turn.completed"', 1),
    stream() + '\n{"type":"thread.started"}',
    stream().replace('"input_tokens": 17', '"input_tokens": 17, "input_tokens": 18'),
    stream().replace('"input_tokens": 17', '"input_tokens": NaN'),
    stream().replace('"input_tokens": 17', '"input_tokens": Infinity'),
    stream().replace('"input_tokens": 17', '"input_tokens": 1e999'),
    stream().replace('"thread.started"', '"thread.started", "nonfinite": 1e999'),
    stream().replace('"input_tokens": 17', '"input_tokens": true'),
    stream().replace('"input_tokens": 17', '"input_tokens": "17"'),
    stream().replace('"input_tokens": 17', '"input_tokens": -1'),
    stream().replace('"input_tokens": 17', '"input_tokens": 1.5'),
    stream({"input_tokens": 17, "output_tokens": 3, "cached_input_tokens": 18}),
    stream({"input_tokens": 17, "output_tokens": 3, "cached_input_tokens": 7, "cache_creation_input_tokens": 11}),
    stream({"input_tokens": 17, "output_tokens": 3, "reasoning_output_tokens": 4}),
    stream({"input_tokens": 2**63, "output_tokens": 3}),
    stream({"input_tokens": 17}),
    stream({"input_tokens": 17, "output_tokens": 3, "partial": True}),
    stream({"total_token_usage": {"input_tokens": 17, "output_tokens": 3}}),
    stream().replace('"type": "agent_message"', '"type": "error"'),
    stream().replace('"type": "agent_message"', '"type": "command_execution"'),
    stream().replace('"thread.started"', '"unknown.event"'),
    stream().replace('"thread.started"', '[]'),
    stream().replace('"turn.completed", "usage"', '"turn.completed", "error": {"message": "not retained"}, "usage"'),
    stream().replace('"type": "item.completed", "item": {"type": "agent_message", "text": "not retained"}',
                     '"type": "item.completed", "item": null'),
])
def test_unproven_final_usage_remains_unknown(monkeypatch, body):
    error, _, _ = timed_out(monkeypatch, body)
    assert error.usage is None and not error.final_usage_verified
    assert error.diagnostic["usage"] is None and error.diagnostic["timed_out"]


def test_partial_item_usage_is_not_final_usage(monkeypatch):
    body = json.dumps({"type": "turn.started"}) + "\n" + json.dumps({
        "type": "item.updated", "item": {"type": "reasoning", "usage": {"input_tokens": 17, "output_tokens": 3}}})
    error, _, _ = timed_out(monkeypatch, body)
    assert error.usage is None


@pytest.mark.parametrize("suffix", ["", '\n{"type":'])
@pytest.mark.parametrize("stop,kind", [
    ({"type": "error", "error": {"code": "authentication_failed"}}, "codex_auth_or_permission_stop"),
    ({"type": "turn.failed", "error": {"http_status": 403}}, "codex_auth_or_permission_stop"),
    ({"type": "error", "code": "content_filter"}, "codex_safety_refusal"),
    ({"type": "item.completed", "item": {"type": "agent_message", "refusal": "not retained"}}, "codex_safety_refusal"),
])
def test_typed_authorization_or_safety_stop_survives_timeout(monkeypatch, suffix, stop, kind):
    body = json.dumps(stop) + "\n" + stream() + suffix
    error, _, _ = timed_out(monkeypatch, body)
    assert error.diagnostic["kind"] == kind and error.diagnostic["timed_out"]
    assert error.usage is None and error.diagnostic["no_fallback"]


def test_explicit_stop_after_malformed_row_is_not_hidden(monkeypatch):
    body = '{"type":\n' + json.dumps({"type": "error", "http_status": 401})
    error, _, _ = timed_out(monkeypatch, body)
    assert error.diagnostic["kind"] == "codex_auth_or_permission_stop" and error.usage is None


def test_terminal_nested_auth_error_is_not_usage_evidence(monkeypatch):
    body = stream().replace('"turn.completed", "usage"',
                            '"turn.completed", "error": {"code": "authorization_failed"}, "usage"')
    error, _, _ = timed_out(monkeypatch, body)
    assert error.diagnostic["kind"] == "codex_auth_or_permission_stop" and error.usage is None


def test_stream_size_and_event_count_bounds(monkeypatch):
    monkeypatch.setattr(process, "MAX_TIMEOUT_BYTES", len(stream().encode()) + 1)
    error, _, _ = timed_out(monkeypatch, stream() + "\n" + " " * 10)
    assert error.usage is None and error.diagnostic["usage_evidence"] == "stream_exceeds_bound"
    monkeypatch.setattr(process, "MAX_TIMEOUT_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(process, "MAX_TIMEOUT_EVENTS", 2)
    error, _, _ = timed_out(monkeypatch, stream())
    assert error.usage is None and error.diagnostic["usage_evidence"] == "event_count_exceeds_bound"


def test_normal_launch_is_unchanged(monkeypatch):
    class Child:
        returncode = 0
        def communicate(self, *args, **kwargs):
            return stream(), "ordinary stderr"
    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: Child())
    monkeypatch.setattr(process.os, "killpg", lambda *args: pytest.fail("normal completion must not signal"))
    assert process.launch(["fixture"], "prompt", env={}, cwd=".") == (stream(), "ordinary stderr", 0)
