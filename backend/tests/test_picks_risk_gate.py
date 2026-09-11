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


# ---------------------------------------------------------------- 闸门分级（2026-09-10 用户拍板）
# 背景实测：近 9 个交易日 stand_aside 命中 9/9，其中 3 天仅凭单条弱信号触发 mild。
# 旧口径一律撤区间 ⇒ 盘前卡片全期不显示买入区间，闸门失去区分度。
# 判据按**信号性质**而非 level 标签（level 是「理由条数」的计数产物）。


def test_should_strip_buy_range_by_signal_nature():
    """撤除判据：相位级信号（退潮/冰点）或多条理由叠加 → 撤；单条量化擦线 → 只提示。

    ⚠️ 关键区分：「退潮」单条只算 1 条理由（level 是 mild），但它是 regime 级判断，
    必须撤区间——按 level 分档会把「市场在山腰」与「指标差 1 个点」混为一谈。
    """
    from app.picks.gate import should_strip_buy_range

    # 相位级：退潮单条 → 撤（尽管 level=mild）
    lone_ebb = evaluate_stand_aside(phase="退潮")
    assert lone_ebb["level"] == "mild" and should_strip_buy_range(lone_ebb) is True
    # 相位级：冰点 → 撤
    assert should_strip_buy_range(evaluate_stand_aside(phase="冰点")) is True
    # 多条叠加 → 撤（无相位，两条量化信号相互印证）
    multi = evaluate_stand_aside(promotion_1to2=0.1, break_rate=0.4)
    assert len(multi["reasons"]) >= 2 and should_strip_buy_range(multi) is True
    # 单条量化擦线 → 只提示
    lone_line = evaluate_stand_aside(promotion_1to2=PROMO_FLOOR - 0.01)
    assert lone_line["level"] == "mild" and should_strip_buy_range(lone_line) is False
    # 未触发 → 不撤
    assert should_strip_buy_range({"stand_aside": False}) is False


def test_gate_warn_only_keeps_buy_range():
    """只提示档（单条量化擦线）**不撤区间**：标的层保持原样——
    有买入范围、无 observation_only、无三态 tag。

    否则会出现自相矛盾的渲染「有买入范围 + 标注仅观察」。
    """
    gate = evaluate_stand_aside(promotion_1to2=PROMO_FLOOR - 0.01)
    assert gate["stand_aside"] and gate["level"] == "mild"  # 前置：单条理由 = mild
    picks = [{"symbol": "600519", "buy_range": {"low": 1, "high": 2}}]
    out = apply_gate_to_picks(picks, gate)
    assert out[0]["buy_range"] == {"low": 1, "high": 2}
    assert not out[0].get("observation_only")
    assert "follow_state" not in out[0]


def test_gate_strip_tier_still_strips_buy_range():
    """撤除档（相位退潮）保持原行为：撤区间 + 三态分层；且 phase 随 gate 带出。"""
    gate = evaluate_stand_aside(phase="退潮")
    assert gate["phase"] == "退潮"
    picks = [{"symbol": "600519", "buy_range": {"low": 1, "high": 2}, "boards": 1}]
    out = apply_gate_to_picks(picks, gate)
    assert "buy_range" not in out[0] and out[0]["observation_only"] is True
    assert out[0]["follow_state"] == "observe"  # 首板不够「可跟」判据


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


# ---------------------------------------------------------------- 阈值分位化（P1-31，2026-09-10）
# 绝对经验值对不上本项目分布：近 241 个交易日 promo_1to2 中位仅 14.3%、p90 仅 22.9%，
# 而 PROMO_FLOOR=0.30 被写成"低于 30% 就是接力无人接" ⇒ 近似恒真（近 9 日 8 天命中）。


def test_gate_prefers_percentile_over_absolute():
    """给了历史分位就按分位判——绝对值只作兜底，不再单独影响结论。"""
    # 18% 低于绝对线 30%，但在历史分布里只是中位水平（分位 50）→ 不触发
    g = evaluate_stand_aside(promotion_1to2=0.18, promotion_1to2_pctl=50.0)
    assert g["stand_aside"] is False and g["reasons"] == []
    # 8% 落在历史后 10% → 接力异常弱
    g = evaluate_stand_aside(promotion_1to2=0.08, promotion_1to2_pctl=5.0)
    assert g["stand_aside"] is True
    assert "接力异常弱" in g["reasons"][0] and "5 分位" in g["reasons"][0]
    # 炸板率 20% 低于绝对线 35%，但处在历史前 10% → 照样是"封板异常不牢"（分位优先）
    g = evaluate_stand_aside(break_rate=0.20, break_rate_pctl=95.0)
    assert g["stand_aside"] is True and "封板异常不牢" in g["reasons"][0]
    # 炸板率 40% 高于绝对线，但历史分位只有 85（市场本来就爱炸板）→ 不触发
    g = evaluate_stand_aside(break_rate=0.40, break_rate_pctl=85.0)
    assert g["stand_aside"] is False


def test_gate_percentile_fallback_marks_caliber():
    """分位不可用（样本不足/校准关闭）→ 回落绝对经验值，且**理由里写明用的是哪个口径**。"""
    g = evaluate_stand_aside(promotion_1to2=0.18)
    assert g["stand_aside"] is True
    assert "历史分位不可用" in g["reasons"][0] and "经验值" in g["reasons"][0]
    assert g["signals"]["promo_caliber"] == "absolute"
    g2 = evaluate_stand_aside(break_rate=0.40)
    assert "历史分位不可用" in g2["reasons"][0]
    assert g2["signals"]["break_caliber"] == "absolute"


def test_gate_signals_record_inputs_and_caliber():
    """留痕：原始输入 + 实际口径都要能只看落库数据就回答「这天为什么触发」。"""
    g = evaluate_stand_aside(
        phase="退潮", promotion_1to2=0.06, promotion_1to2_pctl=3.0,
        break_rate=0.42, break_rate_pctl=93.0, limit_down=18, prev_zt_median_pct=-1.8,
    )
    s = g["signals"]
    assert s["promotion_1to2"] == 0.06 and s["promotion_1to2_pctl"] == 3.0
    assert s["break_rate"] == 0.42 and s["break_rate_pctl"] == 93.0
    assert s["limit_down"] == 18 and s["prev_zt_median_pct"] == -1.8
    assert s["promo_caliber"] == "percentile" and s["break_caliber"] == "percentile"
    assert len(g["reasons"]) == 5  # 相位 + promo + break + 跌停 + 溢价
    # 值缺失 → 口径 missing（不是 absolute，也不是 percentile）
    empty = evaluate_stand_aside(phase="退潮")
    assert empty["signals"]["promo_caliber"] == "missing"
    assert empty["signals"]["break_caliber"] == "missing"
