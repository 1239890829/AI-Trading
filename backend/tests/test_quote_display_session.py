"""Calendar-backed UI context must never upgrade business quote quality."""
from datetime import date, datetime, timezone

import pytest

from app.core.bjtime import BJ_TZ
from app.core import freshness
from app.schemas.market import Quality, Quote
from app.services import quote_hub as qh


def hub_at(monkeypatch, clock, trade_day=True):
    now = datetime.fromisoformat(f"2026-10-13T{clock}+08:00")
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(freshness, "datetime", FixedDatetime)
    monkeypatch.setattr(qh, "beijing_now", lambda: now)
    # 使用真实的二参数日历契约；固定日期，不借用运行机器的日历快照。
    days = [date(2026, 10, 12)]
    if trade_day is not None:
        days.append(date(2026, 10, 13) if trade_day else date(2026, 10, 14))
    monkeypatch.setattr(qh.tc, "known_trade_days", lambda: days)
    return qh.QuoteHub(provider=None, poll_interval=1, stale_after=10)


@pytest.mark.parametrize("clock,phase", [
    ("09:14:59", "closed"), ("09:15:00", "auction"),
    ("09:24:59", "auction"), ("09:25:00", "pre_open_gap"),
    ("09:29:59", "pre_open_gap"), ("09:30:00", "continuous"),
    ("11:31:00", "closed"), ("13:00:00", "continuous"),
])
def test_known_calendar_boundaries(monkeypatch, clock, phase):
    session = hub_at(monkeypatch, clock)._display_session()
    assert session.phase == phase
    assert session.trade_date == date(2026, 10, 13)
    assert session.as_of.utcoffset() == BJ_TZ.utcoffset(None)
    assert session.trade_day_result is True


@pytest.mark.parametrize("trade_day,phase", [(False, "closed"), (None, "unknown")])
def test_holiday_or_unknown_calendar_cannot_become_auction(monkeypatch, trade_day, phase):
    assert hub_at(monkeypatch, "09:20:00", trade_day)._display_session().phase == phase


@pytest.mark.parametrize("source_time", ["2026-10-12T15:00:00+08:00", "2026-10-13T09:25:00+08:00"])
def test_aged_quote_keeps_original_business_age_and_current_cache(monkeypatch, source_time):
    hub = hub_at(monkeypatch, "09:27:00")
    current = Quote(symbol="000001", market="SH", price=3000,
                    data_timestamp=datetime.fromisoformat(source_time),
                    received_at=datetime.fromisoformat("2026-10-13T09:26:59+08:00"), source="fixture")
    hub.indices[current.symbol] = current
    before = current.model_dump()
    visible = hub.get_quotes(["sh000001"])[0]
    assert visible.quality is Quality.stale
    assert visible.quality_reasons == ["quote_age_exceeded"]
    assert not visible.freshness(fresh_within=10).is_fresh()
    assert visible.data_timestamp == current.data_timestamp
    assert visible.received_at == current.received_at
    assert visible.price == current.price
    assert visible.display_session.phase == "pre_open_gap"
    assert current.model_dump() == before
    assert current.display_session is None
    assert hub.stale_after == 10
    assert hub.poll_interval == 1


def test_source_rejection_and_recovery_remain_raw_quality_transitions(monkeypatch):
    hub = hub_at(monkeypatch, "09:20:00")
    current = Quote(symbol="600127", price=10, source="fixture", quality=Quality.stale,
                    quality_reasons=["source_time_regress_ignored"],
                    data_timestamp=datetime(2026, 10, 13, 1, 19, 59, tzinfo=timezone.utc))
    hub.quotes[current.symbol] = current
    rejected = hub.get_quotes([current.symbol])[0]
    assert rejected.quality is Quality.stale
    assert rejected.quality_reasons == ["source_time_regress_ignored"]
    assert not rejected.freshness(fresh_within=10).is_fresh()
    hub.quotes[current.symbol] = current.model_copy(update={"quality": Quality.high, "quality_reasons": []})
    recovered = hub.get_quotes([current.symbol])[0]
    assert recovered.quality is Quality.high
    assert recovered.freshness(fresh_within=10).is_fresh()
    assert recovered.display_session.phase == "auction"
    assert hub.quotes[current.symbol].display_session is None


def test_display_context_does_not_change_execution_version(monkeypatch):
    from app.picks.opportunity_learning import build_execution_contract

    hub = hub_at(monkeypatch, "09:30:00")
    quote = Quote(symbol="600127", price=10, change_pct=1, prev_close=9.9,
                  source="fixture", data_timestamp=datetime(2026, 10, 13, 1, 30, tzinfo=timezone.utc))
    hub.quotes[quote.symbol] = quote
    arguments = dict(trade_date="2026-10-13", symbol=quote.symbol,
                     item={"price": 10, "confidence": {"tier": "high"}},
                     gate_decision="notify", gate_reason=None,
                     pick_generated_at="2026-10-13T09:20:00+08:00")
    snapshot = {"state": "ready", **quote.model_dump(mode="json")}
    before = build_execution_contract(executable_snapshot=snapshot, **arguments)
    display = hub.get_quotes([quote.symbol])[0].model_dump(mode="json")
    after = build_execution_contract(executable_snapshot={"state": "ready", **display}, **arguments)
    assert display["display_session"] is not None
    assert before["decision_id"] == after["decision_id"]
    assert before["decision_version"] == after["decision_version"]
    changed = build_execution_contract(executable_snapshot={"state": "ready", **display, "price": 11}, **arguments)
    assert changed["decision_version"] != before["decision_version"]


@pytest.mark.parametrize("trade_day,phase", [(True, "auction"), (False, "closed"), (None, "unknown")])
def test_wire_context_preserves_raw_reasons_and_calendar_unknown(monkeypatch, trade_day, phase):
    hub = hub_at(monkeypatch, "09:20:00", trade_day)
    reasons = ["source_time_unknown_ignored", "future_reason_v99:尚未核验"]
    quote = Quote(symbol="600127", price=10, source="fixture", quality=Quality.stale,
                  quality_reasons=reasons,
                  data_timestamp=datetime(2026, 10, 13, 1, 19, 59, tzinfo=timezone.utc))
    hub.quotes[quote.symbol] = quote
    before = quote.model_dump(mode="json")
    wire = hub.get_quotes([quote.symbol])[0].model_dump(mode="json")
    assert wire["quality_reasons"] == reasons
    assert wire["display_session"]["trade_day_result"] is trade_day
    assert wire["display_session"]["phase"] == phase
    assert {k: value for k, value in wire.items() if k != "display_session"} == {
        k: value for k, value in before.items() if k != "display_session"
    }
    assert quote.model_dump(mode="json") == before


def test_visible_mutation_cannot_change_cache_other_quotes_or_reused_session(monkeypatch):
    hub = hub_at(monkeypatch, "09:20:00")
    for symbol in ("600127", "600128"):
        hub.quotes[symbol] = Quote(symbol=symbol, price=10, source="fixture",
                                   quality=Quality.stale,
                                   quality_reasons=["source_time_regress_ignored"],
                                   data_timestamp=datetime(2026, 10, 13, 1, 19, 59, tzinfo=timezone.utc))
    before = {symbol: quote.model_dump(mode="json") for symbol, quote in hub.quotes.items()}
    session = hub._display_session()
    left = hub._visible_quote(hub.quotes["600127"], session)
    right = hub._visible_quote(hub.quotes["600128"], session)
    assert left.quality_reasons is not hub.quotes["600127"].quality_reasons
    assert left.display_session is not right.display_session
    assert left.display_session is not session
    left.price = 999
    left.quality = Quality.high
    left.quality_reasons.append("consumer_side_mutation")
    left.display_session.phase = "closed"
    left.display_session.trade_day_result = False
    left.display_session.as_of = datetime(2026, 10, 12, 7, 0, tzinfo=timezone.utc)
    assert {symbol: quote.model_dump(mode="json") for symbol, quote in hub.quotes.items()} == before
    assert right.quality_reasons == ["source_time_regress_ignored"]
    assert right.display_session.phase == session.phase == "auction"
    assert right.display_session.trade_day_result is session.trade_day_result is True
    again = hub.get_quotes(["600127"])[0]
    assert again.price == 10
    assert again.quality is Quality.stale
    assert again.quality_reasons == ["source_time_regress_ignored"]
    assert again.display_session.phase == "auction"
