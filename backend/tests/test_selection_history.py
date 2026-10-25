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
