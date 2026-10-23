"""Offline terminal failures retain numeric evidence, never a paid retry."""
import hashlib
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


def offline_infer(tmp_path, monkeypatch, *, version='codex-cli 0.162.1', version_code=0, body=None, schema_override=None):
    entry, auth = tmp_path / 'cli', tmp_path / 'auth'
    entry.write_bytes(b'native offline fixture'); auth.write_text('PRIVATE_CREDENTIAL_REFERENCE')
    calls, marks = [], []
    def preflight(args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs['env']['CODEX_HOME'] != str(tmp_path) and kwargs['timeout'] == 15
        if args[-1] == '--version':
            return SimpleNamespace(returncode=version_code, stdout=version, stderr='PRIVATE_STDERR')
        return SimpleNamespace(returncode=0, stdout='\n'.join(flag + ' stable false' for flag in transport.DISABLE), stderr='')
    def launch(args, prompt, **kwargs):
        Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps(VALUE))
        return body if body is not None else stream(), '', 0
    monkeypatch.setattr(transport.subprocess, 'run', preflight)
    monkeypatch.setattr(transport, 'launch', launch)
    schema={'type':'object','properties':{'content':{'type':'string'},'tool_calls':{'type':'array','items':{'type':'object','properties':{}}}},'required':['content','tool_calls']}
    def invoke():
        return transport.codex_infer([{'role':'user','content':'PRIVATE_REQUEST'}], schema_override or schema,
            model='fixture', mark_launch=lambda: marks.append(True), executable=entry, auth_file=auth)
    return invoke, calls, marks


def test_verified_0162_retry_error_and_final_success_complete_without_paid_retry(tmp_path, monkeypatch):
    body = stream(extra=[{'type':'error','message':'PRIVATE_TRANSIENT_ERROR'}])
    invoke, calls, marks = offline_infer(tmp_path, monkeypatch, body=body)
    result = invoke()
    assert result['structured_output'] == VALUE and result['usage'] == USAGE and marks == [True]
    assert calls[0][0][-1] == '--version' and calls[1][0][1:3] == ['features','list']
    diagnostic=result['transport_diagnostic']
    assert diagnostic['typed_error_count'] == 1 and diagnostic['recovered_error_count'] == 1
    assert diagnostic['error_contract_version'] == '0.162.1' and diagnostic['error_disposition'] == 'recovered_under_verified_cli'
    assert transport.safe_diagnostic(diagnostic) == diagnostic
    assert 'PRIVATE_' not in json.dumps(diagnostic)
    assert result['internal_retries'] is None and result['provider_request_count'] is None


@pytest.mark.parametrize('version,code',[('codex-cli 0.162.2',0),('unverified wrapper',0),('codex-cli 0.162.1',1)])
def test_cli_contract_version_drift_blocks_before_mark_launch(tmp_path,monkeypatch,version,code):
    invoke,calls,marks=offline_infer(tmp_path,monkeypatch,version=version,version_code=code)
    with pytest.raises(ValueError,match='version'): invoke()
    assert marks == [] and len(calls) == 1 and calls[0][0][-1] == '--version'


@pytest.mark.parametrize('event',[
    {'type':'error','message':'failed to serialize exec json event: PRIVATE_DETAIL'},
    {'type':'error','message':'PRIVATE_MESSAGE','unexpected':'PRIVATE_FIELD'},
    {'type':'error','message':''}, {'type':'error','message':None}, {'type':'error','message':'\ud800'},
])
def test_successful_turn_does_not_waive_serialization_or_untyped_error(event):
    with pytest.raises(TransportError) as raised:
        transport.decode(stream(extra=[event]),'',0,json.dumps(VALUE),verified_cli_version='0.162.1')
    assert raised.value.usage is None and not raised.value.final_usage_verified and raised.value.metadata['no_fallback']
    assert 'PRIVATE_' not in json.dumps(raised.value.metadata)


def test_unverified_decode_still_refuses_an_error_with_successful_turn():
    with pytest.raises(TransportError):
        transport.decode(stream(extra=[{'type':'error','message':'PRIVATE_TRANSIENT'}]),'',0,json.dumps(VALUE))


@pytest.mark.parametrize('case',['exit_nonzero','turn_failed','auth','safety','refusal','host','unknown','final_binding','final_parse','before_turn','after_completed'])
def test_verified_retry_contract_keeps_every_failure_and_unknown_boundary(case):
    extra=[{'type':'error','message':'PRIVATE_TRANSIENT'}]; code=0; final=json.dumps(VALUE); text=None
    if case=='exit_nonzero': code=1
    elif case=='turn_failed': extra.append({'type':'turn.failed','error':{'message':'PRIVATE_FATAL'}})
    elif case=='auth': extra[0]['http_status']=403
    elif case=='safety': extra[0]['code']='content_filter'
    elif case=='refusal': extra.append({'type':'item.completed','item':{'type':'agent_message','refusal':True}})
    elif case=='host': extra.append({'type':'item.completed','item':{'type':'command_execution'}})
    elif case=='unknown': extra.append({'type':'PRIVATE_EVENT'})
    elif case=='final_binding': final='{}'
    elif case=='final_parse': text='PRIVATE_NON_JSON'; final=text
    body=stream(extra=extra,text=text)
    if case in {'before_turn','after_completed'}:
        rows=list(map(json.loads,stream().splitlines())); rows.insert(1 if case=='before_turn' else len(rows),extra[0]); body='\n'.join(map(json.dumps,rows))
    with pytest.raises(TransportError) as raised:
        transport.decode(body,'',code,final,verified_cli_version='0.162.1')
    error=raised.value
    assert error.metadata['no_fallback'] and error.usage_observation['numeric_fields']==USAGE
    if case in {'final_binding', 'final_parse'}:
        assert error.usage == USAGE and error.final_usage_verified
        assert error.metadata['recovered_error_count'] == 1
    else:
        assert error.usage is None and not error.final_usage_verified and 'recovered_error_count' not in error.metadata
    assert 'PRIVATE_' not in json.dumps(error.metadata)


def test_version_probe_timeout_never_marks_a_model_launch(tmp_path,monkeypatch):
    invoke,_,marks=offline_infer(tmp_path,monkeypatch)
    def timeout(*args,**kwargs): raise transport.subprocess.TimeoutExpired('PRIVATE_COMMAND',15)
    monkeypatch.setattr(transport.subprocess,'run',timeout)
    with pytest.raises(ValueError,match='version') as error: invoke()
    assert not marks and 'PRIVATE_' not in str(error.value)


def test_retry_error_does_not_waive_original_schema_restoration(tmp_path,monkeypatch):
    schema={'type':'object','properties':{'content':{'type':'string'}},'required':['content']}
    invoke,_,marks=offline_infer(tmp_path,monkeypatch,body=stream(extra=[{'type':'error','message':'PRIVATE_TRANSIENT'}]),schema_override=schema)
    with pytest.raises(TransportError,match='original_schema_failure') as raised: invoke()
    assert marks==[True] and raised.value.usage == USAGE and raised.value.final_usage_verified
    assert raised.value.usage_observation['numeric_fields']==USAGE and raised.value.metadata['no_fallback']


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
    monkeypatch.setattr(transport.subprocess, "run", lambda args, **k: SimpleNamespace(returncode=0,
        stdout='codex-cli 0.162.1' if args[-1] == '--version' else "\n".join(flag + " stable false" for flag in transport.DISABLE)))
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
    assert error.usage_observation["numeric_fields"] == USAGE and error.usage == USAGE and error.final_usage_verified
    assert error.metadata["no_fallback"]
    assert error.metadata["failure_stage"] == ("original_schema_restore" if failure == "restore" else "final_file_read")
    assert "PRIVATE_" not in json.dumps(error.metadata) + json.dumps(error.usage_observation)


def startup_stream(*, warning_type='item.completed', item_type='error'):
    rows = list(map(json.loads, stream().splitlines()))
    rows[1:1] = [{"type": warning_type, "item": {"type": item_type, "message": "PRIVATE_STARTUP_WARNING"}} for _ in range(2)]
    return "\n".join(map(json.dumps, rows))


def test_official_0162_startup_warnings_are_nonfatal_before_turn_started():
    result = transport.decode(startup_stream(), "", 0, json.dumps(VALUE))
    assert result["wire_structured_output"] == VALUE and result["usage"] == USAGE
    assert result["codex_terminal"]["warning_count"] == 2
    diagnostic = result["transport_diagnostic"]
    assert diagnostic["event_order"] == [
        {"event_type": "thread.started", "item_type": "none"},
        {"event_type": "item.completed", "item_type": "error"},
        {"event_type": "item.completed", "item_type": "error"},
        {"event_type": "turn.started", "item_type": "none"},
        {"event_type": "item.completed", "item_type": "agent_message"},
        {"event_type": "turn.completed", "item_type": "none"},
    ]
    assert diagnostic["event_order_omitted"] == 0 and diagnostic["event_order_complete"] is True
    assert transport.safe_diagnostic(diagnostic) == diagnostic
    assert "PRIVATE_" not in json.dumps(diagnostic)


@pytest.mark.parametrize('warning_type,item_type', [
    ('item.started', 'error'), ('item.updated', 'error'),
    ('item.completed', 'agent_message'), ('item.completed', 'reasoning'),
    ('item.completed', 'command_execution'),
])
def test_startup_exception_never_allows_turn_external_work(warning_type, item_type):
    with pytest.raises(TransportError) as raised:
        transport.decode(startup_stream(warning_type=warning_type, item_type=item_type), "", 0, json.dumps(VALUE))
    diagnostic = raised.value.metadata
    assert diagnostic['no_fallback'] and not raised.value.final_usage_verified
    if item_type == 'command_execution': assert diagnostic['failure_stage'] == 'terminal'
    else: assert diagnostic['order_violation'] == 'invalid_startup_event'


@pytest.mark.parametrize('trailing', [
    {"type": "item.completed", "item": {"type": "error", "message": "PRIVATE_TRAILING_WARNING"}},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
    {"type": "item.updated", "item": {"type": "reasoning"}}, {"type": "thread.started"},
])
def test_no_event_can_follow_terminal_completed_even_if_a_warning(trailing):
    with pytest.raises(TransportError) as raised:
        transport.decode(startup_stream()+"\n"+json.dumps(trailing), "", 0, json.dumps(VALUE))
    diagnostic = raised.value.metadata
    assert diagnostic['failure_stage'] == 'terminal_order' and diagnostic['order_violation'] == 'completed_not_last'
    assert diagnostic['no_fallback'] and not raised.value.final_usage_verified
    assert transport.safe_diagnostic(diagnostic) == diagnostic


def test_reversed_turn_is_distinguishable_without_raw_stream():
    rows = list(map(json.loads, stream().splitlines())); rows[1], rows[-1] = rows[-1], rows[1]
    with pytest.raises(TransportError) as raised:
        transport.decode('\n'.join(map(json.dumps,rows)), '', 0, json.dumps(VALUE))
    assert raised.value.metadata['order_violation'] == 'reversed_turn'


def test_order_diagnostic_is_bounded_and_cannot_store_injected_fields():
    rows = [{'type':'item.completed','item':{'type':'error','message':'PRIVATE_CONTEXT'}} for _ in range(70)]
    diagnostic = transport.decode(stream(extra=rows), '', 0, json.dumps(VALUE))['transport_diagnostic']
    assert len(diagnostic['event_order']) == 64 and diagnostic['event_order_omitted'] == 10
    assert diagnostic['event_order_complete'] is False and transport.safe_diagnostic(diagnostic) == diagnostic
    bad = {**diagnostic, 'order_violation': 'PRIVATE_CONTEXT', 'event_order': [
        {'event_type':'PRIVATE_EVENT','item_type':'PRIVATE_SECRET'},
        {'event_type':'item.completed','item_type':'error','message':'PRIVATE_CONTEXT'},
    ]}
    projected = transport.safe_diagnostic(bad)
    assert 'PRIVATE_' not in json.dumps(projected) and 'order_violation' not in projected


@pytest.mark.parametrize('code,kind', [('authentication_failed','codex_auth_or_permission_stop'),('content_filter','codex_safety_refusal')])
def test_startup_warning_permission_or_safety_code_remains_hard_stop(code, kind):
    body = startup_stream().replace('"message": "PRIVATE_STARTUP_WARNING"', '"code": "'+code+'"')
    with pytest.raises(TransportError, match=kind) as raised: transport.decode(body, '', 0, json.dumps(VALUE))
    assert raised.value.metadata['no_fallback'] and not raised.value.final_usage_verified


@pytest.mark.parametrize('final,stage', [(None,'final_message_missing'), ('PRIVATE_NON_JSON','final_message_parse'), ('{}','final_message_binding')])
def test_startup_warning_does_not_bypass_final_message_binding(final,stage):
    with pytest.raises(TransportError) as raised: transport.decode(startup_stream(), '', 0, final)
    assert raised.value.metadata['failure_stage'] == stage and not raised.value.final_usage_verified


def test_warning_before_initial_thread_is_not_a_general_prefix_exception():
    rows = list(map(json.loads,startup_stream().splitlines()))
    rows[0],rows[1] = rows[1],rows[0]
    with pytest.raises(TransportError) as raised:
        transport.decode('\n'.join(map(json.dumps,rows)), '', 0, json.dumps(VALUE))
    assert raised.value.metadata['order_violation'] == 'invalid_startup_event'


@pytest.mark.parametrize('text,final,stage', [
    ('not JSON', 'not JSON', 'final_message_parse'),
    (None, None, 'final_message_missing'),
    (None, '{}', 'final_message_binding'),
])
def test_verified_terminal_accounts_failed_answer_without_accepting_it(text, final, stage):
    with pytest.raises(TransportError) as raised:
        transport.decode(stream(text=text), '', 0, final,
                         verified_cli_version=transport.RECOVERABLE_ERROR_CLI_VERSION)
    error = raised.value
    assert error.metadata['failure_stage'] == stage and error.metadata['no_fallback']
    assert error.usage == USAGE and error.final_usage_verified
    assert error.usage_observation['projection_rejection'] is None
    assert error.usage_observation['terminal_evidence'] == 'verified_single_final_turn'


@pytest.mark.parametrize('failure', ['restore', 'read'])
def test_verified_usage_survives_actual_infer_file_and_restore_failures(tmp_path, monkeypatch, failure):
    invoke, _, marks = offline_infer(tmp_path, monkeypatch,
        schema_override={'type': 'object', 'properties': {'content': {'type': 'string'}}, 'required': ['content']})
    original_launch = transport.launch
    if failure == 'read':
        def launch(args, *a, **kw):
            result = original_launch(args, *a, **kw)
            Path(args[args.index('--output-last-message') + 1]).write_bytes(b'\xff')
            return result
        monkeypatch.setattr(transport, 'launch', launch)
    with pytest.raises(TransportError) as raised:
        invoke()
    error = raised.value
    assert marks == [True] and error.metadata['no_fallback']
    assert error.metadata['failure_stage'] == ('final_file_read' if failure == 'read' else 'original_schema_restore')
    assert error.usage == USAGE and error.final_usage_verified


@pytest.mark.parametrize('event,category', [
    ({'type': 'error', 'message': 'request timed out: PRIVATE_TOKEN https://private.invalid'}, 'timeout'),
    ({'type': 'turn.failed', 'error': {'message': 'rate limit exceeded: PRIVATE_CONTEXT'}}, 'rate_limit'),
    ({'type': 'error', 'message': 'unclassified PRIVATE_CONTEXT'}, 'unclassified'),
])
def test_message_only_failure_keeps_bounded_channel_observation(event, category):
    rows = [{'type': 'thread.started'}, {'type': 'turn.started'}, event]
    if event['type'] != 'turn.failed':
        rows.append({'type': 'turn.failed', 'error': {'message': 'PRIVATE_FINAL'}})
    with pytest.raises(TransportError) as raised:
        transport.decode('\n'.join(map(json.dumps, rows)), 'PRIVATE_STDERR', 1, None,
                         verified_cli_version=transport.RECOVERABLE_ERROR_CLI_VERSION)
    error = raised.value
    observed = error.metadata['error_observations'][0]
    message = event.get('message', event.get('error', {}).get('message'))
    assert observed['event_index'] == 2
    assert observed['event_type'] == event['type'] and observed['stage'] == ('terminal' if event['type'] == 'turn.failed' else 'active_turn')
    assert observed['reported_reason'] == category and observed['evidence_scope'] == 'channel_report_only'
    assert observed['reason_basis'] == ('unclassified' if category == 'unclassified' else 'message_prefix')
    assert observed['message_sha256'] == hashlib.sha256(message.encode()).hexdigest()
    assert observed['message_bytes'] == len(message.encode()) and observed['message_type'] == 'string'
    assert error.usage is None and not error.final_usage_verified
    assert transport.safe_diagnostic(error.metadata) == error.metadata
    assert 'PRIVATE_' not in json.dumps(error.metadata) and 'private.invalid' not in json.dumps(error.metadata)


@pytest.mark.parametrize('message,category', [
    ('rate limit exceeded: PRIVATE_TRANSIENT', 'rate_limit'),
    ('permission denied: PRIVATE_TRANSIENT', 'authentication_or_permission'),
    ('safety refusal: PRIVATE_TRANSIENT', 'safety'),
])
def test_channel_reason_does_not_authorize_or_change_recovered_error_accounting(message, category):
    body = stream(extra=[{'type': 'error', 'message': message}])
    result = transport.decode(body, '', 0, json.dumps(VALUE),
                              verified_cli_version=transport.RECOVERABLE_ERROR_CLI_VERSION)
    assert result['usage'] == USAGE and result['transport_diagnostic']['recovered_error_count'] == 1
    assert result['transport_diagnostic']['error_observations'][0]['reported_reason'] == category
    assert not result['transport_diagnostic']['no_fallback']


def test_diagnostic_observations_are_bounded_and_reproject_idempotently():
    rows = [{'type': 'error', 'message': 'PRIVATE_' + '\ud800' * 4000} for _ in range(20)]
    with pytest.raises(TransportError) as raised:
        transport.decode(stream(extra=rows), '', 1, None)
    diagnostic = raised.value.metadata
    assert len(diagnostic['error_observations']) == 16 and diagnostic['error_observations_omitted'] == 4
    assert diagnostic['error_observations'][0]['message_bytes'] == len(rows[0]['message'].encode('utf-8', errors='surrogatepass'))
    assert transport.safe_diagnostic(diagnostic) == diagnostic and 'PRIVATE_' not in json.dumps(diagnostic)
    injected = {**diagnostic, 'error_observations': [{**diagnostic['error_observations'][0], 'message': 'PRIVATE_CONTEXT'}]}
    projected = transport.safe_diagnostic(injected)
    assert projected['error_observations'] == [] and projected['error_observations_omitted'] == 5
    assert 'PRIVATE_' not in json.dumps(projected) and transport.safe_diagnostic(projected) == projected


@pytest.mark.parametrize('event', [
    {'type': 'item.started'}, {'type': 'item.updated', 'item': None},
    {'type': 'item.completed', 'item': 'PRIVATE_NONOBJECT'},
    {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}'}, 'error': {'message': 'PRIVATE_ERROR'}},
    {'type': 'item.completed', 'item': {'type': 'reasoning', 'error': {'code': 'server_error'}}},
    {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}', 'error': {'status_code': 500}}},
])
@pytest.mark.parametrize('final', [json.dumps(VALUE), 'not JSON'])
def test_malformed_items_and_embedded_errors_never_prove_accounting(event, final):
    with pytest.raises(TransportError) as raised:
        transport.decode(stream(extra=[event]), '', 0, final, verified_cli_version='0.162.1')
    error = raised.value
    assert error.metadata['failure_stage'] == 'terminal' and error.metadata['no_fallback']
    assert error.usage is None and not error.final_usage_verified


@pytest.mark.parametrize('detail,kind', [
    ({'code': 'permission_denied'}, 'codex_auth_or_permission_stop'),
    ({'http_status': 403}, 'codex_auth_or_permission_stop'),
    ({'code': 'content_filter'}, 'codex_safety_refusal'),
])
def test_nested_error_cannot_hide_permission_or_safety_stop(detail, kind):
    event = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}', 'error': detail}}
    with pytest.raises(TransportError, match=kind) as raised:
        transport.decode(stream(extra=[event]), '', 0, 'not JSON', verified_cli_version='0.162.1')
    assert raised.value.usage is None and not raised.value.final_usage_verified and raised.value.metadata['no_fallback']


@pytest.mark.parametrize('event_type', [None, [], {}, 7])
def test_nonstring_event_type_keeps_safe_observation_without_masking_failure(event_type):
    event = {'type': event_type, 'error': {'message': 'request timed out PRIVATE_CONTEXT'}}
    with pytest.raises(TransportError) as raised:
        transport.decode(stream(extra=[event]), '', 0, 'not JSON', verified_cli_version='0.162.1')
    error = raised.value
    assert error.metadata['failure_stage'] == 'terminal' and error.usage is None and not error.final_usage_verified
    observation = error.metadata['error_observations'][0]
    assert observation['event_type'] == 'unknown' and observation['reported_reason'] == 'timeout'
    assert transport.safe_diagnostic(error.metadata) == error.metadata and 'PRIVATE_' not in json.dumps(error.metadata)


@pytest.mark.parametrize('extra', [
    [{'type': 'turn.failed', 'error': {'code': 'server_error'}}],
    [{'type': 'error', 'code': 'permission_denied'}],
    [{'type': 'error', 'code': 'content_filter'}],
    [{'type': 'item.started', 'item': None}],
])
def test_unreadable_final_file_cannot_hide_an_untrusted_terminal(tmp_path, monkeypatch, extra):
    invoke, _, marks = offline_infer(tmp_path, monkeypatch, body=stream(extra=extra))
    original_launch = transport.launch
    def launch(args, *a, **kw):
        result = original_launch(args, *a, **kw)
        Path(args[args.index('--output-last-message') + 1]).write_bytes(b'\xff')
        return result
    monkeypatch.setattr(transport, 'launch', launch)
    with pytest.raises(TransportError) as raised:
        invoke()
    assert marks == [True] and raised.value.metadata['failure_stage'] == 'terminal'
    assert raised.value.usage is None and not raised.value.final_usage_verified and raised.value.metadata['no_fallback']


@pytest.mark.parametrize('usage,reason', [
    (None, 'missing_usage'),
    ({'input_tokens': 17, 'output_tokens': True}, 'noninteger_token_count'),
    ({'input_tokens': 17, 'output_tokens': 3, 'cache_write_input_tokens': 18}, 'cache_tokens_exceed_input'),
])
def test_late_answer_failure_never_upgrades_invalid_usage(usage, reason):
    rows = list(map(json.loads, stream().splitlines()))
    rows[-1]['usage'] = usage
    with pytest.raises(TransportError) as raised:
        transport.decode('\n'.join(map(json.dumps, rows)), '', 0, 'not JSON', verified_cli_version='0.162.1')
    error = raised.value
    assert error.metadata['failure_stage'] == 'final_message_parse'
    assert error.usage is None and not error.final_usage_verified and error.usage_observation['projection_rejection'] == reason
