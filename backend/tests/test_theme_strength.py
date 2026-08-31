"""题材内资金合力聚合测试（P1-5）。

核心契约：
- 归属 = 官方成分表（传入 theme_members），与涨停归因/关键词无关
- 涨停近似口径分 10cm/20cm
- 无行情成分记 missing，不冒充 0
- 清晰 basis（可解释纪律）
"""

from __future__ import annotations

from app.services.theme_catalog_service import aggregate_theme_strength


def _q(symbol: str, name: str, change_pct: float, amount: float = 1e8) -> dict:
    return {"symbol": symbol, "name": name, "price": 10.0, "change_pct": change_pct, "amount": amount}


MEMBERS = {"881156.TI": ["600105", "603118", "300750", "688981", "600000"]}
QUOTES = {
    "600105": _q("600105", "永鼎股份", 5.2),
    "603118": _q("603118", "共进股份", -1.3),
    "300750": _q("300750", "宁德时代", 12.0, 5e9),   # 20cm 板 12% 不算涨停
    "688981": _q("688981", "中芯国际", 20.5),          # 20cm 涨停
    "600000": _q("600000", "浦发银行", 0.0),
}


def test_basic_counts_and_limit_up_threshold():
    s = aggregate_theme_strength(MEMBERS, QUOTES)["881156.TI"]
    # 涨：600105/300750/688981；跌：603118；平：600000
    assert s["up"] == 3 and s["down"] == 1 and s["flat"] == 1
    assert s["missing"] == 0
    # 300750 +12% 在 20cm 阈值下不算涨停；688981 +20.5% 算
    assert s["limit_up_count"] == 1
    assert s["count"] == 5


def test_avg_change_is_equal_weight():
    s = aggregate_theme_strength(MEMBERS, QUOTES)["881156.TI"]
    expected = round((5.2 - 1.3 + 12.0 + 20.5 + 0.0) / 5, 2)
    assert s["avg_change_pct"] == expected


def test_total_amount_sums_components():
    s = aggregate_theme_strength(MEMBERS, QUOTES)["881156.TI"]
    assert s["total_amount"] == round(4 * 1e8 + 5e9, 0)


def test_top_gainers_sorted_desc():
    s = aggregate_theme_strength(MEMBERS, QUOTES)["881156.TI"]
    top = [g["symbol"] for g in s["top_gainers"]]
    assert top == ["688981", "300750", "600105"]


def test_missing_quotes_counted_not_zeroed():
    """部分成分无行情 → 记 missing 而非按 0 计（不冒充）。"""
    quotes = {k: v for k, v in QUOTES.items() if k != "600000"}
    s = aggregate_theme_strength(MEMBERS, quotes)["881156.TI"]
    assert s["missing"] == 1
    assert "1 只无行情" in s["basis"]


def test_unknown_theme_members_all_missing():
    s = aggregate_theme_strength({"889999.TI": ["600001", "600002"]}, QUOTES)["889999.TI"]
    assert s["up"] == 0 and s["down"] == 0 and s["missing"] == 2
    assert s["avg_change_pct"] is None
