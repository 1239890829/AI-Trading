"""The UI must read frozen facts without creating samples or predicting outcomes."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.models.opportunity_learning import OpportunityDecisionSnapshot as Snapshot
from app.picks.opportunity_learning import archive_intraday_pipeline
from app.picks.opportunity_view import read_opportunities
from app.models.leader_research import LeaderResearchObservation as Observation


@pytest.fixture
def sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated.db'}")
    Observation.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def payload(theme="算力", participant=True):
    return {"hot_available": True, "linkage_stats": {
        "snapshot_state": "ready", "snapshot_as_of": "2026-09-30T10:00:00+08:00", "missing_quote": 0,
        "theme_gate_counts": {"mined_with_candidates" if participant else "not_concentrated": 1},
    }, "themes": [{"theme": theme, "stage": "发酵", "strength_tier": "强势", "participants": [{
        "symbol": "600127", "name": "金健米业", "price": 10, "change_pct": 6.8,
        "tradability": {"level": "可参与", "basis": "未封板"},
        "linkage": {"level": "高", "basis": "相对强势"},
    }] if participant else []}]}


def archive(sf, data, minute):
    return archive_intraday_pipeline(data, trade_date="2026-09-30",
                                     as_of=datetime(2026, 9, 30, 10, minute), session_factory=sf)


def test_empty_uncollected_and_latest_zero_run_are_distinct(sf):
    assert read_opportunities("2026-09-30", sf)["state"] == "not_collected"
    archive(sf, payload(), 0)
    first = read_opportunities("2026-09-30", sf)
    assert len(first["cards"]) == 1
    assert first["cards"][0]["hypotheses"][0]["actionable"] is False
    archive(sf, payload(participant=False), 1)
    empty = read_opportunities("2026-09-30", sf)
    assert empty["state"] == "collected_empty"
    assert empty["cards"] == [], "old accepted cards must not survive a new empty run"
    assert empty["runs"][0]["gate_counts"] == {"not_concentrated": 1}


def test_polling_is_read_only_and_material_change_preserves_history(sf):
    archive(sf, payload(), 0)
    before = read_opportunities("2026-09-30", sf)
    h = before["cards"][0]["hypotheses"][0]
    archive(sf, payload(), 1)
    again = read_opportunities("2026-09-30", sf)["cards"][0]["hypotheses"][0]
    assert again["decision_id"] == h["decision_id"]
    assert again["decision_version"] == h["decision_version"]
    changed = payload(); changed["themes"][0]["participants"][0]["price"] = 11
    archive(sf, changed, 2)
    later = read_opportunities("2026-09-30", sf)["cards"][0]["hypotheses"][0]
    assert later["first_seen"] == h["first_seen"]
    assert later["decision_id"] == h["decision_id"]
    assert later["decision_version"] != h["decision_version"]
    writes = []
    engine = sf.kw['bind']
    def capture(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for _ in range(3):
            read_opportunities("2026-09-30", sf)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert writes == []
    with sf() as db:
        old = db.scalar(select(Snapshot).where(Snapshot.snapshot_id == h["snapshot_refs"][-1]))
        assert old.entry_price == 10


def test_two_paths_same_symbol_and_unknown_scenario_are_preserved(sf):
    data = payload(); data['themes'].append(payload('农业')['themes'][0])
    archive(sf, data, 0)
    with sf() as db:
        rows = list(db.scalars(select(Snapshot)).all())
        # An unfamiliar future path is displayed with its original identity, not forced into four classes.
        for row in rows:
            if row.source_theme == '农业':
                row.scenario = 'new_unclassified_path'
        db.commit()
    card = read_opportunities("2026-09-30", sf)['cards'][0]
    assert len(card['hypotheses']) == 2
    assert len({h['opportunity_id'] for h in card['hypotheses']}) == 2
    assert 'new_unclassified_path' in {h['scenario'] for h in card['hypotheses']}


def test_unknown_quotes_and_corrupt_evidence_never_enable_execution(sf):
    data = payload(); data['linkage_stats']['snapshot_state'] = 'stale'
    archive(sf, data, 0)
    with sf() as db:
        row = db.scalar(select(Snapshot).where(Snapshot.stage == 'rank'))
        row.evidence = 'invalid'
        db.commit()
    h = read_opportunities("2026-09-30", sf)['cards'][0]['hypotheses'][0]
    assert h['state'] == 'unknown'
    assert h['actionable'] is False
    assert '来源或行情质量未就绪' in h['unknowns']
    assert h['expires_at'] is None
    with pytest.raises(ValueError):
        read_opportunities('2026-02-30', sf)


def test_refresh_metadata_is_not_a_new_decision_but_counterevidence_is(sf):
    data = payload(); data['themes'][0]['_candidate_audit'] = [{
        'symbol': '600127', 'candidate_decision': 'included', 'hard_gate_decision': 'passed',
        'facts': {'version': '2026-09-30T10:00:00', 'current_sealed': False}, 'price': 10,
    }]
    archive(sf, data, 0)
    old = read_opportunities('2026-09-30', sf)['cards'][0]['hypotheses'][0]
    data['themes'][0]['_candidate_audit'][0]['facts']['version'] = '2026-09-30T10:01:00'
    archive(sf, data, 1)
    refreshed = read_opportunities('2026-09-30', sf)['cards'][0]['hypotheses'][0]
    assert old['decision_version'] == refreshed['decision_version']
    data['themes'][0]['_candidate_audit'][0]['facts']['current_sealed'] = True
    archive(sf, data, 2)
    changed = read_opportunities('2026-09-30', sf)['cards'][0]['hypotheses'][0]
    assert old['decision_version'] != changed['decision_version']


def test_malformed_nested_evidence_remains_visible_without_crashing(sf):
    archive(sf, payload(), 0)
    with sf() as db:
        row = db.scalar(select(Snapshot).where(Snapshot.stage == 'hard_gate'))
        row.evidence = '{"facts": "broken", "seal_state": []}'
        db.commit()
    h = read_opportunities('2026-09-30', sf)['cards'][0]['hypotheses'][0]
    assert h['evidence']['hard_gate']['facts'] == 'broken'
    assert h['actionable'] is False


def test_actual_http_consumer_is_read_only_and_invalid_dates_are_rejected(sf, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes.picks_intraday import router
    from app.picks import opportunity_view
    archive(sf, payload(), 0)
    monkeypatch.setattr(opportunity_view, 'get_session_factory', lambda: sf)
    app = FastAPI(); app.include_router(router, prefix="/api")
    writes = []
    engine = sf.kw['bind']
    def capture(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            writes.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        with TestClient(app) as client:
            for _ in range(3):
                response = client.get('/api/picks/opportunities?date=2026-09-30')
                assert response.status_code == 200
                assert response.json()['meta']['read_only'] is True
            assert client.get('/api/picks/opportunities?date=2026-02-30').status_code == 422
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert writes == []


def test_cold_and_warm_reads_do_not_write_but_real_background_tick_does(sf, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.picks import intraday_opportunity_runtime as runtime, opportunity_learning
    import app.market.trade_calendar as calendar
    import app.services.market_snapshot as snapshot_service
    app = SimpleNamespace(state=SimpleNamespace(snapshot_service=SimpleNamespace(saved_files=1), hub=object()))
    data = payload(); data['trade_date'] = '2026-09-30'
    async def assemble(*_args, **_kwargs):
        return {'data': data, 'meta': {}}
    async def trade_date(_hub):
        from datetime import date
        return date(2026, 9, 30)
    bundle = ({}, 'ready', '2026-09-30T10:00:00+08:00')
    monkeypatch.setattr(runtime, '_build_opportunities_uncached', assemble)
    monkeypatch.setattr(runtime, 'attach_risk_to_themes', lambda *_: None)
    monkeypatch.setattr(runtime, 'durable_snapshot_context', lambda _: bundle)
    monkeypatch.setattr(opportunity_learning, 'get_session_factory', lambda: sf)
    monkeypatch.setattr(calendar, 'in_trading_window', lambda _: True)
    monkeypatch.setattr(snapshot_service, 'default_trade_date', trade_date)
    sightings = []
    def record(*args):
        sightings.append(args)
        if len(sightings) == 1:
            raise RuntimeError('isolated ledger unavailable')
    monkeypatch.setattr(runtime, '_record_sightings', record)
    for _ in range(3):
        asyncio.run(runtime.build_opportunities(app, '2026-09-30', 5, 8, snapshot_bundle=bundle))
    with sf() as db:
        assert list(db.scalars(select(Snapshot))) == []
    with pytest.raises(RuntimeError, match='isolated ledger unavailable'):
        asyncio.run(runtime.archive_intraday_evidence_tick(app))
    assert not hasattr(app.state, 'opportunity_evidence_saved_files')
    tick = asyncio.run(runtime.archive_intraday_evidence_tick(app))
    assert tick['state'] == 'archived'
    assert tick['records'] == 3
    assert len(sightings) == 2
    with sf() as db:
        assert len(list(db.scalars(select(Snapshot)))) == 3, 'retry cannot multiply the evidence denominator'
    assert sightings[0][1] == bundle[2]
    assert asyncio.run(runtime.archive_intraday_evidence_tick(app))['state'] == 'idle'
    assert read_opportunities('2026-09-30', sf)['cards'][0]['hypotheses'][0]['first_seen'] == '2026-09-30T10:00:00'


def test_zero_run_with_failed_source_is_not_a_valid_empty_pool(sf):
    data = payload(participant=False)
    data['linkage_stats']['snapshot_state'] = 'stale'
    archive(sf, data, 0)
    result = read_opportunities('2026-09-30', sf)
    assert result['state'] == 'unavailable'
    assert result['cards'] == []
    assert result['runs'][0]['data_state'] == 'degraded'
    # Fresh market quotes do not compensate for an unavailable theme constituent list.
    data = payload(participant=False)
    data['linkage_stats']['theme_gate_counts'] = {'mined_empty_unavailable': 1}
    archive(sf, data, 1)
    result = read_opportunities('2026-09-30', sf)
    assert result['state'] == 'unavailable'
    assert result['runs'][0]['data_state'] == 'ready'


def test_notification_consumer_preserves_execution_owner_identity_and_gate(sf):
    from app.picks.opportunity_learning import archive_notification_pipeline, latest_notification_execution
    item = {'symbol': '600127', 'name': '金健米业', 'price': 10,
            'confidence': {'tier': '高'}, 'buy_range': [9.8, 10.2], 'vetoes': []}
    archive_notification_pipeline([item], trade_date='2026-09-30', hits=[{'symbol': '600127'}],
        skips=[], dispatch_by_symbol={'600127': 'sent'}, as_of=datetime(2026, 9, 30, 10),
        pick_generated_at='2026-09-30T09:00:00', session_factory=sf,
        execution_by_symbol={'600127': {'state': 'ready', 'price': 10, 'source': 'test'}})
    owner = latest_notification_execution('2026-09-30', sf)['600127']
    h = read_opportunities('2026-09-30', sf)['cards'][0]['hypotheses'][0]
    assert (h['decision_id'], h['decision_version']) == (owner['decision_id'], owner['decision_version'])
    assert h['entry_conditions']['buy_range'] == [9.8, 10.2]
    assert h['actionable'] is False, 'archived notification is not current execution authority'


def test_research_company_event_and_trend_keep_refs_without_future_outcome_fields(sf):
    from datetime import date
    from app.research.leader_followthrough import capture
    now = datetime(2026, 9, 30, 10)
    ref = {'event_id': 1, 'version_id': 1, 'state': 'active', 'direction': 1,
           'published_at': now.isoformat(), 'available_at': now.isoformat(), 'route': 'company_event'}
    capture([{'symbol': '600127', 'name': '金健米业', 'price': 10, 'change_pct': 10},
             {'symbol': '600001', 'name': '对照', 'price': 10, 'change_pct': 0}],
            {'600127': [ref]}, as_of=now, source_as_of=now, trading_days=[date(2026, 9, 30)],
            source='isolated-test', session_factory=sf)
    h = read_opportunities('2026-09-30', sf)['cards'][0]['hypotheses'][0]
    assert h['routes'] == ['company_event', 'trend']
    assert h['event_refs'] == [ref]
    assert h['decision_version'] is None and not h['actionable']
    assert 'outcomes' not in h and 'return_pct' not in h
    assert h['execution_blocker'] == '封板附近，无法假定参与'


def test_intraday_top_retains_frozen_reference_and_risk_in_both_groups(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.api.routes import picks_intraday as route
    data = payload()
    participant = data['themes'][0]['participants'][0]
    participant.update(stop_ref={'price': 9}, exit_plan={'basis': 'frozen'})
    data['themes'][0]['stocks'] = [{'symbol': '600001', 'boards': 2, 'price': 7,
                                   'stop_ref': {'price': 6}, 'exit_plan': {'basis': 'old'}}]
    async def built(*_args):
        return {'data': data, 'meta': {}}
    async def date(*_args):
        return '2026-09-30'
    monkeypatch.setattr(route, '_build_opportunities', built)
    monkeypatch.setattr(route, 'default_trade_date', date)
    # No snapshot service is available after assembly: it must not reread live facts.
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(hub=object())))
    result = asyncio.run(route.intraday_top(request, limit=8))['data']
    assert result['items'][0]['price'] == 10
    assert result['items'][0]['stop_ref'] == {'price': 9}
    assert result['reference_items'][0]['price'] == 7
    assert result['reference_items'][0]['exit_plan'] == {'basis': 'old'}


def test_nonfinite_legacy_facts_cannot_break_the_http_read(sf, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes.picks_intraday import router
    from app.picks import opportunity_view
    archive(sf, payload(), 0)
    with sf() as db:
        for row in db.scalars(select(Snapshot)):
            row.entry_price = float('inf')
            row.evidence = '{"price": Infinity}'
        db.commit()
    monkeypatch.setattr(opportunity_view, 'get_session_factory', lambda: sf)
    app = FastAPI(); app.include_router(router, prefix='/api')
    with TestClient(app) as client:
        response = client.get('/api/picks/opportunities?date=2026-09-30')
        assert response.status_code == 200
        h = response.json()['data']['cards'][0]['hypotheses'][0]
        assert h['state'] == 'unknown' and h['reference']['price'] is None
