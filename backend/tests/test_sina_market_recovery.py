"""Source-page repair uses one bounded generation and never publishes a partial market."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from time import monotonic

import httpx
import pytest

from app.market import sina_market as sm
from app.data_providers.eastmoney import ProviderError
from app.services import snapshot_service as ss
from app.core import freshness
from app.schemas.market import Quote


def raw_row(index):
    code = str(600000 + index)
    return {"symbol": "sh" + code, "code": code, "name": f"S{index}", "trade": "10",
            "settlement": "10", "changepercent": "0", "volume": "100", "amount": "1000",
            "ticktime": "14:53:20"}


def patch_http(monkeypatch, handler):
    client = httpx.AsyncClient
    created = []

    def factory(**kwargs):
        instance = client(transport=httpx.MockTransport(handler), **kwargs)
        created.append(instance)
        return instance

    monkeypatch.setattr(sm.httpx, "AsyncClient", factory)
    return created


@pytest.mark.parametrize("failure", ["empty", "timeout"])
def test_one_failed_page_is_retried_once_without_fetching_good_pages_again(monkeypatch, failure):
    calls = Counter()

    async def handler(request):
        assert request.url.host == "vip.stock.finance.sina.com.cn"
        if request.url.path.endswith("getHQNodeStockCount"):
            calls["count"] += 1
            return httpx.Response(200, text='"5"')
        page = int(request.url.params["page"])
        calls[page] += 1
        if page == 2 and calls[page] == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("offline interrupted page", request=request)
            return httpx.Response(200, json=[])
        first = (page - 1) * 2
        return httpx.Response(200, json=[raw_row(i) for i in range(first, min(first + 2, 5))])

    patch_http(monkeypatch, handler)
    rows = asyncio.run(sm.fetch_market_snapshot(page_size=2, concurrency=2))
    assert [row["symbol"] for row in rows] == [str(600000 + i) for i in range(5)]
    assert calls == {"count": 1, 1: 1, 2: 2, 3: 1}
    assert all(row["ticktime"] == "14:53:20" and row["source"] == "sina_market" for row in rows)


def test_observed_rate_limit_survives_a_slow_peer_at_the_round_deadline(monkeypatch):
    async def check():
        cancelled = []
        async def handler(request):
            if request.url.path.endswith("getHQNodeStockCount"):
                return httpx.Response(200, text='"2"')
            if request.url.params["page"] == "1":
                return httpx.Response(456)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        clients = patch_http(monkeypatch, handler)
        with pytest.raises(sm.SinaRateLimited):
            await asyncio.wait_for(sm.fetch_market_snapshot(page_size=1, deadline=monotonic() + 0.03), 1)
        assert cancelled == [True] and all(client.is_closed for client in clients)
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(check())


@pytest.mark.parametrize("bad", ["json", "nonlist", "challenge", "identity", "duplicate", "overlong_tail"])
def test_bad_contract_and_count_drift_never_retry_or_publish(monkeypatch, bad):
    calls = Counter()
    async def handler(request):
        if request.url.path.endswith("getHQNodeStockCount"):
            return httpx.Response(200, text='"3"')
        page = int(request.url.params["page"])
        calls[page] += 1
        if page == 1:
            return httpx.Response(200, json=[raw_row(0), raw_row(1)])
        if bad == "json":
            return httpx.Response(200, text="[invalid")
        if bad == "nonlist":
            return httpx.Response(200, json={"error": "unavailable"})
        if bad == "challenge":
            return httpx.Response(200, text="<html>verification required</html>")
        row = raw_row(2)
        if bad == "identity":
            row["code"] = "600099"  # Cannot launder the original sh600002 identity.
        body = [row, row] if bad == "duplicate" else [row, raw_row(3)] if bad == "overlong_tail" else [row]
        return httpx.Response(200, json=body)
    patch_http(monkeypatch, handler)
    with pytest.raises(ProviderError):
        asyncio.run(sm.fetch_market_snapshot(page_size=2))
    assert calls == {1: 1, 2: 1}


@pytest.mark.parametrize("status", [401, 403, 429, 456, 500])
def test_any_http_rejection_stops_other_short_pages_from_retrying(monkeypatch, status):
    calls = Counter()
    async def handler(request):
        if request.url.path.endswith("getHQNodeStockCount"):
            return httpx.Response(200, text='"2"')
        page = int(request.url.params["page"])
        calls[page] += 1
        return httpx.Response(200, json=[]) if page == 1 else httpx.Response(status)
    patch_http(monkeypatch, handler)
    expected = sm.SinaRateLimited if status == 456 else ProviderError
    with pytest.raises(expected):
        asyncio.run(sm.fetch_market_snapshot(page_size=1))
    assert calls == {1: 1, 2: 1}


def test_persistently_short_page_is_read_at_most_twice(monkeypatch):
    calls = Counter()
    async def handler(request):
        if request.url.path.endswith("getHQNodeStockCount"):
            return httpx.Response(200, text='"2"')
        page = int(request.url.params["page"])
        calls[page] += 1
        return httpx.Response(200, json=[raw_row(0)] if page == 1 else [])
    patch_http(monkeypatch, handler)
    with pytest.raises(ProviderError, match="page 2.*expected_page=1.*expected=2"):
        asyncio.run(sm.fetch_market_snapshot(page_size=1))
    assert calls == {1: 1, 2: 2}


def test_duplicate_across_full_pages_cannot_pass_global_identity_guard(monkeypatch):
    calls = Counter()
    async def handler(request):
        if request.url.path.endswith("getHQNodeStockCount"):
            return httpx.Response(200, text='"2"')
        calls[int(request.url.params["page"])] += 1
        return httpx.Response(200, json=[raw_row(0)])
    patch_http(monkeypatch, handler)
    with pytest.raises(ProviderError, match="rows=2 unique=1 expected=2"):
        asyncio.run(sm.fetch_market_snapshot(page_size=1))
    assert calls == {1: 1, 2: 1}


def test_retry_allowance_and_concurrency_are_bounded_by_one_six_page_wave(monkeypatch):
    async def check():
        calls, active, maximum = Counter(), 0, 0
        async def handler(request):
            nonlocal active, maximum
            if request.url.path.endswith("getHQNodeStockCount"):
                return httpx.Response(200, text='"13"')
            page = int(request.url.params["page"])
            calls[page] += 1
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(0)
                return httpx.Response(200, json=[] if page <= 7 and calls[page] == 1 else [raw_row(page)])
            finally:
                active -= 1
        clients = patch_http(monkeypatch, handler)
        with pytest.raises(ProviderError):
            await sm.fetch_market_snapshot(page_size=1)
        assert calls == {**{p: 2 for p in range(1, 7)}, **{p: 1 for p in range(7, 13)}}
        assert sum(calls.values()) == 18 and maximum == 6 and active == 0
        assert all(client.is_closed for client in clients)
    asyncio.run(check())


def test_individual_slow_page_is_drained_before_its_single_retry(monkeypatch):
    async def check():
        calls, cancelled = Counter(), []
        async def handler(request):
            if request.url.path.endswith("getHQNodeStockCount"):
                return httpx.Response(200, text='"2"')
            page = int(request.url.params["page"])
            calls[page] += 1
            if page == 2 and calls[page] == 1:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(True)
            if page == 2:
                assert cancelled == [True]
            return httpx.Response(200, json=[raw_row(page)])
        clients = patch_http(monkeypatch, handler)
        rows = await asyncio.wait_for(sm.fetch_market_snapshot(page_size=1, timeout=0.02,
            deadline=monotonic() + 0.5), 1)
        assert len(rows) == 2 and calls == {1: 1, 2: 2}
        assert all(client.is_closed for client in clients)
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(check())


def test_original_deadline_includes_count_and_does_not_start_pages_after_expiry(monkeypatch):
    async def check():
        calls, cancelled = [], []
        async def handler(request):
            calls.append(request.url.path)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        clients = patch_http(monkeypatch, handler)
        with pytest.raises(sm.SinaSnapshotDeadline):
            await asyncio.wait_for(sm.fetch_market_snapshot(deadline=monotonic() + 0.02), 1)
        assert len(calls) == 1 and calls[0].endswith("getHQNodeStockCount")
        assert cancelled == [True] and all(client.is_closed for client in clients)
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(check())


def test_expired_original_slot_never_restarts_a_budget_for_repair(monkeypatch):
    async def check():
        clock, calls = [100.0], Counter()
        monkeypatch.setattr(sm, "monotonic", lambda: clock[0])
        async def handler(request):
            if request.url.path.endswith("getHQNodeStockCount"):
                return httpx.Response(200, text='"2"')
            page = int(request.url.params["page"])
            calls[page] += 1
            if page == 2:
                clock[0] = 101.0
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[raw_row(0)])
        patch_http(monkeypatch, handler)
        with pytest.raises(sm.SinaSnapshotDeadline):
            await sm.fetch_market_snapshot(page_size=1, deadline=101.0)
        assert calls == {1: 1, 2: 1}
    asyncio.run(check())


def test_external_cancellation_drains_all_owned_page_requests(monkeypatch):
    async def check():
        entered, calls, cancelled = asyncio.Event(), [], []
        async def handler(request):
            if request.url.path.endswith("getHQNodeStockCount"):
                return httpx.Response(200, text='"6"')
            page = int(request.url.params["page"])
            calls.append(page)
            if len(calls) == 6:
                entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(page)
        clients = patch_http(monkeypatch, handler)
        task = asyncio.create_task(sm.fetch_market_snapshot(page_size=1))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        results = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert sorted(calls) == sorted(cancelled) == list(range(1, 7))
        assert all(client.is_closed for client in clients)
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(check())


def test_service_supplies_one_poll_deadline_and_rejects_a_late_reader(monkeypatch, tmp_path):
    async def check():
        clock, deadlines = [100.0], []
        monkeypatch.setattr(ss, "monotonic", lambda: clock[0])
        svc = ss.MarketSnapshotService(60, 300, tmp_path)
        old_at = datetime.fromisoformat("2026-10-27T06:50:00+00:00")
        old_rows = [{"symbol": "600105", "market": "SH", "change_pct": 0, "source": "sina_market"}]
        svc.snapshot = old_rows
        svc.last_success = old_at
        svc._snapshot_version = (old_at, tuple(dict(row) for row in old_rows))
        before = svc.versioned_snapshot()
        async def late(*, deadline):
            deadlines.append(deadline)
            clock[0] = 161.0
            return [{"symbol": "600000", "market": "SH", "change_pct": 1}]
        monkeypatch.setattr(sm, "fetch_market_snapshot", late)
        monkeypatch.setattr(svc, "_maybe_save", lambda: pytest.fail("late collection must not save"))
        with pytest.raises(sm.SinaSnapshotDeadline):
            await svc.refresh()
        assert deadlines == [160.0] and svc.versioned_snapshot() == before
        assert svc.snapshot == old_rows and svc.last_success == old_at and svc.saved_files == 0
    asyncio.run(check())


def test_verified_stock_identity_is_separate_from_quotes_and_survives_fallback(monkeypatch, tmp_path):
    async def check():
        from app.services import quote_enrich

        now = [datetime.fromisoformat("2026-10-27T14:53:27+08:00").astimezone(timezone.utc)]
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now[0].astimezone(tz) if tz else now[0].replace(tzinfo=None)
        monkeypatch.setattr(ss, "datetime", Clock)
        monkeypatch.setattr(sm, "datetime", Clock)
        monkeypatch.setattr(freshness, "datetime", Clock)
        source_rows = [raw_row(0), {**raw_row(1), "symbol": "sz000001", "code": "000001"},
                       {**raw_row(2), "symbol": "bj920001", "code": "920001"}]
        for row in source_rows:
            row.update(trade="0", ticktime="09:25:00")
        calls = []
        async def handler(request):
            calls.append(request.url.path)
            if request.url.path.endswith("getHQNodeStockCount"):
                return httpx.Response(200, text='"3"')
            return httpx.Response(200, json=source_rows)
        patch_http(monkeypatch, handler)
        svc = ss.MarketSnapshotService(60, 300, tmp_path, quote_hub=object())
        monkeypatch.setattr(svc, "_maybe_save", lambda: None)
        assert svc.stock_universe()["state"] == "unknown"
        await svc.refresh()
        verified = svc.stock_universe()
        assert verified["state"] == "ready" and verified["reason"] is None
        assert verified["source"] == "sina_market" and verified["node"] == "hs_a"
        assert verified["verified_at"] == now[0].isoformat() and verified["trade_date"] == "2026-10-27"
        assert verified["source_total"] == verified["source_returned"] == verified["source_unique"] == 3
        assert len(verified["version"]) == 64
        assert {tuple(sorted(row.items())) for row in verified["identities"]} == {
            tuple(sorted({"symbol": symbol, "market": market}.items()))
            for symbol, market in [("600000", "SH"), ("000001", "SZ"), ("920001", "BJ")]
        }
        assert all(set(row) == {"symbol", "market"} for row in verified["identities"])
        damaged = svc.stock_universe()
        damaged["identities"][0]["symbol"] = "mutated"
        damaged["identities"].clear()
        assert svc.stock_universe() == verified and len(calls) == 2
        async def quotes(_hub, symbols, **kwargs):
            return {symbol: Quote(symbol=symbol, market="SH", price=11, prev_close=10, source="tencent",
                                  data_timestamp=now[0]) for symbol in symbols}
        monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", quotes)
        await svc.refresh_fallback()
        assert svc.last_snapshot_source == "quote_fallback"
        assert svc.stock_universe() == verified and len(calls) == 2
        # Midnight Beijing changes identity validity, independently of live quote seconds.
        now[0] = datetime.fromisoformat("2026-10-28T00:00:01+08:00").astimezone(timezone.utc)
        stale = svc.stock_universe()
        assert stale["state"] == "unknown" and stale["reason"] == "different_beijing_day"
        assert stale["identities"] == [] and stale["version"] == verified["version"]
        assert stale["verified_at"] == verified["verified_at"]
        now[0] = datetime.fromisoformat("2026-10-27T14:53:26+08:00").astimezone(timezone.utc)
        assert svc.stock_universe()["reason"] == "verification_clock_future"
        assert svc.stock_universe()["identities"] == []
    asyncio.run(check())


def test_cold_fallback_cannot_create_verified_hs_a_identity_from_source_strings(monkeypatch, tmp_path):
    async def check():
        from app.services import quote_enrich

        svc = ss.MarketSnapshotService(60, 300, tmp_path, quote_hub=object())
        # Existing memory/durable quote rows do not prove a verified directory read.
        svc.snapshot = [{"symbol": "600000", "market": "SH", "source": "sina_market", "change_pct": 0}]
        monkeypatch.setattr(svc, "_maybe_save", lambda: None)
        async def quotes(_hub, symbols, **kwargs):
            return {symbol: Quote(symbol=symbol, market="SH", price=10, source="tencent",
                                  data_timestamp=datetime.now(timezone.utc)) for symbol in symbols}
        monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", quotes)
        await svc.refresh_fallback()
        unknown = svc.stock_universe()
        assert unknown["state"] == "unknown" and unknown["reason"] == "not_observed"
        assert unknown["source_total"] is None and unknown["source_returned"] == unknown["source_unique"] == 0
        assert unknown["verified_at"] is unknown["version"] is None and unknown["identities"] == []
    asyncio.run(check())


@pytest.mark.parametrize("change", [{"market": None}, {"symbol": "60000X"}, {"source": "tencent_fallback"}])
def test_invalid_normalized_source_cannot_publish_an_identity_or_quote_version(monkeypatch, tmp_path, change):
    async def check():
        svc = ss.MarketSnapshotService(60, 300, tmp_path)
        async def invalid(*, deadline):
            return [{"symbol": "600000", "market": "SH", "source": "sina_market", "change_pct": 0, **change}]
        monkeypatch.setattr(sm, "fetch_market_snapshot", invalid)
        monkeypatch.setattr(svc, "_maybe_save", lambda: pytest.fail("invalid identity must not save"))
        with pytest.raises(ProviderError, match="universe identity invalid"):
            await svc.refresh()
        assert svc.versioned_snapshot() == ([], None)
        assert svc.stock_universe()["state"] == "unknown"
    asyncio.run(check())


@pytest.mark.parametrize("status", [200, 403])
def test_late_response_cannot_turn_a_denial_or_challenge_into_retry_permission(monkeypatch, status):
    async def check():
        calls = Counter()
        async def handler(request):
            if request.url.path.endswith("getHQNodeStockCount"):
                return httpx.Response(200, text='"2"')
            page = int(request.url.params["page"])
            calls[page] += 1
            if page == 1:
                return httpx.Response(200, json=[raw_row(0)])
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return httpx.Response(status, text="<html>verification required</html>")
        clients = patch_http(monkeypatch, handler)
        with pytest.raises(ProviderError):
            await asyncio.wait_for(sm.fetch_market_snapshot(page_size=1, timeout=0.02,
                deadline=monotonic() + 0.5), 1)
        assert calls == {1: 1, 2: 1}
        assert all(client.is_closed for client in clients)
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(check())


def test_short_followup_retains_the_previous_quote_and_identity_versions(monkeypatch, tmp_path):
    async def check():
        good, calls = [True], Counter()
        async def handler(request):
            if request.url.path.endswith("getHQNodeStockCount"):
                calls["count"] += 1
                return httpx.Response(200, text='"3"')
            calls["data"] += 1
            return httpx.Response(200, json=[raw_row(i) for i in range(3 if good[0] else 2)])
        patch_http(monkeypatch, handler)
        svc = ss.MarketSnapshotService(60, 300, tmp_path)
        monkeypatch.setattr(svc, "_maybe_save", lambda: None)
        await svc.refresh()
        quotes, identity = svc.versioned_snapshot(), svc.stock_universe()
        good[0] = False
        with pytest.raises(ProviderError, match="snapshot incomplete"):
            await svc.refresh()
        assert svc.versioned_snapshot() == quotes
        assert svc.stock_universe() == identity
        assert calls == {"count": 2, "data": 3}, "one failed page retries once; no old rows fill the gap"
    asyncio.run(check())
