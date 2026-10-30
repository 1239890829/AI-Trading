"""Offline public-source contracts; all evidence writes stay inside tmp_path."""
import asyncio
from collections import Counter
from datetime import datetime
import hashlib
import json

import httpx
import pytest

from app.assets.funds import AssetSourceError, instrument
from app.assets.service import AssetReadService, HOLDINGS, NAV, PROFILE, SEARCH
from app.core.errors import AppError
from app.core.inflight_reads import LocalReadCapacityExceeded


def ms(value):
    return int(datetime.fromisoformat(value + "T00:00:00+08:00").timestamp() * 1000)


def directory(code="040046.OF", currency="CNY"):
    return {"thscode": code, "ticker": code.split(".")[0], "name": "来源基金A",
            "asset_type": "fund-otc", "exchange": None, "currency": currency}


def stock(code, weight, report="2026-09-30"):
    return {"thscode": code, "stock_name": code, "asset_type": "stock", "hold_ratio": weight,
            "end_date_ms": ms(report), "publish_date_ms": ms("2026-10-27")}


def fixtures():
    return {
        SEARCH: {"timestamp": ms("2026-10-30"), "item": [directory()]},
        NAV: {"timestamp": ms("2026-10-30"), "item": [
            {"nav_date": ms("2026-10-28"), "unit_nav": 8.1932, "adj_nav": 8.1932}]},
        PROFILE: {"timestamp": ms("2026-10-30"), "item": [{"thscode": "040046.OF", "mgmt_name": "来源管理人",
                   "rate_info": [{"rate_type": "申购费", "standard_rate": "1.2%", "condition": "按来源档位"}],
                   "trade_rule": [{"title": "开放日", "display_time": "以基金公告为准"}]}]},
        HOLDINGS: {"timestamp": 0, "item": [{**stock("159632.SZ", 90.01), "asset_type": "fund"}]},
        "target": {"timestamp": 0, "item": [stock("NVDA.O", 7.72), stock("AAPL.O", 6.81), stock("MSFT.O", 28.72)]},
    }


def setup(monkeypatch, *, data=None, handler=None, evidence_dir=None):
    from app.assets import service as module
    monkeypatch.setattr(module, "_utcnow", lambda: datetime.fromisoformat("2026-10-30T10:00:00+08:00"))
    data = data or fixtures()
    calls = Counter()

    async def default(request):
        path = request.url.path
        calls[(path, request.url.params.get("thscode"))] += 1
        payload = data["target"] if path == HOLDINGS and request.url.params.get("thscode") == "159632.SZ" else data[path]
        # Preserve intentionally malformed numeric JSON in source-error cases.
        return httpx.Response(200, content=json.dumps({"code": 0, "data": payload}))

    client = httpx.AsyncClient(base_url="https://fuyao.aicubes.cn", transport=httpx.MockTransport(handler or default))
    return AssetReadService("isolated-test-key", http_client=client, evidence_dir=evidence_dir), calls, data


def test_full_fund_read_preserves_nav_clock_partial_exposure_and_raw_hash(monkeypatch, tmp_path):
    async def run():
        service, calls, _ = setup(monkeypatch, evidence_dir=tmp_path / "evidence")
        try:
            result = await service.snapshot("040046.OF")
            assert result.instrument.instrument_id == "fund:OF:040046:CNY"
            assert str(result.nav.latest.nav_date) == "2026-10-28"
            assert result.nav.published_at is None and not result.nav.publication_time_known
            assert result.lookthrough.covered_weight_pct == pytest.approx(38.929325)
            assert result.lookthrough.uncovered_weight_pct == pytest.approx(61.070675)
            assert all(row.via == ["040046.OF", "159632.SZ"] for row in result.lookthrough.items)
            assert not result.research.eligible and result.estimate.value is None
            assert "investment_scope" in result.research.missing
            assert result.terms.fees[0].value == "1.2%"
            files = list((tmp_path / "evidence").glob("*.receipt.json"))
            assert len(files) == 5
            for file in files:
                record = json.loads(file.read_text())
                raw = file.parent / record["raw_file"]
                assert hashlib.sha256(raw.read_bytes()).hexdigest() == record["payload_sha256"]
                assert "isolated-test-key" not in file.read_text()
            result.nav.latest.unit_nav = 999
            again = await service.snapshot("040046.OF")
            assert again.nav.latest.unit_nav == 8.1932
            assert sum(calls.values()) == 5
            assert len(list((tmp_path / "evidence").glob("*.body.json"))) == 5
            assert len(list((tmp_path / "evidence").glob("*.receipt.json"))) == 5
        finally:
            await service.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("bad_value", [None, "", "XXX", "XYZ", "usd"])
def test_currency_must_be_source_confirmed_supported_iso(bad_value):
    with pytest.raises(AssetSourceError):
        instrument(directory(currency=bad_value))


def test_explicit_usd_share_keeps_source_currency_not_cny_default():
    result = instrument(directory(code="040047.OF", currency="USD"))
    assert result.instrument_id == "fund:OF:040047:USD"
    assert result.currency == "USD"


@pytest.mark.parametrize("code", ["040046", "AAPL", "040046.SH", "../../040046.OF", "159632.SZ"])
def test_invalid_or_out_of_scope_root_never_calls_source(monkeypatch, code):
    async def run():
        service, calls, _ = setup(monkeypatch)
        try:
            with pytest.raises(AppError):
                await service.snapshot(code)
            assert not calls
        finally:
            await service.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("mutate,missing", [
    (lambda d: d[NAV]["item"][0].update(nav_date=ms("2026-10-31")), "fund_date_invalid"),
    (lambda d: d[NAV]["item"][0].update(thscode="040047.OF"), "fund_identity_mismatch"),
    (lambda d: d[NAV]["item"][0].update(unit_nav=float("inf")), "fund_value_invalid"),
    (lambda d: d[NAV]["item"][0].update(currency="USD"), "fund_currency_mismatch"),
    (lambda d: d[NAV]["item"].append(d[NAV]["item"][0].copy()), "fund_date_invalid"),
])
def test_bad_nav_is_not_hidden_by_response_clock_or_cached(monkeypatch, mutate, missing):
    async def run():
        data = fixtures()
        mutate(data)
        service, calls, _ = setup(monkeypatch, data=data)
        try:
            result = await service.snapshot("040046.OF")
            assert result.nav.state == "unavailable"
            assert missing in result.nav.missing
            assert result.nav.evidence[0].payload_sha256
            data[NAV] = fixtures()[NAV]
            recovered = await service.snapshot("040046.OF")
            assert recovered.nav.latest.unit_nav == 8.1932
            assert calls[(NAV, "040046.OF")] == 2
        finally:
            await service.aclose()
    asyncio.run(run())


def test_mismatched_target_period_cannot_fill_missing_exposure(monkeypatch):
    async def run():
        data = fixtures()
        for row in data["target"]["item"]:
            row["end_date_ms"] = ms("2026-06-30")
        service, _, _ = setup(monkeypatch, data=data)
        try:
            result = await service.snapshot("040046.OF")
            assert result.lookthrough.covered_weight_pct == 0
            assert result.lookthrough.uncovered_weight_pct == 100
            assert "target_report_not_aligned" in result.lookthrough.missing
        finally:
            await service.aclose()
    asyncio.run(run())


def test_us_and_disabled_capabilities_never_start_source(monkeypatch):
    async def run():
        service, calls, _ = setup(monkeypatch)
        service.enabled = False
        try:
            assert service.capabilities().items[0].state == "blocked"
            result = await service.search("AAPL", "us_equity")
            assert result.state == "blocked" and result.items == []
            with pytest.raises(AppError) as exc:
                await service.snapshot("040046.OF")
            assert exc.value.code == "asset_source_disabled"
            assert not calls
        finally:
            await service.aclose()
    asyncio.run(run())


def test_cache_expiry_and_different_services_keep_source_ownership(monkeypatch):
    from app.core import ttl_cache
    tick = {"now": 100.0}
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: tick["now"])

    async def run():
        first, calls, data = setup(monkeypatch)
        second, other_calls, _ = setup(monkeypatch)
        try:
            await first.snapshot("040046.OF")
            await second.snapshot("040046.OF")
            assert sum(calls.values()) == sum(other_calls.values()) == 5
            tick["now"] += 301
            data[NAV]["item"][0]["unit_nav"] = 8.3
            result = await first.snapshot("040046.OF")
            assert result.nav.latest.unit_nav == 8.3
            assert calls[(NAV, "040046.OF")] == 2
            assert calls[(HOLDINGS, "040046.OF")] == 1
        finally:
            await first.aclose()
            await second.aclose()
    asyncio.run(run())


def test_cancelled_waiter_and_close_drain_actual_shared_source(monkeypatch):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def source(request):
            calls.append(request.url.path)
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"code": 0, "data": fixtures()[SEARCH]})

        service, _, _ = setup(monkeypatch, handler=source)
        waiter = asyncio.create_task(service.resolve_fund("040046.OF"))
        await entered.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        closer = asyncio.create_task(service.aclose())
        await asyncio.sleep(0)
        assert not closer.done()
        release.set()
        await closer
        await asyncio.sleep(0)
        assert calls == [SEARCH]
        assert service._client.is_closed
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())


def test_source_capacity_rejects_new_key_but_same_key_joins(monkeypatch):
    async def run():
        release = asyncio.Event()
        started = asyncio.Event()
        calls = []

        async def source(request):
            calls.append(request.url.params["q"])
            if len(calls) == 4:
                started.set()
            await release.wait()
            return httpx.Response(200, json={"code": 0, "data": fixtures()[SEARCH]})

        service, _, _ = setup(monkeypatch, handler=source)
        tasks = [asyncio.create_task(service.search(str(n))) for n in range(4)]
        try:
            await started.wait()
            joined = asyncio.create_task(service.search("0"))
            with pytest.raises(LocalReadCapacityExceeded):
                await service.search("extra")
            assert len(calls) == 4
            release.set()
            result = await asyncio.gather(*tasks, joined)
            assert result[0] == result[-1]
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await service.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("currency", ["USD", "XXX"])
def test_conflicting_source_share_currencies_are_not_selected_or_cached(monkeypatch, currency):
    async def run():
        data = fixtures()
        data[SEARCH]["item"].append(directory(currency=currency))
        service, calls, _ = setup(monkeypatch, data=data)
        try:
            result = await service.search("040046", limit=2)
            assert result.items == [] and "duplicate_fund_identity" in result.issues
            with pytest.raises(AppError) as exc:
                await service.resolve_fund("040046.OF")
            assert exc.value.code == "fund_identity_unconfirmed"
            data[SEARCH] = fixtures()[SEARCH]
            assert (await service.resolve_fund("040046.OF")).currency == "CNY"
            assert sum(calls.values()) == 3
        finally:
            await service.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("mutate,missing", [
    (lambda d: d[HOLDINGS]["item"][0].update(hold_ratio=101), "fund_value_invalid"),
    (lambda d: d[HOLDINGS]["item"][0].update(publish_date_ms=ms("2026-09-29")), "fund_date_invalid"),
    (lambda d: d[HOLDINGS]["item"][0].update(publish_date_ms=ms("2026-10-30") + 12 * 3600_000), "fund_date_invalid"),
    (lambda d: d[HOLDINGS]["item"].append(stock("AAPL.O", 20)), "fund_weight_unsupported"),
    (lambda d: d[HOLDINGS]["item"].append(stock("AAPL.O", 1, "2026-06-30")), "fund_report_mismatch"),
])
def test_invalid_disclosure_keeps_other_sections_and_never_invents_coverage(monkeypatch, mutate, missing):
    async def run():
        data = fixtures()
        mutate(data)
        service, calls, _ = setup(monkeypatch, data=data)
        try:
            result = await service.snapshot("040046.OF")
            assert result.nav.latest.unit_nav == 8.1932
            assert result.holdings.state == "unavailable" and missing in result.holdings.missing
            assert result.holdings.evidence[0].payload_sha256
            assert result.lookthrough.items == []
            assert result.lookthrough.covered_weight_pct == 0 and result.lookthrough.uncovered_weight_pct == 100
            assert calls[(HOLDINGS, "159632.SZ")] == 0
        finally:
            await service.aclose()
    asyncio.run(run())


def test_partial_nav_does_not_fill_unit_nav_from_adjusted_nav(monkeypatch):
    async def run():
        data = fixtures()
        data[NAV]["item"][0]["unit_nav"] = None
        service, _, _ = setup(monkeypatch, data=data)
        try:
            result = await service.snapshot("040046.OF")
            assert result.nav.latest.unit_nav is None and result.nav.latest.adj_nav == 8.1932
            assert "unit_nav" in result.research.missing and result.estimate.value is None
        finally:
            await service.aclose()
    asyncio.run(run())


def test_cancelled_close_still_drains_source_and_client(monkeypatch):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def source(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"code": 0, "data": fixtures()[SEARCH]})
        service, _, _ = setup(monkeypatch, handler=source)
        reader = asyncio.create_task(service.resolve_fund("040046.OF"))
        await entered.wait()
        closer = asyncio.create_task(service.aclose())
        try:
            await asyncio.sleep(0)
            closer.cancel()
            await asyncio.sleep(0)
            assert not closer.done() and not service._client.is_closed
        finally:
            release.set()
            await reader
            with pytest.raises(asyncio.CancelledError):
                await closer
        assert service._client.is_closed
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())


def test_same_public_body_from_different_queries_is_stored_once(monkeypatch, tmp_path):
    async def run():
        service, _, _ = setup(monkeypatch, evidence_dir=tmp_path)
        try:
            first = await service.search("040046")
            second = await service.search("来源基金")
            assert first.evidence[0].payload_sha256 == second.evidence[0].payload_sha256
            assert first.evidence[0].receipt_id != second.evidence[0].receipt_id
            assert len(list(tmp_path.glob("*.body.json"))) == 1
            assert len(list(tmp_path.glob("*.receipt.json"))) == 2
            assert not list(tmp_path.glob("*.tmp"))
        finally:
            await service.aclose()
    asyncio.run(run())


def test_snapshot_capacity_failure_drains_children_without_starting_fallback(monkeypatch):
    async def run():
        release, loaded = asyncio.Event(), asyncio.Event()
        calls = []
        async def source(request):
            calls.append(request.url.path)
            if request.url.path == SEARCH:
                return httpx.Response(200, json={"code": 0, "data": fixtures()[SEARCH]})
            loaded.set()
            await release.wait()
            return httpx.Response(200, json={"code": 0, "data": fixtures()[request.url.path]})
        service, _, _ = setup(monkeypatch, handler=source)
        service._requests.maxsize = 1
        try:
            with pytest.raises(LocalReadCapacityExceeded):
                await service.snapshot("040046.OF")
            await loaded.wait()
            assert calls == [SEARCH, NAV]
            release.set()
            await service.aclose()
            await asyncio.sleep(0)
            assert asyncio.all_tasks() == {asyncio.current_task()}
        finally:
            release.set()
            await service.aclose()
    asyncio.run(run())
