"""空仓闸门（Stand-aside Gate）与风险档位（Risk Tier）单测。"""

from __future__ import annotations

from app.picks.gate import (
    ADVICE_NONE,
    BREAK_RATE_CEIL,
    LIMIT_DOWN_CEIL,
    PROMO_FLOOR,
    apply_gate_to_picks,
    evaluate_stand_aside,
)
from app.picks.risk import (
    STOP_PCT_CEIL,
    build_invalidations,
    exit_discipline,
    risk_tier_of,
    stop_loss_reference,
)


# ---------------------------------------------------------------- 空仓闸门


def test_no_trigger_when_market_healthy():
    g = evaluate_stand_aside(phase="发酵", promotion_1to2=0.5, break_rate=0.1, limit_down=2, prev_zt_median_pct=1.5)
    assert g["stand_aside"] is False
    assert g["level"] == "none"
    assert g["advice"] == ADVICE_NONE


def test_weak_phase_triggers_mild():
    g = evaluate_stand_aside(phase="退潮")
    assert g["stand_aside"] is True
    assert g["level"] == "mild"
    assert "退潮" in g["reasons"][0]


def test_frozen_phase_is_severe():
    """冰点直接升级为强预警（市场几乎无机会）。"""
    g = evaluate_stand_aside(phase="冰点")
    assert g["level"] == "strong"


def test_multiple_triggers_escalate():
    g = evaluate_stand_aside(promotion_1to2=PROMO_FLOOR - 0.05, break_rate=BREAK_RATE_CEIL + 0.05)
    assert g["level"] == "strong"
    assert len(g["reasons"]) == 2


def test_each_threshold_individually_fires():
    assert evaluate_stand_aside(promotion_1to2=PROMO_FLOOR - 0.01)["stand_aside"]
    assert evaluate_stand_aside(break_rate=BREAK_RATE_CEIL)["stand_aside"]
    assert evaluate_stand_aside(limit_down=LIMIT_DOWN_CEIL)["stand_aside"]
    assert evaluate_stand_aside(prev_zt_median_pct=-2.0)["stand_aside"]
    # 刚好在阈值内不触发
    assert not evaluate_stand_aside(promotion_1to2=PROMO_FLOOR, break_rate=BREAK_RATE_CEIL - 0.01)["stand_aside"]


def test_unreliable_phase_flags_uncertainty():
    g = evaluate_stand_aside(phase="退潮", phase_unreliable=True)
    assert any("置信度下调" in r for r in g["reasons"])


def test_gate_strips_buy_range_but_keeps_records():
    """触发时撤掉买入范围并标「仅观察」，但保留记录以便复盘归因。"""
    picks = [{"symbol": "600519", "buy_range": {"low": 1, "high": 2}}, {"symbol": "000001"}]
    gated = apply_gate_to_picks(picks, evaluate_stand_aside(phase="冰点"))
    assert all(p.get("observation_only") for p in gated)
    assert all("buy_range" not in p for p in gated)
    assert [p["symbol"] for p in gated] == ["600519", "000001"]
    # 未触发时原样返回
    assert apply_gate_to_picks(picks, {"stand_aside": False}) == picks


# ---------------------------------------------------------------- 风险档位


def test_risk_tier_by_role():
    assert risk_tier_of("龙头") == "龙头博弈"
    assert risk_tier_of("空间板") == "龙头博弈"
    assert risk_tier_of("中军") == "趋势跟随"
    assert risk_tier_of("领涨") == "趋势跟随"
    assert risk_tier_of("补涨") == "情绪低位"
    assert risk_tier_of("跟风") == "情绪低位"
    assert risk_tier_of("不存在的角色") == "情绪低位"  # 未知角色按最保守处理


def test_stop_loss_uses_tier_base_without_atr():
    r = stop_loss_reference(price=10.0, tier="龙头博弈")
    assert r["pct"] == 7.0
    assert r["price"] == 9.3


def test_stop_loss_widens_with_high_atr():
    """波动大的票给足空间：ATR 12% → 1.5× = 18% 被 clamp 到上限 12%。"""
    r = stop_loss_reference(price=10.0, tier="情绪低位", atr_pct=12.0)
    assert r["pct"] == STOP_PCT_CEIL * 100
    assert "ATR" in r["basis"]


def test_stop_loss_clamped_to_floor():
    r = stop_loss_reference(price=10.0, tier="情绪低位", atr_pct=0.5)
    assert r["pct"] == 4.0  # 档位基准 4% > 1.5×0.5%=0.75%


def test_stop_loss_none_without_price():
    assert stop_loss_reference(price=None, tier="龙头博弈") is None
    assert stop_loss_reference(price=0, tier="龙头博弈") is None


def test_exit_discipline_has_trailing_and_roi():
    d = exit_discipline("趋势跟随")
    assert d["trailing_pct"] > 0
    assert len(d["roi_ladder"]) >= 1
    assert "不构成买卖建议" in d["disclaimer"]
    # 龙头档的跟踪止盈比波段档更紧
    assert exit_discipline("龙头博弈")["trailing_pct"] < d["trailing_pct"]


def test_invalidations_include_theme_and_ma_and_event():
    inv = build_invalidations(
        role="龙头",
        tier="龙头博弈",
        theme_stage="退潮",
        theme_max_boards=3,
        prev_theme_max_boards=5,
        ma_value=18.5,
        event_titles=["英伟达禁令传闻"],
    )
    joined = " | ".join(inv)
    assert "退潮" in joined
    assert "5 板回落至 3 板" in joined
    assert "5 日线" in joined and "18.5" in joined
    assert "炸板" in joined  # 龙头专属失效条件
    assert "英伟达禁令传闻" in joined


def test_invalidations_for_low_tier_mentions_time_cost():
    inv = build_invalidations(role="补涨", tier="情绪低位")
    assert any("两日内未能兑现反抽" in x for x in inv)


def test_invalidations_minimal_without_context():
    """无上下文时至少给出均线失效条件，不虚构题材信息。"""
    inv = build_invalidations(role="中军", tier="趋势跟随")
    assert len(inv) >= 1
    assert any("10 日线" in x for x in inv)
