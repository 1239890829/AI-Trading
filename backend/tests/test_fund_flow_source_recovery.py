"""Source identity, recovery and shared cold reads with isolated local fixtures."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core import ttl_cache
from app.core.bjtime import BJ_TZ
from app.core.ttl_cache import TTLCache
from app.market import fund_flow as ff, trade_calendar as tc


@pytest.fixture()
def state(monkeypatch, tmp_path):
    clock = {"now": datetime(2026, 10, 12, 15, 6, tzinfo=BJ_TZ), "mono": 10000.0}
    monkeypatch.setattr(ff, "beijing_now", lambda: clock["now"])
    monkeypatch.setattr(ff, "monotonic", lambda: clock["mono"])
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock["mono"])
    monkeypatch.setattr(ff, "_FLOW_STORE", tmp_path / "daily.json")
    monkeypatch.setattr(ff, "_FLOW_HIST_CACHE", TTLCache("fixture-history", 21600, maxsize=1))
    monkeypatch.setattr(ff, "_SINA_CACHE", TTLCache("fixture-sina", 300, maxsize=8))
    monkeypatch.setattr(ff, "_TURNOVER_CACHE", TTLCache("fixture-turnover", 30, maxsize=2))
    monkeypatch.setattr(ff, "_FFLOW_MIN_CACHE", TTLCache("fixture-minute", 60, maxsize=4))
    monkeypatch.setattr(ff, "_FFLOW_MIN_READS", {})
    monkeypatch.setattr(ff, "_AGGREGATE_READS", {})
    monkeypatch.setattr(ff, "_last_backfill_ts", [0.0])
    calendar = [date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)]

    async def days(_):
        return calendar

    monkeypatch.setattr(tc, "trading_days", days)
    monkeypatch.setattr(tc, "known_trade_days", lambda: calendar)
    real_sleep = asyncio.sleep

    async def spacing(_):
        await real_sleep(0)

    monkeypatch.setattr(ff.asyncio, "sleep", spacing)
    return clock, calendar


def ulist(sh: datetime | None, sz: datetime | None):
    return [{"f12": code, "f124": int(stamp.timestamp()) if stamp else None,
             "f62": -1e8, "f66": -0.4e8, "f72": -0.6e8, "f78": 0.5e8, "f84": 0.5e8}
            for code, stamp in (("000001", sh), ("399107", sz))]


@pytest.mark.parametrize("kind", ["yesterday", "missing", "delayed", "future"])
def test_snapshot_requires_current_completed_source_not_receipt_time(state, kind):
    clock, _ = state
    stamp = clock["now"].replace(hour=15, minute=0)
    invalid = {"yesterday": stamp - timedelta(days=3), "missing": None,
               "delayed": stamp.replace(hour=14, minute=51),
               "future": stamp + timedelta(hours=1)}[kind]
    data = ff._rt_from_ulist(ulist(invalid, invalid), [])
    ff._snapshot_today_if_closed(data)
    assert not ff._FLOW_STORE.exists()
    recovered = ff._rt_from_ulist(ulist(stamp, stamp), [])
    ff._snapshot_today_if_closed(recovered)
    saved = json.loads(ff._FLOW_STORE.read_text())["days"]["2026-10-12"]
    assert saved["sh"]["main"] == saved["sz"]["main"] == -1


def test_ulist_different_source_days_cannot_form_a_two_market_total(state):
    clock, _ = state
    data = ff._rt_from_ulist(ulist(clock["now"] - timedelta(days=3), clock["now"]), [])
    assert not data["available"]
    assert data["degraded"]
    ff._snapshot_today_if_closed(data)
    assert not ff._FLOW_STORE.exists()


def response(lines):
    return SimpleNamespace(raise_for_status=lambda: None,
                           json=lambda: {"data": {"klines": lines}})


def minute_line(day, hm, value=1):
    return f"{day} {hm},{value * 1e8},0,0,{value * 1e8},0"


def test_parsed_minute_source_dates_survive_shared_cache_and_block_cross_day_merge(monkeypatch, state):
    calls = []

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            day = "2026-10-09" if params["secid"] == "1.000001" else "2026-10-12"
            return response([minute_line(day, "15:00")])

    monkeypatch.setattr(ff, "_HTTP", HTTP())

    async def go():
        await ff._em_fflow_kline("1.000001")
        assert await ff._rt_from_intraday_tail() is None
        assert calls == ["1.000001", "0.399107"]

    asyncio.run(go())


def bar(day, value=1):
    return f"{day},{value*1e8},0,0,{value*1e8},0,0,0,0,0,0,0,1.5"


def test_empty_200_backfill_is_not_success_and_does_not_write_store(monkeypatch, state):
    class HTTP:
        async def get(self, *_args, **_kwargs):
            return response([])

    monkeypatch.setattr(ff, "_HTTP", HTTP())
    assert asyncio.run(ff._pull_em_fflow_into_store()) is False
    assert not ff._FLOW_STORE.exists()


def test_history_partial_market_retries_after_300s_and_preserves_existing_market(monkeypatch, state):
    clock, _ = state
    calls = []
    broken = {"sz": True}

    class HTTP:
        async def get(self, url, params, headers):
            secid = params["secid"]
            calls.append(secid)
            if secid == "0.399107" and broken["sz"]:
                raise ValueError("fixture failed market")
            return response([bar("2026-10-09", 1 if secid == "1.000001" else 2)])

    monkeypatch.setattr(ff, "_HTTP", HTTP())

    async def go():
        first = await ff.get_fund_flow_history(SimpleNamespace(provider=None), 20)
        assert first["items"][0]["main"] is None
        assert first["degraded"]
        before = json.loads(ff._FLOW_STORE.read_text())["days"]["2026-10-09"]["sh"]
        clock["mono"] += 301
        broken["sz"] = False
        recovered = await ff.get_fund_flow_history(SimpleNamespace(provider=None), 20)
        assert recovered["items"][0]["main"] == 3
        assert json.loads(ff._FLOW_STORE.read_text())["days"]["2026-10-09"]["sh"] == before
        assert calls == ["1.000001", "0.399107", "1.000001", "0.399107"]

    asyncio.run(go())


def test_history_failure_recovers_at_retry_deadline_not_six_hours(monkeypatch, state):
    clock, _ = state
    calls = []
    failed = {"value": True}

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            if failed["value"]:
                raise ValueError("fixture source unavailable")
            return response([bar("2026-10-09")])

    monkeypatch.setattr(ff, "_HTTP", HTTP())

    async def go():
        first = await ff.get_fund_flow_history(SimpleNamespace(provider=None), 20)
        assert first["degraded"] and not first["items"]
        failed["value"] = False
        clock["mono"] += 299
        await ff.get_fund_flow_history(SimpleNamespace(provider=None), 20)
        assert len(calls) == 2
        clock["mono"] += 2
        last = await ff.get_fund_flow_history(SimpleNamespace(provider=None), 20)
        assert last["items"][0]["main"] == 2
        assert len(calls) == 4

    asyncio.run(go())


def test_sina_cold_read_coalesces_returns_copies_and_failure_recovers(monkeypatch, state):
    calls = []
    failed = {"value": False}

    class HTTP:
        async def get(self, url, params):
            calls.append((params["symbol"], params["datalen"]))
            await asyncio.sleep(0)
            if failed["value"]:
                raise ValueError("fixture unavailable")
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: [{"day":"2026-10-09 15:00:00", "amount":"100"}])

    monkeypatch.setattr(ff, "_HTTP", HTTP())

    async def go():
        rows = await asyncio.gather(*(ff._sina_mk_bars(ff.SH_SYMBOL) for _ in range(8)))
        assert len(calls) == 1
        rows[0][0]["amount"] = 999
        assert all(r[0]["amount"] == 100 for r in rows[1:])
        assert (await ff._sina_mk_bars(ff.SH_SYMBOL))[0]["amount"] == 100
        ff._SINA_CACHE.invalidate()
        failed["value"] = True
        assert await asyncio.gather(*(ff._sina_mk_bars(ff.SH_SYMBOL) for _ in range(8))) == [None] * 8
        assert len(calls) == 2
        failed["value"] = False
        assert (await ff._sina_mk_bars(ff.SH_SYMBOL))[0]["amount"] == 100
        assert len(calls) == 3

    asyncio.run(go())


def test_turnover_today_cold_read_shared_and_midnight_invalidates_identity(monkeypatch, state):
    clock, _ = state
    calls = []

    async def load(hub, degraded):
        calls.append(clock["now"].date())
        await asyncio.sleep(0)
        return {"today_amount_yi":1, "today_series":[{"t":"15:00", "cum":1}], "degraded":[]}

    monkeypatch.setattr(ff, "_turnover_today_uncached", load)

    async def go():
        outputs = await asyncio.gather(*(ff.get_turnover_today(None) for _ in range(8)))
        assert len(calls) == 1
        outputs[0]["today_series"][0]["cum"] = 999
        assert (await ff.get_turnover_today(None))["today_series"][0]["cum"] == 1
        clock["now"] += timedelta(days=1)
        await ff.get_turnover_today(None)
        assert len(calls) == 2

    asyncio.run(go())


def test_partial_snapshot_repair_keeps_the_preserved_markets_source_identity(state):
    clock, _ = state
    old_stamp = clock["now"].replace(hour=15, minute=0, second=0)
    new_stamp = old_stamp.replace(minute=1)
    previous = ff._rt_from_ulist(ulist(old_stamp, old_stamp), [])
    previous["items"]["sh"]["main"] = 9
    ff._write_store_atomic({"days": {"2026-10-12": {
        "sh": previous["items"]["sh"], "sz": {"main": None},
        "source_timestamps": {"sh": old_stamp.isoformat(), "sz": None}}}})
    recovered = ff._rt_from_ulist(ulist(new_stamp, new_stamp), [])
    ff._snapshot_today_if_closed(recovered)
    saved = json.loads(ff._FLOW_STORE.read_text())["days"]["2026-10-12"]
    assert saved["sh"]["main"] == 9
    assert saved["sz"]["main"] == -1
    assert saved["source_timestamps"] == {"sh": old_stamp.isoformat(), "sz": new_stamp.isoformat()}


@pytest.mark.parametrize("source_day", [None, "2026-10-09"])
def test_intraday_unverified_or_past_date_is_explicit_even_when_markets_match(monkeypatch, state, source_day):
    monkeypatch.setattr(ff, "_INTRADAY_CACHE", TTLCache("fixture-intraday", 60, maxsize=1))

    class HTTP:
        async def get(self, *_args, **_kwargs):
            label = f"{source_day} 15:00" if source_day else "15:00"
            return response([f"{label},100000000,0,0,100000000,0"])

    monkeypatch.setattr(ff, "_HTTP", HTTP())
    result = asyncio.run(ff.get_fund_flow_intraday())
    assert result["items"] == [{"t":"15:00", "main":2, "small":0, "mid":0, "big":2, "super_":0}]
    assert any("日期" in reason for reason in result["degraded"])


@pytest.mark.parametrize("cancel_all", [False, True])
def test_turnover_waiter_cancel_does_not_abort_source_and_last_waiter_can_rejoin(monkeypatch, state, cancel_all):
    async def go():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def load(hub, degraded):
            calls.append(1)
            started.set()
            await release.wait()
            return {"today_amount_yi":1, "degraded":[]}

        monkeypatch.setattr(ff, "_turnover_today_uncached", load)
        first = asyncio.create_task(ff.get_turnover_today(None))
        second = asyncio.create_task(ff.get_turnover_today(None))
        await started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        if cancel_all:
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
        rejoined = asyncio.create_task(ff.get_turnover_today(None))
        await asyncio.sleep(0)
        assert calls == [1]
        release.set()
        outputs = [await rejoined] if cancel_all else await asyncio.gather(second, rejoined)
        assert all(out["today_amount_yi"] == 1 for out in outputs)
        assert not ff._AGGREGATE_READS
        assert (await ff.get_turnover_today(None))["today_amount_yi"] == 1
        assert calls == [1]

    asyncio.run(go())


def test_turnover_failed_or_raised_attempt_is_released_for_next_read(monkeypatch, state):
    async def go():
        calls = []
        mode = {"value":"degraded"}

        async def load(hub, degraded):
            calls.append(1)
            await asyncio.sleep(0)
            if mode["value"] == "raise":
                raise ValueError("fixture failure")
            return {"today_amount_yi":None if mode["value"] == "degraded" else 1,
                    "degraded":["fixture failed"] if mode["value"] == "degraded" else []}

        monkeypatch.setattr(ff, "_turnover_today_uncached", load)
        assert all(out["today_amount_yi"] is None for out in await asyncio.gather(
            *(ff.get_turnover_today(None) for _ in range(8))))
        assert len(calls) == 1 and not ff._AGGREGATE_READS
        mode["value"] = "raise"
        failures = await asyncio.gather(*(ff.get_turnover_today(None) for _ in range(8)), return_exceptions=True)
        assert all(isinstance(exc, ValueError) for exc in failures)
        assert len(calls) == 2 and not ff._AGGREGATE_READS
        mode["value"] = "success"
        assert (await ff.get_turnover_today(None))["today_amount_yi"] == 1
        assert len(calls) == 3

    asyncio.run(go())


def test_backfill_write_failure_and_malformed_bars_can_recover_without_losing_verified_snapshot(monkeypatch, state):
    clock, _ = state
    writer = ff._write_store_atomic
    fail_write = {"value":True}
    calls = []

    def write(payload):
        if fail_write["value"]:
            raise OSError("fixture write failed")
        writer(payload)

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            return response(["bad-date,0,0,0,0,0,0,0,0,0,0,0,0", bar("2026-10-09"),
                             bar("2026-10-12", 999), bar("2026-10-13", 999)])

    monkeypatch.setattr(ff, "_HTTP", HTTP())
    monkeypatch.setattr(ff, "_write_store_atomic", write)

    async def go():
        first = await ff.get_fund_flow_history(None, 20)
        assert first["items"] == [] and first["degraded"] and not ff._FLOW_STORE.exists()
        fail_write["value"] = False
        clock["mono"] += 301
        stamp = clock["now"].replace(hour=15, minute=0)
        ff._snapshot_today_if_closed(ff._rt_from_ulist(ulist(stamp, stamp), []))
        verified_today = json.loads(ff._FLOW_STORE.read_text())["days"]["2026-10-12"]
        result = await ff.get_fund_flow_history(None, 20)
        saved = json.loads(ff._FLOW_STORE.read_text())["days"]
        assert saved["2026-10-12"] == verified_today
        assert "2026-10-13" not in saved
        assert result["items"][1]["main"] == 2
        assert calls == ["1.000001", "0.399107"] * 2

    asyncio.run(go())


def test_history_parallel_reads_share_one_pull_and_calendar_version_reopens_unknown_filter(monkeypatch, state):
    _, calendar = state
    calls = []

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            await asyncio.sleep(0)
            return response([bar("2026-10-09"), bar("2026-10-07")])

    monkeypatch.setattr(ff, "_HTTP", HTTP())

    async def go():
        outputs = await asyncio.gather(*(ff.get_fund_flow_history(None, 20) for _ in range(8)))
        assert calls == ["1.000001", "0.399107"]
        assert [row["date"] for row in outputs[0]["items"]] == ["2026-10-09", "2026-10-07"]
        outputs[0]["items"][0]["main"] = 999
        assert (await ff.get_fund_flow_history(None, 20))["items"][0]["main"] == 2
        calendar.insert(0, date(2026, 9, 30))
        reread = await ff.get_fund_flow_history(None, 20)
        assert [row["date"] for row in reread["items"]] == ["2026-10-09"]
        assert calls == ["1.000001", "0.399107"]

    asyncio.run(go())


def test_backfill_reloads_file_after_network_wait_to_preserve_concurrent_verified_close(monkeypatch, state):
    clock, _ = state

    async def go():
        started, release = asyncio.Event(), asyncio.Event()

        class HTTP:
            async def get(self, url, params, headers):
                if params["secid"] == "1.000001":
                    started.set()
                    await release.wait()
                return response([bar("2026-10-09")])

        monkeypatch.setattr(ff, "_HTTP", HTTP())
        pending = asyncio.create_task(ff._pull_em_fflow_into_store())
        await started.wait()
        stamp = clock["now"].replace(hour=15, minute=0)
        ff._snapshot_today_if_closed(ff._rt_from_ulist(ulist(stamp, stamp), []))
        verified_today = json.loads(ff._FLOW_STORE.read_text())["days"]["2026-10-12"]
        release.set()
        assert await pending is True
        saved = json.loads(ff._FLOW_STORE.read_text())["days"]
        assert saved["2026-10-12"] == verified_today
        assert ff._flow_total_yi(saved["2026-10-09"])["main"] == 2

    asyncio.run(go())


@pytest.mark.parametrize("stamp", [None, datetime(2026, 10, 9, 15, tzinfo=BJ_TZ)])
def test_date_warning_never_bypasses_minute_source_age_budget(monkeypatch, state, stamp):
    clock, _ = state
    monkeypatch.setattr(ff, "_INTRADAY_CACHE", TTLCache("fixture-intraday-age", 60, maxsize=1))

    async def read(secid):
        if secid == "0.399107":
            clock["mono"] += 61
        values = {key:1 for key in ff._FLOW_KEYS.values()}
        return ff._MinuteRows([("15:00", values)], clock["mono"], [stamp])

    monkeypatch.setattr(ff, "_em_fflow_kline", read)
    result = asyncio.run(ff.get_fund_flow_intraday())
    assert result["items"] == []
    assert ff._MINUTE_EXPIRED_REASON in result["degraded"]
