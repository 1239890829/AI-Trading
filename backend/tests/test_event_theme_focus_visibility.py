"""Theme focus must not revive unavailable event directions."""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import events as routes
from app.events.extract import build_event
from app.events.store import EventStore
from app.models.event import EventInterpretation
from app.models.watchlist import Base


def test_theme_focus_filters_invisible_and_withdrawn_before_limit(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'focus.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = EventStore(sf)
    now = datetime(2026, 9, 26, 10, 0)
    monkeypatch.setattr(routes, "beijing_now_naive", lambda: now)
    monkeypatch.setattr("app.events.store.beijing_now_naive", lambda: now)

    def add(title, published):
        event = build_event(title, source="东财快讯", published_at=published)
        event["directions"] = [{"target_type": "theme", "target": "算力", "direction": 1,
                                 "strength": 1, "chain": "事件关联", "basis": title}]
        return store.add_event(event)[0]

    eligible = add("算力订单已签署", now - timedelta(hours=1))
    withdrawn = add("算力旧订单已撤销", now - timedelta(minutes=30))
    store.set_status(withdrawn.id, "resolved")
    future_version = add("算力待公布补充协议", now - timedelta(minutes=20))
    add("算力明日公告新订单", now + timedelta(hours=1))
    with sf() as db:
        version = db.query(EventInterpretation).filter_by(event_id=future_version.id).one()
        version.effective_at = now + timedelta(hours=1)
        db.commit()

    result = asyncio.run(routes.theme_focus(None, store=store, days=1, limit=8))["data"]
    assert result["count"] == 1
    assert result["items"][0]["events"] == 1
    assert result["items"][0]["samples"][0]["title"] == eligible.title

    # The SQL gate must run before LIMIT; a newer invisible card cannot hide
    # an older visible one even when the caller asks for one event.
    rows = store.list_events(active_only=False, limit=1, published_since=now - timedelta(days=1),
                             visible_at=now, status="active", exclude_pending=True)
    assert [row.id for row in rows] == [eligible.id]

    # Equality is visible. A newer pending correction must still be excluded
    # before LIMIT, leaving the exact-boundary interpretation available.
    with sf() as db:
        version = db.query(EventInterpretation).filter_by(event_id=future_version.id).one()
        version.effective_at = now
        db.commit()
    pending = add("算力新订单传闻", now - timedelta(minutes=10))
    corrected = build_event(pending.title, source="东财快讯",
                            summary="金额仍在核实", published_at=pending.published_at)
    store.add_event(corrected)
    assert store.get_event(pending.id).revision_pending_at is not None
    result = asyncio.run(routes.theme_focus(None, store=store, days=1, limit=8))["data"]
    assert result["items"][0]["events"] == 2
    rows = store.list_events(active_only=False, limit=1, published_since=now - timedelta(days=1),
                             visible_at=now, status="active", exclude_pending=True)
    assert [row.id for row in rows] == [future_version.id]


def test_theme_focus_does_not_count_llm_hypothesis_as_judged_direction(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'hypothesis-focus.db'}")
    Base.metadata.create_all(engine)
    store = EventStore(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    now = datetime(2026, 9, 26, 10, 0)
    monkeypatch.setattr(routes, "beijing_now_naive", lambda: now)
    monkeypatch.setattr("app.events.store.beijing_now_naive", lambda: now)
    for event_id, matched_by in (("rule", "name"), ("model", "llm_aux")):
        store.add_event({
            "fingerprint": event_id, "title": f"算力消息 {event_id}",
            "source": "东财快讯", "source_item_id": event_id,
            "published_at": now - timedelta(minutes=10),
            "directions": [{"target_type": "theme", "target": "算力", "direction": 1,
                            "matched_by": matched_by}],
        })

    result = asyncio.run(routes.theme_focus(None, store=store, days=1, limit=8))["data"]
    assert result["count"] == 1
    assert result["items"][0]["events"] == 1
    assert result["items"][0]["net"] == 1
    assert result["items"][0]["judged"] == 1
