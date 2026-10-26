"""Offline contracts from Sina public board and member response shapes."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import json

import httpx
import pytest

from app.core.bjtime import BJ_TZ
from app.market import _sina_board_source as source


NOW = datetime(2026, 10, 26, 23, 10, tzinfo=BJ_TZ)


def board(index=0, kind="industry", **changes):
    return {"cate_type": "0" if kind == "industry" else "1", "category": f"new_fixture_{index}",
            "name": f"fixture分类{index}", "avg_changeratio": "0.00566433", "netamount": "100000000",
            "ratioamount": "0.0163633", "ts_symbol": "sh600011", "ts_name": "华能国际", **changes}


def member(index=0, **changes):
    return {"symbol": f"sh{600000 + index}", "name": f"fixture成员{index}", "trade": "7.3500",
            "changeratio": "0.0137931", "netamount": "100000000", "r0_net": "20000000",
            "ratioamount": "0.080794", **changes}


def directory(*, kind="industry", category="new_fixture_0", amount=1):
    return {"available": True, "source": source.SOURCE, "kind": kind,
            "coverage": {"total": 1, "returned": 1, "complete": True},
            "rows": [{"category": category, "name": "fixture分类", "kind": kind, "net_inflow_yi": amount}]}


def daily(**changes):
    return [{"opendate": "2026-10-26", "netamount": "100000000", "r0_net": "20000000",
             "ratioamount": "0.0163633", "r0_ratio": "0.00679844", **changes}]


class Scenario:
    def __init__(self, boards=None, members=None, day=None):
        self.boards = [board()] if boards is None else boards
        self.members = [member()] if members is None else members
        self.day = daily() if day is None else day
        self.calls, self.count_overrides, self.page_overrides = [], {}, {}
        self.count_calls = {}

    async def handler(self, request):
        self.calls.append(request)
        method, params = request.url.path.rsplit("/", 1)[1], request.url.params
        if method in ("MoneyFlow.ssc_bkzj_bk", "MoneyFlow.ssc_bkzj_ssggzj"):
            self.count_calls[method] = self.count_calls.get(method, 0) + 1
            data = self.boards if method.endswith("_bk") else self.members
            value = self.count_overrides.get((method, self.count_calls[method]), str(len(data)))
        elif method == "MoneyFlow.ssl_bkzj_zjlrqs":
            value = self.day
        else:
            data = self.boards if method.endswith("_bk") else self.members
            page = int(params["page"])
            value = self.page_overrides.get((method, page), data[(page - 1) * 100:page * 100])
        # Captured Sina responses are GBK-declared JSON, with escaped names.
        # Do not let MockTransport silently substitute HTTPX's UTF-8 JSON.
        return httpx.Response(200, content=json.dumps(value, ensure_ascii=True).encode("ascii"),
                              headers={"content-type": "application/json; charset=gbk"})

    def run(self, action="boards", *, kind="industry", category="new_fixture_0", known=None, now=lambda: NOW):
        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.handler)) as http:
                if action == "boards":
                    return await source.fetch_boards(http, kind, now)
                return await source.fetch_members(http, kind, category, now, directory=known)
        return asyncio.run(go())


def test_complete_reported_board_directory_preserves_source_category_case_and_units():
    rows = [board(i, "concept") for i in range(181)]
    rows[0]["category"] = "gn_BCdc"
    fixture = Scenario(boards=rows)
    result = fixture.run(kind="concept")
    assert result["coverage"] == {"total": 181, "returned": 181, "complete": True}
    first = result["rows"][0]
    assert first["category"] == "gn_BCdc" and first["kind"] == "concept"
    assert first["net_inflow_yi"] == 1 and first["change_pct"] == pytest.approx(0.566433)
    assert first["net_inflow_ratio_pct"] == pytest.approx(1.63633)
    assert "main_net_yi" not in first and "board_code" not in first
    assert result["source_as_of"] is None and first["source_as_of"] is None
    assert len(result["source_receipts"]) == len(fixture.calls) == 4
    assert all(len(item["sha256"]) == 64 for item in result["source_receipts"])
    assert all(call.url.params["fenlei"] == "1" for call in fixture.calls)


def test_captured_normal_stock_values_scale_as_money_and_fraction_not_f62():
    raw = member(symbol="sh600011", name="华能国际", trade="7.3500", changeratio="0.0137931",
                 netamount="61435180.9300", r0_net="13557680.2500", ratioamount="0.080794")
    result = Scenario(members=[raw], day=daily(netamount="61435180.9300", r0_net="13557680.2500")).run(
        "members", known=directory(amount=0.6143518093))
    item = result["rows"][0]
    assert item["symbol"] == "600011" and item["source_symbol"] == "sh600011"
    assert item["price"] == 7.35 and item["change_pct"] == pytest.approx(1.37931)
    assert item["net_inflow_yi"] == 0.6143518093 and item["r0_net_yi"] == 0.1355768025
    assert item["net_inflow_ratio_pct"] == pytest.approx(8.0794)
    assert "main_net_yi" not in item and item["source_as_of"] is None
    assert result["reconciliation"] == {"directory_net": "matched", "daily_net": "matched", "daily_r0": "matched"}
    day = result["daily_snapshot"]
    assert day["source_day"] == "2026-10-26" and day["source_as_of"] is None
    assert day["r0_ratio_pct"] == pytest.approx(0.679844)


def test_empty_reported_lists_and_members_are_confirmed_empty_not_failure():
    fixture = Scenario(boards=[])
    assert fixture.run()["coverage"] == {"total": 0, "returned": 0, "complete": True}
    fixture = Scenario(members=[])
    result = fixture.run("members", known=directory(amount=0))
    assert result["available"] and result["coverage"] == {"total": 0, "returned": 0, "complete": True}
    assert result["rows"] == []


def test_invalid_or_overflow_counts_never_become_zero_or_start_pages():
    for value in (None, True, 1, "-1", "1.0", "8001", "01", "unknown"):
        fixture = Scenario()
        fixture.count_overrides[("MoneyFlow.ssc_bkzj_bk", 1)] = value
        with pytest.raises(ValueError, match="计数"):
            fixture.run()
        assert len(fixture.calls) == 1


def test_bad_or_repeated_board_identity_rejects_whole_directory():
    for rows in ([board(cate_type="1")], [board(category="../escape")], [board(name=" ")], [board(), board()]):
        with pytest.raises(ValueError):
            Scenario(boards=rows).run()
    fixture = Scenario()
    fixture.count_overrides[("MoneyFlow.ssc_bkzj_bk", 2)] = "2"
    with pytest.raises(ValueError, match="期间改变"):
        fixture.run()


def test_member_pages_are_full_unique_identity_sorted_and_do_not_refetch_supplied_directory():
    fixture = Scenario(members=[member(i) for i in range(201)], day=daily(netamount="20100000000", r0_net="4020000000"))
    known = directory(amount=201)
    original = deepcopy(known)
    result = fixture.run("members", known=known)
    assert known == original
    assert result["coverage"] == {"total": 201, "returned": 201, "complete": True}
    assert result["totals"] == {"total": 201, "net_inflow_yi": 201, "net_known": 201, "r0_net_yi": 40.2, "r0_known": 201}
    assert all("ssggzj" in call.url.path or "zjlrqs" in call.url.path for call in fixture.calls)
    pages = [call for call in fixture.calls if "ssl_bkzj_ssggzj" in call.url.path]
    assert {call.url.params["page"] for call in pages} == {"1", "2", "3"}
    assert all(call.url.params["sort"] == "symbol" and call.url.params["asc"] == "1" and
               call.url.params["bankuai"] == "0/new_fixture_0" and call.url.params["shichang"] == "" for call in pages)


def test_member_category_is_same_source_kind_and_exact_registered_identity():
    for known in ({**directory(), "source": "eastmoney_board_clist"}, directory(kind="concept"),
                  {**directory(), "coverage": {"total": 2, "returned": 1, "complete": False}},
                  directory(category="gn_other")):
        fixture = Scenario()
        with pytest.raises(ValueError):
            fixture.run("members", known=known)
        assert fixture.calls == []
    fixture = Scenario()
    result = fixture.run("members")
    assert result["name"] == "fixture分类0"
    assert any("ssc_bkzj_bk" in call.url.path for call in fixture.calls)


def test_member_count_drift_short_page_duplicate_and_bad_order_never_report_full():
    for mode in ("drift", "short", "duplicate", "order"):
        fixture = Scenario(members=[member(i) for i in range(101)])
        if mode == "drift":
            fixture.count_overrides[("MoneyFlow.ssc_bkzj_ssggzj", 2)] = "102"
        elif mode == "short":
            fixture.page_overrides[("MoneyFlow.ssl_bkzj_ssggzj", 2)] = []
        elif mode == "duplicate":
            fixture.page_overrides[("MoneyFlow.ssl_bkzj_ssggzj", 2)] = [member(0)]
        else:
            fixture.members[0], fixture.members[1] = fixture.members[1], fixture.members[0]
        with pytest.raises(ValueError):
            fixture.run("members", known=directory())


def test_unknown_market_or_invalid_member_identity_is_not_silently_dropped():
    for changes in ({"symbol": "bj920001"}, {"symbol": "sz600000"}, {"symbol": "sh900001"},
                    {"symbol": "600011"}, {"symbol": True}, {"name": " "}):
        with pytest.raises(ValueError):
            Scenario(members=[member(**changes)]).run("members", known=directory())


def test_zero_quote_keeps_inactive_member_and_real_money_but_not_fake_flat_quote():
    result = Scenario(members=[member(trade="0.0000", changeratio="0", ratioamount=None)]).run("members", known=directory())
    item = result["rows"][0]
    assert result["coverage"]["returned"] == 1 and item["price"] is None and item["change_pct"] is None
    assert item["net_inflow_yi"] == 1 and item["r0_net_yi"] == 0.2
    assert item["net_inflow_ratio_pct"] is None and "quote_price_nonpositive" in item["degraded"]


def test_missing_invalid_and_nonfinite_amounts_are_unknown_not_zero_or_full_totals():
    for value in (None, True, "NaN", "Infinity", "1e9999", "not-number"):
        result = Scenario(members=[member(netamount=value, r0_net=value)]).run("members", known=directory())
        item = result["rows"][0]
        assert item["net_inflow_yi"] is None and item["r0_net_yi"] is None
        assert result["totals"]["net_known"] == result["totals"]["r0_known"] == 0
        assert result["totals"]["net_inflow_yi"] is None and result["reconciliation"]["directory_net"] == "unknown"


def test_reconciliation_mismatch_is_explicit_and_not_labeled_same_atomic_batch():
    result = Scenario(day=daily(netamount="200000000", r0_net="50000000")).run("members", known=directory(amount=3))
    assert result["reconciliation"] == {"directory_net": "mismatch", "daily_net": "mismatch", "daily_r0": "mismatch"}
    assert any("非原子同批" in reason for reason in result["degraded"])
    assert result["available"] and result["coverage"]["complete"]


def test_latest_day_label_is_independent_and_invalid_future_or_absent_day_is_not_faked():
    result = Scenario(day=daily(opendate="2026-10-23")).run("members", known=directory())
    assert result["daily_snapshot"]["source_day"] == "2026-10-23" and result["source_as_of"] is None
    assert any("不能据此" in item for item in result["daily_snapshot"]["degraded"])
    for raw in ([], daily(opendate="2026-10-27"), daily(opendate="2026-02-30"), daily(opendate="2026-1-1")):
        result = Scenario(day=raw).run("members", known=directory())
        assert result["available"] and not result["daily_snapshot"]["available"]
        assert result["daily_snapshot"]["source_day"] is None and result["daily_snapshot"]["net_inflow_yi"] is None


def test_daily_http_failure_is_visible_separate_from_complete_membership():
    fixture = Scenario()
    original = fixture.handler
    async def handler(request):
        if request.url.path.endswith("MoneyFlow.ssl_bkzj_zjlrqs"):
            return httpx.Response(503, content=b"source failure")
        return await original(request)
    fixture.handler = handler
    result = fixture.run("members", known=directory())
    assert result["available"] and result["coverage"]["complete"]
    assert not result["daily_snapshot"]["available"] and "HTTPStatusError" in result["daily_snapshot"]["degraded"]


def test_source_body_bound_and_bad_clock_are_not_empty_success():
    calls = []
    async def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"x" * (source.MAX_BODY_BYTES + 1))
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await source.fetch_boards(http, "industry", lambda: NOW)
    with pytest.raises(ValueError, match="512KiB"):
        asyncio.run(go())
    assert len(calls) == 1
    fixture = Scenario()
    with pytest.raises(ValueError, match="时区"):
        fixture.run(now=lambda: NOW.replace(tzinfo=None))
    assert fixture.calls == []


def test_four_page_concurrency_drains_on_caller_cancel_timeout_and_page_failure(monkeypatch):
    async def go(mode):
        fixture, active, peak, entered = Scenario(boards=[board(i) for i in range(601)]), 0, 0, asyncio.Event()
        original = fixture.handler
        async def handler(request):
            nonlocal active, peak
            if not request.url.path.endswith("MoneyFlow.ssl_bkzj_bk"):
                return await original(request)
            active += 1; peak = max(peak, active)
            try:
                entered.set()
                if mode == "success":
                    await asyncio.sleep(0.001)
                    return await original(request)
                if mode == "failure" and request.url.params["page"] == "1":
                    await asyncio.sleep(0)
                    raise httpx.ReadError("fixture page failure")
                await asyncio.Event().wait()
            finally:
                active -= 1
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            task = asyncio.create_task(source.fetch_boards(http, "industry", lambda: NOW))
            await entered.wait()
            if mode == "cancel":
                task.cancel()
            if mode == "success":
                assert (await task)["coverage"]["returned"] == 601
            else:
                expected = {"cancel": asyncio.CancelledError, "timeout": TimeoutError, "failure": httpx.ReadError}[mode]
                with pytest.raises(expected):
                    await task
            assert active == 0 and peak <= 4
            if mode == "success":
                assert peak == 4
    for mode in ("success", "cancel", "failure", "timeout"):
        monkeypatch.setattr(source, "READ_SECONDS", 0.05 if mode == "timeout" else 22)
        asyncio.run(go(mode))


def test_bad_json_or_source_http_failure_propagates_without_publishing_directory():
    async def go(status, content):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, content=content))) as http:
            await source.fetch_boards(http, "industry", lambda: NOW)
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(go(200, b"not-json"))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(go(503, b"unavailable"))


def test_invalid_kind_and_category_never_start_source_calls():
    fixture = Scenario()
    for kind in ("region", "BK0001", None):
        with pytest.raises(ValueError):
            fixture.run(kind=kind)
    for category in ("../path", "0/new_dlhy", "new dlhy", ""):
        with pytest.raises(ValueError):
            fixture.run("members", category=category, known=directory())
    assert fixture.calls == []
