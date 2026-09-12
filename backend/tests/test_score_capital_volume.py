"""score_capital 量比倒 U 形状自证（P2-33①，KB-STOCK-32 实证改写）。

阈值依据 marketdb 10y 全 A 的 5 日基线量比分位（p50=0.91 / p90=1.65 / p95=2.08），
形状依据量比十分位次日收益的倒 U（D6 峰 +0.074%、D10 −0.127%/日）。
只钉量比子项（净流入/龙虎榜不属本改动面）。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.picks.engine import score_capital


def _volume_part(volume_ratio: float | None) -> tuple[float, str]:
    """只含量比子项的资金分（净流入 None、非龙虎榜 → 基线 50）。"""
    score, basis = score_capital(None, volume_ratio, False)
    return score, basis


def test_moderate_volume_gets_peak_bonus():
    score, basis = _volume_part(1.2)  # 适度放量区（≈p50-p90，实证峰区）
    assert score == 62.0  # 50 + 12
    assert "适度放量" in basis


def test_weakening_zone_neutral():
    score, basis = _volume_part(1.8)  # p90-p95 转弱区
    assert score == 50.0
    assert "转弱区" in basis


def test_extreme_volume_penalized():
    score, basis = _volume_part(2.5)  # ≥p95 极端放量
    assert score == 40.0  # 50 − 10
    assert "极端放量" in basis and "KB-STOCK-32" in basis


def test_boundaries_match_quantile_thresholds():
    # 边界取自分位数：0.9≈p50（峰区起点，含）、1.65=p90（转弱区起点，含）、2.1=p95（惩罚，含）
    assert _volume_part(0.9)[0] == 62.0
    assert _volume_part(1.64)[0] == 62.0   # 峰区末端
    assert _volume_part(1.65)[0] == 50.0   # p90 含 → 转弱区
    assert _volume_part(2.09)[0] == 50.0   # 转弱区末端
    assert _volume_part(2.1)[0] == 40.0    # p95 含 → 惩罚


def test_low_volume_keeps_existing_semantics():
    assert _volume_part(0.7)[0] == 55.0   # 0.5-0.9 弱加分（旧口径 0.5-0.8 落空区间的顺带修复）
    assert _volume_part(0.4)[0] == 42.0   # <0.5 显著缩量 −8（保留）
    assert _volume_part(None)[0] == 50.0  # 缺量比 → 不评分
