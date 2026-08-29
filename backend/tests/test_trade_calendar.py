"""交易日历测试。

只测纯函数；`trading_days()` 依赖网络，不在单元测试范围内（由接口层验证）。
这些边界函数是整个情绪计算的地基：日期锚错一点，后面所有指标都是自指计算。
"""
from __future__ import annotations

from datetime import date

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
