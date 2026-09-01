"""QuoteHub.get_quotes 的裸代码/指数撞码回归（2026-09-01 P0）。

背景：裸 000001（平安银行）查询曾直接命中 indices 返回上证指数数据
（实测返回 3979.88 冒充股票行情）。项目纪律：裸代码=股票，指数必须带前缀。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.market import Quote
from app.services.quote_hub import QuoteHub


def make_q(symbol: str, name: str, price: float) -> Quote:
    return Quote(
        symbol=symbol,
        name=name,
        market="SH",
        price=price,
        prev_close=price,
        change=0.0,
        change_pct=0.0,
        volume=1.0,
        amount=1.0,
        data_timestamp=datetime.now(timezone.utc),
        source="test",
    )


def test_bare_stock_symbol_never_falls_back_to_index():
    hub = QuoteHub(provider=None, poll_interval=10)
    hub.indices["000001"] = make_q("000001", "上证指数", 3979.88)
    # quotes 缓存里没有平安银行 → 裸代码查询不得命中指数缓存
    assert hub.get_quotes(["000001"]) == []
    # 000688 国城矿业 ↔ 科创50 同类撞码
    hub.indices["000688"] = make_q("000688", "科创50", 1679.0)
    assert hub.get_quotes(["000688"]) == []


def test_prefixed_index_query_hits_indices():
    hub = QuoteHub(provider=None, poll_interval=10)
    hub.indices["000001"] = make_q("000001", "上证指数", 3979.88)
    out = hub.get_quotes(["sh000001"])
    assert len(out) == 1
    assert out[0].name == "上证指数"
    assert out[0].symbol == "sh000001"  # 以查询形态返回


def test_watchlist_stock_takes_priority():
    hub = QuoteHub(provider=None, poll_interval=10)
    hub.indices["000001"] = make_q("000001", "上证指数", 3979.88)
    hub.quotes["000001"] = make_q("000001", "平安银行", 12.34)
    out = hub.get_quotes(["000001"])
    assert len(out) == 1
    assert out[0].name == "平安银行"
    assert out[0].price == 12.34
