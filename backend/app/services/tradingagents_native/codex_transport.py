"""Server-pinned Codex CLI, tool-free/ephemeral, typed output-schema only.

The accounted driver owns budget and authorizes
every launch. This module does not retry, log full context, change credentials,
install dependencies or use the upstream's separate ChatGPT sign-in.
"""
import hashlib
import json
from pathlib import Path
import tempfile

import subprocess

from .cli_codec import encode_cli_transcript
from .transport_process import DISABLE, TransportError, launch
from .schema_projection import strict_wire_schema, restore_original_omissions

MAX_BYTES = 8 * 1024 * 1024
NOTE = ('Transport protocol only: the following lossless ordered JSON transcript contains the original graph roles, '
        'messages, tool declarations and actual tool-result history. Continue only the current assistant turn. '
        'No host tools are available. Graph functions are data declarations, never native Codex tools. '
        'For a content/tool_calls schema, express graph requests only in tool_calls with original allowed names and arguments; '
        'the original graph executes them after this process returns. Never invent tool_result or claim unexecuted tools ran. '
        'For an original structured result schema, produce that object. Null on a wire-only optional nonnullable property '
        'represents omission, not a measured value. Preserve every original fact, role, restriction and field description.\n')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


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


def decode(stdout, stderr, exit_code, final_file):
    events = [parse(line) for line in stdout.splitlines() if line.strip()]
    if len(stdout.encode()) > MAX_BYTES or not events or not all(isinstance(row, dict) for row in events):
        raise ValueError('Invalid Codex event stream')
    turns = [row for row in events if row.get('type') == 'turn.completed']
    starts = [row for row in events if row.get('type') == 'turn.started']
    allowed_events = {'thread.started', 'turn.started', 'turn.completed', 'turn.failed',
                      'item.started', 'item.updated', 'item.completed', 'error'}
    unknown_events = sorted({str(row.get('type')) for row in events if row.get('type') not in allowed_events})
    bad = [row for row in events if row.get('type') in ('error', 'turn.failed')]
    items = [row['item'] for row in events if row.get('type', '').startswith('item.') and isinstance(row.get('item'), dict)]
    host = [item.get('type') for item in items if item.get('type') not in ('agent_message', 'reasoning', 'error')]
    errors = items + bad + [row.get('error') for row in bad if isinstance(row.get('error'), dict)]
    codes = [row.get('code', row.get('error_code')) for row in errors if isinstance(row, dict)]
    codes = {code for code in codes if isinstance(code, str)}
    auth_codes = {'authentication_failed', 'authorization_failed', 'invalid_api_key', 'unauthorized', 'permission_denied', 'account_deactivated'}
    safety_codes = {'safety_refusal', 'content_policy_violation', 'content_filter', 'policy_violation'}
    statuses = [row.get('http_status', row.get('status_code')) for row in errors if isinstance(row, dict)]
    statuses = [value for value in statuses if isinstance(value, int) and not isinstance(value, bool)]
    auth_status = next((value for value in statuses if value in (401, 403)), None)
    status = auth_status if auth_status is not None else next(iter(statuses), None)
    if codes & auth_codes or auth_status is not None:
        raise TransportError({'kind': 'codex_auth_or_permission_stop', 'http_status': status,
                              'explicit_error_codes': sorted(codes & auth_codes), 'no_fallback': True})
    if codes & safety_codes or any(item.get('refusal') for item in items):
        raise TransportError({'kind': 'codex_safety_refusal', 'no_fallback': True})
    if exit_code or bad or len(turns) != 1 or len(starts) != 1 or host or unknown_events:
        raise TransportError({'kind': 'codex_typed_terminal_failure', 'exit_code': exit_code,
                              'turn_count': len(turns), 'turn_started_count': len(starts), 'host_tool_types': host,
                              'unknown_event_types': unknown_events, 'http_status': status,
                              'explicit_error_codes': sorted(codes), 'no_fallback': True,
                              'stdout_sha256': sha(stdout.encode()), 'stderr_sha256': sha(stderr.encode())})
    texts = [row['item'].get('text') for row in events if row.get('type') == 'item.completed'
             and isinstance(row.get('item'), dict) and row['item'].get('type') == 'agent_message']
    if not texts or not isinstance(texts[-1], str) or not isinstance(final_file, str):
        raise ValueError('Schema-shaped final message and output file required')
    # The documented last-message file binds the final agent message. Earlier
    # commentary is never parsed as protocol or repaired into a schema result.
    value, file_value = parse(texts[-1]), parse(final_file)
    if not isinstance(value, dict) or value != file_value:
        raise ValueError('Codex final file/message mismatch or nonobject result')
    return {'wire_structured_output': value, 'usage': turns[0].get('usage'),
            'codex_terminal': {'type': 'turn.completed', 'turn_count': 1,
                               'warning_count': sum(item.get('type') == 'error' for item in items)},
            'item_warning_details_unknown': any(item.get('type') == 'error' for item in items),
            'earlier_agent_message_count': len(texts) - 1,
            'stdout_sha256': sha(stdout.encode()), 'stderr_sha256': sha(stderr.encode()),
            'final_file_sha256': sha(final_file.encode()), 'host_tool_event_count': 0,
            'provider_request_count': None, 'internal_retries': None,
            'actual_cost_usd': None, 'model_reported': None}


def codex_infer(messages, original_schema, *, model, mark_launch, executable, auth_file,
                timeout=180, effort="medium", proxy_env=None):
    CODEX = Path(executable).resolve()
    # Original codec checks role ordering, actual tool-result IDs and bounds.
    transcript = encode_cli_transcript(messages)
    prompt = NOTE + transcript
    wire, projection = strict_wire_schema(original_schema)
    with tempfile.TemporaryDirectory(prefix='ta-codex-typed-') as td:
        path = Path(td)
        for name in ('home', 'codex', 'cwd'):
            (path / name).mkdir(mode=0o700)
        # Same authorized credential indirection as the existing control. No
        # credential value is read here or passed to the graph worker.
        (path / 'codex/auth.json').symlink_to(Path(auth_file).resolve())
        schema_path, answer_path = path / 'schema.json', path / 'answer.json'
        schema_path.write_text(canonical(wire))
        env = {'PATH': str(CODEX.parent) + ':/usr/bin:/bin', 'HOME': str(path / 'home'),
               'CODEX_HOME': str(path / 'codex'), 'LANG': 'en_US.UTF-8', 'TMPDIR': td,
               **(proxy_env or {})}
        assert effort in ('low','medium','high')
        settings = ['-c', f'model_reasoning_effort="{effort}"', '-c', 'approval_policy="never"',
                    '-c', 'web_search="disabled"', '-c', 'project_doc_max_bytes=0', '-c', 'mcp_servers={}',
                    '-c', 'history.persistence="none"', '-c', 'memories.generate_memories=false',
                    '-c', 'features.skip_host_skill_discovery=true']
        for flag in DISABLE:
            settings += ['--disable', flag]
        pre = subprocess.run([str(CODEX), 'features', 'list'] + settings,
            env=env, cwd=path / 'cwd', capture_output=True, text=True, timeout=15)
        flags = {line.split()[0]: line.split()[-1] for line in pre.stdout.splitlines() if line.split()}
        if pre.returncode or any(flags.get(flag) != 'false' for flag in DISABLE):
            raise ValueError('Codex isolation preflight failed before inference')
        args = [str(CODEX), '--no-daemon', 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral',
                '--skip-git-repo-check', '--strict-config', '--sandbox', 'read-only', '--cd', str(path / 'cwd'),
                '--model', model, '--color', 'never', '--json', '--output-schema', str(schema_path),
                '--output-last-message', str(answer_path)] + settings + ['-']
        mark_launch()
        stdout, stderr, code = launch(args, prompt, env=env, cwd=path / 'cwd', timeout=timeout)
        final = answer_path.read_text() if answer_path.exists() else None
        result = decode(stdout, stderr, code, final)
    value, omissions = restore_original_omissions(result.pop('wire_structured_output'), original_schema)
    result.update(structured_output=value, wire_schema_sha256=sha(canonical(wire).encode()),
                  original_schema_sha256=sha(canonical(original_schema).encode()),
                  schema_projection=projection, wire_null_omitted_original_keys=omissions,
                  request_prompt_sha256=sha(prompt.encode()), messages_sha256=sha(canonical(messages).encode()),
                  ordered_transcript_roundtrip=json.loads(transcript)['messages'] == messages,
                  model_requested=model, protocol='codex_cli_output_schema_v1')
    return result
