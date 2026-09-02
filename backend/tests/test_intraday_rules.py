"""盘中跟踪规则库回归测试（选股 2.0 批次 A）。

锁三类行为：
1. **unknown 三态**：缺数据的判定项绝不冒充通过（否则"缺量比"的板块也会被确认），
   也不触发证伪（缺数据不是利空证据）。
2. **阈值边界**：确认/证伪的每个常量边界各测一次，回测调参改常量时这些测试
   就是"改动是否破坏语义"的护栏。
3. **可解释性**：confirm/falsify 必须返回逐项明细与阈值，提醒必须八段齐全——
   只给 bool 的规则引擎没法复盘。
"""
from __future__ import annotations

import pytest

from app.picks import intraday_rules as ir


# ---------------------------------------------------------------- 盘前排序


def test_rank_directions_orders_by_score():
    evs = [
        {"direction": "AI应用", "event_strength": 5, "theme_momentum": 80, "echelon": 70, "defensive": False},
        {"direction": "红利银行", "event_strength": 0, "theme_momentum": 30, "echelon": 20, "defensive": True},
    ]
    ranked = ir.rank_directions(evs, phase="发酵")
    assert ranked[0]["direction"] == "AI应用"
    assert ranked[0]["score"] > ranked[1]["score"]
    assert "basis" in ranked[0]


def test_rank_directions_defensive_boosted_in_ebb():
    ev = {"direction": "红利", "event_strength": 0, "theme_momentum": 0, "echelon": 0, "defensive": True}
    boost = ir.rank_directions([ev], phase="退潮")[0]["score"]
    flat = ir.rank_directions([ev], phase="高潮")[0]["score"]
    assert boost == pytest.approx(flat + 3.0)  # 10 × 0.3


# ---------------------------------------------------------------- 确认走强


def _confirm_kwargs(**over):
    base = dict(
        theme_pct=2.8, theme_limit_up=3, theme_max_boards=4, leader_pct=6.0,
        volume_ratio=1.7, promo_percentile=49.2, phase="发酵", now_minutes=14 * 60,
    )
    base.update(over)
    return base


def test_confirm_all_met_confirms_with_full_strength():
    r = ir.confirm_signal(**_confirm_kwargs())
    assert r["confirmed"] is True
    assert r["unknown_count"] == 0
    assert r["strength"] == 1.0


def test_confirm_uses_early_threshold_before_10am():
    r = ir.confirm_signal(**_confirm_kwargs(theme_pct=2.0, now_minutes=9 * 60 + 50))
    assert r["confirmed"] is True  # 早盘 1.5 线
    r2 = ir.confirm_signal(**_confirm_kwargs(theme_pct=2.0, now_minutes=10 * 60 + 10))
    assert r2["confirmed"] is False  # 盘中 2.5 线


def test_confirm_missing_volume_ratio_degrades_to_075_confirm():
    """量比缺失 → 该项 unknown，但其余四项全过 → 0.75 档降级确认（§5.1#4）。

    2026-09-02 定案选 b：旧实现 confirmed 永假（量比恒 unknown），盘中提醒
    整条死掉——实现违背自身 docstring 设计。放宽后按满足率 4/5 → 0.75。
    """
    r = ir.confirm_signal(**_confirm_kwargs(volume_ratio=None))
    vol = next(c for c in r["checks"] if c["key"] == "volume_ratio")
    assert vol["met"] is None  # 三态保留：unknown 不冒充通过
    assert r["confirmed"] is True
    assert r["unknown_count"] == 1
    assert r["strength"] == 0.75  # 降一档不清零


def test_confirm_missing_volume_ratio_with_unmet_still_rejects():
    """量比缺失 + 任一项明确不满足 → 不确认（降级确认只兜 unknown，不兜 unmet）。"""
    r = ir.confirm_signal(**_confirm_kwargs(volume_ratio=None, phase="退潮"))
    assert r["unmet_count"] == 1
    assert r["confirmed"] is False


def test_confirm_missing_volume_ratio_plus_other_unknown_rejects():
    """量比缺失 + 另一项也判不出来（两项 unknown）→ 不确认（放宽仅限单项量能）。"""
    r = ir.confirm_signal(**_confirm_kwargs(volume_ratio=None, leader_pct=None))
    assert r["unknown_count"] == 2
    assert r["confirmed"] is False
    assert r["strength"] == 0.5  # 满足率 3/5 → 0.5 档，但仍不到确认线


def test_confirm_missing_non_volume_item_not_degraded():
    """放宽仅限量能项：龙头强度判不出来（非量比）→ 其余全过也不确认。"""
    r = ir.confirm_signal(**_confirm_kwargs(leader_pct=None))
    assert r["unknown_count"] == 1
    assert r["confirmed"] is False
    assert r["strength"] == 0.75


def test_confirm_environment_ebb_phase_fails():
    r = ir.confirm_signal(**_confirm_kwargs(phase="退潮"))
    env = next(c for c in r["checks"] if c["key"] == "environment")
    assert env["met"] is False
    assert r["confirmed"] is False


def test_confirm_height_alternative_satisfies_theme_item():
    """涨停不足 2 家但出现 3 板高度股 → 板块高度项仍算满足。"""
    r = ir.confirm_signal(**_confirm_kwargs(theme_limit_up=1, theme_max_boards=3))
    h = next(c for c in r["checks"] if c["key"] == "theme_height")
    assert h["met"] is True


# ---------------------------------------------------------------- 证伪


def test_falsify_drawdown_triggers():
    r = ir.falsify_signal(peak_pct=4.0, current_pct=1.5)
    assert r["falsified"] is True
    assert r["triggers"][0]["key"] == "drawdown"


def test_falsify_negative_streak_requires_15_beats():
    r14 = ir.falsify_signal(peak_pct=None, current_pct=-0.5, below_zero_beats=14)
    r15 = ir.falsify_signal(peak_pct=None, current_pct=-0.5, below_zero_beats=15)
    assert r14["falsified"] is False
    assert r15["falsified"] is True


def test_falsify_leader_break_needs_limit_down_too():
    r = ir.falsify_signal(peak_pct=None, current_pct=1.0, leader_broke_board=True, theme_limit_down=1)
    assert r["falsified"] is True
    r2 = ir.falsify_signal(peak_pct=None, current_pct=1.0, leader_broke_board=True, theme_limit_down=0)
    assert r2["falsified"] is False  # 只炸板没跌停，不构成组合证伪


def test_falsify_environment_promo_low_percentile():
    r = ir.falsify_signal(peak_pct=None, current_pct=1.0, promo_percentile=12.0)
    assert r["falsified"] is True
    assert r["triggers"][0]["key"] == "environment"


def test_falsify_missing_data_is_not_evidence():
    """全缺 → 无触发。缺数据不是利空证据，绝不默认证伪。"""
    r = ir.falsify_signal(peak_pct=None, current_pct=None)
    assert r["falsified"] is False
    assert r["triggers"] == []


# ---------------------------------------------------------------- 模式与退潮判定


def test_entry_mode_matrix():
    assert ir.entry_mode("发酵", "发酵")[0] == "追涨"
    assert ir.entry_mode("高潮", "高潮")[0] == "追涨减半"
    assert ir.entry_mode("分歧", "分歧")[0] == "潜伏"
    assert ir.entry_mode("退潮", "退潮")[0] == "观望"
    assert ir.entry_mode(None, "修复")[0] == "观望"  # 阶段缺失不下结论


def test_ebb_or_end_three_way():
    end, _ = ir.ebb_or_end(promo_percentile=10.0, height_gap=3, leader_below_ma5=True, leader_below_ma10=True)
    assert end == "趋势结束"
    rec, _ = ir.ebb_or_end(promo_percentile=55.0, height_gap=1, leader_below_ma5=False, leader_below_ma10=False)
    assert rec == "短暂退潮"
    watch, basis = ir.ebb_or_end(promo_percentile=30.0, height_gap=2, leader_below_ma5=True, leader_below_ma10=False)
    assert watch == "观望带"
    assert basis["detail"]  # 观望带也必须给可读依据


def test_ebb_or_end_never_concludes_end_on_missing_data():
    """分位缺失时绝不下"趋势结束"的重结论。"""
    verdict, _ = ir.ebb_or_end(promo_percentile=None, height_gap=3, leader_below_ma5=True, leader_below_ma10=True)
    assert verdict != "趋势结束"


# ---------------------------------------------------------------- 进场计划


def test_plan_pullback_conditions():
    p = ir.plan_pullback(price=9.9, ma5=10.0, ma10=9.7, volume_ratio=0.7, theme_pct=1.0, intraday_low_held=True)
    assert p["ready"] is True
    assert p["buy_range"] == [9.8, 10.2]
    assert p["stop"] == round(9.7 * 0.99, 2)
    p2 = ir.plan_pullback(price=9.9, ma5=10.0, ma10=9.7, volume_ratio=1.2, theme_pct=1.0, intraday_low_held=True)
    assert p2["ready"] is False
    assert any("缩量" in r for r in p2["unmet"])


def test_plan_pullback_missing_ma_gives_no_range():
    p = ir.plan_pullback(price=9.9, ma5=None, ma10=None, volume_ratio=0.7, theme_pct=1.0, intraday_low_held=True)
    assert p["buy_range"] is None
    assert p["stop"] is None


def test_plan_breakout_conditions():
    p = ir.plan_breakout(price=10.5, platform_high=10.2, volume_ratio=2.2, theme_pct=1.8)
    assert p["ready"] is True
    assert p["buy_range"][0] == 10.2
    p2 = ir.plan_breakout(price=10.5, platform_high=10.2, volume_ratio=1.2, theme_pct=1.8)
    assert p2["ready"] is False


def test_plan_reseal():
    p = ir.plan_reseal(limit_price=11.0, broke_price=10.4, sealed=True)
    assert p["ready"] is True
    assert p["stop"] == round(10.4 * 0.98, 2)
    assert ir.plan_reseal(limit_price=11.0, broke_price=10.4, sealed=False)["ready"] is False


def test_position_size_cap():
    assert ir.position_size(1.0) == 10.0
    assert ir.position_size(0.75) == 7.5
    assert ir.position_size(0.0) == 0.0
    assert ir.position_size(99) == ir.POSITION_CAP  # 上限 20%


# ---------------------------------------------------------------- 八段式提醒


def test_build_alert_has_all_sections_and_unknown_note():
    confirm = ir.confirm_signal(**_confirm_kwargs(volume_ratio=None))
    alert = ir.build_alert(
        direction="粮食安全", symbol="600598", name="北大荒", role="龙头",
        confirm=confirm, logic="厄尔尼诺预期 → 减产传导种业",
        buy_range=[9.8, 10.2], stop=9.6, position=7.5, risks=["板块高位"],
        plan_note="MA5±2%",
    )
    for seg in ("【盘中机会】", "1. 个股：", "2. 触发指标：", "3. 逻辑：", "4. 买入区间：",
                "5. 止损：", "6. 仓位建议：", "7. 风险点：", "8. 状态：非投资建议"):
        assert seg in alert, f"缺少段落 {seg}"
    assert "数据缺失项：量能" in alert  # unknown 必须显式呈现


def test_build_alert_no_range_when_data_missing():
    confirm = ir.confirm_signal(**_confirm_kwargs())
    alert = ir.build_alert(
        direction="x", symbol="1", name="n", role="龙头", confirm=confirm,
        logic="l", buy_range=None, stop=None, position=0, risks=[],
    )
    assert "缺关键数据，不给区间" in alert
    assert "缺数据，不给止损位" in alert
