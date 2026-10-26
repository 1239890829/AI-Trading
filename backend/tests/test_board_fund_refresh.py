"""Board-fund dynamic rows must follow the original input cache lifetime.

Uses the real route, source-list cache, and theme mapping. Supplier transport,
company identity lookup, persisted streaks, and Hub meta are all replaced.
"""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.routes import market_flow
from app.core import ttl_cache
from app.market import board_flow


@pytest.mark.parametrize("input_ttl,in_session", [(30, True), (300, False)])
def test_dynamic_fields_do_not_outlive_original_input_cache(monkeypatch, input_ttl, in_session):
    clock = [0.0]
    generation = {"value": 1}
    source_calls = []
    trace = []
    monkeypatch.setattr(board_flow, "_inflight_source_reads", None, raising=False)
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock[0])
    monkeypatch.setattr(board_flow, "_in_session", lambda: in_session)
    monkeypatch.setattr(
        board_flow, "beijing_now", lambda: datetime(2026, 10, 14, 6, tzinfo=timezone.utc)
    )
    monkeypatch.setattr(board_flow, "_LIST_CACHE", ttl_cache.TTLCache("audit-list", 30, maxsize=4))
    monkeypatch.setattr(board_flow, "_LIST_EOD_CACHE", ttl_cache.TTLCache("audit-eod", 300, maxsize=4))
    monkeypatch.setattr(board_flow, "get_board_streaks", lambda rows: {})
    monkeypatch.setattr(market_flow, "meta_payload", lambda hub: {})
    monkeypatch.setattr(
        market_flow, "main_board",
        lambda groups, codes: {"code": "BK0001", "name": "Fixture", "level": "L2"},
    )

    async def fake_pages(fs, fields):
        source_calls.append({"time": clock[0], "fs": fs, "generation": generation["value"]})
        return [{"f12": "BK0001", "f14": "Fixture", "f3": generation["value"],
                 "f62": generation["value"] * 100_000_000, "f6": 1_000_000_000}], board_flow._HOSTS[0]

    class FakeProvider:
        async def get_company_profile(self, symbol):
            return {"board_groups": {}, "board_codes": {}}

    monkeypatch.setattr(board_flow, "_clist_pages", fake_pages)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    hub = SimpleNamespace(provider=FakeProvider())

    async def read_route(label):
        result = await market_flow.market_board_fund_by_symbols(request, "600001", hub)
        value = result["data"]["boards"]["600001"]["main_net_yi"]
        trace.append({"label": label, "time": clock[0], "value": value,
                      "supplier_generation": generation["value"]})
        return value

    async def run():
        # Prime the actual 30s/300s source cache before a later route cache miss.
        for kind in ("industry", "concept"):
            await board_flow.get_board_list(kind)
        clock[0] = input_ttl - 1
        assert await read_route("outer_filled_from_old_input") == 1
        legacy_outer_loaded_at = clock[0]
        # Another existing consumer legitimately refreshes the expired input.
        clock[0] = input_ttl + 40
        generation["value"] = 2
        for kind in ("industry", "concept"):
            rows, errors = await board_flow.get_board_list(kind)
            assert rows[0]["main_net_yi"] == 2 and not errors
        clock[0] = input_ttl + 58
        before_outer_expiry = await read_route("input_current_but_outer_old")
        # Demonstrate the exact independent outer expiry as well.
        clock[0] = input_ttl + 59
        assert await read_route("outer_expires") == 2
        assert len(source_calls) == 4  # Two kinds, once at t0 and once after source expiry.
        assert {item["time"] for item in source_calls} == {0.0, input_ttl + 40}
        print(json.dumps({"input_ttl": input_ttl, "input_v1_loaded_at": 0,
                          "legacy_outer_loaded_at": legacy_outer_loaded_at,
                          "legacy_outer_expires_at": legacy_outer_loaded_at + 60,
                          "old_input_age_when_returned": input_ttl + 58,
                          "current_input_loaded_at": input_ttl + 40,
                          "source_calls": source_calls, "trace": trace}, sort_keys=True))
        assert before_outer_expiry == 2, (
            "A current input generation is already cached, but the outer route retains dynamic "
            f"generation={before_outer_expiry} until {input_ttl + 59}s; old acquisition age="
            f"{input_ttl + 58}s. This is acquisition age, not an asserted vendor source-clock age."
        )

    asyncio.run(run())


def _route_fixture(monkeypatch, provider):
    from app.services import theme_service

    ref = {"code": "BK0001", "name": "Fixture", "level": "industry"}
    row = {"board_code": "BK0001", "name": "Fixture", "kind": "industry",
           "change_pct": 1.0, "main_net_yi": 2.0, "main_net_ratio": 3.0, "streak": None}

    async def board_rows(codes):
        return {"BK0001": row} if "BK0001" in codes else {}

    monkeypatch.setattr(theme_service, "board_rows_for_codes", board_rows)
    monkeypatch.setattr(market_flow, "main_board", lambda groups, codes: groups.get("ref"))
    monkeypatch.setattr(market_flow, "meta_payload", lambda hub: {})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    return request, SimpleNamespace(provider=provider), ref, row


def test_overlapping_batches_share_one_profile_read(monkeypatch):
    calls = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Provider:
            async def get_company_profile(self, symbol):
                calls.append(symbol)
                if symbol == "600001":
                    entered.set()
                    await release.wait()
                return {"board_groups": {"ref": ref}}

        request, hub, ref, _ = _route_fixture(monkeypatch, Provider())
        first = asyncio.create_task(market_flow.market_board_fund_by_symbols(request, "600001,600002", hub))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            second = asyncio.create_task(market_flow.market_board_fund_by_symbols(request, "600001", hub))
            await asyncio.sleep(0)
            release.set()
            one, two = await asyncio.wait_for(asyncio.gather(first, second), 1)
            assert sorted(calls) == ["600001", "600002"]
            assert set(one["data"]["boards"]) == {"600001", "600002"}
            assert set(two["data"]["boards"]) == {"600001"}
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("initial", ["empty", "exception"])
def test_missing_identity_shares_only_short_negative_cache(monkeypatch, initial):
    clock = [0.0]
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock[0])
    calls = []

    class Provider:
        async def get_company_profile(self, symbol):
            calls.append(symbol)
            if len(calls) == 1:
                if initial == "exception":
                    raise RuntimeError("isolated profile failure")
                return {"board_groups": {}}
            return {"board_groups": {"ref": ref}}

    request, hub, ref, _ = _route_fixture(monkeypatch, Provider())

    async def scenario():
        for symbols in ("600001", "600001,600001"):
            result = await market_flow.market_board_fund_by_symbols(request, symbols, hub)
            assert result["data"]["boards"] == {} and result["data"]["note"]
        assert calls == ["600001"]
        clock[0] = 59.9
        assert not (await market_flow.market_board_fund_by_symbols(request, "600001", hub))["data"]["boards"]
        assert calls == ["600001"]
        clock[0] = 60.0
        result = await market_flow.market_board_fund_by_symbols(request, "600001", hub)
        assert result["data"]["boards"]["600001"]["board_code"] == "BK0001"
        clock[0] = 60.0 + 6 * 3600 - 0.1
        await market_flow.market_board_fund_by_symbols(request, "600001", hub)
        assert calls == ["600001", "600001"]
        clock[0] += 0.1
        await market_flow.market_board_fund_by_symbols(request, "600001", hub)
        assert calls == ["600001", "600001", "600001"]

    asyncio.run(scenario())


def test_cancelled_profile_read_releases_singleflight_for_retry(monkeypatch):
    calls = []

    async def scenario():
        entered, cancelled = asyncio.Event(), asyncio.Event()

        class Provider:
            async def get_company_profile(self, symbol):
                calls.append(symbol)
                if len(calls) == 1:
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
                return {"board_groups": {"ref": ref}}

        request, hub, ref, _ = _route_fixture(monkeypatch, Provider())
        first = asyncio.create_task(market_flow.market_board_fund_by_symbols(request, "600001", hub))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert cancelled.is_set()
            result = await asyncio.wait_for(market_flow.market_board_fund_by_symbols(request, "600001", hub), 1)
            assert result["data"]["boards"]["600001"]["board_code"] == "BK0001"
            assert calls == ["600001", "600001"]
        finally:
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)

    asyncio.run(scenario())


def test_response_mutations_do_not_change_identity_or_dynamic_source_row(monkeypatch):
    class Provider:
        async def get_company_profile(self, symbol):
            return {"board_groups": {"ref": ref}}

    request, hub, ref, row = _route_fixture(monkeypatch, Provider())

    async def scenario():
        first = await market_flow.market_board_fund_by_symbols(request, "600001", hub)
        first["data"]["boards"]["600001"].update(board_name="Changed", main_net_yi=999)
        assert row["main_net_yi"] == 2 and ref["name"] == "Fixture"
        row["main_net_yi"] = 4
        second = await market_flow.market_board_fund_by_symbols(request, "600001", hub)
        assert second["data"]["boards"]["600001"]["main_net_yi"] == 4
        assert second["data"]["boards"]["600001"]["board_name"] == "Fixture"
        assert row["main_net_yi"] == 4

    asyncio.run(scenario())
