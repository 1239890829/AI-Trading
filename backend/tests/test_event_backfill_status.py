"""Direction backfill cannot revive withdrawn event evidence."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import events as routes
from app.events.store import EventStore
from app.models.watchlist import Base


def _store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'backfill.db'}")
    Base.metadata.create_all(engine)
    return EventStore(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))


@pytest.mark.parametrize("status", ["resolved", "rejected"])
def test_backfill_cannot_append_active_version_to_withdrawn_card(tmp_path, status):
    store = _store(tmp_path)
    row, _ = store.register("算力订单公告", source="东财快讯", theme_names=[])
    store.set_status(row.id, status)
    withdrawn_id = store.interpretations_of(row.id)[-1].id

    filled = store.backfill_event(row.id, category="corporate", half_life_hours=48,
                                  directions=[{"target_type": "theme", "target": "算力",
                                               "direction": 1, "basis": "算力订单公告"}])

    assert filled == 0
    assert store.directions_of(row.id) == []
    assert store.interpretations_of(row.id)[-1].id == withdrawn_id
    assert store.get_event(row.id).status == status


def test_backfill_route_skips_withdrawn_card_before_scan(tmp_path):
    store = _store(tmp_path)
    row, _ = store.register("算力订单公告", source="东财快讯", theme_names=[])
    store.set_status(row.id, "resolved")
    catalog = SimpleNamespace(catalog_size=lambda: 1,
                              get_catalog=lambda limit: [SimpleNamespace(name="算力")])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(theme_catalog=catalog)))

    result = asyncio.run(routes.backfill_directions(request, store=store, days=3))["data"]

    assert result["scanned"] == 0
    assert result["filled"] == 0
    assert store.directions_of(row.id) == []
