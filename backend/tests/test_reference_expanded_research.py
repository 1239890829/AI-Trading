"""Independent frozen hypotheses and exact-date reference outcomes, never fills."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import duckdb
import pytest

from app.picks._strategy_reference_rules import BASE_RULES, EXTRA_RULES, evaluate_reference_rules
from app.picks.reference_research import _add_outcome, _outcome_counters, _outcome_summary
from app.picks import reference_research as owner
from app.core import bjtime


def bars():
    return [{'date': (date(2025, 1, 1)+timedelta(days=i)).isoformat(), 'close': 100., 'open': 100.,
             'high': 101., 'low': 99., 'volume': 100., 'turnover': 1000.} for i in range(91)]


@pytest.mark.parametrize('method', EXTRA_RULES)
def test_every_new_hypothesis_has_a_real_positive_fixture_and_fails_a_condition(method):
    rows = bars()
    if method == 'rsi_reversal':
        for row, close in zip(rows[-16:-1], range(114, 99, -1)):
            row['close'] = close
        rows[-1]['close'] = 110
    elif method == 'shrink_pullback':
        rows[-1]['volume'] = 50
    elif method == 'volume_breakout':
        rows[-1].update(close=103., high=104., turnover=2000.)
    elif method == 'l1_l2_volume_resonance':
        rows[-21].update(close=104., high=105., volume=250.)
        rows[-1].update(open=100., close=101., high=102., low=96., volume=400.)
    elif method == 'one_yang_three_yin':
        rows[-4].update(open=100., close=106., high=107., low=99., volume=1000.)
        for row, op, close, volume in zip(rows[-3:], [106., 105., 104.], [105., 104., 103.], [500., 400., 300.]):
            row.update(open=op, close=close, high=op+1, low=close-1, volume=volume)
    elif method == 'w_bottom_breakout':
        rows[-40].update(open=95., close=95., high=96., low=90.)
        rows[-20].update(open=95., close=95., high=96., low=91.)
        rows[-1].update(close=102., high=103., volume=200.)
    elif method == 'bottom_volume':
        for row in rows:
            row.update(open=130., close=130., high=131., low=129.)
        rows[-1].update(open=96., close=100., high=101., low=95., volume=300.)
    elif method == 'box_oscillation':
        for row in rows:
            row.update(open=105., close=105., high=110., low=100.)
        rows[-1].update(open=100., close=101., high=102., low=100.)
    result = evaluate_reference_rules(rows, through=rows[-1]['date'])[method]
    assert result['state'] == 'matched', result
    assert result['execution_eligible'] is False and result['score_contribution'] is False
    assert result['conditions'] and all(row['met'] is True for row in result['conditions'])
    broken = deepcopy(rows)
    broken[-1]['close'] = None
    assert evaluate_reference_rules(broken, through=broken[-1]['date'])[method]['state'] == 'unknown'
    future = [{'date': '2099-01-01', 'close': None, 'quality_issue': 'future'}]
    assert evaluate_reference_rules(rows+future, through=rows[-1]['date'])[method] == result


def test_original_three_versions_and_conditions_are_unchanged():
    assert {key: row['version'] for key, row in BASE_RULES.items()} == {
        'macd_golden_cross': 'ashare-macd-12-26-9-v1', 'ma_bullish': 'ashare-ma-5-10-20-v1',
        'ma_golden_cross': 'ashare-ma-cross-volume5-v1'}


def test_mature_missing_is_not_immature_loss_or_a_shifted_future_price():
    days = [f'2025-01-{i:02}' for i in range(1, 8)]
    positions = {day: i for i, day in enumerate(days)}
    prices = dict.fromkeys(days, 100.)
    prices[days[1]] = None
    prices[days[3]] = 103.
    prices[days[5]] = 105.
    counts = _outcome_counters()
    _add_outcome(counts, days[0], prices, days, positions)
    _add_outcome(counts, days[-1], prices, days, positions)
    stats = _outcome_summary(counts)
    assert stats['1'] == {'signals': 2, 'matured': 0, 'immature': 1, 'missing': 1, 'wins': 0,
                           'positive_rate': None, 'mean_return_pct': None, 'stress_mean_return_pct': None}
    assert stats['3']['matured'] == stats['3']['immature'] == 1 and stats['3']['missing'] == 0
    assert stats['3']['mean_return_pct'] == pytest.approx(3.)
    assert stats['3']['stress_mean_return_pct'] == pytest.approx(2.65)
    assert stats['5']['mean_return_pct'] == pytest.approx(5.)


def test_ohlc_changes_bind_new_rule_inputs_without_changing_original_close_volume_identity():
    rows = bars()
    first = evaluate_reference_rules(rows, through=rows[-1]['date'])
    rows[-1]['open'] = 99.5
    second = evaluate_reference_rules(rows, through=rows[-1]['date'])
    assert first['ma_bullish']['input_digest'] == second['ma_bullish']['input_digest']
    assert first['one_yang_three_yin']['input_digest'] != second['one_yang_three_yin']['input_digest']


@pytest.fixture
def market_source(tmp_path, monkeypatch):
    monkeypatch.setattr(bjtime, 'beijing_now', lambda: datetime(2026, 10, 26, 20, tzinfo=bjtime.BJ_TZ))
    path = tmp_path/'market.duckdb'
    days = [datetime(2026, 1, 1, tzinfo=timezone.utc)+timedelta(days=i) for i in range(90)]
    with duckdb.connect(str(path)) as con:
        con.execute('CREATE TABLE daily_k(thscode VARCHAR,date_ms BIGINT,volume DOUBLE,open_price DOUBLE,high_price DOUBLE,low_price DOUBLE,close_price DOUBLE,turnover DOUBLE)')
        con.execute('CREATE TABLE daily_k_adj(thscode VARCHAR,date_ms BIGINT,close_adj DOUBLE)')
        for symbol in ('000001.SZ', '600000.SH'):
            for i, day in enumerate(days):
                if symbol == '000001.SZ' and i == 83:
                    continue
                stamp, close = int(day.timestamp()*1000), 100.+i
                con.execute('INSERT INTO daily_k VALUES(?,?,?,?,?,?,?,?)', [symbol, stamp, 100., close-.5, close+1, close-1, close, 1000.])
                con.execute('INSERT INTO daily_k_adj VALUES(?,?,?)', [symbol, stamp, close])
    (tmp_path/'quality_report.json').write_text(json.dumps({'available': True, 'status': 'ok', 'issues': []}))
    return path, days, tmp_path/'runs'


def load_saved(result, root, method='reference:ma_bullish'):
    identity = next(row for row in result['runs'] if row['method'] == method)
    return owner.get_run(method, identity['run_id'], trade_date=identity['trade_date'], content_version=identity['content_version'],
                         source_version=identity['source_version'], root=root)


def test_shared_scan_measures_all_horizons_and_preserves_mature_missing(market_source):
    path, days, root = market_source
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    through = days[84].date().isoformat()
    result = owner.run_batch({'trade_date': through}, db_path=path, root=root)
    assert len(result['runs']) == 11 and hashlib.sha256(path.read_bytes()).hexdigest() == source_hash
    saved = load_saved(result, root)
    study = saved['outcomes']
    assert study['signal_through'] == through and study['outcome_through'] == through
    assert study['production_promotion_eligible'] is False
    assert study['horizons']['1']['missing'] == 1
    assert study['horizons']['1']['immature'] == 1
    assert study['horizons']['1']['matured'] + study['horizons']['1']['missing'] + study['horizons']['1']['immature'] == study['horizons']['1']['signals']
    assert set(study['horizons']) == {'1', '3', '5'}
    # Later database rows alter neither the historical signal nor its as-of statistics.
    with duckdb.connect(str(path)) as con:
        con.execute('UPDATE daily_k_adj SET close_adj=500 WHERE date_ms>?', [int(days[84].timestamp()*1000)])
    later = load_saved(owner.run_batch({'trade_date': through}, db_path=path, root=root), root)
    assert later['items'] == saved['items'] and later['counts'] == saved['counts']
    assert later['outcomes'] == study


def test_new_methods_skip_legacy_batches_while_original_three_stay_readable(market_source):
    path, days, root = market_source
    owner.run_batch({'trade_date': days[84].date().isoformat()}, db_path=path, root=root)
    file = next(root.glob('*.json'))
    batch = owner._load(file)
    batch.pop('storage_version')
    for key in BASE_RULES:
        batch['methods'][key]['items'] = owner._load(file, method=key)['methods'][key]['items']
        batch['methods'][key].pop('items_ref')
    batch['methods'] = {key: value for key, value in batch['methods'].items() if key in BASE_RULES}
    for value in batch['methods'].values():
        value.pop('outcomes')
    batch.pop('content_version')
    batch['content_version'] = hashlib.sha256(owner._json(batch).encode()).hexdigest()
    file.write_text(owner._json(batch))
    original = owner.list_runs('reference:ma_bullish', root=root)['items'][0]
    saved = owner.get_run(original['method'], original['run_id'], trade_date=original['trade_date'],
        content_version=original['content_version'], source_version=original['source_version'], root=root)
    assert saved['outcomes'] is None and saved['counts']['universe'] == 2
    assert owner.list_runs('reference:rsi_reversal', root=root)['items'] == []


def test_manifest_indexes_never_read_item_shards_and_details_read_only_the_requested_method(market_source, monkeypatch):
    path, days, root = market_source
    result = owner.run_batch({'trade_date': days[84].date().isoformat()}, db_path=path, root=root)
    manifest = json.loads(next(root.glob('*.json')).read_text())
    assert manifest['storage_version'] == 2 and all('items' not in value for value in manifest['methods'].values())
    opened, original = [], type(path).open
    def tracked(self, *args, **kwargs):
        opened.append(self.name)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(type(path), 'open', tracked)
    owner.list_runs('reference:ma_bullish', root=root)
    assert not any(name.endswith('.rows') for name in opened)
    opened.clear()
    load_saved(result, root)
    assert [name for name in opened if name.endswith('.rows')] == [manifest['methods']['ma_bullish']['items_ref']['file']]


def test_missing_or_same_size_tampered_shards_are_not_accepted(market_source):
    path, days, root = market_source
    result = owner.run_batch({'trade_date': days[84].date().isoformat()}, db_path=path, root=root)
    manifest = json.loads(next(root.glob('*.json')).read_text())
    shard = root/manifest['methods']['ma_bullish']['items_ref']['file']
    raw = shard.read_bytes()
    shard.write_bytes(raw.replace(b'"close":', b'"cloze":', 1))
    with pytest.raises(owner.SavedRunMissing):
        load_saved(result, root)
    shard.unlink()
    with pytest.raises(owner.SavedRunMissing):
        owner.list_runs('reference:ma_bullish', root=root)


def test_partial_shard_writes_do_not_publish_a_successful_batch(market_source, monkeypatch):
    path, days, root = market_source
    original = owner._atomic_artifact
    count = 0
    def fail_second_shard(target, raw):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('injected write failure')
        return original(target, raw)
    monkeypatch.setattr(owner, '_atomic_artifact', fail_second_shard)
    with pytest.raises(OSError):
        owner.run_batch({'trade_date': days[84].date().isoformat()}, db_path=path, root=root)
    assert not list(root.glob('*.json'))
    assert owner.list_runs('reference:ma_bullish', root=root)['items'] == []


def test_duplicate_adjusted_keys_remain_unknown_without_exploding_the_raw_denominator(market_source):
    path, days, root = market_source
    with duckdb.connect(str(path)) as con:
        con.execute('INSERT INTO daily_k_adj SELECT * FROM daily_k_adj WHERE thscode=? AND date_ms=?',
                    ['000001.SZ', int(days[84].timestamp()*1000)])
    result = owner.run_batch({'trade_date': days[84].date().isoformat()}, db_path=path, root=root)
    saved = load_saved(result, root)
    assert saved['counts']['universe'] == 2
    row = next(item for item in saved['items'] if item['symbol'] == '000001')
    assert row['state'] == 'unknown' and 'source_quality_issue' in row['reason_codes']


def test_bounded_source_groups_preserve_global_order_and_missing_adjusted_rows(tmp_path):
    path = tmp_path/'grouped.duckdb'
    with duckdb.connect(str(path)) as con:
        con.execute('CREATE TABLE daily_k(thscode VARCHAR,date_ms BIGINT,volume DOUBLE)')
        con.execute('CREATE TABLE daily_k_adj(thscode VARCHAR,date_ms BIGINT,close_adj DOUBLE)')
        for i in reversed(range(65)):
            code = f'{600000+i}.SH'
            con.execute('INSERT INTO daily_k VALUES(?,?,?)', [code, 1, 100.])
            if i != 32:
                con.execute('INSERT INTO daily_k_adj VALUES(?,?,?)', [code, 1, 10.+i])
        rows = list(owner._ordered_source_rows(con, 'NULL,NULL,NULL,NULL,NULL', 10))
    assert len(rows) == 65 and [row[0] for row in rows] == sorted(row[0] for row in rows)
    assert rows[32][0] == '600032.SH' and rows[32][2] is None
    assert rows[31][2] == 41. and rows[33][2] == 43.


def test_explicit_reuse_verifies_all_shards_without_recalculating_a_bad_batch(market_source, monkeypatch):
    path, days, root = market_source
    params = {'trade_date': days[84].date().isoformat()}
    owner.run_batch(params, db_path=path, root=root)
    manifest = json.loads(next(root.glob('*.json')).read_text())
    shard = root/manifest['methods']['rsi_reversal']['items_ref']['file']
    raw = shard.read_bytes()
    shard.write_bytes(raw.replace(b'"close":', b'"cloze":', 1))
    monkeypatch.setattr(owner, 'iter_reference_rules', lambda *a, **kw: pytest.fail('never recalculate a changed original batch'))
    with pytest.raises(owner.SavedRunMissing, match='不重新计算'):
        owner.run_batch(params, db_path=path, root=root)
