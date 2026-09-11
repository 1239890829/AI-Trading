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
        _row("600004", "ST 某某", -9.97),       # ST 跌停（并轨后主板 ST 同为 10cm）
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


def test_breadth_st_same_limit_as_main_board():
    """ST 并轨（2026-07-06）：主板 ST 不再收窄，4.99% 不算涨停、9.99% 才算。"""
    b = compute_breadth([_row("600004", "*ST 某某", 4.99), _row("600005", "某某", 4.99)])
    assert b["limit_up"] == 0 and b["limit_up"] != b["up"]
    c = compute_breadth([_row("600004", "*ST 某某", 9.99), _row("600005", "某某", 9.99)])
    assert c["limit_up"] == 2


def test_breadth_gem_st_still_20pct():
    """创业板/科创板 ST 维持 20%，不因风险警示降档。"""
    b = compute_breadth([_row("300123", "*ST 创业", 19.9), _row("300124", "创业普通", 10.0)])
    assert b["limit_up"] == 1


def test_breadth_excludes_new_stocks_from_limit_counts():
    # N/C 字头上市初期无涨跌幅限制，涨得再猛也不计入涨停
    b = compute_breadth([_row("301999", "N 新股", 500.0), _row("688999", "C 次新", 120.0)])
    assert b["limit_up"] == 0
    assert b["up"] == 2


# ---------------------------------------------------------------- 限价口径


def _limit_row(symbol, name, pct, price=10.0):
    """构造带真实价格量级的行（price 影响一档跳的大小，进而影响限价判定）。"""
    return {"symbol": symbol, "name": name, "price": price,
            "prev_close": round(price / (1 + pct / 100), 2), "change_pct": pct,
            "amount": 1e6}


def test_limit_down_uses_two_sided_band():
    """跌停是「落在限价上」，不是「跌得比限价还多」。

    两个方向都断言：贴线 -10% 计入跌停；-15% 超出限价则单独计 anomaly，
    既不冒充跌停，也不悄悄丢掉。
    """
    on_line = compute_breadth([_limit_row("600123", "某主板", -10.0, 12.0)])
    assert on_line["limit_down"] == 1
    beyond = compute_breadth([_limit_row("600123", "某主板", -15.0, 12.0)])
    assert beyond["limit_down"] == 0
    assert beyond["limit_anomaly"] == 1


def test_st_9p57_fall_is_normal_under_2026_rule():
    """回归修正（2026-09-11）：`*ST萃华` 跌 9.574% 曾被记为「限价口径存疑」。

    真因是 **ST 口径过期**（主板 ST 自 2026-07-06 起为 10%），不是数据源问题——
    9.574% 在 10% 限价内，既非跌停也非 anomaly。旧断言 anomaly=1 建立在
    「ST=5%」的过期前提上，属误报。
    """
    b = compute_breadth([_limit_row("002731", "*ST萃华", -9.574, 0.85)])
    assert b["limit_down"] == 0
    assert b["limit_anomaly"] == 0, "限价内跌幅不得误报为口径异常"
    # 而真·ST 跌停（贴 -9.85 极限带）必须计入
    assert compute_breadth([_limit_row("002731", "*ST萃华", -9.9, 0.85)])["limit_down"] == 1


def test_real_limit_down_still_counted():
    """真跌停（价格落在限价上）必须照常计入。"""
    rows = [_limit_row("002963", "豪尔赛", -10.009, 19.15)]
    assert compute_breadth(rows)["limit_down"] == 1


def test_limit_up_uses_two_sided_band():
    """涨停同理：涨停是落在 +limit 上。"""
    assert compute_breadth([_limit_row("600519", "贵州茅台", 10.0, 1300.0)])["limit_up"] == 1
    # 新股无涨跌幅（N 字头）本就被排除；这里验证超额涨幅不冒充涨停
    b = compute_breadth([_limit_row("600123", "某主板", 15.0, 12.0)])
    assert b["limit_up"] == 0
    assert b["limit_anomaly"] == 1


def test_creatboard_20pct_limit_not_miscounted():
    """创业板 20% 限制：跌 11% 只是普通下跌，不是跌停。"""
    b = compute_breadth([_limit_row("300750", "宁德时代", -11.29, 200.0)])
    assert b["limit_down"] == 0
    assert b["limit_anomaly"] == 0, "20% 限制下 -11.29% 完全正常，不算存疑"
