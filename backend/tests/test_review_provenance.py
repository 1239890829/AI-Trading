"""A report date cannot relabel current, missing-clock or future market facts."""
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS
import asyncio
import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.models.watchlist import Base
from app.models.paper import PaperAccount, PaperOrder, PaperPosition
from app.review.collector import collect_market, collect_trading
from app.review.provenance import quote_evidence
from app.review.schemas import MarketSnapshot

DAY = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 15, 30, tzinfo=BJ_TZ)


def quote(**extra):
    value = dict(symbol="sh000001", name="上证", price=3000.0, change_pct=0.0,
                 amount=0.0, source="fixture", quality="high",
                 data_timestamp=NOW.replace(hour=15, minute=0), received_at=NOW)
    value.update(extra)
    return NS(**value)


def setup_market(monkeypatch, tmp_path, *, q=None, rows=None):
    import app.core.bjtime as clock
    import app.market.auction_premium as auction
    import app.market.board_flow as board
    import app.services.market_context as context
    import app.services.theme_service as themes
    monkeypatch.setattr(clock, "beijing_now", lambda: NOW)
    monkeypatch.setattr(board, "_DAILY_STORE", tmp_path / "boards.json")
    monkeypatch.setattr(board, "_mem_memo", {})
    calls = []

    async def sentiment(hub, snapshot):
        calls.append("sentiment")
        assert snapshot.breadth["flat"] == 1
        return {"trade_date": DAY.isoformat(), "phase": "分歧", "temperature": 0}

    async def theme(provider, day):
        calls.append("themes")
        return {"trade_date": day.isoformat(), "themes": [], "broken_ladder": []}

    async def premium(hub, day):
        calls.append("auction")
        return {"trade_date": day.isoformat(), "summary": None, "caveats": []}

    monkeypatch.setattr(context, "compute_market_sentiment", sentiment)
    monkeypatch.setattr(themes, "build_theme_board", theme)
    monkeypatch.setattr(auction, "collect_premium", premium)
    hub = NS(get_indices=lambda: [q or quote()], provider=NS())
    values = rows if rows is not None else [{**vars(quote()), "symbol": "600001", "name": "测试"}]
    svc = NS(breadth_payload=lambda: {"breadth": {"up": 999}, "snapshot_source": "fixture"},
             versioned_snapshot=lambda: (values, NOW), poll_interval=60)
    return hub, svc, calls


def test_same_source_day_keeps_real_zero_and_source_clock(monkeypatch, tmp_path):
    hub, svc, calls = setup_market(monkeypatch, tmp_path)
    got = asyncio.run(collect_market(hub, svc, DAY))
    assert got.indices[0].change_pct == 0 and got.indices[0].amount == 0
    assert got.indices[0].data_timestamp == "2026-10-23T15:00:00+08:00"
    assert got.provenance["indices"].state == "verified"
    assert got.breadth["flat"] == 1 and got.breadth["up"] == 0
    assert got.sentiment["temperature"] == 0
    assert "sentiment" in calls and "auction" in calls


def test_historical_day_rejects_current_inputs_without_today_calculation(monkeypatch, tmp_path):
    hub, svc, calls = setup_market(monkeypatch, tmp_path)
    got = asyncio.run(collect_market(hub, svc, date(2026, 10, 22)))
    assert got.indices == [] and got.breadth == {} and got.sentiment == {}
    assert got.auction_premium is None
    assert "sentiment" not in calls and "auction" not in calls
    assert got.reported_values["breadth"] == {"up": 999}
    assert got.reported_values["indices"][0]["provenance"]["state"] == "mismatch"


def test_receipt_date_and_time_only_tick_do_not_prove_source_day(monkeypatch, tmp_path):
    row = {"symbol": "600001", "source": "sina_market", "ticktime": "15:00:00",
           "received_at": NOW.isoformat(), "price": 10.0, "change_pct": 1.0}
    hub, svc, calls = setup_market(monkeypatch, tmp_path, q=quote(data_timestamp=None), rows=[row])
    got = asyncio.run(collect_market(hub, svc, DAY))
    assert got.indices == [] and got.breadth == {} and got.sentiment == {}
    assert got.provenance["breadth"].state == "unverified" and "sentiment" not in calls


def test_partial_snapshot_is_not_full_market_or_full_sentiment(monkeypatch, tmp_path):
    valid = {**vars(quote()), "symbol": "600001", "name": "测试"}
    hub, svc, calls = setup_market(monkeypatch, tmp_path, rows=[valid, {**valid, "symbol": "600002", "data_timestamp": None}])
    got = asyncio.run(collect_market(hub, svc, DAY))
    assert got.provenance["breadth"].state == "partial"
    assert got.breadth["total"] == 1 and "1/2" in got.provenance["breadth"].reason
    assert got.sentiment == {} and "sentiment" not in calls


def test_invalid_or_future_quote_not_used(monkeypatch, tmp_path):
    for change in ({"price": None}, {"price": float("nan")}, {"quality": "stale"},
                   {"data_timestamp": NOW.replace(hour=16)}, {"data_timestamp": datetime(2026, 10, 23, 15)}):
        hub, svc, _ = setup_market(monkeypatch, tmp_path, q=quote(**change))
        assert asyncio.run(collect_market(hub, svc, DAY)).indices == []


def test_utc_source_clock_uses_beijing_day():
    proof = quote_evidence(quote(data_timestamp=datetime(2026, 10, 22, 16, 1, tzinfo=timezone.utc)), DAY, NOW)
    assert proof.state == "verified" and proof.source_trade_date == DAY.isoformat()


def test_legacy_values_are_not_promoted_or_rewritten():
    old = {"trade_date": "20261022", "indices": [{"symbol": "sh000001", "name": "上证", "close": 3000, "change_pct": 0}], "breadth": {"up": 123}}
    snapshot = MarketSnapshot.model_validate(old)
    assert snapshot.contract_version is None and snapshot.provenance == {}
    assert snapshot.indices[0].binding_state == "unverified"
    assert snapshot.breadth["up"] == 123
    assert set(old["indices"][0]) == {"symbol", "name", "close", "change_pct"}


def sf_at(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_historical_trading_does_not_revalue_current_positions(monkeypatch, tmp_path):
    import app.core.bjtime as clock
    monkeypatch.setattr(clock, "beijing_now", lambda: NOW)
    sf = sf_at(tmp_path)
    with sf() as db:
        db.add(PaperAccount(scope="main", cash=12345, initial_cash=20000))
        db.add(PaperPosition(scope="main", symbol="600001", quantity=100, cost_price=10))
        db.add(PaperOrder(scope="main", symbol="600001", side="buy", price=10, quantity=100, status="filled", filled_price=10, created_at=datetime(2026, 10, 22, 2)))
        db.commit()
    result = collect_trading(sf, date(2026, 10, 22), {"600001": 50})
    assert result.account == {} and result.positions == [] and len(result.orders) == 1
    assert result.provenance["positions"].state == "unavailable"
    with sf() as db:
        assert len(list(db.scalars(select(PaperOrder)))) == 1


def test_outlook_reads_original_conditions_and_rejects_wrong_day(monkeypatch):
    from app.review.reader_context import collect_outlook
    import app.picks.morning_brief as brief
    import app.review.reader_context as reader
    monkeypatch.setattr(reader, "beijing_now", lambda: NOW)
    monkeypatch.setattr(brief, "load_brief", lambda day: {"brief_date": day, "generated_at": "2026-10-23T08:40:00+08:00", "directions": [{"direction": "测试", "logic": "原逻辑", "trigger_conditions": ["原触发"], "falsify_conditions": ["原证伪"], "review": {"outcome": "unknown"}}]})
    result = collect_outlook(DAY)
    assert result["state"] == "saved" and result["directions"][0]["trigger_conditions"] == ["原触发"]
    monkeypatch.setattr(brief, "load_brief", lambda _day: {"brief_date": "20261022", "generated_at": NOW.isoformat()})
    assert collect_outlook(DAY)["state"] == "mismatch"


def test_journey_only_joins_explicit_origin_and_source_version(monkeypatch, tmp_path):
    from app.models.alert import AlertEvent, AlertRule
    from app.review.reader_context import collect_selection_journey
    import app.review.reader_context as reader
    monkeypatch.setattr(reader, "beijing_now", lambda: NOW)
    sf = sf_at(tmp_path)
    with sf() as db:
        rule = AlertRule(name="fixture", condition_type="source_event", scope="all", threshold=0)
        db.add(rule); db.flush()
        origin = AlertEvent(rule_id=rule.id, symbol="600001", trigger_value=0, threshold=0, triggered_at=NOW.replace(hour=9, minute=26, tzinfo=None), delivered_channels='["in_app"]', snapshot=json.dumps({"kind": "selection", "trade_date": DAY.isoformat(), "selection_source": "daily", "source_version": "original", "source_as_of": "2026-10-23T09:26:00+08:00", "selection_evidence": {"pick_basis": "原理由"}}))
        db.add(origin); db.flush()
        for version, status in [("wrong", "filled"), ("original", "rejected")]:
            db.add(AlertEvent(rule_id=rule.id, symbol="600001", trigger_value=0, threshold=0, triggered_at=NOW.replace(hour=10, tzinfo=None), snapshot=json.dumps({"kind": "paper_status", "origin_event_id": origin.id, "source_as_of": "2026-10-23T10:00:00+08:00", "paper_execution": {"scope": "main", "selection_ref": "daily:2026-10-23:600001", "source_version": version, "status": status, "order_id": 1}})))
        db.commit()
    result = collect_selection_journey(sf, DAY)
    assert result["state"] == "saved" and len(result["items"]) == 1
    assert result["items"][0]["execution"]["state"] == "rejected"
    assert result["items"][0]["notification"]["delivery_state"] == "unknown"


def test_late_generated_plan_cannot_become_original_historical_outlook(monkeypatch):
    from app.review.reader_context import collect_outlook
    import app.picks.morning_brief as brief
    import app.review.reader_context as reader
    monkeypatch.setattr(reader, "beijing_now", lambda: NOW)
    monkeypatch.setattr(brief, "load_brief", lambda _day: {"brief_date": "20261022", "generated_at": "2026-10-23T08:40:00+08:00", "directions": [{"direction": "后来补的"}]})
    got = collect_outlook(date(2026, 10, 22))
    assert got["state"] == "mismatch" and got["directions"] == []


def test_sell_order_limit_difference_is_not_realized_profit(monkeypatch, tmp_path):
    import app.core.bjtime as clock
    monkeypatch.setattr(clock, "beijing_now", lambda: NOW)
    sf = sf_at(tmp_path)
    with sf() as db:
        db.add(PaperAccount(scope="main", cash=1000, initial_cash=2000))
        db.add(PaperOrder(scope="main", symbol="600001", side="sell", price=10, quantity=100,
                          status="filled", filled_price=11, fee=5, created_at=datetime(2026, 10, 23, 2)))
        db.commit()
    got = collect_trading(sf, DAY)
    assert got.realized_pnl is None
    assert any(g.field == "realized_pnl" for g in got.gaps)


def test_future_review_date_is_rejected_before_collection(monkeypatch):
    from app.review.service import ReviewService
    import app.review.service as svc
    monkeypatch.setattr(svc, "beijing_now", lambda: NOW)
    service = object.__new__(ReviewService)
    import pytest
    with pytest.raises(ValueError, match="未来日期"):
        asyncio.run(service.resolve_trade_date(date(2026, 10, 24)))


def test_summary_excludes_unbound_price_reviews():
    from app.review.schemas import ReviewData, TradingSnapshot, PicksSnapshot, PickReviewEntry
    from app.review.service import ReviewService
    data = ReviewData(trade_date="20261023", market=MarketSnapshot(trade_date="20261023"),
        trading=TradingSnapshot(trade_date="20261023"), picks=PicksSnapshot(trade_date="20261023", reviews=[
            PickReviewEntry(symbol="600001", verdict="good", reason_category="gone_well", statistics_eligible=False)]))
    assert "准确率" not in ReviewService._summarize([], [], data)


def test_journey_does_not_backfill_later_acceptance_or_untyped_basis(monkeypatch, tmp_path):
    from app.models.alert import AlertEvent, AlertRule
    from app.models.notification_outbox import NotificationOutbox
    from app.review.reader_context import collect_selection_journey
    import app.review.reader_context as reader
    monkeypatch.setattr(reader, "beijing_now", lambda: NOW)
    sf = sf_at(tmp_path)
    day = date(2026, 10, 22)
    original = datetime(2026, 10, 22, 0, 1, tzinfo=BJ_TZ)
    with sf() as db:
        rule = AlertRule(name="fixture", condition_type="source_event", scope="all", threshold=0)
        db.add(rule); db.flush()
        row = AlertEvent(rule_id=rule.id, symbol="600001", trigger_value=0, threshold=0,
            triggered_at=original.replace(tzinfo=None), snapshot=json.dumps({"kind": "selection", "trade_date": day.isoformat(),
            "selection_source": "daily", "source_version": "original", "source_as_of": original.isoformat(),
            "selection_evidence": {"pick_basis": {"untyped": "legacy"}}}))
        db.add(row); db.flush()
        db.add(NotificationOutbox(event_id=row.id, channel="feishu", idempotency_key="fixture", target="hash-only", payload="{}",
            state="accepted", reason="accepted", created_at_ms=int(original.timestamp()*1000),
            expires_at_ms=int(NOW.timestamp()*1000), accepted_at_ms=int(NOW.timestamp()*1000)))
        db.commit()
    got = collect_selection_journey(sf, day)
    assert len(got["items"]) == 1  # 00:01 BJ belongs to this report day, not its UTC date.
    item = got["items"][0]
    assert item["basis"] is None
    assert item["notification"]["outbox"][0]["state"] == "unknown_at_report_cutoff"
    assert item["notification"]["outbox"][0]["accepted_at"] is None


def test_rule_analysis_never_calls_order_limit_difference_a_loss():
    from app.review.analyzers import RulesAnalyzer
    from app.review.config import MethodologyConfig
    from app.review.schemas import ReviewData, TradingSnapshot, OrderRecord
    data = ReviewData(trade_date="20261023", market=MarketSnapshot(trade_date="20261023"),
        trading=TradingSnapshot(trade_date="20261023", trade_count=1,
            orders=[OrderRecord(id=1, symbol="600001", side="sell", price=10, quantity=100, status="filled", filled_price=9)]))
    dim = RulesAnalyzer()._trades(data, MethodologyConfig())
    assert dim.evidence["loss_ratio"] is None
    assert not any("卖出亏损占比" in j for j in dim.judgements)


def test_requested_theme_date_does_not_prove_mixed_current_board_facts(monkeypatch, tmp_path):
    import app.services.theme_service as themes
    hub, svc, _ = setup_market(monkeypatch, tmp_path)

    async def mixed(_provider, requested):
        return {"trade_date": requested.isoformat(), "themes": [{"theme": "采集参考", "board": {"change_pct": 8}}],
                "summary": {"theme_count": 1}}

    monkeypatch.setattr(themes, "build_theme_board", mixed)
    got = asyncio.run(collect_market(hub, svc, DAY))
    assert got.themes == [] and got.theme_summary == {}
    assert got.reported_values["themes"][0]["theme"] == "采集参考"
    assert got.provenance["themes"].state == "unverified"
    assert got.provenance["themes"].source_trade_date is None

    async def should_not_fetch(*args):
        raise AssertionError("historical report fetched mixed current board")

    monkeypatch.setattr(themes, "build_theme_board", should_not_fetch)
    old = asyncio.run(collect_market(hub, svc, date(2026, 10, 22)))
    assert old.themes == [] and "themes" not in old.reported_values
    assert "历史复盘不调用" in old.provenance["themes"].reason


def test_market_collector_consumes_bound_board_sample_without_hiding_it(monkeypatch, tmp_path):
    import app.market.board_review as boards
    from app.review.provenance import evidence
    hub, svc, _ = setup_market(monkeypatch, tmp_path)
    catalog = object()
    original = {"board_code": "881101.TI", "name": "正式行业", "change_pct": 0,
                "main_net_yi": None, "source_trade_date": DAY.isoformat(),
                "source": "ths_official_kline", "source_date_ms": 1792684800000, "binding_state": "partial"}

    async def sample(provider, day, *, catalog_service, retry_missing):
        assert provider is hub.provider and day == DAY and catalog_service is catalog and retry_missing
        return {"boards": {"industry": [original], "concept": []},
                "summary": {"industry": {"verified_count": 1, "universe_count": 320, "missing_count": 0}},
                **evidence(DAY, state="partial", source="ths_official_kline", source_day=DAY).model_dump()}

    monkeypatch.setattr(boards, "collect_review_boards", sample)
    got = asyncio.run(collect_market(hub, svc, DAY, theme_catalog=catalog))
    assert got.sector_boards["industry"] == [original]
    assert got.sector_board_summary["industry"]["verified_count"] == 1
    assert got.provenance["sector_boards"].state == "partial"


def test_existing_daily_source_fills_missing_breadth_without_mixing_sentiment(monkeypatch, tmp_path):
    import app.review.daily_market as daily
    import app.market.trade_calendar as calendar
    from app.review.provenance import evidence
    hub, svc, calls = setup_market(monkeypatch, tmp_path, rows=[])
    path = tmp_path / "existing.duckdb"
    path.touch()
    monkeypatch.setattr(daily, "DEFAULT_DB_PATH", path)

    async def dates(provider):
        return [date(2026, 10, 22), DAY]

    def saved(day, *, trade_days, now):
        assert day == DAY and trade_days[-1] == DAY
        return ({"up": 2, "down": 0, "total": 3, "basis": "复权日线对照", "coverage": {"missing_count": 1}},
                evidence(day, state="partial", source="marketdb.daily_k+daily_k_adj", source_day=day, reason="2/3；无源日内钟"),
                {"source_version": "existing-version"})

    monkeypatch.setattr(calendar, "trading_days", dates)
    monkeypatch.setattr(daily, "collect_daily_breadth", saved)
    got = asyncio.run(collect_market(hub, svc, DAY))
    assert got.breadth["up"] == 2 and "limit_up" not in got.breadth
    assert got.reported_values["daily_breadth_audit"]["source_version"] == "existing-version"
    assert got.provenance["breadth"].source_trade_date == DAY.isoformat()
    assert got.sentiment == {} and "sentiment" not in calls
    assert [g for g in got.gaps if g.field == "breadth"][0].source == "marketdb.daily_k+daily_k_adj"


def test_outlook_post_review_has_its_own_cutoff_not_plan_generation_clock(monkeypatch):
    from app.review.reader_context import collect_outlook
    import app.picks.morning_brief as brief
    import app.review.reader_context as reader
    monkeypatch.setattr(reader, "beijing_now", lambda: NOW)
    raw = {"brief_date": "20261022", "generated_at": "2026-10-22T08:40:00+08:00",
           "review": {"reviewed_at": "2026-10-23T08:40:00+08:00"},
           "directions": [{"direction": "原方向", "review": {"outcome": "later"}}]}
    monkeypatch.setattr(brief, "load_brief", lambda _day: raw)
    got = collect_outlook(date(2026, 10, 22))
    assert got["state"] == "saved" and got["directions"][0]["review"] is None
    raw["review"]["reviewed_at"] = "2026-10-22T15:10:00+08:00"
    got = collect_outlook(date(2026, 10, 22))
    assert got["directions"][0]["review"] == {"outcome": "later"}
    assert got["directions"][0]["reviewed_at"] == "2026-10-22T15:10:00+08:00"
