"""Real DuckDB fixture, immutable saved reads and no production selection writes."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json

import duckdb
import pytest

from app.core import bjtime
from app.picks import reference_research as owner
from app.picks.selection_history import SavedRunMissing


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(bjtime, 'beijing_now', lambda: datetime(2026, 10, 26, 8, tzinfo=bjtime.BJ_TZ))
    path = tmp_path / 'market.duckdb'
    # Actual marketdb stores daily stamps at UTC midnight, Beijing 08:00.
    days = [(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)) for i in range(65)]
    with duckdb.connect(str(path)) as db:
        db.execute('CREATE TABLE daily_k(thscode VARCHAR,date_ms BIGINT,volume DOUBLE)')
        db.execute('CREATE TABLE daily_k_adj(thscode VARCHAR,date_ms BIGINT,close_adj DOUBLE)')
        for symbol in ('000001.SZ', '600000.SH', '000001.SH'):
            db.executemany('INSERT INTO daily_k VALUES(?,?,?)', [(symbol, int(day.timestamp()*1000), 100+i) for i, day in enumerate(days)])
            if symbol != '600000.SH':
                db.executemany('INSERT INTO daily_k_adj VALUES(?,?,?)', [(symbol, int(day.timestamp()*1000), 10+i) for i, day in enumerate(days)])
    (tmp_path / 'quality_report.json').write_text(json.dumps({'available': True, 'status': 'ok', 'issues': [], 'synced_at': '2026-10-23'}))
    return path, tmp_path / 'runs', days[-1].date().isoformat()


def run(source, **kwargs):
    path, root, through = source
    return owner.run_batch({'trade_date': through}, db_path=path, root=root, **kwargs)


def detail(source, identity, **kwargs):
    return owner.get_run(identity['method'], identity['run_id'], root=source[1],
        trade_date=identity['trade_date'], content_version=identity['content_version'],
        source_version=identity['source_version'], **kwargs)


def test_three_rules_shared_saved_denominator_unknown_missing_adjusted_and_exclude_index(source):
    result = run(source)
    assert len(result['runs']) == 3 and result['reused'] is False
    assert len({item['run_id'] for item in result['runs']}) == 1
    for identity in result['runs']:
        data = detail(source, identity)
        assert data['counts']['universe'] == 2
        assert data['counts']['unknown'] == 1
        assert data['counts']['matched'] + data['counts']['not_matched'] == 1
        missing = next(row for row in data['items'] if row['symbol'] == '600000')
        assert missing['state'] == 'unknown'
        assert 'invalid_or_missing_close' in missing['reason_codes']
        assert all(row['execution_eligible'] is False and row['score_contribution'] is False for row in data['items'])
    matched = detail(source, next(i for i in result['runs'] if i['method'].endswith('ma_bullish')))
    assert matched['counts']['matched'] == 1
    assert owner.list_runs(matched['method'], root=source[1])['items'][0]['content_version'] == matched['content_version']
    zero = detail(source, next(i for i in result['runs'] if i['method'].endswith('ma_bullish')), row_state='not_matched')
    assert zero['row_state'] == 'not_matched' and zero['items'] == []
    assert zero['coverage']['total'] == zero['coverage']['returned'] == 0
    assert zero['coverage']['batch_total'] == zero['counts']['universe'] == 2
    assert zero['counts']['unknown'] == 1 and zero['coverage']['next_offset'] is None


def test_repeat_reuses_original_batch_without_rule_call(source, monkeypatch):
    first = run(source)
    original = next(source[1].glob('*.json')).read_bytes()
    monkeypatch.setattr(owner, 'evaluate_reference_rules', lambda *a, **k: pytest.fail('must reuse saved answers'))
    second = run(source)
    assert second['reused'] and second['runs'] == first['runs']
    assert next(source[1].glob('*.json')).read_bytes() == original


def test_saved_read_needs_no_marketdb_or_current_rule_replay(source, monkeypatch):
    identity = run(source)['runs'][0]
    monkeypatch.setattr(owner, 'evaluate_reference_rules', lambda *a, **k: pytest.fail('read must not run rules'))
    source[0].unlink()
    assert detail(source, identity)['state'] == 'saved'
    with pytest.raises(SavedRunMissing):
        detail(source, {**identity, 'content_version': 'a'*64})
    with pytest.raises(SavedRunMissing):
        detail(source, {**identity, 'trade_date': '2026-01-01'})
    with pytest.raises(SavedRunMissing):
        detail(source, {**identity, 'run_id': '../bad'})


def test_saved_corruption_not_skipped_or_fallback_to_latest(source):
    run(source)
    file = next(source[1].glob('*.json'))
    file.write_text(file.read_text().replace('matched', 'tampered', 1))
    with pytest.raises(SavedRunMissing):
        owner.list_runs(owner.METHODS[0], root=source[1])


def test_quality_warning_quarantines_named_symbol_not_whole_market(source):
    path, _, through = source
    day_ms = int(datetime.fromisoformat(through).replace(tzinfo=timezone.utc).timestamp()*1000)
    report = path.parent / 'quality_report.json'
    report.write_text(json.dumps({'available': True, 'status': 'warn', 'issues': [{
        'check': 'daily_k_adj.single_day_jump', 'severity': 'warn', 'sample': [['000001.SZ', day_ms, 40]]}]}))
    for identity in run(source)['runs']:
        assert detail(source, identity)['counts']['unknown'] == 2


def test_quality_error_or_future_missing_session_fail_closed(source):
    path, root, _ = source
    with pytest.raises(ValueError, match='已完成'):
        owner.run_batch({'trade_date': '2026-10-26'}, db_path=path, root=root)
    with pytest.raises(ValueError, match='没有源交易日'):
        owner.run_batch({'trade_date': '2026-04-01'}, db_path=path, root=root)
    with pytest.raises(ValueError, match='只接受'):
        owner.run_batch({'path': 'unsafe'}, db_path=path, root=root)
    (path.parent/'quality_report.json').write_text(json.dumps({'available': True, 'status': 'fail'}))
    with pytest.raises(ValueError, match='未通过'):
        run(source)
    assert not list(root.glob('*.json'))


def test_cancel_before_scan_leaves_no_partial_success(source):
    count = 0

    def cancel():
        nonlocal count
        count += 1
        if count > 1:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        run(source, checkpoint=cancel)
    assert not list(source[1].glob('*.json'))


def test_source_change_during_scan_does_not_publish_mixed_snapshot(source, monkeypatch):
    original = owner.evaluate_reference_rules
    def changing(*args, **kwargs):
        result = original(*args, **kwargs)
        (source[0].parent/'quality_report.json').write_text('{"available":true,"status":"ok","issues":[],"new":1}')
        return result
    monkeypatch.setattr(owner, 'evaluate_reference_rules', changing)
    with pytest.raises(RuntimeError, match='源版本变化'):
        run(source)
    assert not list(source[1].glob('*.json'))


def test_registry_only_three_explicit_research_adapters_and_original_production_specs():
    from app.picks._strategy_reference_catalog import reference_catalog_entries
    from app.picks.strategy_registry import SPECS

    rows = reference_catalog_entries()
    assert len(rows) == 35 and len({row['key'] for row in rows}) == 35
    runnable = [row for row in rows if row['candidate']['runnable']]
    assert {row['key'] for row in runnable} == set(owner.METHODS)
    for row in runnable:
        assert row['status'] == 'research_ready' and not row['evaluable']
        assert row['candidate']['execution']['production_enabled'] is False
        assert row['candidate']['execution']['definition_digest'] == owner.DEFINITION_DIGEST
        assert row['candidate']['execution']['definition']
        assert row['discovery']['supports_saved_runs']
    assert len([row for row in rows if row['candidate'].get('adoption', {}).get('state') == 'reuse_existing']) == 6
    assert len([row for row in rows if row['candidate']['asset'] == 'etf' and not row['candidate']['runnable']]) == 4
    assert [spec.key for spec in SPECS] == ['daily_picks', 'intraday_watch', 'pullback_reversal', 'triple_volume', 'two_thirty_five']


@pytest.fixture
def partitioned_saved_run(tmp_path, monkeypatch):
    """Synthetic saved rows reproduce the real 353/146/5081 partition, not strategy performance."""
    monkeypatch.setattr(bjtime, 'beijing_now', lambda: datetime(2026, 10, 26, 8, tzinfo=bjtime.BJ_TZ))
    rows = []
    for index, state in enumerate(['matched'] * 353 + ['unknown'] * 146 + ['not_matched'] * 5081):
        rows.append({'item_id': f'{600000+index:06}.SH', 'symbol': f'{600000+index:06}', 'state': state,
            'metrics': {'dif': None if state == 'unknown' else 0}, 'execution_eligible': False,
            'score_contribution': False, 'reason': 'saved-' + state})
    code = owner.METHODS[0].split(':', 1)[1]
    counts = {'universe': 5580, 'eligible': 5434, 'matched': 353, 'unknown': 146, 'not_matched': 5081}
    batch = {'run_id': '1' * 64, 'trade_date': '2026-10-23', 'source_version': 'fixture-source',
        'run_at': '2026-10-26T08:00:00+08:00', 'source_completed_date': '2026-10-23',
        'source_as_of': None, 'source_cutoff_at': '2026-10-23T23:59:59+08:00',
        'source_as_of_semantics': 'fixture unknown', 'definition_digest': '2' * 64,
        'data_limitations': ['synthetic saved projection fixture'], 'semantics': owner.SEMANTICS, 'elapsed_ms': 0,
        'methods': {code: {'version': 'fixture-v1', 'definition': 'saved fixture, no rule execution',
            'definition_supplement': 'not performance evidence', 'items': rows, 'counts': counts}}}
    batch['content_version'] = hashlib.sha256(owner._json(batch).encode()).hexdigest()
    root = tmp_path / 'saved'
    root.mkdir()
    path = root / (batch['run_id'] + '.json')
    path.write_text(owner._json(batch))
    monkeypatch.setattr(owner, 'evaluate_reference_rules', lambda *a, **kw: pytest.fail('saved read must not rescan rules'))
    return root, owner._identity(batch, code), path, rows, counts


def read_partition(fixture, **kwargs):
    root, identity, _path, _rows, _counts = fixture
    return owner.get_run(identity['method'], identity['run_id'], root=root,
        trade_date=identity['trade_date'], content_version=identity['content_version'],
        source_version=identity['source_version'], **kwargs)


def test_state_filter_reads_original_whole_partition_before_bounded_slice(partitioned_saved_run):
    fixture = partitioned_saved_run
    _root, identity, path, rows, counts = fixture
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    first = read_partition(fixture)
    assert first['row_state'] == 'all' and len(first['items']) == 200
    assert all(row['state'] == 'matched' for row in first['items'])
    assert first['coverage']['total'] == first['coverage']['batch_total'] == 5580
    for state, total in [('matched', 353), ('unknown', 146), ('not_matched', 5081)]:
        page = read_partition(fixture, row_state=state)
        expected = [row for row in rows if row['state'] == state]
        assert page['items'] == expected[:200] and page['row_state'] == state
        assert page['counts'] == counts and page['content_version'] == identity['content_version']
        assert page['coverage'] == {'offset': 0, 'limit': 200, 'total': total, 'batch_total': 5580,
            'returned': min(total, 200), 'next_offset': 200 if total > 200 else None,
            'possibly_truncated': total > 200, 'history_semantics': owner.SEMANTICS}
        if total > 200:
            next_page = read_partition(fixture, row_state=state, offset=200)
            assert next_page['items'] == expected[200:400] and next_page['row_state'] == state
            assert next_page['counts'] == counts and next_page['coverage']['total'] == total
            assert next_page['coverage']['batch_total'] == 5580
    unknown = read_partition(fixture, row_state='unknown')
    assert len(unknown['items']) == 146 and all(row['metrics']['dif'] is None for row in unknown['items'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash


def test_exhausted_offset_does_not_zero_the_original_denominator(partitioned_saved_run):
    exhausted = read_partition(partitioned_saved_run, row_state='unknown', offset=146)
    assert exhausted['items'] == [] and exhausted['coverage']['total'] == exhausted['counts']['unknown'] == 146
    assert exhausted['coverage']['returned'] == 0 and exhausted['coverage']['next_offset'] is None


@pytest.mark.parametrize('state', ['rejected', 'selected', '', None, []])
def test_reference_state_rejects_unsupported_scope(partitioned_saved_run, state):
    with pytest.raises(ValueError, match='row_state'):
        read_partition(partitioned_saved_run, row_state=state)


def test_content_identity_is_checked_before_state_iteration(partitioned_saved_run, monkeypatch):
    original = owner._load

    class NoIteration(list):
        def __iter__(self):
            pytest.fail('a mismatched content identity must not filter or iterate saved rows')

    def load(path):
        batch = original(path)
        for entry in batch['methods'].values():
            entry['items'] = NoIteration(entry['items'])
        return batch

    monkeypatch.setattr(owner, '_load', load)
    root, identity, _path, _rows, _counts = partitioned_saved_run
    with pytest.raises(SavedRunMissing):
        owner.get_run(identity['method'], identity['run_id'], root=root, row_state='unknown',
            trade_date=identity['trade_date'], content_version='f' * 64, source_version=identity['source_version'])


def test_reference_state_filter_in_real_router_preserves_exact_identity_and_auth(partitioned_saved_run, monkeypatch):
    from fastapi.testclient import TestClient
    from app.core.config import settings
    from app.main import app

    root, identity, _path, _rows, _counts = partitioned_saved_run
    monkeypatch.setattr(owner, 'ROOT', root)
    monkeypatch.setattr(settings, 'api_token', 'fixture-reference-token')
    path = f"/api/picks/method-runs/{identity['method']}/{identity['run_id']}"
    params = {key: identity[key] for key in ('trade_date', 'content_version', 'source_version')}
    client = TestClient(app)
    assert client.get(path, params={**params, 'row_state': 'unknown'}).status_code == 401
    headers = {'X-API-Token': 'fixture-reference-token'}
    result = client.get(path, params={**params, 'row_state': 'unknown'}, headers=headers)
    assert result.status_code == 200
    payload = result.json()['data']
    assert payload['row_state'] == 'unknown' and len(payload['items']) == 146
    assert payload['coverage']['batch_total'] == payload['counts']['universe'] == 5580
    assert client.get(path, params={**params, 'row_state': 'not_matched', 'content_version': 'f' * 64}, headers=headers).status_code == 404
    assert client.get(path, params={**params, 'row_state': 'rejected'}, headers=headers).status_code == 422
