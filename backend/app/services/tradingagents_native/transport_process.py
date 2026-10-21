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


class TransportError(RuntimeError):
    def __init__(self, diagnostic, *, verified_final_usage=None):
        self.diagnostic = diagnostic
        # Only the bounded timeout parser supplies this separately verified
        # projection. An arbitrary diagnostic['usage'] is not an accounting fact.
        self.usage = verified_final_usage
        self.final_usage_verified = verified_final_usage is not None
        self.metadata = dict(diagnostic)
        super().__init__(diagnostic['kind'])


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _project_usage(value):
    """Codex single-turn inclusive input; no partial or cumulative fallback."""
    if not isinstance(value, dict):
        return None
    names = ('input_tokens', 'output_tokens', 'cached_input_tokens',
             'cache_creation_input_tokens', 'reasoning_output_tokens')
    if set(value) - set(names):
        return None
    result = {name: value[name] for name in names if name in value}
    if not {'input_tokens', 'output_tokens'} <= result.keys():
        return None
    if any(type(item) is not int or not 0 <= item <= 2**63 - 1 for item in result.values()):
        return None
    if (result.get('cached_input_tokens', 0) + result.get('cache_creation_input_tokens', 0)
            > result['input_tokens'] or result.get('reasoning_output_tokens', 0) > result['output_tokens']):
        return None
    return result


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
    """
    events, reason = [], None
    try:
        body = stdout.encode('utf-8')
    except (AttributeError, UnicodeError):
        return {'usage': None, 'usage_evidence': 'invalid_encoding'}, None
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
    errors = items + bad + [row['error'] for row in events if isinstance(row.get('error'), dict)]
    codes = {row.get('code', row.get('error_code')) for row in errors
             if isinstance(row.get('code', row.get('error_code')), str)}
    statuses = [row.get('http_status', row.get('status_code')) for row in errors]
    auth_status = next((status for status in statuses if type(status) is int and status in (401, 403)), None)
    result = {'usage': None, 'turn_started_count': sum(row.get('type') == 'turn.started' for row in events),
              'turn_count': sum(row.get('type') == 'turn.completed' for row in events)}
    if codes & _AUTH_CODES or auth_status is not None:
        result.update(kind='codex_auth_or_permission_stop', http_status=auth_status,
                      explicit_error_codes=sorted(codes & _AUTH_CODES), no_fallback=True,
                      usage_evidence='explicit_authorization_stop')
        return result, None
    if codes & _SAFETY_CODES or any(item.get('refusal') for item in items):
        result.update(kind='codex_safety_refusal', no_fallback=True, usage_evidence='explicit_safety_stop')
        return result, None
    host = any(item.get('type') not in ('agent_message', 'reasoning', 'error') for item in items)
    unknown = any(not isinstance(row.get('type'), str) or row['type'] not in _EVENTS for row in events)
    malformed_item = any(row.get('type') in ('item.started', 'item.updated', 'item.completed')
                         and not isinstance(row.get('item'), dict) for row in events)
    if host or unknown or malformed_item:
        result.update(kind='codex_typed_terminal_failure', no_fallback=True,
                      host_tool_types=['unrecognized_item_type'] if host or malformed_item else [],
                      unknown_event_types=['unrecognized_event_type'] if unknown else [],
                      usage_evidence='unrecognized_typed_event')
        return result, None
    if reason or bad or any('error' in row for row in events) or any(item.get('type') == 'error' for item in items):
        result['usage_evidence'] = reason or 'typed_error_present'
        return result, None
    types = [row.get('type') for row in events]
    if (result['turn_started_count'] != 1 or result['turn_count'] != 1
            or types[-1:] != ['turn.completed'] or types.index('turn.started') >= types.index('turn.completed')
            or any(kind != 'thread.started' for kind in types[:types.index('turn.started')])):
        result['usage_evidence'] = 'single_final_turn_not_established'
        return result, None
    usage = _project_usage(events[-1].get('usage'))
    result.update(usage=usage, usage_evidence='verified_single_final_turn' if usage is not None else 'invalid_final_usage')
    return result, usage

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
        evidence, usage = timeout_evidence(out)
        raise TransportError({'kind':'timeout', **evidence, 'timed_out': True,
            'termination_requested': True, 'kill_requested': killed,
            'local_drain_complete': True, 'exit_code': proc.returncode,
            'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),
            'stderr_sha256':hashlib.sha256(err.encode()).hexdigest()}, verified_final_usage=usage) from None
    return out,err,proc.returncode
