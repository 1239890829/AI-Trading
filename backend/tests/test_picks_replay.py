"""跨日回放器单测：用合成数据验证换股门槛的稳定性效果与统计口径。"""

from __future__ import annotations

from app.picks.replay import holding_spans, replay_picks, stability_stats


def _c(symbol: str, score: float) -> dict:
    return {"symbol": symbol, "score": score}


def test_holding_spans_splits_on_gap():
    """中断后重新出现要分段：连续持有天数不能被"两次短暂持有"平均掉。"""
    spans = holding_spans([["A", "B"], ["A"], ["A", "B"]])
    assert spans["A"] == [3]  # 三天连续
    assert spans["B"] == [1, 1]  # 中间断过 → 两段


def test_stability_stats_basic():
    """1 次换股 / 2 个间隔 / 容量 5 → 日均换手 10%。"""
    daily = [["A", "B", "C"], ["A", "B", "C"], ["A", "B", "D"]]
    s = stability_stats(daily, max_picks=5)
    assert s["days"] == 3
    assert s["replacements_total"] == 1  # D 换入
    assert s["replacements_per_day"] == 0.5
    assert s["avg_turnover_pct"] == 10.0
    assert s["unique_symbols"] == 4
    assert s["max_holding_days"] == 3


def test_stability_stats_single_day_has_no_turnover():
    """单日样本不给换手指标（None 而非 0）——避免把"无数据"读成"零换手"。"""
    s = stability_stats([["A", "B"]], max_picks=5)
    assert s["avg_turnover_pct"] is None
    assert s["replacements_per_day"] is None
    assert s["unique_symbols"] == 2


def test_replacement_threshold_keeps_stable_members():
    """核心契约：分数小幅波动不换股，只有大幅超越才换。"""
    days = [
        ("d1", [_c("A", 80), _c("B", 70), _c("C", 60), _c("D", 50), _c("E", 40), _c("F", 30)]),
        # B/C/D/E 分数上下浮动 3 分（<15 门槛）→ 全部保留
        ("d2", [_c("A", 78), _c("B", 72), _c("C", 61), _c("D", 49), _c("E", 41), _c("F", 32)]),
    ]
    out = replay_picks(days, threshold=15.0, max_picks=5)
    assert out["daily"][1]["replaced"] == []
    assert out["stats"]["replacements_total"] == 0
    assert out["stats"]["avg_holding_days"] == 2.0


def test_replacement_threshold_allows_strong_challenger():
    """新候选超出最弱者 ≥15 分 → 换掉最弱者。"""
    days = [
        ("d1", [_c("A", 80), _c("B", 70), _c("C", 60), _c("D", 50), _c("E", 40)]),
        # F=70 超出最弱者 E=40 达 30 分 → 换入 F、换出 E
        ("d2", [_c("A", 80), _c("B", 70), _c("C", 60), _c("D", 50), _c("F", 70), _c("E", 40)]),
    ]
    out = replay_picks(days, threshold=15.0, max_picks=5)
    swaps = out["daily"][1]["replaced"]
    assert len(swaps) == 1
    assert swaps[0]["out"] == "E" and swaps[0]["in"] == "F"
    assert "E" not in out["daily"][1]["symbols"]


def test_threshold_effect_quantifies_savings():
    """对照组：无门槛时同样的数据会换更多次——差值即门槛效果。"""
    days = [
        ("d1", [_c("A", 80), _c("B", 70), _c("C", 60), _c("D", 50), _c("E", 40), _c("F", 39)]),
        # 排序在 D/E 附近翻转：无门槛会换 2 只，有门槛（差 1 分）一只都不换
        ("d2", [_c("A", 80), _c("B", 70), _c("C", 60), _c("F", 51), _c("E", 41), _c("D", 40)]),
    ]
    out = replay_picks(days, threshold=15.0, max_picks=5)
    assert out["stats"]["replacements_total"] == 0
    assert out["baseline"]["replacements_total"] == 1  # F 挤掉 D
    assert out["threshold_effect"]["swaps_avoided"] == 1


def test_replay_on_empty_input():
    out = replay_picks([])
    assert out["daily"] == []
    assert out["stats"]["days"] == 0
    assert out["threshold_effect"]["swaps_avoided"] == 0


def test_avg_holding_days_reflects_stability():
    """门槛越高 → 平均持有天数越长（单调性检查）。"""
    days = [
        ("d1", [_c("A", 80), _c("B", 70), _c("C", 60), _c("D", 50), _c("E", 40), _c("F", 35)]),
        ("d2", [_c("A", 79), _c("B", 69), _c("C", 59), _c("E", 52), _c("D", 48), _c("F", 45)]),
        ("d3", [_c("A", 78), _c("B", 68), _c("C", 58), _c("F", 56), _c("E", 50), _c("D", 44)]),
    ]
    loose = replay_picks(days, threshold=30.0, max_picks=5)
    tight = replay_picks(days, threshold=0.0, max_picks=5)
    assert loose["stats"]["avg_holding_days"] >= tight["stats"]["avg_holding_days"]
