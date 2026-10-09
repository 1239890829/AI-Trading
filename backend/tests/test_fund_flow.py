"""fund_flow 纯函数与降级逻辑单测（不外呼——数据源调用一概 monkeypatch/直测纯函数）。"""

import asyncio
from datetime import date, datetime

import pytest

from app.core.bjtime import BJ_TZ  # S2-8：时区常量唯一权威
from app.core.ttl_cache import TTLCache
from app.market import fund_flow as ff


# ---------------------------------------------------------------- 时间口径

def test_market_progress_minutes_sessions():
    mk = ff._market_progress_minutes
    # 盘前
    assert mk(datetime(2026, 9, 4, 9, 0, tzinfo=BJ_TZ)) is None
    # 早盘 09:31 → 1 分钟
    assert mk(datetime(2026, 9, 4, 9, 31, tzinfo=BJ_TZ)) == 1
    # 午休
    assert mk(datetime(2026, 9, 4, 12, 0, tzinfo=BJ_TZ)) is None
    # 午后 13:01 → 121
    assert mk(datetime(2026, 9, 4, 13, 1, tzinfo=BJ_TZ)) == 121
    # 收盘后封顶 240
    assert mk(datetime(2026, 9, 4, 15, 30, tzinfo=BJ_TZ)) == 240
    # naive datetime 视作北京时间
    assert mk(datetime(2026, 9, 4, 9, 31)) == 1


def test_sina_bar_seq_maps_minutes():
    sq = ff._sina_bar_seq
    assert sq(datetime(2026, 9, 3, 9, 35)) == 5
    assert sq(datetime(2026, 9, 3, 11, 30)) == 120
    assert sq(datetime(2026, 9, 3, 11, 35)) is None  # 午休
    assert sq(datetime(2026, 9, 3, 13, 5)) == 125
    assert sq(datetime(2026, 9, 3, 15, 0)) == 240
    assert sq(datetime(2026, 9, 3, 15, 5)) is None  # 收盘外


# ---------------------------------------------------------------- 新浪 bar 聚合

def _bar(day: str, hm: str, amount: float) -> dict:
    """构造 _sina_mk_bars 解析后的 bar 结构。"""
    dt = datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M")
    seq = ff._sina_bar_seq(dt)
    return {"dt": dt, "seq": seq, "amount": amount}


def test_cum_amount_by_seq_accumulates_increment_bars():
    d = date(2026, 9, 3)
    bars = [
        _bar("2026-09-03", "09:35", 100.0),
        _bar("2026-09-03", "09:40", 200.0),
        _bar("2026-09-04", "09:35", 999.0),  # 其他交易日不串
    ]
    total = ff._cum_amount_by_seq(bars, d, max_seq=10)
    assert total == 300.0
    # max_seq 截断
    assert ff._cum_amount_by_seq(bars, d, max_seq=5) == 100.0
    # 无该日数据
    assert ff._cum_amount_by_seq(bars, date(2026, 8, 1)) is None


def test_pair_total_sums_two_markets():
    d = date(2026, 9, 3)
    sh = [_bar("2026-09-03", "09:35", 100e8)]  # 元
    sz = [_bar("2026-09-03", "09:35", 50e8)]
    assert ff._pair_total(sh, sz, d) == 150.0  # 亿元
    assert ff._pair_total(sh, [], d) is None  # 任一市场缺数据 → None 不臆造


# ---------------------------------------------------------------- 实时聚合与降级

def test_rt_from_ulist_aggregates_markets():
    diff = [
        {"f12": "000001", "f62": -100e8, "f66": -30e8, "f72": -70e8,
         "f78": -20e8, "f84": 120e8, "f124": 1788000000},
        {"f12": "399107", "f62": -50e8, "f66": -10e8, "f72": -40e8,
         "f78": -30e8, "f84": 80e8, "f124": 1788000000},
    ]
    out = ff._rt_from_ulist(diff, [])
    assert out["available"] is True
    t = out["items"]["total"]
    assert t["main"] == -150.0
    assert t["super_"] == -40.0
    assert t["small"] == 200.0
    # self-check：main+mid+small ≈ 0
    assert abs(t["main"] + t["mid"] + t["small"]) < 0.01


def test_rt_from_ulist_missing_market_degrades():
    diff = [{"f12": "000001", "f62": -1e8, "f66": 0, "f72": 0, "f78": 0, "f84": 0}]
    out = ff._rt_from_ulist(diff, [])
    assert out["available"] is False


def test_rt_fallback_chain_marks_delay(monkeypatch):
    async def fail_ulist():
        return None

    async def fake_kline(secid, **kwargs):
        if secid == "1.000001":
            return [("14:00", {"main": -10.0, "small": 5.0, "mid": 3.0, "big": -8.0, "super_": -2.0})]
        return [("14:00", {"main": -5.0, "small": 3.0, "mid": 1.0, "big": -4.0, "super_": -1.0})]

    monkeypatch.setattr(ff, "_em_ulist", fail_ulist)
    monkeypatch.setattr(ff, "_em_fflow_kline", fake_kline)
    import asyncio
    out = asyncio.run(ff._fund_flow_rt_uncached())
    assert out["available"] is True
    assert "15 分钟延迟" in " ".join(out["degraded"])
    assert out["items"]["total"]["main"] == -15.0


def test_rt_fallback_chain_full_failure(monkeypatch):
    import asyncio

    async def fail(*a, **k):
        return None

    monkeypatch.setattr(ff, "_em_ulist", fail)
    monkeypatch.setattr(ff, "_em_fflow_kline", fail)
    out = asyncio.run(ff._fund_flow_rt_uncached())
    assert out["available"] is False  # 绝不填 0


# ---------------------------------------------------------------- 收盘快照

def test_snapshot_today_if_closed_persists_once(monkeypatch, tmp_path):
    store_file = tmp_path / "daily.json"
    monkeypatch.setattr(ff, "_FLOW_STORE", store_file)
    # 14:00 不落盘
    monkeypatch.setattr(ff, "beijing_now", lambda: datetime(2026, 9, 4, 14, 0, tzinfo=BJ_TZ))
    rt = {"available": True, "items": {"sh": {"main": -1.0}, "sz": {"main": -2.0}}}
    ff._snapshot_today_if_closed(rt)
    assert not store_file.exists()
    # 15:06 落盘
    monkeypatch.setattr(ff, "beijing_now", lambda: datetime(2026, 9, 4, 15, 6, tzinfo=BJ_TZ))
    ff._snapshot_today_if_closed(rt)
    import json
    data = json.loads(store_file.read_text())
    assert "2026-09-04" in data["days"]
    assert data["days"]["2026-09-04"]["sh"]["main"] == -1.0
    # 幂等：已有当日不覆盖
    ff._snapshot_today_if_closed({**rt, "items": {"sh": {"main": -9.0}, "sz": {"main": -9.0}}})
    assert json.loads(store_file.read_text())["days"]["2026-09-04"]["sh"]["main"] == -1.0


def test_flow_total_yi_merges_markets():
    rec = {"sh": {"main": -1.0, "small": 2.0, "mid": None, "big": -0.5, "super_": -0.5},
           "sz": {"main": -2.0, "small": 3.0, "mid": 1.0, "big": None, "super_": None}}
    out = ff._flow_total_yi(rec)
    assert out["main"] == -3.0
    assert out["mid"] is None  # 任一市场缺失 → None（三态，不填 0）
    assert out["close_pct"] is None


# ---------------------------------------------------------------- 回源节流

def test_backfill_throttle(monkeypatch):
    # monotonic 绝对值不可依赖（CI 新 runner 可能 < 300s）：注入固定时钟
    clock = {"t": 10000.0}
    import time as _time
    monkeypatch.setattr(_time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(ff, "_last_backfill_ts", [0.0])
    assert ff._backfill_throttled() is False  # 首次放行
    clock["t"] += 60
    assert ff._backfill_throttled() is True  # 5 分钟内节流
    clock["t"] += 301
    assert ff._backfill_throttled() is False  # 超过节流窗口放行


def test_flow_store_read_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "_FLOW_STORE", tmp_path / "nope.json")
    assert ff._read_flow_store() is None


# ---------------------------------------------------------------- 同源共享读取（真实解析，隔离 HTTP/时钟/文件）

@pytest.fixture()
def isolated_minute_reads(monkeypatch, tmp_path):
    clock = {"now": datetime(2026, 10, 9, 10, 0, tzinfo=BJ_TZ)}
    monkeypatch.setattr(ff, "beijing_now", lambda: clock["now"])
    monkeypatch.setattr(ff, "_FLOW_STORE", tmp_path / "daily.json")
    monkeypatch.setattr(ff, "_FFLOW_MIN_CACHE", TTLCache("test-fflow-minute", 60, maxsize=8), raising=False)
    monkeypatch.setattr(ff, "_FFLOW_MIN_READS", {}, raising=False)
    monkeypatch.setattr(ff, "_FLOW_RT_CACHE", TTLCache("test-fflow-rt", 30, maxsize=1))
    monkeypatch.setattr(ff, "_INTRADAY_CACHE", TTLCache("test-fflow-intraday", 60, maxsize=1))
    return clock


class MinuteResponse:
    def __init__(self, main=100000000):
        self.main = main

    def raise_for_status(self):
        pass

    def json(self):
        return {"data": {"klines": [f"2026-10-09 09:45,{self.main},-,0,70000000,30000000"]}}


def test_minute_same_source_cold_reads_share_and_return_copies(monkeypatch, isolated_minute_reads):
    calls = []

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            await asyncio.sleep(0)
            return MinuteResponse()

    monkeypatch.setattr(ff, "_HTTP", HTTP())

    async def go():
        rows = await asyncio.gather(*(ff._em_fflow_kline("1.000001") for _ in range(8)))
        assert calls == ["1.000001"]
        rows[0][0][1]["main"] = 99
        assert all(r[0][1]["main"] == 1 for r in rows[1:])
        warm = await ff._em_fflow_kline("1.000001")
        assert warm[0][1]["main"] == 1 and warm[0][1]["small"] is None
        assert calls == ["1.000001"]

    asyncio.run(go())


def test_minute_cache_separates_markets_and_dates(monkeypatch, isolated_minute_reads):
    calls = []

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            return MinuteResponse()

    monkeypatch.setattr(ff, "_HTTP", HTTP())

    async def go():
        await ff._em_fflow_kline("1.000001")
        await ff._em_fflow_kline("1.000001")
        await ff._em_fflow_kline("0.399107")
        isolated_minute_reads["now"] = datetime(2026, 10, 10, 10, 0, tzinfo=BJ_TZ)
        await ff._em_fflow_kline("1.000001")
        assert calls == ["1.000001", "0.399107", "1.000001"]

    asyncio.run(go())


def test_minute_cancelled_reader_does_not_cancel_shared_request(monkeypatch, isolated_minute_reads):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        class HTTP:
            async def get(self, url, params, headers):
                calls.append(params["secid"])
                entered.set()
                await release.wait()
                return MinuteResponse()

        monkeypatch.setattr(ff, "_HTTP", HTTP())
        first = asyncio.create_task(ff._em_fflow_kline("1.000001"))
        await entered.wait()
        second = asyncio.create_task(ff._em_fflow_kline("1.000001"))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        rows = await second
        assert rows[0][1]["main"] == 1
        assert calls == ["1.000001"]

    asyncio.run(go())


def test_minute_failed_read_retries_on_next_request(monkeypatch, isolated_minute_reads):
    calls = []

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            if len(calls) <= 3:
                raise RuntimeError("offline fixture")
            return MinuteResponse()

    monkeypatch.setattr(ff, "_HTTP", HTTP())

    async def go():
        failed = await asyncio.gather(*(ff._em_fflow_kline("1.000001") for _ in range(8)))
        assert failed == [None] * 8 and len(calls) == 3
        assert (await ff._em_fflow_kline("1.000001"))[0][1]["main"] == 1
        assert len(calls) == 4

    asyncio.run(go())


def test_fund_realtime_warm_payload_is_not_mutated_by_reader(monkeypatch, isolated_minute_reads):
    async def source(**kwargs):
        return {"available": True, "as_of": "09:45:00",
                "items": {"total": {"main": 1}}, "degraded": ["延迟"]}

    monkeypatch.setattr(ff, "_fund_flow_rt_uncached", source)

    async def go():
        cold = await ff.get_fund_flow_realtime()
        cold["items"]["total"]["main"] = 99
        cold["degraded"].clear()
        warm = await ff.get_fund_flow_realtime()
        assert warm["items"]["total"]["main"] == 1 and warm["degraded"] == ["延迟"]

    asyncio.run(go())


def test_realtime_fallback_cache_does_not_extend_minute_source_age(monkeypatch, isolated_minute_reads):
    clock, calls = {"t": 100.0}, []
    monkeypatch.setattr(ff, "monotonic", lambda: clock["t"], raising=False)

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            return MinuteResponse()

    async def no_realtime():
        return None

    monkeypatch.setattr(ff, "_HTTP", HTTP())
    monkeypatch.setattr(ff, "_em_ulist", no_realtime)

    async def go():
        await asyncio.gather(ff._em_fflow_kline("1.000001"), ff._em_fflow_kline("0.399107"))
        clock["t"] = 115
        assert (await ff.get_fund_flow_realtime())["available"] is True
        assert len(calls) == 2
        clock["t"] = 131  # 输出缓存仅 16s，最老源已 31s，须尊重原实时 30s 预算。
        assert (await ff.get_fund_flow_realtime())["available"] is True
        assert len(calls) == 4

    asyncio.run(go())


def test_intraday_cache_does_not_extend_minute_source_age(monkeypatch, isolated_minute_reads):
    clock, calls = {"t": 100.0}, []
    monkeypatch.setattr(ff, "monotonic", lambda: clock["t"], raising=False)

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            return MinuteResponse()

    monkeypatch.setattr(ff, "_HTTP", HTTP())

    async def go():
        await asyncio.gather(ff._em_fflow_kline("1.000001"), ff._em_fflow_kline("0.399107"))
        clock["t"] = 145
        await ff.get_fund_flow_intraday()
        assert len(calls) == 2
        clock["t"] = 161  # 输出缓存仅 16s，最老源已 61s，不能继续复用。
        await ff.get_fund_flow_intraday()
        assert len(calls) == 4

    asyncio.run(go())


def test_realtime_cache_age_is_bound_to_used_source_version(monkeypatch, isolated_minute_reads):
    clock, calls = {"t": 100.0}, []
    monkeypatch.setattr(ff, "monotonic", lambda: clock["t"])

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            return MinuteResponse(main=len(calls) * 100000000)

    async def no_realtime():
        return None

    monkeypatch.setattr(ff, "_HTTP", HTTP())
    monkeypatch.setattr(ff, "_em_ulist", no_realtime)

    async def go():
        assert (await ff.get_fund_flow_realtime())["items"]["total"]["main"] == 3
        clock["t"] = 120
        ff._FFLOW_MIN_CACHE.invalidate()
        await asyncio.gather(ff._em_fflow_kline("1.000001"), ff._em_fflow_kline("0.399107"))
        clock["t"] = 131
        # t=100 的聚合已过期；t=120 的输入可复用，但不能替旧值续命。
        assert (await ff.get_fund_flow_realtime())["items"]["total"]["main"] == 7
        assert len(calls) == 4

    asyncio.run(go())


def test_realtime_refreshes_source_that_expires_while_other_market_loads(monkeypatch, isolated_minute_reads):
    clock, calls = {"t": 100.0}, []
    monkeypatch.setattr(ff, "monotonic", lambda: clock["t"])

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            if params["secid"] == "0.399107":
                for _ in range(5):
                    await asyncio.sleep(0)  # 沪市缓存先交付，深市响应后推进源年龄。
                clock["t"] = 155
            return MinuteResponse(main=len(calls) * 100000000)

    async def no_realtime():
        return None

    monkeypatch.setattr(ff, "_HTTP", HTTP())
    monkeypatch.setattr(ff, "_em_ulist", no_realtime)

    async def go():
        await ff._em_fflow_kline("1.000001")
        clock["t"] = 129
        out = await ff.get_fund_flow_realtime()
        assert out["items"]["total"]["main"] == 7  # 旧沪市值 1 未混入新深圳值。
        assert len(calls) == 4

    asyncio.run(go())


def test_intraday_warm_payload_is_not_mutated_by_reader(monkeypatch, isolated_minute_reads):
    calls = []

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            return MinuteResponse()

    monkeypatch.setattr(ff, "_HTTP", HTTP())

    async def go():
        cold = await ff.get_fund_flow_intraday()
        cold["items"][0]["main"] = 99
        cold["degraded"].append("consumer change")
        warm = await ff.get_fund_flow_intraday()
        assert warm["items"][0]["main"] == 2 and warm["degraded"] == []
        assert warm["items"][0]["small"] is None and len(calls) == 2

    asyncio.run(go())


def test_minute_successful_parsed_empty_is_distinct_from_failure(monkeypatch, isolated_minute_reads):
    calls = []

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            response = MinuteResponse()
            response.json = lambda: {"data": {"klines": ["malformed record"]}}
            return response

    monkeypatch.setattr(ff, "_HTTP", HTTP())

    async def go():
        assert await ff._em_fflow_kline("1.000001") == []
        assert await ff._em_fflow_kline("1.000001") == []
        assert calls == ["1.000001"]  # 原解析契约的空列表可复用，失败 None 不可复用。

    asyncio.run(go())


def test_intraday_alternating_slow_markets_return_degraded_without_unbounded_reload(monkeypatch, isolated_minute_reads):
    clock, calls = {"t": 100.0}, []
    original_sleep = asyncio.sleep
    monkeypatch.setattr(ff, "monotonic", lambda: clock["t"])

    async def yield_spacing(seconds):
        await original_sleep(0)

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            clock["t"] += 70  # 两源交替晚到，每轮最老输入都会超过 60s。
            return MinuteResponse()

    monkeypatch.setattr(ff, "_HTTP", HTTP())
    monkeypatch.setattr(ff.asyncio, "sleep", yield_spacing)

    async def go():
        out = await asyncio.wait_for(ff.get_fund_flow_intraday(), 0.1)  # 仅测试死循环看门狗。
        assert out["items"] == [] and any("过期" in d for d in out["degraded"])
        assert calls == ["1.000001", "0.399107"]
        assert len(ff._INTRADAY_CACHE) == 0

    asyncio.run(go())


def test_realtime_second_slow_market_recheck_still_expired_is_unavailable(monkeypatch, isolated_minute_reads):
    clock, calls = {"t": 100.0}, []
    monkeypatch.setattr(ff, "monotonic", lambda: clock["t"])

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            if params["secid"] == "0.399107":
                for _ in range(5):
                    await asyncio.sleep(0)
                clock["t"] += 70  # 首读和唯一重核的深圳源都在沪市过期后才到。
            return MinuteResponse()

    async def no_realtime():
        return None

    monkeypatch.setattr(ff, "_HTTP", HTTP())
    monkeypatch.setattr(ff, "_em_ulist", no_realtime)

    async def go():
        out = await asyncio.wait_for(ff.get_fund_flow_realtime(), 0.1)
        assert out["available"] is False and "过期" in out["reason"]
        assert any("过期" in d for d in out["degraded"])
        assert calls == ["1.000001", "0.399107"] * 2
        assert len(ff._FLOW_RT_CACHE) == 0

    asyncio.run(go())


def test_minute_source_scheduling_expiry_has_only_one_recheck(monkeypatch, isolated_minute_reads):
    clock, calls = {"t": 100.0}, []

    def delayed_resume():
        clock["t"] += 70  # 每次恢复执行都晚于源年龄预算，不能递归无限刷新。
        return clock["t"]

    monkeypatch.setattr(ff, "monotonic", delayed_resume)

    class HTTP:
        async def get(self, url, params, headers):
            calls.append(params["secid"])
            return MinuteResponse()

    monkeypatch.setattr(ff, "_HTTP", HTTP())

    async def go():
        assert await asyncio.wait_for(ff._em_fflow_kline("1.000001"), 0.1) is None
        assert calls == ["1.000001"] * 2
        assert len(ff._FFLOW_MIN_CACHE) == 0

    asyncio.run(go())
