"""Exact saved-history reads; no source calls, current-parameter replay or writes."""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core import bjtime
from app.models.daily_pick import DailyPickSet
from app.models.opportunity_learning import OpportunityDecisionRun, OpportunityDecisionSnapshot
from app.models.watchlist import Base
from app.picks.selection_history import SavedRunMissing, get_method_run, list_method_runs
from app.services.selection_notifications import daily_selection_version

NOW = datetime(2026, 10, 25, 12, tzinfo=bjtime.BJ_TZ)


@pytest.fixture
def sf(tmp_path, monkeypatch):
    monkeypatch.setattr(bjtime, 'beijing_now', lambda: NOW)
    engine = create_engine(f"sqlite:///{tmp_path / 'selection-history.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def daily(sf, *, day='2026-10-23', legacy=False, items=None, rejected=None):
    items = [] if items is None else items
    meta = {'generated_at': day + 'T09:26:00+08:00', 'min_pick_score': 0,
            'candidate_count': 0, 'selection_funnel': {'scored': 0},
            'selection_contract': {'version': 'daily_selection_v2', 'trade_date': day,
                'generated_at': day + 'T09:26:00+08:00', 'strategy_version': 'method-old', 'feature_version': 'features-old'}}
    if not legacy:
        meta['selection_version'] = daily_selection_version(day, meta['generated_at'], items)
    with sf() as db:
        row = DailyPickSet(date=day, items=json.dumps(items), meta=json.dumps(meta), rejected=json.dumps(rejected or []))
        db.add(row)
        db.commit()
        return row.id


def intra(sf, *, run_id='intraday-old', day='2026-10-23', stages=('rank',), scenario='intraday_opportunity'):
    at = datetime.fromisoformat(day + 'T10:00:00')
    with sf() as db:
        run = OpportunityDecisionRun(run_id=run_id, trade_date=day, as_of=at, scenario=scenario,
            strategy_version='intra-v2', feature_version='intra-f2', evidence_digest='original-owner-digest',
            records_total=len(stages), rank_rows=stages.count('rank'), hard_gate_rows=stages.count('hard_gate'),
            candidate_rows=stages.count('candidate'))
        db.add(run)
        for i, stage in enumerate(stages):
            db.add(OpportunityDecisionSnapshot(snapshot_id=f'{run_id}-{i}', run_id=run_id,
                trade_date=day, as_of=at, scenario=scenario, stage=stage, symbol=f'{600000+i}',
                decision='ranked' if stage == 'rank' else 'rejected', rank=i+1 if stage == 'rank' else None,
                strategy_version=run.strategy_version, feature_version=run.feature_version,
                evidence=json.dumps({'effective_limit': 1, 'within_display_capacity': i == 0, 'reason': f'原理由{i}'})))
        db.commit()


def identity(sf, method='daily_picks', **filters):
    return list_method_runs(sf, method, **filters)['items'][0]


def detail(sf, ident, **overrides):
    args = {'trade_date': ident['trade_date'], 'content_version': ident['content_version'],
            'source_version': ident['source_version'], **overrides}
    return get_method_run(sf, ident['method'], ident['run_id'], **args)


def test_list_filter_date_method_versions_legacy_and_stable_order(sf):
    daily(sf, day='2026-10-22', legacy=True)
    daily(sf)
    intra(sf)
    intra(sf, run_id='notification-only', scenario='notification')
    rows = list_method_runs(sf, 'daily_picks')['items']
    assert [row['trade_date'] for row in rows] == ['2026-10-23', '2026-10-22']
    assert rows[0]['strategy_version'] == 'method-old' and rows[0]['version_state'] == 'bound'
    assert rows[1]['source_version'] is None and rows[1]['version_state'] == 'legacy'
    assert len(list_method_runs(sf, 'daily_picks', strategy_version='method-old')['items']) == 2
    assert list_method_runs(sf, 'daily_picks', strategy_version='v1')['items'] == []
    assert [row['trade_date'] for row in list_method_runs(sf, 'daily_picks', source_version_missing=True)['items']] == ['2026-10-22']
    assert list_method_runs(sf, 'daily_picks', source_version='未知')['items'] == []
    assert list_method_runs(sf, 'daily_picks', trade_date='2026-10-21')['items'] == []
    assert [row['run_id'] for row in list_method_runs(sf, 'intraday_watch')['items']] == ['intraday-old']


@pytest.mark.parametrize('mutation', ['day', 'method', 'id', 'source_version', 'strategy_version', 'content_version'])
def test_detail_wrong_identity_never_returns_latest(sf, mutation):
    daily(sf)
    intra(sf)
    ident = identity(sf)
    args = dict(trade_date=ident['trade_date'], content_version=ident['content_version'])
    method, run_id = ident['method'], ident['run_id']
    if mutation == 'day': args['trade_date'] = '2026-10-22'
    elif mutation == 'method': method = 'intraday_watch'
    elif mutation == 'id': run_id = '999999'
    else: args[mutation] = '0' * 64
    with pytest.raises(SavedRunMissing):
        get_method_run(sf, method, run_id, **args)


def test_overwritten_generation_and_nonselected_evidence_drift_fail_closed(sf):
    daily(sf, rejected=[{'symbol': '600000', 'score': 0, 'selection_reason': 'capacity_or_replacement'}])
    original = identity(sf)
    with sf() as db:
        row = db.get(DailyPickSet, int(original['run_id']))
        row.rejected = '[{"symbol":"600000","score":0,"vetoes":["新增旧证据"]}]'
        db.commit()
    with pytest.raises(SavedRunMissing): detail(sf, original)
    current = identity(sf)
    assert current['source_version'] == original['source_version']
    assert current['content_version'] != original['content_version']
    with sf() as db:
        row = db.get(DailyPickSet, int(original['run_id']))
        meta = json.loads(row.meta)
        meta['generated_at'] = '2026-10-23T10:26:00+08:00'
        row.meta = json.dumps(meta)
        db.commit()
    with pytest.raises(SavedRunMissing): detail(sf, current)


def test_zero_and_missing_preserve_original_diagnostics_without_current_parameters(sf, monkeypatch):
    import app.picks.engine as engine
    monkeypatch.setattr(engine, 'effective_limits', lambda: pytest.fail('history must not read current limits'))
    rejected = [dict(symbol='600000', score=0, sub_scores={'tech': 0}, dimension_evidence={'tech': {'state': 'missing'}},
        scoring_trace={'dimensions': {'tech': {'score': 0, 'weight': 0, 'contribution': 0}}}, vetoes=[],
        score_shortfall=0, selection_reason='capacity_or_replacement'), {'symbol': '600001'}]
    daily(sf, rejected=rejected)
    run = detail(sf, identity(sf))
    first, legacy = run['items']
    assert first['score'] == first['diagnostics']['min_pick_score'] == first['diagnostics']['score_shortfall'] == 0
    assert first['diagnostics']['sub_scores']['tech'] == 0 and first['diagnostics']['vetoes'] == []
    assert first['diagnostics']['scoring_trace']['dimensions']['tech']['contribution'] == 0
    assert '具体原因未区分' in first['reason']
    assert legacy['score'] is None and legacy['diagnostics']['sub_scores'] is None
    assert legacy['diagnostics']['score_shortfall'] is None
    assert 'vetoes' in legacy['diagnostics']['missing']


def test_corrupt_and_legacy_records_are_readable_without_manufactured_version(sf):
    key = daily(sf, legacy=True, rejected=[{'symbol':'600000','selection_reason':'below_min_score'}])
    run = detail(sf, identity(sf), source_version_missing=True)
    assert run['source_version'] is None and run['version_state'] == 'legacy'
    assert run['items'][0]['diagnostics']['scoring_trace'] is None
    with sf() as db:
        row = db.get(DailyPickSet, key)
        row.items, row.meta = 'not-json', 'null'
        db.commit()
    run = detail(sf, identity(sf))
    assert run['state'] == 'partial' and run['counts']['selected'] is None
    assert run['counts']['rejected_saved'] == 1


def test_late_rejected_object_is_reachable_with_bound_pagination(sf):
    intra(sf, stages=tuple(['hard_gate'] * 225 + ['rank']))
    ident = identity(sf, 'intraday_watch')
    first = detail(sf, ident)
    assert len(first['items']) == 200 and first['coverage']['next_offset'] == 200
    assert first['coverage']['total'] == 226 and first['possibly_truncated'] is True
    second = detail(sf, ident, offset=first['coverage']['next_offset'])
    assert second['items'][24]['snapshot_id'] == 'intraday-old-224'
    assert second['coverage']['next_offset'] is None and second['possibly_truncated'] is False
    filtered = detail(sf, ident, stage='rank')
    assert filtered['coverage']['total'] == 1 and filtered['items'][0]['stage'] == 'rank'
    with sf() as db:
        row = db.scalar(select(OpportunityDecisionSnapshot).where(OpportunityDecisionSnapshot.snapshot_id == 'intraday-old-224'))
        row.evidence = '{"reason":"原存档被修改"}'
        db.commit()
    with pytest.raises(SavedRunMissing): detail(sf, ident, offset=200)


def test_intra_date_version_binding_and_counts_cannot_hide_missing_snapshot(sf):
    intra(sf)
    with sf() as db:
        row = db.scalar(select(OpportunityDecisionSnapshot))
        row.trade_date = '2026-10-22'
        db.commit()
    run = detail(sf, identity(sf, 'intraday_watch'))
    assert run['state'] == 'partial' and run['items'][0]['state'] == 'unknown'
    assert run['counts']['selected'] is None
    with sf() as db:
        db.delete(db.scalar(select(OpportunityDecisionSnapshot)))
        db.commit()
    run = detail(sf, identity(sf, 'intraday_watch'))
    assert run['items'] == [] and run['counts']['selected'] is None


def test_real_zero_run_and_list_truncation_are_not_missing(sf):
    intra(sf, run_id='empty', stages=())
    daily(sf, day='2026-10-21')
    daily(sf, day='2026-10-22')
    page = list_method_runs(sf, 'daily_picks', limit=1)
    assert page['coverage']['possibly_truncated'] and len(page['items']) == 1
    run = detail(sf, identity(sf, 'intraday_watch'))
    assert run['state'] == 'saved' and run['counts']['selected'] == 0 and run['coverage']['total'] == 0


def test_future_sources_excluded_and_invalid_filters_rejected(sf):
    daily(sf, day='2026-10-26')
    intra(sf, day='2026-10-26')
    key = daily(sf, day='2026-10-25')
    with sf() as db:
        row = db.get(DailyPickSet, key)
        meta = json.loads(row.meta)
        meta['generated_at'] = '2026-10-25T13:00:00+08:00'
        row.meta = json.dumps(meta)
        db.commit()
    assert list_method_runs(sf, 'daily_picks')['items'] == []
    assert list_method_runs(sf, 'intraday_watch')['items'] == []
    for kwargs in [dict(trade_date='2026-02-30'), dict(source_version='x',source_version_missing=True),dict(limit=201)]:
        with pytest.raises(ValueError): list_method_runs(sf, 'daily_picks', **kwargs)


def test_get_routes_are_exact_and_pure_reads_in_real_router_assembly(sf, monkeypatch):
    from app.api.routes import picks
    from app.core.config import settings
    from app.main import app
    monkeypatch.setattr(picks, 'get_session_factory', lambda: sf)
    monkeypatch.setattr(settings, 'api_token', 'fixture-history-token')
    daily(sf, rejected=[{'symbol':'600000','score':0}])
    statements = []
    engine = sf.kw['bind']
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        client = TestClient(app)
        assert client.get('/api/picks/method-runs?method=daily_picks').status_code == 401
        headers = {'X-API-Token':'fixture-history-token'}
        response = client.get('/api/picks/method-runs?method=daily_picks',headers=headers)
        assert response.status_code == 200
        ident = response.json()['data']['items'][0]
        path = f"/api/picks/method-runs/{ident['method']}/{ident['run_id']}"
        params = {key:ident[key] for key in ['trade_date','content_version','source_version']}
        response = client.get(path,params=params,headers=headers)
        assert response.status_code == 200 and response.json()['data']['items'][0]['score'] == 0
        assert client.get(path, params={**params, 'row_state': 'all'}, headers=headers).json()['data'] == response.json()['data']
        assert client.get(path, params={**params, 'row_state': 'unknown'}, headers=headers).status_code == 422
        assert client.get(path,params={**params,'trade_date':'2026-10-22'},headers=headers).status_code == 404
        assert client.get(path,params={**params,'content_version':'f'*64},headers=headers).status_code == 404
        assert client.get(path,params={'trade_date':'2026-10-23'},headers=headers).status_code == 422
        assert client.get('/api/picks/method-runs?method=notification',headers=headers).status_code == 422
        assert statements and set(statements) <= {'SELECT', 'BEGIN'}
    finally:
        event.remove(engine, 'before_cursor_execute', capture)


def test_directory_and_display_page_have_bounded_materialization_and_query_count(sf):
    # Other runs must not be fingerprinted, projected or ORM-loaded for this page.
    for i in range(8):
        intra(sf, run_id=f'run-{i}', stages=tuple(['hard_gate'] * 40))
    loads, selects = [], []
    def loaded(session, instance):
        loads.append(type(instance).__name__)
    def captured(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith('SELECT'):
            selects.append((statement, parameters))
    engine = sf.kw['bind']
    event.listen(sf, 'loaded_as_persistent', loaded)
    event.listen(engine, 'before_cursor_execute', captured)
    try:
        page = list_method_runs(sf, 'intraday_watch', limit=2)
        assert len(selects) == 2
        assert loads.count('OpportunityDecisionRun') == 3
        assert 'OpportunityDecisionSnapshot' not in loads
        assert page['coverage']['scanned'] == 2
        assert page['coverage']['fingerprint_rows'] == 80
        assert page['coverage']['next_offset'] == 2
        next_page = list_method_runs(sf, 'intraday_watch', offset=2, limit=2)
        assert not ({i['run_id'] for i in page['items']} & {i['run_id'] for i in next_page['items']})
        loads.clear()
        selects.clear()
        run = detail(sf, page['items'][0], offset=20, limit=5)
        assert loads.count('OpportunityDecisionRun') == 1 and loads.count('OpportunityDecisionSnapshot') == 5
        assert len(selects) == 3
        assert run['coverage']['fingerprint_rows'] == 40
        assert run['items'][0]['snapshot_id'].endswith('-20')
    finally:
        event.remove(sf, 'loaded_as_persistent', loaded)
        event.remove(engine, 'before_cursor_execute', captured)


def test_sql_version_filters_do_not_scan_unmatched_daily_owners(sf):
    for i in range(1, 24):
        daily(sf, day=f'2026-10-{i:02d}')
    selected = identity(sf, trade_date='2026-10-02')
    loads = []
    def loaded(session, instance):
        loads.append(type(instance).__name__)
    event.listen(sf, 'loaded_as_persistent', loaded)
    try:
        page = list_method_runs(sf, 'daily_picks', source_version=selected['source_version'], strategy_version='method-old', limit=1)
        assert page['items'][0]['trade_date'] == '2026-10-02'
        assert loads == ['DailyPickSet']
        assert page['coverage']['scanned'] == 1 and page['coverage']['next_offset'] is None
    finally:
        event.remove(sf, 'loaded_as_persistent', loaded)


def test_excluded_clock_is_bounded_scan_not_claim_of_no_history(sf):
    daily(sf, day='2026-10-24')
    key = daily(sf, day='2026-10-25')
    with sf() as db:
        row = db.get(DailyPickSet, key)
        data = json.loads(row.meta)
        data['generated_at'] = '2026-10-25T13:00:00+08:00'
        row.meta = json.dumps(data)
        db.commit()
    page = list_method_runs(sf, 'daily_picks', limit=1)
    assert page['items'] == []
    assert page['coverage']['scanned'] == page['coverage']['excluded_source_rows'] == 1
    assert page['coverage']['next_offset'] == 1
    older = list_method_runs(sf, 'daily_picks', limit=1, offset=1)
    assert older['items'][0]['trade_date'] == '2026-10-24'


def test_digest_and_page_share_snapshot_under_concurrent_owner_change(sf, monkeypatch):
    from app.picks import selection_history
    engine = sf.kw['bind']
    with engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA journal_mode=WAL')
    intra(sf)
    ident = identity(sf, 'intraday_watch')
    original = selection_history._stream_fingerprints
    changed = False
    def changing(db, runs, **kwargs):
        nonlocal changed
        result = original(db, runs, **kwargs)
        if not changed:
            changed = True
            with sf() as writer:
                row = writer.scalar(select(OpportunityDecisionSnapshot))
                row.evidence = '{"reason":"之后改写的证据"}'
                writer.commit()
        return result
    monkeypatch.setattr(selection_history, '_stream_fingerprints', changing)
    run = detail(sf, ident)
    assert run['items'][0]['reason'] == '原理由0'
    with pytest.raises(SavedRunMissing): detail(sf, ident)


def test_existing_registry_route_keeps_original_rejection_diagnostics(sf, monkeypatch):
    """Existing endpoint regression: old projection silently discarded these fields."""
    from app.api.routes import picks
    from app.core.config import settings
    from app.main import app
    monkeypatch.setattr(picks, 'get_session_factory', lambda: sf)
    monkeypatch.setattr(settings, 'api_token', 'fixture-history-token')
    monkeypatch.setattr('app.picks.strategy_registry._verification_of', lambda key: {'available':False})
    daily(sf, rejected=[{'symbol':'600000','score':0,'sub_scores':{'tech':0},
        'dimension_evidence':{'tech':{'state':'missing','reason':'旧源缺项'}},
        'scoring_trace':{'dimensions':{'tech':{'score':0,'weight':0.2,'contribution':0}}},
        'vetoes':['原停牌否决'], 'selection_reason':'below_min_score', 'score_shortfall':0}])
    response = TestClient(app).get('/api/picks/strategy-registry',headers={'X-API-Token':'fixture-history-token'})
    assert response.status_code == 200
    runtime = next(row['runtime'] for row in response.json()['data']['strategies'] if row['key']=='daily_picks')
    original = runtime['items'][0]['diagnostics']
    assert original['sub_scores']['tech'] == 0
    assert original['scoring_trace']['dimensions']['tech']['contribution'] == 0
    assert original['dimension_evidence']['tech']['reason'] == '旧源缺项'
    assert original['vetoes'] == ['原停牌否决'] and original['score_shortfall'] == 0


def test_saved_candidate_exclusions_read_without_replaying_today_or_inventing_old_reasons(sf):
    pk = daily(sf, items=[{'symbol': '600001', 'score': 55}], rejected=[{'symbol': '600002', 'score': 45}])
    with sf() as db:
        row = db.get(DailyPickSet, pk)
        meta = json.loads(row.meta)
        meta['candidate_audit'] = {'version': 'daily-candidate-domain-v1', 'complete': True,
            'input_digest': 'a' * 64, 'input_total': 4, 'saved_total': 4, 'omitted_total': 0,
            'universe': {'source': 'sina_market', 'node': 'hs_a', 'version': 'original'},
            'rows': [{'symbol': '600001', 'reason_code': 'selected', 'input_position': 1},
                     {'symbol': '600002', 'reason_code': 'below_min_score', 'input_position': 2},
                     {'symbol': '513880', 'reason_code': 'outside_a_share_universe', 'stage': 'identity', 'input_position': 3},
                     {'symbol': '600003', 'reason_code': 'scoring_unavailable', 'stage': 'score', 'input_position': 4}]}
        row.meta = json.dumps(meta)
        db.commit()
    run = detail(sf, identity(sf))
    assert [item['symbol'] for item in run['items']] == ['600001', '600002', '513880', '600003']
    assert run['candidate_audit']['state'] == 'saved' and 'rows' not in run['candidate_audit']
    excluded, unknown = run['items'][2:]
    assert excluded['score'] is None and excluded['state'] == 'rejected'
    assert excluded['reason_code'] == 'outside_a_share_universe'
    assert unknown['state'] == 'unknown' and '不能补成0分' in unknown['reason']
    assert run['counts']['rejected_saved'] == 1  # Original scored rejection denominator is unchanged.
    assert len(detail(sf, identity(sf), offset=2, limit=1)['items']) == 1
    daily(sf, day='2026-10-22')
    old = list_method_runs(sf, 'daily_picks', trade_date='2026-10-22')['items'][0]
    assert detail(sf, old)['candidate_audit']['state'] == 'missing'


def _selection_summary_fixture(sf, *, omitted=0):
    selected = {'symbol': '600001', 'score': 65}
    rejected = {'symbol': '600002', 'score': 45, 'selection_reason': 'below_min_score'}
    key = daily(sf, items=[selected], rejected=[rejected])
    reasons = ['selected', 'below_min_score', 'candidate_capacity', 'deep_capacity',
               'quote_missing', 'account_board_excluded', 'candidate_admitted']
    with sf() as db:
        row = db.get(DailyPickSet, key)
        meta = json.loads(row.meta)
        meta['candidate_count'] = 4
        meta['selection_funnel'] = {'scored': 2}
        meta['style_routing'] = {'style': '趋势防守', 'basis': '原权重偏移', 'routed': True}
        meta['market_phase'] = '冰点'
        meta['candidate_audit'] = {
            'version': 'daily-candidate-domain-v1', 'complete': omitted == 0,
            'input_total': 7 + omitted, 'saved_total': 7, 'omitted_total': omitted,
            'source_scope': {'candidate_capacity': 40, 'deep_capacity': 24},
            'rows': [{'symbol': f'60000{index}', 'reason_code': reason, 'input_position': index}
                     for index, reason in enumerate(reasons, 1)]}
        row.meta = json.dumps(meta)
        db.commit()
        original = (row.items, row.rejected, row.meta)
    return key, original


def test_full_selection_distribution_uses_original_input_not_scored_or_page_denominator(sf, monkeypatch):
    from app.picks import style_router
    monkeypatch.setattr(style_router, 'route_style', lambda *_: pytest.fail('must not route historical style again'))
    key, original = _selection_summary_fixture(sf)
    ident = identity(sf)
    first = detail(sf, ident, limit=1)
    tail = detail(sf, ident, offset=2, limit=2)
    summary = first['selection_summary']
    assert summary == tail['selection_summary']
    assert summary['state'] == 'saved'
    assert summary['input_total'] == 7 and first['counts']['candidates'] == 4
    assert summary['counts'] == dict(selected=1, score_rejected=1, not_evaluated=2,
                                     input_unavailable=1, excluded=1, unknown=1)
    assert sum(summary['counts'].values()) == summary['input_total']
    assert summary['saved_counts'] == summary['counts']
    assert summary['scope'] == 'recorded_inputs_not_all_market'
    assert first['items'][0]['state'] == 'selected'
    assert first['items'][0]['evaluation']['state'] == 'selected'
    assert all(row['state'] == 'rejected' and row['evaluation']['state'] == 'not_evaluated'
               for row in tail['items'])
    assert first['strategy_context'] == {
        'state': 'saved', 'market_phase': '冰点', 'style': '趋势防守',
        'style_basis': '原权重偏移', 'routed': True,
        'semantics': 'saved_ranking_style_not_execution_strategy'}
    with sf() as db:
        row = db.get(DailyPickSet, key)
        assert (row.items, row.rejected, row.meta) == original


def test_partial_audit_shows_saved_distribution_without_fabricating_whole_population(sf):
    _selection_summary_fixture(sf, omitted=3)
    summary = detail(sf, identity(sf), limit=1)['selection_summary']
    assert summary['state'] == 'partial' and summary['input_total'] == 10
    assert summary['saved_total'] == 7 and summary['omitted_total'] == 3
    assert all(value is None for value in summary['counts'].values())
    assert summary['saved_counts']['not_evaluated'] == 2
    assert sum(summary['saved_counts'].values()) == 7


@pytest.mark.parametrize('damage', ['duplicate_input', 'duplicate_scored', 'selected_rejected_conflict', 'bool_total'])
def test_candidate_summary_conflicts_do_not_create_a_complete_distribution(sf, damage):
    key, _ = _selection_summary_fixture(sf)
    with sf() as db:
        row = db.get(DailyPickSet, key)
        meta = json.loads(row.meta)
        if damage == 'duplicate_input':
            meta['candidate_audit']['rows'][-1]['symbol'] = '600001'
        elif damage == 'bool_total':
            meta['candidate_audit']['input_total'] = True
        elif damage == 'duplicate_scored':
            row.rejected = json.dumps([{'symbol': '600002', 'score': 45}] * 2)
        else:
            row.rejected = json.dumps([{'symbol': '600001', 'score': 45}])
        row.meta = json.dumps(meta)
        db.commit()
    result = detail(sf, identity(sf))
    summary = result['selection_summary']
    assert summary['state'] == 'partial'
    assert all(value is None for value in summary['counts'].values())
    assert summary['reason']
    if damage in {'duplicate_scored', 'selected_rejected_conflict'}:
        symbol = '600002' if damage == 'duplicate_scored' else '600001'
        conflicting = [item for item in result['items'] if item['symbol'] == symbol]
        assert len(conflicting) == 2
        assert all(item['evaluation']['state'] == 'unknown' for item in conflicting)
        assert {item['state'] for item in conflicting} == ({'rejected'} if damage == 'duplicate_scored'
                                                        else {'selected', 'rejected'})


def test_missing_candidate_audit_and_style_never_inherit_current_defaults(sf):
    daily(sf)
    result = detail(sf, identity(sf))
    assert result['selection_summary']['state'] == 'missing'
    assert result['selection_summary']['input_total'] is None
    assert all(value is None for value in result['selection_summary']['counts'].values())
    assert result['strategy_context']['state'] == 'missing'
    assert result['strategy_context']['style'] is None
    assert result['strategy_context']['routed'] is None


@pytest.mark.parametrize('reason', [{'value': 'below_min_score'}, ['below_min_score'], None])
def test_malformed_original_reason_is_unknown_without_breaking_other_rows(sf, reason):
    daily(sf, rejected=[{'symbol': '600001', 'score': 40, 'selection_reason': reason},
                        {'symbol': '600002', 'score': 0, 'selection_reason': 'below_min_score'}])
    items = detail(sf, identity(sf))['items']
    assert items[0]['evaluation']['state'] == 'unknown'
    assert items[1]['evaluation']['state'] == 'score_rejected'


def test_zero_input_unknown_audit_version_cannot_claim_complete_distribution(sf):
    key = daily(sf)
    with sf() as db:
        row = db.get(DailyPickSet, key)
        meta = json.loads(row.meta)
        meta['candidate_audit'] = {'version': 'future-unrecognized-version', 'complete': True,
                                   'input_total': 0, 'saved_total': 0, 'omitted_total': 0, 'rows': []}
        row.meta = json.dumps(meta)
        db.commit()
    summary = detail(sf, identity(sf))['selection_summary']
    assert summary['state'] == 'partial'
    assert all(value is None for value in summary['counts'].values())


@pytest.mark.parametrize('invalid_item', [{}, {'symbol': ''}, {'symbol': 600001}])
def test_unidentified_final_row_cannot_validate_zero_input_distribution(sf, invalid_item):
    key = daily(sf, items=[invalid_item])
    with sf() as db:
        row = db.get(DailyPickSet, key)
        meta = json.loads(row.meta)
        meta['candidate_audit'] = {'version': 'daily-candidate-domain-v1', 'complete': True,
                                  'input_total': 0, 'saved_total': 0, 'omitted_total': 0, 'rows': []}
        row.meta = json.dumps(meta)
        db.commit()
    result = detail(sf, identity(sf))
    assert result['selection_summary']['state'] == 'partial'
    assert all(value is None for value in result['selection_summary']['counts'].values())
    assert result['counts']['selected'] == 1
    assert result['items'][0]['state'] == 'selected'
