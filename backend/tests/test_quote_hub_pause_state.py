"""Closed source observations stay consistent before the next shared publication."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from app.data_quality import validator
from app.data_quality.validator import mark_stale
from app.market.indices import INDEX_MARKETS
from app.schemas.market import Quality, Quote
from app.services import quote_hub as qh


def observation(symbol, timestamp, price=100, market="SH"):
    return Quote(symbol=symbol, market=market, price=price, prev_close=price,
                 change_pct=0, volume=1, amount=1, data_timestamp=timestamp,
                 source="offline-pause-state")


@pytest.fixture
def context(monkeypatch):
    state = {"now": datetime.fromisoformat("2026-10-13T19:37:02+08:00"),
             "days": [date(2026, 10, 13)]}
    monkeypatch.setattr(qh, "beijing_now", lambda: state["now"])
    monkeypatch.setattr(qh.tc, "known_trade_days", lambda: state["days"])
    monkeypatch.setattr(validator, "utcnow", lambda: state["now"].astimezone(timezone.utc))
    monkeypatch.setattr(qh.tc, "in_trading_window", lambda *a, **k:
                        qh.tc.market_open_state(state["days"], state["now"]) == qh.tc.MARKET_OPEN)
    async def calendar(*args, **kwargs):
        return state["days"]
    monkeypatch.setattr(qh.tc, "trading_days", calendar)
    return state


def rows(timestamp):
    return [observation(s, timestamp, market=m) for s, m in INDEX_MARKETS.items()]


@pytest.mark.parametrize("domain", ["indices", "quotes"])
def test_accepted_closed_rows_have_reason_before_publication(context, domain):
    hub = qh.QuoteHub(None, 1)
    stamp = context["now"].replace(hour=16, minute=15).astimezone(timezone.utc)
    if domain == "indices":
        hub._accept_indices(rows(stamp))
        cached = hub.indices["000001"]
        visible = hub.get_indices()[0]
    else:
        hub._accept_quotes(["600519"], [observation("600519", stamp)])
        cached = hub.quotes["600519"]
        visible = hub.get_quotes(["600519"])[0]
    assert hub._closed_marked is False  # 真值来自当次阶段，尚未跑 publisher。
    assert hub._seq == 0
    assert cached.quality == visible.quality == Quality.stale
    assert cached.quality_reasons == visible.quality_reasons == ["market_closed"]
    assert cached.data_timestamp == visible.data_timestamp == stamp


@pytest.mark.parametrize("domain", ["indices", "quotes"])
def test_partial_acceptance_and_publication_preserve_real_source_rejection(context, domain):
    async def scenario():
        hub = qh.QuoteHub(None, 1)
        stamp = context["now"].replace(hour=16, minute=15).astimezone(timezone.utc)
        if domain == "indices":
            hub._accept_indices(rows(stamp))
            incoming = rows(stamp + timedelta(seconds=1))
            incoming[0].data_timestamp = stamp - timedelta(seconds=1)
            incoming[0].price = 91
            hub._accept_indices(incoming)
            retained, accepted = hub.indices["000001"], hub.indices["399001"]
        else:
            hub._accept_quotes(["600519", "600036"], [observation(s, stamp) for s in ("600519", "600036")])
            hub._accept_quotes(["600519", "600036"], [
                observation("600519", stamp - timedelta(seconds=1), 91),
                observation("600036", stamp + timedelta(seconds=1)),
            ])
            retained, accepted = hub.quotes["600519"], hub.quotes["600036"]
        assert retained.price == 100 and retained.data_timestamp == stamp
        assert retained.quality_reasons == ["source_time_regress_ignored"]
        assert accepted.quality_reasons == ["market_closed"]
        hub._pending_domains.add(domain)
        await hub._publish_pending()
        assert retained.quality_reasons == ["source_time_regress_ignored"]
        assert retained.data_timestamp == stamp
    asyncio.run(scenario())


def test_closed_publication_keeps_refresh_failure_and_unknown_source_clock(context):
    async def scenario():
        hub = qh.QuoteHub(None, 1)
        stamp = context["now"].replace(hour=16, minute=15).astimezone(timezone.utc)
        hub._accept_quotes(["600519", "600036"], [observation("600519", stamp), observation("600036", None)])
        failed, unknown = hub.quotes["600519"], hub.quotes["600036"]
        mark_stale(failed, "refresh_failed")
        assert unknown.data_timestamp is None
        assert unknown.quality == Quality.stale and unknown.quality_reasons == ["source_time_unknown"]
        unknown_quality, unknown_reasons = unknown.quality, list(unknown.quality_reasons)
        hub._pending_domains.add("quotes")
        await hub._publish_pending()
        assert failed.quality_reasons == ["refresh_failed"] and failed.data_timestamp == stamp
        assert unknown.quality == unknown_quality and unknown.quality_reasons == unknown_reasons
        assert unknown.data_timestamp is None
    asyncio.run(scenario())


@pytest.mark.parametrize("domain", ["indices", "quotes"])
def test_closed_partial_publication_keeps_missing_observation(context, domain):
    async def scenario():
        hub = qh.QuoteHub(None, 1)
        stamp = context["now"].replace(hour=16, minute=15).astimezone(timezone.utc)
        if domain == "indices":
            hub._accept_indices(rows(stamp))
            hub._accept_indices(rows(stamp + timedelta(seconds=1))[1:])
            retained, accepted = hub.indices["000001"], hub.indices["399001"]
            missing_reason = "index_batch_missing"
        else:
            hub._accept_quotes(["600519", "600036"], [observation(s, stamp) for s in ("600519", "600036")])
            hub._accept_quotes(["600519", "600036"], [observation("600036", stamp + timedelta(seconds=1))])
            retained, accepted = hub.quotes["600519"], hub.quotes["600036"]
            missing_reason = "batch_missing"
        assert retained.quality_reasons == [missing_reason]
        hub._pending_domains.add(domain)
        await hub._publish_pending()
        assert retained.price == 100 and retained.data_timestamp == stamp
        assert retained.quality_reasons == [missing_reason]
        assert accepted.quality_reasons == ["market_closed"]
    asyncio.run(scenario())


def test_closed_publication_keeps_real_low_quality(context):
    async def scenario():
        hub = qh.QuoteHub(None, 1)
        row = observation("600519", context["now"].astimezone(timezone.utc))
        row.high = row.low = 0
        validator.validate_quote(row, live=True)
        assert row.quality == Quality.low and row.quality_reasons == ["unset_high_low"]
        hub.quotes[row.symbol] = row
        hub._pending_domains.add("quotes")
        await hub._publish_pending()
        assert row.quality == Quality.low and row.quality_reasons == ["unset_high_low"]
    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["09:15:00", "12:55:00"])
@pytest.mark.parametrize("calendar_error", [False, True])
def test_calendar_wait_uses_completion_clock_at_wide_window_boundary(context, monkeypatch, boundary, calendar_error):
    async def scenario():
        opening = datetime.fromisoformat(f"2026-10-13T{boundary}+08:00")
        context["now"] = opening - timedelta(seconds=1)
        entered, release = asyncio.Event(), asyncio.Event()
        async def calendar(*args, **kwargs):
            entered.set()
            await release.wait()
            if calendar_error:
                raise RuntimeError("offline calendar unavailable")
            return context["days"]
        monkeypatch.setattr(qh.tc, "trading_days", calendar)
        hub = qh.QuoteHub(None, 1)
        hub._closed_marked = True
        queue = hub.subscribe({"600519"})
        hub._pending_domains.add("quotes")
        publish = asyncio.create_task(hub._publish_pending())
        try:
            await entered.wait()
            context["now"] = opening
            stamp = opening.astimezone(timezone.utc)
            hub._accept_quotes(["600519"], [observation("600519", stamp)])
            row = hub.quotes["600519"]
            assert row.quality == Quality.high
            release.set()
            await publish
            assert row.quality == Quality.high and "market_closed" not in row.quality_reasons
            assert row.data_timestamp == stamp
            assert hub._closed_marked is False and queue.qsize() == 1
        finally:
            publish.cancel()
            await asyncio.gather(publish, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("hour,minute", [(9, 30), (13, 0)])
def test_open_boundary_does_not_reuse_previous_closed_flag(context, hour, minute):
    async def scenario():
        context["now"] = context["now"].replace(hour=hour, minute=minute)
        hub = qh.QuoteHub(None, 1)
        hub._closed_marked = True
        stamp = context["now"].astimezone(timezone.utc)
        hub._accept_indices(rows(stamp))
        hub._accept_quotes(["600519"], [observation("600519", stamp)])
        assert all(q.quality == Quality.high and q.quality_reasons == []
                   for q in [*hub.indices.values(), *hub.quotes.values()])
        hub._pending_domains.update(("indices", "quotes"))
        await hub._publish_pending()
        assert hub._closed_marked is False
        assert hub.indices["000001"].data_timestamp == stamp
    asyncio.run(scenario())


@pytest.mark.parametrize("calendar_error", [False, True])
def test_missing_current_calendar_releases_publication_without_claiming_open(context, monkeypatch, calendar_error):
    async def scenario():
        context["now"] = datetime.fromisoformat("2026-10-14T09:30:00+08:00")
        # 昨日日历不覆盖今日；跨日旧 closed 不能充当今日阶段真值。
        async def unavailable(*args, **kwargs):
            if calendar_error:
                raise RuntimeError("offline calendar unavailable")
            return []
        monkeypatch.setattr(qh.tc, "trading_days", unavailable)
        hub = qh.QuoteHub(None, 1)
        hub._closed_marked = True
        queue = hub.subscribe({"600519"})
        stamp = context["now"].astimezone(timezone.utc)
        hub._accept_quotes(["600519"], [observation("600519", stamp)])
        cached = hub.quotes["600519"]
        assert cached.quality == Quality.high and "market_closed" not in cached.quality_reasons
        hub._pending_domains.add("quotes")
        await hub._publish_pending()
        assert queue.qsize() == 1 and hub._pending_domains == set()
        frame = queue.get_nowait()
        assert hub._closed_marked is False
        assert frame["data"][0]["display_session"]["phase"] == "unknown"
        assert frame["data"][0]["data_timestamp"] == cached.model_dump(mode="json")["data_timestamp"]
        assert "market_closed" not in frame["data"][0]["quality_reasons"]
    asyncio.run(scenario())


@pytest.mark.parametrize("calendar_error", [False, True])
def test_empty_or_error_calendar_still_publishes_confirmed_offhours(context, monkeypatch, calendar_error):
    async def scenario():
        async def unavailable(*args, **kwargs):
            if calendar_error:
                raise RuntimeError("offline calendar unavailable")
            return []
        monkeypatch.setattr(qh.tc, "trading_days", unavailable)
        hub = qh.QuoteHub(None, 1)
        hub._closed_marked = True
        queue = hub.subscribe({"600519"})
        stamp = context["now"].replace(hour=16, minute=15).astimezone(timezone.utc)
        hub._accept_quotes(["600519"], [observation("600519", stamp)])
        hub._pending_domains.add("quotes")
        await hub._publish_pending()
        assert queue.qsize() == 1 and hub._closed_marked is True
        frame = queue.get_nowait()
        assert frame["type"] == "stale"
        assert frame["data"][0]["quality_reasons"] == ["market_closed"]
        assert frame["data"][0]["data_timestamp"] == hub.quotes["600519"].model_dump(mode="json")["data_timestamp"]
        assert hub._pending_domains == set()
    asyncio.run(scenario())


@pytest.mark.parametrize(("now", "days", "expected"), [
    ("2026-10-14T19:35:00+08:00", ["2026-10-13", "2026-10-14"], "2026-10-14"),
    ("2026-10-14T12:00:00+08:00", ["2026-10-13", "2026-10-14"], "2026-10-14"),
    ("2026-10-14T08:00:00+08:00", ["2026-10-13", "2026-10-14"], "2026-10-13"),
    ("2026-10-17T12:00:00+08:00", ["2026-10-15", "2026-10-16"], "2026-10-16"),
    ("2026-10-18T12:00:00+08:00", ["2026-10-15", "2026-10-16"], "2026-10-16"),
    ("2026-10-01T12:00:00+08:00", ["2026-09-30", "2026-10-09"], "2026-09-30"),
    ("2026-10-14T19:35:00+08:00", ["2026-10-13"], None),
    ("2026-10-17T12:00:00+08:00", ["2026-10-14"], None),
    ("2026-10-14T08:00:00+08:00", ["2026-10-14"], None),
])
def test_display_session_has_calendar_backed_expected_source_date(context, now, days, expected):
    context["now"] = datetime.fromisoformat(now)
    context["days"] = [date.fromisoformat(day) for day in days]
    hub = qh.QuoteHub(None, 1)
    session = hub._display_session()
    assert session.model_dump(mode="json")["expected_source_date"] == expected
    assert hub.last_attempt is None and hub.last_success_refresh is None
    assert not hub.quotes and not hub.indices


@pytest.mark.parametrize(("now", "expected"), [
    ("2026-10-14T19:35:00+08:00", "2026-10-14T14:59:50+08:00"),
    ("2026-10-14T12:00:00+08:00", "2026-10-14T11:29:50+08:00"),
    ("2026-10-14T08:00:00+08:00", "2026-10-13T14:59:50+08:00"),
    ("2026-10-17T12:00:00+08:00", "2026-10-16T14:59:50+08:00"),
    ("2026-10-14T10:00:00+08:00", None),
    ("2026-10-14T09:20:00+08:00", None),
])
def test_pause_source_tail_uses_original_age_tolerance(context, now, expected):
    context["now"] = datetime.fromisoformat(now)
    context["days"] = [date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)]
    hub = qh.QuoteHub(None, 1, stale_after=10)
    assert hub._display_session().model_dump(mode="json")["expected_source_after"] == expected
