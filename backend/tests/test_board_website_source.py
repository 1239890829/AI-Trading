"""Official website funds: complete identities, raw units, clocks and read owner."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime

import httpx
import pytest

from app.core.bjtime import BJ_TZ
from app.core.inflight_reads import close_inflight_reads
from app.core.ttl_cache import TTLCache
from app.market import board_flow as bf
from app.market import _board_website_source as source

NOW = datetime(2026, 10, 27, 8, 30, tzinfo=BJ_TZ)
QUOTE = datetime(2026, 10, 26, 15, 0, tzinfo=BJ_TZ)
DIRECTORY = {"concept": {"BK1184": "人形机器人"}, "industry": {"BK0481": "汽车零部件"},
             "region": {"BK0149": "安徽板块"}}
DIRECTORY_HTML = '''<ul class="pop_slider"><li>行业</li><li>概念</li><li>地域</li></ul>
<div class="pop-cont"><a href="/bkzj/BK0481.html">汽车零部件</a></div>
<div class="pop-cont"><a href="/bkzj/BK1184.html">人形机器人</a></div>
<div class="pop-cont"><a href="/bkzj/BK0149.html">安徽板块</a></div>'''


def board_row(**updates):
    row = {"f12": "BK1184", "f13": 90, "f14": "人形机器人", "f62": 2632463616,
           "f2": 159807, "f152": 2, "f3": -80, "f124": int(QUOTE.timestamp())}
    return {**row, **updates}


def member_row(**updates):
    return board_row(**{"f12": "300476", "f13": 0, "f14": "胜宏科技", "f62": 725201328,
                        "f2": 18801, "f3": 247, **updates})


def payload(rows=None, **updates):
    rows = [board_row()] if rows is None else rows
    return {"rc": 0, "full": 1, "data": {"total": len(rows), "diff": rows}, **updates}


def decode(value, **kwargs):
    return source.decode_response(value, DIRECTORY, "concept", NOW, **kwargs)


def test_full_raw_money_quote_scaling_and_unknown_funds_clock():
    out = decode(payload())
    assert out["available"] and out["coverage_state"] == "source_complete"
    assert out["source_total"] == out["source_returned"] == out["directory_total"] == 1
    assert out["coverage"] == {"total": 1, "returned": 1, "complete": True}
    assert out["source"] == "eastmoney_official_funds" and out["range"] == "website"
    assert out["source_as_of"] is out["as_of"] is None
    assert out["quality"] == "stale" and out["observation_state"] == "clock_unknown"
    assert out["received_at"] == NOW.isoformat()
    row = out["rows"][0]
    assert row["main_net_yi"] == 26.32463616  # No two-decimal rounding.
    assert row["price"] == 1598.07 and row["change_pct"] == -0.8
    assert row["quote_source_as_of"] == out["quote_source_as_of"] == QUOTE.isoformat()
    assert row["main_net_ratio"] is row["super_net_yi"] is row["streak"] is None


def test_complete_members_are_not_cut_to_original_top20():
    members = [member_row()]
    members += [member_row(f12=f"{i:06d}", f14=f"成员{i}", f62=-i) for i in range(30)]
    out = decode(payload(members), board_code="BK1184")
    assert out["board_code"] == "BK1184" and out["name"] == "人形机器人"
    assert len(out["rows"]) == out["source_total"] == 31
    assert out["rows"][0]["price"] == 188.01 and out["rows"][0]["change_pct"] == 2.47
    assert out["rows"][0]["main_net_yi"] == 7.25201328
    assert out["rows"][0]["big_net_yi"] is None


@pytest.mark.parametrize("case", ["truncated", "duplicate", "foreign_code", "wrong_kind", "wrong_name",
                                 "wrong_market", "rc_bool", "full_bool", "not_full", "total_bool",
                                 "oversized_total", "diff_object"])
def test_incomplete_or_mismatched_board_identity_fails(case):
    value = payload()
    if case == "truncated":
        value["data"]["total"] = 2
    elif case == "duplicate":
        value = payload([board_row(), board_row()])
    elif case == "foreign_code":
        value = payload([board_row(f12="BK9999")])
    elif case == "wrong_kind":
        value = payload([board_row(f12="BK0481", f14="汽车零部件")])
    elif case == "wrong_name":
        value = payload([board_row(f14="同名线索")])
    elif case == "wrong_market":
        value = payload([board_row(f13=0)])
    elif case == "rc_bool":
        value["rc"] = False
    elif case == "full_bool":
        value["full"] = True
    elif case == "not_full":
        value["full"] = 0
    elif case == "total_bool":
        value["data"]["total"] = True
    elif case == "oversized_total":
        value["data"]["total"] = source.MAX_ROWS + 1
    elif case == "diff_object":
        value["data"]["diff"] = {"0": board_row()}
    with pytest.raises(ValueError):
        decode(value)


@pytest.mark.parametrize("field,value", [("f62", True), ("f62", float("inf")), ("f3", float("nan")),
                                        ("f2", False), ("f152", True), ("f124", "Infinity")])
def test_nonfinite_and_boolean_source_numbers_are_rejected(field, value):
    with pytest.raises(ValueError):
        decode(payload([board_row(**{field: value})]))


@pytest.mark.parametrize("precision", [None, "-", -1, 9, 2.5])
def test_price_requires_valid_source_precision(precision):
    out = decode(payload([board_row(f152=precision)]))
    assert out["rows"][0]["price"] is None
    assert out["rows"][0]["main_net_yi"] == 26.32463616


def test_null_source_values_remain_unknown_and_future_quote_clock_not_used():
    row = board_row(f62=None, f2="-", f3=None, f124=int(NOW.timestamp()) + 1)
    out = decode(payload([row]))
    assert out["rows"][0]["main_net_yi"] is out["rows"][0]["price"] is None
    assert out["rows"][0]["change_pct"] is out["rows"][0]["quote_source_as_of"] is None
    assert out["quote_source_as_of"] is out["source_as_of"] is out["as_of"] is None


@pytest.mark.parametrize("price", [0, -1])
def test_nonpositive_quote_is_unknown_but_zero_funds_is_valid(price):
    row = decode(payload([board_row(f2=price, f62=0, f124=None)]))["rows"][0]
    assert row["price"] is None and row["quote_source_as_of"] is None
    assert row["main_net_yi"] == 0


def test_name_never_substitutes_for_requested_kind_and_member_identity():
    with pytest.raises(ValueError, match="正式类别"):
        decode(payload([member_row()]), board_code="BK0481")
    with pytest.raises(ValueError):
        decode(payload([member_row(), member_row()]), board_code="BK1184")
    with pytest.raises(ValueError):
        decode(payload([member_row(f12="BK1184")]), board_code="BK1184")
    assert decode(payload([]), board_code="BK1184")["coverage"]["complete"]


def test_actual_member_single_field_market_mutation_is_rejected():
    # Captured public row: 300476 胜宏科技 is source market 0, price 18801 with
    # precision 2 and f62 725201328. Only f13 is changed in this counterexample.
    original = payload([member_row()])
    baseline = decode(original, board_code="BK1184")
    assert baseline["rows"][0]["source_security_id"] == "0.300476"
    mutated = deepcopy(original)
    mutated["data"]["diff"][0]["f13"] = 1
    with pytest.raises(ValueError, match="来源市场身份"):
        decode(mutated, board_code="BK1184")
    mutated["data"]["diff"][0]["f13"] = 0
    assert mutated == original


@pytest.mark.parametrize("symbol,market", [("600000", 1), ("000559", 0), ("301001", 0),
                                          ("920001", 0), ("830001", 0), ("430001", 0)])
def test_stock_market_identity_and_beijing_funds_are_retained(symbol, market):
    row = decode(payload([member_row(f12=symbol, f13=market)]), board_code="BK1184")["rows"][0]
    assert row["source_security_id"] == f"{market}.{symbol}"
    assert row["share_class"] == "A" and row["market_identity_state"] == "verified"
    assert row["symbol"] == symbol and row["main_net_yi"] == 7.25201328


@pytest.mark.parametrize("symbol,market", [("600000", 0), ("920001", 1), ("830001", 1),
                                          ("430001", 1), ("200001", 1), ("900001", 0)])
def test_known_code_market_conflict_rejects_complete_response(symbol, market):
    with pytest.raises(ValueError):
        decode(payload([member_row(), member_row(f12=symbol, f13=market)]), board_code="BK1184")


@pytest.mark.parametrize("symbol,market,share_class,state", [("200001", 0, "B", "verified"),
    ("900001", 1, "B", "verified"), ("700001", 1, None, "unsupported"), ("100001", 0, None, "unsupported")])
def test_b_shares_and_unknown_prefix_keep_original_identity_without_dropping_rows(symbol, market, share_class, state):
    out = decode(payload([member_row(), member_row(f12=symbol, f13=market)]), board_code="BK1184")
    assert out["source_returned"] == out["source_total"] == len(out["rows"]) == 2
    row = next(row for row in out["rows"] if row["symbol"] == symbol)
    assert row["source_security_id"] == f"{market}.{symbol}"
    assert row["share_class"] == share_class and row["market_identity_state"] == state
    assert row["main_net_yi"] == 7.25201328


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(bf, "beijing_now", lambda: NOW)
    monkeypatch.setattr(bf, "_inflight_source_reads", None, raising=False)
    for name, size in [("_WEBSITE_DIRECTORY_CACHE", 1), ("_WEBSITE_BOARD_CACHE", 2), ("_WEBSITE_MEMBER_CACHE", 8)]:
        monkeypatch.setattr(bf, name, TTLCache("test-" + name, 60, maxsize=size))


def test_owner_cache_copy_fresh_and_complete_member_request(monkeypatch, isolated):
    async def run():
        calls = []
        async def handler(request):
            calls.append(request)
            if request.url.path.endswith(".html"):
                return httpx.Response(200, text=DIRECTORY_HTML)
            code = request.url.params["code"]
            return httpx.Response(200, json=payload([member_row()] if code.startswith("b:") else None))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            monkeypatch.setattr(bf, "_http", lambda: client)
            first = await bf.get_board_fund_flow("concept", "website")
            assert len(calls) == 2
            first["rows"][0]["name"] = "changed consumer"
            second = await bf.get_board_fund_flow("concept", "website")
            assert second["rows"][0]["name"] == "人形机器人" and len(calls) == 2
            await bf.get_board_fund_flow("concept", "website", fresh=True)
            assert len(calls) == 4
            members = await bf.get_official_website_members("BK1184", kind="concept")
            assert members["available"] and len(calls) == 5
            assert calls[-1].url.params["code"] == "b:BK1184"
            assert calls[-1].url.params["key"] == "f62,f3,f2,f124,f152"
            assert "cookie" not in calls[-1].headers
            await bf.get_official_website_members("BK1184", kind="concept", fresh=True)
            assert len(calls) == 7
            await close_inflight_reads(bf)
    asyncio.run(run())


def test_same_key_concurrent_reads_and_shared_directory_are_single_flight(monkeypatch, isolated):
    async def run():
        calls = []
        async def handler(request):
            calls.append(request)
            await asyncio.sleep(0)
            if request.url.path.endswith(".html"):
                return httpx.Response(200, text=DIRECTORY_HTML)
            row = board_row() if request.url.params["code"] == "m:90+t:3" else board_row(f12="BK0481", f14="汽车零部件")
            return httpx.Response(200, json=payload([row]))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            monkeypatch.setattr(bf, "_http", lambda: client)
            results = await asyncio.gather(*(bf.get_board_fund_flow("concept", "website") for _ in range(12)),
                                           bf.get_board_fund_flow("industry", "website"))
            assert all(x["available"] for x in results)
            assert len(calls) == 3  # One shared directory plus two complete source reads.
            await close_inflight_reads(bf)
    asyncio.run(run())


def test_expired_success_and_failed_fresh_never_reappear_as_success(monkeypatch, isolated):
    async def run():
        state = {"fail": False, "fund_calls": 0}
        async def handler(request):
            if request.url.path.endswith(".html"):
                return httpx.Response(200, text=DIRECTORY_HTML)
            state["fund_calls"] += 1
            if state["fail"]:
                return httpx.Response(403)
            return httpx.Response(200, json=payload())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            monkeypatch.setattr(bf, "_http", lambda: client)
            out = await bf.get_board_fund_flow("concept", "website")
            key = (NOW.date().isoformat(), "concept")
            bf._WEBSITE_BOARD_CACHE.set(key, deepcopy(out), ttl=0)
            state["fail"] = True
            failed = await bf.get_board_fund_flow("concept", "website")
            assert not failed["available"] and failed["rows"] == []
            assert failed["received_at"] is None and failed["source_total"] is None
            assert state["fund_calls"] == 2
            assert not (await bf.get_board_fund_flow("concept", "website"))["available"]
            assert state["fund_calls"] == 2  # Explicit failure backoff, never a success.
            state["fail"] = False
            assert (await bf.get_board_fund_flow("concept", "website", fresh=True))["available"]
            state["fail"] = True
            fresh_failed = await bf.get_board_fund_flow("concept", "website", fresh=True)
            assert not fresh_failed["available"]
            assert not (await bf.get_board_fund_flow("concept", "website"))["available"]
            assert state["fund_calls"] == 4
            bf._WEBSITE_BOARD_CACHE.set(key, fresh_failed, ttl=0)
            assert not (await bf.get_board_fund_flow("concept", "website"))["available"]
            assert state["fund_calls"] == 5  # An expired failure causes a real read, not success.
            await close_inflight_reads(bf)
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["directory_refused", "directory_wrong_kind", "truncated", "too_large"])
def test_owner_rejects_bad_source_without_fallback_or_persistence(monkeypatch, isolated, failure):
    async def run():
        calls = []
        async def handler(request):
            calls.append(request)
            if request.url.path.endswith(".html"):
                return httpx.Response(403) if failure == "directory_refused" else httpx.Response(200, text=DIRECTORY_HTML)
            if failure == "too_large":
                return httpx.Response(200, content=b" " * (source.MAX_BYTES + 1))
            value = payload()
            value["data"]["total"] = 2
            return httpx.Response(200, json=value)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            monkeypatch.setattr(bf, "_http", lambda: client)
            if failure == "directory_wrong_kind":
                out = await bf.get_official_website_members("BK0481", kind="concept")
                assert len(calls) == 1  # Known wrong category stops before funds request.
            else:
                out = await bf.get_board_fund_flow("concept", "website")
                assert len(calls) == (1 if failure == "directory_refused" else 2)
            assert not out["available"] and out["coverage"]["complete"] is False
            assert out["rows"] == [] and out["source_as_of"] is None
            await close_inflight_reads(bf)
    asyncio.run(run())


def test_disconnected_waiter_and_shutdown_drain_existing_read_owner(monkeypatch, isolated):
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()
        async def handler(request):
            if request.url.path.endswith(".html"):
                return httpx.Response(200, text=DIRECTORY_HTML)
            started.set()
            await finish.wait()
            return httpx.Response(200, json=payload())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            monkeypatch.setattr(bf, "_http", lambda: client)
            waiter = asyncio.create_task(bf.get_board_fund_flow("concept", "website"))
            await started.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            close = asyncio.create_task(close_inflight_reads(bf))
            await asyncio.sleep(0)
            assert not close.done()
            finish.set()
            await close
            assert not bf._inflight_source_reads.tasks
            assert bf._WEBSITE_BOARD_CACHE.get((NOW.date().isoformat(), "concept"))[0]
    asyncio.run(run())
