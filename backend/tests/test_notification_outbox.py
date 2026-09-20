"""Outbox fault windows use separate SQLite connections and isolated HTTP only."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from threading import Barrier

import httpx
import pytest
from sqlalchemy import create_engine, event as sa_event, func, select, text
from sqlalchemy.orm import sessionmaker

from app.market.alert_engine import AlertEngine
from app.core.bjtime import BJ_TZ
from app.core.config import settings
from app.models.notification_outbox import NotificationAttempt, NotificationOutbox
from app.models.watchlist import Base
from app.notifiers import NotifierRegistry
from app.notifiers.feishu import FeishuNotifier
from app.repositories.alert_repo import AlertRepository
from app.repositories.watchlist_repo import WatchlistRepository


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "poll_interval_seconds", 5)
    monkeypatch.setattr(settings, "stale_after_seconds", 10)
    from app.models.opportunity_learning import OpportunityDecisionSnapshot, OpportunityOutcomeLabel
    assert OpportunityDecisionSnapshot.__table__.name and OpportunityOutcomeLabel.__table__.name
    engine = create_engine(f"sqlite:///{tmp_path / 'outbox.db'}")
    @sa_event.listens_for(engine, "connect")
    def foreign_keys(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repo = AlertRepository(factory)
    rule = repo.create_rule(name="threshold", condition_type="price_above", threshold=10,
                            symbols=["600000"], scope="symbols", channels=["in_app", "feishu"],
                            cooldown_seconds=300, enabled=True)
    sent = []
    response = {"body": {"code": 0}}
    async def handle(request):
        sent.append(json.loads(request.content))
        if response.get("error"):
            raise response["error"]
        return httpx.Response(200, json=response["body"])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    notifier = FeishuNotifier(webhook="https://example.test/hook/original", secret="",
                              app_id="", app_secret="", open_id="", client=client)
    registry = NotifierRegistry()
    registry.register(notifier)
    service = AlertEngine(repo, WatchlistRepository(factory))
    service._registry = registry
    quote = {"symbol": "600000", "price": 11.0, "source": "isolated-test",
             "quality": "high", "data_timestamp": datetime.now(timezone.utc)}
    service.update_quotes({"600000": quote})
    monkeypatch.setattr("app.market.trade_calendar.in_trading_window", lambda: True)
    yield repo, factory, rule, service, notifier, sent, response, quote
    asyncio.run(client.aclose())
    engine.dispose()


def rows(factory, model=NotificationOutbox):
    with factory() as db:
        return db.scalars(select(model).order_by(model.id)).all()


def queue(rig, *, lifetime=300_000):
    repo, _, rule, service, notifier, _, _, quote = rig
    now = service._now_ms()
    e = repo.record_trigger(rule.id, "600000", 11, 10, snapshot=quote,
                            outbox_target=notifier.delivery_target(), now_ms=now, expires_at_ms=now+lifetime)
    return e, repo.outbox.pending_ids()[0], now


def queue_buy_point(rig, *, price=10.5, as_of=None):
    from app.picks.opportunity_learning import archive_notification_pipeline, latest_notification_execution

    repo, factory, _, service, notifier, *_ = rig
    trade_date = "2026-09-21"
    as_of = as_of or datetime(2026, 9, 21, 10, 30)
    item = {
        "symbol": "600000", "name": "甲", "price": 10.0,
        "confidence": {"tier": "executable"}, "vetoes": [],
        "buy_range": {"low": 10.2, "high": 10.8},
    }
    hit = {"item": item, "price": price, "chg": 5.0}
    archive_notification_pipeline(
        [item], trade_date=trade_date, as_of=as_of, hits=[hit], skips=[],
        dispatch_by_symbol={}, pick_generated_at="2026-09-21T09:26:00+08:00",
        execution_by_symbol={"600000": {
            "state": "ready", "price": price, "change_pct": 5.0,
            "prev_close": 10.0, "source": "isolated-test",
        }},
        session_factory=factory,
    )
    latest = latest_notification_execution(trade_date, factory)["600000"]
    rule = repo.create_rule(
        name="buy-point", condition_type="picks_buy_point", threshold=0,
        scope="all", channels=["in_app", "feishu"], cooldown_seconds=0, enabled=True,
    )
    ref = {
        "decision_id": latest["decision_id"],
        "decision_version": latest["decision_version"],
        "execution_snapshot_state": "ready",
        "execution_snapshot_price": price,
    }
    now = service._now_ms()
    event, created = repo.record_trigger_once(
        rule.id, "600000", 0, 0,
        dedup_key="b" * 64,
        snapshot={"kind": "buy_point", "name": "甲", "card": {"header": {}}, "execution_ref": ref},
        outbox_target=notifier.delivery_target(),
        now_ms=now, expires_at_ms=now + 60_000,
        outbox_intent={
            "kind": "picks_buy_point",
            "trade_date": trade_date,
            "decision_id": latest["decision_id"],
            "decision_version": latest["decision_version"],
        },
    )
    assert created
    return event, rule, item, hit, latest


def test_rule_engine_acceptance_is_durable_and_not_delivery(rig):
    repo, factory, _, service, _, sent, _, _ = rig
    asyncio.run(service._tick())
    assert len(sent) == 1
    row, = rows(factory)
    attempt, = rows(factory, NotificationAttempt)
    assert row.state == attempt.state == "accepted"
    assert row.accepted_at_ms is not None and attempt.finished_at_ms is not None
    event, = repo.list_events()
    assert json.loads(event.delivered_channels) == ["in_app", "feishu"]
    assert event.acknowledged == 0
    assert "delivered_at" not in repo.outbox.states_for_events([event.id])[event.id][0]
    # Recreated service/repository represents restart; accepted work is not sent again.
    restarted = AlertEngine(AlertRepository(factory), WatchlistRepository(factory))
    restarted._registry = service._registry
    restarted.update_quotes(service._quotes)
    asyncio.run(restarted._deliver_pending())
    assert len(sent) == 1


@pytest.mark.parametrize("elapsed,aware,blocked", [
    (299, False, True), (300, False, False), (301, False, False), (300, True, False),
])
def test_cooldown_uses_persisted_beijing_time(rig, monkeypatch, elapsed, aware, blocked):
    repo, _, rule, service, *_ = rig
    now = datetime(2026, 9, 17, 2, 30, tzinfo=timezone.utc)
    last = (now - timedelta(seconds=elapsed)).astimezone(BJ_TZ)
    repo.update_last_triggered(rule.id, last.replace(tzinfo=None))
    persisted = repo.get_rule(rule.id)
    if aware:
        persisted.last_triggered_at = last
    monkeypatch.setattr("app.market.alert_engine.utcnow", lambda: now)
    assert service._in_cooldown(persisted) is blocked


def test_outbox_freshness_matches_quote_hub_poll_window(rig, monkeypatch):
    repo, factory, _, _, _, sent, _, quote = rig
    monkeypatch.setattr(settings, "poll_interval_seconds", 60)
    service = AlertEngine(repo, WatchlistRepository(factory))
    service._registry = rig[3]._registry
    quote["data_timestamp"] = datetime.now(timezone.utc) - timedelta(seconds=30)
    service.update_quotes({"600000": quote})
    asyncio.run(service._tick())
    row, = rows(factory)
    assert len(sent) == 1 and row.state == "accepted"
    assert row.expires_at_ms - row.created_at_ms == 60_000


@pytest.mark.parametrize("body,state", [
    ({}, "unknown"),
    ({"code": None}, "unknown"),
    ({"code": 1}, "permanent_failed"),
    ({"code": False}, "unknown"),
])
def test_typed_response_terminal_state_does_not_retry(rig, body, state):
    _, factory, _, service, _, sent, response, _ = rig
    response["body"] = body
    asyncio.run(service._tick())
    asyncio.run(service._deliver_pending())
    assert len(sent) == 1
    row, = rows(factory)
    assert row.state == state and row.accepted_at_ms is None


def test_crash_after_commit_before_dispatch_is_recoverable(rig):
    repo, factory, _, service, _, sent, _, _ = rig
    _, _, _ = queue(rig)
    assert len(repo.list_events()) == 1 and len(sent) == 0
    restarted = AlertEngine(AlertRepository(factory), WatchlistRepository(factory))
    restarted._registry = service._registry
    restarted.update_quotes(service._quotes)
    asyncio.run(restarted._deliver_pending())
    assert len(sent) == 1 and rows(factory)[0].state == "accepted"


def test_buy_point_intent_uses_dedicated_recheck_not_price_rule(rig):
    _, factory, _, service, _, sent, *_ = rig
    queue_buy_point(rig)
    # A generic scope/quote recheck would fail with no live quote. The buy-point
    # path must use the archived exact decision/version instead.
    service.update_quotes({})
    asyncio.run(service._deliver_pending())
    row, = rows(factory)
    assert row.state == "accepted" and len(sent) == 1


def test_buy_point_intent_suppressed_when_event_execution_is_not_ready(rig):
    from app.models.alert import AlertEvent

    _repo, factory, _, service, _, sent, *_ = rig
    event, *_ = queue_buy_point(rig)
    with factory() as db:
        row = db.get(AlertEvent, event.id)
        snap = json.loads(row.snapshot)
        snap["execution_ref"]["execution_snapshot_state"] = "stale"
        row.snapshot = json.dumps(snap, ensure_ascii=False)
        db.commit()

    asyncio.run(service._deliver_pending())
    outbox, = rows(factory)
    assert outbox.state == "suppressed"
    assert outbox.reason == "buy_point_event_execution_not_ready"
    assert sent == []


def test_buy_point_intent_suppressed_when_decision_version_is_superseded(rig):
    from app.picks.opportunity_learning import archive_notification_pipeline

    _, factory, _, service, _, sent, *_ = rig
    _event, _rule, item, _hit, _old = queue_buy_point(rig)
    newer_hit = {"item": item, "price": 10.6, "chg": 6.0}
    archive_notification_pipeline(
        [item], trade_date="2026-09-21", as_of=datetime(2026, 9, 21, 10, 31),
        hits=[newer_hit], skips=[], dispatch_by_symbol={},
        pick_generated_at="2026-09-21T09:26:00+08:00",
        execution_by_symbol={"600000": {
            "state": "ready", "price": 10.6, "change_pct": 6.0,
            "prev_close": 10.0, "source": "isolated-test",
        }},
        session_factory=factory,
    )
    asyncio.run(service._deliver_pending())
    row, = rows(factory)
    assert row.state == "suppressed"
    assert row.reason == "buy_point_decision_superseded"
    assert sent == []


def test_transaction_rolls_back_event_rule_and_outbox_together(rig):
    repo, factory, rule, _, _, sent, _, _ = rig
    def fail_insert(*args):
        raise RuntimeError("injected outbox storage failure")
    sa_event.listen(NotificationOutbox, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            queue(rig)
    finally:
        sa_event.remove(NotificationOutbox, "before_insert", fail_insert)
    assert repo.list_events() == [] and rows(factory) == [] and sent == []
    assert repo.get_rule(rule.id).last_triggered_at is None


def test_two_connections_cannot_create_same_durable_buy_point(rig):
    repo, factory, rule, service, notifier, *_ = rig
    repo.update_rule(rule.id, condition_type="picks_buy_point", channels=["in_app", "feishu"])
    now = service._now_ms()
    barrier = Barrier(2)

    def create_once():
        barrier.wait()
        return AlertRepository(factory).record_trigger_once(
            rule.id, "600000", 0, 0,
            dedup_key="c" * 64,
            snapshot={"kind": "buy_point", "card": {"header": {}}, "execution_ref": {
                "decision_id": "D", "decision_version": "V",
            }},
            outbox_target=notifier.delivery_target(),
            now_ms=now, expires_at_ms=now + 60_000,
            outbox_intent={
                "kind": "picks_buy_point", "trade_date": "2026-09-21",
                "decision_id": "D", "decision_version": "V",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(create_once), pool.submit(create_once)]
        created = [future.result()[1] for future in results]
    assert sum(created) == 1
    assert len(repo.list_events()) == 1
    assert len(rows(factory)) == 1


def test_buy_point_event_and_outbox_rollback_together(rig):
    repo, factory, rule, service, notifier, sent, *_ = rig
    repo.update_rule(rule.id, condition_type="picks_buy_point", channels=["in_app", "feishu"])
    now = service._now_ms()

    def fail_insert(*args):
        raise RuntimeError("injected buy-point outbox failure")

    sa_event.listen(NotificationOutbox, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            repo.record_trigger_once(
                rule.id, "600000", 0, 0,
                dedup_key="d" * 64,
                snapshot={"kind": "buy_point", "card": {"header": {}}},
                outbox_target=notifier.delivery_target(),
                now_ms=now, expires_at_ms=now + 60_000,
                outbox_intent={"kind": "picks_buy_point"},
            )
    finally:
        sa_event.remove(NotificationOutbox, "before_insert", fail_insert)
    assert repo.list_events() == []
    assert rows(factory) == []
    assert sent == []


def test_two_connections_create_one_durable_buy_point_intent(rig):
    repo, factory, rule, service, notifier, *_ = rig
    now = service._now_ms()
    barrier = Barrier(2)

    def create():
        barrier.wait()
        return AlertRepository(factory).record_trigger_once(
            rule.id, "600000", 0, 0,
            dedup_key="c" * 64,
            snapshot={"kind": "buy_point", "card": {"header": {}}, "execution_ref": {
                "decision_id": "OD-concurrent", "decision_version": "ODV-concurrent",
            }},
            outbox_target=notifier.delivery_target(),
            now_ms=now, expires_at_ms=now + 60_000,
            outbox_intent={
                "kind": "picks_buy_point", "trade_date": "2026-09-21",
                "decision_id": "OD-concurrent", "decision_version": "ODV-concurrent",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(create), pool.submit(create)
        ra, rb = a.result(), b.result()

    assert sum(created for _event, created in (ra, rb)) == 1
    assert ra[0].id == rb[0].id
    assert len(repo.list_events()) == 1
    assert len(rows(factory)) == 1


def test_two_connections_cannot_claim_same_intent(rig):
    repo, factory, *_ = rig
    _, oid, now = queue(rig)
    barrier = Barrier(2)
    def claim():
        barrier.wait()
        return AlertRepository(factory).outbox.claim(oid, now)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(claim), pool.submit(claim)
        assert sum(x is not None for x in (a.result(), b.result())) == 1
    assert len(rows(factory, NotificationAttempt)) == 1
    assert not repo.outbox.claim(oid, now)


def test_idle_reconcile_does_not_compete_with_an_existing_writer(rig):
    repo, factory, _, service, *_ = rig
    with factory().get_bind().begin() as writer:
        writer.execute(text("UPDATE alert_rule SET name='uncommitted writer'"))
        repo.outbox.reconcile(service._now_ms())
    assert rows(factory) == []


@pytest.mark.parametrize("started", [False, True])
def test_lease_recovery_distinguishes_pre_send_and_ambiguous_send(rig, started):
    repo, factory, *_ = rig
    _, oid, now = queue(rig)
    lease = repo.outbox.claim(oid, now)
    if started:
        assert repo.outbox.begin_send(lease, now)
    repo.outbox.reconcile(now + 90_001)
    row, = rows(factory)
    assert row.state == ("unknown" if started else "pending")
    assert not repo.outbox.finish(lease, "accepted", "late_result", now + 90_002)
    assert (repo.outbox.claim(oid, now + 90_003) is not None) is not started


def test_cancelled_send_and_timeout_do_not_retry_after_restart(rig):
    repo, factory, _, service, _, sent, response, _ = rig
    response["error"] = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service._tick())
    row, = rows(factory)
    assert row.state == "leased" and row.send_started_at_ms is not None
    repo.outbox.reconcile(row.lease_until_ms + 1)
    assert rows(factory)[0].state == "unknown"
    asyncio.run(service._deliver_pending())
    assert len(sent) == 1


def test_acceptance_then_database_failure_remains_unknown_not_retried(rig, monkeypatch):
    repo, factory, _, service, _, sent, *_ = rig
    def failed_finish(*args):
        raise RuntimeError("injected commit failure after acceptance")
    with monkeypatch.context() as patch:
        patch.setattr(repo.outbox, "finish", failed_finish)
        with pytest.raises(RuntimeError, match="commit failure"):
            asyncio.run(service._tick())
    assert len(sent) == 1
    row, = rows(factory)
    assert row.state == "leased" and row.accepted_at_ms is None
    repo.outbox.reconcile(row.lease_until_ms + 1)
    asyncio.run(service._deliver_pending())
    assert rows(factory)[0].state == "unknown" and len(sent) == 1


def test_two_dispatchers_send_once(rig):
    _, factory, _, service, _, sent, *_ = rig
    queue(rig)
    second = AlertEngine(AlertRepository(factory), WatchlistRepository(factory))
    second._registry = service._registry
    second.update_quotes(service._quotes)
    async def together():
        await asyncio.gather(service._deliver_pending(), second._deliver_pending())
    asyncio.run(together())
    assert len(sent) == 1
    assert rows(factory)[0].state == "accepted"
    assert len(rows(factory, NotificationAttempt)) == 1


@pytest.mark.parametrize("change,reason", [
    ("disabled", "event_or_rule_removed_or_disabled"),
    ("channels", "rule_or_channels_changed"),
    ("threshold", "rule_or_channels_changed"),
    ("recipient", "channel_unconfigured_or_target_changed"),
    ("closed", "outside_trading_window"),
    ("missing", "quote_time_unknown"),
    ("stale", "quote_not_fresh"),
    ("old", "quote_not_fresh"),
    ("future", "quote_time_untrusted"),
    ("price", "condition_no_longer_met"),
    ("deleted", "event_or_rule_removed_or_disabled"),
])
def test_pending_work_rechecks_preferences_validity_and_expiry(rig, monkeypatch, change, reason):
    repo, factory, rule, service, notifier, sent, _, quote = rig
    queue(rig)
    if change == "disabled": repo.update_rule(rule.id, enabled=False)
    elif change == "channels": repo.update_rule(rule.id, channels=["in_app"])
    elif change == "threshold": repo.update_rule(rule.id, threshold=99)
    elif change == "recipient": notifier.webhook = "https://example.test/hook/new"
    elif change == "closed": monkeypatch.setattr("app.market.trade_calendar.in_trading_window", lambda: False)
    elif change == "missing": quote.pop("data_timestamp")
    elif change == "stale": quote["quality"] = "stale"
    elif change == "old": quote["data_timestamp"] -= timedelta(hours=1)
    elif change == "future": quote["data_timestamp"] += timedelta(hours=1)
    elif change == "price": quote["price"] = 1
    elif change == "deleted": repo.delete_rule(rule.id)
    asyncio.run(service._deliver_pending())
    row, = rows(factory)
    assert row.state == "suppressed" and row.reason == reason and not sent
    assert json.loads(row.payload)["event"]["symbol"] == "600000"


def test_expired_work_never_sends(rig):
    repo, factory, _, service, _, sent, *_ = rig
    queue(rig, lifetime=-1)
    asyncio.run(service._deliver_pending())
    assert rows(factory)[0].state == "expired" and not sent
    assert not rows(factory, NotificationAttempt)


def test_event_without_feishu_preference_never_gets_external_intent(rig):
    repo, factory, rule, service, _, sent, *_ = rig
    repo.update_rule(rule.id, channels=["in_app"])
    asyncio.run(service._tick())
    assert len(repo.list_events()) == 1 and not rows(factory) and not sent


def test_unconfigured_channel_is_suppressed(rig):
    _, factory, _, service, notifier, sent, *_ = rig
    notifier.webhook = ""
    asyncio.run(service._tick())
    assert rows(factory)[0].reason == "channel_unconfigured_or_target_changed" and not sent


def test_pre_send_lease_expiry_prevents_io(rig):
    repo, factory, *_ = rig
    _, oid, now = queue(rig, lifetime=100)
    lease = repo.outbox.claim(oid, now)
    assert not repo.outbox.begin_send(lease, now+101)
    assert rows(factory)[0].send_started_at_ms is None


def test_deletion_during_send_preserves_attempt_and_cannot_mark_new_event(rig):
    repo, factory, rule, *_ = rig
    _, oid, now = queue(rig)
    lease = repo.outbox.claim(oid, now)
    assert repo.outbox.begin_send(lease, now)
    repo.delete_rule(rule.id)
    new_rule = repo.create_rule(name="new", channels=["in_app"])
    new = repo.record_trigger(new_rule.id, "600001", 1, 1)
    assert repo.outbox.finish(lease, "accepted", "platform_accepted", now+1)
    assert repo.get_event(new.id).delivered_channels is None
    assert rows(factory)[0].event_id is None
    assert rows(factory, NotificationAttempt)[0].state == "accepted"


def test_event_api_exposes_channel_state_without_claiming_delivered(client):
    from app.main import app
    repo = app.state.alert_repo
    rule = repo.create_rule(name="outbox-api", channels=["feishu"])
    event = repo.record_trigger(rule.id, "600000", 11, 10, outbox_target="opaque-target",
                                now_ms=1, expires_at_ms=2)
    repo.outbox.reconcile(3)
    data = client.get("/api/alerts/events", params={"rule_id": rule.id}).json()["data"]
    found, = [e for e in data if e["id"] == event.id]
    state, = found["channel_states"]
    assert state["state"] == "expired" and state["accepted_at_ms"] is None
    assert found["delivered_channels"] == [] and found["acknowledged"] is False
    repo.delete_rule(rule.id)


def test_new_outbox_schema_matches_models_and_old_events_are_not_backfilled(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from app.core.migrations import BACKEND_DIR, run_migrations
    from tests.test_db_migrations import _schema_drift

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["configure_logger"] = False
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "d2e4a6b8c0f1")
        # Build a genuine pre-dedup historical event without importing the new
        # ORM column into the old schema.
        conn.execute(text("""
            INSERT INTO alert_rule
            (id,name,enabled,condition_type,threshold,symbols,scope,cooldown_seconds,channels,
             last_triggered_at,created_at,updated_at)
            VALUES (1,'old',1,'price_above',10,NULL,'symbols',300,'["feishu"]',
                    NULL,'2026-09-20 09:00:00','2026-09-20 09:00:00')
        """))
        conn.execute(text("""
            INSERT INTO alert_event
            (id,rule_id,symbol,trigger_value,threshold,triggered_at,acknowledged,delivered_channels,snapshot)
            VALUES (1,1,'600000',11,10,'2026-09-20 10:00:00',0,NULL,NULL)
        """))
    assert run_migrations(engine) == "upgraded"
    assert run_migrations(engine) == "upgraded"

    for model in (NotificationOutbox, NotificationAttempt):
        assert not _schema_drift(engine, model)
        with engine.connect() as conn:
            assert conn.scalar(select(func.count()).select_from(model)) == 0

    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("alert_event")}
    indexes = {i["name"]: i for i in insp.get_indexes("alert_event")}
    assert cols["dedup_key"]["nullable"] is True
    assert bool(indexes["ux_alert_event_dedup_key"]["unique"]) is True
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT dedup_key FROM alert_event WHERE id=1")) is None
    engine.dispose()
