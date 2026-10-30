"""US source contracts use small offline rows, with no source or account writes."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json

import httpx
import pytest

from app.assets import us_market as us
from app.core.errors import AppError
from app.core.inflight_reads import LocalReadCapacityExceeded
from app.schemas.asset_evidence import AssetEvidence

NOW = datetime(2026, 10, 30, 5, 45, tzinfo=timezone.utc)


def quote_body(code="AAPL.OQ", **changes):
    # Source positions and small public values were checked against Tencent's
    # own US page. Test modifications are synthetic counterexamples.
    fields = [""] * 73
    fixed = {0: "200", 1: "苹果", 2: code, 3: "348.91", 4: "343.83", 5: "348.00",
             30: "2026-10-29 16:00:01", 31: "5.08", 32: "1.48", 33: "351.43",
             34: "347.30", 35: "USD", 36: "42582126", 37: "14878265494",
             46: "Apple Inc.", 56: "GP"}
    fixed.update({int(index): value for index, value in changes.items()})
    for index, value in fixed.items():
        fields[index] = value
    ticker = code.rsplit(".", 1)[0].replace(".", "__")
    return f'v_us{ticker}="{"~".join(fields)}";\n'.encode("gb18030")


def daily_body(code="AAPL.OQ", rows=None):
    if rows is None:
        rows = [["2026-10-28", "343.00", "343.83", "344.00", "342.00", "100"],
                ["2026-10-29", "348.00", "348.91", "351.43", "347.30", "42582126",
                 {"FHcontent": "来源公司行动原文"}]]
    return json.dumps({"code": 0, "data": {"us" + code: {"day": rows}}}).encode()


def evidence(at=NOW):
    return AssetEvidence(endpoint=us.QUOTE_URL, params={"q": "usAAPL"}, received_at=at,
                         payload_sha256="a" * 64, receipt_id="b" * 64)


def make_service(handler, **options):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return us.UsMarketService(http_client=client, **options)


def test_quote_keeps_source_identity_currency_units_and_regular_close():
    instrument, quote = us.parse_quote(quote_body(), "AAPL.OQ", evidence())
    assert instrument.instrument_id == "us_equity:US:AAPL.OQ:USD"
    assert (instrument.symbol, instrument.source_symbol, instrument.currency) == ("AAPL", "AAPL.OQ", "USD")
    assert (instrument.name, instrument.english_name) == ("苹果", "Apple Inc.")
    assert quote.source_time.isoformat() == "2026-10-29T16:00:01-04:00"
    assert quote.source_time.astimezone(timezone.utc).hour == 20
    assert quote.observation == "regular_close"
    assert quote.price == 348.91 and quote.change_pct == 1.48
    assert quote.change_basis == "source_previous_regular_close"
    assert quote.volume_shares == 42582126 and quote.turnover == 14878265494
    assert quote.feed_delay_seconds is None and quote.realtime_verified is False


def test_quote_dst_uses_source_date_instead_of_host_or_current_offset():
    later = evidence(datetime(2026, 11, 10, 2, tzinfo=timezone.utc))
    _, quote = us.parse_quote(quote_body(**{"30": "2026-11-09 16:00:01"}), "AAPL", later)
    assert quote.source_time.isoformat() == "2026-11-09T16:00:01-05:00"


@pytest.mark.parametrize("changes,code", [
    ({"35": ""}, "us_currency_unknown"), ({"35": "CNY"}, "us_currency_unknown"),
    ({"3": "NaN"}, "us_source_value_invalid"), ({"3": "inf"}, "us_source_value_invalid"),
    ({"36": "-1"}, "us_source_value_invalid"), ({"3": "0"}, "us_source_value_invalid"),
    ({"30": "2027-10-29 16:00:01"}, "us_source_clock_invalid"),
    ({"56": "ZS"}, "us_quote_shape_invalid"),
])
def test_quote_rejects_missing_currency_nonfinite_and_unverified_session(changes, code):
    with pytest.raises(us.UsSourceError) as error:
        us.parse_quote(quote_body(**changes), "AAPL.OQ", evidence())
    assert error.value.code == code


def test_wrong_header_inner_code_and_duplicate_assignment_are_not_renamed():
    for body in [quote_body().replace(b"v_usAAPL", b"v_usNVDA"),
                 quote_body().replace(b"~AAPL.OQ~", b"~NVDA.OQ~"), quote_body() * 2]:
        with pytest.raises(us.UsSourceError, match="身份"):
            us.parse_quote(body, "AAPL.OQ", evidence())
    with pytest.raises(us.UsSourceError) as error:
        us.parse_quote(quote_body(), "AAPL.N", evidence())
    assert error.value.code == "us_identity_mismatch"


def test_source_confirmed_class_share_identity_preserves_class():
    instrument, _ = us.parse_quote(quote_body("BRK.B.N"), "BRK.B.N", evidence())
    assert instrument.source_symbol == "BRK.B.N" and instrument.symbol == "BRK.B"
    assert instrument.exchange_source_code == "N"
    assert instrument.instrument_id == "us_equity:US:BRK.B.N:USD"


def test_source_etf_kind_and_amex_venue_are_preserved_without_name_guessing():
    instrument, _ = us.parse_quote(quote_body("SPY.AM", **{"56": "GP-ETF"}), "SPY.AM", evidence())
    assert instrument.security_type == "etf" and instrument.exchange_source_code == "AM"
    assert instrument.asset_type == "us_equity"
    result = us.parse_search(b'v_hint="us~spy.am~SPY~pg~GP-ETF"', "SPY", evidence())
    assert result.items[0].security_type == "etf" and result.items[0].source_kind == "GP-ETF"
    stock, _ = us.parse_quote(quote_body(**{"1": "NotAnETF"}), "AAPL", evidence())
    assert stock.security_type == "stock"


@pytest.mark.parametrize("wall", ["16:04:51", "02:00:00"])
def test_valid_source_quote_outside_verified_regular_window_keeps_unverified_session(wall):
    _, quote = us.parse_quote(quote_body(**{"30": "2026-10-29 " + wall}), "AAPL", evidence())
    assert quote.observation == "session_unverified"  # Does not assert a current extended quote.
    same_day = evidence(datetime(2026, 10, 29, 20, 5, tzinfo=timezone.utc))
    if wall == "16:04:51":
        _, fresh = us.parse_quote(quote_body(**{"30": "2026-10-29 " + wall}), "AAPL", same_day)
        assert fresh.observation == "session_unverified"


@pytest.mark.parametrize("suffix", ["SH", "SZ", "BJ", "OF", "HK", "P", "A", "FAKE"])
def test_source_cannot_smuggle_unverified_venue_into_us_namespace(suffix):
    wrong = quote_body().replace(b"~AAPL.OQ~", f"~AAPL.{suffix}~".encode())
    with pytest.raises(us.UsSourceError) as error:
        us.parse_quote(wrong, "AAPL", evidence())
    assert error.value.code == "us_identity_mismatch"
    result = us.parse_search(f'v_hint="us~aapl.{suffix.lower()}~Apple~pg~GP"'.encode(), "AAPL", evidence())
    assert result.state == "partial" and result.items == []


def test_search_is_source_only_and_does_not_execute_javascript():
    body = b'v_hint="us~aapl.oq~\\u82f9\\u679c~pg~GP^us~brk.b.n~BRK B~pg~GP"'
    result = us.parse_search(body, "AAPL", evidence())
    assert result.state == "ready"
    assert [(item.name, item.source_symbol) for item in result.items] == [("苹果", "AAPL.OQ"), ("BRK B", "BRK.B.N")]
    row = result.items[0].model_dump()
    assert "instrument_id" not in row and "currency" not in row
    with pytest.raises(us.UsSourceError):
        us.parse_search(body + b';fetch("private")', "AAPL", evidence())


def test_search_keeps_unsupported_and_duplicate_identities_out_of_candidates():
    body = b'v_hint="sh~600519~wrong~pg~GP^us~aapl.oq~Apple~pg~GP^us~aapl.oq~Again~pg~GP^us~qqq.oq~QQQ~zs~ZS"'
    result = us.parse_search(body, "a", evidence())
    assert result.state == "partial"
    assert len(result.items) == 1 and result.items[0].source_symbol == "AAPL.OQ"
    assert set(result.issues) == {"unsupported_source_identity", "invalid_or_duplicate_source_identity"}


def test_daily_preserves_raw_prices_share_units_and_corporate_action_extra():
    result = us.parse_daily(daily_body(), "AAPL.OQ", evidence())
    assert result.state == "ready" and result.price_basis == "none"
    assert result.points[-1].close == 348.91
    assert result.points[-1].volume_shares == 42582126
    assert result.points[0].date.isoformat() == "2026-10-28"


@pytest.mark.parametrize("body", [
    daily_body("NVDA.OQ"), b'{"code":false,"data":{}}', b'{"code":0,"data":{"usAAPL.OQ":{"qfqday":[]}}}',
    daily_body(rows=[]), daily_body(rows=[["2027-10-29", "1", "1", "1", "1", "1"]]),
    daily_body(rows=[["2026-10-29", "1", "2", "1", "1", "1"]]),
    daily_body(rows=[["2026-10-29", "1", "1", "1", "1", "NaN"]]),
    daily_body(rows=[["2026-10-29", "1", "1", "1", "1", "1"]] * 2),
])
def test_daily_wrong_identity_adjustment_missing_dates_duplicates_and_bad_values_fail(body):
    with pytest.raises(us.UsSourceError):
        us.parse_daily(body, "AAPL.OQ", evidence())


def test_snapshot_retains_regular_quote_when_daily_fails_and_does_not_guess_sessions(monkeypatch):
    async def run():
        calls = []
        async def source(request):
            calls.append((request.url.host, dict(request.url.params)))
            if request.url.host == "qt.gtimg.cn":
                return httpx.Response(200, content=quote_body())
            return httpx.Response(503, text="private-diagnostic")
        service = make_service(source)
        try:
            result = await service.snapshot("AAPL.OQ")
            assert result.regular.price == 348.91 and result.daily.state == "unavailable"
            assert result.daily.reason == "us_source_request_failed"
            assert result.clock_phase == "overnight" and result.calendar_status == "unverified"
            assert all(getattr(result, mode).state == "unavailable" for mode in ["pre_market", "post_market", "overnight", "minute"])
            assert len(calls) == 2 and calls[1][1] == {"param": "usAAPL.OQ,day,,,120,"}
            assert "private-diagnostic" not in result.model_dump_json()
        finally:
            await service.aclose()
    monkeypatch.setattr(us, "_utcnow", lambda: NOW)
    asyncio.run(run())


def test_resolve_only_uses_quote_and_valid_alias_shares_cache(monkeypatch):
    async def run():
        calls = []
        async def source(request):
            calls.append(str(request.url))
            return httpx.Response(200, content=quote_body())
        service = make_service(source)
        try:
            first = await service.resolve("AAPL")
            second = await service.resolve("AAPL.OQ")
            assert first == second and len(calls) == 1
            with pytest.raises(us.UsSourceError):
                await service.resolve("AAPL.N")
            assert (await service.resolve("AAPL.OQ")) == first
            assert len(calls) == 1
        finally:
            await service.aclose()
    monkeypatch.setattr(us, "_utcnow", lambda: NOW)
    asyncio.run(run())


def test_cached_quote_crossing_sixty_seconds_rechecks_age_without_new_source_or_reclocking(monkeypatch):
    from app.core import ttl_cache
    received = datetime(2026, 10, 30, 14, 0, 50, tzinfo=timezone.utc)
    clock = {"at": received, "tick": 1000.0}
    monkeypatch.setattr(us, "_utcnow", lambda: clock["at"])
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock["tick"])

    async def run():
        calls = []
        async def source(request):
            calls.append(str(request.url))
            return httpx.Response(200, content=quote_body(**{"30": "2026-10-30 10:00:00"}))
        service = make_service(source)
        try:
            _, first = await service._quote("AAPL.OQ")
            assert first.observation == "regular_quote"
            original = first.model_dump()
            clock["at"] += timedelta(seconds=14)
            clock["tick"] += 14
            _, cached = await service._quote("AAPL.OQ")
            assert cached.observation == "stale"
            assert cached.source_time.isoformat() == "2026-10-30T10:00:00-04:00"
            assert cached.received_at == received
            assert cached.evidence == first.evidence and first.model_dump() == original
            assert len(calls) == 1
        finally:
            await service.aclose()
    asyncio.run(run())


def test_waiting_for_daily_crosses_sixty_seconds_and_retains_quote_with_stale_label(monkeypatch):
    received = datetime(2026, 10, 30, 14, 0, 50, tzinfo=timezone.utc)
    clock = {"at": received}
    monkeypatch.setattr(us, "_utcnow", lambda: clock["at"])

    async def run():
        calls = []
        async def source(request):
            calls.append(request.url.host)
            if request.url.host == "qt.gtimg.cn":
                return httpx.Response(200, content=quote_body(**{"30": "2026-10-30 10:00:00"}))
            clock["at"] += timedelta(seconds=11)
            return httpx.Response(200, content=daily_body())
        service = make_service(source)
        try:
            snapshot = await service.snapshot("AAPL.OQ")
            assert snapshot.regular.observation == "stale"
            assert snapshot.regular.price == 348.91 and snapshot.daily.state == "ready"
            assert snapshot.regular.source_time.isoformat() == "2026-10-30T10:00:00-04:00"
            assert snapshot.regular.received_at == received
            assert snapshot.received_at == clock["at"]
            assert calls == ["qt.gtimg.cn", "web.ifzq.gtimg.cn"]
        finally:
            await service.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("seconds,expected", [(60, "regular_quote"), (60.000001, "stale")])
def test_observe_quote_keeps_exact_sixty_second_threshold_and_original_evidence(seconds, expected):
    received = datetime(2026, 10, 30, 14, 0, 50, tzinfo=timezone.utc)
    _, quote = us.parse_quote(quote_body(**{"30": "2026-10-30 10:00:00"}), "AAPL.OQ", evidence(received))
    original = quote.model_dump()
    result = us.observe_quote(quote, quote.source_time + timedelta(seconds=seconds))
    assert result.observation == expected
    assert result.received_at == received and result.source_time == quote.source_time
    assert result.evidence == quote.evidence and quote.model_dump() == original


@pytest.mark.parametrize("wall,days,expected", [
    ("16:00:01", 7, "regular_close"), ("16:00:01", 8, "stale"),
    ("16:04:51", 7, "session_unverified"), ("16:04:51", 8, "stale"),
])
def test_observe_quote_preserves_close_and_unverified_session_seven_day_rule(wall, days, expected):
    _, quote = us.parse_quote(quote_body(**{"30": "2026-10-29 " + wall}), "AAPL.OQ", evidence())
    result = us.observe_quote(quote, quote.source_time + timedelta(days=days))
    assert result.observation == expected
    assert result.received_at == quote.received_at


def test_observe_quote_cannot_infer_a_timezone_for_naive_observation_clock():
    _, quote = us.parse_quote(quote_body(), "AAPL.OQ", evidence())
    with pytest.raises(us.UsSourceError) as error:
        us.observe_quote(quote, datetime(2026, 10, 30, 14, 0))
    assert error.value.code == "us_source_clock_invalid"


def test_bad_quote_is_not_cached_and_429_is_not_retried(monkeypatch):
    async def run():
        calls = []
        async def source(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(200, content=quote_body(**{"3": "NaN"}))
            if len(calls) == 2:
                return httpx.Response(429, text="provider-private-data")
            return httpx.Response(200, content=quote_body())
        service = make_service(source)
        try:
            for count in [1, 2]:
                with pytest.raises(us.UsSourceError) as error:
                    await service.resolve("AAPL.OQ")
                assert len(calls) == count and "provider-private-data" not in str(error.value)
            assert (await service.resolve("AAPL.OQ")).currency == "USD" and len(calls) == 3
        finally:
            await service.aclose()
    monkeypatch.setattr(us, "_utcnow", lambda: NOW)
    asyncio.run(run())


def test_redirect_is_not_followed_even_with_wider_injected_client():
    async def run():
        calls = []
        async def source(request):
            calls.append(request)
            return httpx.Response(302, headers={"Location": "https://unapproved.test/"})
        client = httpx.AsyncClient(transport=httpx.MockTransport(source), follow_redirects=True)
        service = us.UsMarketService(http_client=client)
        try:
            with pytest.raises(us.UsSourceError):
                await service.resolve("AAPL.OQ")
            assert len(calls) == 1 and calls[0].url.host == "qt.gtimg.cn"
        finally:
            await service.aclose()
    asyncio.run(run())


def test_source_response_limit_rejects_before_parsing_or_persistence(tmp_path):
    async def run():
        calls = []
        async def source(request):
            calls.append(request)
            return httpx.Response(200, content=b"x" * (us.MAX_BODY + 1))
        service = make_service(source, evidence_dir=tmp_path)
        try:
            with pytest.raises(us.UsSourceError) as error:
                await service.resolve("AAPL.OQ")
            assert error.value.code == "us_source_too_large" and len(calls) == 1
            assert list(tmp_path.iterdir()) == []
        finally:
            await service.aclose()
    asyncio.run(run())


def test_disabled_invalid_and_capacity_reads_start_no_source():
    async def run():
        async def source(request):
            raise AssertionError("must not reach source")
        service = make_service(source, enabled=False)
        try:
            with pytest.raises(AppError) as error:
                await service.resolve("AAPL.OQ")
            assert error.value.code == "us_source_disabled"
            service.enabled = True
            for code in ["600519.SH", "AAPL.SH", "AAPL.OQ&other=secret", "../../.env", "AAPL:USD"]:
                with pytest.raises(AppError):
                    await service.resolve(code)
            with pytest.raises(AppError):
                await service.snapshot("AAPL.OQ", 121)
            service._requests.maxsize = 0
            with pytest.raises(LocalReadCapacityExceeded):
                await service.resolve("AAPL.OQ")
            assert service._client is not None
        finally:
            await service.aclose()
    asyncio.run(run())


def test_source_evidence_deduplicates_body_but_not_receive_clock(monkeypatch, tmp_path):
    async def run():
        async def source(request):
            return httpx.Response(200, content=quote_body())
        clock = {"at": NOW}
        monkeypatch.setattr(us, "_utcnow", lambda: clock["at"])
        service = make_service(source, evidence_dir=tmp_path)
        try:
            _, first = await service._quote("AAPL.OQ")
            receipt = first.evidence[0]
            original_path = tmp_path / f"{receipt.receipt_id}.receipt.json"
            original, mtime = original_path.read_bytes(), original_path.stat().st_mtime_ns
            service._cache.invalidate()
            clock["at"] = NOW.replace(second=10)
            _, second = await service._quote("AAPL.OQ")
            newer = second.evidence[0]
            assert receipt.receipt_id != newer.receipt_id and receipt.payload_sha256 == newer.payload_sha256
            assert len(list(tmp_path.glob('*.body.bin'))) == 1
            assert len(list(tmp_path.glob('*.receipt.json'))) == 2
            assert original_path.read_bytes() == original and original_path.stat().st_mtime_ns == mtime
            saved = json.loads((tmp_path / f"{newer.receipt_id}.receipt.json").read_text())
            assert saved['received_at'] == newer.model_dump(mode='json')['received_at']
            assert hashlib.sha256(next(tmp_path.glob('*.body.bin')).read_bytes()).hexdigest() == newer.payload_sha256
        finally:
            await service.aclose()
    asyncio.run(run())


def test_waiter_cancellation_and_close_drain_owner_without_repeating_quote(monkeypatch):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def source(request):
            calls.append(request)
            entered.set()
            await release.wait()
            return httpx.Response(200, content=quote_body())
        service = make_service(source)
        first = asyncio.create_task(service.resolve("AAPL.OQ"))
        second = asyncio.create_task(service.resolve("AAPL"))
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        closer = asyncio.create_task(service.aclose())
        await asyncio.sleep(0)
        assert not closer.done() and not service._client.is_closed and len(calls) == 1
        release.set()
        assert (await second).source_symbol == "AAPL.OQ"
        await closer
        assert service._client.is_closed and service._requests.tasks == {}
        with pytest.raises(AppError) as error:
            await service.resolve("AAPL")
        assert error.value.code == "us_service_closing"
    monkeypatch.setattr(us, "_utcnow", lambda: NOW)
    asyncio.run(run())


def test_total_stream_deadline_closes_response_without_per_chunk_extension(monkeypatch):
    async def run():
        class SlowStream(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                for _ in range(100):
                    await asyncio.sleep(.01)
                    yield b" "
            async def aclose(self):
                self.closed = True
        stream = SlowStream()
        async def source(request):
            return httpx.Response(200, stream=stream)
        service = make_service(source)
        original = asyncio.timeout
        monkeypatch.setattr(us.asyncio, "timeout", lambda delay: original(.035 if delay == 10 else delay))
        try:
            with pytest.raises(us.UsSourceError) as error:
                await service.resolve("AAPL")
            assert error.value.code == "us_source_request_failed" and stream.closed
        finally:
            await service.aclose()
        assert service._requests.tasks == {}
    asyncio.run(run())


def test_canceling_close_still_drains_owner_before_propagating_cancellation(monkeypatch):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def source(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, content=quote_body())
        service = make_service(source)
        read = asyncio.create_task(service.resolve("AAPL"))
        await entered.wait()
        close = asyncio.create_task(service.aclose())
        await asyncio.sleep(0)
        close.cancel()
        await asyncio.sleep(0)
        assert not close.done() and not service._client.is_closed
        release.set()
        assert (await read).source_symbol == "AAPL.OQ"
        with pytest.raises(asyncio.CancelledError):
            await close
        assert service._client.is_closed and service._requests.tasks == {}
        await service.aclose()
    monkeypatch.setattr(us, "_utcnow", lambda: NOW)
    asyncio.run(run())
