"""Forward-only research evidence, isolated from live data and notifications."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
import sqlite3
from time import perf_counter
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


def test_latest_uses_indexed_latest_per_symbol_without_reading_all_history_payloads(sf, tmp_path):
    from app.research import leader_followthrough as module
    start_day = date(2026, 8, 1)
    seed = []
    for day_index in range(80):
        day = start_day + timedelta(days=day_index)
        for n in range(100):
            seed.append({"observation_id": f"{day_index:04}-{n:04}", "symbol": f"600{n:03}",
                "trade_date": day.isoformat(), "as_of": datetime.combine(day, datetime.min.time()).replace(hour=10),
                "first_seen": datetime(2026, 8, 1, 10), "version": module.VERSION,
                "signature": "fixture", "payload": json.dumps({"fixture": "x" * 2000})})
    with sf.begin() as db:
        db.execute(Obs.__table__.insert(), seed)
        db.add(Obs(observation_id="future", symbol="600000", trade_date="2026-10-20", as_of=datetime(2026, 10, 20, 10),
            first_seen=datetime(2026, 8, 1, 10), version=module.VERSION, signature="fixture", payload="{}"))
        db.add(Obs(observation_id="other-version", symbol="600001", trade_date="2026-10-19", as_of=datetime(2026, 10, 19, 11),
            first_seen=datetime(2026, 8, 1, 10), version="other", signature="fixture", payload="{}"))
        db.add(Obs(observation_id="backdated-high-id", symbol="600002", trade_date="2026-10-01", as_of=datetime(2026, 10, 1, 10),
            first_seen=datetime(2026, 8, 1, 10), version=module.VERSION, signature="fixture", payload="{}"))
    comparisons = []
    with sf() as db:
        raw = db.connection().connection.driver_connection
        def measured(operation):
            steps = 0
            def progress():
                nonlocal steps
                steps += 100
                return 0
            raw.set_progress_handler(progress, 100)
            began = perf_counter()
            try:
                result = operation()
                return {row.id for row in result}, steps, (perf_counter() - began) * 1000
            finally:
                raw.set_progress_handler(None, 0)
        for end in (datetime(2026, 10, 19, 10), datetime(2026, 10, 10, 10)):
            start = "2026-09-20"
            old_ids = select(func.max(Obs.id)).where(Obs.trade_date >= start, Obs.as_of <= end,
                Obs.version == module.VERSION).group_by(Obs.symbol)
            original = measured(lambda: list(db.scalars(select(Obs).where(Obs.id.in_(old_ids))).all()))
            revised = measured(lambda: module._latest(db, start, end))
            assert revised[0] == original[0]
            comparisons.append({"end": end.isoformat(), "rows": len(revised[0]),
                "original_vm_steps": original[1], "revised_vm_steps": revised[1],
                "original_ms": original[2], "revised_ms": revised[2]})
        assert comparisons[0]["revised_vm_steps"] < comparisons[0]["original_vm_steps"] * .65
        inspected_payloads = []
        def source_clock(payload, path):
            assert path == "$.source_as_of"
            inspected_payloads.append(payload)
            return json.loads(payload).get("source_as_of")
        raw.create_function("json_extract", 2, source_clock)
        assert module._newer_watermark(db, ["600000"],
            as_of=datetime(2026, 10, 19, 10), source_as_of=datetime(2026, 10, 19, 10)) is True
        assert len(inspected_payloads) == 1, "Only the affected symbol's winning row may decode its payload"
    (tmp_path / "query-comparison.json").write_text(json.dumps(comparisons, indent=2))


def test_capture_computes_transitions_without_holding_a_sqlite_writer(sf, monkeypatch, tmp_path):
    from app.research import leader_followthrough as module
    original = module.decide
    measured = []
    calls = 0
    path = sf.kw["bind"].url.database
    def competing_writer():
        began = perf_counter()
        try:
            with sqlite3.connect(path, timeout=.05) as db:
                db.execute("INSERT INTO leader_research_session VALUES (?, ?, ?, ?)",
                    ("2020-01-01", "2020-01-01 15:00:00.000000", module.VERSION, '{"universe":{}}'))
            return {"committed": True, "ms": (perf_counter() - began) * 1000}
        except sqlite3.OperationalError:
            return {"committed": False, "ms": (perf_counter() - began) * 1000}
    with ThreadPoolExecutor(max_workers=1) as workers:
        def decide(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                measured.append(workers.submit(competing_writer).result(timeout=2))
            return original(*args, **kwargs)
        monkeypatch.setattr(module, "decide", decide)
        assert run(sf)["inserted"] == 1
    assert measured[0]["committed"] is True
    (tmp_path / "writer-comparison.json").write_text(json.dumps(measured, indent=2))


def test_capture_rechecks_generation_before_applying_a_prepared_older_frame(sf, monkeypatch):
    from app.research import leader_followthrough as module
    original, nested = module.decide, []
    def decide(*args, **kwargs):
        if not nested:
            nested.append(run(sf, at(minute=1), data=rows(7, 11)))
        return original(*args, **kwargs)
    # Set the guard before entering the inner capture; this hook represents a
    # separate caller that commits while the original frame is being computed.
    entered = False
    def interleaved(*args, **kwargs):
        nonlocal entered
        if not entered:
            entered = True
            return decide(*args, **kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "decide", interleaved)
    result = run(sf)
    assert result["state"] == "concurrent_update" and result["inserted"] == 0
    assert count(sf) == 1
    card = summary("2026-09-29", sf)["cards"][0]
    assert card["reference_price"] == 11 and card["as_of"] == at(minute=1).replace(tzinfo=None).isoformat()


def test_closing_census_does_not_read_observation_history(sf, monkeypatch):
    from app.research import leader_followthrough as module
    monkeypatch.setattr(module, "_latest", lambda *a: pytest.fail("Closing census must not read prior observations"))
    assert run(sf, at(hour=15))["state"] == "closing_census"
    assert count(sf) == 0 and count(sf, Census) == 1


def test_late_frame_cannot_append_after_newer_frame_committed_before_capture_started(sf):
    assert run(sf, at(minute=1), rows(7, 11))["inserted"] == 1
    result = run(sf, at(), rows(6, 10))
    assert result["state"] == "out_of_order" and result["inserted"] == 0
    assert count(sf) == 1
    card = summary("2026-09-29", sf)["cards"][0]
    assert card["reference_price"] == 11 and card["as_of"] == at(minute=1).replace(tzinfo=None).isoformat()
    assert run(sf, at(minute=1), rows(7, 11))["inserted"] == 0
    assert count(sf) == 1


def test_future_watermarks_for_unrelated_symbols_or_versions_do_not_block_current_capture(sf):
    from app.research import leader_followthrough as module
    future = at(minute=2).replace(tzinfo=None)
    with sf.begin() as db:
        for symbol, version in (("600825", module.VERSION), ("600127", "other-version")):
            db.add(Obs(observation_id=symbol + version, symbol=symbol, trade_date="2026-09-29", as_of=future,
                first_seen=future, version=version, signature="future", payload=json.dumps({"source_as_of": future.isoformat()})))
    result = run(sf)
    assert result["state"] == "ready" and result["inserted"] == 1
    with sf() as db:
        row = db.scalar(select(Obs).where(Obs.symbol == "600127", Obs.version == module.VERSION))
        card = json.loads(row.payload)
    assert card["reference_price"] == 10 and card["as_of"] == at().replace(tzinfo=None).isoformat()


def test_empty_calendar_still_exits_before_watermark_or_any_write(sf):
    result = capture(rows(), {}, as_of=at(), source_as_of=at(), trading_days=[],
        source="中文来源", session_factory=sf)
    assert result == {"state": "outside_session", "inserted": 0}
    assert count(sf) == 0 and count(sf, Census) == 0
