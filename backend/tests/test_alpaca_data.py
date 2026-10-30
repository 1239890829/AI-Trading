"""Offline source-contract tests. They do not prove an account's API entitlement.

The official latest-quotes example establishes the envelope and quote fields.
Overnight tape/time and failures below are explicit synthetic counterexamples.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading

import httpx
import pytest

from app.assets import alpaca_data as alpaca
from app.schemas.us_assets import UsInstrument

NOW = datetime(2026, 10, 30, 6, 20, tzinfo=timezone.utc)


def instrument(symbol="AAPL", venue="OQ"):
    return UsInstrument(instrument_id=f"us_equity:US:{symbol}.{venue}:USD", symbol=symbol,
                        source_symbol=f"{symbol}.{venue}", name="来源名称", english_name="Source Name",
                        exchange_source_code=venue, security_type="stock")


def source_body(symbol="AAPL", **changes):
    quote = {"ap": 172.7, "as": 100, "ax": "Q", "bp": 172.62, "bs": 200,
             "bx": "Q", "c": ["R"], "t": "2026-10-30T06:19:59.286587431Z", "z": "N"}
    quote.update(changes)
    return json.dumps({"quotes": {symbol: quote}}, allow_nan=True).encode()


def service(handler, **options):
    return alpaca.AlpacaDataService(enabled=True, api_key="test-public-contract-key",
        secret_key="test-public-contract-secret", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **options)


@pytest.fixture(autouse=True)
def source_clock(monkeypatch):
    monkeypatch.setattr(alpaca, "_now", lambda: NOW)


def test_only_verified_identity_fixed_data_origin_and_indicative_bid_ask():
    async def run():
        calls, writes = [], []
        async def source(request):
            calls.append(request)
            assert request.method == "GET"
            assert request.url.scheme == "https" and request.url.host == "data.alpaca.markets"
            assert request.url.path == "/v2/stocks/quotes/latest"
            assert dict(request.url.params) == {"symbols": "AAPL", "feed": "overnight", "currency": "USD"}
            assert request.headers["APCA-API-KEY-ID"] == "test-public-contract-key"
            assert request.headers["APCA-API-SECRET-KEY"] == "test-public-contract-secret"
            return httpx.Response(200, content=source_body())
        owner = service(source, evidence_writer=lambda evidence, raw: writes.append((evidence.model_copy(), raw)))
        try:
            result = await owner.latest(instrument())
            assert result.state == "available" and result.authorization_verified is True
            assert result.calendar_status == "unverified"
            assert result.quote.bid_price == 172.62 and result.quote.ask_price == 172.7
            assert result.quote.bid_size_shares == 200 and result.quote.ask_size_shares == 100
            assert result.quote.price_kind == "indicative_bid_ask" and result.quote.last_trade_available is False
            assert result.quote.source_timestamp == "2026-10-30T06:19:59.286587431Z"
            assert result.quote.source_time.isoformat() == "2026-10-30T06:19:59.286587+00:00"
            assert result.quote.received_at == NOW and result.quote.currency == "USD"
            assert not {"price", "last", "mid", "last_trade"} & result.quote.model_dump().keys()
            assert len(calls) == len(writes) == len(result.evidence) == 1
            receipt = result.evidence[0]
            assert receipt.payload_sha256 == hashlib.sha256(writes[0][1]).hexdigest()
            assert receipt.raw_available is True
            assert receipt.params == {"symbols": "AAPL", "feed": "overnight", "currency": "USD"}
            assert "test-public-contract" not in result.model_dump_json()
        finally:
            await owner.aclose()
    asyncio.run(run())


def test_disabled_missing_key_or_identity_never_allocate_client_or_send():
    async def run():
        owners = [alpaca.AlpacaDataService(), alpaca.AlpacaDataService(enabled=True),
                  alpaca.AlpacaDataService(enabled=True, api_key="only-key")]
        try:
            assert (await owners[0].latest(instrument())).state == "disabled"
            for owner in owners[1:]:
                assert (await owner.latest(instrument())).state == "missing_credentials"
                assert (await owner.latest("AAPL")).reason == "verified_us_identity_required"
            assert all(owner._client is None for owner in owners)
        finally:
            for owner in owners:
                await owner.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("changes", [
    {"symbol": "https://evil.invalid/path"}, {"symbol": "AAPL,TSLA"},
    {"source_symbol": "AAPL.HK", "exchange_source_code": "HK"},
    {"instrument_id": "us_equity:US:NVDA.OQ:USD"}, {"currency": "CNY"},
    {"market": "CN"}, {"source": "unverified"}, {"symbol": "NVDA"},
])
def test_tampered_us_identity_does_not_reach_source(changes):
    async def run():
        calls = []
        owner = service(lambda request: calls.append(request))
        try:
            result = await owner.latest(instrument().model_copy(update=changes))
            assert result.state == "unavailable" and result.reason == "verified_us_identity_required"
            assert result.instrument_id is None and calls == []
        finally:
            await owner.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("at,eligible", [
    ("2026-10-30T07:59:59+00:00", True), ("2026-10-30T08:00:00+00:00", False),
    ("2026-10-31T00:00:00+00:00", False), ("2026-11-01T08:00:00+00:00", False),
    ("2026-11-02T01:00:00+00:00", True), ("2026-11-02T08:59:59+00:00", True),
    ("2026-11-02T09:00:00+00:00", False),
])
def test_request_window_honors_ny_dst_and_weekend_without_claiming_calendar(at, eligible):
    assert alpaca.overnight_window(datetime.fromisoformat(at)) is eligible


def test_outside_window_is_explicit_without_network_or_assumed_open(monkeypatch):
    monkeypatch.setattr(alpaca, "_now", lambda: datetime(2026, 10, 30, 15, tzinfo=timezone.utc))
    async def run():
        calls = []
        owner = service(lambda request: calls.append(request))
        try:
            result = await owner.latest(instrument())
            assert result.reason == "outside_overnight_request_window" and result.state == "unavailable"
            assert result.authorization_verified is False and result.calendar_status == "unverified"
            assert calls == []
        finally:
            await owner.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("changes", [
    {"ap": float("nan")}, {"bp": float("inf")}, {"bp": True}, {"bp": "172"},
    {"bp": -1}, {"bp": 173}, {"as": -1}, {"bs": True}, {"as": 4_294_967_296},
    {"z": "C"}, {"t": "2026-10-30T06:20:01Z"}, {"t": "2026-10-30T16:00:00Z"},
    {"t": "2026-10-30T06:19:59"}, {"t": "not a timestamp"}, {"c": "R"},
    {"c": ["test-public-contract-secret"]}, {"ax": "bad-exchange"},
])
def test_quote_rejects_nonfinite_future_clock_wrong_tape_and_bad_fields(changes):
    with pytest.raises((TypeError, ValueError)):
        alpaca.parse_quote(source_body(**changes), "AAPL", NOW)


@pytest.mark.parametrize("body", [
    b'{"quotes":{"NVDA":{}}}', b'{"quotes":{"AAPL":{},"NVDA":{}}}',
    b'{"quotes":{"AAPL":null}}', b'{"quotes":{}}', b'{"quotes":{},"quotes":{}}',
    source_body().replace(b'"quotes":', b'"headers":{},"quotes":'),
    source_body().replace(b'"quotes":', b'"currency":"CNY","quotes":'),
])
def test_wrong_symbol_duplicate_envelope_and_wrong_currency_fail(body):
    with pytest.raises((TypeError, ValueError)):
        alpaca.parse_quote(body, "AAPL", NOW)


def test_old_and_one_sided_quotes_keep_facts_without_fresh_or_trade_claim():
    async def run():
        async def source(request):
            return httpx.Response(200, content=source_body(t="2026-10-30T06:00:00Z"))
        owner = service(source)
        try:
            result = await owner.latest(instrument())
            assert result.state == "stale" and result.source_age_seconds == 1200
            assert result.quote.bid_price == 172.62 and result.authorization_verified
        finally:
            await owner.aclose()
        owner = service(lambda request: httpx.Response(200, content=source_body(bp=0, bs=0)))
        try:
            result = await owner.latest(instrument())
            assert result.state == "unavailable" and result.reason == "no_active_two_sided_indicative_quote"
            assert result.quote.bid_price is None and result.quote.ask_price == 172.7
        finally:
            await owner.aclose()
    asyncio.run(run())


def test_optional_tape_absence_is_preserved_and_not_fabricated_as_overnight():
    async def run():
        payload = json.loads(source_body())
        del payload["quotes"]["AAPL"]["z"]
        owner = service(lambda request: httpx.Response(200, json=payload))
        try:
            result = await owner.latest(instrument())
            assert result.state == "available" and result.quote.tape is None
            assert result.missing == ["source_tape"]
            assert result.feed == "overnight" and result.quote.price_kind == "indicative_bid_ask"
        finally:
            await owner.aclose()
    asyncio.run(run())


def test_pre_share_unit_quote_is_not_mislabeled_as_current_shares():
    with pytest.raises(ValueError, match="size unit"):
        alpaca.parse_quote(source_body(t="2025-10-30T06:19:59Z"), "AAPL", NOW)


def test_freshness_boundary_is_not_extended_and_no_zero_side_becomes_last_trade(monkeypatch):
    async def run():
        owner = service(lambda request: httpx.Response(200, content=source_body(t="2026-10-30T06:19:00Z")))
        try:
            exact = await owner.latest(instrument())
            assert exact.state == "available" and exact.source_age_seconds == 60
            monkeypatch.setattr(alpaca, "_now", lambda: NOW + timedelta(microseconds=1))
            assert (await owner.latest(instrument())).state == "stale"
            monkeypatch.setattr(alpaca, "_now", lambda: NOW - timedelta(minutes=2))
            assert (await owner.latest(instrument())).reason == "source_quote_clock_ahead"
        finally:
            await owner.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("status", [401, 403])
def test_authorization_denial_is_terminal_without_retry_other_feed_or_evidence(status):
    async def run():
        calls, writes = [], []
        async def source(request):
            calls.append(request)
            return httpx.Response(status, text="test-public-contract-secret")
        owner = service(source, evidence_writer=lambda *args: writes.append(args))
        try:
            result = await owner.latest(instrument())
            again = await owner.latest(instrument("NVDA"))
            assert result.state == again.state == "authorization_unverified"
            assert result.reason == again.reason == f"alpaca_data_http_{status}"
            assert len(calls) == 1 and writes == []
            assert "test-public-contract" not in result.model_dump_json()
        finally:
            await owner.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("status", [302, 429, 500])
def test_no_redirect_or_retry_and_error_body_never_persisted(status):
    async def run():
        calls, writes = [], []
        async def source(request):
            calls.append(request)
            return httpx.Response(status, headers={"Location": "https://evil.invalid/collect"},
                                  text="test-public-contract-secret")
        client = httpx.AsyncClient(transport=httpx.MockTransport(source), follow_redirects=True)
        owner = alpaca.AlpacaDataService(enabled=True, api_key="test-public-contract-key",
            secret_key="test-public-contract-secret", http_client=client, evidence_writer=lambda *a: writes.append(a))
        try:
            result = await owner.latest(instrument())
            assert result.state == "unavailable" and result.reason == f"alpaca_data_http_{status}"
            assert len(calls) == 1 and calls[0].url.host == "data.alpaca.markets" and writes == []
            assert "evil.invalid" not in result.model_dump_json()
        finally:
            await owner.aclose()
    asyncio.run(run())


def test_transport_errors_are_sanitized_and_do_not_claim_authorization():
    async def run():
        async def source(request):
            raise httpx.ConnectError("test-public-contract-secret", request=request)
        owner = service(source)
        try:
            result = await owner.latest(instrument())
            assert result.state == "authorization_unverified" and result.authorization_verified is False
            assert result.reason == "alpaca_data_transport_failed"
            assert "test-public-contract" not in result.model_dump_json()
        finally:
            await owner.aclose()
    asyncio.run(run())


def test_invalid_response_is_not_cached_or_written():
    async def run():
        calls, writes = [], []
        async def source(request):
            calls.append(request)
            return httpx.Response(200, content=source_body("NVDA") if len(calls) == 1 else source_body())
        owner = service(source, evidence_writer=lambda *a: writes.append(a))
        try:
            assert (await owner.latest(instrument())).reason == "alpaca_data_contract_invalid"
            assert (await owner.latest(instrument())).state == "available"
            assert len(calls) == 2 and len(writes) == 1
        finally:
            await owner.aclose()
    asyncio.run(run())


def test_cache_rechecks_age_and_returned_result_cannot_mutate_shared_facts(monkeypatch):
    async def run():
        calls = []
        async def source(request):
            calls.append(request)
            return httpx.Response(200, content=source_body())
        owner = service(source)
        try:
            first = await owner.latest(instrument())
            first.quote.ask_price = 1
            first.evidence[0].params["symbols"] = "NVDA"
            second = await owner.latest(instrument())
            assert second.quote.ask_price == 172.7 and second.evidence[0].params["symbols"] == "AAPL"
            monkeypatch.setattr(alpaca, "_now", lambda: NOW + timedelta(seconds=61))
            third = await owner.latest(instrument())
            assert third.state == "stale" and third.received_at == NOW and len(calls) == 1
        finally:
            await owner.aclose()
    asyncio.run(run())


def test_one_inflight_owner_survives_waiter_cancel_and_closes_after_source_drains():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def source(request):
            calls.append(request)
            entered.set()
            await release.wait()
            return httpx.Response(200, content=source_body())
        owner = service(source)
        abandoned = asyncio.create_task(owner.latest(instrument()))
        await entered.wait()
        retained = asyncio.create_task(owner.latest(instrument()))
        await asyncio.sleep(0)
        abandoned.cancel()
        with pytest.raises(asyncio.CancelledError):
            await abandoned
        closing = asyncio.create_task(owner.aclose())
        await asyncio.sleep(0)
        assert not closing.done() and not owner._client.is_closed
        release.set()
        assert (await retained).state == "available"
        await closing
        assert owner._client.is_closed and len(calls) == 1
        assert (await owner.latest(instrument())).reason == "alpaca_data_closing"
    asyncio.run(run())


def test_capacity_rejects_new_source_without_queueing_or_fifth_request():
    async def run():
        release, four = asyncio.Event(), asyncio.Event()
        calls = []
        async def source(request):
            calls.append(request)
            if len(calls) == 4:
                four.set()
            await release.wait()
            return httpx.Response(200, content=source_body(request.url.params["symbols"]))
        owner = service(source)
        pending = [asyncio.create_task(owner.latest(instrument(symbol))) for symbol in ["AAPL", "NVDA", "MSFT", "META"]]
        try:
            await four.wait()
            result = await owner.latest(instrument("AMZN"))
            assert result.reason == "alpaca_data_local_busy" and len(calls) == 4
        finally:
            release.set()
            await asyncio.gather(*pending)
            await owner.aclose()
    asyncio.run(run())


def test_slow_stream_has_one_whole_deadline_and_oversize_never_reaches_writer(monkeypatch):
    async def run():
        writes = []
        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{'
                await asyncio.sleep(0.05)
                yield b'}'
        owner = service(lambda request: httpx.Response(200, stream=Slow()), evidence_writer=lambda *a: writes.append(a))
        try:
            result = await owner.latest(instrument())
            assert result.reason == "alpaca_data_timeout" and writes == []
        finally:
            await owner.aclose()
        owner = service(lambda request: httpx.Response(200, content=b' ' * (alpaca.MAX_BODY + 1)),
                        evidence_writer=lambda *a: writes.append(a))
        try:
            assert (await owner.latest(instrument())).reason == "alpaca_data_response_too_large"
            assert writes == []
        finally:
            await owner.aclose()
    monkeypatch.setattr(alpaca, "READ_TIMEOUT", 0.01)
    asyncio.run(run())


def test_close_waits_for_owned_evidence_writer_even_when_close_is_cancelled():
    async def run():
        entered, release, written = threading.Event(), threading.Event(), []
        def writer(evidence, body):
            entered.set()
            release.wait(timeout=3)
            written.append(evidence.payload_sha256)
        owner = service(lambda request: httpx.Response(200, content=source_body()), evidence_writer=writer)
        pending = asyncio.create_task(owner.latest(instrument()))
        while not entered.is_set():
            await asyncio.sleep(0)
        closing = asyncio.create_task(owner.aclose())
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done() and not owner._client.is_closed
        release.set()
        assert (await pending).state == "available"
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert written and owner._client.is_closed
        await owner.aclose()
    asyncio.run(run())


def test_credential_values_and_ambient_client_auth_are_not_accepted_as_urls_or_logs():
    for bad in ["value\nheader: secret", " key with space ", "x" * 257]:
        with pytest.raises(ValueError, match="credential format"):
            alpaca.AlpacaDataService(api_key=bad)
    async def run():
        for options in [{"headers": {"Cookie": "secret"}}, {"params": {"token": "secret"}},
                        {"auth": ("username", "password")}]:
            client = httpx.AsyncClient(**options)
            try:
                with pytest.raises(ValueError, match="ambient"):
                    alpaca.AlpacaDataService(http_client=client)
            finally:
                await client.aclose()
    asyncio.run(run())
