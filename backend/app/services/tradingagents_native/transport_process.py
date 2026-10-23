"""Existing Codex CLI with tools disabled; one fixed-input baseline only."""
import hashlib
import json
import math
import os
import signal
import subprocess

DISABLE = ['shell_tool', 'shell_snapshot', 'multi_agent', 'apps', 'plugins', 'remote_plugin',
           'hooks', 'goals', 'memories', 'image_generation', 'skill_search',
           'skill_mcp_dependency_install', 'code_mode_host', 'view_image']

MAX_TIMEOUT_BYTES = 8 * 1024 * 1024
MAX_TIMEOUT_EVENTS = 4096
_AUTH_CODES = {'authentication_failed', 'authorization_failed', 'invalid_api_key',
               'unauthorized', 'permission_denied', 'account_deactivated'}
_SAFETY_CODES = {'safety_refusal', 'content_policy_violation', 'content_filter', 'policy_violation'}
_EVENTS = {'thread.started', 'turn.started', 'turn.completed', 'turn.failed',
           'item.started', 'item.updated', 'item.completed', 'error'}
_USAGE_FIELDS = ('input_tokens', 'output_tokens', 'cached_input_tokens',
                 'cache_creation_input_tokens', 'cache_write_input_tokens', 'reasoning_output_tokens')
_MAX_USAGE_FIELDS = 16
_MAX_TOKEN_COUNT = 2**63 - 1
_UNOBSERVED = object()
_USAGE_EVIDENCE = {'invalid_encoding', 'stream_exceeds_bound', 'event_count_exceeds_bound',
                   'invalid_event_stream', 'explicit_authorization_stop', 'explicit_safety_stop',
                   'unrecognized_typed_event', 'typed_error_present', 'single_final_turn_not_established',
                   'verified_single_final_turn', 'invalid_final_usage'}


class TransportError(RuntimeError):
    def __init__(self, diagnostic, *, verified_final_usage=None, observed_terminal_usage=_UNOBSERVED):
        self.diagnostic = diagnostic
        # Only terminal-proof callers supply this separately verified
        # projection. An arbitrary diagnostic['usage'] is not an accounting fact.
        self.usage = verified_final_usage
        self.final_usage_verified = verified_final_usage is not None
        # Observation does not establish that a timed-out stream was complete.
        # Numeric evidence survives even when the separate proof gate rejects it.
        observed = verified_final_usage if observed_terminal_usage is _UNOBSERVED else observed_terminal_usage
        self.usage_observation = project_usage_evidence(observed)[1]
        reason = diagnostic.get('usage_evidence')
        self.usage_observation['terminal_evidence'] = (reason if isinstance(reason, str) and reason in _USAGE_EVIDENCE
            else 'verified_transport_final' if self.final_usage_verified else 'unverified_transport_failure')
        self.metadata = dict(diagnostic)
        super().__init__(diagnostic['kind'])


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _usage_type(value):
    return {type(None): 'null', bool: 'boolean', int: 'integer', float: 'float',
            str: 'string', dict: 'object', list: 'array'}.get(type(value), 'other')


def project_usage_evidence(value):
    """Strict token projection plus bounded, text-free terminal observation.

    cache_write is an observed CLI field. The previously supported creation
    field remains independent: no alias, inferred total, or cumulative fallback.
    Arbitrary field names and text values are never copied into recovery files.
    """
    observed = {'version': 1, 'source_type': _usage_type(value), 'numeric_fields': {},
                'field_types': {}, 'unrecognized_fields': [], 'field_count': len(value) if isinstance(value, dict) else 0,
                'omitted_field_count': 0, 'raw_complete': False, 'projection_rejection': None}
    reason = None
    if not isinstance(value, dict):
        reason = 'missing_usage' if value is None else 'nonobject_usage'
    else:
        # Inspect fixed known keys even if an oversized object placed them last.
        for key in _USAGE_FIELDS:
            if key in value:
                item = value[key]
                observed['field_types'][key] = _usage_type(item)
                if type(item) is int and -_MAX_TOKEN_COUNT <= item <= _MAX_TOKEN_COUNT:
                    observed['numeric_fields'][key] = item
        for key in value:
            if key in _USAGE_FIELDS:
                continue
            if len(observed['unrecognized_fields']) >= _MAX_USAGE_FIELDS:
                break
            item = value[key]
            field = {'field_sha256': hashlib.sha256(key.encode('utf-8', errors='surrogatepass')).hexdigest()
                     if isinstance(key, str) else None,
                     'value_type': _usage_type(item)}
            if type(item) is int and -_MAX_TOKEN_COUNT <= item <= _MAX_TOKEN_COUNT:
                field['numeric_value'] = item
            observed['unrecognized_fields'].append(field)
        observed['omitted_field_count'] = len(value) - len(observed['field_types']) - len(observed['unrecognized_fields'])
        if len(value) > _MAX_USAGE_FIELDS:
            reason = 'usage_field_count_exceeds_bound'
        elif observed['unrecognized_fields']:
            reason = 'unsupported_usage_field'
        elif not {'input_tokens', 'output_tokens'} <= value.keys():
            reason = 'missing_required_token_count'
        elif any(type(item) is not int for item in value.values()):
            reason = 'noninteger_token_count'
        elif any(item < 0 or item > _MAX_TOKEN_COUNT for item in value.values()):
            reason = 'token_count_out_of_range'
        elif sum(value.get(key, 0) for key in ('cached_input_tokens', 'cache_creation_input_tokens',
                                              'cache_write_input_tokens')) > value['input_tokens']:
            reason = 'cache_tokens_exceed_input'
        elif value.get('reasoning_output_tokens', 0) > value['output_tokens']:
            reason = 'reasoning_tokens_exceed_output'
        observed['raw_complete'] = (not observed['unrecognized_fields'] and not observed['omitted_field_count']
                                    and len(observed['numeric_fields']) == len(value))
    observed['projection_rejection'] = reason
    return ({key: value[key] for key in _USAGE_FIELDS if key in value} if reason is None else None), observed


def _project_usage(value):
    return project_usage_evidence(value)[0]


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('Nonfinite JSON')
    return value


def timeout_evidence(stdout):
    """Return only bounded typed accounting evidence, never a successful answer.

    Parse complete JSONL rows within an 8 MiB / 4096 event bound. Explicit
    safety/auth/tool stops in the parsed prefix survive a later malformed row.
    Only one correctly ordered, error-free, final completed turn supplies usage.
    This does not validate the model answer, estimate usage, or infer billing.
    The third return value is ephemeral terminal usage for numeric observation;
    launch never copies its arbitrary keys or values into a diagnostic.
    """
    events, reason = [], None
    try:
        body = stdout.encode('utf-8')
    except (AttributeError, UnicodeError):
        return {'usage': None, 'usage_evidence': 'invalid_encoding'}, None, None
    if len(body) > MAX_TIMEOUT_BYTES:
        reason = 'stream_exceeds_bound'
    # A bounded prefix may still contain an explicit typed stop. It can never
    # provide usage when any suffix is outside the inspection bound.
    inspected = body[:MAX_TIMEOUT_BYTES].decode('utf-8', errors='ignore')
    inspected_rows = 0
    for line in inspected.splitlines():
        if not line.strip():
            continue
        if inspected_rows >= MAX_TIMEOUT_EVENTS:
            reason = reason or 'event_count_exceeds_bound'
            break
        inspected_rows += 1
        try:
            row = json.loads(line, object_pairs_hook=_unique,
                             parse_float=_finite_float,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
            if not isinstance(row, dict):
                raise ValueError('Nonobject event')
            events.append(row)
        except (ValueError, RecursionError):
            reason = reason or 'invalid_event_stream'
            # Continue only this bounded scan for explicit safety/auth/tool
            # stops. The malformed row already makes all usage ineligible.
    items = [row['item'] for row in events if row.get('type') in
             ('item.started', 'item.updated', 'item.completed') and isinstance(row.get('item'), dict)]
    bad = [row for row in events if row.get('type') in ('error', 'turn.failed')]
    errors = items + bad + [row['error'] for row in events + items if isinstance(row.get('error'), dict)]
    codes = {row.get('code', row.get('error_code')) for row in errors
             if isinstance(row.get('code', row.get('error_code')), str)}
    statuses = [row.get('http_status', row.get('status_code')) for row in errors]
    auth_status = next((status for status in statuses if type(status) is int and status in (401, 403)), None)
    result = {'usage': None, 'turn_started_count': sum(row.get('type') == 'turn.started' for row in events),
              'turn_count': sum(row.get('type') == 'turn.completed' for row in events)}
    completed = [row for row in events if row.get('type') == 'turn.completed']
    observed_usage = completed[0].get('usage') if len(completed) == 1 else None
    if codes & _AUTH_CODES or auth_status is not None:
        result.update(kind='codex_auth_or_permission_stop', http_status=auth_status,
                      explicit_error_codes=sorted(codes & _AUTH_CODES), no_fallback=True,
                      usage_evidence='explicit_authorization_stop')
        return result, None, observed_usage
    if codes & _SAFETY_CODES or any(item.get('refusal') for item in items):
        result.update(kind='codex_safety_refusal', no_fallback=True, usage_evidence='explicit_safety_stop')
        return result, None, observed_usage
    host = any(item.get('type') not in ('agent_message', 'reasoning', 'error') for item in items)
    unknown = any(not isinstance(row.get('type'), str) or row['type'] not in _EVENTS for row in events)
    malformed_item = any(row.get('type') in ('item.started', 'item.updated', 'item.completed')
                         and not isinstance(row.get('item'), dict) for row in events)
    if host or unknown or malformed_item:
        result.update(kind='codex_typed_terminal_failure', no_fallback=True,
                      host_tool_types=['unrecognized_item_type'] if host or malformed_item else [],
                      unknown_event_types=['unrecognized_event_type'] if unknown else [],
                      usage_evidence='unrecognized_typed_event')
        return result, None, observed_usage
    if (reason or bad or any('error' in row for row in events)
            or any(item.get('type') == 'error' or 'error' in item for item in items)):
        result['usage_evidence'] = reason or 'typed_error_present'
        return result, None, observed_usage
    types = [row.get('type') for row in events]
    if (result['turn_started_count'] != 1 or result['turn_count'] != 1
            or types[-1:] != ['turn.completed'] or types.index('turn.started') >= types.index('turn.completed')
            or any(kind != 'thread.started' for kind in types[:types.index('turn.started')])):
        result['usage_evidence'] = 'single_final_turn_not_established'
        return result, None, observed_usage
    usage = _project_usage(events[-1].get('usage'))
    result.update(usage=usage, usage_evidence='verified_single_final_turn' if usage is not None else 'invalid_final_usage')
    return result, usage, observed_usage

def launch(args, prompt, *, env, cwd, timeout=360):
    proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,env=env,cwd=cwd,start_new_session=True)
    try:
        out,err=proc.communicate(prompt,timeout=timeout)
    except subprocess.TimeoutExpired:
        killed = False
        try:
            os.killpg(proc.pid,signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            out,err=proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            killed = True
            try:
                os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            out,err=proc.communicate()
        evidence, usage, observed_usage = timeout_evidence(out)
        raise TransportError({'kind':'timeout', **evidence, 'timed_out': True,
            'termination_requested': True, 'kill_requested': killed,
            'local_drain_complete': True, 'exit_code': proc.returncode,
            'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),
            'stderr_sha256':hashlib.sha256(err.encode()).hexdigest()}, verified_final_usage=usage,
            observed_terminal_usage=observed_usage) from None
    return out,err,proc.returncode
