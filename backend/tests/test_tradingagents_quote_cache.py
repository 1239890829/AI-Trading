"""Stored research snapshots consume real Hub cache quality, without collecting."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core import freshness
from app.data_quality import validator
from app.schemas.market import Quality, Quote
from app.services import quote_hub as qh
from app.services.tradingagents_driver import StoredCompanyPacketBuilder


class NoSourceReads:
    name = "tencent"

    def __init__(self):
        self.calls = 0

    def get_quotes(self, *args):
        self.calls += 1
        raise AssertionError("snapshot must not fetch provider quotes")

    def get_quote(self, *args):
        self.calls += 1
        raise AssertionError("snapshot must not fetch a provider quote")


@pytest.fixture
def cache(monkeypatch):
    now = datetime.fromisoformat("2026-10-26T10:00:00+08:00")

    class SourceClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(freshness, "datetime", SourceClock)
    monkeypatch.setattr(qh, "beijing_now", lambda: now)
    monkeypatch.setattr(qh, "utcnow", lambda: now.astimezone(timezone.utc))
    monkeypatch.setattr(validator, "utcnow", lambda: now.astimezone(timezone.utc))
    monkeypatch.setattr(qh.tc, "known_trade_days", lambda: [now.date()])
    monkeypatch.setattr(qh.tc, "in_trading_window", lambda: True)
    provider = NoSourceReads()
    return qh.QuoteHub(provider, 1, stale_after=10), provider, now


def observation(now, *, quality=Quality.high, age=1, price=11):
    return Quote(symbol="600000", market="SH", price=price, prev_close=10,
        open=10, high=12, low=9, volume=1000, amount=11000,
        source="tencent", quality=quality,
        quality_reasons=[] if quality is Quality.high else ["provider_contract_degraded"],
        data_timestamp=now - timedelta(seconds=age), received_at=now - timedelta(milliseconds=100))


@pytest.mark.parametrize("quality", [Quality.high, Quality.medium, Quality.low, Quality.stale, Quality.invalid])
def test_stored_snapshot_preserves_hub_quality_and_never_fetches_a_quote(cache, quality):
    hub, provider, now = cache
    row = observation(now, quality=quality)
    hub._accept_quotes(["600000"], [row])
    snapshot, audit = StoredCompanyPacketBuilder._snapshot(hub, "600000", now)

    if quality in {Quality.high, Quality.medium}:
        assert snapshot["data"]["quality"] == quality.value
        assert snapshot["data"]["quality_reasons"] == row.quality_reasons
        assert snapshot["data"]["data_timestamp"] == row.data_timestamp.isoformat()
        assert snapshot["data"]["received_at"] == row.received_at.isoformat()
        assert audit["status"] == "observed"
    else:
        assert snapshot is None and audit["status"] == "unavailable"
        if quality is Quality.invalid:
            assert not hub.quotes
        else:
            assert hub.quotes["600000"].quality is quality
    assert audit["attempted"] is False and provider.calls == 0


def test_invalid_provider_packet_cannot_replace_the_snapshots_existing_trusted_fact(cache):
    hub, provider, now = cache
    trusted = observation(now, age=2, price=10.5)
    hub._accept_quotes(["600000"], [trusted])
    before = trusted.model_dump()
    hub._accept_quotes(["600000"], [observation(now, quality=Quality.invalid)])

    snapshot, audit = StoredCompanyPacketBuilder._snapshot(hub, "600000", now)

    assert snapshot["data"]["price"] == 10.5
    assert snapshot["data"]["data_timestamp"] == trusted.data_timestamp.isoformat()
    assert snapshot["data"]["received_at"] == trusted.received_at.isoformat()
    assert trusted.model_dump() == before
    assert audit["status"] == "observed" and audit["attempted"] is False
    assert provider.calls == 0


def test_stored_snapshot_does_not_renew_an_expired_hub_source_clock(cache):
    hub, provider, now = cache
    row = observation(now, age=11)
    hub._accept_quotes(["600000"], [row])
    snapshot, audit = StoredCompanyPacketBuilder._snapshot(hub, "600000", now)
    assert snapshot is None and audit["status"] == "unavailable"
    assert hub.quotes["600000"].data_timestamp == row.data_timestamp
    assert audit["attempted"] is False and provider.calls == 0


@pytest.mark.parametrize("quality", [Quality.low, Quality.medium])
@pytest.mark.parametrize("has_reason", [True, False])
def test_expired_snapshot_projection_preserves_source_quality_evidence(cache, quality, has_reason):
    hub, provider, now = cache
    row = observation(now, quality=quality, age=11)
    row.quality_reasons = ["provider_contract_degraded"] if has_reason else []
    hub._accept_quotes(["600000"], [row])
    before = row.model_dump()
    visible = hub.get_quotes(["600000"])[0]
    expected = (["provider_contract_degraded"] if has_reason else []) + [
        f"source_quality_{quality.value}", "quote_age_exceeded",
    ]
    assert visible.quality is Quality.stale and visible.quality_reasons == expected
    assert visible.data_timestamp == row.data_timestamp and visible.received_at == row.received_at
    snapshot, audit = StoredCompanyPacketBuilder._snapshot(hub, "600000", now)
    assert snapshot is None and audit["status"] == "unavailable" and audit["attempted"] is False
    assert row.model_dump() == before and provider.calls == 0
