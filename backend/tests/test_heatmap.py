"""云图聚合测试：分组/市值 TopN+"其他"聚合/降级标注/加权涨跌幅（纯函数，无网络）。"""

from app.services.heatmap_service import build_heatmap


def _row(sym: str, pct: int | float, nmc: float, price: float = 10.0, name: str | None = None, amount: float = 1e8) -> dict:
    return {"symbol": sym, "name": name or sym, "price": price, "change_pct": pct, "nmc": nmc, "amount": amount}


def test_groups_by_industry_and_aggregates_rest():
    rows = [
        _row("600519", 1.0, nmc=200000),   # 白酒 20 亿
        _row("000858", -1.0, nmc=50000),   # 白酒 5 亿
        _row("000560", 3.0, nmc=62000),    # 房产服务 6.2 亿
    ]
    imap = {"600519": "白酒", "000858": "白酒", "000560": "房产服务"}
    out = build_heatmap(rows, imap, top_per_group=1)
    by = {g["industry"]: g for g in out["groups"]}
    assert set(by) == {"白酒", "房产服务"}
    assert by["白酒"]["float_cap_yi"] == 25.0  # (200000+50000)万元 → 亿
    # 组内市值 Top1 = 茅台，五粮液并入「其他」
    names = [s["name"] for s in by["白酒"]["stocks"]]
    assert names[0] == "600519"
    assert any("其他" in n for n in names[1:])
    other = next(s for s in by["白酒"]["stocks"] if "其他" in s["name"])
    assert other["is_aggregate"] and other["float_cap_yi"] == 5.0
    # 组涨跌幅 = 市值加权：(1.0*20 + (-1.0)*5) / 25 = 0.6
    assert by["白酒"]["change_pct_w"] == 0.6


def test_missing_industry_falls_back_to_unclassified():
    rows = [_row("600519", 1.0, nmc=20000), _row("600560", 2.0, nmc=1000)]
    out = build_heatmap(rows, {})  # 映射缺失
    by = {g["industry"] for g in out["groups"]}
    assert by == {"未分类"}
    assert out["industry_coverage"] == 0.0


def test_bad_rows_skipped_and_counted():
    rows = [
        _row("600519", 1.0, nmc=20000),
        _row("bad001", None, nmc=20000),   # 无涨跌幅
        _row("bad002", 1.0, nmc=0),        # 无市值
        _row("bad003", 1.0, nmc=None),     # 无市值
    ]
    out = build_heatmap(rows, {})
    assert out["count"] == 1
    assert out["skipped_no_quote"] == 3
    assert out["breadth_summary"] == {"up": 1, "down": 0, "flat": 0}


def test_breadth_summary_counts_signs():
    rows = [
        _row("a", 1.0, nmc=100), _row("b", -2.0, nmc=100),
        _row("c", 0.0, nmc=100), _row("d", 5.0, nmc=100),
    ]
    out = build_heatmap(rows, {})
    assert out["breadth_summary"] == {"up": 2, "down": 1, "flat": 1}
    assert out["total_amount_yi"] == round(4 * 1e8 / 1e8, 1)
