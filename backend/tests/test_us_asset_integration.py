"""US reads remain ordinary, isolated and usable when optional night reads fail."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.assets.service import AssetReadService
from app.schemas.alpaca_data import AlpacaIndicativeQuote, AlpacaOvernightResult
from app.schemas.asset_evidence import AssetEvidence
from app.schemas.us_assets import UsDailySection, UsInstrument, UsRegularQuote, UsSnapshot


def snapshot():
    now = datetime(2026, 10, 30, 5, 0, tzinfo=timezone.utc)
    return UsSnapshot(instrument=UsInstrument(instrument_id="us_equity:US:AAPL.OQ:USD", symbol="AAPL",
        source_symbol="AAPL.OQ", name="苹果", exchange_source_code="OQ", security_type="stock"),
        regular=UsRegularQuote(price=348.91, previous_close=343.83, source_time=now,
            received_at=now, observation="session_unverified"),
        daily=UsDailySection(state="unavailable", source_symbol="AAPL.OQ", reason="isolated history missing"),
        clock_phase="overnight", received_at=now, missing=["overnight_source", "minute"])


def test_us_source_does_not_depend_on_ths_and_missing_night_credentials_preserve_quote():
    async def run():
        service = AssetReadService(enabled=False, us_enabled=True)
        async def read(code, limit):
            assert (code, limit) == ("AAPL.OQ", 120)
            return snapshot()
        service.us.snapshot = read
        try:
            assert [item.state for item in service.capabilities().items] == ["blocked", "available"]
            result = await service.us_snapshot("AAPL.OQ")
            assert result.regular.price == 348.91 and result.daily.state == "unavailable"
            assert result.overnight_data.state == "missing_credentials"
            assert result.overnight_data.quote is None and "overnight_source" in result.missing
            assert service.overnight._client is None
        finally:
            await service.aclose()
    asyncio.run(run())


def test_optional_night_failure_and_verified_result_do_not_replace_regular_price():
    async def run():
        service = AssetReadService(us_enabled=True)
        async def read(code, limit):
            return snapshot()
        service.us.snapshot = read
        state = "authorization_unverified"
        async def latest(instrument):
            assert instrument.instrument_id == snapshot().instrument.instrument_id
            return AlpacaOvernightResult(state=state, reason="isolated permission result",
                checked_at=snapshot().received_at, instrument_id=instrument.instrument_id,
                symbol=instrument.symbol, authorization_verified=state == "available", quote=AlpacaIndicativeQuote(bid_price=349, ask_price=350, bid_size_shares=10, ask_size_shares=20, bid_exchange="V", ask_exchange="V", conditions=[], source_time=snapshot().received_at, source_timestamp=snapshot().received_at.isoformat(), received_at=snapshot().received_at) if state == "available" else None)
        service.overnight.latest = latest
        try:
            first = await service.us_snapshot("AAPL.OQ")
            assert first.overnight_data.state == "authorization_unverified"
            assert "overnight_source" in first.missing and first.regular.price == 348.91
            state = "available"
            second = await service.us_snapshot("AAPL.OQ")
            assert "overnight_source" not in second.missing and "minute" in second.missing
            assert "overnight_last_trade" in second.missing
            assert second.regular.price == first.regular.price
            assert first.overnight_data.state == "authorization_unverified"
        finally:
            await service.aclose()
    asyncio.run(run())


def test_all_new_owners_close_even_when_an_optional_owner_fails():
    async def run():
        service = AssetReadService()
        closed = []
        async def fail():
            raise RuntimeError("isolated source failure")
        async def close_us():
            closed.append("us")
        async def close_night():
            closed.append("overnight")
        async def close_http():
            closed.append("http")
        service.public.aclose = fail
        service.us.aclose = close_us
        service.overnight.aclose = close_night
        service._client = SimpleNamespace(aclose=close_http)
        try:
            await service.aclose()
        except RuntimeError as exc:
            assert str(exc) == "isolated source failure"
        else:
            raise AssertionError("source failure must remain visible")
        assert closed == ["us", "overnight", "http"]
    asyncio.run(run())


def test_public_and_night_evidence_share_isolated_content_dedup_writer(tmp_path):
    async def run():
        root = tmp_path / "public-evidence"
        service = AssetReadService(us_enabled=True, public_enabled=True, evidence_dir=root)
        try:
            import hashlib
            body = b'{"small":"public fixture"}'
            digest = hashlib.sha256(body).hexdigest()
            for receipt in ("a" * 64, "b" * 64):
                item = AssetEvidence(endpoint="https://data.alpaca.markets/v2/stocks/quotes/latest",
                    params={"symbols": "AAPL", "feed": "overnight", "currency": "USD"},
                    received_at=snapshot().received_at, payload_sha256=digest, receipt_id=receipt)
                service.overnight._writer(item, body)
            assert len(list(root.glob("*.body.json"))) == 1
            assert len(list(root.glob("*.receipt.json"))) == 2
            assert service.public._writer is not None
        finally:
            await service.aclose()
    asyncio.run(run())


def test_optional_night_wait_rechecks_regular_age_without_changing_source_clock(monkeypatch):
    from app.assets import service as module
    async def run():
        service = AssetReadService(us_enabled=True)
        now = {"at": datetime(2026, 10, 29, 14, 0, 50, tzinfo=timezone.utc)}
        monkeypatch.setattr(module, "_utcnow", lambda: now["at"])
        value = snapshot()
        value.regular.source_time = datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc)
        value.regular.received_at = now["at"]
        value.regular.observation = "regular_quote"
        async def read(code, limit):
            return value.model_copy(deep=True)
        async def latest(instrument):
            now["at"] = datetime(2026, 10, 29, 14, 1, 4, tzinfo=timezone.utc)
            return AlpacaOvernightResult(state="unavailable", reason="outside_overnight_request_window", checked_at=now["at"])
        service.us.snapshot = read
        service.overnight.latest = latest
        try:
            result = await service.us_snapshot("AAPL.OQ")
            assert result.regular.observation == "stale"
            assert result.regular.source_time == value.regular.source_time
            assert result.regular.received_at == value.regular.received_at
            assert value.regular.observation == "regular_quote"
        finally:
            await service.aclose()
    asyncio.run(run())


def test_parent_enrichment_does_not_mutate_coalesced_source_snapshot():
    async def run():
        service = AssetReadService(us_enabled=True)
        shared = snapshot()
        async def read(code, limit):
            return shared
        service.us.snapshot = read
        try:
            first, second = await asyncio.gather(service.us_snapshot("AAPL.OQ"), service.us_snapshot("AAPL.OQ"))
            assert first is not second and first is not shared
            assert shared.overnight_data is None
            assert first.overnight_data.state == second.overnight_data.state == "missing_credentials"
            first.regular.price = 1
            assert second.regular.price == shared.regular.price == 348.91
        finally:
            await service.aclose()
    asyncio.run(run())
