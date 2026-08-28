from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.data_quality.validator import board_limit_pct, validate_order_book, validate_quote
from app.schemas.market import OrderBook, OrderBookLevel, Quality, Quote


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
    )
    assert q.quality is Quality.high
    assert q.quality_reasons == []


def test_exactly_10pct_jump_is_allowed():
    prev = make_quote(price=10.0)
    q = validate_quote(make_quote(price=11.0, high=11.0, change_pct=10.0, prev_close=10.0), prev)
    assert q.quality is Quality.high


def test_jump_beyond_10pct_is_low():
    prev = make_quote(price=10.0)
    q = validate_quote(make_quote(price=11.2, high=11.3, prev_close=10.0, change_pct=12.0), prev)
    assert q.quality is Quality.low
    assert "tick_jump_gt_10pct" in q.quality_reasons


def test_chinext_allows_20pct():
    prev = make_quote(symbol="300750", price=10.0)
    assert board_limit_pct(prev) == 0.20
    q = validate_quote(
        make_quote(symbol="300750", price=12.0, high=12.1, prev_close=10.0, change_pct=20.0), prev
    )
    assert q.quality is Quality.high
    q2 = validate_quote(
        make_quote(symbol="300750", price=12.5, high=12.6, prev_close=10.0, change_pct=25.0), prev
    )
    assert q2.quality is Quality.low


def test_st_narrower_limit():
    q = make_quote(name="ST 某某", symbol="600077")
    assert board_limit_pct(q) == 0.05


def test_non_positive_price_is_invalid():
    q = validate_quote(make_quote(price=0))
    assert q.quality is Quality.invalid
    assert "non_positive_price" in q.quality_reasons


def test_high_below_low_is_invalid():
    q = validate_quote(make_quote(high=9.0, low=9.5))
    assert q.quality is Quality.invalid
    assert "high_below_low" in q.quality_reasons


def test_price_outside_range_is_invalid():
    q = validate_quote(make_quote(price=11.0, high=10.5, low=9.8))
    assert q.quality is Quality.invalid
    assert "price_above_high" in q.quality_reasons


def test_future_timestamp_is_invalid():
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    q = validate_quote(make_quote(data_timestamp=future))
    assert q.quality is Quality.invalid
    assert "timestamp_in_future" in q.quality_reasons


def test_change_pct_mismatch_is_low():
    # 实际应约 +5%，声明为 +50%
    q = validate_quote(make_quote(price=10.5, prev_close=10.0, change_pct=50.0, high=10.6))
    assert q.quality is Quality.low
    assert "change_pct_mismatch" in q.quality_reasons


def test_time_regress_is_low():
    now = datetime.now(timezone.utc)
    prev = make_quote(data_timestamp=now)
    q = validate_quote(make_quote(price=10.01, high=10.5, data_timestamp=now - timedelta(seconds=30)), prev)
    assert q.quality is Quality.low
    assert "time_regress" in q.quality_reasons


def test_invalid_symbol():
    q = validate_quote(make_quote(symbol="60051"))
    assert q.quality is Quality.invalid


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
