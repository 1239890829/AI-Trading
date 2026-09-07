"""题材梯队看板：纯计算逻辑测试。

这里的用例刻意覆盖「错了也看不出来」的地方——
界面照常渲染、数字看着合理，但结论是错的（例如反包被当成首板、
梯队断层被当成完整、题材碎片化被当成主线）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.theme_service import (
    MIDDLE_WEIGHT_MIN_CAP,
    UNCLASSIFIED,
    _parse_hhmmss,
    assign_primary_themes,
    classify_role,
    early_seal_rate,
    echelon_completeness,
    formation_level,
    judge_theme_stage,
    normalize_theme,
    parse_theme_tags,
    seal_phase,
    seal_quality_score,
    seal_retention_rate,
    strength_tier,
    theme_health_note,
    theme_strength_score,
)


def _rec(symbol: str, reason: str | None, boards: int = 1):
    return SimpleNamespace(symbol=symbol, reason=reason, consecutive_boards=boards)


# ---------------------------------------------------------------- 题材解析


def test_parse_theme_tags_splits_on_plus():
    assert parse_theme_tags("黄金珠宝+珠宝加工+客户拓展") == ["黄金珠宝", "珠宝加工", "客户拓展"]


def test_parse_theme_tags_handles_empty_and_dupes():
    assert parse_theme_tags(None) == []
    assert parse_theme_tags("") == []
    assert parse_theme_tags("AI应用+AI应用") == ["AI应用"]


def test_normalize_theme_merges_synonyms():
    """业绩类标签不合并会人为制造碎片题材：8/28 前三大标签全是业绩类。"""
    for raw in ("业绩增长", "中报增长", "半年报增长", "中报扭亏", "半年报减亏"):
        assert normalize_theme(raw) == "业绩驱动", raw
    assert normalize_theme("黄金概念") == "黄金珠宝"
    assert normalize_theme("液冷服务器") == "液冷"
    assert normalize_theme("福建国资") == "国资改革"


def test_normalize_theme_keeps_unknown_tags():
    """未收录的标签必须原样保留，不能因为不在词典里就丢掉。"""
    assert normalize_theme("创新药") == "创新药"


# ---------------------------------------------------------------- 封板时间


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("09:25", "早盘"),
        ("092500", "早盘"),
        ("10:30", "上午"),
        ("13:15", "午后"),
        ("14:45", "尾盘"),
    ],
)
def test_seal_phase_buckets(raw, expected):
    assert seal_phase(raw) == expected


def test_seal_phase_returns_none_when_unparsable():
    assert seal_phase(None) is None
    assert seal_phase("") is None
    assert seal_phase("abc") is None


# ---------------------------------------------------------------- 封单质量（A2 下半）


def test_parse_hhmmss_variants():
    assert _parse_hhmmss("09:33:00") == 93300
    assert _parse_hhmmss("093300") == 93300
    assert _parse_hhmmss("10:30") == 103000  # HHMM 补零成 HHMMSS
    assert _parse_hhmmss("") is None
    assert _parse_hhmmss(None) is None
    assert _parse_hhmmss("abc") is None


def test_early_seal_rate_counts_only_before_10am():
    # 2/3 早封；None 样本（时间缺失）从分母剔除
    got = early_seal_rate([_parse_hhmmss("09:31"), _parse_hhmmss("10:30"), None, _parse_hhmmss("09:58")])
    assert got == round(2 / 3, 4)


def test_early_seal_rate_none_when_no_samples():
    """全部成员缺首封时间 → None（三态），绝不冒充 0%。"""
    assert early_seal_rate([None, None]) is None
    assert early_seal_rate([]) is None


def test_seal_retention_pairs_current_over_max():
    # 5000 万收盘 / 8000 万最高 = 0.625；缺失对剔除；max=0 防除零
    got = seal_retention_rate([
        (5e7, 8e7),
        (None, 8e7),      # 当前封单缺失 → 成对剔除
        (3e7, None),      # 最高封单缺失 → 成对剔除
        (1e7, 0.0),       # max=0 → 剔除（防除零）
        (2e7, 2e7),       # 收盘=最高 → 留存 1.0
    ])
    assert got == round((5e7 + 2e7) / (8e7 + 2e7), 4)


def test_seal_retention_none_when_no_valid_pairs():
    assert seal_retention_rate([(None, None), (1e7, None)]) is None
    assert seal_retention_rate([]) is None


# ---------------------------------------------------------------- 角色判定


def test_role_fanbao_detected_by_prev_boards_drop():
    """反包：昨日 ≥2 板、今日回到 1 板。只按当日数据看会被当成普通首板。"""
    role = classify_role(
        boards=1,
        theme_max_boards=1,
        market_max_boards=5,
        prev_boards=3,
        float_market_cap=None,
        seal_phase_value="早盘",
        break_count=0,
    )
    assert role == "反包"


def test_role_space_board_when_market_highest():
    role = classify_role(
        boards=7,
        theme_max_boards=7,
        market_max_boards=7,
        prev_boards=6,
        float_market_cap=None,
        seal_phase_value="早盘",
        break_count=0,
    )
    assert role == "空间板"


def test_role_leader_when_theme_highest_but_not_market():
    role = classify_role(
        boards=3,
        theme_max_boards=3,
        market_max_boards=7,
        prev_boards=2,
        float_market_cap=None,
        seal_phase_value="早盘",
        break_count=0,
    )
    assert role == "龙头"


def test_role_middle_weight_by_market_cap():
    """中军按市值识别，与连板高度无关。"""
    role = classify_role(
        boards=1,
        theme_max_boards=4,
        market_max_boards=7,
        prev_boards=None,
        float_market_cap=MIDDLE_WEIGHT_MIN_CAP * 2,
        seal_phase_value="上午",
        break_count=0,
    )
    assert role == "中军"


def test_role_repair_after_leader_made_space():
    """补涨：首板且题材已出现 ≥3 板。"""
    role = classify_role(
        boards=1,
        theme_max_boards=4,
        market_max_boards=7,
        prev_boards=None,
        float_market_cap=3e9,
        seal_phase_value="早盘",
        break_count=0,
    )
    assert role == "补涨"


def test_role_follower_when_late_or_reopened():
    for phase, bc in (("尾盘", 0), ("早盘", 2)):
        role = classify_role(
            boards=1,
            theme_max_boards=1,
            market_max_boards=3,
            prev_boards=None,
            float_market_cap=2e9,
            seal_phase_value=phase,
            break_count=bc,
        )
        assert role == "跟风", (phase, bc)


def test_role_first_board_default():
    role = classify_role(
        boards=1,
        theme_max_boards=1,
        market_max_boards=3,
        prev_boards=None,
        float_market_cap=2e9,
        seal_phase_value="早盘",
        break_count=0,
    )
    assert role == "首板"


# ---------------------------------------------------------------- 梯队完整度


def test_echelon_completeness_rewards_full_ladder():
    assert echelon_completeness({1: 5, 2: 2, 3: 1, 4: 1}, 4) == 1.0


def test_echelon_completeness_penalizes_gap():
    """4 板下面直接是首板、2/3 板全空 —— 断层，不能算健康。"""
    assert echelon_completeness({1: 8, 4: 1}, 4) == pytest.approx(0.333, abs=0.01)


def test_echelon_completeness_zero_when_only_first_boards():
    assert echelon_completeness({1: 6}, 1) == 0.0


# ---------------------------------------------------------------- 阶段判断


def test_stage_climax_requires_count_height_and_firm_seal():
    stage, basis = judge_theme_stage(
        limit_up_count=8, max_boards=6, prev_limit_up_count=None,
        prev_max_boards=None, reopen_rate=0.1, completeness=1.0, premium_median=5.0,
    )
    assert stage == "高潮"


def test_stage_divergence_when_premium_negative():
    """家数够多但接力亏钱 = 分歧，不是高潮。这是本次复盘的核心修正。"""
    stage, basis = judge_theme_stage(
        limit_up_count=8, max_boards=5, prev_limit_up_count=6,
        prev_max_boards=5, reopen_rate=0.1, completeness=1.0, premium_median=-1.5,
    )
    assert stage == "分歧"
    assert any("溢价" in b for b in basis)


def test_stage_divergence_when_height_retreats():
    stage, _ = judge_theme_stage(
        limit_up_count=6, max_boards=3, prev_limit_up_count=6,
        prev_max_boards=5, reopen_rate=0.1, completeness=0.5, premium_median=None,
    )
    assert stage == "分歧"


def test_stage_retreat_when_count_collapses():
    stage, basis = judge_theme_stage(
        limit_up_count=1, max_boards=1, prev_limit_up_count=6,
        prev_max_boards=5, reopen_rate=0.2, completeness=0.0, premium_median=None,
    )
    assert stage == "退潮"
    assert any("骤降" in b for b in basis)


def test_stage_ferment_with_healthy_ladder():
    stage, _ = judge_theme_stage(
        limit_up_count=4, max_boards=3, prev_limit_up_count=3,
        prev_max_boards=3, reopen_rate=0.1, completeness=1.0, premium_median=2.0,
    )
    assert stage == "发酵"


def test_stage_startup_for_single_board_theme():
    stage, _ = judge_theme_stage(
        limit_up_count=2, max_boards=1, prev_limit_up_count=None,
        prev_max_boards=None, reopen_rate=0.0, completeness=0.0, premium_median=None,
    )
    assert stage == "启动"


# ---------------------------------------------------------------- 强度与健康度


def test_strength_score_monotonic_on_ladder_quality():
    weak = theme_strength_score(
        limit_up_count=3, max_boards=1, completeness=0.0,
        reopen_rate=0.5, seal_quality=0.3, active_days=1,
    )
    strong = theme_strength_score(
        limit_up_count=6, max_boards=5, completeness=1.0,
        reopen_rate=0.0, seal_quality=0.9, active_days=4,
    )
    assert strong > weak


def test_strength_score_damps_tiny_themes():
    """单只票靠高板数不能压过成建制的真梯队。

    8/28 实测：211 个题材标签里 47 个只对应 1 只涨停股，其中「客户拓展」
    只有 1 只票但是 7 板，未打折时强度分 91.5，几乎追平 6 只票的「算力」。
    这正是「错了也看不出来」的典型——数字看着合理，排序却反了。
    """
    solo_high = theme_strength_score(
        limit_up_count=1, max_boards=7, completeness=0.2,
        reopen_rate=0.0, seal_quality=1.0, active_days=3,
    )
    formed_mid = theme_strength_score(
        limit_up_count=6, max_boards=3, completeness=1.0,
        reopen_rate=0.3, seal_quality=0.85, active_days=5,
    )
    assert formed_mid > solo_high


def test_formation_level_buckets():
    assert formation_level(1) == "个股行情"
    assert formation_level(2) == "零散"
    assert formation_level(3) == "初步成形"
    assert formation_level(4) == "初步成形"
    assert formation_level(5) == "成建制"
    assert formation_level(36) == "成建制"


def test_normalize_theme_merges_real_world_fragments():
    """8/28 实测散出的碎片标签，不合并会各成一档伪题材。"""
    assert normalize_theme("PTFE薄膜") == "PTFE"
    assert normalize_theme("AI液冷") == "液冷"
    assert normalize_theme("算力服务") == "算力"
    assert normalize_theme("黄金租赁") == "黄金珠宝"
    assert normalize_theme("业绩高增长") == "业绩驱动"
    assert normalize_theme("半年报预增") == "业绩驱动"
    assert normalize_theme("人形机器人") == "机器人"
    assert normalize_theme("数据中心业务") == "数据中心"


def test_health_note_calls_out_solo_stock_as_not_a_theme():
    note, risks = theme_health_note(
        theme="客户拓展", stage="启动", limit_up_count=1, max_boards=7,
        completeness=0.17, missing_levels=[2, 3, 4, 5, 6], has_middle_weight=False,
        reopen_rate=0.0, premium_median=9.9,
    )
    assert "个股独立行情" in note
    # 必须是首要风险，不能只是末尾补一句
    assert "个股独立行情" in risks[0]


def test_health_note_flags_missing_middle_weight():
    note, risks = theme_health_note(
        theme="创新药", stage="发酵", limit_up_count=5, max_boards=3,
        completeness=1.0, missing_levels=[], has_middle_weight=False,
        reopen_rate=0.1, premium_median=4.0,
    )
    assert any("中军" in r for r in risks)
    assert "创新药" in note


def test_health_note_flags_ladder_gap():
    _, risks = theme_health_note(
        theme="算力", stage="分歧", limit_up_count=6, max_boards=4,
        completeness=0.33, missing_levels=[2, 3], has_middle_weight=True,
        reopen_rate=0.4, premium_median=-2.0,
    )
    joined = " ".join(risks)
    assert "断层" in joined
    assert "开板率" in joined
    assert "溢价" in joined


def test_ladder_gap_message_excludes_the_top_level():
    """断层文案不能把最高板所在档位算成「缺失」。

    旧实现直接用 range(2, max_boards + 1) 当缺失档位，最高板按定义必然有票，
    于是输出「7 板下方 2~7 板无承接」这种自相矛盾的话（8/28 黄金珠宝实拍）。
    """
    _, risks = theme_health_note(
        theme="黄金珠宝", stage="分歧", limit_up_count=5, max_boards=7,
        completeness=0.33, missing_levels=[2, 3, 4, 5, 6],
        has_middle_weight=False, reopen_rate=0.4, premium_median=9.9,
    )
    gap = next(r for r in risks if "断层" in r)
    assert "2~6" in gap
    assert "2~7" not in gap


def test_health_note_positive_when_all_good():
    note, risks = theme_health_note(
        theme="黄金珠宝", stage="发酵", limit_up_count=7, max_boards=4,
        completeness=1.0, missing_levels=[], has_middle_weight=True,
        reopen_rate=0.0, premium_median=5.0,
    )
    assert risks == []
    assert "梯队健康" in note


def test_seal_quality_prefers_early():
    early = seal_quality_score({"早盘": 5}, 5)
    late = seal_quality_score({"尾盘": 5}, 5)
    assert early > late


# ---------------------------------------------------------------- 未分类兜底


def test_unclassified_constant_stable():
    assert UNCLASSIFIED == "未分类"
    assert parse_theme_tags(None) == []


# ---------------------------------------------------------------- 梯队联动归属


def test_attribution_echelon_beats_big_generic_tag():
    """核心回归：3 板龙头不能被大杂烩题材吸收。

    2026-08-28 实拍：千金药业（3 板，标签含创新药）被「业绩驱动 41 家」
    按成员总数吸走，创新药梯队被拆散。连板密度判据下必须归属创新药。
    """
    pool = [
        _rec("600479", "业绩驱动+创新药+女性健康+药材种植", boards=3),  # 千金
        _rec("c1", "创新药", boards=1),
        _rec("c2", "创新药", boards=1),
        _rec("b1", "业绩驱动", boards=1),
        _rec("b2", "业绩驱动", boards=1),
        _rec("b3", "业绩驱动", boards=1),
        _rec("b4", "业绩驱动", boards=1),
    ]
    primary = assign_primary_themes(pool)
    # 业绩驱动密度 0.2（1/5）< 创新药密度 0.33（1/3）→ 千金归创新药
    assert primary["600479"] == "创新药"
    assert primary["c1"] == "创新药" and primary["c2"] == "创新药"
    assert primary["b1"] == "业绩驱动"


def test_attribution_solo_theme_cannot_steal_member():
    """1 只票的题材没有吸成员资格：中安科(2板) 必须留在算力，而不是
    自立「安保安防」伪题材把算力梯队拆掉一格。"""
    pool = [
        _rec("zake", "安保安防+算力", boards=2),
        _rec("s1", "算力", boards=1),
        _rec("s2", "算力", boards=1),
        _rec("s3", "算力", boards=1),
    ]
    primary = assign_primary_themes(pool)
    assert primary["zake"] == "算力"


def test_attribution_all_solo_candidates_falls_back_to_first_tag():
    """全部候选都是 1 家（个股行情）时：回退首个标签，结果仍唯一。"""
    pool = [_rec("x", "客户拓展+折叠屏", boards=7)]
    primary = assign_primary_themes(pool)
    assert primary["x"] == "客户拓展"


def test_attribution_two_member_theme_beats_solo_tag():
    """候选里存在 ≥2 家的题材时，1 家的伪题材无资格当主属性。"""
    pool = [
        _rec("x", "客户拓展+折叠屏", boards=7),
        _rec("y", "折叠屏", boards=1),
    ]
    primary = assign_primary_themes(pool)
    assert primary["x"] == "折叠屏"
    assert primary["y"] == "折叠屏"


def test_attribution_member_count_ties_break_by_boards_then_first_tag():
    """同密度（都是 1.0）时按家数 → 最高板 → 首标签决定，保证确定性。"""
    pool = [
        _rec("a", "甲+乙", boards=2),
        _rec("a2", "甲", boards=1),
        _rec("a3", "甲", boards=1),  # 甲：3 家 2 连板，密度 0.67
        _rec("b2", "乙", boards=2),  # 乙：2 家 2 连板，密度 1.0
    ]
    primary = assign_primary_themes(pool)
    # 乙密度 1.0 > 甲 0.67 → a 归乙
    assert primary["a"] == "乙"


def test_attribution_no_tags_goes_unclassified():
    primary = assign_primary_themes([_rec("n", None), _rec("n2", "")])
    assert primary["n"] == UNCLASSIFIED and primary["n2"] == UNCLASSIFIED


def test_attribution_every_stock_has_exactly_one_primary():
    """归属结果必须覆盖全部股票且值唯一——这是「梯队不拆散」的结构保证。"""
    pool = [
        _rec("p1", "黄金+珠宝加工+黄金概念", boards=3),
        _rec("p2", "黄金概念", boards=2),
        _rec("p3", "液冷服务器+算力", boards=2),
        _rec("p4", "算力", boards=1),
        _rec("p5", "算力", boards=1),
    ]
    primary = assign_primary_themes(pool)
    assert set(primary) == {"p1", "p2", "p3", "p4", "p5"}
    # 每个主归属都是规范化后的题材名
    for t in primary.values():
        assert t == normalize_theme(t)


# ---------------------------------------------------------------- 强弱分级


def test_tier_leader_requires_formation_stage_and_firm_seal():
    tier, basis = strength_tier(
        formation="成建制", stage="发酵", max_boards=4,
        reopen_rate=0.1, premium_median=3.0,
    )
    assert tier == "领涨" and "封板牢" in basis


def test_tier_losing_premium_caps_at_active():
    """接力亏钱一票否决：家数再多最高也只能是「活跃」。"""
    for formation in ("成建制", "初步成形"):
        tier, basis = strength_tier(
            formation=formation, stage="高潮", max_boards=5,
            reopen_rate=0.1, premium_median=-1.0,
        )
        assert tier == "活跃", formation
        assert "溢价为负" in basis


def test_tier_strong_on_stage_even_without_full_formation():
    tier, _ = strength_tier(
        formation="初步成形", stage="高潮", max_boards=4,
        reopen_rate=0.2, premium_median=1.0,
    )
    assert tier == "强势"


def test_tier_active_needs_a_real_ladder():
    tier, _ = strength_tier(
        formation="零散", stage="启动", max_boards=2,
        reopen_rate=0.2, premium_median=None,
    )
    assert tier == "活跃"


def test_tier_observe_when_retreat_or_all_first_boards():
    tier, _ = strength_tier(
        formation="初步成形", stage="退潮", max_boards=4,
        reopen_rate=0.1, premium_median=2.0,
    )
    assert tier == "观察"
    tier2, _ = strength_tier(
        formation="零散", stage="启动", max_boards=1,
        reopen_rate=0.0, premium_median=None,
    )
    assert tier2 == "观察"
