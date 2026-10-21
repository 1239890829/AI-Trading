"""Offline terminal failures retain numeric evidence, never a paid retry."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.tradingagents_native import codex_transport as transport
from app.services.tradingagents_native.transport_process import TransportError


USAGE = {"input_tokens": 17, "output_tokens": 3, "cache_write_input_tokens": 0}
VALUE = {"content": "offline fixture", "tool_calls": []}


def stream(*, extra=(), text=None):
    rows = [{"type": "thread.started"}, {"type": "turn.started"}, *extra,
            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(VALUE) if text is None else text}},
            {"type": "turn.completed", "usage": USAGE}]
    return "\n".join(json.dumps(row) for row in rows)


@pytest.mark.parametrize("body,code,final,kind,stage", [
    (stream(extra=[{"type": "error", "error": {"code": "context_length_exceeded", "http_status": 400,
                                               "message": "PRIVATE_PROVIDER_MESSAGE"}}]), 1, json.dumps(VALUE),
     "codex_typed_terminal_failure", "terminal"),
    (stream() + '\n{"type":', 0, json.dumps(VALUE), "codex_event_stream_failure", "event_stream"),
    (stream(text="PRIVATE_NON_JSON_ANSWER"), 0, "PRIVATE_NON_JSON_ANSWER",
     "codex_final_schema_failure", "final_message_parse"),
    (stream(), 0, None, "codex_final_schema_failure", "final_message_missing"),
    (stream(), 0, "{}", "codex_final_schema_failure", "final_message_binding"),
])
def test_terminal_failure_preserves_observation_without_approving_usage(body, code, final, kind, stage):
    with pytest.raises(TransportError) as raised:
        transport.decode(body, "PRIVATE_STDERR", code, final)
    error = raised.value
    assert str(error) == kind and error.metadata["failure_stage"] == stage
    assert error.metadata["exit_code"] == code and error.metadata["turn_count"] == 1
    assert error.usage_observation["numeric_fields"] == USAGE
    assert error.usage is None and not error.final_usage_verified and error.metadata["no_fallback"]
    assert len(error.metadata["stdout_sha256"]) == 64 and len(error.metadata["stderr_sha256"]) == 64
    assert "PRIVATE_" not in json.dumps(error.metadata) + json.dumps(error.usage_observation)


def test_unknown_codes_and_event_names_are_hashes_only():
    body = stream(extra=[{"type": "error", "error": {"code": "PRIVATE_CODE", "http_status": 429}},
                         {"type": "PRIVATE_EVENT"},
                         {"type": "item.completed", "item": {"type": "PRIVATE_HOST_TOOL"}}])
    with pytest.raises(TransportError) as raised:
        transport.decode(body, "PRIVATE_STDERR", 7, json.dumps(VALUE))
    diagnostic = raised.value.metadata
    assert diagnostic["exit_code"] == 7 and diagnostic["http_status"] == 429
    for key in ("unknown_error_codes", "host_tool_types", "unknown_event_types"):
        assert diagnostic[key][0]["value_type"] == "string" and len(diagnostic[key][0]["sha256"]) == 64
    assert "PRIVATE_" not in json.dumps(diagnostic)


@pytest.mark.parametrize("code,kind", [("authentication_failed", "codex_auth_or_permission_stop"),
                                        ("content_filter", "codex_safety_refusal")])
def test_explicit_stop_survives_malformed_row_and_never_promotes_observed_usage(code, kind):
    body = '{"type":\n' + stream(extra=[{"type": "error", "error": {"code": code}}])
    with pytest.raises(TransportError, match=kind) as raised:
        transport.decode(body, "PRIVATE_STDERR", 1, json.dumps(VALUE))
    assert raised.value.metadata["no_fallback"]
    assert raised.value.usage_observation["numeric_fields"] == USAGE
    assert raised.value.usage is None and not raised.value.final_usage_verified


def test_success_preserves_sanitized_terminal_counts():
    result = transport.decode(stream(), "PRIVATE_STDERR", 0, json.dumps(VALUE))
    assert result["usage"] == USAGE
    diagnostic = result["transport_diagnostic"]
    assert diagnostic["exit_code"] == 0 and diagnostic["turn_count"] == diagnostic["turn_started_count"] == 1
    assert diagnostic["kind"] == "codex_typed_terminal_success"
    assert "PRIVATE_" not in json.dumps(diagnostic)


@pytest.mark.parametrize("rows", [
    [{"type": "turn.completed", "usage": USAGE}, {"type": "turn.started"},
     {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}}],
    [*map(json.loads, stream(text="{}").splitlines()),
     {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}}],
    [{"type": "item.updated", "item": {"type": "reasoning"}},
     *map(json.loads, stream(text="{}").splitlines())],
])
def test_normal_success_requires_one_ordered_final_completed_turn(rows):
    with pytest.raises(TransportError, match="typed_terminal_failure") as raised:
        transport.decode("\n".join(json.dumps(row) for row in rows), "", 0, "{}")
    error = raised.value
    assert error.metadata["failure_stage"] == "terminal_order" and error.metadata["no_fallback"]
    assert error.usage_observation["numeric_fields"] == USAGE
    assert error.usage is None and not error.final_usage_verified


def test_invalid_utf8_cannot_be_silently_removed_into_a_success():
    with pytest.raises(TransportError, match="event_stream_failure") as raised:
        transport.decode(stream() + "\ud800", "", 0, json.dumps(VALUE))
    assert raised.value.metadata["parse_failure"] == "invalid_encoding"
    assert raised.value.usage_observation["numeric_fields"] == USAGE
    assert not raised.value.final_usage_verified


def test_known_authorization_code_is_not_hidden_by_bounded_unknown_codes():
    extra = [{"type": "error", "code": "PRIVATE_CODE_" + str(n)} for n in range(20)]
    extra.append({"type": "error", "code": "authentication_failed"})
    with pytest.raises(TransportError, match="auth_or_permission") as raised:
        transport.decode(stream(extra=extra), "", 1, None)
    diagnostic = raised.value.metadata
    assert diagnostic["explicit_error_codes"] == ["authentication_failed"]
    assert len(diagnostic["unknown_error_codes"]) == 16 and diagnostic["unknown_error_codes_omitted"] == 4
    assert "PRIVATE_" not in json.dumps(diagnostic)


def test_sanitizer_rejects_malformed_diagnostics_without_discarding_safe_fields():
    diagnostic = transport.safe_diagnostic({"exit_code": 1, "turn_count": -1, "http_status": 999,
        "host_tool_types": [{"sha256": "a" * 64, "value_type": []}],
        "unknown_event_types": ["\ud800"], "context": "PRIVATE_CONTEXT"})
    assert diagnostic["exit_code"] == 1 and "turn_count" not in diagnostic and "http_status" not in diagnostic
    assert diagnostic["host_tool_types"][0]["value_type"] == "object"
    assert "PRIVATE_" not in json.dumps(diagnostic) and len(json.dumps(diagnostic)) < 5000


@pytest.mark.parametrize("failure", ["restore", "read"])
def test_infer_retains_schema_and_file_failures_before_temporary_cleanup(tmp_path, monkeypatch, failure):
    entry, auth = tmp_path / "cli", tmp_path / "auth"
    entry.write_bytes(b"native offline fixture")
    auth.write_text("PRIVATE_CREDENTIAL_REFERENCE")
    schema = {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}
    calls, locations = [], []
    monkeypatch.setattr(transport.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0,
        stdout="\n".join(flag + " stable false" for flag in transport.DISABLE)))
    def launch(args, prompt, **kwargs):
        calls.append(True)
        locations.append(Path(kwargs["cwd"]).parent)
        answer = Path(args[args.index("--output-last-message") + 1])
        if failure == "read":
            answer.write_bytes(b"\xff")
        else:
            answer.write_text(json.dumps(VALUE))
        return stream(), "PRIVATE_STDERR", 0
    monkeypatch.setattr(transport, "launch", launch)
    original = transport.TransportError
    class BeforeCleanup(original):
        def __init__(self, *args, **kwargs):
            assert locations[-1].is_dir(), "Evidence must exist before temp cleanup"
            super().__init__(*args, **kwargs)
    monkeypatch.setattr(transport, "TransportError", BeforeCleanup)
    with pytest.raises(TransportError) as raised:
        transport.codex_infer([{"role": "user", "content": "PRIVATE_CONTEXT"}], schema,
            model="fixture", mark_launch=lambda: None, executable=entry, auth_file=auth)
    error = raised.value
    assert len(calls) == 1 and not locations[0].exists()
    assert error.usage_observation["numeric_fields"] == USAGE and error.usage is None
    assert error.metadata["no_fallback"]
    assert error.metadata["failure_stage"] == ("original_schema_restore" if failure == "restore" else "final_file_read")
    assert "PRIVATE_" not in json.dumps(error.metadata) + json.dumps(error.usage_observation)
