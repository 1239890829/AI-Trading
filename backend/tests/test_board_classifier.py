"""东财 F10 板块标签分类（行业/地域/概念/风格指数）。

fixture 为 2026-08-29 实抓的贵州茅台 CoreConception/PageAjax ssbk 数据（27 条，已剔除无关字段）。
针对性回归：IS_PRECISE 是字符串 '0'/'1' 而非整数，按整数比较会静默失效、全部标签落进"风格/指数"。
"""
from __future__ import annotations

from app.market.normalizer import board_code_norm, classify_boards, main_board

SSBK_MAOTAI = [
    {"BOARD_CODE": "438", "BOARD_NAME": "食品饮料", "BOARD_RANK": 1, "IS_PRECISE": "0"},
    {"BOARD_CODE": "1277", "BOARD_NAME": "白酒Ⅱ", "BOARD_RANK": 2, "IS_PRECISE": None},
    {"BOARD_CODE": "1575", "BOARD_NAME": "白酒Ⅲ", "BOARD_RANK": 3, "IS_PRECISE": None},
    {"BOARD_CODE": "173", "BOARD_NAME": "贵州板块", "BOARD_RANK": 4, "IS_PRECISE": "0"},
    {"BOARD_CODE": "1711", "BOARD_NAME": "消费风格", "BOARD_RANK": 5, "IS_PRECISE": None},
    {"BOARD_CODE": "1663", "BOARD_NAME": "大盘股", "BOARD_RANK": 6, "IS_PRECISE": None},
    {"BOARD_CODE": "1662", "BOARD_NAME": "权重股", "BOARD_RANK": 7, "IS_PRECISE": None},
    {"BOARD_CODE": "1661", "BOARD_NAME": "行业龙头", "BOARD_RANK": 8, "IS_PRECISE": None},
    {"BOARD_CODE": "1059", "BOARD_NAME": "百元股", "BOARD_RANK": 9, "IS_PRECISE": "0"},
    {"BOARD_CODE": "999", "BOARD_NAME": "茅指数", "BOARD_RANK": 10, "IS_PRECISE": "0"},
    {"BOARD_CODE": "879", "BOARD_NAME": "标准普尔", "BOARD_RANK": 11, "IS_PRECISE": "0"},
    {"BOARD_CODE": "867", "BOARD_NAME": "富时罗素", "BOARD_RANK": 12, "IS_PRECISE": "0"},
    {"BOARD_CODE": "821", "BOARD_NAME": "MSCI中国", "BOARD_RANK": 13, "IS_PRECISE": "0"},
    {"BOARD_CODE": "707", "BOARD_NAME": "沪股通", "BOARD_RANK": 14, "IS_PRECISE": "0"},
    {"BOARD_CODE": "612", "BOARD_NAME": "上证180_", "BOARD_RANK": 15, "IS_PRECISE": "0"},
    {"BOARD_CODE": "611", "BOARD_NAME": "上证50_", "BOARD_RANK": 16, "IS_PRECISE": "0"},
    {"BOARD_CODE": "610", "BOARD_NAME": "央视50_", "BOARD_RANK": 17, "IS_PRECISE": "0"},
    {"BOARD_CODE": "596", "BOARD_NAME": "融资融券", "BOARD_RANK": 18, "IS_PRECISE": "0"},
    {"BOARD_CODE": "500", "BOARD_NAME": "HS300_", "BOARD_RANK": 19, "IS_PRECISE": "0"},
    {"BOARD_CODE": "1653", "BOARD_NAME": "味蕾经济", "BOARD_RANK": 20, "IS_PRECISE": "1"},
    {"BOARD_CODE": "477", "BOARD_NAME": "酿酒概念", "BOARD_RANK": 21, "IS_PRECISE": "0"},
    {"BOARD_CODE": "896", "BOARD_NAME": "白酒", "BOARD_RANK": 22, "IS_PRECISE": "1"},
    {"BOARD_CODE": "834", "BOARD_NAME": "乡村振兴", "BOARD_RANK": 23, "IS_PRECISE": "1"},
    {"BOARD_CODE": "811", "BOARD_NAME": "超级品牌", "BOARD_RANK": 24, "IS_PRECISE": "1"},
    {"BOARD_CODE": "683", "BOARD_NAME": "央国企改革", "BOARD_RANK": 25, "IS_PRECISE": "1"},
    {"BOARD_CODE": "665", "BOARD_NAME": "电商概念", "BOARD_RANK": 26, "IS_PRECISE": "1"},
    {"BOARD_CODE": "590", "BOARD_NAME": "西部大开发", "BOARD_RANK": 27, "IS_PRECISE": "1"},
]


def test_classify_boards_splits_maotai_into_four_groups():
    g = classify_boards(SSBK_MAOTAI)
    assert g["industry"] == ["食品饮料", "白酒Ⅱ", "白酒Ⅲ"]
    assert g["region"] == ["贵州板块"]
    assert g["concept"] == ["味蕾经济", "酿酒概念", "白酒", "乡村振兴", "超级品牌", "央国企改革", "电商概念", "西部大开发"]
    assert g["style_index"] == [
        "消费风格", "大盘股", "权重股", "行业龙头", "百元股", "茅指数", "标准普尔", "富时罗素",
        "MSCI中国", "沪股通", "上证180_", "上证50_", "央视50_", "融资融券", "HS300_",
    ]
    # 不丢数据：四组之和等于全量
    assert sum(len(v) for v in g.values()) == len(SSBK_MAOTAI)


def test_classify_boards_keeps_style_labels_out_of_concepts():
    """盘点 #5 的核心诉求：大盘股/MSCI中国 不得与 白酒 混在同一组。"""
    g = classify_boards(SSBK_MAOTAI)
    for style in ("大盘股", "MSCI中国", "标准普尔", "融资融券"):
        assert style in g["style_index"]
        assert style not in g["concept"]
    assert "白酒" in g["concept"]
    assert "白酒" not in g["style_index"]


def test_classify_boards_treats_is_precise_as_string():
    """回归：IS_PRECISE 是字符串，按整数比较会静默失效。

    fixture 取自招商银行(SH600036) 2026-08-29 实抓数据的形状：
    行业三级 → 地域 → 风格/指数 → 概念（尾部 IS_PRECISE='1'）。
    """
    rows = [
        {"BOARD_NAME": "银行", "BOARD_RANK": 1, "IS_PRECISE": None},
        {"BOARD_NAME": "银行Ⅱ", "BOARD_RANK": 2, "IS_PRECISE": "0"},
        {"BOARD_NAME": "股份制银行Ⅲ", "BOARD_RANK": 3, "IS_PRECISE": None},
        {"BOARD_NAME": "广东板块", "BOARD_RANK": 4, "IS_PRECISE": "0"},
        {"BOARD_NAME": "大盘股", "BOARD_RANK": 7, "IS_PRECISE": None},
        {"BOARD_NAME": "跨境支付", "BOARD_RANK": 22, "IS_PRECISE": "1"},
        {"BOARD_NAME": "区块链", "BOARD_RANK": 23, "IS_PRECISE": "1"},
    ]
    g = classify_boards(rows)
    assert g["industry"] == ["银行", "银行Ⅱ", "股份制银行Ⅲ"], g
    assert g["region"] == ["广东板块"], g
    assert g["style_index"] == ["大盘股"], g
    assert g["concept"] == ["跨境支付", "区块链"], g


def test_classify_boards_without_any_precise_flag():
    """无 IS_PRECISE='1' 时不应崩溃，概念组为空。"""
    rows = [
        {"BOARD_NAME": "汽车", "BOARD_RANK": 1, "IS_PRECISE": None},
        {"BOARD_NAME": "乘用车", "BOARD_RANK": 2, "IS_PRECISE": None},
        {"BOARD_NAME": "电动乘用车", "BOARD_RANK": 3, "IS_PRECISE": None},
        {"BOARD_NAME": "广东板块", "BOARD_RANK": 4, "IS_PRECISE": "0"},
        {"BOARD_NAME": "大盘股", "BOARD_RANK": 6, "IS_PRECISE": None},
    ]
    g = classify_boards(rows)
    assert g["industry"] == ["汽车", "乘用车", "电动乘用车"], g
    assert g["region"] == ["广东板块"], g
    assert g["concept"] == [], g
    assert g["style_index"] == ["大盘股"], g


def test_classify_boards_region_suffix_wins_over_rank():
    """地域后缀优先级高于 BOARD_RANK≤3，避免地域被误判为行业。"""
    rows = [{"BOARD_NAME": "深圳板块", "BOARD_RANK": 1, "IS_PRECISE": "0"}]
    g = classify_boards(rows)
    assert g["region"] == ["深圳板块"]
    assert g["industry"] == []


def test_classify_boards_empty_input():
    g = classify_boards([])
    assert g == {"industry": [], "region": [], "concept": [], "style_index": []}
    assert classify_boards(None) == g


def test_classify_boards_skips_rows_without_name():
    rows = [
        {"BOARD_CODE": "1", "BOARD_RANK": 1, "IS_PRECISE": "0"},
        {"BOARD_NAME": "银行", "BOARD_RANK": 1, "IS_PRECISE": "0"},
    ]
    g = classify_boards(rows)
    assert g["industry"] == ["银行"]


def test_classify_boards_tolerates_bad_rank():
    rows = [{"BOARD_NAME": "某某概念", "BOARD_RANK": "N/A", "IS_PRECISE": "1"}]
    g = classify_boards(rows)
    assert g["concept"] == ["某某概念"]


# ---------------------------------------------------------------- 主板块选取（P1-4，2026-09-10）


def _codes(rows):
    return {r["BOARD_NAME"]: r["BOARD_CODE"] for r in rows}


def test_main_board_prefers_industry_l2():
    """主板块 = 行业三级 L2（茅台→白酒Ⅱ），**不是**概念段首个。"""
    groups = classify_boards(SSBK_MAOTAI)
    mb = main_board(groups, _codes(SSBK_MAOTAI))
    # code 已由 main_board 规范化成板块榜格式（F10 原始值是纯数字 1277）
    assert mb == {"name": "白酒Ⅱ", "code": "BK1277", "level": "industry"}
    # 反例固化：概念段起点是「味蕾经济」（ssbk 里首个 IS_PRECISE='1'），
    # 语义上不是主板块——这正是本函数存在的理由（初版取 concept[0] 实测不成立）。
    assert groups["concept"][0] == "味蕾经济"


def test_main_board_falls_back_when_no_l2():
    """行业段只有一级 → 取该一级；不因为缺 L2 就跳到概念。"""
    groups = {"industry": ["银行"], "concept": ["跨境支付"], "region": [], "style_index": []}
    assert main_board(groups, {"银行": "475"}) == {"name": "银行", "code": "BK0475", "level": "industry"}


def test_main_board_concept_fallback_is_level_labelled():
    """无行业段才回落概念首个，且 level 如实标 concept（不冒充行业）。"""
    groups = {"industry": [], "concept": ["跨境支付", "区块链"], "region": [], "style_index": []}
    assert main_board(groups, {"跨境支付": "1071"}) == {
        "name": "跨境支付", "code": "BK1071", "level": "concept",
    }


def test_main_board_empty_is_none_not_fabricated():
    """四组全空 → None（三态：判不出就是判不出，绝不臆造一个板块）。"""
    assert main_board({}, {}) is None
    assert main_board({"industry": [], "concept": [], "region": ["广东板块"]}, {}) is None
    assert main_board(None, None) is None


def test_main_board_missing_code_is_none_field():
    """名字有但拿不到代码 → code=None，不臆造代码（下游按缺省跳过）。"""
    groups = {"industry": ["食品饮料", "白酒Ⅱ"], "concept": [], "region": [], "style_index": []}
    assert main_board(groups, {}) == {"name": "白酒Ⅱ", "code": None, "level": "industry"}


# ---------------------------------------------------------------- 板块代码规范化（P1-4）


def test_board_code_norm_pads_to_four_digits():
    """F10 纯数字 ID → 板块榜 `BK`+4 位补零（实测：白酒Ⅱ 1277→BK1277、银行Ⅱ 475→BK0475）。"""
    assert board_code_norm("1277") == "BK1277"
    assert board_code_norm("475") == "BK0475"   # 不补零会 miss（榜里是 BK0475）
    assert board_code_norm(438) == "BK0438"     # 容忍 int 入参


def test_board_code_norm_passthrough_and_empty():
    assert board_code_norm("BK1033") == "BK1033"  # 已是榜格式 → 原样
    assert board_code_norm(None) is None
    assert board_code_norm("") is None
    assert board_code_norm("X") == "X"            # 非数字非 BK → 原样交给下游匹配


def test_main_board_code_round_trips_through_norm():
    """端到端一致性：F10 取出的主板块代码规范化后，能在板块榜里按 code 命中。"""
    groups = classify_boards(SSBK_MAOTAI)
    mb = main_board(groups, _codes(SSBK_MAOTAI))
    assert mb["code"] == board_code_norm(mb["code"]) == "BK1277"  # 幂等：内部已规范化
