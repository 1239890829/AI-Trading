"""Rejected packets do not replace the retained quote's own clock or phase."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

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
@pytest.mark.parametrize("quality", [Quality.medium, Quality.low, Quality.stale])
def test_batch_preserves_provider_quality_without_replacing_source_clocks(clock, domain, quality):
    hub = qh.QuoteHub(None, 1, stale_after=10)
    now = clock["now"].astimezone(timezone.utc)
    current = accept(hub, domain, now - timedelta(seconds=2))
    observed = quote(current.symbol, now - timedelta(seconds=1), current.market, 101)
    observed.received_at = now
    observed.quality = quality
    observed.quality_reasons = ["provider_contract_degraded"]
    before = (observed.price, observed.data_timestamp, observed.received_at)

    accepted = accept(hub, domain, now, observed)

    assert accepted.quality == quality
    assert accepted.quality_reasons == ["provider_contract_degraded"]
    assert (accepted.price, accepted.data_timestamp, accepted.received_at) == before
    assert accepted.freshness(fresh_within=10).state == ("stale" if quality is Quality.stale else "degraded")
    assert hub.source_rejections()[domain] == {"count": 0, "reasons": {}}
    state = hub._domains[domain]
    state.requested_symbols = (current.symbol,)
    observed_domain = hub.refresh_domains()[domain]
    assert observed_domain["source_stale_count"] == int(quality is Quality.stale)
    assert observed_domain["source_degraded_count"] == int(quality is not Quality.stale)


@pytest.mark.parametrize("domain", ["indices", "quotes"])
@pytest.mark.parametrize("has_current", [False, True])
def test_provider_invalid_never_enters_current_or_replaces_a_trusted_fact(clock, domain, has_current):
    hub = qh.QuoteHub(None, 1, stale_after=10)
    now = clock["now"].astimezone(timezone.utc)
    current = accept(hub, domain, now - timedelta(seconds=2)) if has_current else None
    before = current.model_dump() if current is not None else None
    symbol = "000001" if domain == "indices" else "600519"
    observed = quote(symbol, now - timedelta(seconds=1), price=101)
    observed.quality, observed.quality_reasons = Quality.invalid, ["provider_identity_untrusted"]
    if domain == "indices":
        hub._accept_indices([observed])
    else:
        hub._accept_quotes([symbol], [observed])

    cached = (hub.indices if domain == "indices" else hub.quotes).get(symbol)
    assert cached is current
    if current is not None:
        assert current.model_dump() == before
    assert observed.quality is Quality.invalid
    assert observed.quality_reasons == ["provider_identity_untrusted"]
    assert rejection(hub, domain) == "source_invalid_ignored"


@pytest.mark.parametrize("domain", ["indices", "quotes"])
def test_same_second_trusted_update_still_advances_values(clock, domain):
    hub = qh.QuoteHub(None, 1, stale_after=10)
    stamp = clock["now"].astimezone(timezone.utc) - timedelta(seconds=1)
    current = accept(hub, domain, stamp)
    observed = quote(current.symbol, stamp, current.market, 101)
    accepted = accept(hub, domain, stamp, observed)
    assert accepted.price == 101 and accepted.data_timestamp == stamp
    assert accepted.quality is Quality.high
    assert hub.source_rejections()[domain] == {"count": 0, "reasons": {}}
    assert hub.source_rejection_observations()["events"] == []


@pytest.mark.parametrize("domain", ["indices", "quotes"])
@pytest.mark.parametrize("unknown_clock", [False, True])
def test_rejection_observation_keeps_minimal_original_clocks(clock, domain, unknown_clock):
    hub = qh.QuoteHub(None, 1, stale_after=10)
    now = clock["now"].astimezone(timezone.utc)
    current = accept(hub, domain, now - timedelta(seconds=1))
    observed = quote(current.symbol, now - timedelta(seconds=3), current.market, 99)
    observed.received_at = now - timedelta(milliseconds=200)
    if unknown_clock:
        observed.data_timestamp = None
    accept(hub, domain, now, observed)

    assert hub.source_rejection_observations() == {
        "scope": "hub_admission", "limit": 64, "events": [{
            "domain": domain, "identity": {"symbol": current.symbol, "market": current.market},
            "source": observed.source,
            "observed_source_at": observed.data_timestamp.isoformat() if observed.data_timestamp else None,
            "current_source_at": current.data_timestamp.isoformat(),
            "received_at": observed.received_at.isoformat(),
            "reason": "source_time_unknown_ignored" if unknown_clock else "source_time_regress_ignored",
            "observed_at": now.isoformat(),
        }],
    }


def test_rejection_observations_are_bounded_detached_and_survive_clean_admission(clock):
    hub = qh.QuoteHub(None, 1, stale_after=10)
    stamp = clock["now"].astimezone(timezone.utc) - timedelta(seconds=1)
    current = accept(hub, "quotes", stamp)
    for _ in range(66):
        clock["now"] += timedelta(seconds=1)
        observed = quote(current.symbol, stamp - timedelta(seconds=1), price=99)
        observed.received_at = clock["now"].astimezone(timezone.utc)
        accept(hub, "quotes", stamp, observed)
    history = hub.source_rejection_observations()
    assert len(history["events"]) == history["limit"] == 64
    assert history["events"][0]["observed_at"] > history["events"][-1]["observed_at"]
    assert history["events"][0]["current_source_at"] == stamp.isoformat()
    detached = hub.source_rejection_observations()
    detached["events"][0]["identity"]["symbol"] = "altered"
    detached["events"].clear()
    assert hub.source_rejection_observations() == history

    accept(hub, "quotes", clock["now"].astimezone(timezone.utc))
    assert hub.source_rejections()["quotes"]["count"] == 0
    assert hub.source_rejection_observations() == history


def test_rejection_details_only_reach_the_existing_maintenance_provider_endpoint(clock):
    from app.api.routes.health import health, system_providers
    from app.api.routes.market_envelope import meta_payload

    hub = qh.QuoteHub(SimpleNamespace(name="offline-provider", realtime=True), 1)
    stamp = clock["now"].astimezone(timezone.utc) - timedelta(seconds=1)
    current = accept(hub, "quotes", stamp)
    accept(hub, "quotes", stamp, quote(current.symbol, stamp - timedelta(seconds=1)))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    expected = hub.source_rejection_observations()
    assert asyncio.run(system_providers(request, hub))["quote_source_rejections"] == expected
    assert "quote_source_rejections" not in asyncio.run(health(request, hub))
    assert "events" not in hub.source_rejections()
    assert "quote_source_rejections" not in meta_payload(hub)


@pytest.mark.parametrize("domain", ["indices", "quotes"])
@pytest.mark.parametrize("quality", [Quality.low, Quality.stale, Quality.invalid])
def test_provider_verdict_reaches_health_and_a_clean_packet_recovers(clock, domain, quality):
    from app.api.routes.health import health

    hub = qh.QuoteHub(SimpleNamespace(name="offline-provider"), 1, stale_after=10)
    now = clock["now"].astimezone(timezone.utc)
    current = accept(hub, domain, now - timedelta(seconds=2))
    hub.last_success_refresh = now
    for state in hub._domains.values():
        state.last_success = now
    state = hub._domains[domain]
    state.requested, state.requested_symbols = True, (current.symbol,)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    assert asyncio.run(health(request, hub))["status"] == "ok"
    observed = quote(current.symbol, now - timedelta(seconds=1), current.market, 101)
    observed.quality, observed.quality_reasons = quality, ["provider_contract_degraded"]
    accept(hub, domain, now, observed)

    status = asyncio.run(health(request, hub))
    assert status["status"] == "degraded"
    diagnostics = status["refresh_domains"][domain]
    assert diagnostics["freshness"]["state"] == "ready"
    assert diagnostics["source_stale_count"] == int(quality is Quality.stale)
    assert diagnostics["source_degraded_count"] == int(quality is Quality.low)
    assert diagnostics["rejected_count"] == int(quality is Quality.invalid)

    accept(hub, domain, now)
    assert asyncio.run(health(request, hub))["status"] == "ok"


@pytest.mark.parametrize("domain", ["indices", "quotes"])
@pytest.mark.parametrize("quality", [Quality.low, Quality.medium])
@pytest.mark.parametrize("has_reason", [True, False])
def test_closed_tail_http_and_ws_copies_keep_aged_source_quality(clock, monkeypatch, domain, quality, has_reason):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.deps import get_hub
    from app.api.routes.market_quotes import router as market_router
    from app.core.config import settings
    from app.websocket.routes import router as ws_router

    clock["now"] = datetime.fromisoformat("2026-10-13T14:59:56+08:00")
    hub = qh.QuoteHub(SimpleNamespace(name="offline-provider", realtime=True), 1, stale_after=10)
    symbol = "000001" if domain == "indices" else "600519"
    requested = "sh000001" if domain == "indices" else symbol
    stamp = clock["now"].astimezone(timezone.utc) - timedelta(seconds=1)
    row = quote(symbol, stamp)
    row.quality = quality
    row.quality_reasons = ["provider_contract_degraded"] if has_reason else []
    current = accept(hub, domain, stamp, row)
    before = current.model_dump()
    clock["now"] = datetime.fromisoformat("2026-10-13T15:00:20+08:00")
    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "api_token", "")
    app = FastAPI()
    app.state.hub = hub
    app.include_router(market_router, prefix="/api")
    app.include_router(ws_router)
    app.dependency_overrides[get_hub] = lambda: hub

    with TestClient(app) as client:
        response = client.get("/api/quotes", params={"symbols": requested})
        assert response.status_code == 200
        http_row = response.json()["data"][0]
        with client.websocket_connect(f"/ws/quotes?symbols={requested}") as socket:
            ws_row = socket.receive_json()["data"][0]
    expected = (["provider_contract_degraded"] if has_reason else []) + [
        f"source_quality_{quality.value}", "quote_age_exceeded",
    ]
    for visible in (http_row, ws_row):
        assert visible["quality"] == "stale" and visible["quality_reasons"] == expected
        assert visible["display_session"]["phase"] == "closed"
        assert visible["display_session"]["expected_source_after"] == "2026-10-13T14:59:50+08:00"
        assert visible["data_timestamp"] == current.model_dump(mode="json")["data_timestamp"]
        assert visible["received_at"] == current.model_dump(mode="json")["received_at"]
    assert current.model_dump() == before and not hub._subscribers


@pytest.mark.parametrize("domain", ["indices", "quotes"])
def test_high_quality_closed_tail_age_copy_keeps_its_existing_contract(clock, domain):
    clock["now"] = datetime.fromisoformat("2026-10-13T14:59:56+08:00")
    hub = qh.QuoteHub(None, 1, stale_after=10)
    stamp = clock["now"].astimezone(timezone.utc) - timedelta(seconds=1)
    current = accept(hub, domain, stamp)
    clock["now"] = datetime.fromisoformat("2026-10-13T15:00:20+08:00")
    visible = hub.get_indices()[0] if domain == "indices" else hub.get_quotes()[0]
    assert visible.quality is Quality.stale and visible.quality_reasons == ["quote_age_exceeded"]
    assert current.quality is Quality.high and current.quality_reasons == []


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
