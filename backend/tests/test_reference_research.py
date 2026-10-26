"""Real DuckDB fixture, immutable saved reads and no production selection writes."""
import asyncio
from datetime import datetime, timedelta, timezone
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
