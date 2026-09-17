"""BUG-020: upstream failure must preserve the last accepted member snapshot."""
import asyncio
from copy import deepcopy
from datetime import datetime

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.theme_catalog import Theme, ThemeMember
from app.services.theme_catalog_service import ThemeCatalogService

CODE = "889901.TI"
READY = {"code": 0, "data": {"timestamp": 1789606800000, "item": []}}


@pytest.fixture
def service(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'members.sqlite'}")
    Theme.__table__.create(engine)
    ThemeMember.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    old = datetime(2026, 9, 1)
    with factory() as db:
        db.add(Theme(code=CODE, name="fixture", synced_at=old, created_at=old))
        db.add_all([ThemeMember(theme_code=CODE, symbol=s, name="old", synced_at=old)
                    for s in ["600001", "600002"]])
        db.commit()
    svc = ThemeCatalogService(factory, api_key="fixture")
    response = {"payload": deepcopy(READY), "status": 200}

    async def get(url, params):
        assert params == {"thscode": CODE}
        return httpx.Response(response["status"], json=response["payload"],
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(svc._client, "get", get)
    yield svc, factory, response
    asyncio.run(svc.aclose())
    engine.dispose()


def snapshot(factory):
    with factory() as db:
        theme = db.execute(select(Theme).where(Theme.code == CODE)).scalar_one()
        members = db.execute(select(ThemeMember).where(ThemeMember.theme_code == CODE)
                             .order_by(ThemeMember.symbol)).scalars()
        return theme.synced_at, [(m.symbol, m.name, m.synced_at) for m in members]


@pytest.mark.parametrize("payload", [
    {"code": 3002, "data": None},
    {"code": 2003, "data": {"timestamp": 1, "item": []}},
    {"data": {"timestamp": 1, "item": []}},
    {"code": False, "data": {"timestamp": 1, "item": []}},
    {"code": 0, "data": None},
    {"code": 0, "data": {}},
    {"code": 0, "data": {"timestamp": 1}},
    {"code": 0, "data": {"timestamp": 1, "item": None}},
    {"code": 0, "data": {"timestamp": 1, "item": {}}},
    {"code": 0, "data": {"timestamp": None, "item": []}},
    {"code": 0, "data": {"timestamp": 0, "item": []}},
    {"code": 0, "data": {"timestamp": 1, "item": [{"ticker": "bad"}]}},
    {"code": 0, "data": {"timestamp": 1, "item": [None]}},
    {"code": 0, "data": {"timestamp": 1, "item": [{"ticker": "６００００１"}]}},
    {"code": 0, "data": {"timestamp": 1, "item": [{"ticker": "600001"}, {"ticker": "bad"}]}},
    {"code": 0, "data": {"timestamp": 1, "item": [{"ticker": "600001"}, {"ticker": "600001"}]}},
])
def test_business_or_incomplete_response_cannot_change_members_or_sync_time(service, payload):
    svc, factory, response = service
    response["payload"] = payload
    before = snapshot(factory)
    with pytest.raises(RuntimeError):
        asyncio.run(svc.sync_members(CODE))
    assert snapshot(factory) == before


def test_http_failure_preserves_snapshot(service):
    svc, factory, response = service
    response["status"] = 503
    before = snapshot(factory)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(svc.sync_members(CODE))
    assert snapshot(factory) == before


def test_valid_empty_snapshot_can_remove_members(service):
    svc, factory, _ = service
    old_time, _ = snapshot(factory)
    assert asyncio.run(svc.sync_members(CODE)) == 0
    new_time, members = snapshot(factory)
    assert members == [] and new_time != old_time


def test_valid_snapshot_reconciles_members_after_failure(service):
    svc, factory, response = service
    response["payload"] = {"code": 3002, "data": None}
    with pytest.raises(RuntimeError):
        asyncio.run(svc.sync_members(CODE))
    response["payload"] = deepcopy(READY)
    response["payload"]["data"]["item"] = [
        {"ticker": "600001", "name": "updated"}, {"ticker": "600003", "name": "new"},
    ]
    assert asyncio.run(svc.sync_members(CODE)) == 2
    _, members = snapshot(factory)
    assert [(s, n) for s, n, _ in members] == [("600001", "updated"), ("600003", "new")]


def test_batch_result_reports_only_successfully_written_codes(service, monkeypatch, caplog):
    svc, _, _ = service
    monkeypatch.setattr(svc, "stale_codes", lambda limit: ["good", "bad", "empty"])

    async def sync(code):
        if code == "bad":
            raise RuntimeError("upstream unavailable")
        return 0 if code == "empty" else 2

    monkeypatch.setattr(svc, "sync_members", sync)
    assert asyncio.run(svc.sync_stale_members()) == ["good", "empty"]
    assert "theme members sync failed bad: upstream unavailable" in caplog.text
    empty_logs = [r.getMessage() for r in caplog.records if "官方有效成分为空" in r.getMessage()]
    assert empty_logs == ["theme members sync: 1/3 个题材官方有效成分为空: ['empty']"]
