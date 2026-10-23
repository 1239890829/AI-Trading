"""Server-pinned Codex CLI, tool-free/ephemeral, typed output-schema only.

The accounted driver owns budget and authorizes
every launch. This module does not retry, log full context, change credentials,
install dependencies or use the upstream's separate ChatGPT sign-in.
"""
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
import re
import tempfile

import subprocess

from .cli_codec import encode_cli_transcript
from .transport_process import DISABLE, TransportError, launch, project_usage_evidence
from .schema_projection import strict_wire_schema, restore_original_omissions

MAX_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 4096
RECOVERABLE_ERROR_CLI_VERSION = '0.162.1'
_SERIALIZATION_ERROR_PREFIX = 'failed to serialize exec json event:'
MAX_ORDER_EVENTS = 64
MAX_ERROR_OBSERVATIONS = 16
MAX_REASON_CHARACTERS = 2048
_EVENT_TYPES = {'thread.started', 'turn.started', 'turn.completed', 'turn.failed',
                'item.started', 'item.updated', 'item.completed', 'error'}
_ORDER_EVENT_TYPES = _EVENT_TYPES | {'unknown'}
_ORDER_ITEM_TYPES = {'agent_message', 'reasoning', 'error', 'none', 'missing', 'unknown'}
_ORDER_VIOLATIONS = {'reversed_turn', 'completed_not_last', 'invalid_startup_event'}
_AUTH_CODES = {'authentication_failed', 'authorization_failed', 'invalid_api_key', 'unauthorized',
               'permission_denied', 'account_deactivated'}
_SAFETY_CODES = {'safety_refusal', 'content_policy_violation', 'content_filter', 'policy_violation'}
_KNOWN_CODES = _AUTH_CODES | _SAFETY_CODES | {'context_length_exceeded', 'rate_limit_exceeded',
    'rate_limit_error', 'insufficient_quota', 'model_not_found', 'server_error', 'internal_server_error',
    'connection_error', 'request_timeout', 'service_unavailable', 'overloaded_error'}
_KINDS = {'timeout', 'codex_auth_or_permission_stop', 'codex_safety_refusal', 'codex_typed_terminal_failure',
          'codex_event_stream_failure', 'codex_final_schema_failure', 'codex_original_schema_failure',
          'codex_final_file_failure', 'codex_process_failure', 'codex_typed_terminal_success'}
_STAGES = {'event_stream', 'terminal', 'terminal_order', 'final_message_parse', 'final_message_missing', 'final_message_binding',
           'original_schema_restore', 'final_file_read', 'process_launch_or_drain'}
_REPORTED_REASONS = {'authentication_or_permission', 'safety', 'context_limit', 'rate_limit',
                     'quota', 'timeout', 'connection', 'server', 'serialization', 'retry_exhausted', 'unclassified'}
_REASON_CODES = {
    **{code: 'authentication_or_permission' for code in _AUTH_CODES},
    **{code: 'safety' for code in _SAFETY_CODES},
    'context_length_exceeded': 'context_limit', 'rate_limit_exceeded': 'rate_limit', 'rate_limit_error': 'rate_limit',
    'insufficient_quota': 'quota', 'request_timeout': 'timeout', 'connection_error': 'connection',
    **{code: 'server' for code in ('server_error', 'internal_server_error', 'service_unavailable', 'overloaded_error')},
}
# These labels report what a channel said. They are never authority, proof of
# the remote cause, or input to admission, usage verification or retry policy.
_REASON_PREFIXES = (
    ('serialization', (_SERIALIZATION_ERROR_PREFIX,)),
    ('retry_exhausted', ('exceeded retry limit', 'retry limit exceeded')),
    ('timeout', ('request timed out', 'request timeout', 'connection timed out')),
    ('connection', ('stream disconnected before completion', 'error sending request', 'connection reset', 'connection refused')),
    ('rate_limit', ('rate limit exceeded', 'rate limit reached', 'too many requests')),
    ('quota', ('insufficient quota', 'you exceeded your current quota', 'usage limit reached')),
    ('context_limit', ('context length exceeded', 'maximum context length', 'input exceeds the context window')),
    ('authentication_or_permission', ('authentication failed', 'unauthorized', 'permission denied')),
    ('safety', ('content policy violation', 'content filter', 'safety refusal')),
    ('server', ('internal server error', 'service unavailable', 'server error', 'server overloaded')),
)
_MESSAGE_TYPES = {'string', 'integer', 'boolean', 'null', 'float', 'object', 'array', 'other'}
_ERROR_OBSERVATION_FIELDS = {'event_index', 'event_type', 'stage', 'message_source', 'message_type', 'message_sha256',
                             'message_bytes', 'reported_reason', 'reason_basis', 'evidence_scope'}
NOTE = ('Transport protocol only: the following lossless ordered JSON transcript contains the original graph roles, '
        'messages, tool declarations and actual tool-result history. Continue only the current assistant turn. '
        'No host tools are available. Graph functions are data declarations, never native Codex tools. '
        'For a content/tool_calls schema, express graph requests only in tool_calls with original allowed names and arguments; '
        'the original graph executes them after this process returns. Never invent tool_result or claim unexecuted tools ran. '
        'For an original structured result schema, produce that object. Null on a wire-only optional nonnullable property '
        'represents omission, not a measured value. Preserve every original fact, role, restriction and field description.\n')
FROZEN_TABLE_NOTE = ('Frozen evidence representation only: frozen-facts-tables-v1 preserves the original facts. '
    '__frozen_table_v1__ columns are original field names; each ordered row contains those exact values, including null. '
    'Read each row as the object formed by pairing columns with values. No fact, source, row, or restriction is added or removed.\n')
_FROZEN_START = '<UNTRUSTED_FROZEN_EVIDENCE>'
_FROZEN_END = '</UNTRUSTED_FROZEN_EVIDENCE>'
_TABLE_TAG = '__frozen_table_v1__'
_TABLE_PATHS = (('price_history', 'observations'), ('fundamentals', 'periods'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _check_frozen_keys(value, depth=0):
    if depth > 64:
        raise ValueError('frozen_wire_source_invalid')
    if isinstance(value, dict):
        if _TABLE_TAG in value:
            raise ValueError('frozen_wire_source_invalid')
        for item in value.values():
            _check_frozen_keys(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_frozen_keys(item, depth + 1)


def _frozen_tables(facts):
    """Only two offered homogeneous row arrays; every other value is untouched."""
    encoded = deepcopy(facts)
    changed = False
    for parent, field in _TABLE_PATHS:
        owner = encoded.get(parent)
        rows = owner.get(field) if isinstance(owner, dict) else None
        if (not isinstance(rows, list) or len(rows) < 3 or not isinstance(rows[0], dict)
                or not rows[0] or any(not isinstance(row, dict) for row in rows)):
            continue
        columns = list(rows[0])
        if any(list(row) != columns for row in rows):
            continue
        table = {_TABLE_TAG: {'columns': columns, 'rows': [[row[key] for key in columns] for row in rows]}}
        if len(canonical(table)) >= len(canonical(rows)):
            continue
        owner[field] = table
        changed = True
    # A local restoration check is proof of representation equality only. It
    # never attests to the source's truth or approves a model response.
    restored = deepcopy(encoded)
    for parent, field in _TABLE_PATHS:
        owner = restored.get(parent)
        table = owner.get(field) if isinstance(owner, dict) else None
        if isinstance(table, dict) and set(table) == {_TABLE_TAG}:
            data = table[_TABLE_TAG]
            owner[field] = [dict(zip(data['columns'], row)) for row in data['rows']]
    if canonical(restored) != canonical(facts):
        raise ValueError('frozen_wire_roundtrip_invalid')
    return encoded, changed


def _selected_wire_messages(messages, expected_sha256):
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError('frozen_wire_binding_invalid')
    starts = [(index, row['content'].count(_FROZEN_START)) for index, row in enumerate(messages)]
    ends = [(index, row['content'].count(_FROZEN_END)) for index, row in enumerate(messages)]
    if sum(count for _, count in starts) != 1 or sum(count for _, count in ends) != 1:
        raise ValueError('frozen_wire_source_invalid')
    index = next(index for index, count in starts if count)
    if not ends[index][1] or messages[index]['role'] not in {'system', 'user'}:
        raise ValueError('frozen_wire_source_invalid')
    content = messages[index]['content']
    start = content.index(_FROZEN_START) + len(_FROZEN_START)
    end = content.index(_FROZEN_END)
    if end <= start:
        raise ValueError('frozen_wire_source_invalid')
    try:
        facts = json.loads(content[start:end], object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('frozen_wire_source_invalid')))
        if not isinstance(facts, dict) or facts.get('format') == 'frozen-facts-tables-v1':
            raise ValueError('frozen_wire_source_invalid')
        _check_frozen_keys(facts)
        original = canonical(facts)
        if sha(original.encode()) != expected_sha256:
            raise ValueError('frozen_wire_binding_invalid')
        encoded, changed = _frozen_tables(facts)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('frozen_wire_source_invalid') from None
    if not changed:
        return messages, False
    body = canonical({'format': 'frozen-facts-tables-v1', 'source_facts_sha256': expected_sha256, 'facts': encoded})
    copied = [dict(row) for row in messages]
    copied[index]['content'] = content[:start] + '\n' + body + '\n' + content[end:]
    return copied, True


def prepare_cli_request(messages, original_schema, *, selected_facts_sha256=None):
    """One exact wire builder for accounting preflight and actual CLI input.

    The optional hash is supplied only by the selected consumer after the
    original request/recovery checks. Source text cannot authorize this mode.
    No original message, packet, checkpoint, schema or tool is modified.
    """
    transcript = encode_cli_transcript(messages)  # Keep original role/tool/size guards.
    changed = False
    if selected_facts_sha256 is not None:
        prepared, changed = _selected_wire_messages(messages, selected_facts_sha256)
        if changed:
            candidate = encode_cli_transcript(prepared)
            if len(candidate) + len(FROZEN_TABLE_NOTE) < len(transcript):
                transcript = candidate
            else:
                changed = False  # A small table must not grow the actual wire.
    wire, projection = strict_wire_schema(original_schema)
    return NOTE + (FROZEN_TABLE_NOTE if changed else '') + transcript, wire, projection


def sha(body):
    return hashlib.sha256(body).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def parse(text):
    if not isinstance(text, str) or len(text.encode()) > MAX_BYTES:
        raise ValueError('Codex response exceeds bound')
    return json.loads(text, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def _opaque(value):
    types = {str: 'string', int: 'integer', bool: 'boolean', type(None): 'null', float: 'float',
             dict: 'object', list: 'array'}
    body = value if isinstance(value, str) else str(value) if (type(value) in (bool, type(None))
        or type(value) is int and -(2**63) <= value <= 2**63 - 1) else None
    return {'sha256': sha(body.encode('utf-8', errors='surrogatepass')) if body is not None else None,
            'value_type': types.get(type(value), 'other')}


def _error_observations(events):
    """Bounded error-channel facts while the transient stream is still present."""
    observations, count = [], 0
    active, ended = False, False
    for index, row in enumerate(events):
        event = row.get('type')
        event = event if isinstance(event, str) and event in _EVENT_TYPES else 'unknown'
        if event == 'turn.started':
            active = True
        item = row.get('item')
        if event in {'error', 'turn.failed'} or 'error' in row:
            payload, prefix = row, 'event'
        elif (event in {'item.started', 'item.updated', 'item.completed'} and isinstance(item, dict)
                and (item.get('type') == 'error' or 'error' in item)):
            payload, prefix = item, 'item'
        else:
            if event == 'turn.completed':
                active, ended = False, True
            continue
        count += 1
        if len(observations) >= MAX_ERROR_OBSERVATIONS:
            continue
        nested = payload.get('error')
        nested = nested if isinstance(nested, dict) else {}
        if 'message' in payload:
            message, source = payload['message'], prefix + '.message'
        elif 'message' in nested:
            message, source = nested['message'], prefix + '.error.message'
        else:
            message, source = None, 'none'
        raw = message.encode('utf-8', errors='surrogatepass') if isinstance(message, str) else None
        reason, basis = 'unclassified', 'unclassified'
        for data in (payload, nested):
            code = data.get('code', data.get('error_code'))
            if isinstance(code, str) and code in _REASON_CODES:
                reason, basis = _REASON_CODES[code], 'structured_code'
                break
        if basis == 'unclassified':
            for data in (payload, nested):
                status = data.get('http_status', data.get('status_code'))
                if type(status) is int:
                    reported = ('authentication_or_permission' if status in (401, 403) else
                                'rate_limit' if status == 429 else 'timeout' if status in (408, 504) else
                                'server' if 500 <= status <= 599 else None)
                    if reported:
                        reason, basis = reported, 'structured_status'
                        break
        if basis == 'unclassified' and isinstance(message, str):
            inspected = message[:MAX_REASON_CHARACTERS].lstrip().lower()
            if inspected.startswith('error: '):
                inspected = inspected[7:].lstrip()
            for category, prefixes in _REASON_PREFIXES:
                if any(inspected.startswith(prefix) and (not prefix[-1].isalnum() or len(inspected) == len(prefix)
                        or not inspected[len(prefix)].isalnum()) for prefix in prefixes):
                    reason, basis = category, 'message_prefix'
                    break
        observations.append({'event_index': index, 'event_type': event,
            'stage': 'terminal' if event == 'turn.failed' else 'after_terminal' if ended else 'active_turn' if active else 'startup',
            'message_source': source, 'message_type': _opaque(message)['value_type'],
            'message_sha256': sha(raw) if raw is not None else None, 'message_bytes': len(raw) if raw is not None else None,
            'reported_reason': reason, 'reason_basis': basis, 'evidence_scope': 'channel_report_only'})
        if event == 'turn.failed':
            active, ended = False, True
    return observations, max(0, count - len(observations))


def safe_diagnostic(value):
    """Fixed vocabulary, bounded scalars and opaque codes; no provider text."""
    value = value if isinstance(value, dict) else {}
    result = {'version': 1}
    for key, allowed in [('kind', _KINDS), ('failure_stage', _STAGES),
        ('parse_failure', {'invalid_encoding', 'stream_exceeds_bound', 'event_count_exceeds_bound',
                           'invalid_event_stream', 'nonobject_event'}),
        ('order_violation', _ORDER_VIOLATIONS),
        ('error_contract_version', {RECOVERABLE_ERROR_CLI_VERSION}),
        ('usage_evidence', {'verified_single_final_turn', 'invalid_final_usage'}),
        ('error_disposition', {'recovered_under_verified_cli', 'unverified_error_stop', 'serialization_failure_stop', 'malformed_error_stop', 'error_outside_active_turn'}),
        ('exception_type', {'ValueError', 'JSONDecodeError', 'UnicodeError', 'UnicodeDecodeError',
                            'RecursionError', 'ProjectionError', 'OSError', 'FileNotFoundError', 'PermissionError'})]:
        if isinstance(value.get(key), str) and value[key] in allowed:
            result[key] = value[key]
    for key in ('exit_code', 'turn_count', 'turn_started_count', 'parsed_event_count', 'invalid_event_count',
                'typed_error_count', 'warning_count', 'recovered_error_count', 'malformed_item_count',
                'unexpected_error_payload_count', 'stream_bytes', 'http_status'):
        number = value.get(key)
        lower, upper = ((-(2**31), 2**31 - 1) if key == 'exit_code' else
                        (100, 599) if key == 'http_status' else (0, 2**63 - 1) if key == 'stream_bytes'
                        else (0, MAX_EVENTS))
        if type(number) is int and lower <= number <= upper:
            result[key] = number
    for key in ('no_fallback', 'timed_out', 'termination_requested', 'kill_requested', 'local_drain_complete',
                'stream_truncated', 'event_count_truncated', 'final_file_present', 'refusal_present', 'event_order_complete'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    for key in ('stdout_sha256', 'stderr_sha256', 'final_file_sha256'):
        if isinstance(value.get(key), str) and re.fullmatch('[a-f0-9]{64}', value[key]):
            result[key] = value[key]
    raw_observations = value.get('error_observations')
    if isinstance(raw_observations, list):
        observations, invalid = [], 0
        for item in raw_observations[:MAX_ERROR_OBSERVATIONS]:
            if (isinstance(item, dict) and set(item) == _ERROR_OBSERVATION_FIELDS
                    and type(item['event_index']) is int and 0 <= item['event_index'] < MAX_EVENTS
                    and isinstance(item['event_type'], str) and item['event_type'] in _ORDER_EVENT_TYPES
                    and isinstance(item['stage'], str) and item['stage'] in {'startup', 'active_turn', 'terminal', 'after_terminal'}
                    and isinstance(item['message_source'], str) and item['message_source'] in {'event.message', 'event.error.message', 'item.message', 'item.error.message', 'none'}
                    and isinstance(item['message_type'], str) and item['message_type'] in _MESSAGE_TYPES
                    and (item['message_sha256'] is None or isinstance(item['message_sha256'], str)
                         and re.fullmatch('[a-f0-9]{64}', item['message_sha256']))
                    and (item['message_bytes'] is None or type(item['message_bytes']) is int and 0 <= item['message_bytes'] <= 3 * MAX_BYTES)
                    and isinstance(item['reported_reason'], str) and item['reported_reason'] in _REPORTED_REASONS
                    and isinstance(item['reason_basis'], str) and item['reason_basis'] in {'structured_code', 'structured_status', 'message_prefix', 'unclassified'}
                    and item['evidence_scope'] == 'channel_report_only'):
                observations.append(dict(item))
            else:
                invalid += 1
        omitted = value.get('error_observations_omitted', 0)
        omitted = omitted if type(omitted) is int and 0 <= omitted <= MAX_EVENTS else 0
        result['error_observations'] = observations
        result['error_observations_omitted'] = min(MAX_EVENTS, omitted + invalid + max(0, len(raw_observations) - MAX_ERROR_OBSERVATIONS))
    order = value.get('event_order')
    if isinstance(order, list):
        retained = []
        invalid = 0
        for item in order[:MAX_ORDER_EVENTS]:
            if (isinstance(item, dict) and set(item) == {'event_type', 'item_type'}
                    and isinstance(item['event_type'], str) and item['event_type'] in _ORDER_EVENT_TYPES
                    and isinstance(item['item_type'], str) and item['item_type'] in _ORDER_ITEM_TYPES):
                retained.append(dict(item))
            else:
                invalid += 1
        omitted = value.get('event_order_omitted', 0)
        omitted = omitted if type(omitted) is int and 0 <= omitted <= MAX_EVENTS else 0
        result['event_order'] = retained
        result['event_order_omitted'] = min(MAX_EVENTS, omitted + invalid + max(0, len(order) - MAX_ORDER_EVENTS))
        if invalid or len(order) > MAX_ORDER_EVENTS or result['event_order_omitted']:
            result['event_order_complete'] = False
    codes = value.get('explicit_error_codes')
    codes = codes if isinstance(codes, list) else []
    known = list(dict.fromkeys(code for code in codes if isinstance(code, str) and code in _KNOWN_CODES))
    result['explicit_error_codes'] = known[:16]
    omitted = value.get('explicit_error_codes_omitted', 0)
    result['explicit_error_codes_omitted'] = max(0, len(known) - 16) + (omitted if type(omitted) is int and 0 <= omitted <= 2**31 else 0)
    unknown_codes = [code for code in codes if not isinstance(code, str) or code not in _KNOWN_CODES]
    for key, raw in [('unknown_error_codes', unknown_codes), ('host_tool_types', value.get('host_tool_types')),
                     ('unknown_event_types', value.get('unknown_event_types'))]:
        raw = raw if isinstance(raw, list) else []
        # Retain already sanitized observations when the driver projects again.
        if key == 'unknown_error_codes' and not raw:
            raw = value.get(key) if isinstance(value.get(key), list) else []
        observations = []
        for item in raw[:16]:
            if (isinstance(item, dict) and set(item) == {'sha256', 'value_type'}
                    and isinstance(item['value_type'], str)
                    and item['value_type'] in {'string', 'integer', 'boolean', 'null', 'float', 'object', 'array', 'other'}
                    and (item['sha256'] is None or isinstance(item['sha256'], str)
                         and re.fullmatch('[a-f0-9]{64}', item['sha256']))):
                observations.append(dict(item))
            else:
                observations.append(_opaque(item))
        result[key] = observations
        omitted = value.get(key + '_omitted', 0)
        result[key + '_omitted'] = max(0, len(raw) - 16) + (omitted if type(omitted) is int and 0 <= omitted <= 2**31 else 0)
    return result


def _event_label(row):
    event = row.get('type')
    event = event if isinstance(event, str) and event in _EVENT_TYPES else 'unknown'
    item = row.get('item')
    kind = 'none'
    if event.startswith('item.'):
        subtype = item.get('type') if isinstance(item, dict) else None
        kind = subtype if isinstance(subtype, str) and subtype in {'agent_message', 'reasoning', 'error'} else (
            'missing' if subtype is None else 'unknown')
    return {'event_type': event, 'item_type': kind}


def _order_violation(events):
    types = [row.get('type') for row in events]
    start, completed = types.index('turn.started'), types.index('turn.completed')
    if start >= completed:
        return 'reversed_turn'
    if completed != len(events) - 1:
        return 'completed_not_last'
    thread_seen = False
    for row in events[:start]:
        if row.get('type') == 'thread.started':
            thread_seen = True
        elif (not thread_seen or row.get('type') != 'item.completed'
                or not isinstance(row.get('item'), dict) or row['item'].get('type') != 'error'):
            return 'invalid_startup_event'
    return None


def _terminal_context(stdout, stderr, exit_code, final_file):
    """Inspect a bounded stream, including valid rows after a malformed row."""
    events, invalid, reason = [], 0, None
    try:
        body = stdout.encode('utf-8')
    except (AttributeError, UnicodeError):
        body = stdout.encode('utf-8', errors='surrogatepass') if isinstance(stdout, str) else b''
        reason = 'invalid_encoding'
    if len(body) > MAX_BYTES:
        reason = 'stream_exceeds_bound'
    inspected, event_truncated = 0, False
    for line in body[:MAX_BYTES].decode('utf-8', errors='ignore').splitlines():
        if not line.strip():
            continue
        if inspected >= MAX_EVENTS:
            reason = reason or 'event_count_exceeds_bound'
            event_truncated = True
            break
        inspected += 1
        try:
            row = parse(line)
            if not isinstance(row, dict):
                invalid += 1
                reason = reason or 'nonobject_event'
            else:
                events.append(row)
        except (ValueError, UnicodeError, RecursionError):
            invalid += 1
            reason = reason or 'invalid_event_stream'
    turns = [row for row in events if row.get('type') == 'turn.completed']
    starts = [row for row in events if row.get('type') == 'turn.started']
    unknown_events = [row.get('type') for row in events if not isinstance(row.get('type'), str) or row['type'] not in _EVENT_TYPES]
    bad = [row for row in events if row.get('type') in ('error', 'turn.failed')]
    items = [row['item'] for row in events if isinstance(row.get('type'), str)
             and row['type'].startswith('item.') and isinstance(row.get('item'), dict)]
    malformed_items = sum(row.get('type') in ('item.started', 'item.updated', 'item.completed')
                          and not isinstance(row.get('item'), dict) for row in events)
    unexpected_errors = sum(('error' in row and row.get('type') not in ('error', 'turn.failed'))
                            or isinstance(row.get('item'), dict) and 'error' in row['item'] for row in events)
    host = [item.get('type') for item in items if item.get('type') not in ('agent_message', 'reasoning', 'error')]
    errors = items + bad + [row['error'] for row in events + items if isinstance(row.get('error'), dict)]
    codes = [row.get('code', row.get('error_code')) for row in errors if isinstance(row, dict)]
    codes = [code for code in codes if code is not None]
    code_set = {code for code in codes if isinstance(code, str)}
    statuses = [row.get('http_status', row.get('status_code')) for row in errors if isinstance(row, dict)]
    statuses = [value for value in statuses if type(value) is int and 100 <= value <= 599]
    auth_status = next((value for value in statuses if value in (401, 403)), None)
    status = auth_status if auth_status is not None else next(iter(statuses), None)
    diagnostic = {'exit_code': exit_code, 'turn_count': len(turns), 'turn_started_count': len(starts),
        'parsed_event_count': len(events), 'invalid_event_count': invalid, 'typed_error_count': len(bad),
        'malformed_item_count': malformed_items, 'unexpected_error_payload_count': unexpected_errors,
        'warning_count': sum(item.get('type') == 'error' for item in items), 'http_status': status,
        'host_tool_types': host, 'unknown_event_types': unknown_events, 'explicit_error_codes': codes,
        'no_fallback': True, 'stream_bytes': len(body), 'stream_truncated': len(body) > MAX_BYTES,
        'event_count_truncated': event_truncated,
        'final_file_present': isinstance(final_file, str), 'refusal_present': any(bool(item.get('refusal')) for item in items),
        'stdout_sha256': sha(body), 'stderr_sha256': sha(stderr.encode('utf-8', errors='surrogatepass')) if isinstance(stderr, str) else None}
    diagnostic['event_order'] = [_event_label(row) for row in events[:MAX_ORDER_EVENTS]]
    diagnostic['event_order_omitted'] = max(0, len(events) - MAX_ORDER_EVENTS)
    diagnostic['event_order_complete'] = reason is None and invalid == 0 and len(events) <= MAX_ORDER_EVENTS
    diagnostic['error_observations'], diagnostic['error_observations_omitted'] = _error_observations(events)
    if isinstance(final_file, str):
        diagnostic['final_file_sha256'] = sha(final_file.encode('utf-8', errors='surrogatepass'))
    if reason:
        diagnostic['parse_failure'] = reason
    stop = ('codex_auth_or_permission_stop' if code_set & _AUTH_CODES or auth_status is not None else
            'codex_safety_refusal' if code_set & _SAFETY_CODES or diagnostic['refusal_present'] else None)
    return events, diagnostic, turns[0].get('usage') if len(turns) == 1 else None, stop


def _failure(diagnostic, observed_usage, kind, stage, error=None, *, verified_final_usage=None):
    return TransportError(safe_diagnostic({**diagnostic, 'kind': kind, 'failure_stage': stage,
        'no_fallback': True,
        'exception_type': type(error).__name__ if error is not None else None}),
        verified_final_usage=verified_final_usage, observed_terminal_usage=observed_usage)


def _error_disposition(events, verified_cli_version):
    """JSONL drops will_retry; only the verified CLI's exit0 supplies it.

    The second error emitter is JSON serialization fallback. It never sets the
    CLI fatal flag, so its fixed prefix must stay fatal even with a final turn.
    """
    if verified_cli_version != RECOVERABLE_ERROR_CLI_VERSION:
        return 'unverified_error_stop'
    types = [row.get('type') for row in events]
    start, end = types.index('turn.started'), types.index('turn.completed')
    for index, row in enumerate(events):
        if row.get('type') != 'error':
            continue
        message = row.get('message')
        if set(row) != {'type', 'message'} or not isinstance(message, str) or not message.strip():
            return 'malformed_error_stop'
        try:
            message.encode('utf-8')
        except UnicodeError:
            return 'malformed_error_stop'
        if message.lstrip().startswith(_SERIALIZATION_ERROR_PREFIX):
            return 'serialization_failure_stop'
        if not start < index < end:
            return 'error_outside_active_turn'
    return 'recovered_under_verified_cli'


def _verified_terminal_usage(events, diagnostic, observed, stop, verified_cli_version):
    """Validate terminal proof before considering answer contents or local files.

    Late answer failures remain failures. Only the pinned CLI's complete,
    ordered, error-checked terminal may separately establish its token usage.
    """
    if stop:
        raise _failure(diagnostic, observed, stop, 'terminal')
    if diagnostic.get('parse_failure') or not events:
        raise _failure(diagnostic, observed, 'codex_event_stream_failure', 'event_stream')
    if (diagnostic['exit_code'] or any(row.get('type') == 'turn.failed' for row in events) or diagnostic['turn_count'] != 1
            or diagnostic['turn_started_count'] != 1 or diagnostic['host_tool_types'] or diagnostic['unknown_event_types']
            or diagnostic['malformed_item_count'] or diagnostic['unexpected_error_payload_count']):
        raise _failure(diagnostic, observed, 'codex_typed_terminal_failure', 'terminal')
    if diagnostic['typed_error_count']:
        disposition = _error_disposition(events, verified_cli_version)
        diagnostic['error_disposition'] = disposition
        if disposition != 'recovered_under_verified_cli':
            raise _failure(diagnostic, observed, 'codex_typed_terminal_failure', 'terminal')
    violation = _order_violation(events)
    if violation:
        diagnostic['order_violation'] = violation
        raise _failure(diagnostic, observed, 'codex_typed_terminal_failure', 'terminal_order')
    usage = project_usage_evidence(observed)[0] if verified_cli_version == RECOVERABLE_ERROR_CLI_VERSION else None
    if verified_cli_version == RECOVERABLE_ERROR_CLI_VERSION:
        diagnostic['usage_evidence'] = 'verified_single_final_turn' if usage is not None else 'invalid_final_usage'
    if diagnostic['typed_error_count']:
        diagnostic.update(error_contract_version=verified_cli_version,
                          recovered_error_count=diagnostic['typed_error_count'])
    return usage


def decode(stdout, stderr, exit_code, final_file, *, verified_cli_version=None):
    events, diagnostic, observed, stop = _terminal_context(stdout, stderr, exit_code, final_file)
    usage = _verified_terminal_usage(events, diagnostic, observed, stop, verified_cli_version)
    texts = [row['item'].get('text') for row in events if row.get('type') == 'item.completed'
             and isinstance(row.get('item'), dict) and row['item'].get('type') == 'agent_message']
    if not texts or not isinstance(texts[-1], str) or not isinstance(final_file, str):
        raise _failure(diagnostic, observed, 'codex_final_schema_failure', 'final_message_missing', verified_final_usage=usage)
    # The documented last-message file binds the final agent message. Earlier
    # commentary is never parsed as protocol or repaired into a schema result.
    try:
        value, file_value = parse(texts[-1]), parse(final_file)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise _failure(diagnostic, observed, 'codex_final_schema_failure', 'final_message_parse', error,
                       verified_final_usage=usage) from None
    if not isinstance(value, dict) or value != file_value:
        raise _failure(diagnostic, observed, 'codex_final_schema_failure', 'final_message_binding', verified_final_usage=usage)
    return {'wire_structured_output': value, 'usage': observed,
            'codex_terminal': {'type': 'turn.completed', 'turn_count': 1,
                               'warning_count': diagnostic['warning_count']},
            'transport_diagnostic': safe_diagnostic({**diagnostic, 'kind': 'codex_typed_terminal_success', 'no_fallback': False}),
            'item_warning_details_unknown': diagnostic['warning_count'] > 0,
            'earlier_agent_message_count': len(texts) - 1,
            'stdout_sha256': diagnostic['stdout_sha256'], 'stderr_sha256': diagnostic['stderr_sha256'],
            'final_file_sha256': sha(final_file.encode()), 'host_tool_event_count': 0,
            'provider_request_count': None, 'internal_retries': None,
            'actual_cost_usd': None, 'model_reported': None}


def interpreter_binding(executable, node_executable=None, node_executable_sha256=None):
    """Bind the interpreter required by the approved npm CLI entry point.

    Native CLIs need no Node setting. A Node shebang must not discover an
    arbitrary interpreter from the host PATH after resolving the CLI symlink.
    """
    with Path(executable).resolve().open('rb') as handle:
        requires_node = handle.readline(256).strip() == b'#!/usr/bin/env node'
    if not requires_node:
        if node_executable is not None or node_executable_sha256 is not None:
            raise ValueError('Unused Node interpreter configuration for native CLI')
        return {'kind': 'native_or_os_executable'}
    if node_executable is None or not Path(node_executable).is_absolute():
        raise ValueError('Pinned Node interpreter required for npm CLI')
    node = Path(node_executable).resolve()
    if (not node.is_file() or not os.access(node, os.X_OK)
            or sha(node.read_bytes()) != node_executable_sha256):
        raise ValueError('Pinned Node interpreter unavailable or changed')
    return {'kind': 'node', 'executable_sha256': node_executable_sha256}


def codex_infer(messages, original_schema, *, model, mark_launch, executable, auth_file,
                timeout=180, effort="medium", proxy_env=None, node_executable=None,
                node_executable_sha256=None, selected_facts_sha256=None):
    CODEX = Path(executable).resolve()
    interpreter = interpreter_binding(CODEX, node_executable, node_executable_sha256)
    command = [str(Path(node_executable).resolve()), str(CODEX)] if interpreter['kind'] == 'node' else [str(CODEX)]
    runtime_path = ([str(Path(node_executable).resolve().parent)] if interpreter['kind'] == 'node' else []) + [str(CODEX.parent), '/usr/bin', '/bin']
    # Original codec checks role ordering, actual tool-result IDs and bounds.
    prompt, wire, projection = prepare_cli_request(messages, original_schema,
        selected_facts_sha256=selected_facts_sha256)
    with tempfile.TemporaryDirectory(prefix='ta-codex-typed-') as td:
        path = Path(td)
        for name in ('home', 'codex', 'cwd'):
            (path / name).mkdir(mode=0o700)
        # Same authorized credential indirection as the existing control. No
        # credential value is read here or passed to the graph worker.
        (path / 'codex/auth.json').symlink_to(Path(auth_file).resolve())
        schema_path, answer_path = path / 'schema.json', path / 'answer.json'
        schema_path.write_text(canonical(wire))
        env = {'PATH': ':'.join(runtime_path), 'HOME': str(path / 'home'),
               'CODEX_HOME': str(path / 'codex'), 'LANG': 'en_US.UTF-8', 'TMPDIR': td,
               **(proxy_env or {})}
        assert effort in ('low','medium','high')
        settings = ['-c', f'model_reasoning_effort="{effort}"', '-c', 'approval_policy="never"',
                    '-c', 'web_search="disabled"', '-c', 'project_doc_max_bytes=0', '-c', 'mcp_servers={}',
                    '-c', 'history.persistence="none"', '-c', 'memories.generate_memories=false',
                    '-c', 'features.skip_host_skill_discovery=true']
        for flag in DISABLE:
            settings += ['--disable', flag]
        # The npm wrapper and pinned Node can remain unchanged while its native
        # package changes. Verify the actual invoked CLI before any model start.
        try:
            version = subprocess.run(command + ['--version'], env=env, cwd=path / 'cwd',
                capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('Codex CLI contract version preflight failed before inference') from None
        if (version.returncode or not isinstance(version.stdout, str)
                or version.stdout.strip() != 'codex-cli ' + RECOVERABLE_ERROR_CLI_VERSION):
            raise ValueError('Codex CLI contract version mismatch before inference')
        pre = subprocess.run(command + ['features', 'list'] + settings,
            env=env, cwd=path / 'cwd', capture_output=True, text=True, timeout=15)
        flags = {line.split()[0]: line.split()[-1] for line in pre.stdout.splitlines() if line.split()}
        if pre.returncode or any(flags.get(flag) != 'false' for flag in DISABLE):
            raise ValueError('Codex isolation preflight failed before inference')
        args = command + ['--no-daemon', 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral',
                '--skip-git-repo-check', '--strict-config', '--sandbox', 'read-only', '--cd', str(path / 'cwd'),
                '--model', model, '--color', 'never', '--json', '--output-schema', str(schema_path),
                '--output-last-message', str(answer_path)] + settings + ['-']
        mark_launch()
        try:
            stdout, stderr, code = launch(args, prompt, env=env, cwd=path / 'cwd', timeout=timeout)
        except TransportError as error:
            error.metadata = safe_diagnostic(error.metadata)
            error.diagnostic = error.metadata
            raise
        except Exception as error:
            raise _failure({}, None, 'codex_process_failure', 'process_launch_or_drain', error) from None
        try:
            final = answer_path.read_text() if answer_path.exists() else None
        except (OSError, UnicodeError) as error:
            events, diagnostic, observed, stop = _terminal_context(stdout, stderr, code, None)
            diagnostic['final_file_present'] = True
            usage = _verified_terminal_usage(events, diagnostic, observed, stop, RECOVERABLE_ERROR_CLI_VERSION)
            raise _failure(diagnostic, observed, 'codex_final_file_failure', 'final_file_read', error,
                           verified_final_usage=usage) from None
        result = decode(stdout, stderr, code, final, verified_cli_version=RECOVERABLE_ERROR_CLI_VERSION)
        try:
            value, omissions = restore_original_omissions(result.pop('wire_structured_output'), original_schema)
        except Exception as error:
            raise _failure(result['transport_diagnostic'], result.get('usage'), 'codex_original_schema_failure',
                           'original_schema_restore', error,
                           verified_final_usage=project_usage_evidence(result.get('usage'))[0]) from None
    result.update(structured_output=value, wire_schema_sha256=sha(canonical(wire).encode()),
                  original_schema_sha256=sha(canonical(original_schema).encode()),
                  schema_projection=projection, wire_null_omitted_original_keys=omissions,
                  request_prompt_sha256=sha(prompt.encode()), messages_sha256=sha(canonical(messages).encode()),
                  # This is the original ordered message codec check; the
                  # optional evidence representation has its own strict
                  # canonical restoration check before any launch.
                  ordered_transcript_roundtrip=json.loads(encode_cli_transcript(messages))['messages'] == messages,
                  model_requested=model, protocol='codex_cli_output_schema_v1')
    return result
