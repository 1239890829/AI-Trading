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
    """同义标签归一到**官方概念名**（2026-09-08 官方名称纪律：目标名必须逐字
    存在于同花顺概念板块目录；官方无统一概念的近似标签一律直通原始标签）。"""
    assert normalize_theme("黄金概念") == "黄金概念"  # 官方名直通
    assert normalize_theme("黄金租赁") == "黄金概念"  # 归到官方「黄金概念」
    assert normalize_theme("AI液冷") == "液冷服务器"  # 官方概念逐字名
    assert normalize_theme("数据中心业务") == "数据中心(AIDC)"  # 逐字含括号
    assert normalize_theme("央国企改革") == "央企国企改革"
    assert normalize_theme("转基因玉米") == "转基因"


def test_normalize_theme_passes_through_when_no_official_concept():
    """官方目录无统一概念的近似标签（业绩类/算力/PTFE/控制权等）→ 直通原始标签。

    曾把它们归并到自创名（业绩驱动/算力/PTFE/控制权变更…）——自创名在同花顺
    App 里搜不到，违反官方名称纪律。名称准确性优先于碎片化担忧。
    """
    for raw in ("业绩增长", "中报增长", "业绩高增长", "半年报预增",
                "算力服务", "AI算力", "PTFE薄膜", "控制权转让", "并购重组",
                "新股", "国资背景", "福建国资", "人形机器人", "算力租赁",
                "液冷服务器", "国企改革"):
        assert normalize_theme(raw) == raw, raw


def test_theme_aliases_targets_are_official_names():
    """守卫：THEME_ALIASES 的所有 value 必须是官方概念名（本清单为题材目录
    同步快照中与映射相关的子集；新增映射条目时先把目标名核对进官方目录）。"""
    from app.services.theme_service import THEME_ALIASES

    official_subset = {
        "黄金概念", "液冷服务器", "数据中心(AIDC)", "央企国企改革", "转基因",
    }
    for raw, target in THEME_ALIASES.items():
        assert target in official_subset, f"映射 {raw!r}→{target!r}：目标名不在官方概念目录"


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


def test_normalize_theme_official_names_over_fragments():
    """官方名称纪律（2026-09-08）：ths 原发标签逐字直通，归并目标必须是官方概念名。"""
    assert normalize_theme("PTFE薄膜") == "PTFE薄膜"  # 官方无 PTFE 概念 → 直通
    assert normalize_theme("AI液冷") == "液冷服务器"  # 官方「液冷服务器」
    assert normalize_theme("黄金租赁") == "黄金概念"  # 官方「黄金概念」
    assert normalize_theme("业绩高增长") == "业绩高增长"  # 业绩类直通
    assert normalize_theme("人形机器人") == "人形机器人"  # 官方概念直通
    assert normalize_theme("数据中心业务") == "数据中心(AIDC)"  # 官方逐字含括号


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


# ---------------------------------------------------------------- 炸板率双源直取（P1-19 收尾）


def _break_provider(name: str, result=None, boom: bool = False):
    """按名字造一个**独立类**的桩。

    ⚠️ 不能写 `self.__class__.__name__ = name`：那是**类属性**，同一测试文件里
    所有桩都是同一个类，改一次会把全部实例的名字一起改掉，`_pick_provider`
    于是永远只认到第一个源（本用例第一版就是这么假绿的）。
    """

    async def get_limit_break_pool(self, trade_date):
        self.calls += 1
        if self.boom:
            raise RuntimeError("upstream down")
        return self.result

    def __init__(self, res, b):
        self.result = res
        self.boom = b
        self.calls = 0

    cls = type(name, (), {"get_limit_break_pool": get_limit_break_pool, "__init__": __init__})
    return cls(result, boom)


def _chain(*providers):
    """造一个 composite 形状的容器（有 .providers 列表）。"""
    return SimpleNamespace(providers=list(providers))


def test_market_break_rate_prefers_ths():
    import asyncio

    from app.services.theme_service import _market_break_rate

    ths = _break_provider("ThsFuyaoProvider", result=[1, 2, 3])
    em = _break_provider("EastmoneyProvider", result=[9])
    rate = asyncio.run(_market_break_rate(_chain(ths, em), __import__("datetime").date(2026, 9, 10), 7))
    assert rate == pytest.approx(3 / 10)
    assert ths.calls == 1 and em.calls == 0  # 主源成功就不动备源


def test_market_break_rate_falls_back_to_eastmoney_on_ths_failure():
    """ths 抛异常 → 直取东财（P1-19：此前 ths 一挂就静默退化为近似口径）。"""
    import asyncio
    from datetime import date

    from app.services.theme_service import _market_break_rate

    ths = _break_provider("ThsFuyaoProvider", boom=True)
    em = _break_provider("EastmoneyProvider", result=[1, 2])
    rate = asyncio.run(_market_break_rate(_chain(ths, em), date(2026, 9, 10), 2))
    assert rate == pytest.approx(2 / 4)
    assert ths.calls == 1 and em.calls == 1


def test_market_break_rate_both_down_returns_none():
    import asyncio
    from datetime import date

    from app.services.theme_service import _market_break_rate

    ths = _break_provider("ThsFuyaoProvider", boom=True)
    em = _break_provider("EastmoneyProvider", boom=True)
    assert asyncio.run(_market_break_rate(_chain(ths, em), date(2026, 9, 10), 5)) is None


def test_market_break_rate_empty_pool_is_none_and_no_source_switch():
    """拿到但为空 = 不是异常 → 不换源（避免拿另一源口径硬凑），如实 None。"""
    import asyncio
    from datetime import date

    from app.services.theme_service import _market_break_rate

    ths = _break_provider("ThsFuyaoProvider", result=[])
    em = _break_provider("EastmoneyProvider", result=[1, 2, 3])
    assert asyncio.run(_market_break_rate(_chain(ths, em), date(2026, 9, 10), 5)) is None
    assert ths.calls == 1 and em.calls == 0


def test_market_break_rate_zero_limit_up_is_none():
    """0 涨停 → 分母为 0，绝不伪造 0% 炸板率。"""
    import asyncio
    from datetime import date

    from app.services.theme_service import _market_break_rate

    ths = _break_provider("ThsFuyaoProvider", result=[1])
    assert asyncio.run(_market_break_rate(_chain(ths), date(2026, 9, 10), 0)) is None


# ---------------------------------------------------------------- 题材/代码 → 板块资金行（P1-5 / P1-4）


def _patch_boards(monkeypatch, industry, concept, streaks):
    """桩掉 board_flow 的板块列表与落盘 streak（L3 层只依赖这两个出口）。"""
    from app.market import board_flow as bf

    async def fake_list(kind):
        return (industry if kind == "industry" else concept), []

    monkeypatch.setattr(bf, "get_board_list", fake_list)
    monkeypatch.setattr(bf, "get_board_streaks", lambda m: dict(streaks))


_IND = [{"board_code": "BK1033", "name": "电池", "kind": "industry",
         "change_pct": -1.37, "main_net_yi": 4.01, "main_net_ratio": 2.0}]
_CON = [{"board_code": "BK1024", "name": "绿色电力", "kind": "concept",
         "change_pct": 0.19, "main_net_yi": 32.33, "main_net_ratio": 5.1}]


def test_board_rows_for_names_attaches_streak_and_keeps_units(monkeypatch):
    import asyncio

    from app.services import theme_service as ts

    _patch_boards(monkeypatch, _IND, _CON, {"BK1024": 4})
    out = asyncio.run(ts.board_rows_for_names(["绿色电力"]))
    assert out["绿色电力"]["board_code"] == "BK1024"
    assert out["绿色电力"]["streak"] == 4
    assert out["绿色电力"]["main_net_yi"] == 32.33      # 亿，不因 streak 计算被改单位
    assert out["绿色电力"]["main_net_inflow"] == 32.33 * 1e8  # 元，news_persistence 口径不变


def test_board_rows_for_names_unknown_absent_not_fabricated(monkeypatch):
    """匹配不到的名字不出现在返回里（调用方按三态处理，绝不臆造默认板块）。"""
    import asyncio

    from app.services import theme_service as ts

    _patch_boards(monkeypatch, _IND, _CON, {})
    out = asyncio.run(ts.board_rows_for_names(["绿色电力", "查无此题材zzz", ""]))
    assert set(out) == {"绿色电力"}


def test_board_rows_for_names_streak_none_when_not_settled(monkeypatch):
    """未落盘的板块 → streak=None（区别于 0=今日净流出，三态不混）。"""
    import asyncio

    from app.services import theme_service as ts

    _patch_boards(monkeypatch, _IND, _CON, {})
    out = asyncio.run(ts.board_rows_for_names(["绿色电力"]))
    assert out["绿色电力"]["streak"] is None


def test_board_rows_for_codes_indexes_by_code(monkeypatch):
    """按板块代码直取（P1-4）：索引键是 code，绕开名字歧义。"""
    import asyncio

    from app.services import theme_service as ts

    _patch_boards(monkeypatch, _IND, _CON, {"BK1033": 2})
    out = asyncio.run(ts.board_rows_for_codes(["BK1033", "BK9999"]))
    assert set(out) == {"BK1033"}
    assert out["BK1033"]["name"] == "电池"
    assert out["BK1033"]["streak"] == 2


def test_board_rows_empty_input_no_upstream_call(monkeypatch):
    """空输入直接返回，不触发任何上游（自选为空时不打东财）。"""
    import asyncio

    from app.market import board_flow as bf
    from app.services import theme_service as ts

    called = {"n": 0}

    async def boom(kind):
        called["n"] += 1
        return [], []

    monkeypatch.setattr(bf, "get_board_list", boom)
    assert asyncio.run(ts.board_rows_for_names([])) == {}
    assert asyncio.run(ts.board_rows_for_codes([])) == {}
    assert called["n"] == 0


# ---------------------------------------------------------------- 介入条件清单（P1-13）

def _checklist_board() -> dict:
    """最小题材看板 payload（字段名与 build_theme_board 输出一致）。"""
    return {
        "trade_date": "2026-09-10",
        "themes": [
            {
                "theme": "固态电池",
                "stage": "发酵",
                "stage_basis": "连板高度 3、家数 5",
                "health_note": "梯队成建制",
                "risks": ["高位股分歧"],
                "ladder": [
                    {
                        "symbol": "600540", "name": "新赛股份", "role": "龙头", "boards": 3,
                        "boards_stat": "3天3板", "seal_amount": 3.2e8, "break_count": 0,
                        "turnover_rate": 8.5, "float_market_cap": 9.0e9,
                        "first_seal_time": "09:35:00", "last_seal_time": "09:35:00",
                        "seal_phase": "早盘", "change_pct": 10.0,
                        "dragon": {"score": 12, "grade": "龙头相", "basis": [], "missing": []},
                        "sentiment": {"level": "高"},
                        # 同花顺官方涨停原因：ladder 行**必须自带**（消费方
                        # intraday_opportunity 的「涨停原因」直接取它，不再绕道
                        # leaders.candidates —— 那份列表只含补涨/反包角色）
                        "reason": "固态电池+锂电材料",
                    }
                ],
            }
        ],
    }


def test_entry_checklist_from_board_uses_card_stage_and_market_phase():
    from app.services.theme_service import entry_checklist_from_board

    got = entry_checklist_from_board(_checklist_board(), "600540", market_phase="发酵")
    assert got is not None
    assert got["found"] if "found" in got else True  # 直接调用时不加 found（路由层加）
    # 题材层来自 card.stage，市场层来自入参——两处都不能自己编
    assert got["theme_layer"]["stage"] == "发酵"
    assert got["theme_layer"]["blocked"] is False
    assert got["market_layer"]["phase"] == "发酵"
    assert got["dragon_grade"] == "龙头相"
    assert got["role"] == "龙头"
    assert got["boards"] == 3
    assert got["conditions"] and got["invalidation"] and got["timing"]
    # 输入齐全 → missing 不应包含个股层维度
    assert "封单额" not in got["missing"] and "流通市值" not in got["missing"]


def test_entry_checklist_from_board_retreat_theme_blocks():
    """题材退潮 → theme_layer.blocked，且回避项里必须能看见原因。"""
    from app.services.theme_service import entry_checklist_from_board

    board = _checklist_board()
    board["themes"][0]["stage"] = "退潮"
    got = entry_checklist_from_board(board, "600540", market_phase="发酵")
    assert got is not None
    assert got["theme_layer"]["blocked"] is True
    assert any("退潮" in a for a in got["avoid"])


def test_entry_checklist_from_board_unknown_symbol_returns_none():
    """不在任何题材梯队 → None（调用方给通用清单，不臆造角色/封单质量）。"""
    from app.services.theme_service import entry_checklist_from_board

    assert entry_checklist_from_board(_checklist_board(), "000001") is None
    assert entry_checklist_from_board(_checklist_board(), "") is None
    assert entry_checklist_from_board({}, "600540") is None


def test_entry_checklist_from_board_marks_missing_inputs():
    """缺失的输入必须进 missing，且不因缺失而静默当成中性。"""
    from app.services.theme_service import entry_checklist_from_board

    board = _checklist_board()
    row = board["themes"][0]["ladder"][0]
    row["seal_amount"] = None
    row["turnover_rate"] = None
    got = entry_checklist_from_board(board, "600540", market_phase=None)
    assert got is not None
    assert {"封单额", "换手率", "市场阶段"} <= set(got["missing"])
