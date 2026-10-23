"""Rejected packets do not replace the retained quote's own clock or phase."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core import freshness
from app.data_quality import validator
from app.market.indices import INDEX_MARKETS
from app.schemas.market import Quality, Quote
from app.services import quote_hub as qh


@pytest.fixture
def clock(monkeypatch):
    state = {"now": datetime.fromisoformat("2026-10-13T10:00:00+08:00"),
             "days": [date(2026, 10, 12), date(2026, 10, 13)]}
    class SourceClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"].astimezone(tz) if tz is not None else state["now"].replace(tzinfo=None)
    monkeypatch.setattr(freshness, "datetime", SourceClock)
    monkeypatch.setattr(qh, "beijing_now", lambda: state["now"])
    monkeypatch.setattr(qh, "utcnow", lambda: state["now"].astimezone(timezone.utc))
    monkeypatch.setattr(validator, "utcnow", lambda: state["now"].astimezone(timezone.utc))
    monkeypatch.setattr(qh.tc, "known_trade_days", lambda: state["days"])
    monkeypatch.setattr(qh.tc, "in_trading_window", lambda *a, **k:
                        qh.tc.market_open_state(state["days"], state["now"]) == qh.tc.MARKET_OPEN)
    return state


def quote(symbol, stamp, market="SH", price=100):
    return Quote(symbol=symbol, market=market, price=price, prev_close=price, change_pct=0,
                 volume=1, amount=1, data_timestamp=stamp, received_at=stamp,
                 source="offline-retained-state")


def accept(hub, domain, stamp, bad=None):
    if domain == "indices":
        rows = [quote(s, stamp, m) for s, m in INDEX_MARKETS.items()]
        if bad is not None:
            rows[0] = bad
        hub._accept_indices(rows)
        return hub.indices["000001"]
    hub._accept_quotes(["600519"], [bad or quote("600519", stamp)])
    return hub.quotes["600519"]


def rejection(hub, domain):
    rows = hub.last_index_rejections if domain == "indices" else hub.last_quote_rejections
    return rows["000001" if domain == "indices" else "600519"]


@pytest.mark.parametrize("domain", ["indices", "quotes"])
@pytest.mark.parametrize("bad_kind", ["late", "invalid", "unknown_clock"])
def test_fresh_trusted_current_survives_rejected_packet_without_quality_flicker(clock, domain, bad_kind):
    hub = qh.QuoteHub(None, 1, stale_after=10)
    stamp = clock["now"].astimezone(timezone.utc) - timedelta(seconds=1)
    current = accept(hub, domain, stamp)
    before, history = current.model_dump(), list(hub.quote_history)
    bad = current.model_copy(deep=True)
    bad.price = 99
    if bad_kind == "late":
        bad.data_timestamp = stamp - timedelta(seconds=1)
    elif bad_kind == "invalid":
        bad.volume = -1
    else:
        bad.data_timestamp = None
    retained = accept(hub, domain, stamp, bad)
    assert retained is current and retained.model_dump() == before
    assert retained.quality == Quality.high and retained.freshness(fresh_within=10).state == "ready"
    assert list(hub.quote_history) == history
    assert rejection(hub, domain).startswith("source_")
    summary = hub.source_rejections()[domain]
    assert summary["count"] == 1 and summary["reasons"] == {rejection(hub, domain): 1}
    assert (hub.last_index_coverage if domain == "indices" else hub.last_batch_coverage) < 1


@pytest.mark.parametrize("domain", ["indices", "quotes"])
@pytest.mark.parametrize(("now", "tail"), [
    ("2026-10-13T12:00:00+08:00", "2026-10-13T11:29:55+08:00"),
    ("2026-10-13T16:00:00+08:00", "2026-10-13T14:59:55+08:00"),
    ("2026-10-13T08:00:00+08:00", "2026-10-12T14:59:55+08:00"),
])
def test_confirmed_pause_tail_keeps_phase_when_another_packet_is_rejected(clock, domain, now, tail):
    clock["now"] = datetime.fromisoformat(now)
    hub = qh.QuoteHub(None, 1, stale_after=10)
    stamp = datetime.fromisoformat(tail).astimezone(timezone.utc)
    current = accept(hub, domain, stamp)
    before = current.model_dump()
    assert current.quality == Quality.stale and current.quality_reasons == ["market_closed"]
    bad = current.model_copy(update={"data_timestamp": stamp - timedelta(seconds=20), "price": 99})
    retained = accept(hub, domain, stamp, bad)
    assert retained.model_dump() == before
    assert rejection(hub, domain) == "source_time_regress_ignored"
    visible = (hub.get_indices() if domain == "indices" else hub.get_quotes())[0]
    assert visible.quality_reasons == ["market_closed"] and visible.display_session.phase == "closed"


@pytest.mark.parametrize("domain", ["indices", "quotes"])
@pytest.mark.parametrize("untrusted", ["aged", "unknown_clock", "future_clock", "already_stale", "low",
    "unknown_calendar", "wrong_date", "before_tail"])
def test_rejected_packet_does_not_upgrade_an_untrusted_retained_quote(clock, domain, untrusted):
    hub = qh.QuoteHub(None, 1, stale_after=10)
    stamp = clock["now"].astimezone(timezone.utc) - timedelta(seconds=1)
    if untrusted in {"unknown_calendar", "wrong_date", "before_tail"}:
        clock["now"] = datetime.fromisoformat("2026-10-13T12:00:00+08:00")
        stamp = datetime.fromisoformat("2026-10-13T11:29:55+08:00").astimezone(timezone.utc)
    current = accept(hub, domain, stamp)
    if untrusted == "aged":
        clock["now"] += timedelta(seconds=10)
    elif untrusted == "unknown_clock":
        current.data_timestamp = None
    elif untrusted == "future_clock":
        current.data_timestamp = clock["now"].astimezone(timezone.utc) + timedelta(minutes=1)
    elif untrusted == "already_stale":
        current.quality, current.quality_reasons = Quality.stale, ["refresh_failed"]
    elif untrusted == "low":
        current.quality, current.quality_reasons = Quality.low, ["unset_high_low"]
    elif untrusted == "unknown_calendar":
        clock["days"] = [date(2026, 10, 12)]
    elif untrusted == "wrong_date":
        current.data_timestamp -= timedelta(days=1)
    else:
        current.data_timestamp -= timedelta(seconds=10)
    before = (current.price, current.data_timestamp, current.received_at)
    bad = quote(current.symbol, stamp, current.market, 99)
    bad.volume = -1
    retained = accept(hub, domain, stamp, bad)
    assert retained.quality == Quality.stale and retained.quality_reasons == ["source_invalid_ignored"]
    assert (retained.price, retained.data_timestamp, retained.received_at) == before


@pytest.mark.parametrize("domain", ["indices", "quotes"])
def test_repeated_rejections_cannot_extend_the_original_source_age_window(clock, domain):
    hub = qh.QuoteHub(None, 1, stale_after=10)
    stamp = clock["now"].astimezone(timezone.utc) - timedelta(seconds=1)
    current = accept(hub, domain, stamp)
    bad = current.model_copy(update={"data_timestamp": stamp - timedelta(seconds=1), "price": 99})
    for _ in range(5):
        clock["now"] += timedelta(seconds=1)
        accept(hub, domain, stamp, bad.model_copy(deep=True))
        assert current.quality == Quality.high and current.data_timestamp == stamp
    clock["now"] += timedelta(seconds=5)
    accept(hub, domain, stamp, bad.model_copy(deep=True))
    assert current.quality == Quality.stale and current.quality_reasons == ["source_time_regress_ignored"]
    assert current.data_timestamp == stamp and current.price == 100


@pytest.mark.parametrize("domain,symbol", [("indices", "sh000001"), ("quotes", "600519")])
@pytest.mark.parametrize("pause", [False, True])
@pytest.mark.parametrize("provider_degraded", [False, True])
def test_single_quote_rest_presentation_reuses_the_same_retained_state_contract(clock, domain, symbol, pause, provider_degraded):
    if pause:
        clock["now"] = datetime.fromisoformat("2026-10-13T12:00:00+08:00")
        stamp = datetime.fromisoformat("2026-10-13T11:29:55+08:00").astimezone(timezone.utc)
    else:
        stamp = clock["now"].astimezone(timezone.utc) - timedelta(seconds=1)
    hub = qh.QuoteHub(None, 1, stale_after=10)
    current = accept(hub, domain, stamp)
    before = current.model_dump()
    bad = current.model_copy(update={"price": 99, "data_timestamp": stamp - timedelta(seconds=1)})
    if provider_degraded:
        bad.quality, bad.quality_reasons = Quality.low, ["provider_degraded"]
    visible = hub.present_quote(bad, symbol)
    assert visible.symbol == symbol and visible.price == current.price and visible.data_timestamp == stamp
    assert visible.quality == current.quality and visible.quality_reasons == current.quality_reasons
    assert current.model_dump() == before
