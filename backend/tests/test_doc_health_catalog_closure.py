"""`scripts/doc-health.py` 的「O 编目完整性」检查器自证测试（2026-09-14，GOV-008）。

## 为什么单独立一个测试文件

`docs/INDEX.md` §0.0 书库编目是**全仓唯一的编目面**，而「**可定位**」是
「一份好文档」的第一条判据（`kb/11`）——**找不到 = 等于不存在**。但它长期**只靠人守**：
新增文档忘了登记、文档删了条目留着，**没有任何一项检查会红**（B 只认正文里的
`docs/**.md` 引用，不认编目表本身）。

## ⚠️ 为什么必须先「量噪声」再定档（[[KB-ENG-68]] 同族）

编目表的写法有**三种基线**共存，朴素抽取（凡反引号里的 `.md` 都当 docs 相对路径）
会把 `kb/*` 12 份、`summary/*` 6 份、`evolution/`、`repo-watch/` 全判成「未登记」：
**把一份本来正确的编目表固化成门禁，比没有门禁更糟**——它会逼人去"修"对的文档。

故上线前先按当前仓库实测口径是否诚实：**83 份 md / 48 条文件条目 / 10 条目录条目，
双向 0 命中** ⇒ 判据可信，才落门禁。本文件把这次实测的三种写法**逐条钉成用例**，
防止有人"简化"判据时把其中一种写法悄悄判成未登记。

## 本文件主要钉住的分界点

| # | 分界点 | 钉子 |
|---|---|---|
| 1 | 三种基线：`docs/` 相对 · 仓库相对（`（根）`/`artifacts/`）· 区间行裸名继承目录 | `test_*_baseline*` |
| 2 | **双向**都是缺口：未登记 / 幽灵条目 | `test_unregistered_*` · `test_ghost_*` |
| 3 | 目录级条目覆盖其下全部文件（`DR-01 daily-review/`） | `test_directory_entry_covers_children` |
| 4 | 占位名（`YYYY-MM-DD.md`）与区间行省略号不是真条目 | `test_placeholder_is_not_an_entry` |
| 5 | **保险丝**：编目表缺失/解析为空 ⇒ 判红，不静默跳过 | `test_missing_catalog_fails_loud` |
| 6 | 正向对照：合法编目不得误报（防"一律报红"的假守卫） | `test_clean_catalog_passes` |
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "doc-health.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("doc_health_catalog_probe", SCRIPT)
    assert spec is not None and spec.loader is not None, f"加载失败：{SCRIPT}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Probe:
    def __init__(self, mod: ModuleType, root: Path, docs: Path) -> None:
        self.mod = mod
        self.root = root
        self.docs = docs
        #: 夹具文档根**相对仓库根**的路径。⚠️ 刻意不叫 `docs/`——夹具里写的都是
        #: **临时路径**（`daily-review/2026-09-15.md` 之类并不存在于真实仓库），
        #: 若字面写成 `docs/...` 会被 **F 项（代码注释死引用）**判成死链：那项的
        #: 用途是抓「删档后残留的文档指针」，而这里从来不是指针。用变量拼路径既
        #: 不污染 F 的例外名单，也不必为每条夹具去登记理由。
        self.d = docs.relative_to(root).as_posix()

    def write(self, rel: str, text: str = "x\n") -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def catalog(self, *rows: str, kb_rows: str = "") -> None:
        """写出一张编目表（含 §0.0 章节头）。`rows` 为表格数据行。

        ⚠️ 默认**先补 AG-03（`INDEX.md` 自身）**：检查器对编目表**本身**不设豁免
        （"docs/ 下的一切都要可定位"不留例外面），而 `INDEX.md` 确实在真实编目里
        登记为 AG-03。若不补，每个用例都会先被这一条污染（实测 6 例全红，
        而报错原因与被测点无关）——**夹具必须反映真实形态，不能靠豁免绕开**。
        """
        table = "\n".join(
            [
                "| 编号 | 文档 | 层 | 领域 | 用途 |",
                "|---|---|---|---|---|",
                "| **AG-03** | `INDEX.md` | L3 | 编目 | 文档总入口 |",
                *rows,
            ]
        )
        kb = ""
        if kb_rows:
            kb = "\n\n| 编号 | 册 | 领域 | 条目数 | 说明 |\n|---|---|---|---|---|\n" + kb_rows
        self.write(
            f"{self.d}/INDEX.md",
            "# 文档索引（INDEX）\n\n## 0.0 书库编目（编号 / 层 / 领域 / 用途）\n\n" + table + kb + "\n",
        )

    def unregistered(self) -> list[str]:
        return self.mod.check_catalog_closure()[0]

    def ghosts(self) -> list[str]:
        return self.mod.check_catalog_closure()[1]


@pytest.fixture()
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Probe:
    mod = _load()
    docs = tmp_path / "doclib"
    docs.mkdir()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "DOCS", docs)
    return Probe(mod, tmp_path, docs)


# ------------------------------------------------------------------ 正向对照

def test_clean_catalog_passes(probe: Probe) -> None:
    """**防假守卫**：一份合法编目必须全绿。

    "一律报红"的守卫能通过注入测试、却在生产无法使用；故每个检查器都要有正向用例。
    """
    probe.write(f"{probe.d}/plan-registry.md")
    probe.catalog("| **AG-05** | `plan-registry.md` | L3 | 计划 | 历史计划去向表 |")
    assert probe.unregistered() == []
    assert probe.ghosts() == []


def test_directory_entry_covers_children(probe: Probe) -> None:
    """**目录级条目**：`DR-01 daily-review/` 覆盖其下所有逐日报告。

    逐日报告每天新增一份，逐份登记不现实（也违反 L4 的定位）；
    但**整个目录**必须登记，否则等于没有入口。
    """
    probe.write(f"{probe.d}/daily-review/2026-09-14.md")
    probe.write(f"{probe.d}/daily-review/2026-09-15.md")
    probe.catalog("| **DR-01** | `daily-review/` | L1 | 存档 | 逐日复盘报告 |")
    assert probe.unregistered() == []


# ------------------------------------------------------------------ 三种基线

def test_docs_relative_is_default_baseline(probe: Probe) -> None:
    probe.write(f"{probe.d}/kb/07-doc-curation.md")
    probe.catalog("| **KW-07** | `kb/07-doc-curation.md` | L2 | 治理 | 文档治理流程 |")
    assert probe.unregistered() == []
    assert probe.ghosts() == []


def test_root_marked_entry_resolves_against_repo_root(probe: Probe) -> None:
    """`（根）` 标记的条目是**仓库相对**（`AGENTS.md` / `README.md` / `CONTEXT.md`）。

    按 docs 相对解析会判成幽灵条目 ⇒ 误报会倒逼人去改一份正确的编目表。
    """
    probe.write("AGENTS.md")
    probe.catalog("| **AG-01** | `AGENTS.md`（根） | L3 | 工程治理 | 作业手册 |")
    assert probe.ghosts() == []


def test_workbuddy_entry_resolves_against_repo_root(probe: Probe) -> None:
    probe.write("artifacts/logs/INDEX.md")
    probe.catalog("| **WB-01** | `artifacts/logs/` | L1 | 日志 | 逐日日志 |")
    assert probe.ghosts() == []


def test_interval_row_bare_stems_inherit_directory(probe: Probe) -> None:
    """**区间行裸名**：`SM-01..06` 的 `summary/stock-strategy` · `factor-system` · …

    后续裸名**继承前一个带路径 token 的目录**。只认带斜杠的那种 ⇒ 6 份 summary
    会被判成未登记（实测：这正是朴素抽取的 6 例误报）。
    """
    probe.write(f"{probe.d}/summary/stock-strategy.md")
    probe.write(f"{probe.d}/summary/factor-system.md")
    probe.catalog(
        "| **SM-01..06** | `summary/stock-strategy` · `factor-system` | L2 | 主题汇总 | 查主题先看这里 |"
    )
    assert probe.unregistered() == []


def test_kb_subtable_is_relative_to_kb_dir(probe: Probe) -> None:
    """KB 分表的册名是**相对 `docs/kb/`** 的简写，不是 docs 相对。"""
    probe.write(f"{probe.d}/kb/00-INDEX.md")
    probe.catalog(
        "| **KW-00..11** | `kb/00-INDEX.md` | L2 | 知识 | 见分表 |",
        kb_rows="| **KW-00** | `00-INDEX.md` | 总索引 | — | 全序列唯一登记处 |",
    )
    assert probe.unregistered() == []
    assert probe.ghosts() == []


# ------------------------------------------------------------------ 双向缺口

def test_unregistered_file_is_flagged(probe: Probe) -> None:
    """**A 方向**：文件在、编目没有 ⇒ 判红（找不到 = 等于不存在）。"""
    probe.write(f"{probe.d}/orphan-notes.md")
    probe.catalog("| **AG-05** | `plan-registry.md` | L3 | 计划 | — |")
    probe.write(f"{probe.d}/plan-registry.md")
    assert probe.unregistered() == ["orphan-notes.md"]


def test_ghost_entry_is_flagged(probe: Probe) -> None:
    """**B 方向**：编目有、文件不在 ⇒ 判红（读者按编目跳过去是空处）。"""
    probe.catalog("| **DT-01** | `data-source-comparison.md` | L2 | 数据源 | 四源实测对比 |")
    assert probe.ghosts() == ["data-source-comparison.md"]


def test_placeholder_is_not_an_entry(probe: Probe) -> None:
    """占位名（`YYYY-MM-DD.md` / `overview-*.md`）不是真引用，不得判幽灵。"""
    probe.catalog(
        "| **DR-01** | `daily-review/` | L1 | 存档 | 逐日复盘报告（`YYYY-MM-DD.md`） |"
    )
    probe.write(f"{probe.d}/daily-review/2026-09-14.md")
    assert probe.ghosts() == []


# ------------------------------------------------------------------ 保险丝

def test_missing_catalog_fails_loud(probe: Probe) -> None:
    """**守卫的守卫**：编目表本体缺失 ⇒ **判红**，不得静默跳过。

    静默跳过的症状是"全绿"，而全绿会被读成"编目没问题"——
    守卫覆盖面失效比误报危险得多（[[KB-ENG-72]]）。
    """
    assert probe.unregistered() == ["docs/INDEX.md（编目表本体缺失）"]


def test_empty_catalog_fails_loud(probe: Probe) -> None:
    """章节在、但解析不出任何条目 ⇒ 同样判红（多半是章节标题被改了）。"""
    probe.write(f"{probe.d}/INDEX.md", "# INDEX\n\n## 0.0 其它章节\n\n无表格\n")
    assert probe.unregistered() == ["docs/INDEX.md §0.0 编目表解析为空"]
