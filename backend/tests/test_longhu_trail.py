"""龙虎榜跨日题材轨迹（B3）测试——aggregate_concept_trail 纯函数为主。"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.services.longhu_trail import UNCLASSIFIED, aggregate_concept_trail


def _rec(symbol="600519", net=1.0e8, tags="白酒,消费", range_days=1):
    return SimpleNamespace(
        symbol=symbol, net_buy=net, concept_tags=tags, range_days=range_days
    )


def test_conservation_split_across_concepts():
    """守恒：单股净额按概念数等分，Σ题材分摊 = 股净额（官方口径）。"""
    trail = aggregate_concept_trail([(date(2026, 9, 4), [_rec(net=1.0e8, tags="白酒,消费")])])
    by = {t["concept"]: t["daily"][0]["net"] for t in trail}
    assert by["白酒"] == 0.5e8 and by["消费"] == 0.5e8
    assert sum(by.values()) == 1.0e8


def test_multi_day_series_and_missing_day_is_none():
    """跨日序列：某概念当日缺席 → net=None（断线显示，不冒充 0）。"""
    d1, d2 = date(2026, 9, 3), date(2026, 9, 4)
    trail = aggregate_concept_trail([
        (d1, [_rec(net=1.0e8, tags="白酒")]),
        (d2, [_rec(symbol="000001", net=2.0e8, tags="银行")]),
    ])
    by = {t["concept"]: t for t in trail}
    assert by["白酒"]["daily"][0]["net"] == 1.0e8
    assert by["白酒"]["daily"][1]["net"] is None
    assert by["银行"]["daily"][0]["net"] is None
    assert by["银行"]["daily"][1]["net"] == 2.0e8
    assert by["白酒"]["total"] == 1.0e8


def test_three_day_board_excluded():
    """跨口径红线：range_days=3 的三日榜绝不混入日榜聚合（8-31 实测符号可相反）。"""
    trail = aggregate_concept_trail([
        (date(2026, 9, 4), [_rec(net=5.0e8, range_days=3), _rec(net=1.0e8, range_days=1)]),
    ])
    total = sum(t["daily"][0]["net"] for t in trail)  # 白酒+消费 各 0.5e8
    assert total == 1.0e8, "只有日榜的 1 亿参与等分，三日榜 5 亿被剔除"


def test_none_net_and_untagged_go_missing_or_unclassified():
    """净额缺失跳过；无概念标签归「未分类」，不冒充。"""
    trail = aggregate_concept_trail([
        (date(2026, 9, 4), [
            _rec(symbol="1", net=None),
            _rec(symbol="2", net=3.0e8, tags=None),
        ]),
    ])
    assert len(trail) == 1
    assert trail[0]["concept"] == UNCLASSIFIED
    assert trail[0]["daily"][0]["net"] == 3.0e8


def test_sorted_by_abs_total_desc():
    """按总净额绝对值降序（净流出大户也排前面）。"""
    trail = aggregate_concept_trail([
        (date(2026, 9, 4), [
            _rec(symbol="1", net=-5.0e8, tags="甲"),
            _rec(symbol="2", net=2.0e8, tags="乙"),
        ]),
    ])
    assert [t["concept"] for t in trail] == ["甲", "乙"]
    assert trail[0]["total"] == -5.0e8
    assert trail[0]["first"] == -5.0e8 and trail[0]["last"] == -5.0e8
