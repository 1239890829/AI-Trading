"""Health episodes use the existing outbox, isolated DB and HTTP transport only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.notification_outbox import NotificationOutbox
from app.models.watchlist import Base
from app.market.alert_engine import AlertEngine
from app.notifiers import NotifierRegistry
from app.notifiers.feishu import FeishuNotifier
from app.repositories.alert_repo import AlertRepository
from app.repositories.watchlist_repo import WatchlistRepository
from app.services.data_health_loop import _push_anomaly


@pytest.fixture
def health(tmp_path, monkeypatch):
    from app.picks import source_events

    db_engine = create_engine(f"sqlite:///{tmp_path / 'health.db'}")
    Base.metadata.create_all(db_engine)
    sf = sessionmaker(bind=db_engine, expire_on_commit=False)
    sent, result = [], {'unknown': False}
    async def transport(request):
        sent.append(json.loads(request.content))
        if result['unknown']:
            raise httpx.ReadTimeout('receipt lost')
        return httpx.Response(200, json={'code': 0})
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    notifier = FeishuNotifier(webhook='https://example.test/health', secret='', app_id='',
                              app_secret='', open_id='', client=client)
    registry = NotifierRegistry()
    registry.register(notifier)
    monkeypatch.setattr('app.notifiers.get_notifier_registry', lambda: registry)
    monkeypatch.setattr(source_events, 'get_notifier_registry', lambda: registry)
    monkeypatch.setattr('app.core.db.get_session_factory', lambda: sf)
    monkeypatch.setattr('app.market.trade_calendar.in_trading_window', lambda *a, **k: True)
    repo = AlertRepository(sf)
    service = AlertEngine(repo, WatchlistRepository(sf))
    service._registry = registry
    app = SimpleNamespace()
    yield sf, repo, service, sent, result, app
    asyncio.run(client.aclose())
    db_engine.dispose()


def _outboxes(sf):
    with sf() as db:
        return db.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id)).all()


def test_health_unknown_receipt_survives_next_probe_without_resending(health):
    sf, repo, service, sent, result, app = health
    result['unknown'] = True
    now = datetime.now(timezone.utc)
    asyncio.run(_push_anomaly(now, ['数据停更'], app))
    asyncio.run(service._deliver_pending())
    asyncio.run(_push_anomaly(now, ['数据停更'], app))
    asyncio.run(service._deliver_pending())
    assert len(sent) == 1
    box, = _outboxes(sf)
    assert box.state == 'unknown'
    assert len(repo.list_events()) == 1


def test_concurrent_probes_share_one_durable_health_episode(health):
    sf, repo, service, sent, result, app = health
    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: asyncio.run(_push_anomaly(now, ['数据停更'], app)), range(2)))
    assert {r['state'] for r in receipts} == {'queued', 'unchanged'}
    assert len(_outboxes(sf)) == 1 and len(repo.list_events()) == 1 and sent == []


def test_pre_send_unavailable_recheck_is_retryable_without_starting_send(health, monkeypatch):
    sf, repo, service, sent, result, app = health
    now = datetime.now(timezone.utc)
    asyncio.run(_push_anomaly(now, ['数据停更'], app))
    original = service._source_event_delivery_block
    monkeypatch.setattr(service, '_source_event_delivery_block', lambda *a: 'source_recheck_unavailable')
    asyncio.run(service._deliver_pending())
    box, = _outboxes(sf)
    assert box.state == 'leased' and box.send_started_at_ms is None and sent == []
    monkeypatch.setattr(service, '_source_event_delivery_block', original)
    repo.outbox.reconcile(box.lease_until_ms)
    asyncio.run(service._deliver_pending())
    box, = _outboxes(sf)
    assert box.state == 'accepted' and len(sent) == 1


def test_recovery_suppresses_queued_intent_before_external_send(health):
    sf, repo, service, sent, result, app = health
    now = datetime.now(timezone.utc)
    asyncio.run(_push_anomaly(now, ['数据停更'], app))
    asyncio.run(_push_anomaly(now, [], app))
    asyncio.run(service._deliver_pending())
    box, = _outboxes(sf)
    assert box.state == 'suppressed' and box.reason == 'health_anomaly_recovered'
    assert sent == []
    event, = repo.list_events()
    assert json.loads(event.snapshot)['source_evidence']['issues'] == ['数据停更']
    # A later recurrence is a new episode, not a retry of an unknown send.
    asyncio.run(_push_anomaly(now, ['数据停更'], app))
    asyncio.run(service._deliver_pending())
    assert len(sent) == 1
    assert [row.state for row in _outboxes(sf)] == ['suppressed', 'accepted']


def test_pre_enqueue_failure_does_not_consume_the_health_episode(health, monkeypatch):
    sf, repo, service, sent, result, app = health
    original = AlertRepository.record_trigger_once_in_session
    def fail(*a, **k):
        raise RuntimeError('isolated enqueue unavailable')
    monkeypatch.setattr(AlertRepository, 'record_trigger_once_in_session', fail)
    now = datetime.now(timezone.utc)
    failed = asyncio.run(_push_anomaly(now, ['数据停更'], app))
    assert failed['state'] == 'failed' and _outboxes(sf) == [] and sent == []
    monkeypatch.setattr(AlertRepository, 'record_trigger_once_in_session', original)
    queued = asyncio.run(_push_anomaly(now, ['数据停更'], app))
    assert queued['state'] == 'queued'
    asyncio.run(service._deliver_pending())
    box, = _outboxes(sf)
    assert box.state == 'accepted' and len(sent) == 1


def test_partial_recovery_preserves_original_fact_and_only_sends_active_issues(health):
    sf, repo, service, sent, result, app = health
    now = datetime.now(timezone.utc)
    asyncio.run(_push_anomaly(now, ['停更A', '故障B'], app))
    asyncio.run(_push_anomaly(now, ['故障B'], app))
    asyncio.run(service._deliver_pending())
    assert len(sent) == 1
    card = sent[0]['card']['elements'][0]['text']['content']
    assert '故障B' in card and '停更A' not in card
    event, = repo.list_events()
    assert json.loads(event.snapshot)['source_evidence']['issues'] == ['停更A', '故障B']
