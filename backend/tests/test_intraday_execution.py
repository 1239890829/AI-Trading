"""Real persisted membership and assessment owner; all I/O and money are isolated."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.models.opportunity_learning import OpportunityDecisionRun as Run
from app.models.opportunity_learning import OpportunityDecisionSnapshot as Snapshot
from app.models.watchlist import Base
from app.picks import intraday_execution as ie
from app.picks.meta_confidence import DIMENSIONS
from app.schemas.market import Quote
from app.services import picks_pipeline as pl

REAL_SCORER = pl.score_selected_candidates

NOW = datetime(2026, 9, 10, 10, 30, tzinfo=BJ_TZ)
DAY = NOW.date().isoformat()
SYMBOL = "000858"


def _context(now=NOW):
    return {"state": "ready", "phase": "发酵", "trade_date": DAY,
        "judged_at": now.isoformat(), "source_as_of": now.isoformat(), "source_state": "ready",
        "gate": {"phase": "发酵", "stand_aside": False, "strip_buy_range": False}}


def _card(score=82):
    seed = {"version": 1, "score": score, "sub_scores": dict.fromkeys(DIMENSIONS, score),
        "dimension_evidence": {d: {"state": "success"} for d in DIMENSIONS},
        "vetoes": [], "halt_penalty": 0, "chip_signal": None,
        "buy_range": {"low": 10.2, "high": 10.8}, "observation_only": False, "follow_state": None}
    return {"symbol": SYMBOL, "name": "五粮液", "price": 10.5, "score": score,
        "sub_scores": seed["sub_scores"], "dimension_evidence": seed["dimension_evidence"],
        "vetoes": [], "execution_seed": seed, "buy_range": seed["buy_range"],
        "confidence": {"tier": "strong" if score >= 75 else "observe"},
        "halt_risk": {"available": True, "penalty": 0, "red_lines": []},
        "scoring_trace": {"algorithm_version": ie.SCORING_REVISION,
            "final_score": score, "assessed_at": NOW.isoformat()}}


def _run(sf, run_id, at, symbols=(SYMBOL,), state="ready"):
    created = at.astimezone(timezone.utc).replace(tzinfo=None)
    with sf() as db:
        db.add(Run(run_id=run_id, trade_date=DAY, as_of=at.replace(tzinfo=None),
            scenario="intraday_opportunity", strategy_version="source-test", feature_version="selection-entry-v1",
            data_state=state, snapshot_state=state, snapshot_as_of=at.isoformat(), rank_rows=len(symbols),
            evidence_digest=run_id, created_at=created))
        for rank, symbol in enumerate(symbols, 1):
            db.add(Snapshot(snapshot_id=f"{run_id}-{symbol}", run_id=run_id, trade_date=DAY,
                as_of=at.replace(tzinfo=None), scenario="intraday_opportunity", stage="rank", symbol=symbol,
                name="五粮液", source_theme="白酒", decision="ranked", rank=rank,
                strategy_version="source-test", feature_version="selection-entry-v1", data_state=state,
                entry_price=9.9, created_at=created, evidence=json.dumps({"selection_entry_contract": "selection-entry-v1",
                    "within_display_capacity": True, "effective_limit": 8, "gate_decision": "passed"})))
        db.commit()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    db_engine = create_engine(f"sqlite:///{tmp_path / 'qualification.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(db_engine)
    sf = sessionmaker(bind=db_engine, expire_on_commit=False)
    _run(sf, "first", NOW - timedelta(minutes=10))
    _run(sf, "current", NOW)
    clock = {"now": NOW}
    monkeypatch.setattr(ie, "beijing_now", lambda: clock["now"])
    monkeypatch.setattr(pl, "beijing_now", lambda: clock["now"])
    monkeypatch.setattr(ie, "window_issue", lambda *_: None)
    app = NS(state=NS(alert_repo=NS(_session_factory=sf), hub=NS(), event_store=NS(), snapshot_service=NS()))
    quote = Quote(symbol=SYMBOL, market="SZ", name="五粮液", price=10.5, change_pct=2.1, source="fixture",
        quality="high", data_timestamp=NOW, prev_close=10, limit_up_price=11, limit_down_price=9)
    calls = []
    async def quotes(_hub, symbols):
        calls.append(("quotes", tuple(symbols)))
        return {SYMBOL: quote}
    async def score(_deps, _hub, candidates, _quotes, *, assessed_at):
        calls.append(("score", tuple(c["symbol"] for c in candidates)))
        return {"items": [_card()] if candidates else [], "meta": {"execution_context": _context(),
            "requested_at": assessed_at.isoformat()}, "unscored": []}
    monkeypatch.setattr(pl, "_batch_quotes", quotes)
    monkeypatch.setattr(pl, "score_selected_candidates", score)
    yield sf, app, clock, calls, quote
    db_engine.dispose()


def test_complete_assessment_is_later_than_original_entry_and_reads_have_no_side_effects(rig):
    sf, app, clock, calls, _quote = rig
    assert asyncio.run(ie.refresh_intraday_execution(app))["state"] == "assessed"
    env = ie.load_intraday_execution_candidates(sf, NOW)[SYMBOL]
    assert env["state"] == "ready" and env["source_version"] == "first"
    assert env["reference_entry"]["price"] == 9.9 and env["item"]["price"] == 10.5
    assert env["assessment"]["assessed_at"] == NOW.isoformat()
    with sf() as db:
        original = db.get(Snapshot, 1)
        original_bytes = (original.evidence, original.entry_price, original.as_of)
        count = db.query(Snapshot).count()
    for _ in range(3):
        again = ie.load_intraday_execution_candidates(sf, NOW)
        assert again == {SYMBOL: env}
    assert calls == [("quotes", (SYMBOL,)), ("score", (SYMBOL,))]
    with sf() as db:
        assert db.query(Snapshot).count() == count
        row = db.get(Snapshot, 1)
        assert (row.evidence, row.entry_price, row.as_of) == original_bytes


def test_success_is_reused_but_source_empty_or_unknown_never_resurrects_old_ready(rig):
    sf, app, clock, calls, _ = rig
    asyncio.run(ie.refresh_intraday_execution(app))
    clock["now"] += timedelta(seconds=1)
    _run(sf, "empty", clock["now"], ())
    assert ie.load_intraday_execution_candidates(sf, clock["now"]) == {}
    assert asyncio.run(ie.refresh_intraday_execution(app))["state"] == "empty"
    clock["now"] += timedelta(seconds=1)
    _run(sf, "unknown", clock["now"], (SYMBOL,), "unknown")
    env = ie.load_intraday_execution_candidates(sf, clock["now"])[SYMBOL]
    assert env["state"] == "unknown" and env["item"] is None
    assert asyncio.run(ie.refresh_intraday_execution(app))["state"] == "unknown"
    assert sum(c[0] == "score" for c in calls) == 1


@pytest.mark.parametrize("change", ["source", "clock", "failure"])
def test_waited_scoring_failure_source_change_and_expiry_do_not_authorize(rig, monkeypatch, change):
    sf, app, clock, calls, _quote = rig
    async def score(*args, **kwargs):
        if change == "failure":
            raise OSError("isolated missing input")
        clock["now"] += timedelta(seconds=61 if change == "clock" else 1)
        if change == "source":
            _run(sf, "new-source", clock["now"])
        return {"items": [_card()], "meta": {"execution_context": _context()}, "unscored": []}
    monkeypatch.setattr(pl, "score_selected_candidates", score)
    asyncio.run(ie.refresh_intraday_execution(app))
    env = ie.load_intraday_execution_candidates(sf, clock["now"])[SYMBOL]
    assert env["state"] == "unknown"
    with sf() as db:
        facts = list(db.scalars(select(Snapshot).where(Snapshot.scenario == ie.SCENARIO).order_by(Snapshot.id)))
        assert [f.decision for f in facts] == ["evaluating", "unknown"]


def test_quote_missing_is_explicit_and_never_a_zero_score(rig, monkeypatch):
    sf, app, clock, calls, quote = rig
    quote.data_timestamp = NOW - timedelta(seconds=61)
    asyncio.run(ie.refresh_intraday_execution(app))
    env = ie.load_intraday_execution_candidates(sf, NOW)[SYMBOL]
    assert env["state"] == "unknown" and env["item"] is None
    assert "报价" in env["reason"]
    assert ("score", ()) in calls


def test_single_writer_coalesces_duplicate_refresh_and_does_not_mutate_cached_receipt(rig):
    sf, app, clock, calls, quote = rig
    async def twice():
        return await asyncio.gather(ie.refresh_intraday_execution(app), ie.refresh_intraday_execution(app))
    first, second = asyncio.run(twice())
    assert first == second
    first["state"] = "mutated-by-caller"
    assert asyncio.run(ie.refresh_intraday_execution(app))["state"] == "assessed"
    assert sum(c[0] == "score" for c in calls) == 1
    with sf() as db:
        assert db.query(Snapshot).filter_by(scenario=ie.SCENARIO).count() == 2


def test_selection_reference_expiry_does_not_masquerade_as_fresh_execution_quote(rig):
    sf, app, clock, calls, quote = rig
    asyncio.run(ie.refresh_intraday_execution(app))
    stale = NOW + timedelta(seconds=ie.settings.snapshot_save_interval_seconds
        + max(ie.settings.snapshot_poll_interval_seconds * 3, 60) + 1)
    env = ie.load_intraday_execution_candidates(sf, stale)[SYMBOL]
    assert env["state"] == "unknown"


def test_assessment_version_does_not_change_only_for_refresh_clocks(rig):
    sf, app, clock, calls, quote = rig
    asyncio.run(ie.refresh_intraday_execution(app))
    old = ie.load_intraday_execution_candidates(sf, NOW)[SYMBOL]
    later = NOW + timedelta(seconds=1)
    item = _card()
    item["scoring_trace"]["assessed_at"] = later.isoformat()
    ie._write(sf, {SYMBOL: old}, later, attempt="same-material-new-clock", state="ready", items={SYMBOL: item},
        meta={"execution_context": _context(later), "requested_at": later.isoformat()})
    current = ie.load_intraday_execution_candidates(sf, later)[SYMBOL]
    assert current["assessment"]["version"] == old["assessment"]["version"]
    assert current["assessment"]["snapshot_id"] != old["assessment"]["snapshot_id"]


def test_closed_window_never_fetches_or_scores(rig, monkeypatch):
    sf, app, clock, calls, quote = rig
    monkeypatch.setattr(ie, "window_issue", lambda *_: ("closed", "真实窗口关闭"))
    assert asyncio.run(ie.refresh_intraday_execution(app))["state"] == "skipped"
    assert calls == []


def test_unmeasured_halt_risk_is_unknown_instead_of_zero_risk(rig, monkeypatch):
    sf, app, clock, calls, _ = rig
    async def score(*args, **kwargs):
        card = _card()
        card["halt_risk"] = {"available": False, "penalty": 0}
        return {"items": [card], "meta": {"execution_context": _context()}, "unscored": []}
    monkeypatch.setattr(pl, "score_selected_candidates", score)
    asyncio.run(ie.refresh_intraday_execution(app))
    env = ie.load_intraday_execution_candidates(sf, NOW)[SYMBOL]
    assert env["state"] == "unknown" and "异动风险" in env["reason"]
    assert env["item"]["score"] == 82


@pytest.mark.parametrize("field", ["profile", "algorithm", "score"])
def test_saved_assessment_method_or_score_mismatch_cannot_authorize(rig, field):
    sf, app, clock, calls, _ = rig
    asyncio.run(ie.refresh_intraday_execution(app))
    with sf() as db:
        row = db.scalars(select(Snapshot).where(Snapshot.scenario == ie.SCENARIO)
            .order_by(Snapshot.id.desc())).first()
        payload = json.loads(row.evidence)
        if field == "profile":
            payload["assessment"]["profile"] = "unapproved-method"
        elif field == "algorithm":
            payload["item"]["scoring_trace"]["algorithm_version"] = "old-method"
        else:
            payload["item"]["scoring_trace"]["final_score"] = 99
        row.evidence = json.dumps(payload)
        db.commit()
    env = ie.load_intraday_execution_candidates(sf, NOW)[SYMBOL]
    assert env["state"] == "unknown" and "同次计算证据" in env["reason"]


def test_get_projection_reports_read_failure_and_never_backfills_history(rig, monkeypatch):
    sf, app, clock, calls, _ = rig
    def broken(*_):
        raise OSError("isolated read failure")
    monkeypatch.setattr(ie, "load_intraday_execution_candidates", broken)
    current = {"trade_date": DAY, "themes": [{"participants": [{"symbol": SYMBOL}]}]}
    ie.attach_intraday_execution(current, session_factory=sf, now=NOW)
    env = current["themes"][0]["participants"][0]["execution_qualification"]
    assert env["state"] == "unknown" and "OSError" in env["reason"]
    historical = {"trade_date": "2026-09-09", "themes": [{"participants": [{"symbol": SYMBOL}]}]}
    ie.attach_intraday_execution(historical, session_factory=sf, now=NOW)
    env = historical["themes"][0]["participants"][0]["execution_qualification"]
    assert env["state"] == "missing" and "不补算" in env["reason"]
    assert calls == []


def test_missing_historical_assessment_keeps_known_first_entry_identity_without_scoring(rig):
    sf, app, clock, calls, _ = rig
    day = "2026-09-09"
    entry = {"state": "recorded", "source": "intraday_rank_archive", "source_version": "original-only",
        "selected_at": day + "T10:00:00+08:00", "reference_price": 9.9,
        "quote_as_of": day + "T09:59:59+08:00"}
    data = {"trade_date": day, "themes": [{"participants": [{"symbol": SYMBOL, "selection_entry": entry}]}]}
    ie.attach_intraday_execution(data, session_factory=sf, now=NOW)
    stock = data["themes"][0]["participants"][0]
    env = stock["execution_qualification"]
    assert env["state"] == "missing" and "不补算" in env["reason"]
    assert env["selection_ref"] == f"intraday:{day}:{SYMBOL}" and env["source_version"] == "original-only"
    assert env["reference_entry"]["selected_at"] == entry["selected_at"]
    assert env["reference_entry"]["price"] == 9.9
    assert stock["selection_entry"] == entry and "item" not in env and "assessment" not in env
    assert calls == []


def _real_scoring(rig, monkeypatch):
    from tests.test_picks_pipeline import _Store, _Catalog, _Snapshot, _Hub, _Rps, _Chip
    from app.services import market_context
    sf, app, clock, calls, quote = rig
    # Restore the actual shared scoring entry, not a second copy of its result.
    monkeypatch.setattr(pl, "score_selected_candidates", REAL_SCORER)
    monkeypatch.setattr(pl, "beijing_now", lambda: NOW)
    monkeypatch.setattr(pl, "get_rps_service", lambda: _Rps())
    monkeypatch.setattr(pl, "get_chip_service", lambda: _Chip())
    async def days(_provider):
        return [NOW.date() - timedelta(days=1), NOW.date()]
    async def no_bars(_hub):
        from app.picks.halt_risk import BENCHMARK_INDEX
        bars = await app.state.hub.provider.get_kline(SYMBOL, "1d", "", "")
        return {symbol: bars for symbol in set(BENCHMARK_INDEX.values())}
    async def sentiment(*args, **kwargs):
        return {"phase": "发酵", "trade_date": DAY, "judged_at": NOW.isoformat(),
            "snapshot_freshness": {"state": "ready", "as_of": NOW.isoformat()},
            "promotion": {"promo_1to2": .8}}
    monkeypatch.setattr(pl.tc, "trading_days", days)
    monkeypatch.setattr(pl, "_prefetch_index_bars", no_bars)
    monkeypatch.setattr(market_context, "compute_market_sentiment", sentiment)
    app.state.hub = _Hub()
    app.state.event_store, app.state.theme_catalog, app.state.snapshot_service = _Store(), _Catalog(), _Snapshot()
    async def quotes(_hub, symbols):
        return {SYMBOL: quote}
    monkeypatch.setattr(pl, "_batch_quotes", quotes)
    return sf, app


def test_full_shared_scorer_to_actual_assessment_owner(rig, monkeypatch):
    sf, app = _real_scoring(rig, monkeypatch)
    assert asyncio.run(ie.refresh_intraday_execution(app))["state"] == "assessed"
    env = ie.load_intraday_execution_candidates(sf, NOW)[SYMBOL]
    assert env["state"] == "ready"
    assert set(env["item"]["sub_scores"]) == set(DIMENSIONS)
    assert env["item"]["scoring_trace"]["final_score"] == env["item"]["score"]
    assert env["item"]["execution_seed"]["score"] == env["item"]["score"]
    assert env["source_version"] == "first"


def test_opportunity_get_joins_assessment_from_the_same_app_database(rig, monkeypatch):
    from app.picks import intraday_opportunity_runtime as runtime
    sf, app, clock, calls, _ = rig
    asyncio.run(ie.refresh_intraday_execution(app))
    async def build(*args, **kwargs):
        return {"data": {"trade_date": DAY, "themes": [{"participants": [{"symbol": SYMBOL}],
            "stocks": []}]}, "meta": {}}
    monkeypatch.setattr(runtime, "_build_opportunities_uncached", build)
    monkeypatch.setattr(runtime, "attach_risk_to_themes", lambda *_: None)
    result = asyncio.run(runtime.build_opportunities(app, DAY, 5, 8,
        snapshot_bundle=({}, "ready", NOW.isoformat())))
    stock = result["data"]["themes"][0]["participants"][0]
    assert stock["selection_entry"]["source_version"] == "first"
    assert stock["execution_qualification"]["state"] == "ready"
    assert stock["execution_qualification"]["item"]["score"] == 82
    assert len(calls) == 2


def test_real_selected_scoring_to_notification_consumer_and_atomic_main_fill(rig, monkeypatch, tmp_path):
    from app.models.alert import AlertEvent
    from app.models.daily_pick import DailyPickSet
    from app.models.notification_outbox import BuyPointConsumption
    from app.models.paper import PaperOrder, PaperPosition
    from app.paper.engine import PaperTradingEngine
    from app.picks import buy_point as bp, position_engine, buy_point_consumers
    from app.picks import morning_brief, watcher
    from app.repositories.alert_repo import AlertRepository
    from app.services.selection_notifications import SELECTION_RULE, selection_dedup_key
    from app.services import market_context
    from tests.test_picks_pipeline import _Event

    sf, app = _real_scoring(rig, monkeypatch)
    quote = rig[4]
    quote.pe_ttm, quote.amount = 20, 3e8
    app.state.event_store.list_events = lambda **_: [_Event(91, "公司公告订单签约落地", target=SYMBOL)]
    async def financials(*_):
        return [{"revenue_yoy": 45., "profit_yoy": 35., "roe": 22., "gross_margin": 35.}]
    app.state.hub.provider.get_financials = financials
    for module in (bp, position_engine, buy_point_consumers):
        monkeypatch.setattr(module, "beijing_now", lambda: NOW)
    monkeypatch.setattr("app.paper.engine.beijing_now", lambda: NOW)
    monkeypatch.setattr("app.paper.engine._today", lambda: NOW.strftime("%Y%m%d"))
    monkeypatch.setattr("app.paper.selection_action.beijing_now", lambda: NOW)
    monkeypatch.setattr("app.picks.execution_qualification.beijing_now", lambda: NOW)
    monkeypatch.setattr(bp, "get_session_factory", lambda: sf)
    monkeypatch.setattr(position_engine, "_PLAN_DIR", tmp_path / "plans")
    monkeypatch.setattr(position_engine, "_publish_open_decision", lambda *_: None)
    monkeypatch.setattr(morning_brief, "brief_for_today", lambda: (NOW.date(), {}))
    monkeypatch.setattr(morning_brief, "append_alert", lambda *_: True)
    class LocalDelivery:
        def get(self, _channel):
            return None
        async def dispatch(self, _event, _rule, *, exclude=()):
            return ["in_app"]
    monkeypatch.setattr(watcher, "get_notifier_registry", lambda: LocalDelivery())
    async def sentiment(*_):
        return {"phase": "发酵", "trade_date": DAY, "judged_at": NOW.isoformat(),
            "snapshot_freshness": {"state": "ready", "as_of": NOW.isoformat()}, "promotion": {"promo_1to2": .8}}
    monkeypatch.setattr(market_context, "get_cached_sentiment", sentiment)
    async def actual_quote(_symbol):
        return quote
    async def days():
        return [NOW.date()]
    paper = PaperTradingEngine(sf, actual_quote, days,
        risk_engine=NS(hub=NS(get_quotes=lambda: []), check_order=lambda **_: {"allowed": True}))
    app.state.paper = paper
    repo = app.state.alert_repo = AlertRepository(sf)
    rule = repo.create_rule(name=SELECTION_RULE, condition_type="source_event", channels=["in_app"])
    origin, _ = repo.record_trigger_once(rule.id, SYMBOL, 0, 0, dedup_key=selection_dedup_key(DAY, SYMBOL),
        snapshot={"kind": "selection", "trade_date": DAY, "selection_source": "intraday",
            "source_version": "first", "source_id": "first", "name": "五粮液",
            "source_as_of": (NOW-timedelta(minutes=10)).isoformat(), "selection_evidence": {}})
    app.state.snapshot_service.snapshot = [{"symbol": SYMBOL, "price": quote.price,
        "change_pct": quote.change_pct, "prev_close": quote.prev_close, "source": quote.source}]
    app.state.snapshot_service.freshness = lambda: NS(state="ready", as_of=NOW, age_seconds=0, reason=None, source="fixture")
    monkeypatch.setattr(bp.settings, "picks_buy_point_channels", "in_app,log")
    hits = asyncio.run(bp.check_and_dispatch(app))
    env = ie.load_intraday_execution_candidates(sf, NOW)[SYMBOL]
    assert env["state"] == "ready" and env["item"]["score"] >= 60
    assert len(hits) == 1
    with sf() as db:
        assert db.query(DailyPickSet).count() == 0
        order = db.query(PaperOrder).one()
        assert order.scope == "main" and order.status == "filled" and order.filled_price == quote.price
        assert db.query(PaperPosition).one().frozen_today == order.quantity
        progress = db.query(BuyPointConsumption).filter_by(consumer="position").one()
        result = json.loads(progress.payload)["execution_result"]
        assert progress.state == "done" and result["order_id"] == order.id
        original = db.get(AlertEvent, origin.id)
        assert json.loads(original.snapshot)["source_version"] == "first"
        assert db.query(Snapshot).filter_by(scenario=ie.SCENARIO).count() == 2
    asyncio.run(bp.check_and_dispatch(app))
    with sf() as db:
        assert db.query(PaperOrder).count() == 1
