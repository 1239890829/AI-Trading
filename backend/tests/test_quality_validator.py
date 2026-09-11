from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.data_quality.validator import board_limit_pct, validate_order_book, validate_quote
from app.schemas.market import OrderBook, OrderBookLevel, Quality, Quote
from app.core.bjtime import BJ_TZ  # S2-8 时区收敛


def make_quote(**kw) -> Quote:
    base = dict(
        symbol="600519",
        name="贵州茅台",
        market="SH",
        price=10.0,
        open=10.0,
        high=10.5,
        low=9.8,
        prev_close=10.0,
        change=0.0,
        change_pct=0.0,
        volume=1000.0,
        amount=10000.0,
        data_timestamp=datetime.now(timezone.utc),
        source="test",
    )
    base.update(kw)
    return Quote(**base)


def test_normal_tick_is_high():
    t0 = datetime.now(timezone.utc)
    prev = make_quote(data_timestamp=t0)
    q = validate_quote(
        make_quote(price=10.5, change_pct=5.0, prev_close=10.0, high=10.6, data_timestamp=t0 + timedelta(seconds=1)),
        prev,
        live=True,
    )
    assert q.quality is Quality.high
    assert q.quality_reasons == []


def test_exactly_10pct_jump_is_allowed():
    prev = make_quote(price=10.0)
    q = validate_quote(make_quote(price=11.0, high=11.0, change_pct=10.0, prev_close=10.0), prev, live=True)
    assert q.quality is Quality.high


def test_jump_beyond_10pct_is_low():
    prev = make_quote(price=10.0)
    q = validate_quote(make_quote(price=11.2, high=11.3, prev_close=10.0, change_pct=12.0), prev, live=True)
    assert q.quality is Quality.low
    assert "tick_jump_gt_10pct" in q.quality_reasons


def test_chinext_allows_20pct():
    prev = make_quote(symbol="300750", price=10.0)
    assert board_limit_pct(prev) == 0.20
    q = validate_quote(
        make_quote(symbol="300750", price=12.0, high=12.1, prev_close=10.0, change_pct=20.0), prev, live=True
    )
    assert q.quality is Quality.high
    q2 = validate_quote(
        make_quote(symbol="300750", price=12.5, high=12.6, prev_close=10.0, change_pct=25.0), prev, live=True
    )
    assert q2.quality is Quality.low


def test_st_main_board_same_as_main_limit():
    """2026-07-06 并轨：主板 ST 涨跌幅 5%→10%，不再收窄。"""
    q = make_quote(name="ST 某某", symbol="600077")
    assert board_limit_pct(q) == 0.10


def test_non_positive_price_is_invalid():
    q = validate_quote(make_quote(price=0), live=True)
    assert q.quality is Quality.invalid
    assert "non_positive_price" in q.quality_reasons


def test_high_below_low_is_invalid():
    q = validate_quote(make_quote(high=9.0, low=9.5), live=True)
    assert q.quality is Quality.invalid
    assert "high_below_low" in q.quality_reasons


def test_price_outside_range_is_invalid():
    q = validate_quote(make_quote(price=11.0, high=10.5, low=9.8), live=True)
    assert q.quality is Quality.invalid
    assert "price_above_high" in q.quality_reasons


# ---- 开盘「未建立」形态（2026-09-03：刷新后短暂"非法"的根因修复）----
# 盘前形态（价格=昨收、high/low=0）不会在 9:30:00 瞬间消失——开盘头几拍
# in_trading_window 已 True 而源形态未切换，把 0 当越界依据曾全部误判 invalid。


def test_live_unset_high_low_not_invalid():
    """盘中收到 high/low=0 的未建立形态 → 不判 invalid，降为 low 留痕。"""
    q = validate_quote(
        make_quote(price=3986.1, prev_close=3986.1, high=0.0, low=0.0, change_pct=0.0), live=True
    )
    assert q.quality is not Quality.invalid
    assert "price_above_high" not in q.quality_reasons
    assert "unset_high_low" in q.quality_reasons


def test_live_half_unset_bounds_not_invalid():
    """只有一档为 0（半未建立）也不判 high_below_low / price_below_low。"""
    q = validate_quote(make_quote(high=0.0, low=9.5), live=True)
    assert q.quality is not Quality.invalid
    assert "high_below_low" not in q.quality_reasons
    q2 = validate_quote(make_quote(high=10.5, low=0.0), live=True)
    assert q2.quality is not Quality.invalid


def test_live_real_range_violation_still_invalid():
    """真实越界（高低价均 >0）判 invalid 的语义保持不变。"""
    q = validate_quote(make_quote(price=11.0, high=10.5, low=9.8), live=True)
    assert q.quality is Quality.invalid
    assert "price_above_high" in q.quality_reasons
    q2 = validate_quote(make_quote(high=9.0, low=9.5), live=True)
    assert q2.quality is Quality.invalid
    assert "high_below_low" in q2.quality_reasons


def test_live_bounds_both_missing_no_reason():
    """源完全不提供 high/low（None）维持原行为：不罚分、不留痕。"""
    q = validate_quote(make_quote(high=None, low=None), live=True)
    assert q.quality is Quality.high
    assert q.quality_reasons == []


def test_future_timestamp_is_invalid():
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    q = validate_quote(make_quote(data_timestamp=future))
    assert q.quality is Quality.invalid
    assert "timestamp_in_future" in q.quality_reasons


def test_change_pct_mismatch_is_low():
    # 实际应约 +5%，声明为 +50%
    q = validate_quote(make_quote(price=10.5, prev_close=10.0, change_pct=50.0, high=10.6), live=True)
    assert q.quality is Quality.low
    assert "change_pct_mismatch" in q.quality_reasons


def test_time_regress_is_low():
    now = datetime.now(timezone.utc)
    prev = make_quote(data_timestamp=now)
    q = validate_quote(make_quote(price=10.01, high=10.5, data_timestamp=now - timedelta(seconds=30)), prev, live=True)
    assert q.quality is Quality.low
    assert "time_regress" in q.quality_reasons


def test_invalid_symbol():
    q = validate_quote(make_quote(symbol="60051"))
    assert q.quality is Quality.invalid


# ---- 时段感知（2026-09-01：盘前空数据不再误标"可疑/非法"）----
# 盘外（live=False）完整性罚分豁免，仅保留结构性错误；quality=high 时
# reasons 落 ["off_session"] 供展示层提示"休市"。


def test_off_session_missing_price_not_flagged():
    """盘前个股 quote price=None → high（此前误标 low"可疑"）。"""
    q = validate_quote(make_quote(price=None), live=False)
    assert q.quality is Quality.high
    assert q.quality_reasons == ["off_session"]


def test_off_session_zero_high_low_not_flagged():
    """盘前源返回昨收 + high/low=0 快照（实测上证指数场景）→ 不再 invalid"非法"。"""
    q = validate_quote(make_quote(price=3986.1, high=0.0, low=0.0, change_pct=0.0), live=False)
    assert q.quality is Quality.high
    assert q.quality_reasons == ["off_session"]


def test_off_session_price_zero_not_flagged():
    q = validate_quote(make_quote(price=0), live=False)
    assert q.quality is Quality.high


def test_off_session_change_pct_mismatch_not_flagged():
    q = validate_quote(make_quote(price=10.5, prev_close=10.0, change_pct=50.0, high=10.6), live=False)
    assert q.quality is Quality.high


def test_off_session_structural_still_invalid():
    """代码非法等结构性错误任何时段都判。"""
    q = validate_quote(make_quote(symbol="60051", price=None), live=False)
    assert q.quality is Quality.invalid


def test_off_session_negative_volume_still_invalid():
    q = validate_quote(make_quote(volume=-5.0), live=False)
    assert q.quality is Quality.invalid
    assert "negative_volume" in q.quality_reasons


def test_off_session_order_book_empty_not_flagged():
    ob = OrderBook(symbol="600519", bids=[], asks=[], source="test")
    validate_order_book(ob, live=False)
    assert ob.quality is Quality.high
    assert ob.quality_reasons == ["off_session"]


def test_order_book_valid():
    ob = OrderBook(
        symbol="600519",
        bids=[OrderBookLevel(price=10.0, volume=100), OrderBookLevel(price=9.99, volume=200)],
        asks=[OrderBookLevel(price=10.01, volume=100), OrderBookLevel(price=10.02, volume=200)],
        source="test",
    )
    validate_order_book(ob)
    assert ob.quality is Quality.high


def test_order_book_crossed_is_invalid():
    ob = OrderBook(
        symbol="600519",
        bids=[OrderBookLevel(price=10.05, volume=100)],
        asks=[OrderBookLevel(price=10.01, volume=100)],
        source="test",
    )
    validate_order_book(ob)
    assert ob.quality is Quality.invalid
    assert "bid1_above_ask1" in ob.quality_reasons


# ---- in_trading_window：时刻判定（不依赖日历内容的路径）----


def test_in_trading_window_weekend_false():
    from app.market.trade_calendar import in_trading_window

    # 2026-09-05 是周六（无论日历如何，周末恒 False）
    assert in_trading_window(datetime(2026, 9, 5, 10, 0, tzinfo=BJ_TZ)) is False


def test_in_trading_window_weekday_time_bounds():
    from app.market.trade_calendar import in_trading_window

    cst = BJ_TZ
    # 周三 2026-09-02；窗口 = 连续竞价（09:30–11:30 / 13:00–15:00），集合竞价豁免
    assert in_trading_window(datetime(2026, 9, 2, 9, 14, tzinfo=cst)) is False
    assert in_trading_window(datetime(2026, 9, 2, 9, 21, tzinfo=cst)) is False  # 集合竞价：形态不完整，豁免
    assert in_trading_window(datetime(2026, 9, 2, 9, 30, tzinfo=cst)) is True
    assert in_trading_window(datetime(2026, 9, 2, 11, 30, tzinfo=cst)) is True
    assert in_trading_window(datetime(2026, 9, 2, 12, 0, tzinfo=cst)) is False  # 午休：数据形态静止，豁免
    assert in_trading_window(datetime(2026, 9, 2, 13, 0, tzinfo=cst)) is True
    assert in_trading_window(datetime(2026, 9, 2, 15, 0, tzinfo=cst)) is True
    assert in_trading_window(datetime(2026, 9, 2, 15, 1, tzinfo=cst)) is False
