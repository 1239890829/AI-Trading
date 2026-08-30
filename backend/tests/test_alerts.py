from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.market.alert_engine import AlertEngine
from app.models.watchlist import Base
from app.repositories.alert_repo import AlertRepository
from app.repositories.watchlist_repo import WatchlistRepository


def _repo(tmp_path):
    path = tmp_path / "alert.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return AlertRepository(factory), WatchlistRepository(factory), factory


def test_alert_channels():
    with TestClient(app) as client:
        resp = client.get("/api/alerts/channels")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "in_app" in data["available"]
        assert "log" in data["available"]


def test_alert_rule_crud(tmp_path):
    repo, wl_repo, _ = _repo(tmp_path)
    wl_repo.add("600519", "茅台")

    rule = repo.create_rule(
        name="test",
        condition_type="price_above",
        threshold=100,
        symbols=["600519"],
        scope="symbols",
        cooldown_seconds=0,
        channels=["in_app"],
        enabled=True,
    )
    assert rule.id > 0
    assert json.loads(rule.symbols or "") == ["600519"]

    fetched = repo.get_rule(rule.id)
    assert fetched is not None
    assert fetched.name == "test"

    updated = repo.update_rule(rule.id, threshold=200)
    assert updated and updated.threshold == 200

    events = repo.list_events()
    assert events == []

    repo.delete_rule(rule.id)
    assert repo.get_rule(rule.id) is None


def test_alert_event_ack_and_snapshot(tmp_path):
    repo, _, _ = _repo(tmp_path)
    rule = repo.create_rule(
        name="pct",
        condition_type="change_pct_above",
        threshold=5,
        scope="all",
        cooldown_seconds=0,
        channels=["log"],
        enabled=True,
    )
    event = repo.record_trigger(
        rule_id=rule.id,
        symbol="000001",
        trigger_value=6.5,
        threshold=5.0,
        snapshot={"symbol": "000001", "price": 12.0, "change_pct": 6.5},
        delivered_channels=["log"],
    )
    assert event.acknowledged == 0
    repo.acknowledge_event(event.id)
    events = repo.list_events(acknowledged=True)
    assert len(events) == 1
    assert events[0].snapshot is not None


def test_alert_engine_price_above(tmp_path):
    repo, wl_repo, _ = _repo(tmp_path)
    wl_repo.add("600519", "茅台")
    repo.create_rule(
        name="price",
        condition_type="price_above",
        threshold=1000,
        scope="watchlist",
        cooldown_seconds=0,
        channels=["in_app"],
        enabled=True,
    )
    engine = AlertEngine(repo, wl_repo, interval=60)
    engine.update_quotes({
        "600519": {"symbol": "600519", "price": 1297.4, "change_pct": 0.39},
    })

    asyncio.run(engine._tick())

    events = repo.list_events()
    assert len(events) == 1
    assert events[0].symbol == "600519"
    assert events[0].trigger_value == 1297.4


def test_alert_engine_cooldown_blocks_repeat(tmp_path):
    repo, wl_repo, _ = _repo(tmp_path)
    wl_repo.add("600519", "茅台")
    repo.create_rule(
        name="pct",
        condition_type="change_pct_above",
        threshold=0,
        scope="watchlist",
        cooldown_seconds=3600,
        channels=["in_app"],
        enabled=True,
    )
    engine = AlertEngine(repo, wl_repo, interval=60)
    engine.update_quotes({
        "600519": {"symbol": "600519", "price": 100, "change_pct": 1.0},
    })

    asyncio.run(engine._tick())
    asyncio.run(engine._tick())

    events = repo.list_events()
    assert len(events) == 1  # 第二次被冷却挡住


def test_alert_api_crud():
    with TestClient(app) as client:
        # 清理可能存在的测试规则
        existing = client.get("/api/alerts/rules").json()["data"]
        for r in existing:
            if r["name"].startswith("api-test"):
                client.delete(f"/api/alerts/rules/{r['id']}")

        resp = client.post("/api/alerts/rules", json={
            "name": "api-test-price",
            "condition_type": "price_above",
            "threshold": 1000,
            "scope": "symbols",
            "symbols": ["600519"],
            "cooldown_seconds": 60,
            "channels": ["in_app", "log"],
            "enabled": True,
        })
        assert resp.status_code == 201
        rule_id = resp.json()["data"]["id"]

        resp = client.get("/api/alerts/rules")
        assert any(r["id"] == rule_id for r in resp.json()["data"])

        resp = client.put(f"/api/alerts/rules/{rule_id}", json={"threshold": 2000})
        assert resp.status_code == 200
        assert resp.json()["data"]["threshold"] == 2000

        resp = client.delete(f"/api/alerts/rules/{rule_id}")
        assert resp.status_code == 200

        resp = client.get(f"/api/alerts/rules/{rule_id}")
        assert resp.status_code == 404
