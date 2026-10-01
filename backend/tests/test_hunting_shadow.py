"""Isolated real matching and atomic recovery, never production data."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.models.hunting_shadow import HuntingShadowAttempt as Attempt
from app.models.paper import PaperAccount, PaperOrder
from app.models.watchlist import Base
from app.paper.engine import PaperTradingEngine
from app.picks import hunting_shadow as hs
from app.picks.opportunity_learning import archive_notification_pipeline, latest_notification_execution
from app.schemas.market import Quote, Quality


@pytest.fixture
def setup(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'shadow.sqlite'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    clock = [datetime(2026, 9, 29, 10, 0, tzinfo=BJ_TZ)]
    monkeypatch.setattr(hs, "beijing_now", lambda: clock[0])
    monkeypatch.setattr("app.paper.engine._today", lambda: clock[0].strftime("%Y%m%d"))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    monkeypatch.setattr("app.core.freshness.datetime", Clock)
    quotes = {"600127": Quote(symbol="600127", name="合成材料", price=10., change_pct=2.,
                              prev_close=9.8, limit_up_price=10.78, limit_down_price=8.82,
                              source="fixture", data_timestamp=clock[0], received_at=clock[0])}
    async def quote_fn(symbol): return quotes.get(symbol)
    async def days_fn(): return ["20260929", "20260930", "20261009"]
    class Risk:
        hub = SimpleNamespace(get_quotes=lambda: [])
        allowed = True
        def check_order(self, **kwargs):
            return {"allowed": self.allowed, "reasons": [] if self.allowed else ["fixture risk rejection"]}
    risk = Risk()
    paper = PaperTradingEngine(sf, quote_fn, days_fn, scope=hs.SCOPE, risk_engine=risk)
    yield sf, clock, quotes, hs.HuntingShadowRunner(paper, sf), risk
    engine.dispose()


def archive(sf, clock, *, eligible=True, price=10, symbol="600127"):
    item = {"symbol": symbol, "name": "合成材料", "price": 9., "confidence": {"tier": "strong"},
            "buy_range": {"low": 9., "high": 10.5}, "vetoes": [], "follow_state": "followable"}
    snap = {"price": price, "change_pct": 2., "prev_close": 9.8, "source": "fixture", "state": "ready",
            "checked_at": clock[0].isoformat(), "as_of": clock[0].isoformat()}
    archive_notification_pipeline([item], trade_date=clock[0].date().isoformat(), as_of=clock[0],
                                  hits=[{"item": item, "price": price, "chg": 2.}] if eligible else [],
                                  skips=[] if eligible else [{"symbol": symbol, "reason": "wait"}],
                                  dispatch_by_symbol={}, execution_by_symbol={symbol: snap},
                                  session_factory=sf, pick_generated_at=clock[0].isoformat())
    return latest_notification_execution(clock[0].date().isoformat(), sf)[symbol]


def run(runner): asyncio.run(runner.tick())


def test_wait_then_new_eligible_version_fills_only_once_and_separate_scope(setup):
    sf, clock, _, runner, _ = setup
    before = archive(sf, clock, eligible=False); run(runner)
    assert hs.read_summary(sf)["opportunities"] == 0
    clock[0] += timedelta(seconds=1)
    ready = archive(sf, clock)
    assert ready["decision_version"] != before["decision_version"]
    run(runner); run(runner)
    out = hs.read_summary(sf)
    assert out["counts"] == {"filled": 1, "rejected": 0, "no_fill": 0, "expired": 0, "pending": 0}
    assert out["records"][0]["reference"]["price"] == 9
    assert out["records"][0]["filled_price"] == 10
    assert out["net_return_pct"] is None
    with sf() as db:
        assert len(list(db.scalars(select(PaperOrder)))) == 1
        assert {a.scope for a in db.scalars(select(PaperAccount))} == {hs.SCOPE}
    clock[0] += timedelta(seconds=1)
    archive(sf, clock, price=10.1); run(runner)
    assert hs.read_summary(sf)["opportunities"] == 1


def test_exit_next_confirmed_session_has_actual_fees_and_no_same_day_sell(setup):
    sf, clock, quotes, runner, _ = setup
    archive(sf, clock); run(runner)
    clock[0] += timedelta(minutes=1); run(runner)
    assert hs.read_summary(sf)["closed_fills"] == 0
    clock[0] = datetime(2026, 9, 30, 10, 0, tzinfo=BJ_TZ)
    quotes["600127"] = quotes["600127"].model_copy(update={"price": 10.2, "received_at": clock[0], "data_timestamp": clock[0]})
    run(runner); run(runner)
    out = hs.read_summary(sf)
    assert out["status"] == "complete" and out["closed_fills"] == out["fills_total"] == 1
    r = out["records"][0]
    with sf() as db:
        entry, exit_order = db.get(PaperOrder, r["entry_order_id"]), db.get(PaperOrder, r["exit_order_id"])
        expected = ((exit_order.filled_price * exit_order.quantity - exit_order.fee) /
                    (entry.filled_price * entry.quantity + entry.fee) - 1) * 100
        assert out["net_return_pct"] == pytest.approx(expected)
        assert 0 < expected < 2
        assert len(list(db.scalars(select(PaperOrder)))) == 2


@pytest.mark.parametrize("mode", ["risk", "missing_risk", "stale", "limit", "range", "calendar", "expired"])
def test_failures_enter_denominator_without_fill(setup, mode):
    sf, clock, quotes, runner, risk = setup
    archive(sf, clock)
    if mode == "risk": risk.allowed = False
    if mode == "missing_risk": runner.engine._risk_engine = None
    if mode == "stale": quotes["600127"].quality = Quality.stale
    if mode == "limit": quotes["600127"].price = 10.78; quotes["600127"].change_pct = 10
    if mode == "range": quotes["600127"].price = 8
    if mode == "calendar":
        async def none(): return None
        runner.engine._tdays_fn = none
    if mode == "expired": clock[0] += timedelta(seconds=61)
    run(runner); run(runner)
    out = hs.read_summary(sf)
    assert out["opportunities"] == 1
    assert out["counts"]["rejected"] + out["counts"]["expired"] == 1
    assert out["fills_total"] == 0 and out["net_return_pct"] is None
    assert out["records"][0]["filled_price"] is None


def test_pending_expiry_refunds_cash(setup):
    sf, clock, _, runner, _ = setup
    archive(sf, clock, price=9.9); run(runner)
    assert hs.read_summary(sf)["counts"]["pending"] == 1
    clock[0] += timedelta(seconds=61); run(runner); run(runner)
    out = hs.read_summary(sf)
    assert out["counts"]["no_fill"] == 1 and out["counts"]["pending"] == 0
    assert out["fills_total"] == 0
    with sf() as db:
        acc = db.scalar(select(PaperAccount).where(PaperAccount.scope == hs.SCOPE))
        assert acc.cash == acc.initial_cash
        assert db.scalar(select(PaperOrder)).status == "cancelled"


def test_pending_bounded_fill_and_superseded_version(setup):
    sf, clock, quotes, runner, _ = setup
    archive(sf, clock, price=9.9); run(runner)
    clock[0] += timedelta(seconds=30)
    archive(sf, clock, eligible=False)
    quotes["600127"].price = 9.85; quotes["600127"].data_timestamp = clock[0]
    run(runner)
    assert hs.read_summary(sf)["fills_total"] == 0
    clock[0] += timedelta(seconds=31); run(runner)
    assert hs.read_summary(sf)["counts"]["no_fill"] == 1


def test_current_version_pending_can_fill_at_actual_later_price(setup):
    sf, clock, quotes, runner, _ = setup
    archive(sf, clock, price=9.9); run(runner)
    clock[0] += timedelta(seconds=30)
    quotes["600127"].price = 9.85; quotes["600127"].data_timestamp = clock[0]
    run(runner)
    out = hs.read_summary(sf)
    assert out["fills_total"] == 1 and out["counts"]["pending"] == 0
    assert out["records"][0]["filled_price"] == 9.85


def test_after_fill_storage_crash_rolls_back_money_order_and_receipt(setup, monkeypatch):
    sf, clock, _, runner, _ = setup
    archive(sf, clock)
    original = hs._json
    def fail(value):
        if isinstance(value, dict) and value.get("timeline"):
            raise OSError("injected storage failure")
        return original(value)
    monkeypatch.setattr(hs, "_json", fail)
    with pytest.raises(OSError): run(runner)
    with sf() as db:
        assert list(db.scalars(select(PaperOrder))) == []
        assert list(db.scalars(select(PaperAccount))) == []
        assert list(db.scalars(select(Attempt))) == []
    monkeypatch.setattr(hs, "_json", original)
    run(runner); run(runner)
    assert hs.read_summary(sf)["fills_total"] == 1


def test_read_only_summary_does_not_create_account(setup):
    sf, *_ = setup
    assert hs.read_summary(sf)["opportunities"] == 0
    with sf() as db:
        assert list(db.scalars(select(PaperAccount))) == []
        assert list(db.scalars(select(PaperOrder))) == []


def test_execution_owned_readiness_rejects_resealed_forgery_and_missing_order(setup, monkeypatch):
    sf, clock, quotes, runner, _ = setup
    archive(sf, clock); run(runner)
    clock[0] = datetime(2026, 9, 30, 10, 0, tzinfo=BJ_TZ)
    quotes["600127"].data_timestamp = clock[0]; run(runner)
    evidence = hs.read_summary(sf)
    assert hs.validate_execution_evidence(evidence, sf)
    monkeypatch.setattr("app.core.db._session_factory", sf)
    from app.research.strategy_readiness import build_readiness
    report = build_readiness(strategy_key="daily_picks", verification=None, experiment=None, actual_fill=evidence)
    assert "actual_shadow_fill_not_execution_owned" not in report["blocking_issues"]
    assert "actual_shadow_fill_digest_invalid" not in report["blocking_issues"]
    assert "actual_shadow_fill_experiment_versions_unbound" in report["blocking_issues"]
    assert report["state"] == "blocked"  # existing rule fills don't validate a new experiment
    forged = {**evidence, "net_return_pct": 100}
    forged["evidence_digest"] = hs._digest({k:v for k,v in forged.items() if k != "evidence_digest"})
    assert not hs.validate_execution_evidence(forged, sf)
    with sf() as db:
        entry = db.get(PaperOrder, evidence["records"][0]["entry_order_id"])
        entry.scope = "main"; db.commit()
    current = hs.read_summary(sf)
    assert current["status"] == "incomplete" and current["issues"]
    assert current["net_return_pct"] is None
    assert not hs.validate_execution_evidence(evidence, sf)


def test_pending_and_exit_cash_reconcile_for_new_scope(setup):
    sf, clock, _, runner, _ = setup
    archive(sf, clock, price=9.9); run(runner)
    from app.paper.reconcile import reconcile
    assert reconcile(sf)["ok"]
    clock[0] += timedelta(seconds=61); run(runner)
    assert reconcile(sf)["ok"]


def test_exit_block_and_unknown_calendar_preserve_unclosed_fill(setup):
    sf, clock, quotes, runner, _ = setup
    archive(sf, clock); run(runner)
    clock[0] = datetime(2026, 9, 30, 10, 0, tzinfo=BJ_TZ)
    quotes["600127"].price = 8.82; quotes["600127"].data_timestamp = clock[0]
    run(runner)
    out = hs.read_summary(sf)
    assert out["closed_fills"] == 0 and out["status"] == "incomplete"
    assert "跌停" in out["records"][0]["reason"]
    quotes["600127"].price = 10
    async def unknown(): raise OSError("calendar offline")
    runner.engine._tdays_fn = unknown
    run(runner)
    assert hs.read_summary(sf)["closed_fills"] == 0


def test_summary_route_reads_disabled_history_and_validates_date(setup, monkeypatch):
    sf, clock, _, runner, _ = setup
    archive(sf, clock); run(runner)
    from app.api.routes.paper import hunting_shadow_summary
    from app.core.config import settings
    from fastapi import HTTPException
    monkeypatch.setattr(settings, "hunting_shadow_enabled", False)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(paper=SimpleNamespace(_sf=sf))))
    result = asyncio.run(hunting_shadow_summary(request))
    assert not result["data"]["enabled"] and result["data"]["opportunities"] == 1
    assert result["data"]["runtime"]["state"] == "not_loaded"
    with pytest.raises(HTTPException):
        asyncio.run(hunting_shadow_summary(request, "wrong-date"))
    with sf() as db:
        assert len(list(db.scalars(select(PaperOrder)))) == 1


def test_two_workers_same_version_commit_one_fill(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    sf, clock, quotes, runner, _ = setup
    contract = archive(sf, clock)
    ready = Barrier(2)
    async def simultaneous_quote(symbol):
        ready.wait(timeout=5)
        return quotes[symbol]
    runner.engine._quote_fn = simultaneous_quote
    def work():
        asyncio.run(runner.submit("600127", contract, clock[0], ["2026-09-29", "2026-09-30"]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(work), pool.submit(work)]
        for future in futures: future.result(timeout=10)
    assert hs.read_summary(sf)["fills_total"] == 1
    from app.paper.reconcile import reconcile
    assert reconcile(sf)["ok"]


def test_after_exit_storage_crash_rolls_back_sale_then_retries_once(setup, monkeypatch):
    sf, clock, quotes, runner, _ = setup
    archive(sf, clock); run(runner)
    clock[0] = datetime(2026, 9, 30, 10, 0, tzinfo=BJ_TZ)
    quotes["600127"].data_timestamp = clock[0]
    original = hs._json
    def fail(value):
        if isinstance(value, dict) and any(t.get("state") == "exited" for t in value.get("timeline", [])):
            raise OSError("injected exit receipt crash")
        return original(value)
    monkeypatch.setattr(hs, "_json", fail)
    with pytest.raises(OSError): run(runner)
    with sf() as db: assert len(list(db.scalars(select(PaperOrder)))) == 1
    monkeypatch.setattr(hs, "_json", original)
    run(runner); run(runner)
    assert hs.read_summary(sf)["closed_fills"] == 1
    from app.paper.reconcile import reconcile
    assert reconcile(sf)["ok"]


def test_pending_rechecks_risk_then_expires_with_refund(setup):
    sf, clock, quotes, runner, _ = setup
    archive(sf, clock, price=9.9); run(runner)
    clock[0] += timedelta(seconds=30)
    quotes["600127"].price = 9.85; quotes["600127"].data_timestamp = clock[0]
    runner.engine._risk_engine = None
    run(runner)
    out = hs.read_summary(sf)
    assert out["counts"]["pending"] == 1 and out["fills_total"] == 0
    assert "风控未装配" in out["records"][0]["reason"]
    clock[0] += timedelta(seconds=31); run(runner)
    assert hs.read_summary(sf)["counts"]["no_fill"] == 1
    from app.paper.reconcile import reconcile
    assert reconcile(sf)["ok"]


def test_pending_never_fills_mismatched_quote_symbol(setup):
    sf, clock, quotes, runner, _ = setup
    archive(sf, clock, price=9.9); run(runner)
    clock[0] += timedelta(seconds=30)
    quotes["600127"].price = 9.85; quotes["600127"].data_timestamp = clock[0]
    quotes["600127"].symbol = "600519"
    run(runner)
    assert hs.read_summary(sf)["counts"]["pending"] == 1
    assert hs.read_summary(sf)["fills_total"] == 0


def test_unknown_attempt_state_blocks_aggregate_effect_claim(setup):
    sf, clock, _, runner, _ = setup
    archive(sf, clock); run(runner)
    with sf() as db:
        row = db.scalar(select(Attempt)); row.state = "unknown"; db.commit()
    out = hs.read_summary(sf)
    assert out["status"] == "incomplete" and out["net_return_pct"] is None
    assert any("execution_state_unknown" in issue for issue in out["issues"])
