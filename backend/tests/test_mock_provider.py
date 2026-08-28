from __future__ import annotations

import asyncio
from datetime import datetime

from app.data_providers.mock import MockProvider

FIXED_CLOCK = lambda: datetime(2026, 8, 28, 10, 30, 45)  # noqa: E731


def test_mock_quotes_deterministic_within_minute():
    p = MockProvider(clock=FIXED_CLOCK)
    first = asyncio.run(p.get_quotes(["600519", "000001"]))
    second = asyncio.run(p.get_quotes(["600519", "000001"]))
    assert [q.symbol for q in first] == ["600519", "000001"]
    assert first[0].price == second[0].price
    assert all(q.source == "mock" for q in first)


def test_mock_quote_passes_quality_validation():
    from app.data_quality.validator import validate_quote

    p = MockProvider(clock=FIXED_CLOCK)
    quotes = asyncio.run(p.get_quotes(["600519", "300750", "688981"]))
    for q in quotes:
        validate_quote(q)
        assert q.quality.value in {"high", "low"}, (q.symbol, q.quality_reasons)
        assert q.high >= q.low
        assert q.volume is not None and q.amount is not None


def test_mock_kline_ascending_and_sufficient():
    p = MockProvider(clock=FIXED_CLOCK)
    bars = asyncio.run(p.get_kline("600519", "1d"))
    assert len(bars) == 250
    assert bars == sorted(bars, key=lambda b: b.ts)
    assert all(b.source == "mock" for b in bars)
    assert all(b.high >= b.low for b in bars)


def test_mock_order_book_consistent():
    p = MockProvider(clock=FIXED_CLOCK)
    ob = asyncio.run(p.get_order_book("600519"))
    assert ob is not None
    assert ob.bids[0].price < ob.asks[0].price
    assert len(ob.bids) == 5 and len(ob.asks) == 5
    assert ob.source == "mock"


def test_mock_limit_up_and_longhu():
    from datetime import date

    p = MockProvider(clock=FIXED_CLOCK)
    pool = asyncio.run(p.get_limit_up_pool(date(2026, 8, 28)))
    assert len(pool) == 6
    assert all(r.change_pct and r.change_pct >= 10 for r in pool)
    records = asyncio.run(p.get_longhu_records(date(2026, 8, 28)))
    assert len(records) == 5


def test_mock_search():
    p = MockProvider(clock=FIXED_CLOCK)
    by_code = asyncio.run(p.search("600519"))
    assert by_code and by_code[0].name == "贵州茅台"
    by_name = asyncio.run(p.search("宁德"))
    assert by_name and by_name[0].symbol == "300750"
