from __future__ import annotations

import pytest

from app.market.normalizer import (
    normalize_index,
    normalize_kline_row,
    normalize_limit_up,
    normalize_longhu,
    normalize_order_book,
    normalize_quote,
    normalize_trade,
)
from app.schemas.market import Quality


def test_normalize_index():
    raw = {"f12": "000001", "f13": 1, "f14": "上证指数", "f2": 3952.18, "f3": -0.11, "f4": -4.39,
           "f6": 970365152112.9, "f124": 1787904693}
    q = normalize_index(raw)
    assert q.symbol == "000001"
    assert q.market == "SH"
    assert q.name == "上证指数"
    assert q.price == 3952.18
    assert q.change_pct == -0.11
    assert q.amount == 970365152112.9
    assert q.data_timestamp is not None
    assert q.source == "eastmoney"


def test_normalize_quote_maps_fields_and_converts_volume():
    raw = {"f12": "600519", "f13": 1, "f14": "贵州茅台", "f2": 1445.9, "f3": 1.23, "f4": 17.6,
           "f5": 32000, "f6": 4.6e9, "f8": 0.25, "f15": 1450.0, "f16": 1425.0, "f17": 1430.0,
           "f18": 1428.3, "f124": 1787904693}
    q = normalize_quote(raw)
    assert q.symbol == "600519"
    assert q.price == 1445.9
    assert q.high == 1450.0 and q.low == 1425.0 and q.open == 1430.0 and q.prev_close == 1428.3
    assert q.volume == 3_200_000  # 手 → 股
    assert q.amount == 4.6e9
    assert q.turnover_rate == 0.25
    assert q.quality is Quality.high


@pytest.mark.parametrize("symbol,source_market,market", [
    ("920627", 0, "BJ"), ("430047", "0", "BJ"), ("833171", 0, "BJ"),
    ("900901", 1, "SH"), ("200002", 0, "SZ"), ("510300", 1, "SH"),
    ("600519", 1, "SH"), ("000001", 0, "SZ"),
])
def test_normalize_stock_quote_uses_code_with_eastmoney_market_id(symbol, source_market, market):
    # 920627/f13=0身份来自已存官网成员；其余是合成ulist字段，
    # 不把官网按f152缩放的价格冒充fltt=2报价实抓。
    quote = normalize_quote({"f12": symbol, "f13": source_market, "f2": 17.61})
    assert (quote.symbol, quote.market, quote.price) == (symbol, market, 17.61)


@pytest.mark.parametrize("symbol,source_market", [
    ("920627", 1), ("430047", 1), ("833171", 1), ("900901", 0),
    ("200002", 1), ("510300", 0), ("600519", 0), ("000001", 1),
])
def test_normalize_stock_quote_rejects_conflicting_source_market(symbol, source_market):
    with pytest.raises(ValueError, match="market identity"):
        normalize_quote({"f12": symbol, "f13": source_market, "f2": 10})


@pytest.mark.parametrize("symbol,source_market,market", [
    ("920627", None, None), ("920627", 2, "2"), ("700001", 1, "SH"),
])
def test_normalize_stock_quote_does_not_infer_missing_or_unknown_source_market(symbol, source_market, market):
    assert normalize_quote({"f12": symbol, "f13": source_market}).market == market


def test_normalize_quote_handles_suspended_dash_values():
    raw = {"f12": "600519", "f13": 1, "f14": "贵州茅台", "f2": "-", "f3": "-", "f4": "-",
           "f5": "-", "f6": "-", "f8": "-", "f15": "-", "f16": "-", "f17": "-", "f18": 1428.3}
    q = normalize_quote(raw)
    assert q.price is None
    assert q.volume is None
    assert q.prev_close == 1428.3
    # 质量判定属于 validator：停牌缺价 → low（missing_price）
    from app.data_quality.validator import validate_quote

    validate_quote(q, live=True)  # 停牌缺价罚分仅交易时段生效，显式盘中语境
    assert q.quality is Quality.low
    assert "missing_price" in q.quality_reasons


def test_normalize_order_book():
    raw = {"f86": 1787904693, "f12": 10.02, "f11": 1200, "f14": 10.03, "f13": 800,
           "f31": 9.99, "f32": 1500, "f33": 9.98, "f34": 2200}
    ob = normalize_order_book("600519", raw)
    assert ob.asks[0].price == 10.02 and ob.asks[0].volume == 1200
    assert ob.asks[1].price == 10.03
    assert ob.bids[0].price == 9.99 and ob.bids[0].volume == 1500
    assert ob.bids[1].price == 9.98
    assert ob.data_timestamp is not None


def test_normalize_kline_row():
    row = "2026-08-27,1680.00,1700.00,1710.00,1675.00,32000,5400000000.00,2.1,0.71,12.0,0.35"
    bar = normalize_kline_row("600519", "1d", row)
    assert bar is not None
    assert bar.open == 1680.0 and bar.close == 1700.0 and bar.high == 1710.0 and bar.low == 1675.0
    assert bar.volume == 32000
    assert bar.amount == 5.4e9
    assert bar.change_pct == 0.71
    assert bar.turnover_rate == 0.35
    assert bar.timeframe == "1d"


def test_normalize_kline_row_minute():
    bar = normalize_kline_row("600519", "1m", "2026-08-28 09:31,1680.0,1681.0,1682.0,1679.5,1200,201800000.0,0.1,0.06,1.2,0.01")
    assert bar is not None
    assert bar.ts.hour == 9 and bar.ts.minute == 31


def test_normalize_limit_up():
    raw = {"c": "000712", "n": "锦龙股份", "p": 11800, "zdp": 9.972, "fbt": 92500, "lbt": 92500,
           "fund": 237252275, "hs": 1.69, "lbc": 3, "zbc": 0, "zttj": {"days": 3, "ct": 3}}
    from datetime import date

    rec = normalize_limit_up(raw, date(2026, 8, 28))
    assert rec.symbol == "000712"
    assert rec.price == 11.8  # p ×1000
    assert rec.first_seal_time == "09:25:00"
    assert rec.last_seal_time == "09:25:00"
    assert rec.consecutive_boards == 3
    assert rec.boards_stat == "3天3板"
    assert rec.break_count == 0
    assert rec.seal_amount == 237252275


def test_normalize_limit_up_official_price_contract_preserves_source_fields():
    """2026-10-13 15:53 BJ 官方 ZTPool 摘录；同日 THS/腾讯均为 7.36 元。"""
    from datetime import date

    raw = {"c": "002343", "n": "慈文传媒", "p": 7360,
           "zdp": 10.014947891235352, "fbt": 92500, "lbt": 92500,
           "hybk": "影视院线"}
    rec = normalize_limit_up(raw, date(2026, 10, 13))
    assert rec is not None
    assert rec.symbol == "002343" and rec.name == "慈文传媒"
    assert rec.price == 7.36  # 官方 p=价格×1000；不是 73.60 元。
    assert rec.change_pct == raw["zdp"]  # 已是百分数，不随价格缩放。
    assert rec.trade_date == date(2026, 10, 13)
    assert rec.first_seal_time == rec.last_seal_time == "09:25:00"
    assert rec.industry_board == "影视院线"
    assert rec.reason is None  # 行业字段不能冒充缺失的涨停原因。
    assert rec.source == "eastmoney"


def test_normalize_longhu():
    raw = {"TRADE_DATE": "2026-08-27 00:00:00", "SECURITY_CODE": "002731",
           "SECURITY_NAME_ABBR": "华阳国际", "CLOSE_PRICE": 0.94, "CHANGE_RATE": -9.6154,
           "TURNOVERRATE": 4.0269, "BILLBOARD_DEAL_AMT": 14061638.65,
           "BILLBOARD_NET_AMT": -5200000.0, "BILLBOARD_BUY_AMT": 1200000.0,
           "BILLBOARD_SELL_AMT": 6400000.0, "EXPLAIN": "西藏自治区资金卖出，成功率8.18%"}
    rec = normalize_longhu(raw)
    assert rec.symbol == "002731"
    assert rec.trade_date.isoformat() == "2026-08-27"
    assert rec.change_pct == -9.6154
    assert rec.net_buy == -5200000.0
    assert "西藏" in (rec.reason or "")


def test_normalize_trade_row():
    t = normalize_trade("600519", "09:30:05,1445.90,120,173508000,2")
    assert t is not None
    assert t.price == 1445.90
    assert t.volume == 120
    assert t.side == "sell"
    assert t.ts is not None and t.ts.hour == 9
