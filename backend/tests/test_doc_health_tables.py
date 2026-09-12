"""`scripts/doc-health.py` 的「K 表格分隔行」检查器自证测试（2026-09-13）。

## 为什么单独立一个测试文件

K 项是本轮新加的**结构检查**（`|` 行块首行的下一行必须是 GFM 分隔行），它要抓的缺陷形态
与 I 项同族：**其余检查全绿**。实例——§6.14 的渲染器死循环事故，`docs/` 下 4 份 / 49 行
用**空行给同一张表分组**；这类书写不会让 A/B/C/D/F/G/I/J 任何一项变红，按目录点进去
表格只是"渲染成了几段文字"，**没有任何信号提示书写有问题**。

检查器**自身失效的方式恰好与它要抓的缺陷同形**（同样是"看起来是绿的"），所以按项目纪律
把**注入验证固化成常驻测试**（`doc-code-reconcile` 步骤 7：只手动注入一次，
下次有人改判据时没人会再注入）。

## 本文件主要钉住的分界点

判据 = **`|` 起始的行块首行，其下一行必须是分隔行**。三条分界点各配一条钉子：

1. **"块首行"而非"每一行"**：一张表只该报一次（块首），否则表体每行都命中、噪音爆炸
   ⇒ `test_only_block_first_line_is_reported`；
2. **`---` 不是分隔行**：它是**水平分隔线**（`_HR_LINES`）。少了"必须含 `|`"这一条，
   `---` 之后的块首行会被误判为通过 ⇒ `test_hr_line_is_not_a_delimiter`；
3. **`| |`（两列皆空）不是分隔行**：必须含 `-` ⇒ `test_pipe_only_row_is_not_a_delimiter`。

## 扫描面为什么跳过 `archive/`

与 I 项同口径：`docs/archive/**` 是只读历史快照。把它扫进来会逼人**改历史**来让门禁变绿。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "doc-health.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("doc_health_tables_probe", SCRIPT)
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

    def hits(self) -> list[tuple[str, int, str]]:
        return self.mod.check_table_delimiters()

    def lines(self) -> list[int]:
        return [ln for _, ln, _ in self.hits()]


@pytest.fixture()
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Probe:
    mod = _load()
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "DOCS", docs)
    monkeypatch.setattr(mod, "EMPTY_SECTION_ROOT_FILES", ())
    return Probe(mod, docs)


def test_blank_line_grouped_table_is_flagged(probe: Probe) -> None:
    """复现 §6.14 的缺陷形态：用**空行**给同一张表分组。"""
    probe.write(
        "a.md",
        "| A | B |\n"
        "|---|---|\n"
        "| 1 | 2 |\n"
        "\n"
        "| 3 | 4 |\n"
        "| 5 | 6 |\n",
    )
    assert probe.lines() == [5]


def test_header_without_delimiter_is_flagged(probe: Probe) -> None:
    """表头忘写分隔行 ⇒ 在严格 GFM 下同样不成表。"""
    probe.write("a.md", "| 表头 | 又一行 |\n| 数据 | 数据 |\n")
    assert probe.lines() == [1]


def test_valid_table_passes(probe: Probe) -> None:
    """正向对照：合法表格不得误报（防"一律报红"的假守卫）。"""
    probe.write("a.md", "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n")
    assert probe.hits() == []


def test_only_block_first_line_is_reported(probe: Probe) -> None:
    """**判据分界点**：一张表只报**块首行**一次，不是表体每行都报。

    若这条变红，说明"块首行"过滤被去掉 ⇒ 任何一张表都会产出几十条噪音，
    门禁随即被整体无视（KB-ENG-58）。
    """
    probe.write("a.md", "| A |\n| 1 |\n| 2 |\n| 3 |\n| 4 |\n")
    assert probe.lines() == [1], "只应报块首行"


def test_hr_line_is_not_a_delimiter(probe: Probe) -> None:
    """**判据分界点**：`---` 是水平分隔线，**不是**表分隔行。

    分隔行必须同时含 `|` 与 `-`。少了"含 `|`"这一条，`| A | B |` 会被判成通过
    （`---` 被误当分隔行）⇒ 期望 `[]` 而非 `[1]`，本钉子即失效。
    """
    probe.write("a.md", "| A | B |\n---\n")
    assert probe.lines() == [1]


def test_hr_line_splits_the_pipe_block(probe: Probe) -> None:
    """`---` 会**断开** `|` 块 ⇒ 它两侧各自是独立块首行，各自判。

    这条记录的是判据的**块切分语义**（首版测试就写错了预期）：`| A |` / `---` / `| 1 |`
    里第 1、3 行都是"下一行不是分隔行"的块首 ⇒ **两处都该报**。
    不是缺陷——那两行确实都不成表。
    """
    probe.write("a.md", "| A | B |\n---\n| 1 | 2 |\n")
    assert probe.lines() == [1, 3]


def test_pipe_only_row_is_not_a_delimiter(probe: Probe) -> None:
    """**判据分界点**：`| |`（两列皆空）不是分隔行——必须含 `-`。"""
    probe.write("a.md", "| A | B |\n| | |\n| 1 | 2 |\n")
    assert probe.lines() == [1]


def test_fenced_pipe_lines_are_ignored(probe: Probe) -> None:
    """围栏代码块内的 `|` 行不参与判定（目录树 / 示例里的 `|` 是合法内容）。"""
    probe.write("a.md", "```\n| 不该被扫 | x |\n```\n\n| A |\n|---|\n| 1 |\n")
    assert probe.hits() == []


def test_inline_pipe_in_text_is_ignored(probe: Probe) -> None:
    """**行首**没有 `|` 的普通文字不参与判定（判据只看行首）。"""
    probe.write("a.md", "这是一句带 | 的普通文字，不是表格。\n\n| A |\n|---|\n| 1 |\n")
    assert probe.hits() == []


def test_archive_is_out_of_scope(probe: Probe) -> None:
    """`archive/**` 是只读历史快照，刻意不扫（扫它＝逼人改历史来让门禁变绿）。"""
    probe.write("archive/old.md", "| A | B |\n| 1 | 2 |\n")
    assert probe.hits() == []


def test_delimiter_with_alignment_colons_passes(probe: Probe) -> None:
    """带对齐冒号的分隔行（`:---:`）必须通过——它们是合法 GFM。"""
    probe.write("a.md", "| A | B | C |\n|:---|---:|:---:|\n| 1 | 2 | 3 |\n")
    assert probe.hits() == []


def test_real_repo_has_no_broken_table() -> None:
    """在**真实仓库**上跑一遍。

    一箭双雕：①证明检查器在真实扫描面上可运行（不是只在合成树上能跑）；
    ②把"仓库当前干净"钉住——若这条与 `doc-health` 同时变红，说明新出现了
    空行分组 / 缺分隔行，按输出提示补分隔行即可（这是**期望的双红**，不是误报）。
    """
    hits = _load().check_table_delimiters()
    assert hits == [], f"出现不成表的 `|` 行块（补 GFM 分隔行，或把内容放进围栏）：{hits}"
