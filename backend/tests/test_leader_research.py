"""Forward-only research evidence, isolated from live data and notifications."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.events.store import EventStore
from app.models.watchlist import Base
from app.models.leader_research import LeaderResearchObservation as Obs
from app.models.leader_research import LeaderResearchSession as Census
from app.models.event import EventInterpretation
from app.models.theme_catalog import Theme, ThemeMember
from app.research.leader_followthrough import capture, summary
from app.research.leader_collector import collect, load_evidence, review_contribution


@pytest.fixture
def sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'research.db'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def at(day=29, hour=10, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=BJ_TZ)


def rows(pct=6, price=10):
    return [{"symbol": "600127", "name": "金健米业", "price": price, "change_pct": pct},
            {"symbol": "600001", "name": "对照", "price": 10, "change_pct": 0}]


def run(sf, now=None, data=None, refs=None, days=None, source_time=None):
    now = now or at()
    return capture(data if data is not None else rows(), refs or {}, as_of=now,
                   source_as_of=source_time or now, trading_days=days or [now.date()],
                   source="injected-test", session_factory=sf)


def count(sf, model=Obs):
    with sf() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_source_replay_and_stable_confirmation(sf):
    assert run(sf)["inserted"] == 1
    assert run(sf, at(minute=1), source_time=at())["inserted"] == 0
    assert run(sf, at(minute=15))["inserted"] == 0
    assert run(sf, at(minute=30))["inserted"] == 1
    card = summary("2026-09-29", sf)["cards"][0]
    assert card["state"] == "priority" and card["continuity"]
    assert run(sf, at(minute=31))["inserted"] == 0
    assert count(sf) == 2
    assert run(sf, at(minute=32), rows(1))["inserted"] == 1
    assert summary("2026-09-29", sf)["cards"][0]["state"] == "weakened"
    assert run(sf, at(minute=33))["inserted"] == 1
    assert not summary("2026-09-29", sf)["cards"][0]["continuity"]


def test_late_news_and_one_price_cannot_be_fills(sf):
    ref = {"event_id": 1, "version_id": 1, "state": "active", "direction": 1,
           "published_at": at(hour=9).isoformat(), "available_at": at(hour=11).isoformat(),
           "route": "company_event"}
    run(sf, data=rows(10), refs={"600127": [ref]})
    card = summary("2026-09-29", sf)["cards"][0]
    assert card["no_entry"] and card["event_refs"] == [] and card["state"] == "observing"
    run(sf, at(hour=11), rows(7), {"600127": [ref]})
    card = summary("2026-09-29", sf)["cards"][0]
    assert card["first_seen"] == at().replace(tzinfo=None).isoformat()
    assert card["event_refs"] == [ref] and not card["no_entry"]
    assert card["outcomes"]["d0"]["reference_change_pct"] is None


def test_closing_census_never_backfills_and_historical_query_is_prefix_only(sf):
    days = [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)]
    run(sf, at(day=28), days=days)
    run(sf, at(day=28, hour=15), rows(6, 11), days=days)
    close = rows(-2, 9) + [{"symbol": "600825", "name": "新华传媒", "price": 20, "change_pct": 10}]
    run(sf, at(hour=15), close, days=days)
    result = summary("2026-09-29", sf)
    assert [r["symbol"] for r in result["review"]["missed"]] == ["600825"]
    assert result["review"]["cooled_symbols"] == ["600127"]
    assert count(sf) == 1 and count(sf, Census) == 2
    assert result["cards"][0]["outcomes"]["d1"]["reference_change_pct"] == -10
    assert summary("2026-09-30", sf)["review"]["state"] == "missing_close_census"
    past = summary("2026-09-28", sf)
    assert past["cards"][0]["outcomes"]["d1"]["state"] == "pending"
    assert past["review"]["missed"] == []
    dimension, actions = review_contribution("2026-09-29", sf)
    assert dimension.status == "ok" and actions[0].status == "pending"
    assert "600825" in actions[0].evidence


def test_slow_trend_audit_covers_no_daily_jump(sf):
    days = [date(2026, 9, 21) + timedelta(days=i) for i in range(5)] + [date(2026, 9, 28)]
    for i, day in enumerate(days):
        now = datetime.combine(day, datetime.min.time()).replace(hour=15, tzinfo=BJ_TZ)
        run(sf, now, rows(2, 10 + i * .3), days=days)
    result = summary("2026-09-28", sf)
    assert result["review"]["strong_count"] == 0
    assert result["review"]["missed"][0]["symbol"] == "600127"
    assert count(sf) == 0


def test_invalid_and_stale_inputs_cannot_create_evidence(sf):
    for now, source_time in [(at(), at(hour=9)), (at(hour=12), at(hour=12)), (at(), at(minute=1))]:
        assert run(sf, now, source_time=source_time)["inserted"] == 0
    assert run(sf, data=rows(float("nan")))["inserted"] == 0
    assert count(sf) == 0


def test_event_versions_mask_old_state_and_membership_is_stable(sf, monkeypatch):
    monkeypatch.setattr("app.events.store.beijing_now_naive", lambda: at(hour=9).replace(tzinfo=None))
    store = EventStore(sf)
    row, _ = store.add_event({"fingerprint": "research", "title": "公司事件",
        "source": "test", "published_at": at(hour=9).replace(tzinfo=None),
        "directions": [{"target_type": "theme", "target": "测试题材", "direction": 1, "matched_by": "rule"}],
        "received_at": at(hour=9).replace(tzinfo=None),
    })
    with sf.begin() as db:
        db.add(Theme(code="test", name="测试题材"))
        db.add(ThemeMember(theme_code="test", symbol="600127", synced_at=at(hour=9).astimezone(timezone.utc).replace(tzinfo=None)))
    a, b = load_evidence(at(), sf), load_evidence(at(minute=1), sf)
    assert a and a == b
    with sf.begin() as db:
        old = db.scalars(select(EventInterpretation)).first()
        db.add(EventInterpretation(event_id=row.id, observation_id=old.observation_id,
            effective_at=at(hour=11).replace(tzinfo=None), state="withdrawn", payload_json=old.payload_json))
    assert load_evidence(at(hour=11), sf) == {}
    assert load_evidence(at(), sf) == a


def test_collector_rejects_stale_and_lunch_before_sources(sf, monkeypatch):
    from app.research import leader_collector as module
    async def days(_):
        return [at().date()]
    monkeypatch.setattr(module.tc, "trading_days", days)
    state = NS(hub=NS(provider=None), snapshot_service=NS(
        versioned_snapshot=lambda: (rows(), at()),
        freshness=lambda: NS(state="stale", reason="expired")))
    assert asyncio.run(collect(state, now=at(), session_factory=sf))["state"] == "stale"
    assert asyncio.run(collect(NS(), now=at(hour=12), session_factory=sf))["state"] == "outside_session"
    assert count(sf) == 0


def test_override_blocks_removed_theme_and_llm_aux(sf, monkeypatch):
    from app.models.theme_catalog import ThemeOverride
    monkeypatch.setattr("app.events.store.beijing_now_naive", lambda: at(hour=9).replace(tzinfo=None))
    EventStore(sf).add_event({"fingerprint": "override", "title": "事件",
        "published_at": at(hour=9).replace(tzinfo=None), "directions": [
            {"target_type": "theme", "target": "题材", "direction": 1, "matched_by": "rule"},
            {"target_type": "symbol", "target": "600002", "direction": 1, "matched_by": "llm_aux"}]})
    utc = at(hour=9).astimezone(timezone.utc).replace(tzinfo=None)
    with sf.begin() as db:
        db.add(Theme(code="T", name="题材"))
        db.add(ThemeMember(theme_code="T", symbol="600127", synced_at=utc))
        db.add(ThemeOverride(theme_code="T", symbol="600127", action="exclude", created_at=utc))
    assert load_evidence(at(), sf) == {}


def test_slow_trend_can_enter_next_session_without_daily_limit(sf):
    days = [date(2026, 9, 21) + timedelta(days=i) for i in range(5)] + [date(2026, 9, 28)]
    run(sf, at(day=21, hour=15), rows(0, 10), days=days)
    run(sf, at(day=28), rows(1, 12), days=days)
    card = summary("2026-09-28", sf)["cards"][0]
    assert card["routes"] == ["trend"] and card["sustained"]
    assert not card["strong"] and not card["relative"]


def test_due_missing_price_is_not_zero_and_get_is_read_only(sf):
    days = [date(2026, 9, 28), date(2026, 9, 29)]
    run(sf, at(day=28), days=days)
    run(sf, at(day=28, hour=15), days=days)
    run(sf, at(hour=15), [rows()[1]], days=days)
    result = summary("2026-09-29", sf)
    outcome = result["cards"][0]["outcomes"]["d1"]
    assert outcome == {"target_date": "2026-09-29", "state": "missing", "reference_change_pct": None}
    assert count(sf) == 1 and count(sf, Census) == 2


def test_episode_expires_without_extending_old_reference_forever(sf):
    days = [date(2026, 8, 24) + timedelta(days=i) for i in range(37)
            if (date(2026, 8, 24) + timedelta(days=i)).weekday() < 5]
    for day in days:
        run(sf, datetime.combine(day, datetime.min.time()).replace(hour=10, tzinfo=BJ_TZ), days=days)
    result = summary(days[-1].isoformat(), sf)
    assert result["cards"][0]["first_seen"][:10] == days[21].isoformat()


def test_route_reads_only_and_validates_date(sf, monkeypatch):
    from app.api.routes.picks_intraday import leader_research
    from fastapi import HTTPException
    monkeypatch.setattr("app.research.leader_followthrough.get_session_factory", lambda: sf)
    request = NS(app=NS(state=NS()))
    assert leader_research(request, "2026-09-29")["data"]["state"] == "not_collected"
    with pytest.raises(HTTPException) as error:
        leader_research(request, "2026-02-30")
    assert error.value.status_code == 422
    assert count(sf) == 0 and count(sf, Census) == 0


def test_dead_collector_receipt_cannot_claim_running(sf, monkeypatch):
    from app.api.routes.picks_intraday import leader_research
    monkeypatch.setattr("app.research.leader_followthrough.get_session_factory", lambda: sf)
    monkeypatch.setattr("app.api.routes.picks_intraday.beijing_now", lambda: at(hour=11))
    run(sf)
    request = NS(app=NS(state=NS(leader_research_health={
        "state": "ready", "checked_at": at().isoformat(), "observed_symbols": ["600127"]})))
    result = leader_research(request, "2026-09-29")["data"]
    assert result["collector"]["state"] == "stale"
    assert result["cards"][0]["stale"]


def test_no_census_does_not_invent_future_calendar(sf):
    run(sf)
    outcomes = summary("2026-09-29", sf)["cards"][0]["outcomes"]
    assert outcomes["d0"]["target_date"] == "2026-09-29"
    assert outcomes["d0"]["state"] == "missing"
    assert outcomes["d1"]["state"] == "unknown"
