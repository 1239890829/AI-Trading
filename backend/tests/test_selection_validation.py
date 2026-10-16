"""Selection observation and manually recorded fill statistics have separate denominators."""
from datetime import date, datetime, timezone
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.watchlist import Base
from app.models.daily_pick import DailyPickSet
from app.models.real_position import RealTrade
from app.picks.validation import validation_report, validate_trade_selection


@pytest.fixture
def sf():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def selected(sf, day='2026-10-12', symbol='600127', clock='10:00:00'):
    key = f'daily:{day}:{symbol}'
    with sf() as db:
        db.add(DailyPickSet(date=day, items=json.dumps([{'symbol': symbol, 'name': '金健米业'}]), meta=json.dumps({
            'selection_entries': {symbol: {'state': 'recorded', 'selected_at': f'{day}T{clock}+08:00',
            'reference_price': 10.0, 'reference_change_pct': 1.0, 'source_version': 'v1'}}})))
        db.commit()
    return key


def fill(sf, side, price, qty, at, ref=None, *, known=True, fee=1):
    with sf() as db:
        row = RealTrade(symbol='600127', side=side, fill_price=price, quantity=qty, fee=fee,
                        traded_at=at[:10], executed_at=datetime.fromisoformat(at).astimezone(timezone.utc).replace(tzinfo=None),
                        fee_known=known, selection_ref=ref,
                        selection_evidence=json.dumps({'id': ref, 'symbol': '600127', 'selected_at': '2026-10-12T10:00:00+08:00'}) if ref else None)
        db.add(row); db.commit()


def test_cross_day_same_symbol_is_two_selections_without_fake_outcomes(sf):
    selected(sf); selected(sf, '2026-10-13')
    report = validation_report('2026-10-12', '2026-10-14', session_factory=sf, calendar=[])
    assert report['selection']['count'] == 2
    assert report['selection']['observed'] == 0 and report['selection']['mean_pct'] is None
    assert [r['count'] for r in report['daily']] == [1, 1]
    assert all(x['return_pct'] is None for x in report['entries'])
    assert report['actual']['denominator'] == 0 and report['actual']['win_rate'] is None


def test_partial_sales_one_episode_fees_and_reentry_new_episode(sf):
    ref = selected(sf)
    fill(sf, 'buy', 10, 100, '2026-10-12T10:01:00+08:00', ref)
    fill(sf, 'buy', 12, 100, '2026-10-12T10:02:00+08:00', ref)
    fill(sf, 'sell', 13, 50, '2026-10-13T10:00:00+08:00')
    fill(sf, 'sell', 11, 150, '2026-10-14T10:00:00+08:00')
    fill(sf, 'buy', 10, 100, '2026-10-14T11:00:00+08:00', ref)
    fill(sf, 'sell', 9, 100, '2026-10-15T10:00:00+08:00')
    days = [date(2026, 10, n) for n in range(12, 16)]
    report = validation_report('2026-10-12', '2026-10-15', session_factory=sf, calendar=days)
    actual = report['actual']
    assert actual['denominator'] == 2 and actual['win_rate'] == 50
    expected = [(2300-2202-2)/2202*100, (900-1001-1)/1001*100]
    assert actual['mean_pct'] == pytest.approx(sum(expected)/2)
    assert actual['median_pct'] == pytest.approx(sum(expected)/2)
    assert [e['holding_trade_days'] for e in report['episodes']] == [3, 2]
    assert actual['exposure_trade_days'] == 4 and actual['episode_trade_days'] == 5
    assert report['win_curve'][-1]['denominator'] == 2


def test_open_unknown_fee_legacy_and_oversell_never_win(sf):
    ref = selected(sf)
    fill(sf, 'buy', 10, 100, '2026-10-12T10:01:00+08:00', ref, known=False)
    fill(sf, 'sell', 20, 100, '2026-10-13T10:00:00+08:00')
    fill(sf, 'buy', 10, 100, '2026-10-14T10:00:00+08:00', ref)
    fill(sf, 'sell', 20, 101, '2026-10-15T10:00:00+08:00')
    fill(sf, 'buy', 10, 100, '2026-10-15T11:00:00+08:00', ref)
    with sf() as db:
        db.add(RealTrade(symbol='000001', side='buy', fill_price=1, quantity=100, traded_at='2026-10-12')); db.commit()
    out = validation_report('2026-10-12', '2026-10-15', session_factory=sf, calendar=[])
    assert out['actual']['denominator'] == 0 and out['actual']['win_rate'] is None
    assert out['actual']['open'] == 2 and out['actual']['excluded'] == 2
    assert out['actual']['legacy_fills'] == 1
    assert all(x['holding_trade_days'] is None for x in out['episodes'])


def test_buy_requires_existing_same_symbol_selection_before_exact_fill(sf):
    ref = selected(sf)
    with sf() as db:
        with pytest.raises(ValueError, match='入选'):
            validate_trade_selection(db, '600127', ref, datetime.fromisoformat('2026-10-12T09:59:59+08:00'))
        with pytest.raises(ValueError, match='入选'):
            validate_trade_selection(db, '000001', ref, datetime.fromisoformat('2026-10-12T10:02:00+08:00'))
        evidence = validate_trade_selection(db, '600127', ref, datetime.fromisoformat('2026-10-12T10:02:00+08:00'))
        assert evidence['reference_price'] == 10


def test_daily_archive_is_observation_only_idempotent_and_uses_current_outcome_basis(sf):
    from app.picks.selection_entry import archive_daily_observations
    from app.picks.opportunity_learning import PRICE_BASIS_VERSION, ensure_outcome_horizons
    from app.models.opportunity_learning import OpportunityDecisionSnapshot, OpportunityOutcomeLabel
    from sqlalchemy import select
    selected(sf)
    with sf() as db:
        row = db.scalar(select(DailyPickSet))
        entries = json.loads(row.meta)['selection_entries']
        entries['600127']['quote_as_of'] = '2026-10-12T09:59:59+08:00'
        row.meta = json.dumps({'selection_entries': entries}); db.commit()
    assert archive_daily_observations('2026-10-12', entries, session_factory=sf)['inserted'] == 1
    assert archive_daily_observations('2026-10-12', entries, session_factory=sf)['inserted'] == 0
    ensure_outcome_horizons('2026-10-12', [date(2026,10,n) for n in range(12,17)], session_factory=sf)
    with sf() as db:
        snap = db.scalar(select(OpportunityDecisionSnapshot))
        assert snap.stage == 'validation' and snap.strategy_version != 'stock-opportunity-funnel-v2'
        out = db.scalar(select(OpportunityOutcomeLabel).where(OpportunityOutcomeLabel.horizon == 'd1_close'))
        out.state, out.return_pct, out.price_basis_version = 'labeled', 5.0, PRICE_BASIS_VERSION
        db.commit()
    report = validation_report('2026-10-12', '2026-10-14', session_factory=sf, calendar=[])
    assert report['selection']['mean_pct'] == 5.0
    # The range selects entry dates; a later matured D1 remains visible.
    one_day = validation_report('2026-10-12', '2026-10-12', session_factory=sf, calendar=[])
    assert one_day['selection']['mean_pct'] == 5.0
    assert report['actual']['denominator'] == 0


def test_manual_write_uses_receipt_and_does_not_accept_missing_link_or_future_time(sf, monkeypatch):
    from app.api.routes import real_position as route
    from app.api.routes.real_position import TradeIn
    monkeypatch.setattr(route, '_sf', sf)
    ref = selected(sf)
    common = dict(symbol='600127', side='buy', fill_price=10, quantity=100,
                  executed_at='2026-10-12T10:01:00+08:00', fee=0, fee_known=True)
    with pytest.raises(ValueError, match='入选'):
        route._record_trade(TradeIn(**common))
    out = route._record_trade(TradeIn(**common, selection_ref=ref))
    with sf() as db:
        row = db.get(RealTrade, out['data']['id'])
        assert row.fill_price == 10 and row.fee_known == 1
        assert json.loads(row.selection_evidence)['reference_price'] == 10
        assert row.executed_at == datetime(2026,10,12,2,1)
    with pytest.raises(ValueError, match='日期'):
        route._record_trade(TradeIn(**common, selection_ref=ref, traded_at='2026-10-13'))
    future = dict(common, executed_at='2099-10-12T10:01:00+08:00')
    with pytest.raises(ValueError, match='已发生'):
        route._record_trade(TradeIn(**future, selection_ref=ref))


def test_read_route_is_off_event_loop_and_never_calls_source(sf, monkeypatch):
    import asyncio
    from app.api.routes import selection_validation as route
    selected(sf)
    original = validation_report
    loop_thread = __import__('threading').get_ident()
    def read(*args, **kwargs):
        assert __import__('threading').get_ident() != loop_thread
        return original('2026-10-12', '2026-10-14', session_factory=sf, calendar=[])
    monkeypatch.setattr(route, 'read_validation', lambda *args, **kwargs: (read(*args, **kwargs), 'a'*64))
    out = asyncio.run(route.report(start='2026-10-12',end='2026-10-14', horizon='d1_close', scope='all'))
    assert out['data']['selection']['count'] == 1


def test_zero_pick_day_is_zero_but_absent_day_is_not_invented(sf):
    with sf() as db:
        db.add(DailyPickSet(date='2026-10-12', items='[]', meta='{}'));db.commit()
    out = validation_report('2026-10-12', '2026-10-14', session_factory=sf, calendar=[])
    assert out['daily'] == [{'date': '2026-10-12', 'count': 0, 'observed': 0, 'mean_pct': None, 'median_pct': None}]


@pytest.mark.parametrize('sources', [None, [], 'legacy', {'stage': 'legacy'}, [None], ['legacy']])
def test_unknown_legacy_theme_shape_does_not_break_original_selection(sf, sources):
    from app.models.opportunity_learning import OpportunityDecisionSnapshot
    from app.picks.selection_entry import CONTRACT
    with sf() as db:
        db.add(OpportunityDecisionSnapshot(snapshot_id='legacy-shape', run_id='run', trade_date='2026-10-12',
            as_of=datetime(2026, 10, 12, 10), created_at=datetime(2026, 10, 12, 2),
            scenario='intraday_opportunity', stage='rank', symbol='600127', decision='ranked', rank=1,
            strategy_version='original', feature_version='original', entry_price=10,
            evidence=json.dumps({'selection_entry_contract': CONTRACT, 'within_display_capacity': 1,
                                 'effective_limit': 8, 'theme_sources': sources})))
        db.commit()
    out = validation_report('2026-10-12', '2026-10-12', session_factory=sf, calendar=[])
    assert out['selection']['count'] == 1
    assert out['entries'][0]['original_shape'] is None


def test_zero_net_return_counts_in_denominator_without_float_dust_win(sf):
    ref = selected(sf)
    fill(sf,'buy',10.123,100,'2026-10-12T10:01:00+08:00',ref,fee=0)
    fill(sf,'sell',10.123,100,'2026-10-13T10:01:00+08:00',fee=0)
    out = validation_report('2026-10-12','2026-10-14',session_factory=sf,calendar=[])
    assert out['actual']['denominator'] == 1 and out['actual']['flat'] == 1
    assert out['actual']['win_rate'] == 0 and out['actual']['median_pct'] == 0


def test_compact_legacy_dates_stay_visible_without_duplicate_iso_day(sf):
    with sf() as db:
        db.add(DailyPickSet(date='20261012', items='[{"symbol":"600127"}]', meta='{}'));db.commit()
    legacy = validation_report('2026-10-12','2026-10-14',session_factory=sf,calendar=[])
    assert legacy['selection']['count'] == 1
    assert legacy['entries'][0]['selected_at'] is None and legacy['entries'][0]['date'] == '2026-10-12'
    selected(sf)
    current = validation_report('2026-10-12','2026-10-14',session_factory=sf,calendar=[])
    assert current['selection']['count'] == 1 and current['entries'][0]['selected_at'] is not None


def test_incremental_curve_matches_reference_for_every_prefix():
    from random import Random
    from statistics import mean, median
    from app.picks.validation import _win_curve
    random = Random(31)
    returns = [0.0, 1.0, -1.0, 1e-12, 1e8, -1e8] + [random.uniform(-98, 270) for _ in range(600)]
    episodes = [dict(id=i, close_date='2026-10-15', closed_at=f'2026-10-15T10:{i//60:02}:{i%60:02}',
                     state='closed', return_pct=value) for i, value in enumerate(returns)]
    episodes += [dict(id=999, closed_at='', state='open', return_pct=999),
                 dict(id=1000, closed_at='', state='excluded', return_pct=999),
                 dict(id=1001, closed_at='', state='closed', return_pct=None)]
    curve = _win_curve(list(reversed(episodes)))
    assert len(curve) == len(returns)
    for count, point in enumerate(curve, 1):
        prefix = returns[:count]
        assert point == dict(date='2026-10-15', episode_id=count-1, denominator=count,
                             win_rate=sum(v > 0 for v in prefix)/count*100,
                             mean_pct=mean(prefix), median_pct=median(prefix))


def test_validation_reads_daily_archive_once(sf):
    from sqlalchemy import event
    selected(sf)
    reads = []
    engine = sf.kw['bind']
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith('SELECT') and 'FROM daily_pick_set' in statement:
            reads.append(statement)
    event.listen(engine, 'before_cursor_execute', record)
    try:
        validation_report('2026-10-12', '2026-10-12', session_factory=sf, calendar=[])
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert len(reads) == 1


def test_coverage_never_turns_unknown_into_failure_or_a_current_version(monkeypatch):
    from app.picks.validation import _coverage
    from app.core.bjtime import BJ_TZ
    from datetime import datetime
    monkeypatch.setattr('app.picks.validation.beijing_now', lambda: datetime(2026,10,14,16,tzinfo=BJ_TZ))
    def entry(id, **changes):
        return dict(id=id, state='recorded', date='2026-10-12', scope='daily',
                    return_pct=None, outcome_state='pending', **changes)
    entries = [entry('future', outcome_date='2026-10-15'),
               entry('missing', outcome_date='2026-10-13'), entry('calendar'),
               {**entry('basis'), 'outcome_state':'basis_or_window_unavailable'},
               {**entry('original'), 'state':'partial'},
               {**entry('ok'), 'return_pct':2.0, 'method_version':'v1', 'feature_version':'f1'}]
    result = _coverage(entries, calendar=[], horizon='d1_close')
    assert [e['coverage_state'] for e in entries] == ['not_due','result_missing','calendar_unknown','basis_mismatch','original_missing','observed']
    assert sum(r['count'] for r in result['reasons']) == 6
    unknown = next(r for r in result['versions'] if r['version_state']=='missing')
    assert unknown['count'] == 5 and unknown['method_version'] is None


def test_payoff_is_net_episode_statistics_not_a_fabricated_account_curve():
    from app.picks.validation import _payoff
    result = _payoff([4.0, -2.0, 0.0])
    assert result['payoff_ratio'] == 2
    assert result['net_expectancy_pct'] == pytest.approx(2/3)
    assert result['drawdown_pct'] is None
    assert _payoff([4.0])['payoff_ratio'] is None
    assert _payoff([])['net_expectancy_pct'] is None


def test_today_outcomes_mature_at_close_and_old_selection_range_does_not_delay_them(monkeypatch):
    from app.picks.validation import _coverage
    from app.core.bjtime import BJ_TZ
    from datetime import datetime
    row = dict(state='recorded', date='2026-10-12', scope='daily', return_pct=None,
               outcome_state='basis_or_window_unavailable', outcome_date='2026-10-16')
    for hour, expected in [(14, 'not_due'), (15, 'basis_mismatch')]:
        monkeypatch.setattr('app.picks.validation.beijing_now', lambda: datetime(2026,10,16,hour,tzinfo=BJ_TZ))
        _coverage([row], calendar=[], horizon='d1_close')
        assert row['coverage_state'] == expected


def test_strata_preserve_original_versions_and_missing_classifications(monkeypatch):
    from app.picks.validation import _coverage
    from app.core.bjtime import BJ_TZ
    monkeypatch.setattr('app.picks.validation.beijing_now', lambda: datetime(2026,10,16,16,tzinfo=BJ_TZ))
    rows = [dict(date='2026-10-12', scope='daily', state='recorded', return_pct=2.0, outcome_state='labeled',
                 selected_at='2026-10-12T10:00:00+08:00', method_version='v1', feature_version='f1',
                 original_shape='趋势续强', original_environment='回暖'),
            dict(date='2026-10-12', scope='daily', state='recorded', return_pct=-2.0, outcome_state='labeled',
                 selected_at=None, method_version='v2', feature_version='f1')]
    strata = _coverage(rows, calendar=[], horizon='d1_close')['strata']
    assert any(r['dimension']=='shape' and r['label']=='未记录' and r['method_version']=='v2' for r in strata)
    assert any(r['dimension']=='entry_time' and r['label']=='上午' and r['mean_pct']==2 for r in strata)
    assert all(r['count']==1 for r in strata)
