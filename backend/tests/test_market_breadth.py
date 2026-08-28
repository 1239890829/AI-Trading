from __future__ import annotations

from app.market.breadth import compute_breadth
from app.market.sina_market import parse_row

RAW_ROW = {
    "symbol": "sh600519", "code": "600519", "name": "贵州茅台", "trade": "1297.40",
    "pricechange": 5.10, "changepercent": 0.393, "settlement": "1292.30", "open": "1289.00",
    "high": "1297.89", "low": "1288.00", "volume": 1612600, "amount": 2086010000,
    "mktcap": 16281562.5, "nmc": 16281562.5, "turnoverratio": 0.129, "ticktime": "15:30:01",
}


def test_parse_row_maps_fields():
    r = parse_row(RAW_ROW)
    assert r["symbol"] == "600519" and r["market"] == "SH"
    assert r["price"] == 1297.40 and r["prev_close"] == 1292.30
    assert r["volume"] == 1612600 and r["amount"] == 2086010000
    assert r["source"] == "sina_market"


def test_parse_row_rejects_bad_code():
    assert parse_row({"symbol": "shABC", "code": "ABC"}) is None


def _row(symbol: str, name: str, pct: float, amount: float = 1e8) -> dict:
    return {"symbol": symbol, "name": name, "price": 10.0, "change_pct": pct, "amount": amount}


def test_breadth_counts_and_limit_detection():
    rows = [
        _row("600519", "贵州茅台", 9.98),       # 主板涨停
        _row("300750", "宁德时代", 19.95),      # 创业板涨停
        _row("688981", "中芯国际", 10.0),       # 科创板 +10% 不算涨停
        _row("600000", "浦发银行", 5.2),
        _row("000001", "平安银行", -10.02),     # 主板跌停
        _row("000002", "万科A", -3.0),
        _row("600003", "某平股", 0.0),
        _row("600004", "ST 某某", -4.97),       # ST 跌停
        {"symbol": "600005", "name": "停牌", "price": None, "change_pct": None, "amount": 0},
    ]
    b = compute_breadth(rows)
    assert b["total"] == 9
    assert b["up"] == 4 and b["down"] == 3 and b["flat"] == 1
    assert b["suspended"] == 1
    assert b["limit_up"] == 2      # 茅台 + 宁德；中芯 +10% 是科创板正常
    assert b["limit_down"] == 2    # 平安银行 + ST
    assert b["total_amount"] == 8e8
    assert b["generated_at"]


def test_breadth_st_5pct_limit():
    b = compute_breadth([_row("600004", "*ST 某某", 4.99), _row("600005", "某某", 4.99)])
    assert b["limit_up"] == 1 and b["limit_up"] != b["up"]


def test_breadth_excludes_new_stocks_from_limit_counts():
    # N/C 字头上市初期无涨跌幅限制，涨得再猛也不计入涨停
    b = compute_breadth([_row("301999", "N 新股", 500.0), _row("688999", "C 次新", 120.0)])
    assert b["limit_up"] == 0
    assert b["up"] == 2
