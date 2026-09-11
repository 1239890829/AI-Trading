"""助手工具 `theme_members`（题材成分明细，P2-28① 第一个工具）单测。

此前助手被问「XX 题材有哪些票」时只能凭印象作答——官方题材目录后端早就就绪，
只是**没登记给助手**。这个工具补的是能力空窗。

**本文件锁住三条纪律**（比"能返回数据"重要得多）：
1. 服务不可用/目录为空时，必须**明确说取不到**，绝不凭印象编造成分
   （编成分比说不知道危险得多——用户会照着假成分做决策）；
2. 模糊命中多个题材时**列出候选**让人/模型指认，不擅自挑一个；
3. 未命中时给**目录示例**，帮助模型换个说法重试，而不是干巴巴说"没有"。
"""
from __future__ import annotations

import asyncio

import pytest

from app.assistant.tools import ToolCall, ToolContext, run_tool


class _Concept:
    def __init__(self, code: str, name: str):
        self.code = code
        self.name = name


class _Member:
    def __init__(self, symbol: str, name: str = ""):
        self.symbol = symbol
        self.name = name


class _Catalog:
    """最简官方题材目录服务。"""

    def __init__(self, concepts: list[tuple[str, str]], members: dict[str, list] | None = None,
                 *, boom: bool = False):
        self._concepts = [_Concept(c, n) for c, n in concepts]
        self._members = members or {}
        self._boom = boom

    def get_catalog(self, limit: int = 1000):
        if self._boom:
            raise RuntimeError("catalog down")
        return self._concepts[:limit]

    def get_members(self, code: str):
        if self._boom:
            raise RuntimeError("members down")
        return self._members.get(code, [])


def _ctx(catalog=None) -> ToolContext:
    return ToolContext(provider=object(), theme_catalog=catalog)


def _run(call: ToolCall, ctx: ToolContext) -> str:
    return asyncio.run(run_tool(call, ctx, cache=None))


# ---------------------------------------------------------------- 纪律一：不编造


def test_catalog_missing_says_unavailable_not_empty():
    """服务不可用 ⇒ 说"取不到"，**不是**"没有该题材"（那会让人以为真没有）。"""
    out = _run(ToolCall("theme_members", {"keyword": "代糖"}), _ctx(catalog=None))
    assert "取不到" in out or "不可用" in out
    assert "没有" not in out or "不是没有" in out


def test_empty_catalog_is_explicit():
    out = _run(ToolCall("theme_members", {"keyword": "代糖"}), _ctx(_Catalog([])))
    assert "为空" in out


def test_catalog_failure_is_reported():
    out = _run(ToolCall("theme_members", {"keyword": "代糖"}), _ctx(_Catalog([], boom=True)))
    assert "失败" in out


# ---------------------------------------------------------------- 纪律二：多命中列候选


def test_multiple_hits_asks_for_disambiguation():
    """命中多个 ⇒ 列候选，不擅自挑一个（挑错等于给错成分）。"""
    cat = _Catalog([("885904", "代糖概念"), ("885905", "代糖加工"), ("885906", "其他")])
    out = _run(ToolCall("theme_members", {"keyword": "代糖"}), _ctx(cat))
    assert "命中 2 个" in out or "请指明" in out
    assert "代糖概念" in out and "代糖加工" in out


# ---------------------------------------------------------------- 纪律三：未命中给示例


def test_no_hit_offers_examples():
    cat = _Catalog([("885904", "代糖概念"), ("885811", "玉米")])
    out = _run(ToolCall("theme_members", {"keyword": "量子计算"}), _ctx(cat))
    assert "未找到" in out
    assert "代糖概念" in out, "应给目录示例，帮助模型换个说法重试"


def test_missing_keyword_is_rejected():
    out = _run(ToolCall("theme_members", {}), _ctx(_Catalog([("1", "x")])))
    assert "参数缺失" in out


# ---------------------------------------------------------------- 正常返回


def test_single_hit_returns_members():
    cat = _Catalog(
        [("885904", "代糖概念")],
        {"885904": [_Member("600866", "星湖科技"), _Member("002286", "保龄宝")]},
    )
    out = _run(ToolCall("theme_members", {"keyword": "代糖"}), _ctx(cat))
    assert "代糖概念" in out
    assert "星湖科技" in out and "保龄宝" in out
    assert "600866" in out


def test_no_members_is_stated():
    cat = _Catalog([("885904", "代糖概念")], {"885904": []})
    out = _run(ToolCall("theme_members", {"keyword": "代糖"}), _ctx(cat))
    assert "暂无成分" in out


def test_members_failure_is_reported():
    cat = _Catalog([("885904", "代糖概念")], boom=False)
    # get_members 抛异常
    class _Boom(_Catalog):
        def get_members(self, code):
            raise RuntimeError("members down")

    out = _run(ToolCall("theme_members", {"keyword": "代糖"}),
               _ctx(_Boom([("885904", "代糖概念")])))
    assert "失败" in out


def test_tool_is_registered_with_label():
    from app.assistant import tools as T

    specs = getattr(T, "TOOLS", None) or getattr(T, "TOOL_SPECS", {})
    assert "theme_members" in specs
    assert T.tool_label("theme_members") == "题材成分"
