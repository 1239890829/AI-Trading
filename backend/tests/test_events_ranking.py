"""事件排序测试（2026-09-04）：盘面相关性打分 / 相位加权 / 显式降级。"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.events.ranking import RankContext, phase_weight, score_event

NOW = datetime(2026, 9, 4, 12, 0)


def _ctx(**kw) -> RankContext:
    return RankContext(**kw)


def _score(**kw) -> dict:
    base = dict(
        impact_level="L2", four="hot", source_tier=3,
        published_at=NOW - timedelta(hours=1), half_life_hours=12.0,
        theme_names=[], symbol_chg=[], ctx=_ctx(), now=NOW,
    )
    base.update(kw)
    return score_event(**base)


# ---------------------------------------------------------------- 相位乘数


def test_phase_weight_table_and_missing():
    # 退潮期政策加权、热点降权
    assert phase_weight("policy", "退潮") == 1.5
    assert phase_weight("hot", "退潮") == 0.7
    # 高潮期热点放大
    assert phase_weight("hot", "高潮") == 1.5
    # 相位/分类缺失 → 1.0（三态，不臆造）
    assert phase_weight("policy", None) == 1.0
    assert phase_weight(None, "退潮") == 1.0
    assert phase_weight("hot", "未知相位") == 1.0


# ---------------------------------------------------------------- 打分与解释


def test_resonant_policy_beats_stock_rumor_in_cold_phase():
    """退潮期：政策+题材共振 事件必须压过 个股流水账。"""
    hot_ctx = _ctx(phase="退潮", theme_perf={"存储芯片": 4.2}, stock_chg={"600001": 2.0})
    a = _score(impact_level="L1", four="policy", source_tier=4,
               theme_names=["存储芯片"], symbol_chg=[2.0], ctx=hot_ctx)
    b = _score(four="hot", source_tier=2, published_at=NOW - timedelta(hours=20),
               half_life_hours=6.0, symbol_chg=[None], ctx=hot_ctx)
    assert a["score"] > b["score"]
    # 可解释：a 的理由包含题材共振与相位加权
    assert any("题材共振" in r and "存储芯片" in r for r in a["reasons"])
    assert any("退潮期" in r and "×1.50" in r for r in a["reasons"])
    # 可追溯：factors 记录了题材名/涨幅/相位乘数
    assert a["factors"]["theme_best"]["name"] == "存储芯片"
    assert a["factors"]["phase_weight"] == 1.5


def test_theme_resonance_scaled_by_change():
    """题材涨幅越高共振分越高；负向题材 → 共振 0 分（clamp）。"""
    up = _score(theme_names=["A"], symbol_chg=[], ctx=_ctx(phase="高潮", theme_perf={"A": 5.0}))
    down = _score(theme_names=["A"], symbol_chg=[], ctx=_ctx(phase="高潮", theme_perf={"A": -6.0}))
    assert up["score"] > down["score"]
    assert down["factors"].get("theme_best", {}).get("chg_pct") == -6.0


def test_degraded_context_explicit_not_faked():
    """上下文全缺失：显式降级说明，绝不冒充共振 0 分。"""
    r = _score(ctx=_ctx(degraded=["情绪不可用(X)"]), published_at=None, half_life_hours=None)
    joined = " ".join(r["reasons"])
    assert "盘面上下文不可用" in joined
    assert "按影响力/时效/来源排序" in joined
    # 影响力基线分仍在
    assert r["score"] >= 27.0


def test_score_capped_at_100():
    ctx = _ctx(phase="高潮", theme_perf={"A": 6.0}, stock_chg={"s": 5.0})
    r = _score(impact_level="L1", four="hot", source_tier=5,
               theme_names=["A"], symbol_chg=[5.0], ctx=ctx)
    assert r["score"] <= 100.0


def test_freshness_within_half_life_window():
    fresh = _score(published_at=NOW - timedelta(minutes=10))
    stale = _score(published_at=NOW - timedelta(hours=20), half_life_hours=6.0)
    assert fresh["score"] > stale["score"]
    assert any("时效" in r for r in fresh["reasons"])
