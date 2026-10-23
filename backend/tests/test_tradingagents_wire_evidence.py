"""Lossless selected wire fixtures. No provider, original context log or DB."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.tradingagents_native import codex_transport as transport
from app.services.tradingagents_native.cli_codec import build_cli_schema, encode_cli_transcript

START = '<UNTRUSTED_FROZEN_EVIDENCE>'
END = '</UNTRUSTED_FROZEN_EVIDENCE>'
TAG = '__frozen_table_v1__'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def facts():
    return {'symbol': '600000', 'price_history': {'observations': [
        {'date': str(i), 'close': i + 0.5, 'volume': i * 100, 'available': None, 'flag': i % 2 == 0,
         'source': 'unchanged public source'} for i in range(24)]},
        'fundamentals': {'periods': [{'period': str(i), 'value': None if i % 2 else i,
                                    'published_at': '2026-09-01T15:00:00+08:00'} for i in range(8)]},
        'other': [{'same': 'outside paths remains original'}] * 4}


def messages(value=None, raw=None):
    body = canonical(facts() if value is None else value) if raw is None else raw
    return [{'role': 'system', 'content': 'Original prefix\n' + START + '\n' + body + '\n' + END + '\nOriginal suffix'},
            {'role': 'user', 'content': 'Original question'},
            {'role': 'assistant', 'content': 'Original proposal', 'tool_calls': [
                {'id': 'original-1', 'name': 'get_stock_data', 'args': {'ticker': '600000'}}]},
            {'role': 'tool', 'content': 'Original result', 'name': 'get_stock_data',
             'tool_call_id': 'original-1', 'status': 'success'}]


def encoded_messages(prompt):
    return json.loads(prompt[len(transport.NOTE + transport.FROZEN_TABLE_NOTE):])['messages']


def encoded_facts(prompt):
    content = encoded_messages(prompt)[0]['content']
    return json.loads(content.split(START, 1)[1].split(END, 1)[0])


def test_selected_roundtrip_preserves_fields_values_order_null_and_native_history():
    original = messages(); snapshot = deepcopy(original); schema = build_cli_schema()
    prompt, wire, _ = transport.prepare_cli_request(original, schema, selected_facts_sha256=digest(facts()))
    body = encoded_facts(prompt); restored = deepcopy(body['facts'])
    for parent, field in [('price_history', 'observations'), ('fundamentals', 'periods')]:
        table = restored[parent][field][TAG]
        restored[parent][field] = [dict(zip(table['columns'], row)) for row in table['rows']]
    assert restored == facts() and canonical(restored) == canonical(facts())
    assert digest(restored) == body['source_facts_sha256'] == digest(facts())
    assert encoded_messages(prompt)[1:] == original[1:] and original == snapshot
    assert encoded_messages(prompt)[0]['content'].startswith('Original prefix\n' + START)
    assert encoded_messages(prompt)[0]['content'].endswith(END + '\nOriginal suffix')
    assert wire == transport.strict_wire_schema(schema)[0]
    assert len(prompt) < len(transport.NOTE + encode_cli_transcript(original))
    assert transport.prepare_cli_request(original, schema, selected_facts_sha256=digest(facts()))[0] == prompt


def test_nonselected_is_byte_identical_and_does_not_interpret_source_markers():
    original = messages(raw='{not a trusted packet}')
    prompt, wire, projection = transport.prepare_cli_request(original, build_cli_schema())
    assert prompt == transport.NOTE + encode_cli_transcript(original)
    assert (wire, projection) == transport.strict_wire_schema(build_cli_schema())


def test_short_savings_do_not_add_more_format_overhead_to_the_actual_wire():
    value = {'price_history': {'observations': [{'long_repeated_column_name': i} for i in range(3)]}}
    original = messages(value)
    prompt, _, _ = transport.prepare_cli_request(original, build_cli_schema(), selected_facts_sha256=digest(value))
    assert prompt == transport.NOTE + encode_cli_transcript(original)


@pytest.mark.parametrize('case', ['hash', 'duplicate_key', 'nan', 'infinity', 'not_object',
    'missing_start', 'missing_end', 'two_blocks', 'split_blocks', 'tool_only', 'reserved_tag',
    'fake_wrapper', 'bad_hash', 'malformed'])
def test_selected_rejects_ambiguous_or_unbound_source_without_mutation(case):
    value = facts(); original = messages(value); binding = digest(value)
    if case == 'hash': binding = '0' * 64
    elif case == 'duplicate_key': original = messages(raw='{"symbol":"600000","symbol":"other"}')
    elif case == 'nan': original = messages(raw='{"value":NaN}')
    elif case == 'infinity': original = messages(raw='{"value":1e9999}')
    elif case == 'not_object': original = messages(raw='[]')
    elif case == 'missing_start': original[0]['content'] = original[0]['content'].replace(START, '')
    elif case == 'missing_end': original[0]['content'] = original[0]['content'].replace(END, '')
    elif case == 'two_blocks': original.append({'role': 'user', 'content': original[0]['content']})
    elif case == 'split_blocks': original[0]['content'] = START; original[1]['content'] = canonical(value) + END
    elif case == 'tool_only': original[-1]['content'] = original[0]['content']; original[0]['content'] = 'original role'
    elif case == 'reserved_tag':
        value['other'] = {TAG: {}}; original = messages(value); binding = digest(value)
    elif case == 'fake_wrapper':
        original = messages({'format': 'frozen-facts-tables-v1', 'source_facts_sha256': binding, 'facts': value})
    elif case == 'bad_hash': binding = 'not a hash'
    elif case == 'malformed': original = messages(raw='{"unfinished":')
    snapshot = deepcopy(original)
    with pytest.raises(ValueError):
        transport.prepare_cli_request(original, build_cli_schema(), selected_facts_sha256=binding)
    assert original == snapshot


@pytest.mark.parametrize('kind', ['different_keys', 'non_rows', 'short', 'elsewhere'])
def test_heterogeneous_or_unoffered_tables_remain_original(kind):
    value = facts()
    if kind == 'different_keys': value['price_history']['observations'][1]['extra'] = 1
    elif kind == 'non_rows': value['price_history']['observations'][1] = None
    elif kind == 'short': value['price_history']['observations'] = value['price_history']['observations'][:2]
    elif kind == 'elsewhere': value = {'other': value['price_history']['observations']}
    original = messages(value)
    prompt, _, _ = transport.prepare_cli_request(original, build_cli_schema(), selected_facts_sha256=digest(value))
    if kind == 'elsewhere': assert prompt == transport.NOTE + encode_cli_transcript(original)
    else:
        if prompt.startswith(transport.NOTE + transport.FROZEN_TABLE_NOTE):
            retained = encoded_facts(prompt)['facts']
        else:
            assert prompt == transport.NOTE + encode_cli_transcript(original)
            retained = value
        assert retained['price_history']['observations'] == value['price_history']['observations']


def test_preparation_matches_exact_launch_prompt_schema_and_original_response(tmp_path, monkeypatch):
    entry, auth = tmp_path / 'cli', tmp_path / 'auth'
    entry.write_bytes(b'offline fixture'); auth.write_text('credential reference fixture')
    original = messages(); schema = build_cli_schema(); binding = digest(facts())
    expected, wire, _ = transport.prepare_cli_request(original, schema, selected_facts_sha256=binding)
    launched = []
    def preflight(args, **kwargs):
        if args[-1] == '--version':
            return SimpleNamespace(returncode=0, stdout='codex-cli 0.162.1', stderr='')
        return SimpleNamespace(returncode=0, stdout='\n'.join(flag + ' stable false' for flag in transport.DISABLE), stderr='')
    def launch(args, prompt, **kwargs):
        assert prompt == expected
        assert json.loads(Path(args[args.index('--output-schema') + 1]).read_text()) == wire
        value = {'content': 'saved original result', 'tool_calls': []}
        Path(args[args.index('--output-last-message') + 1]).write_text(canonical(value))
        launched.append(prompt)
        events = [{'type': 'thread.started'}, {'type': 'turn.started'},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': canonical(value)}},
                  {'type': 'turn.completed', 'usage': {'input_tokens': 17, 'output_tokens': 3}}]
        return '\n'.join(canonical(x) for x in events), '', 0
    monkeypatch.setattr(transport.subprocess, 'run', preflight)
    monkeypatch.setattr(transport, 'launch', launch)
    result = transport.codex_infer(original, schema, selected_facts_sha256=binding,
        model='fixture', mark_launch=lambda: None, executable=entry, auth_file=auth)
    assert len(launched) == 1 and result['structured_output']['content'] == 'saved original result'
    assert result['messages_sha256'] == digest(original) and result['ordered_transcript_roundtrip']
