"""Real flow paths with bounded memory sources; no live HTTP, DB, or models."""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.bjtime import BJ_TZ
from app.core.ttl_cache import TTLCache
from app.market import board_flow as bf, fund_flow as ff
from app.market import trade_calendar as tc
from app.picks import watcher as wt


@pytest.fixture()
def board_state(monkeypatch, tmp_path):
    now = datetime(2026, 9, 7, 15, 6, tzinfo=BJ_TZ)
    monkeypatch.setattr(bf, "beijing_now", lambda: now)
    monkeypatch.setattr(bf, "_is_trade_day", lambda _: True)
    monkeypatch.setattr(bf, "_DAILY_STORE", tmp_path / "daily.json")
    monkeypatch.setattr(bf, "_DAYK_STORE", tmp_path / "daykline.json")
    monkeypatch.setattr(bf, "_mem_memo", {"daily": (None, {}), "dayk": (None, {})})
    monkeypatch.setattr(bf, "_MEMBERS_CACHE", TTLCache("fixture-members", 60, maxsize=8))

    async def gather(coros, limit=5):
        return await asyncio.gather(*coros, return_exceptions=True)

    monkeypatch.setattr(bf, "_gather_bounded", gather)
    return tmp_path


def test_member_top20_does_not_fetch_discarded_pages(monkeypatch, board_state):
    calls = []

    class HTTP:
        async def get(self, url, params, headers):
            page, size = int(params["pn"]), int(params["pz"])
            calls.append((url, page))
            rows = [{"f12": str(i).zfill(6), "f14": "fixture", "f62": 1e8 * (401 - i)}
                    for i in range((page - 1) * size, min(page * size, 400))]
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"data": {"total": 400, "diff": rows}})

    monkeypatch.setattr(bf, "_HTTP", HTTP())
    result = asyncio.run(bf.get_board_members("BK0001"))
    assert result["available"] is True and len(result["rows"]) == 20
    assert result["rows"][0]["main_net_yi"] == 401
    assert result["rows"][-1]["symbol"] == "000019"
    assert calls == [(bf._HOSTS[0] + "/api/qt/clist/get", 1)]
    result["rows"][0]["main_net_yi"] = 999
    assert asyncio.run(bf.get_board_members("BK0001"))["rows"][0]["main_net_yi"] == 401


def _snapshot_sources(monkeypatch, *, unavailable=(), missing_members=(), missing_dayk=()):
    calls = Counter()
    faults = {"list": set(unavailable), "members": set(missing_members), "dayk": set(missing_dayk)}

    async def rows(kind):
        calls[("list", kind)] += 1
        if kind in faults["list"]:
            return None, ["fixture source unavailable"]
        code = "BK0001" if kind == "concept" else "BK0002"
        return [{"board_code": code, "name": kind, "kind": kind, "main_net_yi": 3.0}], []

    async def members(code):
        calls[("members", code)] += 1
        return {"available": code not in faults["members"], "rows": [] if code in faults["members"] else [
            {"symbol": "600001", "name": "fixture", "main_net_yi": 3.0, "main_net_ratio": None}]
        }

    async def dayk(code):
        calls[("dayk", code)] += 1
        return None if code in faults["dayk"] else [["2026-09-07", 3.0, None]]

    monkeypatch.setattr(bf, "get_board_list", rows)
    monkeypatch.setattr(bf, "get_board_members", members)
    monkeypatch.setattr(bf, "_fetch_board_dayk", dayk)
    return calls, faults


def test_failed_snapshot_is_not_saved_as_completed_and_recovers(monkeypatch, board_state):
    calls, faults = _snapshot_sources(monkeypatch, unavailable=("concept", "industry"))
    assert asyncio.run(bf.snapshot_daily_if_closed()) is False
    assert not (board_state / "daily.json").exists()
    faults["list"].clear()
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    saved = json.loads((board_state / "daily.json").read_text())["days"]["2026-09-07"]
    assert set(saved["ranks"]) == {"concept", "industry"}
    assert asyncio.run(bf.snapshot_daily_if_closed()) is False
    assert calls[("list", "concept")] == calls[("list", "industry")] == 2


def test_partial_snapshot_preserves_completed_kind_and_repairs_missing_kind(monkeypatch, board_state):
    calls, faults = _snapshot_sources(monkeypatch, unavailable=("industry",))
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    first = json.loads((board_state / "daily.json").read_text())["days"]["2026-09-07"]
    faults["list"].clear()
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    second = json.loads((board_state / "daily.json").read_text())["days"]["2026-09-07"]
    assert second["boards"]["concept"] == first["boards"]["concept"]
    assert calls[("list", "concept")] == 1 and calls[("members", "BK0001")] == 1
    assert calls[("list", "industry")] == 2
    assert asyncio.run(bf.snapshot_daily_if_closed()) is False


@pytest.mark.parametrize("missing", ["members", "dayk"])
def test_snapshot_retries_missing_child_without_replacing_rank(monkeypatch, board_state, missing):
    kwargs = {"missing_" + missing: ("BK0001",)}
    calls, faults = _snapshot_sources(monkeypatch, **kwargs)
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    faults[missing].clear()
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    saved = json.loads((board_state / "daily.json").read_text())["days"]["2026-09-07"]
    assert saved["ranks"]["concept"] == {"BK0001": 1}
    assert calls[("list", "concept")] == 1
    assert calls[(missing, "BK0001")] == 2
    assert asyncio.run(bf.snapshot_daily_if_closed()) is False


def test_snapshot_dayk_write_failure_does_not_freeze_daily(monkeypatch, board_state):
    _snapshot_sources(monkeypatch)
    original = bf._write_json_atomic
    failed = {"value": True}

    def write(path, payload):
        if path == bf._DAYK_STORE and failed["value"]:
            return False
        return original(path, payload)

    monkeypatch.setattr(bf, "_write_json_atomic", write)
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    failed["value"] = False
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    assert (board_state / "daykline.json").exists()
    assert asyncio.run(bf.snapshot_daily_if_closed()) is False


def test_legacy_empty_current_snapshot_recovers_without_changing_history(monkeypatch, board_state):
    historical = {"ranks": {"concept": {"BK0010": 1}}, "boards": {}, "members": {}}
    bf._write_json_atomic(bf._DAILY_STORE, {"days": {
        "2026-09-04": historical,
        "2026-09-07": {"ranks": {}, "boards": {}, "members": {}},
    }})
    _snapshot_sources(monkeypatch)
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    saved = json.loads((board_state / "daily.json").read_text())["days"]
    assert saved["2026-09-04"] == historical
    assert saved["2026-09-07"]["ranks"]["concept"] == {"BK0001": 1}


def test_snapshot_daily_write_failure_does_not_change_memo_or_freeze(monkeypatch, board_state):
    _snapshot_sources(monkeypatch)
    original = bf._write_json_atomic
    fail_daily = {"value": True}

    def write(path, payload):
        return False if path == bf._DAILY_STORE and fail_daily["value"] else original(path, payload)

    monkeypatch.setattr(bf, "_write_json_atomic", write)
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    assert not bf._read_store_memo(bf._DAILY_STORE, "daily")
    fail_daily["value"] = False
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    assert asyncio.run(bf.snapshot_daily_if_closed()) is False


def test_snapshot_child_exception_preserves_other_data_and_can_retry(monkeypatch, board_state):
    _snapshot_sources(monkeypatch)
    fault = {"value": True}

    async def dayk(code):
        if code == "BK0001" and fault["value"]:
            raise ValueError("fixture child failure")
        return [["2026-09-07", 3.0, None]]

    monkeypatch.setattr(bf, "_fetch_board_dayk", dayk)
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    assert "BK0002" in json.loads(bf._DAYK_STORE.read_text())["boards"]
    fault["value"] = False
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    assert asyncio.run(bf.snapshot_daily_if_closed()) is False


def test_snapshot_old_source_bar_remains_retryable_until_today_arrives(monkeypatch, board_state):
    calls, _ = _snapshot_sources(monkeypatch)
    end = {"value": "2026-09-04"}

    async def dayk(code):
        calls[("dayk", code)] += 1
        return [[end["value"], 3.0, None]]

    monkeypatch.setattr(bf, "_fetch_board_dayk", dayk)
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    end["value"] = "2026-09-07"
    assert asyncio.run(bf.snapshot_daily_if_closed()) is True
    assert calls[("list", "concept")] == 1 and calls[("dayk", "BK0001")] == 2
    assert asyncio.run(bf.snapshot_daily_if_closed()) is False


def _history_bars(end: date, amount: float = 1.0):
    return [[(end - timedelta(days=19 - i)).isoformat(), amount, 99.0] for i in range(20)]


def test_board_history_reads_without_current_source_and_keeps_one_cutoff(monkeypatch, board_state):
    today = date(2026, 9, 7)
    yesterday = today - timedelta(days=1)
    source_calls = []

    async def unavailable(kind):
        source_calls.append(kind)
        return None, ["fixture current source failed"]

    monkeypatch.setattr(bf, "get_board_list", unavailable)
    monkeypatch.setattr(bf.trade_calendar, "_load_persisted", lambda: [today])
    store = {
        "BK0001": {"kind": "concept", "name": "one", "bars": _history_bars(yesterday, 2.0)},
        "BK0002": {"kind": "concept", "name": "two", "bars": _history_bars(yesterday)},
        "BK0003": {"kind": "concept", "name": "older", "bars": _history_bars(yesterday - timedelta(days=1))},
        "BK0004": {"kind": "industry", "name": "different", "bars": _history_bars(today)},
        "BK0005": {"kind": "concept", "name": "short", "bars": _history_bars(yesterday)[-19:]},
        "BK0006": {"kind": "concept", "name": "missing", "bars": _history_bars(yesterday)},
        "BK0007": {"name": "unknown-kind", "bars": _history_bars(yesterday)},
        "BK0008": {"kind": "concept", "name": "new-but-incomplete", "bars": [[today.isoformat(), 1, None]]},
    }
    store["BK0006"]["bars"][-2][1] = None
    monkeypatch.setattr(bf, "_load_dayk_store", lambda: store)
    result = asyncio.run(bf.get_board_fund_flow("concept", "20d"))
    assert result["available"] is True
    assert result["historical"] is True and result["quality"] == "stale"
    assert result["source"] == "eastmoney_local_daykline" and result["as_of"] == yesterday.isoformat()
    assert result["coverage"] == 2 and result["total_boards"] == 6
    assert [row["board_code"] for row in result["rows"]] == ["BK0001", "BK0002"]
    assert result["rows"][0]["main_net_20d_yi"] == 40.0
    for row in result["rows"]:
        assert all(row[key] is None for key in ("change_pct", "main_net_yi", "main_net_ratio", "super_net_yi",
                                               "main_net_5d_yi", "main_net_10d_yi", "leader_name", "leader_symbol", "rank_delta"))
    assert source_calls == []
    assert (await_result := asyncio.run(bf.get_board_fund_flow("concept", "intraday")))["available"] is False
    assert not await_result["rows"] and source_calls == ["concept"]


def test_board_history_missing_window_is_unavailable_not_empty_success(monkeypatch, board_state):
    async def unexpected(*args):
        raise AssertionError("local history must not call live source")

    monkeypatch.setattr(bf, "get_board_list", unexpected)
    monkeypatch.setattr(bf.trade_calendar, "_load_persisted", lambda: None)
    monkeypatch.setattr(bf, "_load_dayk_store", lambda: {
        "BK0001": {"kind": "concept", "name": "one", "bars": [["2026-09-04", 1, None]]},
        "BK0002": {"kind": "concept", "bars": _history_bars(date(2026, 9, 4))},
    })
    result = asyncio.run(bf.get_board_fund_flow("concept", "20d"))
    assert result["available"] is False and result["coverage"] == 0 and not result["rows"]
    assert result["historical"] is True


@pytest.mark.parametrize("calendar_known", [False, True])
def test_board_history_quality_requires_known_recent_completed_day(monkeypatch, board_state, calendar_known):
    today = date(2026, 9, 7)
    monkeypatch.setattr(bf.trade_calendar, "_load_persisted", lambda: [today] if calendar_known else None)
    monkeypatch.setattr(bf, "_load_dayk_store", lambda: {
        "BK0001": {"kind": "concept", "name": "one", "bars": _history_bars(today)},
    })
    result = asyncio.run(bf.get_board_fund_flow("concept", "20d"))
    assert result["available"] is True
    assert result["quality"] == ("medium" if calendar_known else "stale")
    assert result["historical"] is True and result["as_of"] == today.isoformat()


@pytest.mark.parametrize("day, calendar_known", [(date(2026, 9, 12), True), (date(2026, 10, 1), True), (date(2026, 10, 12), False)])
def test_fund_snapshot_never_creates_nontrading_or_unknown_date(monkeypatch, tmp_path, day, calendar_known):
    monkeypatch.setattr(ff, "_FLOW_STORE", tmp_path / "daily.json")
    monkeypatch.setattr(ff, "beijing_now", lambda: datetime.combine(day, datetime.min.time(), BJ_TZ).replace(hour=16))
    calendar = [date(2026, 9, 11), date(2026, 9, 30), date(2026, 10, 9)] if calendar_known else []
    monkeypatch.setattr(tc, "known_trade_days", lambda: calendar)
    ff._snapshot_today_if_closed({"available": True, "items": {"sh": {"main": 1}, "sz": {"main": 2}}})
    assert not ff._FLOW_STORE.exists()


def test_fund_history_filters_proven_nontrade_days_without_rewriting_store(monkeypatch, tmp_path):
    store = tmp_path / "daily.json"
    now = datetime(2026, 9, 14, 10, 0, tzinfo=BJ_TZ)
    values = {key: 0 for key in ff._FLOW_KEYS.values()}
    record = {"sh": {**values, "main": 1}, "sz": {**values, "main": 2}, "source": "snapshot"}
    raw = json.dumps({"updated_at": "fixture", "days": {
        "2026-09-10": record, "2026-09-11": record, "2026-09-12": record, "2026-09-13": record,
        "2026-09-14": record, "2026-08-31": record,
    }})
    store.write_text(raw)
    monkeypatch.setattr(ff, "_FLOW_STORE", store)
    monkeypatch.setattr(ff, "_FLOW_HIST_CACHE", TTLCache("fixture-history", 60, maxsize=1))
    monkeypatch.setattr(ff, "beijing_now", lambda: now)
    calendar = [date(2026, 9, 11), date(2026, 9, 14)]

    async def days(provider):
        return calendar

    async def unexpected():
        raise AssertionError("Existing complete prior day must not backfill")

    monkeypatch.setattr(tc, "trading_days", days)
    monkeypatch.setattr(ff, "_pull_em_fflow_into_store", unexpected)
    result = asyncio.run(ff.get_fund_flow_history(SimpleNamespace(provider=None), 20))
    dates = [row["date"] for row in result["items"]]
    assert dates == ["2026-09-14", "2026-09-11", "2026-09-10", "2026-08-31"]
    assert any("交易日归属未核定" in reason for reason in result["degraded"])
    assert any("过滤" in reason for reason in result["degraded"])
    assert store.read_text() == raw


@pytest.fixture()
def aggregate_state(monkeypatch, tmp_path):
    monkeypatch.setattr(ff, "beijing_now", lambda: datetime(2026, 9, 7, 10, 0, tzinfo=BJ_TZ))
    monkeypatch.setattr(ff, "_FLOW_STORE", tmp_path / "daily.json")
    monkeypatch.setattr(ff, "_FLOW_RT_CACHE", TTLCache("fixture-rt", 30, maxsize=1))
    monkeypatch.setattr(ff, "_INTRADAY_CACHE", TTLCache("fixture-intraday", 60, maxsize=1))
    monkeypatch.setattr(ff, "_AGGREGATE_READS", {}, raising=False)


@pytest.mark.parametrize("failed", [False, True])
def test_realtime_aggregate_shares_cold_result_and_recovers(monkeypatch, aggregate_state, failed):
    calls = []
    failure = {"value": failed}

    async def source(**kwargs):
        calls.append(1)
        await asyncio.sleep(0)
        return {"available": not failure["value"], "items": {"total": {"main": None}}, "degraded": []}

    monkeypatch.setattr(ff, "_fund_flow_rt_uncached", source)

    async def go():
        outputs = await asyncio.gather(*(ff.get_fund_flow_realtime() for _ in range(8)))
        assert len(calls) == 1
        outputs[0]["items"]["total"]["main"] = 999
        assert all(out["items"]["total"]["main"] is None for out in outputs[1:])
        failure["value"] = False
        assert (await ff.get_fund_flow_realtime())["available"] is True
        assert len(calls) == (2 if failed else 1)

    asyncio.run(go())


def test_realtime_cancelled_waiter_keeps_other_reader(monkeypatch, aggregate_state):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def source(**kwargs):
            calls.append(1)
            entered.set()
            await release.wait()
            return {"available": True, "items": {}, "degraded": []}

        monkeypatch.setattr(ff, "_fund_flow_rt_uncached", source)
        first = asyncio.create_task(ff.get_fund_flow_realtime())
        await entered.wait()
        second = asyncio.create_task(ff.get_fund_flow_realtime())
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert (await second)["available"] is True
        assert calls == [1]

    asyncio.run(go())


def test_intraday_aggregate_shares_merge_and_spacing(monkeypatch, aggregate_state):
    calls = []
    spacing = []
    original_sleep = asyncio.sleep

    async def source(secid):
        calls.append(secid)
        await original_sleep(0)
        return [("09:45", {key: 1.0 for key in ff._FFLOW_MIN_KEYS})]

    async def sleep(delay):
        if delay == 1.5:
            spacing.append(delay)
        await original_sleep(0)

    monkeypatch.setattr(ff, "_em_fflow_kline", source)
    monkeypatch.setattr(ff.asyncio, "sleep", sleep)

    async def go():
        outputs = await asyncio.gather(*(ff.get_fund_flow_intraday() for _ in range(8)))
        assert calls == ["1.000001", "0.399107"]
        assert spacing == [1.5]
        outputs[0]["items"][0]["main"] = 999
        assert all(out["items"][0]["main"] == 2 for out in outputs[1:])

    asyncio.run(go())


def test_watcher_failure_uses_one_board_read_per_kind_per_beat(monkeypatch):
    now = datetime(2026, 9, 7, 10, 0, tzinfo=BJ_TZ)
    monkeypatch.setattr(wt, "beijing_now", lambda: now)
    monkeypatch.setattr(bf, "beijing_now", lambda: now)
    monkeypatch.setattr(bf, "_in_session", lambda: True)
    monkeypatch.setattr(bf, "_LIST_CACHE", TTLCache("fixture-beat", 30, maxsize=4))
    monkeypatch.setattr(bf, "_LIST_READS", {})
    calls = []

    async def clist(fs, fields):
        calls.append(fs)
        await asyncio.sleep(0)
        return None, None

    async def days(provider):
        return [now.date()]

    async def pool(day):
        return []

    async def noop(*args):
        return None

    monkeypatch.setattr(bf, "_clist_pages", clist)
    monkeypatch.setattr(wt.tc, "trading_days", days)
    monkeypatch.setattr(wt.tc, "in_trading_window", lambda _: True)
    monkeypatch.setattr(wt, "_attach_volume_ratios", noop)
    monkeypatch.setattr(wt, "_refresh_env", noop)
    state = SimpleNamespace(hub=SimpleNamespace(provider=SimpleNamespace(get_limit_up_pool=pool)))

    async def go():
        result = await wt.collect_beat_inputs(state, {}, env_refresh_seconds=60)
        assert Counter(calls) == Counter({bf._KIND_FS["concept"]: 1, bf._KIND_FS["industry"]: 1})
        assert result["board_count"] == 0 and result["board_flows"] == {} and result["themes"] == {}
        await wt.collect_beat_inputs(state, {}, env_refresh_seconds=60)
        assert len(calls) == 4  # A distinct later beat may retry; failure is never success-cached.

    asyncio.run(go())
