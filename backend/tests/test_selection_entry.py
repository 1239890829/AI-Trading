"""Original first joins and shadow reads; all database and runtime state are isolated."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.models.daily_pick import DailyPickSet
from app.models.opportunity_learning import OpportunityDecisionSnapshot as Snapshot
from app.models.paper import PaperAccount
from app.models.watchlist import Base
from app.picks.selection_entry import (CONTRACT, attach_daily_entries, attach_intraday_entries,
                                       daily_entries, intraday_entries)
from app.picks.shadow import ShadowRunner, activation_status

DAY = "2026-10-09"
NOW = datetime(2026, 10, 9, 10, 2, tzinfo=BJ_TZ)


@pytest.fixture
def sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'entry.sqlite'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def item(symbol="600127", price=10, pct=3):
    return {"symbol": symbol, "name": "隔离样本", "price": price, "change_pct": pct,
            "quote_audit": {"data_timestamp": "2026-10-09T10:00:00+08:00", "quality": "high", "source": "fixture"}}


def test_daily_regeneration_and_reentry_preserve_first_clock_and_original_price():
    meta = {"generated_at": NOW.isoformat(), "selection_version": "v1"}
    first = daily_entries([item()], meta, previous_meta=None, previous_items=[], trade_date=DAY, recorded_at=NOW)
    joined = first["600127"]
    assert joined["selected_at"] == NOW.isoformat()
    assert joined["reference_price"] == 10 and joined["reference_change_pct"] == 3
    assert joined["quote_as_of"] == "2026-10-09T10:00:00+08:00"
    # The symbol was removed, then reentered at limit up. Its first join is unchanged.
    later = daily_entries([item(price=11, pct=10)], {"generated_at": (NOW + timedelta(hours=1)).isoformat(), "selection_version": "v2"},
        previous_meta={"selection_entries": first}, previous_items=[], trade_date=DAY, recorded_at=NOW + timedelta(hours=1))
    assert later["600127"] == joined
    assert attach_daily_entries([item(price=11, pct=10)], {"selection_entries": later})[0]["selection_entry"] == joined


def test_legacy_generation_is_missing_and_valid_zero_pct_is_retained():
    entries = daily_entries([item(), item("600002", pct=0)], {"generated_at": NOW.isoformat(), "selection_version": "v1"},
        previous_meta={"generated_at": "2026-10-09T09:26:00+08:00"}, previous_items=[item(price=8)],
        trade_date=DAY, recorded_at=NOW)
    assert entries["600127"]["state"] == "missing"
    assert entries["600127"]["selected_at"] is None
    assert entries["600002"]["reference_change_pct"] == 0
    absent = attach_daily_entries([item()], {})[0]["selection_entry"]
    assert absent["reference_price"] is None and absent["selected_at"] is None


def test_missing_original_quote_keeps_partial_join_without_current_price_backfill():
    unknown = item(price=float("nan"), pct=float("inf"))
    unknown["quote_audit"] = {}
    entries = daily_entries([unknown], {"generated_at": NOW.isoformat()}, previous_meta=None, previous_items=[], trade_date=DAY, recorded_at=NOW)
    assert entries["600127"]["state"] == "partial"
    assert entries["600127"]["reference_price"] is None
    assert entries["600127"]["reference_change_pct"] is None


@pytest.mark.parametrize(("quality", "source_at", "reason"), [
    ("invalid", NOW.isoformat(), "质量"),
    ("stale", NOW.isoformat(), "质量"),
    ("low", NOW.isoformat(), "质量"),
    ("high", (NOW - timedelta(days=1)).isoformat(), "日期"),
    ("high", (NOW + timedelta(minutes=5, seconds=1)).isoformat(), "未来"),
    ("high", (NOW - timedelta(seconds=61)).isoformat(), "新鲜窗口"),
    ("high", "2026-10-09T10:02:00", "明确时区"),
])
def test_untrusted_daily_quote_stays_partial_without_erasing_observed_join(quality, source_at, reason):
    original = item(price=10.25, pct=0)
    original["quote_audit"].update(quality=quality, data_timestamp=source_at)
    joined = daily_entries([original], {"generated_at": NOW.isoformat(), "selection_version": "v1"},
        previous_meta=None, previous_items=[], trade_date=DAY, recorded_at=NOW)["600127"]
    assert joined["state"] == "partial" and reason in joined["reason"]
    assert joined["selected_at"] == NOW.isoformat()
    assert joined["reference_price"] == 10.25 and joined["reference_change_pct"] == 0
    assert original["quote_audit"]["data_timestamp"] == source_at
    later = daily_entries([item(price=11, pct=10)], {"generated_at": NOW.isoformat(), "selection_version": "v2"},
        previous_meta={"selection_entries": {"600127": joined}}, previous_items=[original],
        trade_date=DAY, recorded_at=NOW + timedelta(minutes=1))
    assert later["600127"] == joined


@pytest.mark.parametrize(("quality", "offset_seconds"), [("high", -60), ("medium", 0), ("high", 300)])
def test_daily_quote_quality_and_source_clock_boundaries_are_inclusive(quality, offset_seconds):
    original = item()
    original["quote_audit"].update(quality=quality, data_timestamp=(NOW + timedelta(seconds=offset_seconds)).isoformat())
    joined = daily_entries([original], {"generated_at": NOW.isoformat(), "selection_version": "v1"},
        previous_meta=None, previous_items=[], trade_date=DAY, recorded_at=NOW)["600127"]
    assert joined["state"] == "recorded" and joined["reason"] is None


@pytest.mark.parametrize(("quote_minutes", "state"), [(15 * 60, "recorded"), (14 * 60 + 59, "recorded"), (14 * 60 + 58, "partial")])
def test_daily_post_close_entry_uses_the_same_day_close_window(quote_minutes, state):
    recorded = NOW.replace(hour=16, minute=30)
    source_at = NOW.replace(hour=quote_minutes // 60, minute=quote_minutes % 60, second=0)
    original = item()
    original["quote_audit"]["data_timestamp"] = source_at.isoformat()
    joined = daily_entries([original], {"generated_at": recorded.isoformat(), "selection_version": "v1"},
        previous_meta=None, previous_items=[], trade_date=DAY, recorded_at=recorded)["600127"]
    assert joined["state"] == state
    assert joined["selected_at"] == recorded.isoformat() and joined["reference_price"] == 10
    assert joined["quote_as_of"] == source_at.isoformat()


def test_daily_persistence_merges_entries_without_changing_original_version(sf, monkeypatch):
    from app.services import picks_pipeline as pipeline
    from app.services.selection_notifications import daily_selection_version
    monkeypatch.setattr(pipeline, "get_session_factory", lambda: sf)
    monkeypatch.setattr(pipeline, "beijing_now", lambda: NOW)
    monkeypatch.setattr(pipeline, "retry_daily_selection", lambda **_: {"state": "completed"})
    monkeypatch.setattr("app.picks.watch_ledger.mark_merged_into_picks", lambda *_: None)
    original = [item()]
    version = daily_selection_version(DAY, NOW.isoformat(), original)
    pipeline._persist_picks(DAY, original, {"generated_at": NOW.isoformat(), "selection_version": version}, [], [])
    with sf() as db:
        row = db.scalar(select(DailyPickSet))
        first = json.loads(row.meta)["selection_entries"]["600127"]
        assert json.loads(row.items) == original
        assert json.loads(row.meta)["selection_version"] == version
    pipeline._persist_picks(DAY, [], {"generated_at": NOW.isoformat(), "selection_version": "empty"}, [], [])
    pipeline._persist_picks(DAY, [item(price=11, pct=10)], {"generated_at": NOW.isoformat(), "selection_version": "later"}, [], [])
    with sf() as db:
        assert json.loads(db.scalar(select(DailyPickSet)).meta)["selection_entries"]["600127"] == first


@pytest.mark.parametrize("raw_items", [json.dumps([item()]), "broken-history"])
def test_compact_day_and_corrupt_legacy_items_cannot_create_false_first_join(sf, monkeypatch, raw_items):
    from app.services import picks_pipeline as pipeline
    monkeypatch.setattr(pipeline, "get_session_factory", lambda: sf)
    monkeypatch.setattr(pipeline, "beijing_now", lambda: NOW)
    monkeypatch.setattr(pipeline, "retry_daily_selection", lambda **_: {"state": "completed"})
    monkeypatch.setattr("app.picks.watch_ledger.mark_merged_into_picks", lambda *_: None)
    with sf() as db:
        db.add(DailyPickSet(date="20261009", items=raw_items, meta="{}"))
        db.commit()
    pipeline._persist_picks(DAY, [item(price=11, pct=10)], {"generated_at": NOW.isoformat(), "selection_version": "new"}, [], [])
    with sf() as db:
        current = db.scalar(select(DailyPickSet).where(DailyPickSet.date == DAY))
        assert json.loads(current.meta)["selection_entries"]["600127"]["state"] == "missing"
        assert db.scalar(select(DailyPickSet).where(DailyPickSet.date == "20261009")).items == raw_items


def add_snapshot(sf, identity, *, symbol="600127", stage="rank", decision="ranked", rank=1,
                 capacity=2, selected=True, contract=CONTRACT, recorded=None, asof=None, price=10, pct=3, data_state="ready"):
    evidence = {"selection_entry_contract": contract, "effective_limit": capacity,
                "within_display_capacity": selected, "change_pct": pct}
    with sf() as db:
        db.add(Snapshot(snapshot_id=identity, run_id=identity, trade_date=DAY, scenario="intraday_opportunity",
            stage=stage, decision=decision, symbol=symbol, name="隔离样本", source_theme="题材", rank=rank,
            strategy_version="fixture", feature_version="fixture", data_state=data_state, entry_price=price,
            created_at=recorded or NOW.astimezone(timezone.utc).replace(tzinfo=None),
            as_of=asof or NOW.replace(tzinfo=None), evidence=json.dumps(evidence)))
        db.commit()


def test_intraday_first_join_uses_recorded_clock_not_earliest_rejected_source(sf):
    add_snapshot(sf, "rejected", decision="rejected", recorded=datetime(2026, 10, 9, 1), asof=datetime(2026, 10, 9, 9), price=8)
    add_snapshot(sf, "outside", rank=3, selected=False, recorded=datetime(2026, 10, 9, 1, 30), price=9)
    add_snapshot(sf, "first", recorded=datetime(2026, 10, 9, 2, 2), asof=datetime(2026, 10, 9, 10), price=10, pct=3)
    # An older market snapshot arrives later; it cannot rewrite system join time or price.
    add_snapshot(sf, "late", recorded=datetime(2026, 10, 9, 2, 3), asof=datetime(2026, 10, 9, 9, 59), price=8, pct=1)
    joined = intraday_entries(DAY, ["600127"], session_factory=sf)["600127"]
    assert joined["selected_at"] == "2026-10-09T10:02:00+08:00"
    assert joined["quote_as_of"] == "2026-10-09T10:00:00+08:00"
    assert joined["reference_price"] == 10 and joined["source_version"] == "first"


def test_sealed_reference_retains_original_join_and_unselected_reference_stays_missing(sf):
    add_snapshot(sf, "first")
    data = {"trade_date": DAY, "themes": [{"stocks": [{**item(price=11, pct=10), "reference_only": True}, item("600002")], "participants": []}]}
    attach_intraday_entries(data, session_factory=sf)
    assert data["themes"][0]["stocks"][0]["selection_entry"]["reference_price"] == 10
    assert data["themes"][0]["stocks"][0]["reference_only"] is True
    assert data["themes"][0]["stocks"][1]["selection_entry"]["state"] == "missing"


def test_legacy_capacity_is_not_guessed_and_cross_day_does_not_reuse(sf):
    add_snapshot(sf, "legacy", contract=None)
    assert intraday_entries(DAY, ["600127"], session_factory=sf) == {}
    add_snapshot(sf, "future")
    assert intraday_entries("2026-10-12", ["600127"], session_factory=sf) == {}


def test_next_day_archive_and_future_generation_do_not_backfill_original_joins(sf):
    add_snapshot(sf, "replay", recorded=datetime(2026, 10, 10, 2))
    assert intraday_entries(DAY, ["600127"], session_factory=sf) == {}
    entries = daily_entries([item()], {"generated_at": (NOW + timedelta(hours=2)).isoformat(), "selection_version": "v1"},
        previous_meta=None, previous_items=[], trade_date=DAY, recorded_at=NOW)
    assert entries["600127"]["selected_at"] is None


def test_partial_selection_still_records_actual_join_without_upgrading_data_quality(sf):
    add_snapshot(sf, "partial", data_state="degraded")
    joined = intraday_entries(DAY, ["600127"], session_factory=sf)["600127"]
    assert joined["state"] == "partial" and joined["reference_price"] == 10
    assert joined["selected_at"] == NOW.isoformat()


def test_real_archive_records_capacity_before_a_stock_moves_into_the_display(sf, monkeypatch):
    from app.picks.opportunity_learning import archive_intraday_pipeline
    monkeypatch.setattr("app.core.runtime_params.get", lambda _key, _default: 1)
    # as_of is the market snapshot clock; created_at records the actual archive
    # write. Set it before insertion, independently of SQLAlchemy's cached
    # default callable, so suite order cannot turn this fixture into a replay.
    recorded_clock = {"now": NOW.astimezone(timezone.utc).replace(tzinfo=None)}
    def stamp_archive_clock(_mapper, _connection, row):
        row.created_at = recorded_clock["now"]
    stocks = [{**item("600001", pct=8), "tradability": {"level": "可参与"}, "linkage": {"level": "高"}},
              {**item("600002", pct=7), "tradability": {"level": "可参与"}, "linkage": {"level": "高"}}]
    payload = {"trade_date": DAY, "hot_available": True,
        "linkage_stats": {"snapshot_state": "ready", "snapshot_as_of": NOW.isoformat()},
        "themes": [{"theme": "题材", "participants": stocks}]}
    event.listen(Snapshot, "before_insert", stamp_archive_clock)
    try:
        archive_intraday_pipeline(payload, trade_date=DAY, as_of=NOW, session_factory=sf)
        assert set(intraday_entries(DAY, ["600001", "600002"], session_factory=sf)) == {"600001"}
        assert intraday_entries(DAY, ["600001"], session_factory=sf)["600001"]["selected_at"] == NOW.isoformat()
        # Only a later actual capacity-qualified selection starts the other stock's entry.
        stocks[1]["change_pct"] = 9
        recorded_clock["now"] += timedelta(minutes=1)
        archive_intraday_pipeline(payload, trade_date=DAY, as_of=NOW + timedelta(minutes=1), session_factory=sf)
        assert set(intraday_entries(DAY, ["600001", "600002"], session_factory=sf)) == {"600001", "600002"}
        joined = intraday_entries(DAY, ["600001", "600002"], session_factory=sf)
        assert joined["600001"]["selected_at"] == NOW.isoformat()
        assert joined["600002"]["selected_at"] == (NOW + timedelta(minutes=1)).isoformat()
    finally:
        event.remove(Snapshot, "before_insert", stamp_archive_clock)


def test_highest_board_targets_come_from_full_pool_before_theme_display_truncation(monkeypatch):
    from datetime import date
    from app.schemas.market import LimitUpRecord
    from app.services import theme_service as service
    from app.picks.intraday_opportunity import assemble
    records = [LimitUpRecord(symbol="600001", name="甲", trade_date=date.fromisoformat(DAY), consecutive_boards=6, reason="题材甲", source="fixture"),
               LimitUpRecord(symbol="600002", name="乙", trade_date=date.fromisoformat(DAY), consecutive_boards=6, reason="题材乙", source="fixture"),
               LimitUpRecord(symbol="600003", name="丙", trade_date=date.fromisoformat(DAY), consecutive_boards=1, reason="题材丙", source="fixture")]
    async def empty_map(*_args, **_kwargs): return {}
    async def no_history(*_args, **_kwargs): return []
    async def no_breaks(*_args, **_kwargs): return None
    monkeypatch.setattr(service, "_em_enhancement_map", empty_map)
    monkeypatch.setattr(service, "_board_index", empty_map)
    monkeypatch.setattr(service, "_load_history", no_history)
    monkeypatch.setattr(service, "_market_break_rate", no_breaks)
    monkeypatch.setattr(service, "_auction_gaps", empty_map)
    class Provider:
        async def get_limit_up_pool(self, _day): return records
    board = asyncio.run(service.build_theme_board(Provider(), date.fromisoformat(DAY), snapshot_map={}))
    output = assemble(board, [], False, top_themes=1, stocks_per_theme=1)
    assert len(output["themes"]) == 1
    assert output["summary"]["market_max_board_stocks"] == [
        {"symbol": "600001", "name": "甲", "boards": 6}, {"symbol": "600002", "name": "乙", "boards": 6}]


def test_daily_shadow_get_never_creates_account_or_calls_ensure(sf, monkeypatch):
    from app.api.routes import picks
    from app.core.config import settings
    runner = ShadowRunner(SimpleNamespace(scope="shadow", ensure_account=lambda: pytest.fail("GET initialized funds")), sf)
    monkeypatch.setattr(picks, "get_session_factory", lambda: sf)
    monkeypatch.setattr(settings, "picks_shadow_enabled", True)
    monkeypatch.setattr(settings, "picks_shadow_start_minute", 9 * 60 + 31)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(paper_shadow=runner)))
    result = asyncio.run(picks.shadow_state(request))["data"]
    assert result["enabled"] is True and result["runtime"]["state"] == "unknown"
    assert result["activation"]["start_time"] == "09:31"
    assert result["account_created"] is False and result["cash"] is None
    with sf() as db:
        assert list(db.scalars(select(PaperAccount))) == []


def test_disabled_shadow_retains_existing_account_and_reports_unloaded(sf, monkeypatch):
    from app.api.routes import picks
    from app.core.config import settings
    with sf() as db:
        db.add(PaperAccount(scope="shadow", cash=1234, initial_cash=10000))
        db.commit()
    monkeypatch.setattr(picks, "get_session_factory", lambda: sf)
    monkeypatch.setattr(settings, "picks_shadow_enabled", False)
    result = asyncio.run(picks.shadow_state(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))))["data"]
    assert result["enabled"] is False and result["cash"] == 1234
    assert result["runtime"]["state"] == "not_loaded"
    assert result["activation"]["runner_loaded"] is False


def test_shadow_configuration_loaded_runner_and_scheduler_are_distinct(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "hunting_shadow_enabled", True)
    state = SimpleNamespace(hunting_shadow=None)
    assert activation_status(SimpleNamespace(state=state), "hunting")["runtime"]["state"] == "not_loaded"
    state.hunting_shadow = SimpleNamespace(health={"state": "ready", "as_of": NOW.isoformat()})
    state.schedulers = SimpleNamespace(snapshot=lambda: [{"name": "hunting-shadow", "state": "disabled"}])
    assert activation_status(SimpleNamespace(state=state), "hunting")["runtime"]["state"] == "paused"
    state.schedulers = SimpleNamespace(snapshot=lambda: [{"name": "hunting-shadow", "state": "running"}])
    result = activation_status(SimpleNamespace(state=state), "hunting")
    assert result["runtime"]["state"] == "ready"
    assert result["activation"]["quote_recheck_seconds"] == 60


def test_main_account_inspection_does_not_initialize_or_settle(sf, monkeypatch):
    from app.api.routes.paper import paper_account
    from app.paper.engine import PaperTradingEngine
    engine = PaperTradingEngine(sf, None, scope="main")
    monkeypatch.setattr(engine, "ensure_account", lambda: pytest.fail("inspection initialized main account"))
    async def forbidden_settle(): pytest.fail("inspection settled positions")
    monkeypatch.setattr(engine, "settle_t1", forbidden_settle)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(paper=engine, hub=SimpleNamespace(get_quotes=lambda: []))))
    data = asyncio.run(paper_account(request, read_only=True))["data"]
    assert data["account_created"] is False and data["total"] is None and data["cash"] is None
    assert data["market_value"] == 0
    with sf() as db:
        assert list(db.scalars(select(PaperAccount))) == []


def test_main_trading_consumer_keeps_initialization_and_existing_cash(sf):
    from app.api.routes.paper import paper_account
    from app.paper.engine import PaperTradingEngine
    engine = PaperTradingEngine(sf, None, scope="main")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(paper=engine, hub=SimpleNamespace(get_quotes=lambda: []))))
    assert asyncio.run(paper_account(request))["data"]["cash"] == 1_000_000
    with sf() as db:
        account = db.scalar(select(PaperAccount))
        account.cash = 1234
        db.commit()
    assert asyncio.run(paper_account(request, read_only=True))["data"]["cash"] == 1234
