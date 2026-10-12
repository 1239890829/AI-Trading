"""Missing stage facts stay unknown; saved decisions are not retrospectively rewritten."""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.events.extract import build_event
from app.events.store import EventStore
from app.models.event import EventCard, EventInterpretation
from app.models.watchlist import Base


@pytest.fixture
def storage(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'stage.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    yield EventStore(sf), sf
    engine.dispose()


@pytest.mark.parametrize("stage", ["omitted", None, ""])
def test_missing_stage_is_saved_as_unknown_in_current_and_frozen_versions(storage, stage):
    store, sf = storage
    item = {
        "title": "来源提供的公司事项，阶段未提供",
        "fingerprint": f"missing-{stage}",
        "source": "来源待核", "source_item_id": f"stage-{stage}",
        "published_at": datetime(2026, 10, 12, 9, 0),
        "directions": [],
    }
    if stage != "omitted":
        item["certainty"] = stage
    row, created = store.add_event(item)
    assert created
    assert row.certainty == "unknown"
    with sf() as session:
        version = session.scalar(select(EventInterpretation).where(EventInterpretation.event_id == row.id))
        assert json.loads(version.payload_json)["certainty"] == "unknown"


def test_model_default_does_not_invent_a_completed_event(storage):
    _, sf = storage
    with sf() as session:
        row = EventCard(title="未给阶段", fingerprint="orm-default")
        session.add(row)
        session.commit()
        assert row.certainty == "unknown"


def test_known_historical_stage_remains_unchanged_on_repeat_capture(storage):
    store, sf = storage
    item = {
        "title": "已披露订单", "fingerprint": "known-stage",
        "source": "公司公告", "source_item_id": "original",
        "certainty": "done", "published_at": datetime(2026, 10, 12, 9, 0),
        "directions": [],
    }
    row, _ = store.add_event(item)
    with sf() as session:
        original = session.scalar(select(EventInterpretation).where(EventInterpretation.event_id == row.id))
        original_payload = original.payload_json
    repeated = dict(item)
    repeated.pop("certainty")
    repeated_row, created = store.add_event(repeated)
    assert not created
    assert repeated_row.certainty == "done"
    with sf() as session:
        assert session.get(EventInterpretation, original.id).payload_json == original_payload


def test_public_acquisition_excerpt_retains_proposal_stage():
    # Frozen excerpt from 600825 announcement 2026-020, published 2026-09-19:
    # https://static.cninfo.com.cn/finalpage/2026-09-19/1225571565.PDF
    # This tests source-condition retention, not the cause of a market move.
    event = build_event(
        "新华传媒：关于暂不召开股东会审议本次交易相关事项的公告",
        source="公司公告", is_announcement=True, source_symbol="600825",
        summary="拟以发行股份的方式购买上海界面财联社科技股份有限公司100%股权。本次交易的审计、评估等相关工作尚未完成。",
        published_at=datetime(2026, 9, 19, 8, 0),
    )
    assert event["source_tier"] == 5
    assert event["certainty"] == "proposed"
    assert "尚未完成" in event["summary"]
