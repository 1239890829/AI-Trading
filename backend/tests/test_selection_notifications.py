"""Selection messages use original results and durable identities, without external consumers."""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.models.alert import AlertEvent
from app.models.agent import AgentTriage
from app.models.opportunity_learning import OpportunityDecisionRun
from app.models.watchlist import Base
from app.repositories.alert_repo import AlertRepository

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=BJ_TZ)


@pytest.fixture()
def sf(tmp_path, monkeypatch):
    import app.core.freshness as freshness
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(freshness, 'datetime', Clock)
    import app.services.selection_notifications as selection
    monkeypatch.setattr(selection, 'beijing_now', lambda: NOW)
    engine = create_engine(f"sqlite:///{tmp_path / 'selection.db'}")
    assert AgentTriage.__tablename__ and OpportunityDecisionRun.__tablename__
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def item(symbol='600001', **extra):
    return {'symbol': symbol, 'name': '甲公司', 'price': 10,
            'bases': {'news': '无消息依据', 'technical': '均线观察'},
            'confidence': {'tier': 'strong', 'label': '强观察'},
            'invalidations': ['均线破位'],
            'quote_audit': {'source': 'fixture', 'quality': 'high',
                'data_timestamp': NOW.isoformat(), 'received_at': NOW.isoformat()}, **extra}


def test_unselected_market_events_leave_opportunity_messages(sf):
    from app.api.routes.notifications import _alert_items
    repo = AlertRepository(sf)
    for name, kind in [('__picks_watcher__', 'pre_limit'), ('__source_board_reopen__', 'board_reopen')]:
        rule = repo.create_rule(name=name, scope='all')
        repo.record_trigger(rule.id, '600001', 8, 6.5, {'kind': kind, 'name': '甲', 'text': '涨幅变化'})
    assert _alert_items(repo, 50)[0] == []


def test_explicit_symbol_reminder_is_independent_of_selection_and_watchlist(sf):
    from app.api.routes.notifications import _alert_items
    repo = AlertRepository(sf)
    for scope in ['symbols', 'watchlist', 'all']:
        rule = repo.create_rule(name=f'价格提醒{scope}', condition_type='price_above',
            scope=scope, symbols=['600001'], channels=['in_app'], threshold=9)
        repo.record_trigger(rule.id, '600001', 10, 9, {'price': 10})
    rows, _ = _alert_items(repo, 50)
    assert len(rows) == 1
    assert rows[0]['category'] == 'reminder'
    assert '条件' in rows[0]['label']


def test_daily_results_durable_once_with_frozen_facts_and_no_outbox(sf):
    from app.services.selection_notifications import publish_daily_selection
    result = publish_daily_selection([item()], trade_date='2026-10-09', generated_at=NOW.isoformat(), session_factory=sf)
    assert result['created'] == 1
    again = publish_daily_selection([item(bases={'technical': '后来变化'})], trade_date='2026-10-09', generated_at=(NOW + timedelta(seconds=1)).isoformat(), session_factory=sf)
    assert again['created'] == 0
    with sf() as db:
        events = list(db.scalars(select(AlertEvent)))
        assert len(events) == 1
        snap = json.loads(events[0].snapshot)
        assert snap['selection_evidence']['bases']['technical'] == '均线观察'
        assert snap['source_as_of'] == NOW.isoformat()
        assert 'run_id' not in snap
        from app.models.notification_outbox import NotificationOutbox, BuyPointConsumption
        assert list(db.scalars(select(NotificationOutbox))) == []
        assert list(db.scalars(select(BuyPointConsumption))) == []


@pytest.mark.parametrize('audit', [None, {'quality': 'high'},
    {'source': 'fixture', 'quality': 'stale', 'data_timestamp': NOW.isoformat()},
    {'source': 'fixture', 'quality': 'high', 'data_timestamp': (NOW - timedelta(minutes=5)).isoformat()}])
def test_daily_unknown_or_stale_quote_never_becomes_fresh_generated_result(sf, audit):
    from app.services.selection_notifications import publish_daily_selection
    receipt = publish_daily_selection([item(quote_audit=audit)], trade_date='2026-10-09', generated_at=NOW.isoformat(), session_factory=sf)
    assert receipt['created'] == 0
    assert receipt['suppressed'] == 1
    assert AlertRepository(sf).list_events() == []


def intraday_payload(count=4):
    return {'hot_available': True, 'linkage_stats': {'snapshot_state': 'ready',
        'snapshot_as_of': NOW.isoformat(), 'missing_quote': 0,
        'theme_gate_counts': {'mined_with_candidates': 1}},
        'themes': [{'theme': '算力', 'stage': '发酵', 'participants': [
            {'symbol': f'60000{i}', 'name': f'公司{i}', 'price': 10, 'change_pct': 6 + i / 10,
             'tradability': {'level': '可参与', 'basis': '未封板'},
             'linkage': {'level': '高', 'basis': '原联动依据'}} for i in range(1, count + 1)]}]}


def test_intraday_exact_archive_normal_capacity_daily_same_family_and_history(sf, monkeypatch):
    from app.services.selection_notifications import publish_intraday_selection, publish_daily_selection
    from app.picks.opportunity_learning import archive_intraday_pipeline
    from app.api.routes.notifications import _alert_items
    from app.core import runtime_params
    monkeypatch.setattr(runtime_params, 'get', lambda _key, default: 2)
    payload = intraday_payload()
    archived = archive_intraday_pipeline(payload, trade_date='2026-10-09', as_of=NOW.replace(tzinfo=None), session_factory=sf)
    def publish():
        return publish_intraday_selection(payload, trade_date='2026-10-09', snapshot_as_of=NOW.isoformat(),
            snapshot_state='ready', run_id=archived['run_id'], fresh_within=180, session_factory=sf)
    assert publish()['created'] == 2
    repo = AlertRepository(sf)
    assert {e.symbol for e in repo.list_events()} == {'600003', '600004'}
    original_ids = {e.id for e in repo.list_events()}
    assert publish()['created'] == 0
    assert publish_daily_selection([item('600004')], trade_date='2026-10-09', generated_at=NOW.isoformat(), session_factory=sf)['created'] == 0
    empty = intraday_payload(0)
    archive_intraday_pipeline(empty, trade_date='2026-10-09', as_of=(NOW + timedelta(seconds=1)).replace(tzinfo=None), session_factory=sf)
    # Later empty runs change the selection view, not immutable message visibility or read identity.
    rows, _ = _alert_items(repo, 50)
    assert {int(r['id'][6:]) for r in rows} == original_ids
    assert all('入选时点' in r['validity'] for r in rows)
    with sf() as db:
        assert all(json.loads(e.snapshot)['run_id'] == archived['run_id'] for e in db.scalars(select(AlertEvent)))


def test_intraday_without_real_archive_fails_before_message(sf):
    from app.services.selection_notifications import publish_intraday_selection
    with pytest.raises(ValueError, match='exact ready archived'):
        publish_intraday_selection(intraday_payload(), trade_date='2026-10-09', snapshot_as_of=NOW.isoformat(),
            snapshot_state='ready', run_id='invented', fresh_within=180, session_factory=sf)
    assert AlertRepository(sf).list_events() == []


def test_daily_batch_rollback_preserves_picks_and_retries_after_restart(sf, monkeypatch):
    import app.services.picks_pipeline as pipeline
    import app.picks.watch_ledger as ledger
    from app.models.daily_pick import DailyPickSet
    from app.services.selection_notifications import retry_daily_selection
    monkeypatch.setattr(pipeline, 'get_session_factory', lambda: sf)
    monkeypatch.setattr(ledger, 'mark_merged_into_picks', lambda *_: None)
    original = AlertRepository.record_trigger_once_in_session
    calls = 0
    def partial(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('isolated message write failure')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(AlertRepository, 'record_trigger_once_in_session', partial)
    meta = {'generated_at': NOW.isoformat()}
    pipeline._persist_picks('2026-10-09', [item(), item('600002')], meta, [], [])
    assert meta['selection_notifications']['state'] == 'failed'
    with sf() as db:
        row = db.scalar(select(DailyPickSet))
        assert len(json.loads(row.items)) == 2, 'projection failure must not revoke successful picks'
        assert json.loads(row.meta)['selection_notifications']['state'] == 'failed'
    assert AlertRepository(sf).list_events() == [], 'a failed atomic batch must not reserve even its first daily slot'
    monkeypatch.setattr(AlertRepository, 'record_trigger_once_in_session', original)
    receipt = retry_daily_selection(trade_date='2026-10-09', session_factory=sf)
    assert receipt['created'] == 2
    assert {e.symbol for e in AlertRepository(sf).list_events()} == {'600001', '600002'}
    assert retry_daily_selection(trade_date='2026-10-09', session_factory=sf)['created'] == 2  # persisted receipt, no new call
    assert len(AlertRepository(sf).list_events()) == 2


def test_selection_and_buy_point_keep_first_id_without_merging_risk_or_reminder(sf):
    from app.services.selection_notifications import publish_daily_selection
    from app.api.routes.notifications import _alert_items
    repo = AlertRepository(sf)
    publish_daily_selection([item()], trade_date='2026-10-09', generated_at=NOW.isoformat(), session_factory=sf)
    anchor = repo.list_events()[0]
    bp = repo.create_rule(name='__picks_buy_point__', scope='all')
    repo.record_trigger(bp.id, '600001', 10, 0, {'kind': 'buy_point', 'name': '甲', 'text': '后续原买点条件', 'trade_date': '2026-10-09'})
    risk = repo.create_rule(name='__source_real_exit_alert__', scope='all')
    repo.record_trigger(risk.id, '600001', 9, 9, {'kind': 'real_exit_alert', 'text': '原持仓止损'})
    custom = repo.create_rule(name='指定甲', scope='symbols', symbols=['600001'], condition_type='price_above', channels=['in_app'])
    repo.record_trigger(custom.id, '600001', 10, 9, {'price': 10})
    rows, _ = _alert_items(repo, 50)
    assert len(rows) == 3
    opportunity = next(r for r in rows if r['category'] == 'opportunity')
    assert opportunity['id'] == f'alert-{anchor.id}'
    assert '后续原买点条件' in opportunity['body']
    assert {r['category'] for r in rows} == {'opportunity', 'risk', 'reminder'}


def test_daily_close_is_observation_not_intraday_age(sf, monkeypatch):
    from app.services.selection_notifications import publish_daily_selection
    close = NOW.replace(hour=15)
    generated = NOW.replace(hour=17)
    import app.services.selection_notifications as selection
    monkeypatch.setattr(selection, 'beijing_now', lambda: generated)
    for quality in ['high', 'medium']:
        receipt = publish_daily_selection([item('600001' if quality == 'high' else '600002', quote_audit={
            'source': 'fixture', 'quality': quality, 'data_timestamp': close.isoformat()})],
            trade_date='2026-10-09', generated_at=generated.isoformat(), session_factory=sf)
        assert receipt['created'] == 1
    assert publish_daily_selection([item('600003')], trade_date='2026-10-09',
        generated_at=generated.isoformat(), session_factory=sf)['created'] == 0, 'morning cache is not a closing observation'
    rows = AlertRepository(sf).list_events()
    assert len(rows) == 2
    assert all('15:00:00' in json.loads(e.snapshot)['text'] for e in rows)
    assert all('研究观察' in json.loads(e.snapshot)['text'] for e in rows)


def test_mixed_source_times_sort_later_risk_first_and_get_is_read_only(sf, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import event
    import app.api.routes.notifications as notifications
    from app.services.selection_notifications import publish_daily_selection
    repo = AlertRepository(sf)
    publish_daily_selection([item()], trade_date='2026-10-09', generated_at=NOW.isoformat(), session_factory=sf)
    rule = repo.create_rule(name='__source_real_exit_alert__', scope='all')
    risk = repo.record_trigger(rule.id, '600001', 9, 9, {'kind': 'real_exit_alert', 'text': '风险稍晚'})
    with sf() as db:
        db.get(AlertEvent, risk.id).triggered_at = NOW.replace(hour=13, tzinfo=None)
        db.commit()
    app = FastAPI()
    app.include_router(notifications.router, prefix='/api')
    app.dependency_overrides[notifications.get_alert_repo] = lambda: repo
    writes = []
    def capture(_conn, _cursor, statement, *_):
        if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            writes.append(statement)
    event.listen(sf.kw['bind'], 'before_cursor_execute', capture)
    try:
        with TestClient(app) as client:
            body = client.get('/api/notifications').json()['data']
            assert body['items'][0]['category'] == 'risk'
            assert body['items'][1]['ts'] == NOW.isoformat()
            assert client.get('/api/notifications').json()['data']['items'] == body['items']
    finally:
        event.remove(sf.kw['bind'], 'before_cursor_execute', capture)
    assert writes == []


def test_intraday_event_failure_retries_exact_run_before_cursor_and_after_restart(sf, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.picks import intraday_opportunity_runtime as runtime, opportunity_learning
    import app.services.selection_notifications as selection
    import app.services.market_snapshot as market_snapshot
    from app.models.opportunity_learning import OpportunityDecisionRun
    payload = intraday_payload(2)
    bundle = ({'600001': {'symbol': '600001'}}, 'ready', NOW.isoformat())
    monkeypatch.setattr(runtime, 'durable_snapshot_context', lambda _: bundle)
    monkeypatch.setattr(runtime, '_record_sightings', lambda *_: None)
    monkeypatch.setattr(opportunity_learning, 'get_session_factory', lambda: sf)
    monkeypatch.setattr(selection, 'get_session_factory', lambda: sf)
    async def build(*_args, **_kwargs):
        from copy import deepcopy
        return {'data': deepcopy(payload)}
    async def trade_date(_):
        return NOW.date()
    monkeypatch.setattr(runtime, 'build_opportunities', build)
    monkeypatch.setattr(market_snapshot, 'default_trade_date', trade_date)
    app = SimpleNamespace(state=SimpleNamespace(snapshot_service=SimpleNamespace(saved_files=1, poll_interval=60), hub=object()))
    original = AlertRepository.record_trigger_once
    def fail(*_args, **_kwargs):
        raise RuntimeError('isolated event database unavailable')
    monkeypatch.setattr(AlertRepository, 'record_trigger_once', fail)
    with pytest.raises(RuntimeError, match='isolated event'):
        asyncio.run(runtime.archive_intraday_evidence_tick(app))
    assert not hasattr(app.state, 'opportunity_evidence_saved_files')
    with sf() as db:
        assert len(list(db.scalars(select(OpportunityDecisionRun)))) == 1, 'real archive precedes event failure'
    monkeypatch.setattr(AlertRepository, 'record_trigger_once', original)
    receipt = asyncio.run(runtime.archive_intraday_evidence_tick(app))
    assert receipt['selection_notifications']['created'] == 2
    ids = {e.id for e in AlertRepository(sf).list_events()}
    restarted = SimpleNamespace(state=SimpleNamespace(snapshot_service=SimpleNamespace(saved_files=1, poll_interval=60), hub=object()))
    assert asyncio.run(runtime.archive_intraday_evidence_tick(restarted))['selection_notifications']['created'] == 0
    assert {e.id for e in AlertRepository(sf).list_events()} == ids
    assert asyncio.run(runtime.archive_intraday_evidence_tick(restarted))['state'] == 'idle'


def test_old_populated_run_cannot_emit_across_latest_empty_run(sf):
    from app.picks.opportunity_learning import archive_intraday_pipeline
    from app.services.selection_notifications import publish_intraday_selection
    data = intraday_payload()
    old = archive_intraday_pipeline(data, trade_date='2026-10-09', as_of=NOW.replace(tzinfo=None), session_factory=sf)
    archive_intraday_pipeline(intraday_payload(0), trade_date='2026-10-09', as_of=(NOW + timedelta(seconds=1)).replace(tzinfo=None), session_factory=sf)
    result = publish_intraday_selection(data, trade_date='2026-10-09', snapshot_as_of=NOW.isoformat(),
        snapshot_state='ready', run_id=old['run_id'], fresh_within=180, session_factory=sf)
    assert result['reason'] == 'run_superseded'
    assert AlertRepository(sf).list_events() == []


def test_old_ai_ignore_cannot_veto_selection_risk_or_explicit_reminder(sf):
    from app.services.selection_notifications import publish_daily_selection
    from app.api.routes.notifications import _alert_items
    repo = AlertRepository(sf)
    bp = repo.create_rule(name='__picks_buy_point__', scope='all')
    bp_event = repo.record_trigger(bp.id, '600001', 10, 0, {'kind': 'buy_point', 'name': '甲', 'trade_date': '2026-10-09'})
    publish_daily_selection([item()], trade_date='2026-10-09', generated_at=NOW.isoformat(), session_factory=sf)
    with sf() as db:
        db.add(AgentTriage(event_id=bp_event.id, verdict='ignore', reason='旧薄事件意见', model='fixture'))
        db.commit()
    rows, _ = _alert_items(repo, 50)
    assert len(rows) == 1
    assert rows[0]['label'] == '入选观察'
    assert rows[0]['id'] != f'alert-{bp_event.id}'
    assert '旧薄事件' not in rows[0]['body']


def test_all_market_volume_cannot_evict_first_selection_anchor(sf):
    from app.services.selection_notifications import publish_daily_selection
    from app.api.routes.notifications import _alert_items, _NOTIF_FETCH_LIMIT
    repo = AlertRepository(sf)
    publish_daily_selection([item()], trade_date='2026-10-09', generated_at=NOW.isoformat(), session_factory=sf)
    anchor = repo.list_events()[0].id
    rule = repo.create_rule(name='__picks_watcher__', scope='all')
    with sf() as db:
        db.add_all([AlertEvent(rule_id=rule.id, symbol='600001', trigger_value=8, threshold=6,
            snapshot=json.dumps({'kind': 'pre_limit', 'name': '甲'})) for _ in range(_NOTIF_FETCH_LIMIT + 1)])
        db.commit()
    rows, _ = _alert_items(repo, 1)
    assert len(rows) == 1
    assert rows[0]['id'] == f'alert-{anchor}'


def test_completed_empty_or_suppressed_daily_version_is_not_republished(sf):
    from app.models.daily_pick import DailyPickSet
    from app.services.selection_notifications import retry_daily_selection
    # A prior successful generation with missing quote evidence remains a quiet observation.
    with sf() as db:
        db.add(DailyPickSet(date='2026-10-09', items=json.dumps([item(quote_audit=None)]),
            meta=json.dumps({'generated_at': NOW.isoformat()}), replaced='[]', rejected='[]'))
        db.commit()
    first = retry_daily_selection(trade_date='2026-10-09', session_factory=sf)
    assert first['state'] == 'completed' and first['suppressed'] == 1
    with sf() as db:
        row = db.scalar(select(DailyPickSet))
        row.items = json.dumps([item()])
        db.commit()
    assert retry_daily_selection(trade_date='2026-10-09', session_factory=sf) == first
    assert AlertRepository(sf).list_events() == []
    # Only the original generation owner can publish a new version; reads/retries cannot invent one.


@pytest.mark.parametrize('condition,label,unit', [('price_below', '价格低于', '元'), ('change_pct_above', '涨幅高于', '%')])
def test_explicit_reminder_uses_business_words_and_original_numeric_units(sf, condition, label, unit):
    from app.api.routes.notifications import _alert_items
    repo = AlertRepository(sf)
    rule = repo.create_rule(name='我的条件', scope='symbols', symbols=['600001'],
        condition_type=condition, threshold=5.5, channels=['in_app'])
    repo.record_trigger(rule.id, '600001', 4.25, 5.5, {'price': 10})
    row = _alert_items(repo, 50)[0][0]
    assert f'{label} 5.5{unit}' in row['body']
    assert f'触发值 4.25{unit}' in row['body']
    assert condition not in row['body']


@pytest.mark.parametrize('replacement', [[], [item('600002')]])
def test_daily_captured_version_cannot_publish_after_new_version_commits(sf, replacement):
    from app.models.daily_pick import DailyPickSet
    import app.services.selection_notifications as selection
    day = NOW.date().isoformat()
    with sf() as db:
        db.add(DailyPickSet(date=day, items=json.dumps([item()]),
            meta=json.dumps({'generated_at': NOW.isoformat(), 'selection_notifications': {'state': 'pending'}}),
            replaced='[]', rejected='[]'))
        db.commit()
    calls = 0
    def replace_before_write_session():
        nonlocal calls
        calls += 1
        if calls == 2:
            with sf() as db:
                row = db.scalar(select(DailyPickSet))
                row.items = json.dumps(replacement)
                row.meta = json.dumps({'generated_at': (NOW + timedelta(seconds=1)).isoformat(),
                    'selection_notifications': {'state': 'pending'}})
                db.commit()
        return sf()
    selection.retry_daily_selection(trade_date=day, session_factory=replace_before_write_session)
    assert AlertRepository(sf).list_events() == [], 'superseded A must not claim a daily message identity'
    with sf() as db:
        current = json.loads(db.scalar(select(DailyPickSet)).meta)
        assert current['selection_notifications']['state'] == 'pending', 'A must not complete B receipt'
    assert selection.retry_daily_selection(trade_date=day, session_factory=sf)['state'] == 'completed'
    events = AlertRepository(sf).list_events()
    assert {e.symbol for e in events} == {i['symbol'] for i in replacement}
    assert all(json.loads(e.snapshot)['source_as_of'] == (NOW + timedelta(seconds=1)).isoformat() for e in events)


def test_buy_point_window_cannot_change_selection_anchor_or_channel_refs(sf):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import event
    import app.api.routes.notifications as notifications
    from app.models.notification_outbox import NotificationOutbox
    from app.picks.buy_point import _buy_point_dedup_key
    from app.api.routes.notifications import _alert_items, _NOTIF_FETCH_LIMIT
    from app.services.selection_notifications import publish_daily_selection
    repo = AlertRepository(sf)
    rule = repo.create_rule(name='__picks_buy_point__', scope='all')
    first, _ = repo.record_trigger_once(rule.id, '600001', 10, 0,
        dedup_key=_buy_point_dedup_key('2026-10-09', '600001'),
        snapshot={'kind': 'buy_point', 'name': '甲', 'text': '最早原买点', 'trade_date': '2026-10-09'})
    publish_daily_selection([item()], trade_date='2026-10-09', generated_at=NOW.isoformat(), session_factory=sf)
    with sf() as db:
        db.get(AlertEvent, first.id).triggered_at = NOW.replace(tzinfo=None)
        for i in range(_NOTIF_FETCH_LIMIT + 1):
            symbol = f'{610000 + i}'
            db.add(AlertEvent(rule_id=rule.id, symbol=symbol, trigger_value=10, threshold=0,
                triggered_at=(NOW + timedelta(seconds=i + 1)).replace(tzinfo=None),
                dedup_key=_buy_point_dedup_key('2026-10-09', symbol),
                snapshot=json.dumps({'kind': 'buy_point', 'name': '其他股', 'trade_date': '2026-10-09'})))
        db.commit()
    assert first.id not in {e.id for e in repo.list_events(limit=500, rule_id=rule.id)}
    batched_reads = []
    def capture(_conn, _cursor, statement, *_):
        if 'WHERE alert_event.dedup_key IN' in statement:
            batched_reads.append(statement)
    event.listen(sf.kw['bind'], 'before_cursor_execute', capture)
    try:
        rows, _ = _alert_items(repo, 50)
    finally:
        event.remove(sf.kw['bind'], 'before_cursor_execute', capture)
    assert len(batched_reads) == 2, 'bounded key batches replace per-stock queries/full-history scans'
    selected = next(r for r in rows if r['symbol'] == '600001')
    assert selected['id'] == f'alert-{first.id}'
    assert first.id in selected['_event_ids'], 'old channel receipt remains attached to the same message'
    assert '最早原买点' in selected['body']
    with sf() as db:
        other_ids = list(db.scalars(select(AlertEvent.id).where(AlertEvent.rule_id == rule.id, AlertEvent.id != first.id)))
        db.add_all([AgentTriage(event_id=eid, verdict='ignore', reason='旧薄事件意见', model='fixture') for eid in other_ids])
        db.add(NotificationOutbox(event_id=first.id, channel='feishu', idempotency_key='f' * 64,
            target='isolated-fixture', payload='{}', state='accepted', reason='fixture',
            created_at_ms=1, expires_at_ms=2))
        db.commit()
    app = FastAPI()
    app.include_router(notifications.router, prefix='/api')
    app.dependency_overrides[notifications.get_alert_repo] = lambda: repo
    with TestClient(app) as client:
        projected = client.get('/api/notifications').json()['data']['items']
    assert len(projected) == 1, 'supplemented raw events still pass the ignore filter'
    assert projected[0]['id'] == f'alert-{first.id}'
    assert projected[0]['channels'][0]['state'] == 'accepted'
    # An ignored original buy point is never revived by the supplementary lookup.
    with sf() as db:
        db.add(AgentTriage(event_id=first.id, verdict='ignore', reason='已过原时效', model='rules'))
        db.commit()
    rows, _ = _alert_items(repo, 50)
    assert len(rows) == 1 and rows[0]['label'] == '入选观察'
    assert '最早原买点' not in rows[0]['body']


def test_concurrent_daily_and_intraday_share_one_durable_identity(sf, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.models.daily_pick import DailyPickSet
    from app.picks.opportunity_learning import archive_intraday_pipeline
    import app.services.selection_notifications as selection
    day = NOW.date().isoformat()
    payload = intraday_payload(1)
    archived = archive_intraday_pipeline(payload, trade_date=day, as_of=NOW.replace(tzinfo=None), session_factory=sf)
    repo = AlertRepository(sf)
    repo.create_rule(name=selection.SELECTION_RULE, condition_type='source_event', scope='all', channels=['in_app', 'log'])
    with sf() as db:
        db.add(DailyPickSet(date=day, items=json.dumps([item()]),
            meta=json.dumps({'generated_at': NOW.isoformat(), 'selection_notifications': {'state': 'pending'}}),
            replaced='[]', rejected='[]'))
        db.commit()
    barrier = Barrier(2)
    original = selection._record
    def overlap(*args, **kwargs):
        barrier.wait(timeout=5)
        return original(*args, **kwargs)
    monkeypatch.setattr(selection, '_record', overlap)
    with ThreadPoolExecutor(max_workers=2) as workers:
        daily = workers.submit(selection.retry_daily_selection, trade_date=day, session_factory=sf)
        intraday = workers.submit(selection.publish_intraday_selection, payload, trade_date=day,
            snapshot_as_of=NOW.isoformat(), snapshot_state='ready', run_id=archived['run_id'],
            fresh_within=180, session_factory=sf)
        receipts = [daily.result(timeout=10), intraday.result(timeout=10)]
    assert all(r['state'] == 'completed' for r in receipts)
    assert sum(r['created'] for r in receipts) == 1
    with sf() as db:
        events = list(db.scalars(select(AlertEvent)))
        assert len(events) == 1
        assert events[0].dedup_key == selection.selection_dedup_key(day, '600001')
        assert json.loads(db.scalar(select(DailyPickSet)).meta)['selection_notifications']['state'] == 'completed'


def test_daily_rechecks_source_age_after_entering_atomic_write(sf, monkeypatch):
    import app.core.freshness as freshness
    import app.services.selection_notifications as selection
    from app.models.daily_pick import DailyPickSet
    clock = [NOW]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    monkeypatch.setattr(freshness, 'datetime', Clock)
    monkeypatch.setattr(selection, 'beijing_now', lambda: clock[0])
    with sf() as db:
        db.add(DailyPickSet(date='2026-10-09', items=json.dumps([item()]),
            meta=json.dumps({'generated_at': NOW.isoformat(), 'selection_notifications': {'state': 'pending'}}),
            replaced='[]', rejected='[]'))
        db.commit()
    calls = 0
    def delayed_write_session():
        nonlocal calls
        calls += 1
        if calls == 2:
            clock[0] = NOW + timedelta(seconds=61)
        return sf()
    receipt = selection.retry_daily_selection(trade_date='2026-10-09', session_factory=delayed_write_session)
    assert receipt['created'] == 0 and receipt['suppressed'] == 1
    assert AlertRepository(sf).list_events() == []
