"""Runtime isolation, holiday gating and search association counterexamples."""
import asyncio
import json
from datetime import date, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.models.watchlist import Base
from app.models.leader_research import LeaderResearchObservation as Observation
from app.events.store import EventStore
from app.models.event import EventCard
from scripts import rsh031_isolated_runtime as runtime


def test_output_cannot_target_production_or_symlink(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "artifacts").mkdir(parents=True)
    monkeypatch.setattr(runtime, "REPO", repo)
    for target in [repo / "data", repo / "artifacts", repo.parent]:
        with pytest.raises(ValueError):
            runtime.output_dir(target)
    (repo / "artifacts" / "link").symlink_to(repo.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        runtime.output_dir(repo / "artifacts" / "link" / "unsafe")
    assert runtime.output_dir(repo / "artifacts" / "run").is_dir()
    (repo / "artifacts" / "run" / "research.db").symlink_to(repo / "production.db")
    with pytest.raises(ValueError):
        runtime.output_dir(repo / "artifacts" / "run")


def test_exchange_calendar_is_not_a_weekday_guess(tmp_path):
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({"source_url": "https://www.sse.com.cn/disclosure/dealinstruc/closed/",
                                "days": ["2026-10-08", "2026-10-09"]}))
    days = runtime.calendar(path)
    assert not runtime.eligible(datetime(2026, 10, 2, 10, tzinfo=BJ_TZ), days)
    assert runtime.eligible(datetime(2026, 10, 8, 10, tzinfo=BJ_TZ), days)
    assert not runtime.eligible(datetime(2026, 10, 8, 12, tzinfo=BJ_TZ), days)
    assert not runtime.eligible(datetime(2026, 10, 8, 16, tzinfo=BJ_TZ), days)
    assert not runtime.eligible(datetime(2026, 10, 8, 15, 2, tzinfo=BJ_TZ), days)
    assert runtime.eligible(datetime(2026, 10, 8, 15, 5, tzinfo=BJ_TZ), days)
    with pytest.raises(ValueError):
        runtime.eligible(datetime(2026, 10, 8, 10), days)
    path.write_text('{"source_url":"https://example.com/","days":["2026-10-08"]}')
    with pytest.raises(ValueError):
        runtime.calendar(path)


def test_closed_tick_never_fetches_or_writes(tmp_path):
    service = NS(refresh=AsyncMock())
    result = asyncio.run(runtime.tick(service, None, None, [date(2026, 10, 8)], tmp_path,
                                     now=datetime(2026, 10, 2, 10, tzinfo=BJ_TZ)))
    assert result["state"] == "closed_or_outside_session"
    assert not result["valid_observations"]
    service.refresh.assert_not_awaited()
    assert list(tmp_path.iterdir()) == []


def test_closed_heartbeat_wakes_at_open_instead_of_ten_minutes_late():
    days = [date(2026, 10, 8)]
    assert runtime.idle_delay(datetime(2026, 10, 8, 9, 29, 50, tzinfo=BJ_TZ), days) == 10
    assert runtime.idle_delay(datetime(2026, 10, 8, 12, 59, 45, tzinfo=BJ_TZ), days) == 15
    assert runtime.idle_delay(datetime(2026, 10, 2, 10, tzinfo=BJ_TZ), days) == 600


def test_live_tick_records_real_consumer_without_news_required(tmp_path, monkeypatch):
    now = datetime(2026, 10, 8, 10, tzinfo=BJ_TZ)
    monkeypatch.setattr(runtime, "beijing_now", lambda: now)
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    rows = [{"symbol": "600127", "name": "金健米业", "price": 10, "change_pct": 6},
            {"symbol": "600001", "name": "同日对照", "price": 10, "change_pct": 0}]
    service = NS(refresh=AsyncMock(), freshness=lambda **kw: NS(state="ready"),
                 versioned_snapshot=lambda: (rows, now), last_snapshot_source="injected-test")
    result = asyncio.run(runtime.tick(service, sf, None, [now.date()], tmp_path,
                                     now=now, news_due=False))
    assert result["state"] == "ready" and result["inserted"] == 1
    assert result["valid_observations"]
    again = asyncio.run(runtime.tick(service, sf, None, [now.date()], tmp_path,
                                    now=now, news_due=False))
    assert again["inserted"] == 0
    with sf() as db:
        assert db.scalar(select(func.count()).select_from(Observation)) == 1
    report = json.loads((tmp_path / "review-2026-10-08.json").read_text())
    assert report["cards"][0]["symbol"] == "600127"
    assert report["cards"][0]["outcomes"]["d1"]["reference_change_pct"] is None
    engine.dispose()


def test_degraded_source_never_becomes_observation(tmp_path):
    service = NS(refresh=AsyncMock(), freshness=lambda **kw: NS(state="degraded"),
                 versioned_snapshot=lambda: ([{}], datetime(2026, 10, 8, 10, tzinfo=BJ_TZ)))
    result = asyncio.run(runtime.tick(service, None, None, [date(2026, 10, 8)], tmp_path,
                                     now=datetime(2026, 10, 8, 10, tzinfo=BJ_TZ)))
    assert not result["valid_observations"] and result["inserted"] == 0


def test_search_hit_not_issuer_evidence_and_news_failure_is_visible(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'events.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    provider = NS(get_news=AsyncMock(side_effect=[
        [{"title": "另一家公司公布重大资产收购事项", "date": "2026-10-08 09:00:00"},
         {"title": "金健米业公布重大资产收购事项", "date": "unknown"}],
        RuntimeError("source unavailable")]))
    result = asyncio.run(runtime.company_news([
        {"symbol": "600127", "name": "金健米业", "change_pct": 6},
        {"symbol": "600825", "name": "新华传媒", "change_pct": 5}], provider, EventStore(sf)))
    assert result["created"] == 1 and len(result["errors"]) == 1
    assert result["undated_news"][0]["reason"] == "publication_time_unknown"
    assert result["public_theme_linkage"] == "unknown"
    with sf() as db:
        row = db.scalar(select(EventCard))
        assert row.source_symbol is None
    engine.dispose()


def test_stop_marker_finishes_without_network(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    output = repo / "artifacts" / "run"
    output.mkdir(parents=True)
    monkeypatch.setattr(runtime, "REPO", repo)
    monkeypatch.setattr(runtime, "beijing_now", lambda: datetime(2026, 10, 2, 10, tzinfo=BJ_TZ))
    (output / "STOP").touch()
    cal = output / "calendar.json"
    cal.write_text(json.dumps({"source_url": "https://www.sse.com.cn/",
                              "days": ["2026-10-08", "2026-10-09"]}))
    asyncio.run(runtime.run(output, cal, datetime(2026, 10, 9, 16, tzinfo=BJ_TZ), False))
    assert json.loads((output / "health.json").read_text())["reason"] == "operator_stop"


def test_health_atomic_write_rejects_symlink(tmp_path):
    target = tmp_path / "protected.json"
    target.write_text("original")
    link = tmp_path / "health.json"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        runtime.write_json(link, {"state": "ready"})
    assert target.read_text() == "original"


def test_same_joint_headline_is_not_a_source_revision_for_each_search(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'events.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    item = {"title": "金健米业与新华传媒签订合作协议", "date": "2026-10-08 09:00:00",
            "source_item_id": "joint-event"}
    provider = NS(get_news=AsyncMock(return_value=[item]))
    result = asyncio.run(runtime.company_news([
        {"symbol": "600127", "name": "金健米业", "change_pct": 6},
        {"symbol": "600825", "name": "新华传媒", "change_pct": 5}], provider, EventStore(sf)))
    assert result["created"] == 1 and not result["errors"]
    with sf() as db:
        row = db.scalar(select(EventCard))
        assert row.revision_pending_at is None
        assert row.source_symbol is None
    engine.dispose()
