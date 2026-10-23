"""Single-source reads share Hub identity, source age and calendar presentation."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes.market_quotes import quote as quote_route
from app.core import freshness
from app.data_quality import validator
from app.schemas.market import Quality, Quote
from app.services import quote_hub as qh


@pytest.fixture
def context(monkeypatch):
    state = {"now": datetime.fromisoformat("2026-10-21T12:00:00+08:00"),
             "days": [date(2026, 10, 20), date(2026, 10, 21)]}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"].astimezone(tz) if tz else state["now"].replace(tzinfo=None)

    monkeypatch.setattr(freshness, "datetime", Clock)
    monkeypatch.setattr(validator, "utcnow", lambda: state["now"].astimezone(timezone.utc))
    monkeypatch.setattr(qh, "beijing_now", lambda: state["now"])
    monkeypatch.setattr(qh.tc, "known_trade_days", lambda: state["days"])
    monkeypatch.setattr(qh.tc, "in_trading_window", lambda: qh.tc.market_open_state(state["days"], state["now"]) == qh.tc.MARKET_OPEN)
    return state


def observation(symbol="600519", market="SH", stamp="2026-10-21T11:30:00+08:00", **extra):
    return Quote(symbol=symbol, market=market, source="tencent", price=12.34,
                 prev_close=12.34, change_pct=0, volume=1, amount=1,
                 data_timestamp=datetime.fromisoformat(stamp) if stamp else None, **extra)


def make_hub(row):
    async def get_quote(symbol):
        return row.model_copy(deep=True)
    return qh.QuoteHub(SimpleNamespace(name="tencent", realtime=True, get_quote=get_quote), 1)


@pytest.mark.parametrize("source", [None, "tencent"])
def test_direct_stock_lunch_view_keeps_pause_and_source_clock_without_cache_write(context, source):
    original = observation()
    before = original.model_dump()
    hub = make_hub(original)
    result = asyncio.run(quote_route("600519", source, hub))["data"]
    assert result["quality"] == "stale" and result["quality_reasons"] == ["market_closed"]
    assert result["display_session"]["phase"] == "closed"
    assert result["display_session"]["expected_source_after"] == "2026-10-21T11:29:50+08:00"
    assert result["data_timestamp"] == original.model_dump(mode="json")["data_timestamp"]
    assert hub.quotes == hub.indices == {} and original.model_dump() == before


@pytest.mark.parametrize("source", [None, "tencent"])
def test_prefixed_index_has_correct_identity_and_auction_age_without_invalid_symbol(context, source):
    context["now"] = context["now"].replace(hour=9, minute=20)
    row = observation("sh000001", stamp="2026-10-20T15:00:00+08:00")
    result = asyncio.run(quote_route("sh000001", source, make_hub(row)))["data"]
    assert result["symbol"] == "sh000001" and result["market"] == "SH"
    assert result["quality"] == "stale" and result["quality_reasons"] == ["quote_age_exceeded"]
    assert result["display_session"]["phase"] == "auction"


@pytest.mark.parametrize("symbol,row", [
    ("sh000001", observation("000001", market="SZ")),
    ("000001", observation("000001", market="SH")),
    ("600519", observation("600036")),
    ("600519", observation(market=None)),
])
def test_direct_source_cannot_return_another_market_security_or_unknown_identity(context, symbol, row):
    with pytest.raises(HTTPException) as error:
        asyncio.run(quote_route(symbol, "tencent", make_hub(row)))
    assert error.value.status_code == 502


def test_bare_stock_collision_never_uses_the_cached_index(context):
    hub = make_hub(observation("000001", market="SZ"))
    hub.indices["000001"] = observation("000001").model_copy(update={"price": 3000})
    before = hub.indices["000001"].model_dump()
    result = asyncio.run(quote_route("000001", "tencent", hub))["data"]
    assert result["symbol"] == "000001" and result["market"] == "SZ" and result["price"] == 12.34
    assert hub.indices["000001"].model_dump() == before and hub.quotes == {}


@pytest.mark.parametrize("expired", [False, True])
def test_direct_older_source_retains_original_age_and_fields_without_mutating_cache(context, expired):
    now = context["now"].replace(hour=10, minute=0)
    context["now"] = now
    current_stamp = now - timedelta(seconds=20 if expired else 0)
    old = observation(stamp=(current_stamp - timedelta(seconds=1)).isoformat()).model_copy(update={"price": 11})
    hub = make_hub(old)
    current = observation(stamp=current_stamp.isoformat())
    hub.quotes["600519"] = current
    before = current.model_dump()
    result = asyncio.run(quote_route("600519", "tencent", hub))["data"]
    assert result["price"] == current.price and result["data_timestamp"] == current.model_dump(mode="json")["data_timestamp"]
    assert result["received_at"] == current.model_dump(mode="json")["received_at"]
    assert result["quality"] == ("stale" if expired else "high")
    assert result["quality_reasons"] == (["source_time_regress_ignored"] if expired else [])
    assert hub.last_quote_rejections == {} and not hub.quote_history
    assert current.model_dump() == before


@pytest.mark.parametrize("quality,reason", [(Quality.stale, "refresh_failed"), (Quality.invalid, "negative_volume")])
def test_direct_source_self_reported_failure_is_not_laundered_by_revalidation(context, quality, reason):
    row = observation(quality=quality, quality_reasons=[reason])
    result = asyncio.run(quote_route("600519", "tencent", make_hub(row)))["data"]
    assert result["quality"] == quality.value and reason in result["quality_reasons"]
    assert "market_closed" not in result["quality_reasons"]


def test_direct_calendar_unknown_and_missing_source_clock_stay_explicit(context):
    context["days"] = [date(2026, 10, 20)]
    result = asyncio.run(quote_route("600519", "tencent", make_hub(observation(stamp=None))))["data"]
    assert result["quality"] == "stale" and "source_time_unknown" in result["quality_reasons"]
    assert result["display_session"]["phase"] == "unknown"


def test_cached_route_keeps_the_actual_rejection_and_cache_during_pause(context):
    row = observation(quality=Quality.stale, quality_reasons=["source_time_regress_ignored"])
    hub = make_hub(row)
    hub.quotes[row.symbol] = row
    before = row.model_dump()
    result = asyncio.run(quote_route(row.symbol, None, hub))["data"]
    assert result["quality"] == "stale" and "source_time_regress_ignored" in result["quality_reasons"]
    assert "market_closed" not in result["quality_reasons"]
    assert result["display_session"]["phase"] == "closed"
    assert row.model_dump() == before
