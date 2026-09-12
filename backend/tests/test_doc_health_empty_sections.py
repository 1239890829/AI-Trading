"""`scripts/doc-health.py` 的「I 空章节」检查器自证测试（2026-09-12）。

## 为什么单独立一个测试文件

I 项是本轮新加的**结构检查**（标题在、正文 0 行），它要抓的缺陷形态很特殊：
**其余检查全绿**。实例——`docs/retro-and-gaps.md` 的 §6.6 正文曾被 §6.7 的标题截断、
落到了 6.7 的表下方，表现是「6.6 标题空着、正文挂在别的章节里」；A/B/C/D/F 全过，
没有任何既有检查能发现它，按目录点进去只有一行标题。

检查器**自身失效的方式恰好与它要抓的缺陷同形**（同样是"看起来是绿的"），所以按项目
纪律把**注入验证固化成常驻测试**（`doc-code-reconcile` 步骤 7：只手动注入一次，
下次有人改判据时没人会再注入）。

## 本文件主要钉住的分界点

「空章节」= 本标题之后、到**下一个同级或更高级标题**之前没有任何正文行。
⚠️ **不是**「到下一个任意标题之前」——`## 7` 紧跟 `### 7.1` 是**合法的容器写法**，
按后者判会把所有"带子标题的父章节"全部误报；门禁一旦长期挂红就会被整体无视
（KB-ENG-58）。`test_parent_heading_with_child_is_not_flagged` 就是给这条判据立的钉子。

## 扫描面为什么跳过 `archive/`

`docs/archive/**` 是只读历史快照（与 `check_missing_abstract` 的豁免口径一致）。
把它扫进来会逼人**改历史**来让门禁变绿——那是伪装成修复的破坏。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "doc-health.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("doc_health_probe", SCRIPT)
    assert spec is not None and spec.loader is not None, f"加载失败：{SCRIPT}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Probe:
    """把检查器的扫描面指向临时 `docs/`，避免测试污染真实仓库。"""

    def __init__(self, mod: ModuleType, docs: Path) -> None:
        self.mod = mod
        self.docs = docs

    def write(self, rel: str, text: str) -> None:
        p = self.docs / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def check(self) -> tuple[list[tuple[str, int, str]], list[str]]:
        return self.mod.check_empty_sections()

    def titles(self) -> list[str]:
        return [t for _, _, t in self.check()[0]]


@pytest.fixture()
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Probe:
    mod = _load()
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "DOCS", docs)
    monkeypatch.setattr(mod, "EMPTY_SECTION_ROOT_FILES", ())
    monkeypatch.setattr(mod, "EMPTY_SECTION_ALLOW", {})
    return Probe(mod, docs)


def test_sibling_heading_immediately_after_is_an_empty_section(probe: Probe) -> None:
    """复现 §6.6 的缺陷形态：同级标题紧跟其后，中间只有空行。"""
    probe.write(
        "a.md",
        "## 一\n\n正文\n\n### 1.1 标题在正文没了\n\n### 1.2 下一节\n\n正文\n",
    )
    assert probe.titles() == ["1.1 标题在正文没了"]


def test_parent_heading_with_child_is_not_flagged(probe: Probe) -> None:
    """**判据的分界点**：`## 七` 紧跟 `### 7.1` 是合法容器写法，不得误报。

    若这条变红，说明判据被改成了「到下一个任意标题之前」——那会把全站所有
    "带子标题的父章节"判成空章节，门禁随即被整体无视（KB-ENG-58）。
    """
    probe.write("a.md", "## 七\n\n### 7.1 子节\n\n正文\n")
    assert probe.titles() == []


def test_hr_and_html_comment_are_not_body(probe: Probe) -> None:
    """只有 `---` / `***` / HTML 注释的章节仍是空章节（它们不是正文）。"""
    probe.write("a.md", "## 一\n\n---\n\n***\n\n<!-- 待补 -->\n\n## 二\n\n正文\n")
    assert probe.titles() == ["一"]


def test_strip_fences_replaces_fenced_lines(probe: Probe) -> None:
    """围栏代码块内的行必须被置哨兵——否则代码示例里的 `# 注释` 会被当成标题。"""
    out = probe.mod._strip_fences(["```bash\n", "# 注释\n", "```\n", "正文\n"])
    assert out[1] == probe.mod._FENCE
    assert out[3] == "正文\n"


def test_fenced_hash_line_is_not_treated_as_heading(probe: Probe) -> None:
    """端到端：围栏内的 `# 行` 不得参与标题配对。"""
    probe.write("a.md", "## 一\n\n```bash\n# 这是注释\n```\n\n正文\n")
    assert probe.titles() == []


def test_allowlisted_heading_is_suppressed_and_not_ghost(probe: Probe) -> None:
    """登记过的容忍项：不计命中，也不得被判成"失效"。"""
    probe.write("a.md", "## 一\n\n### 1.1 有意留空\n\n### 1.2 下一节\n\n正文\n")
    probe.mod.EMPTY_SECTION_ALLOW[("docs/a.md", "1.1 有意留空")] = "标题即结论"
    hits, ghost = probe.check()
    assert hits == []
    assert ghost == []


def test_allowlist_turns_into_ghost_when_heading_is_renamed(probe: Probe) -> None:
    """标题被改写 ⇒ 容忍项键对不上 ⇒ **必须被报出来**。

    没有这条反向断言，容忍名单会像 `CLAIM_EXEMPT` 那样静默腐烂：名字早就不存在了，
    却仍被当成"已登记、无需处理"，而没人知道。
    """
    probe.write("a.md", "## 一\n\n### 1.1 措辞改了\n\n### 1.2 下一节\n\n正文\n")
    probe.mod.EMPTY_SECTION_ALLOW[("docs/a.md", "1.1 有意留空")] = "标题即结论"
    hits, ghost = probe.check()
    assert [t for _, _, t in hits] == ["1.1 措辞改了"]
    assert len(ghost) == 1 and "1.1 有意留空" in ghost[0]


def test_archive_is_out_of_scope(probe: Probe) -> None:
    """`archive/**` 是只读历史快照，刻意不扫（扫它＝逼人改历史来让门禁变绿）。"""
    probe.write("archive/old.md", "## 一\n\n### 1.1 空\n\n### 1.2 有正文\n\n正文\n")
    assert probe.titles() == []


def test_real_repo_has_no_unregistered_empty_section() -> None:
    """在**真实仓库**上跑一遍。

    一箭双雕：①证明检查器在真实扫描面上可运行（不是只在合成树上能跑）；
    ②把"仓库当前干净"钉住——若这条与 `doc-health` 同时变红，说明新出现了空章节，
    按输出提示补正文或登记容忍项即可（这是**期望的双红**，不是误报）。
    """
    hits, ghost = _load().check_empty_sections()
    assert hits == [], f"出现空章节（补正文，或登记 EMPTY_SECTION_ALLOW）：{hits}"
    assert ghost == [], f"容忍项已失效（标题改了/文件搬了，须删该条）：{ghost}"
