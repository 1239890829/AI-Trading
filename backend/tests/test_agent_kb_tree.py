"""知识库面板分层口径单测（零网络、零落库）。

**为什么要测这个**：`/api/agent/kb/tree` 原先 `rglob("*.md")` 无差别扫描 docs/，
79 份里只有 11 份是 canonical 知识库，22 份归档件与 13 份逐日日志平铺并列 ——
用户无法从界面分辨「这是现行规则」还是「这是历史结论」，与
`docs/kb/07-doc-curation.md` 的「状态语义不得混用」纪律冲突。修法是给每份打
`tier` 由前端分层折叠。

**本测试锁的是 fallback 方向**：未登记的顶层目录落 `current`（展开可见），
而不是落 `history`（藏进折叠区）。理由是失败要可见——将来新增一个 docs/ 子目录时，
「多显示一份噪音」是可发现、可纠正的；「新内容静默消失」则要等用户搜不到才发现。

> 分层表是**故意硬编码**在本测试里的（未从 `_DOC_TIER_DIRS` 取），否则实现写错
> 测试会跟着一起错——镜像测试恒绿，等于没测。
"""
from __future__ import annotations

import asyncio

import pytest

from app.api.routes.agent import _tier_of, kb_tree

#: 与 `docs/kb/07-doc-curation.md` §7 文档分层模型对齐的期望口径
EXPECTED_TIER: dict[str, str] = {
    "kb": "canonical",           # L0 唯一权威
    "": "current",               # docs/ 根（L1/L2 现役：INDEX / PROJECT-MASTER 等）
    "summary": "current",        # L2 现役专题
    "archive": "history",        # 只读历史
    "daily-review": "timeline",  # L4 时间序列
    "evolution": "timeline",
    "repo-watch": "timeline",
    "push-templates": "timeline",
}

_ALLOWED = {"canonical", "current", "history", "timeline"}


@pytest.mark.parametrize(("top_dir", "tier"), sorted(EXPECTED_TIER.items()))
def test_tier_of_matches_layering_spec(top_dir: str, tier: str):
    assert _tier_of(top_dir) == tier


def test_unregistered_dir_falls_back_to_current_not_history():
    """未登记的新目录必须落到 `current`（展开可见）。

    若这条失败，说明 fallback 被改成了 history/timeline —— 效果是**将来新增的
    docs/ 子目录会静默藏进折叠区**，用户搜不到也看不到，只有等有人抱怨才发现。
    """
    for unknown in ("brand-new-section", "tmp", "notes", "archive2"):
        got = _tier_of(unknown)
        assert got == "current", f"{unknown} 落到了 {got}，新内容会被静默折叠"


def test_docs_root_is_not_treated_as_history():
    """docs/ 根目录（dir_key 为空串）属现役层。

    空串是个容易踩的边界：`"" in ("", "summary")` 为 True 是**有意为之**，
    但若有人把空串从表里删掉，根目录的 27 份现役文档会整体掉进折叠区。
    """
    assert _tier_of("") == "current"


def test_every_registered_tier_is_a_known_value():
    """分层标识是前后端契约（前端 `KbTier` 联合类型），不得出现表外取值。"""
    for top_dir in EXPECTED_TIER:
        assert _tier_of(top_dir) in _ALLOWED


def test_kb_tree_assigns_tier_by_top_level_dir_on_real_docs():
    """端到端接线检查：`kb_tree()` 必须按**顶层目录**（而非完整相对路径）判分层。

    这条用真实 docs/ 数据跑（只读、不写盘）。断言是**逐文件自洽**的：每份文档的
    tier 必须等于其顶层目录对应的层，因此文件在目录间搬动不会让断言失效。
    """
    payload = asyncio.run(kb_tree())
    files = payload["data"]["files"]
    assert files, "docs/ 下应当至少有一份 .md"

    for f in files:
        top = f["dir"].split("/")[0] if f["dir"] else ""
        assert f["tier"] in _ALLOWED, f"{f['path']} 的 tier 取值非法：{f['tier']}"
        assert f["tier"] == EXPECTED_TIER.get(top, "current"), (
            f"{f['path']}（顶层目录 {top!r}）分层为 {f['tier']}，与口径不符"
        )


def test_kb_tree_canonical_files_are_the_kb_dir():
    """canonical 层必须恰好等于 `kb/` 目录 —— 面板把它标为「唯一权威」，
    一旦别的目录混进来，「唯一权威」这个措辞就失真了。"""
    payload = asyncio.run(kb_tree())
    files = payload["data"]["files"]

    canonical = {f["path"] for f in files if f["tier"] == "canonical"}
    in_kb = {f["path"] for f in files if f["path"].startswith("kb/")}

    assert canonical, "canonical 层不应为空"
    assert canonical == in_kb, f"canonical 层与 kb/ 目录不一致：{canonical ^ in_kb}"
