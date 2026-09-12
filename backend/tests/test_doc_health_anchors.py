"""`scripts/doc-health.py` 的「J 文档代码锚点」检查器自证测试（2026-09-12，F-10）。

## 为什么单独立一个测试文件

J 项是本轮新加的检查：**文档点名的仓库代码路径必须存在**。它来自 F-10
（§6.12「设计文档能力失真」的**可判子集**）——本轮先量了三个原型才定档，
**两个宽口径实测否决**（见 `check_doc_anchors` docstring 与 KB-ENG-68）：

- 「整句能力断言」不可机械配对：断言的宾语常是**中文子能力**（「禁止交易名单」），
  而可核验的只有它**所在的文件** ⇒ 语义配对不上，不是调参能解决的；
- 于是退到**判据零歧义**的那一小块：点名的代码路径还在不在。

它上线时实测 **4 处命中 / 0 误报**，且 4 处全是真漂移（`PROJECT-MASTER.md` 架构树里
列着 `screener.py` / `screener_service.py` / `minute_backtest.py` / `predict.py`
四个**已删模块**——死于模块被删，树却没人改）。

与 I 项同理：检查器**自身失效的方式与它要抓的缺陷同形**（都表现为"看起来是绿的"），
所以按项目纪律把**注入验证固化成常驻测试**。

## 本文件主要钉住的分界点

1. **树锚点只在围栏内认**——正文里的 `├── x.py` 是插画/引用，不是结构声明；
2. **"不参与判定" ≠ "通过"**——HTTP 路径、外部域名、非仓库首段一律放过
   （它们不是"文档对仓库代码的点名"）；
3. **只认源码/配置后缀**：`json/jsonl/parquet/duckdb` 是 gitignored 运行时产物，
   「本地在、CI 不在」，纳入会让门禁在 CI 上假红（KB-ENG-57 同族）；
4. **记录性引用不判**：含「已删除 / 迁出 / 计划」等状态词的行是在案引述，不是失真。
   （范本 v1 的 47/66 条命中全部来自账本的"曾未做 → 已完成"叙述 ⇒ 对 changelog
   做存在性对账属**范畴错误**。）
5. **记录性排除是「行级」不是「文件级」**：`PROJECT-MASTER.md` 是**混合体裁**文档
   ——既有权威结构树（必须判），也有「近期路线」changelog 段（不该判）。原先按**文件**
   实现的口径在它身上失效，故补 `~~` 删除线标记（`~~**X**~~ ✅ 已完成（日期）`）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "doc-health.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("doc_health_anchors_probe", SCRIPT)
    assert spec is not None and spec.loader is not None, f"加载失败：{SCRIPT}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Probe:
    """把检查器的扫描面指向临时仓库，避免测试污染真实文档。"""

    def __init__(self, mod: ModuleType, root: Path, docs: Path) -> None:
        self.mod = mod
        self.root = root
        self.docs = docs

    def write(self, rel: str, text: str) -> None:
        p = self.docs / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def touch(self, rel: str) -> None:
        """在**仓库根**建一个真实文件（供 basename 解析命中）。"""
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")

    def check(self) -> tuple[list[tuple[str, int, str, str]], list[str]]:
        return self.mod.check_doc_anchors()

    def anchors(self) -> list[str]:
        return [a for _, _, a, _ in self.check()[0]]


@pytest.fixture()
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Probe:
    mod = _load()
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "DOCS", docs)
    monkeypatch.setattr(mod, "ANCHOR_ALLOW", {})
    return Probe(mod, tmp_path, docs)


# ---------------------------------------------------------------- 基本命中/通过

def test_inline_anchor_to_missing_file_is_flagged(probe: Probe) -> None:
    probe.write("a.md", "编排在 `app/services/picks_pipeline.py` 里。\n")
    assert probe.anchors() == ["app/services/picks_pipeline.py"]


def test_inline_anchor_to_existing_file_passes(probe: Probe) -> None:
    probe.touch("backend/app/market/breadth.py")
    probe.write("a.md", "宽度在 `market/breadth.py` 里。\n")
    assert probe.anchors() == []


def test_bare_filename_resolves_by_basename(probe: Probe) -> None:
    """文档常写简写（`breadth.py`）⇒ 按全仓 basename 解析。"""
    probe.touch("backend/app/market/breadth.py")
    probe.write("a.md", "见 `breadth.py`。\n")
    assert probe.anchors() == []


# ---------------------------------------------------------------- 分界点 1：树锚点只在围栏内

def test_tree_line_inside_fence_is_checked(probe: Probe) -> None:
    """**J 项的首个真阳性形态**：架构树里列着已删模块。

    实例：`docs/PROJECT-MASTER.md` 的 `services/` 段列 `screener_service.py`，
    而选股器 09-01 已彻底删除——那一行**没有反引号**，只靠行内锚点抓不到。
    """
    probe.write("a.md", "```text\n│   ├── services/\n│   │   ├── gone_service.py  # 已消失\n```\n")
    assert probe.anchors() == ["gone_service.py"]


def test_tree_line_outside_fence_is_not_checked(probe: Probe) -> None:
    """**判据分界点**：正文里的 `├── x.py` 是插画/引用，不得当结构声明去判。"""
    probe.write("a.md", "举个树的样子：├── not_a_real_module.py  ← 只是说明格式\n")
    assert probe.anchors() == []


def test_fenced_block_closes_scope(probe: Probe) -> None:
    """围栏闭合后，后续正文行不得再按树行解析。"""
    probe.touch("ok.py")
    probe.write("a.md", "```text\nok.py\n```\n\n├── after_fence.py\n")
    assert probe.anchors() == []


# ---------------------------------------------------------------- 分界点 2：不参与判定的三类

def test_http_path_and_external_domain_are_not_judged(probe: Probe) -> None:
    """`/tmp/x.sh`（HTTP/外部路径）与外部站点**不是**"对仓库代码的点名"。

    ⚠️ 放过 ≠ 通过，而是**不参与判定**——否则 `/openapi.json`、
    `d.10jqka.com.cn/v6/line/....js` 这类会让门禁长期挂红（KB-ENG-58）。
    """
    probe.write("a.md", "见 `/tmp/start_backend_8000.sh` 与 `d.10jqka.com.cn/v6/x.js`。\n")
    assert probe.anchors() == []


def test_non_repo_head_segment_is_not_judged(probe: Probe) -> None:
    """首段不在仓库根目录白名单里的含 `/` 锚点 = 外部路径或文档自造简写。"""
    probe.write("a.md", "见 `kb/NN-x.md`，另见 `some_vendor_dir/x.py`。\n")
    assert probe.anchors() == []


def test_data_artifact_suffix_is_not_judged(probe: Probe) -> None:
    """**只认源码/配置后缀**：数据产物是 gitignored 运行时文件。

    实例：`backend/data/market.duckdb`、`data/*.parquet` 本地在、CI 不在
    ⇒ 纳入会让门禁在 CI 上假红（[[KB-ENG-57]]）。
    """
    probe.write("a.md", "见 `market.duckdb` 与 `snapshots/2026.parquet` 与 `x.jsonl`。\n")
    assert probe.anchors() == []


# ---------------------------------------------------------------- 分界点 3：记录性引用与占位名

def test_record_marker_line_is_skipped(probe: Probe) -> None:
    """含状态词的行是**在案引述**（"曾存在 / 计划中"），不是失真。"""
    probe.write("a.md", "老的 `app/services/screener_service.py` 已删除，改用 picks 管线。\n")
    probe.write("b.md", "第 7 项 `app/picks/theme_core.py`（计划中，尚未创建）。\n")
    assert probe.anchors() == []


def test_struck_through_changelog_line_is_skipped(probe: Probe) -> None:
    """**行级口径**：`~~` 删除线 = 已完成的 changelog 条目，与账本同属"不对账"范畴。

    真实实例：`docs/PROJECT-MASTER.md:371` 的
    `~~**Phase 5 选股器 + 评分系统**~~ ✅ 已完成（2026-08-30）：… + app/services/screener_service.py`
    ——该模块 09-01 已被彻底删除，但这行说的是"**那天**建了什么"，历史为真，
    不该让门禁变红（文件级排除覆盖不到 `PROJECT-MASTER.md`，它是混合体裁）。
    """
    probe.write(
        "a.md",
        "9. ~~**Phase 5 选股器**~~ ✅ 已完成（2026-08-30）：`app/services/screener_service.py`。\n",
    )
    assert probe.anchors() == []


def test_struck_through_marker_does_not_leak_to_next_line(probe: Probe) -> None:
    """删除线标记**只作用本行**——下一行的结构声明照判（防"一处放宽、整段免检"）。"""
    probe.write("a.md", "~~旧的 `gone_a.py`~~\n现在结构：`app/market/gone_b.py`\n")
    assert probe.anchors() == ["app/market/gone_b.py"]


def test_placeholder_names_are_skipped(probe: Probe) -> None:
    """占位/示例名不是真引用。"""
    probe.write("a.md", "模板见 `docs/xxx.md`、`foo.py`、`tmp/a.py`。\n")
    assert probe.anchors() == []


# ---------------------------------------------------------------- 分界点 4：扫描面口径

def test_ledger_and_daily_review_are_out_of_scope(probe: Probe) -> None:
    """**口径**：账本是变更叙述 + 计划登记，逐日复盘是 L4 历史快照。

    对 changelog 做存在性对账属**范畴错误**——范本 v1 的 47/66 条命中全部来自账本
    的"曾未做 → 已完成"叙述。同名裸锚另有 F3/F4 覆盖。
    """
    probe.write("retro-and-gaps.md", "| 7 | `app/picks/theme_core.py` |\n")
    probe.write("daily-review/d.md", "见 `app/old_thing.py`。\n")
    probe.write("archive/a.md", "见 `app/very_old.py`。\n")
    assert probe.anchors() == []


# ---------------------------------------------------------------- 容忍名单 + ghost 反向断言

def test_allowlisted_anchor_is_suppressed_and_not_ghost(probe: Probe) -> None:
    probe.write("a.md", "见 `vendor_lib.py`（外部工具副本，非本仓源码）。\n")
    probe.mod.ANCHOR_ALLOW[("docs/a.md", "vendor_lib.py")] = "外部工具路径"
    hits, ghost = probe.check()
    assert hits == []
    assert ghost == []


def test_allowlist_turns_into_ghost_when_anchor_changes(probe: Probe) -> None:
    """锚点被改写 ⇒ 容忍项键对不上 ⇒ **必须报出来**。

    没有这条反向断言，容忍名单会像 `CLAIM_EXEMPT` 那样静默腐烂。
    """
    probe.write("a.md", "见 `vendor_other.py`（外部工具副本）。\n")
    probe.mod.ANCHOR_ALLOW[("docs/a.md", "vendor_lib.py")] = "外部工具路径"
    hits, ghost = probe.check()
    assert [a for _, _, a, _ in hits] == ["vendor_other.py"]
    assert len(ghost) == 1 and "vendor_lib.py" in ghost[0]


# ---------------------------------------------------------------- 真实仓库

def test_real_repo_has_no_dead_doc_anchor() -> None:
    """在**真实仓库**上跑一遍。

    一箭双雕：①证明检查器在真实扫描面（含大目录遍历）上可运行；
    ②把"仓库当前干净"钉住——若这条与 `doc-health` 同时变红，说明新出现了
    文档点名的死路径（改文档或登记 `ANCHOR_ALLOW`），这是**期望的双红**。
    """
    hits, ghost = _load().check_doc_anchors()
    assert hits == [], f"文档点名了不存在的代码路径（改文档或登记 ANCHOR_ALLOW）：{hits}"
    assert ghost == [], f"容忍项已失效（锚点改了/文件搬了，须删该条）：{ghost}"
