"""助手「认知缺口」自曝检测（`app/assistant/cognition.py`）单测。

**为什么补这个测试**：2026-09-11 做防腐化盘点时发现，这两个函数**在测试里零覆盖**——
函数本身已接线（`api/routes/assistant.py` 的 chat 流里会在"本轮未调工具 + 回答声称无数据"时
`log.warning`），但**没有任何用例守护它的判定逻辑**。

这个判定不简单，藏着几个容易改坏的点：
- **中文语序**：宾语常在动词前（"这部分**行情**我无法获取"），只向后取窗口会漏；
- **宁缺勿滥**：调用过工具就放过、没有数据类名词也放过（"我没有卖出建议"不是缺口）；
- 它只留痕、不改答案——**误报的代价是污染清单，漏报的代价是缺口一直不被发现**，
  所以两边的边界都得钉住。
"""
from __future__ import annotations

from app.assistant.cognition import (
    DENIAL_MARKERS,
    describe_gap,
    looks_like_false_denial,
)


# ---------------------------------------------------------------- 命中


def test_denial_with_data_hint_is_flagged():
    assert looks_like_false_denial("我没有该股的分时明细数据") is True


def test_object_before_verb_is_still_caught():
    """**中文语序定点回归**：宾语在动词之前，只向后取窗口会漏判。

    这是 `LOOKBEHIND` 存在的唯一理由，删掉它这条就会红。
    """
    assert looks_like_false_denial("这部分行情我无法获取") is True


def test_all_denial_markers_are_recognised():
    """每个否认表达都要能命中（防新增 marker 后忘了验证）。"""
    for marker in DENIAL_MARKERS:
        assert looks_like_false_denial(f"{marker}相关的行情数据"), marker


def test_hint_far_from_marker_is_not_counted():
    """数据词必须落在窗口内——隔太远的另一个话题不算。"""
    text = "我无法获取" + "无关内容" * 30 + "资金"
    assert looks_like_false_denial(text) is False


# ---------------------------------------------------------------- 放过（宁缺勿滥）


def test_tools_used_means_not_a_gap():
    """调用过工具 ⇒ 取不到是数据源的事，不是认知缺口。**这条优先级最高**。"""
    assert looks_like_false_denial("我没有该股的分时数据", tools_used=["stock_minute"]) is False


def test_denial_without_data_hint_is_not_a_gap():
    """「我没有卖出建议」这类正当声明不得误判成数据缺口。"""
    assert looks_like_false_denial("我没有卖出建议") is False
    assert looks_like_false_denial("我不具备投顾资质") is False


def test_empty_text_is_false():
    assert looks_like_false_denial("") is False
    assert looks_like_false_denial(None) is False  # type: ignore[arg-type]


def test_plain_answer_is_false():
    assert looks_like_false_denial("该股今日主力净流入 1.2 亿，涨幅 3.4%") is False


# ---------------------------------------------------------------- describe_gap


def test_describe_gap_truncates_both_sides():
    """日志摘要必须截断——不能把整段回答写进日志。"""
    q = "问" * 200
    a = "答" * 500
    out = describe_gap(a, q)
    assert len(out) < 260, "摘要过长，日志会被整段回答淹没"
    assert out.startswith("q=")


def test_describe_gap_handles_empty_question():
    out = describe_gap("我没有这项数据")
    assert "q=''" in out
    assert "我没有这项数据" in out


def test_describe_gap_collapses_whitespace():
    """原文里的换行/连续空格要压平，保证日志单行可读。"""
    out = describe_gap("没有\n\n这项   数据")
    assert "\n" not in out
    assert "这项 数据" in out
