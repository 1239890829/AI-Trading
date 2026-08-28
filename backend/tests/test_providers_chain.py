from __future__ import annotations

import asyncio
from datetime import date

from app.data_providers.composite import CompositeProvider
from app.data_providers.eastmoney import ProviderError
from app.data_providers.mock import MockProvider
from app.data_providers.tencent import parse_quote, parse_order_book, parse_search_row

# 2026-08-28 盘后实测（sh600519）
REAL_SNAPSHOT = (
    "1~贵州茅台~600519~1297.40~1292.30~1289.00~16126~8576~7550"
    "~1297.35~5~1297.20~1~1297.10~3~1297.01~3~1297.00~11"
    "~1297.40~9~1297.50~11~1297.55~2~1297.68~1~1297.70~1"
    "~~20260828161500~5.10~0.39~1297.89~1288.00~1297.40/16126/2086008422~16126~208601~0.13~19.92~~"
).split("~")


def test_tencent_parse_quote_fields():
    q = parse_quote("sh", REAL_SNAPSHOT)
    assert q.symbol == "600519"
    assert q.name == "贵州茅台"
    assert q.market == "SH"
    assert q.price == 1297.40
    assert q.prev_close == 1292.30
    assert q.open == 1289.00
    assert q.high == 1297.89 and q.low == 1288.00
    assert q.change == 5.10 and q.change_pct == 0.39
    assert q.volume == 1_612_600  # 手 → 股
    assert q.amount == 2_086_010_000  # 万 → 元（与快照 2086008422 元一致）
    assert q.turnover_rate == 0.13
    assert q.source == "tencent"
    # 北京时间 16:15:00 → UTC 08:15
    assert q.data_timestamp is not None and q.data_timestamp.hour == 8 and q.data_timestamp.minute == 15


def test_tencent_parse_quote_passes_validator():
    from app.data_quality.validator import validate_quote

    q = validate_quote(parse_quote("sh", REAL_SNAPSHOT))
    assert q.quality.value in {"high", "low"}, q.quality_reasons


def test_tencent_order_book_bids_below_asks():
    ob = parse_order_book("600519", REAL_SNAPSHOT)
    assert len(ob.bids) == 5 and len(ob.asks) == 5
    assert ob.bids[0].price == 1297.35 and ob.bids[0].volume == 500
    assert ob.asks[0].price == 1297.40 and ob.asks[0].volume == 900
    assert ob.bids[0].price < ob.asks[0].price
    assert ob.bids == sorted(ob.bids, key=lambda l: -l.price)
    assert ob.asks == sorted(ob.asks, key=lambda l: l.price)


def test_tencent_search_row():
    item = parse_search_row("sh600519", "贵州茅台")
    assert item and item.symbol == "600519" and item.market == "SH"


def test_composite_failover_to_fallback():
    class FlakyThenDown:
        name = "broken"
        realtime = False
        dead = False

        async def get_quotes(self, symbols):
            if self.dead:
                raise ProviderError("down")
            self.dead = True  # 首次成功，之后失败
            return [{"symbol": "600519", "source": "broken"}]

        async def aclose(self):
            return None

    class Fallback:
        name = "fallback"
        realtime = False

        async def get_quotes(self, symbols):
            return [{"symbol": "600519", "source": "fallback"}]

    chain = CompositeProvider([FlakyThenDown(), Fallback()])
    first = asyncio.run(chain.get_quotes(["600519"]))
    assert first[0]["source"] == "broken"
    second = asyncio.run(chain.get_quotes(["600519"]))
    assert second[0]["source"] == "fallback"
    assert chain.switch_log and "get_quotes: broken -> fallback" in chain.switch_log[0]
    assert chain.name == "chain(broken→fallback)"


def test_composite_all_fail_raises():
    class Broken:
        name = "broken"
        realtime = False

        async def get_quotes(self, symbols):
            raise ProviderError("down")

    chain = CompositeProvider([Broken()])
    try:
        asyncio.run(chain.get_quotes(["600519"]))
        raise AssertionError("should raise")
    except ProviderError as exc:
        assert "all providers failed" in str(exc)


def test_composite_skips_empty_results():
    class Empty:
        name = "empty"
        realtime = False

        async def get_limit_up_pool(self, trade_date):
            return []

    class Full:
        name = "full"
        realtime = True

        async def get_limit_up_pool(self, trade_date):
            return [{"ok": True}]

    chain = CompositeProvider([Empty(), Full()])
    pool = asyncio.run(chain.get_limit_up_pool(date(2026, 8, 28)))
    assert pool == [{"ok": True}]
