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

import json

import pytest

from app.assistant import cognition as cog
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


# ---------------------------------------------------------------- 缺口台账（P2-28③）


def test_record_gap_writes_and_recent_gaps_reads_back(tmp_path, monkeypatch):
    """**定点回归**：落盘后能被读回。

    此前只有 `log.warning` ⇒ 日志会滚动、没人聚合，"缺口清单"实际没有消费方
    （KB-ENG-49 说"日志即自动产出的缺口清单"，但没人看）。落台账才成形。
    """
    monkeypatch.setattr(cog, "GAP_LOG_PATH", tmp_path / "gaps.jsonl")
    rec = cog.record_gap("我没有该股的分时明细数据", "这只股票分时怎样")
    assert rec is not None and rec["at"]
    assert "分时" in rec["answer"]

    gaps = cog.recent_gaps()
    assert len(gaps) == 1
    assert gaps[0]["question"] == "这只股票分时怎样"


def test_record_gap_never_raises(tmp_path, monkeypatch):
    """留痕是旁路——写不进去也必须**不影响回答**。"""
    monkeypatch.setattr(cog, "GAP_LOG_PATH", tmp_path / "no" / "such" / "dir" / "g.jsonl")
    # 目录不存在且无法创建（父目录是文件）
    (tmp_path / "no").write_text("x", encoding="utf-8")
    try:
        assert cog.record_gap("x", "y") is None
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"record_gap 抛出了异常：{exc}")


def test_recent_gaps_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(cog, "GAP_LOG_PATH", tmp_path / "nope.jsonl")
    assert cog.recent_gaps() == []


def test_recent_gaps_skips_corrupt_lines(tmp_path, monkeypatch):
    """单行损坏跳过，**不因一行坏掉整张清单**。"""
    p = tmp_path / "g.jsonl"
    p.write_text('{"at": "1", "question": "q1", "answer": "a1"}\n{ broken\n{"at": "2", "question": "q2", "answer": "a2"}\n',
                 encoding="utf-8")
    monkeypatch.setattr(cog, "GAP_LOG_PATH", p)
    gaps = cog.recent_gaps()
    assert [g["at"] for g in gaps] == ["1", "2"]


def test_recent_gaps_respects_limit(tmp_path, monkeypatch):
    p = tmp_path / "g.jsonl"
    p.write_text("\n".join(json.dumps({"at": str(i)}) for i in range(10)) + "\n", encoding="utf-8")
    monkeypatch.setattr(cog, "GAP_LOG_PATH", p)
    assert len(cog.recent_gaps(limit=3)) == 3
    assert [g["at"] for g in cog.recent_gaps(limit=3)] == ["7", "8", "9"]
