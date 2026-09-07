"""交易日历测试。

只测纯函数；`trading_days()` 依赖网络，不在单元测试范围内（由接口层验证）。
这些边界函数是整个情绪计算的地基：日期锚错一点，后面所有指标都是自指计算。
"""
from __future__ import annotations

from datetime import date

from app.data_providers.composite import CompositeProvider
from app.market import trade_calendar as tc


# 2026-08-24(一) ~ 08-28(五) 为交易日，08-29/30 为周末
DAYS = [
    date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19),
    date(2026, 8, 20), date(2026, 8, 21),           # 周一至周五
    date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26),
    date(2026, 8, 27), date(2026, 8, 28),
]


def test_is_trade_day():
    assert tc.is_trade_day(DAYS, date(2026, 8, 28)) is True
    assert tc.is_trade_day(DAYS, date(2026, 8, 29)) is False  # 周六
    assert tc.is_trade_day(DAYS, date(2026, 8, 23)) is False  # 周日


def test_last_trade_date_anchors_back_on_non_trading_day():
    """核心场景：今天是周六，锚点必须回退到周五，而不是用周六。

    线上事故就是这里错了：date.today() 直接拿去查涨停池，接口静默回退，
    于是"今日池"和"昨日池"指向同一天。
    """
    assert tc.last_trade_date(DAYS, date(2026, 8, 29)) == date(2026, 8, 28)
    assert tc.last_trade_date(DAYS, date(2026, 8, 30)) == date(2026, 8, 28)
    assert tc.last_trade_date(DAYS, date(2026, 8, 28)) == date(2026, 8, 28)


def test_prev_trade_date_strictly_earlier():
    assert tc.prev_trade_date(DAYS, date(2026, 8, 28)) == date(2026, 8, 27)
    # 周一的前一交易日必须跳回上周五，不能是周日
    assert tc.prev_trade_date(DAYS, date(2026, 8, 24)) == date(2026, 8, 21)


def test_prev_trade_date_of_non_trading_day():
    """传入非交易日时，返回它之前的那个交易日（不是它自己）。"""
    assert tc.prev_trade_date(DAYS, date(2026, 8, 29)) == date(2026, 8, 28)


def test_prev_trade_date_none_before_range():
    assert tc.prev_trade_date(DAYS, date(2026, 8, 17)) is None


def test_recent_trade_dates_newest_first():
    got = tc.recent_trade_dates(DAYS, date(2026, 8, 28), 5)
    assert got == [
        date(2026, 8, 28), date(2026, 8, 27), date(2026, 8, 26),
        date(2026, 8, 25), date(2026, 8, 24),
    ]


def test_recent_trade_dates_from_non_trading_anchor():
    """锚点是周六时，从周五开始往前数，且不能把周六算进去。"""
    got = tc.recent_trade_dates(DAYS, date(2026, 8, 29), 3)
    assert got == [date(2026, 8, 28), date(2026, 8, 27), date(2026, 8, 26)]


def test_recent_trade_dates_clamped_at_boundary():
    """锚点是区间内最早一天时，只能给出它自己（不会越界返回负索引）。"""
    assert tc.recent_trade_dates(DAYS, date(2026, 8, 17), 20) == [date(2026, 8, 17)]


def test_nth_prev_trade_date():
    assert tc.nth_prev_trade_date(DAYS, date(2026, 8, 28), 1) == date(2026, 8, 27)
    assert tc.nth_prev_trade_date(DAYS, date(2026, 8, 28), 4) == date(2026, 8, 24)


def test_empty_calendar_is_safe():
    assert tc.last_trade_date([], date(2026, 8, 28)) is None
    assert tc.prev_trade_date([], date(2026, 8, 28)) is None
    assert tc.recent_trade_dates([], date(2026, 8, 28), 5) == []
    assert tc.is_trade_day([], date(2026, 8, 28)) is False


def test_normalize_dedups_and_sorts():
    assert tc._normalize([date(2026, 8, 28), date(2026, 8, 26), date(2026, 8, 28)]) == [
        date(2026, 8, 26), date(2026, 8, 28),
    ]


# ---------------------------------------------------------------- 主源选择


class _FakeOfficial:
    """只提供 get_trading_days 的 provider（模拟 ths）。"""

    name = "ths"

    def __init__(self, days):
        self._days = days

    async def get_trading_days(self):
        return self._days


class _FakeKlineOnly:
    """只提供 get_kline 的 provider（模拟腾讯）。"""

    name = "tencent"

    def __init__(self, days):
        self._days = days

    async def get_kline(self, symbol, timeframe, start, end):
        assert symbol == tc.INDEX_SYMBOL, "备源必须用上证综指"
        return [type("B", (), {"ts": __import__("datetime").datetime.combine(
            d, __import__("datetime").time(8, 0))})() for d in self._days]


def _run(coro):
    import asyncio

    tc.invalidate_cache()
    try:
        return asyncio.run(coro)
    finally:
        tc.invalidate_cache()


def test_parse_official_accepts_yyyymmdd_and_date():
    """官方端点返回 'YYYYMMDD' 字符串，也可能已是 date——两种都要能吃。"""
    assert tc._parse_official(["20260827", "20260828"]) == [
        date(2026, 8, 27), date(2026, 8, 28)]
    assert tc._parse_official([date(2026, 8, 28)]) == [date(2026, 8, 28)]
    assert tc._parse_official(["", None, "bad", "2026082"]) == []
    assert tc._parse_official(None) == []


def test_official_calendar_is_preferred_over_index_kline():
    """核心回归：官方端点必须优先于日 K 推导。

    首个版本只实现了日 K 推导，绕开了早就存在且 main.py 已在用的官方端点。
    实测官方 242 天是推导 124 天的完全超集，用推导的等于白白少一半历史。
    """
    official = ["20260826", "20260827", "20260828"] + [
        f"2026{d:02d}{x:02d}" for d in range(1, 8) for x in range(1, 3)]
    kline_days = [date(2026, 8, 27), date(2026, 8, 28)]   # 故意给得更少

    # 用真实 CompositeProvider：生产环境传的就是它，自制壳会漏测方法转发
    chain = CompositeProvider([_FakeOfficial(official), _FakeKlineOnly(kline_days)])

    got = _run(tc.trading_days(chain))
    assert len(got) > len(kline_days), "必须采用官方那份更长的日历"
    assert date(2026, 8, 26) in got, "官方独有日期必须出现在结果里"


def test_falls_back_to_index_kline_when_official_missing():
    """没有 provider 提供官方日历时，回退到上证日 K 推导。"""
    kline_days = [
        date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26),
        date(2026, 8, 27), date(2026, 8, 28),
    ]

    chain = CompositeProvider([_FakeKlineOnly(kline_days)])
    assert _run(tc.trading_days(chain)) == kline_days


def test_official_failure_falls_back():
    """官方端点抛异常时不能让日历整体挂掉。"""

    class _BrokenOfficial:
        name = "ths"

        async def get_trading_days(self):
            raise RuntimeError("boom")

    kline_days = [
        date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26),
        date(2026, 8, 27), date(2026, 8, 28),
    ]

    chain = CompositeProvider([_BrokenOfficial(), _FakeKlineOnly(kline_days)])
    assert _run(tc.trading_days(chain)) == kline_days


def test_both_sources_fail_raises(tmp_path, monkeypatch):
    """两条路都拿不到且无持久化兜底 → 抛错，绝不退回「只跳周末」的猜测逻辑。"""
    import pytest

    # 兜底文件若存在（真实后端跑过就会写），持久化路径会接管——本用例必须隔离
    monkeypatch.setattr(tc, "_PERSIST_PATH", tmp_path / "nonexistent.json")

    class _Nothing:
        name = "empty"
        providers = []

    with pytest.raises(RuntimeError):
        _run(tc.trading_days(_Nothing()))


def test_iter_providers_expands_composite_and_bare():
    """CompositeProvider 展开 .providers，裸 provider 包一层。"""
    p = _FakeOfficial([])
    assert tc._iter_providers(p) == [p]
    assert tc._iter_providers(None) == []
    comp = type("C", (), {"providers": [p]})()
    assert tc._iter_providers(comp) == [p]



def test_persisted_calendar_fallback(tmp_path, monkeypatch):
    """技术债 #10：双源全挂时读持久化日历（带时间戳的权威快照，非猜测）；
    无兜底文件才抛错——"拒绝猜测"语义保持不变。"""
    import json
    import pytest

    persist = tmp_path / "trade_calendar.json"
    monkeypatch.setattr(tc, "_PERSIST_PATH", persist)
    monkeypatch.setattr(tc, "_cached_days", [])
    monkeypatch.setattr(tc, "_cached_at", 0.0)

    async def no_official(provider):
        return []

    async def no_kline(provider, lookback_days=120):
        raise RuntimeError("network down")

    monkeypatch.setattr(tc, "_official_days", no_official)
    monkeypatch.setattr(tc, "_index_kline_days", no_kline)

    async def call():
        return await tc.trading_days(None)

    # 1) 无兜底文件 → 照旧抛错（拒绝猜测）
    with pytest.raises(RuntimeError):
        _run(call())

    # 2) 有兜底文件 → 双源全挂也能用，且周末不在日历里
    persist.write_text(json.dumps({
        "source": "official",
        "fetched_at": "2026-08-28T08:00:00+00:00",
        "days": [d.isoformat() for d in DAYS],
    }), encoding="utf-8")
    days = _run(call())
    assert date(2026, 8, 28) in days
    assert date(2026, 8, 29) not in days


# ---------------------------------------------------------------- 持久化质量闸门
# 09-04 实测缺陷：ths 失败退 index-kline 备源时，~80 天短日历无条件覆盖
# 243 天官方日历 → nth_prev_trade_date(n>80) 崩、兜底末日不含今天判非交易日。


def _read_persist(persist):
    import json

    return json.loads(persist.read_text(encoding="utf-8"))


def test_gate_blocks_short_source_overwriting_long_persist(tmp_path, monkeypatch):
    """核心回归：短备源（index-kline ~80 天）不得覆盖长官方日历（243 天）。"""
    import json

    persist = tmp_path / "trade_calendar.json"
    monkeypatch.setattr(tc, "_PERSIST_PATH", persist)

    long_days = [date(2026, 1, 1) + __import__("datetime").timedelta(days=i) for i in range(243)]
    persist.write_text(json.dumps({
        "source": "official", "fetched_at": "2026-09-02T00:00:00+00:00",
        "days": [d.isoformat() for d in long_days],
    }), encoding="utf-8")

    tc._persist_if_better(DAYS, "index-kline")  # 10 天 << 243*0.8
    body = _read_persist(persist)
    assert body["source"] == "official", "劣质备源不得覆盖官方日历"
    assert len(body["days"]) == 243


def test_gate_allows_comparable_update(tmp_path, monkeypatch):
    """官方≈官方的正常更新必须放行（否则日历永远不刷新）。"""
    import json

    persist = tmp_path / "trade_calendar.json"
    monkeypatch.setattr(tc, "_PERSIST_PATH", persist)
    persist.write_text(json.dumps({
        "source": "official", "fetched_at": "2026-09-02T00:00:00+00:00",
        "days": [d.isoformat() for d in DAYS],  # 10 天
    }), encoding="utf-8")

    tc._persist_if_better(DAYS, "official")  # 10 天 ≥ 10*0.8
    body = _read_persist(persist)
    assert body["source"] == "official"
    assert len(body["days"]) == 10  # 内容照常刷新


def test_gate_allows_first_persist_without_existing(tmp_path, monkeypatch):
    """无兜底文件时短源也要落盘（首次总得有底）。"""
    persist = tmp_path / "trade_calendar.json"
    monkeypatch.setattr(tc, "_PERSIST_PATH", persist)

    tc._persist_if_better(DAYS, "index-kline")
    body = _read_persist(persist)
    assert body["source"] == "index-kline"
    assert len(body["days"]) == len(DAYS)
