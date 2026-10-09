"""IMP-087: frozen historical references, clocks and concurrent read state."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta, timezone
import json
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.notification import NotificationReadState
from app.models.watchlist import Base
from app.services import notification_read_state as read_state


@pytest.mark.parametrize('initial', [False, True])
def test_two_read_state_writers_do_not_lose_union_or_watermark(tmp_path, monkeypatch, initial):
    engine = create_engine(f"sqlite:///{tmp_path / 'read.db'}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    assert NotificationReadState.__tablename__ in Base.metadata.tables
    if initial:
        read_state.save_state({'seen_before': 1, 'read_ids': ['prior'], 'clear_before': 1}, sf)
    entered, release = Event(), Event()
    original = read_state.merge_read_state

    def paused_merge(stored, incoming):
        if incoming['read_ids'] == ['a']:
            entered.set()
            assert release.wait(5)
        return original(stored, incoming)

    monkeypatch.setattr(read_state, 'merge_read_state', paused_merge)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read_state.save_state, {'seen_before': 10, 'read_ids': ['a'], 'clear_before': 3}, sf)
        assert entered.wait(5)
        second = pool.submit(read_state.save_state, {'seen_before': 20, 'read_ids': ['b'], 'clear_before': 7}, sf)
        try:
            second.result(timeout=.2)
        except TimeoutError:
            pass  # A correct writer serializes behind the first transaction.
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    result = read_state.load_state(sf)
    assert result['seen_before'] == 20 and result['clear_before'] == 7
    assert {'a', 'b'} <= set(result['read_ids'])
    engine.dispose()


def test_selection_message_preserves_both_original_references_and_clocks():
    from app.api.routes.notifications import _alert_items

    class Repo:
        def list_rules(self, **kwargs):
            return [SimpleNamespace(id=1, name='__selection_notifications__'),
                    SimpleNamespace(id=2, name='__picks_buy_point__')]

        def list_events(self, **kwargs):
            return [e for e in (later, first) if e.rule_id == kwargs.get('rule_id')]

        def list_events_by_dedup_keys(self, keys):
            return []

    snapshot = {'kind': 'selection', 'name': '甲', 'trade_date': '2026-10-09',
                'selection_source': 'daily', 'source_id': 'daily-A', 'source_version': 'version-A',
                'source_as_of': '2026-10-09T09:26:00+08:00',
                'selection_evidence': {'pick_basis': '原事件A', 'dimension_evidence': {'tech': {'state': 'missing'}}}}
    first = SimpleNamespace(id=11, rule_id=1, symbol='600001', snapshot=snapshot,
                            triggered_at=datetime(2026, 10, 9, 10, 2))
    later = SimpleNamespace(id=12, rule_id=2, symbol='600001', triggered_at=datetime(2026, 10, 9, 10, 3),
                            snapshot={'kind': 'buy_point', 'name': '甲', 'trade_date': '2026-10-09',
                     'execution_ref': {'decision_id': 'decision-B', 'decision_version': 'version-B'}})
    items, _ = _alert_items(Repo(), 50)
    message = items[0]
    assert message['id'] == 'alert-11'
    assert message['ts'] == snapshot['source_as_of']
    assert message['recorded_at'] == '2026-10-09 10:02:00'
    assert message['session'] == 'pre_open'
    assert [ref['event_id'] for ref in message['references']] == [11, 12]
    assert message['references'][0]['source_version'] == 'version-A'
    assert message['references'][0]['evidence'] == snapshot['selection_evidence']
    assert message['references'][1]['evidence']['execution_ref']['decision_id'] == 'decision-B'
    assert snapshot['selection_evidence']['pick_basis'] == '原事件A'


def test_selection_diagnostics_describe_projection_failure_without_claiming_no_opportunity(tmp_path):
    from app.models.daily_pick import DailyPickSet
    from app.models.opportunity_learning import OpportunityDecisionRun, OpportunityDecisionSnapshot
    from app.models.alert import AlertRule, AlertEvent
    from app.picks.notification_diagnostics import notification_diagnostics

    engine = create_engine(f"sqlite:///{tmp_path / 'diagnosis.db'}")
    Base.metadata.create_all(engine)
    assert all(model.__tablename__ in Base.metadata.tables for model in
               (OpportunityDecisionRun, OpportunityDecisionSnapshot, AlertRule, AlertEvent))
    sf = sessionmaker(bind=engine)
    with sf() as db:
        db.add(DailyPickSet(date='2026-10-09', items=json.dumps([{'symbol': '600001'}]),
               meta=json.dumps({'generated_at': '2026-10-09T09:26:00+08:00',
                                'selection_notifications': {'state': 'failed', 'reason': 'OperationalError'}})))
        db.commit()
    result = notification_diagnostics('2026-10-09', session_factory=sf)
    assert result['selection']['daily']['state'] == 'failed'
    assert result['selection']['daily']['count'] == 1
    assert result['selection']['daily']['receipt']['reason'] == 'OperationalError'
    assert result['selection']['intraday']['state'] == 'no_archive'
    engine.dispose()


@pytest.mark.parametrize('bad', ['missing_time', 'future', 'stale', 'degraded', 'invalid', 'nonpositive'])
def test_bad_quote_does_not_create_reminder_or_spend_cooldown(tmp_path, bad):
    import asyncio
    from app.market.alert_engine import AlertEngine
    from app.repositories.alert_repo import AlertRepository
    from app.repositories.watchlist_repo import WatchlistRepository

    engine = create_engine(f"sqlite:///{tmp_path / 'reminder.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    repo = AlertRepository(sf)
    rule = repo.create_rule(name='my-condition', condition_type='change_pct_above', threshold=3,
        scope='symbols', symbols=['600001'], cooldown_seconds=3600, channels=['in_app'])
    service = AlertEngine(repo, WatchlistRepository(sf))
    source_at = datetime.now(timezone.utc)
    quote = {'symbol': '600001', 'price': 11, 'change_pct': 9,
             'source': 'isolated-test', 'data_timestamp': source_at, 'quality': 'high'}
    if bad == 'missing_time':
        quote.pop('data_timestamp')
        quote['received_at'] = source_at
    elif bad == 'future':
        quote['data_timestamp'] = source_at + timedelta(hours=1)
    elif bad == 'stale':
        quote['data_timestamp'] = source_at - timedelta(hours=2)
    elif bad == 'degraded':
        quote['quality'] = 'medium'
    elif bad == 'invalid':
        quote['quality'] = 'invalid'
    else:
        quote['price'] = 0
    service.update_quotes({'600001': quote})
    asyncio.run(service._tick())
    assert repo.list_events() == []
    assert repo.get_rule(rule.id).last_triggered_at is None
    service.update_quotes({'600001': {**quote, 'price': 11, 'quality': 'high', 'data_timestamp': source_at}})
    asyncio.run(service._tick())
    assert len(repo.list_events()) == 1
    engine.dispose()


@pytest.mark.parametrize('case', ['no_source_time', 'stale', 'snapshot_stale', 'missing_quote', 'fresh'])
def test_real_risk_monitor_never_fabricates_source_time(tmp_path, monkeypatch, case):
    import asyncio
    from app.models.paper import PaperPosition
    from app.picks import exit_engine as monitor

    engine = create_engine(f"sqlite:///{tmp_path / 'monitor.db'}")
    Base.metadata.create_all(engine)
    assert PaperPosition.__tablename__ in Base.metadata.tables
    sf = sessionmaker(bind=engine)
    class Paper:
        scope = 'main'
        _sf = staticmethod(sf)
        async def settle_t1(self):
            return 0
    now = datetime.now(timezone.utc)
    quote = {'symbol': '600001', 'name': '甲', 'price': 9, 'change_pct': -5,
             'source': 'isolated-test', 'quality': 'high', 'data_timestamp': now.isoformat()}
    if case == 'no_source_time':
        quote.pop('data_timestamp')
        quote['ticktime'] = now.astimezone().strftime('%H:%M:%S')
        quote['received_at'] = now.isoformat()
    elif case == 'stale':
        quote['data_timestamp'] = (now - timedelta(hours=1)).isoformat()
    rows = [] if case == 'missing_quote' else [quote]
    snapshot = SimpleNamespace(snapshot=rows, poll_interval=60,
        freshness=lambda **_: SimpleNamespace(state='stale' if case == 'snapshot_stale' else 'ready'),
        versioned_snapshot=lambda: (rows, now))
    app = SimpleNamespace(state=SimpleNamespace(paper=Paper(), snapshot_service=snapshot))
    monkeypatch.setattr(monitor, 'load_plan', lambda: {'date': now.date().isoformat(), 'peaks': {}})
    monkeypatch.setattr(monitor, 'save_plan', lambda _: None)
    monkeypatch.setattr(monitor, '_picks_combos', lambda: {})
    monkeypatch.setattr(monitor, '_real_positions', lambda: {'600001': {'cost': 10, 'name': '甲'}})
    monkeypatch.setattr(monitor, '_REAL_READ', {**monitor._REAL_READ, 'state': 'ok'})
    monkeypatch.setattr(monitor, '_NOTIFIED', set())
    emitted = []
    monkeypatch.setattr(monitor, '_notify', lambda *a, **kw: emitted.append((a, kw)) or True)
    fired = asyncio.run(monitor.evaluate_once(app))
    risks = [e for e in emitted if e[0][3] == 'real_exit_alert']
    if case == 'fresh':
        assert len(risks) == 1
        assert risks[0][1]['source_as_of'] == quote['data_timestamp']
        assert datetime.fromisoformat(risks[0][1]['source_evidence']['quote']['data_timestamp']) == datetime.fromisoformat(quote['data_timestamp'])
        assert monitor.position_monitor_state()['real']['evaluation_state'] == 'completed'
    else:
        assert risks == []
        assert any(row['symbol'] == '600001' and row['action'] == 'degraded' for row in fired)
        status = monitor.position_monitor_state()['real']
        assert status['evaluation_state'] == 'uncompleted'
        assert status['uncompleted_symbols'] == ['600001']
    engine.dispose()


@pytest.mark.parametrize('stored,expected', [
    ({'state': 'failed', 'reason': 'OperationalError', 'evaluation_state': 'completed'}, 'uncompleted'),
    ({'state': 'unknown', 'evaluation_state': 'completed'}, 'unknown'),
    ({'state': 'empty', 'evaluation_state': 'uncompleted'}, 'empty'),
    ({'state': 'ok', 'evaluation_state': 'completed'}, 'completed'),
    ({'state': 'ok', 'evaluation_state': 'uncompleted', 'evaluation_reason': '源报价时间缺失',
      'uncompleted_symbols': ['600001']}, 'uncompleted'),
])
def test_notification_payload_exposes_live_monitor_without_new_message(monkeypatch, stored, expected):
    import asyncio
    import importlib
    from app.api.routes.notifications import notifications

    monkeypatch.setattr('app.picks.exit_engine.position_monitor_state', lambda: {'real': stored})
    monkeypatch.setattr(importlib.import_module('app.picks.notification_diagnostics'),
                        'notification_diagnostics', lambda: {'state': 'no_run'})
    repo = SimpleNamespace(list_rules=lambda: [])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(hub=None)))
    result = asyncio.run(notifications(request, alert_limit=50, news_limit=15, news_min_score=0, repo=repo))['data']
    assert result['monitor']['state'] == expected
    assert result['monitor']['uncompleted_symbols'] == stored.get('uncompleted_symbols', [])
    assert result['items'] == [] and result['count'] == 0
    if stored['state'] == 'failed':
        assert result['monitor']['reason'] == 'OperationalError'


def test_saturated_source_window_is_unknown_not_a_false_exact_end(monkeypatch):
    import asyncio
    import importlib
    from app.api.routes.notifications import notifications

    monkeypatch.setattr(importlib.import_module('app.picks.notification_diagnostics'),
                        'notification_diagnostics', lambda: {'state': 'no_run'})
    rule = SimpleNamespace(id=1, name='__picks_watcher__')
    events = [SimpleNamespace(id=i, rule_id=1, symbol='600001', triggered_at=datetime(2026, 10, 9, 10),
                              snapshot={'kind': 'pre_limit'}) for i in range(500)]
    repo = SimpleNamespace(list_rules=lambda: [rule], list_events=lambda **_: events,
                           list_events_by_dedup_keys=lambda _: [])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(hub=None)))
    result = asyncio.run(notifications(request, alert_limit=50, news_limit=15, news_min_score=0, repo=repo))['data']
    assert result['read_window']['has_more'] is None
    assert result['count'] == 0
