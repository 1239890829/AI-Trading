"""梯队地位（Echelon Role）与炒作阶段（Speculation Regime）单测。

两者都是纯函数，注入参数即可覆盖全分支，不依赖行情。
"""

from __future__ import annotations

from datetime import date

from app.picks.echelon import (
    ROLE_BASE_SCORE,
    STAGE_ADJUST,
    classify_echelon_role,
    classify_non_limit_up_role,
    score_echelon,
    theme_ladder_health,
)
from app.picks.regime import (
    BALANCED_WEIGHTS,
    REGIME_EARNINGS,
    REGIME_SPECULATIVE,
    detect_regime,
    earnings_event_ratio,
    is_earnings_keyword,
    weights_for,
)


# ---------------------------------------------------------------- 梯队地位


def test_non_limit_up_role_by_excess():
    assert classify_non_limit_up_role(excess_pct=5.0)[0] == "领涨"
    assert classify_non_limit_up_role(excess_pct=0.0)[0] == "同步"
    assert classify_non_limit_up_role(excess_pct=-3.0)[0] == "滞涨"
    # 大市值同步票算中军（定深度而非定高度）
    assert classify_non_limit_up_role(excess_pct=0.5, float_market_cap=2e11)[0] == "中军"


def test_non_limit_up_role_without_basis_is_sync():
    """无题材基准可比时归为同步，不臆造地位。"""
    role, basis = classify_non_limit_up_role(excess_pct=None)
    assert role == "同步"
    assert "不臆造" in basis


def test_limit_up_role_uses_precise_classification():
    """涨停股走 classify_role 精确判定：题材内最高板 → 龙头。"""
    role, basis = classify_echelon_role(
        is_limit_up=True, consecutive_boards=2, theme_max_boards=2, market_max_boards=5
    )
    assert role == "龙头"
    assert "涨停池精确判定" in basis


def test_limit_up_space_board_when_market_highest():
    role, _ = classify_echelon_role(
        is_limit_up=True, consecutive_boards=4, theme_max_boards=4, market_max_boards=4
    )
    assert role == "空间板"


def test_score_echelon_stage_matters_more_than_role_alone():
    """联合研判核心：同一个角色，发酵期与退潮期分值差距显著。"""
    ferment, _ = score_echelon(role="龙头", stage="发酵", completeness=0.8)
    receding, _ = score_echelon(role="龙头", stage="退潮", completeness=0.8)
    assert ferment > receding
    # 退潮系数 0.55：龙头在退潮期的地位分 ≈ 发酵期的 0.5 倍
    assert receding < ferment * 0.65


def test_score_echelon_clamped_and_basis_complete():
    score, basis = score_echelon(role="断板", stage="退潮", completeness=0.0)
    assert 0 <= score <= 100
    assert "角色" in basis and "题材阶段" in basis and "梯队完整度" in basis


def test_theme_ladder_health_detects_receding():
    """涨停从 8 家骤降至 2 家 → 退潮。"""
    h = theme_ladder_health(
        limit_up_count=2,
        max_boards=2,
        prev_limit_up_count=8,
        prev_max_boards=4,
        reopen_rate=0.1,
        levels={1: 2},
    )
    assert h["stage"] == "退潮"
    assert h["adjust"] == STAGE_ADJUST["退潮"]
    assert h["stage_basis"]


def test_theme_ladder_health_completeness_detects_gap():
    """最高 4 板但 2/3 板全空 → 梯队断层，完整度显著低于满承接。"""
    gap = theme_ladder_health(
        limit_up_count=4, max_boards=4, levels={1: 3, 4: 1}
    )["completeness"]
    full = theme_ladder_health(
        limit_up_count=4, max_boards=4, levels={1: 1, 2: 1, 3: 1, 4: 1}
    )["completeness"]
    assert gap < full


def test_all_roles_have_base_score_and_tier_coverage():
    """角色表与档位表必须一一覆盖，避免新增角色漏配。"""
    from app.picks.risk import TIER_BY_ROLE

    assert set(ROLE_BASE_SCORE) == set(TIER_BY_ROLE)


# ---------------------------------------------------------------- 炒作阶段


def test_earnings_keyword_detection():
    assert is_earnings_keyword("公司发布业绩预增公告")
    assert is_earnings_keyword("2026 年半年报净利同比增长")
    assert not is_earnings_keyword("某题材受政策催化大涨")
    assert not is_earnings_keyword(None)


def test_earnings_event_ratio():
    texts = ["业绩预增", "政策利好", "净利翻倍", "题材发酵"]
    assert earnings_event_ratio(texts) == 0.5
    assert earnings_event_ratio([]) == 0.0


def test_regime_follows_calendar_when_no_events():
    """无事件样本时按日历判定：4 月=业绩期，6 月=空窗期。"""
    assert detect_regime(today=date(2026, 4, 20))["regime"] == REGIME_EARNINGS
    assert detect_regime(today=date(2026, 6, 20))["regime"] == REGIME_SPECULATIVE


def test_regime_density_can_override_calendar():
    """密度校验：空窗期出现业绩事件密集（≥30%，样本≥5）→ 翻转为业绩驱动期。"""
    r = detect_regime(today=date(2026, 6, 20), earnings_ratio=0.6, event_count=10)
    assert r["regime"] == REGIME_EARNINGS
    assert "占比" in r["basis"]


def test_regime_density_can_downgrade_calendar_window():
    """窗口期内无业绩事件（≤12%）→ 按空窗期处理。"""
    r = detect_regime(today=date(2026, 4, 20), earnings_ratio=0.05, event_count=10)
    assert r["regime"] == REGIME_SPECULATIVE


def test_regime_density_ignored_when_sample_small():
    """样本 <5 条时密度不足以推翻日历（防 2 条事件就翻转结论）。"""
    r = detect_regime(today=date(2026, 6, 20), earnings_ratio=1.0, event_count=3)
    assert r["regime"] == REGIME_SPECULATIVE
    assert "不足以推翻日历" in r["basis"]


def test_weights_sum_to_one_and_fundamental_differs_by_regime():
    for regime in (REGIME_EARNINGS, REGIME_SPECULATIVE):
        w = detect_regime(today=date(2026, 4, 1) if regime == REGIME_EARNINGS else date(2026, 6, 1))["weights"]
        assert abs(sum(w.values()) - 1.0) < 1e-9
    # 空窗期基本面权重必须显著低于业绩期（这是切换的意义）
    we = weights_for(REGIME_EARNINGS)
    ws = weights_for(REGIME_SPECULATIVE)
    assert ws["fundamental"] < we["fundamental"]
    assert ws["sentiment"] > we["sentiment"]
    assert ws["echelon"] > we["echelon"]  # 空窗期更看题材梯队


def test_unknown_regime_falls_back_to_balanced():
    assert weights_for(None) == BALANCED_WEIGHTS
    assert weights_for("不存在的阶段") == BALANCED_WEIGHTS
    assert abs(sum(BALANCED_WEIGHTS.values()) - 1.0) < 1e-9
