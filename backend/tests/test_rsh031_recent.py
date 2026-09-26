"""RSH-031 source completeness and pre-event denominator checks."""
import asyncio
import hashlib
import json
from datetime import date, timedelta

import duckdb
import pytest

from scripts.rsh031_recent_analysis import analyze
from scripts.rsh031_recent_pools import PROTOCOL, date_ms, fetch_pool, validate_page


class FakeProvider:
    def __init__(self, pages):
        self.pages = pages

    async def _get(self, _path, params):
        return self.pages[params["page"]]


def test_pool_pagination_requires_all_rows_and_unique_symbols():
    pages = {
        1: {"item": [{"thscode": f"{i:06d}.SZ"} for i in range(200)],
            "pagination": {"page": 1, "size": 200, "pages": 2, "total": 201}},
        2: {"item": [{"thscode": "000200.SZ"}],
            "pagination": {"page": 2, "size": 200, "pages": 2, "total": 201}},
    }
    row = asyncio.run(fetch_pool(FakeProvider(pages), date(2025, 10, 9), "limit-up-pool"))
    assert row["total"] == len(row["items"]) == 201
    pages[2]["item"][0]["thscode"] = "000000.SZ"
    with pytest.raises(ValueError, match="uniqueness"):
        asyncio.run(fetch_pool(FakeProvider(pages), date(2025, 10, 9), "limit-up-pool"))
    with pytest.raises(ValueError, match="page/size"):
        validate_page(pages[1], 2)


def test_analysis_keeps_symbol_without_outcome_day_bar_in_denominator(tmp_path):
    db = tmp_path / "market.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""CREATE TABLE daily_k(
        thscode VARCHAR, date_ms BIGINT, open_price DOUBLE, high_price DOUBLE,
        low_price DOUBLE, close_price DOUBLE, volume DOUBLE, turnover DOUBLE)""")
    days = []
    d = date(2025, 9, 10)
    while d <= date(2025, 10, 9):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    for index, day in enumerate(days):
        symbols = (["000001.SZ", "000002.SZ", "920001.BJ"]
                   if day < date(2025, 10, 9) else ["000001.SZ"])
        for symbol in symbols:
            price = 10 + index / 10
            con.execute("INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [symbol, date_ms(day), price, price + 0.1,
                         price - 0.1, price, 1000, 100000000])
    con.close()
    collection = tmp_path / "collection"
    collection.mkdir()
    rows = [
        {"trade_date": "2025-10-09", "pool": "limit-up-pool", "total": 1,
         "items": [{"thscode": "000001.SZ", "continue_day_cnt": 1}]},
        {"trade_date": "2025-10-09", "pool": "limit-break-pool", "total": 0,
         "items": []},
        {"trade_date": "2025-10-10", "pool": "limit-up-pool", "total": 0,
         "items": []},
        {"trade_date": "2025-10-10", "pool": "limit-break-pool", "total": 0,
         "items": []},
    ]
    payload = "".join(json.dumps(row) + "\n" for row in rows).encode()
    (collection / "pools.jsonl").write_bytes(payload)
    (collection / "manifest.json").write_text(json.dumps({
        "protocol": PROTOCOL, "complete": True, "trade_days": 2,
        "rows": {"limit-up-pool": 1, "limit-break-pool": 0},
        "pools_sha256": hashlib.sha256(payload).hexdigest(),
    }))
    result = analyze(db, collection, tmp_path / "output")
    exploration = next(row for row in result["denominator"]
                       if row["segment"] == "exploration")
    assert exploration["stock_days"] == 2
    assert exploration["first_boards"] == 1
    assert result["excluded_board"].startswith("beijing:")
    up = next(row for row in result["official_event_join"]
              if row["pool"] == "limit-up-pool")
    assert up["matched_prior_universe"] == 1
    assert result["source_progression"]["first_boards_with_next_day"] == 1
    assert result["source_progression"]["next_day_not_in_either_pool"] == 1
