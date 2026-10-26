"""Real shadow summaries distinguish execution evidence from skip reasons."""
from copy import deepcopy
from datetime import date, datetime
import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.paper import PaperAccount
from app.models.watchlist import Base
from app.picks import shadow
from app.review.analyzers import RulesAnalyzer
from app.review.collector import collect_picks
from app.review.config import load_methodology
from app.review.schemas import (
    MarketSnapshot, PickEntry, PickReviewEntry, PicksSnapshot, ReviewData,
    ShadowSnapshot, TradingSnapshot,
)


DAY = "2026-10-26"
EXECUTED_AT = "2026-10-26T09:27:00+08:00"


def executed(skipped=None):
    return {"day": DAY, "executed_at": EXECUTED_AT,
        "bought": [{"symbol": "600001", "status": "filled", "qty": 100, "filled_price": 10}],
        "sold": [{"symbol": "600002", "status": "filled", "qty": 100, "filled_price": 11}],
        "skipped": skipped if skipped is not None else [],
        "gate_summary": {"blocked": 1, "observe": 0, "anomaly": 0, "unknown": 0}}


def analyze(execution, *, account=None, items=True, reviews=True):
    picks = PicksSnapshot(trade_date=DAY, combo_date=DAY,
        items=[PickEntry(symbol="600001")] if items else [],
        reviews=[PickReviewEntry(symbol="600001", verdict="good", reason_category="gone_well", statistics_eligible=True)] if reviews else [],
        shadow=ShadowSnapshot(enabled=True, execution=execution,
            account=account if account is not None else {"cash": 1000, "total_return_pct": 1.0}))
    data = ReviewData(trade_date=DAY, picks=picks,
        market=MarketSnapshot(trade_date=DAY), trading=TradingSnapshot(trade_date=DAY))
    before = data.model_dump()
    dimensions = RulesAnalyzer().analyze(data, load_methodology("v1"))
    assert data.model_dump() == before
    assert {d.key for d in dimensions} == {"trades", "market", "system", "picks"}
    return next(d for d in dimensions if d.key == "picks")


@pytest.mark.parametrize("reason", [shadow.SKIP_WINDOW_CLOSED, shadow.SKIP_NOT_TRADING_DAY])
def test_reason_only_skip_keeps_reason_without_inventing_execution_or_order_counts(reason):
    dim = analyze({"day": DAY, "skipped": reason, "skipped_reason": reason, "caveat": "原始跳过依据"})
    assert dim.status == "ok"
    assert dim.evidence["shadow"]["state"] == "skip_recorded"
    assert dim.evidence["shadow"]["skip_reason"] == reason
    assert dim.evidence["shadow"]["skipped_count"] is None
    assert dim.evidence["shadow"]["bought_count"] is None
    assert any(reason in item and "原始跳过依据" in item for item in dim.findings)
    assert not any("买入成交" in item for item in dim.findings)


@pytest.mark.parametrize("skipped", [[], [{"symbol": "600003", "state": "blocked", "reason": "竞价禁买"}]])
def test_executed_summary_keeps_fills_and_a_real_skip_list(skipped):
    dim = analyze(executed(skipped))
    proof = dim.evidence["shadow"]
    assert dim.status == "ok" and proof["state"] == "execution_recorded"
    assert proof["bought_count"] == proof["sold_count"] == 1
    assert len(proof["buy_fills"]) == len(proof["sell_fills"]) == 1
    assert proof["skipped_count"] == len(skipped) and proof["execution_details_complete"] is True


def test_executed_zero_orders_is_distinct_from_a_reason_only_skip():
    row = executed([])
    row["bought"] = row["sold"] = []
    dim = analyze(row)
    assert dim.status == "ok" and dim.evidence["shadow"]["state"] == "execution_recorded"
    assert dim.evidence["shadow"]["bought_count"] == dim.evidence["shadow"]["sold_count"] == 0
    assert dim.evidence["shadow"]["execution_details_complete"] is True


def test_mixed_zero_order_summary_keeps_execution_evidence_and_does_not_invent_skip_count():
    row = executed(shadow.SKIP_WINDOW_CLOSED)
    row.update(bought=[], sold=[], executed_at="2026-10-26T09:44:17.421756+08:00",
               updated_at="2026-10-26T09:45:21.502037+08:00", skipped_reason=shadow.SKIP_WINDOW_CLOSED)
    dim = analyze(row)
    assert dim.status == "degraded"
    proof = dim.evidence["shadow"]
    assert proof["state"] == "execution_recorded" and proof["bought_count"] == proof["sold_count"] == 0
    assert proof["skipped_count"] is None and proof["execution_details_complete"] is False
    assert any("已有执行记录" in item for item in dim.findings)
    assert any("逐股跳过明细未知" in gap.reason for gap in dim.gaps)


@pytest.mark.parametrize("items,reviews", [(True, True), (False, True), (True, False)])
def test_mixed_legacy_summary_keeps_fills_and_exposes_lost_skip_details_even_on_early_returns(items, reviews):
    row = executed(shadow.SKIP_WINDOW_CLOSED)
    row["skipped_reason"] = shadow.SKIP_WINDOW_CLOSED
    original = deepcopy(row)
    dim = analyze(row, items=items, reviews=reviews)
    proof = dim.evidence["shadow"]
    assert dim.status == "degraded" and proof["state"] == "execution_recorded"
    assert len(proof["buy_fills"]) == len(proof["sell_fills"]) == 1
    assert proof["executed_at"] == EXECUTED_AT and proof["skip_reason"] == shadow.SKIP_WINDOW_CLOSED
    assert proof["skipped_count"] is None and proof["execution_details_complete"] is False
    assert any(g.field == "picks.shadow.skipped" for g in dim.gaps)
    assert any("逐股跳过明细未知" in item and shadow.SKIP_WINDOW_CLOSED in item for item in dim.findings)
    assert row == original


@pytest.mark.parametrize("field,value", [
    ("bought", "bad"), ("bought", [None, {"symbol": "600001", "status": "filled"}]),
    ("bought", [{"symbol": "600001", "status": "unrecognized"}]),
    ("bought", [{"status": "filled"}]),
    ("sold", {}), ("skipped", [None, "bad"]), ("gate_summary", []),
    ("skipped", [{}]), ("gate_summary", {"blocked": "bad"}),
    ("gate_summary", {"blocked": True}), ("gate_summary", {"blocked": -1}),
])
def test_malformed_execution_fields_degrade_without_crashing_the_whole_review(field, value):
    row = executed([])
    row[field] = value
    dim = analyze(row)
    assert dim.status == "degraded"
    assert dim.evidence["shadow"]["execution_details_complete"] is False
    assert any(g.field.startswith("picks.shadow.") for g in dim.gaps)
    if field == "bought":
        assert dim.evidence["shadow"]["bought_count"] is None
        assert any("买入已见成交" in item and "完整成交数与委托总数未知" in item for item in dim.findings)
    if field == "gate_summary":
        assert any("闸门禁买 ?、观察 ?、异常 ?、未知 ?" in item for item in dim.findings)


def test_bad_account_return_does_not_crash_or_claim_valid_performance():
    dim = analyze(executed([]), account={"cash": 1000, "total_return_pct": "bad"})
    assert dim.status == "degraded" and any(g.field == "picks.shadow.account" for g in dim.gaps)
    assert not any("累计收益" in item for item in dim.findings)


@pytest.mark.parametrize("reason", [shadow.SKIP_NO_RUNNER, shadow.SKIP_GATE_NOT_JUDGEABLE])
def test_operational_skip_reason_stays_visible_as_a_gap(reason):
    dim = analyze({"day": DAY, "skipped": reason, "skipped_reason": reason})
    assert dim.status == "degraded" and any(g.field == "picks.shadow.execution" for g in dim.gaps)
    assert dim.evidence["shadow"]["skip_reason"] == reason


@pytest.mark.parametrize("record", [None, {"skipped": shadow.SKIP_NOT_TRADING_DAY},
    {"bought": [], "sold": [], "skipped": [], "executed_at": EXECUTED_AT}])
def test_collector_with_existing_account_distinguishes_missing_log_skip_and_zero_execution(tmp_path, monkeypatch, record):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    monkeypatch.setattr(shadow, "_shadow_dir", lambda: tmp_path)
    monkeypatch.setattr(settings, "picks_shadow_enabled", True)
    if record is not None:
        (tmp_path / f"{DAY}.json").write_text(json.dumps(record), encoding="utf-8")
    try:
        with sf() as db:
            db.add(PaperAccount(scope="shadow", cash=1000, initial_cash=1000))
            db.commit()
        collected = collect_picks(sf, date(2026, 10, 26))
        assert collected.shadow.enabled is True
        assert collected.shadow.execution == record
        missing = [gap for gap in collected.gaps if gap.field == "picks.shadow.execution"]
        assert bool(missing) is (record is None)
    finally:
        engine.dispose()


@pytest.mark.parametrize("missing", [False, True])
def test_service_saves_shadow_reason_and_gaps_in_report_summary_without_collecting_or_calling_models(tmp_path, monkeypatch, missing):
    from app.review import service, storage
    from app.review.schemas import DimensionResult
    import app.review.reader_context as readers
    import app.review.strategy_health as strategy
    import app.research.leader_collector as leaders
    import app.picks.source_events as events
    import app.core.llm_client as llm

    td = date(2099, 1, 2)
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated-review.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    monkeypatch.setattr(shadow, "_shadow_dir", lambda: tmp_path / "shadow")
    monkeypatch.setattr(settings, "picks_shadow_enabled", True)
    monkeypatch.setattr(storage, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(service, "beijing_now", lambda: datetime.fromisoformat("2099-01-03T16:00:00+08:00"))

    async def market(*args, **kwargs):
        return MarketSnapshot(trade_date="20990102")

    def forbidden(*args, **kwargs):
        raise AssertionError("isolated review must not fetch calendar or call a model")

    monkeypatch.setattr(service, "collect_market", market)
    monkeypatch.setattr(service, "collect_trading", lambda *args: TradingSnapshot(trade_date="20990102"))
    monkeypatch.setattr(service.tc, "trading_days", forbidden)
    monkeypatch.setattr(llm, "chat_completion", forbidden)
    monkeypatch.setattr(readers, "collect_reader_context", lambda *args: {"outlook": None, "selection_journey": None})
    monkeypatch.setattr(strategy, "build_strategy_health_dimension", lambda *args: (DimensionResult(key="health", title="隔离", status="ok"), None))
    monkeypatch.setattr(strategy, "build_strategy_key_action_items", lambda *args: [])
    monkeypatch.setattr(leaders, "review_contribution", lambda *args: (DimensionResult(key="leader_research", title="隔离", status="ok"), []))
    monkeypatch.setattr(events, "record_review_report", lambda *args: None)
    if not missing:
        directory = tmp_path / "shadow"
        directory.mkdir()
        (directory / f"{td.isoformat()}.json").write_text(json.dumps({
            "day": td.isoformat(), "bought": [], "sold": [],
            "executed_at": "2099-01-02T09:44:17.421756+08:00",
            "skipped": shadow.SKIP_WINDOW_CLOSED, "skipped_reason": shadow.SKIP_WINDOW_CLOSED,
            "gate_summary": {},
        }), encoding="utf-8")
    try:
        with sf() as db:
            db.add(PaperAccount(scope="shadow", cash=1000, initial_cash=1000))
            db.commit()
        report = asyncio.run(service.ReviewService(None, None, sf, model="rules").run(td))
        reread = storage.get_report(sf, "20990102")
        assert reread.review_id == report.review_id
        target = "picks.shadow.execution" if missing else "picks.shadow.skipped"
        assert any(gap.field == target for gap in reread.data.all_gaps)
        assert f"数据缺失 {len(reread.data.all_gaps)} 处" in reread.summary
        dim = next(d for d in reread.dimensions if d.key == "picks")
        assert dim.status == "degraded" and any(gap.field == target for gap in dim.gaps)
        if not missing:
            assert dim.evidence["shadow"]["state"] == "execution_recorded"
            assert dim.evidence["shadow"]["skipped_count"] is None
            assert any(shadow.SKIP_WINDOW_CLOSED in finding for finding in dim.findings)
        files = list((tmp_path / "reports").rglob("*.json"))
        assert files and any(json.loads(path.read_text())["summary"] == reread.summary for path in files)
    finally:
        engine.dispose()
