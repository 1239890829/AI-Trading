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
import subprocess
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

    实例：`docs/archive/PROJECT-MASTER.md` 的 `services/` 段列 `screener_service.py`，
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

    真实实例：`docs/archive/PROJECT-MASTER.md:371` 的
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

    ⚠️ **这条守卫曾在本地是瞎的**（2026-09-12）：判定面用的是 `os.walk`，会把
    `artifacts/trash/` 里的**回收站副本**算作"文件存在"⇒ 本地恒绿、CI 变红
    （实测遮盖 `docs/kb/00-INDEX.md:192 → run_review.sh`）。判定面改为 git 跟踪
    清单后本条才真正具备"本地能看见 CI 所见"的能力（见 KB-ENG-70）。
    """
    hits, ghost = _load().check_doc_anchors()
    assert hits == [], f"文档点名了不存在的代码路径（改文档或登记 ANCHOR_ALLOW）：{hits}"
    assert ghost == [], f"容忍项已失效（锚点改了/文件搬了，须删该条）：{ghost}"


# ---------------------------------------------------------------- 分界点 5：判定面 = git 跟踪清单


def test_anchor_face_equals_git_tracked_basenames() -> None:
    """**判定面必须等于 CI 的检出内容**（git 跟踪清单），不是本地文件系统。

    门禁问的是「文档点名的代码路径**在仓库里**还在不在」，而 CI 检出的只有跟踪文件。
    两者不相等 ⇒ 本地被未跟踪文件（回收站副本、数据产物）**系统性喂绿**，
    且"本地绿 / CI 红"会常态复现。本仓实测量级：`os.walk` 10409 个 basename
    vs `git ls-files` 999 个。
    """
    mod = _load()
    if mod._tracked_basenames() is None:
        pytest.skip("非 git 检出（走 os.walk 回退路径），本断言不适用")
    raw = subprocess.run(
        ["git", "ls-files", "-z"], cwd=mod.ROOT, capture_output=True, check=True)
    expected = {Path(p).name for p in raw.stdout.decode("utf-8").split("\0") if p}
    assert mod._repo_basenames() == expected


def test_untracked_local_copy_cannot_mask_dead_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**回收站副本不得"救活"死引用**——两种口径对照，缺陷与修复各跑一遍。

    与本仓删除纪律直接冲突的场景：tracked/独有文件处置进入 `artifacts/trash/`，
    该目录 gitignored 但 `os.walk` 照走 ⇒ **越守纪律，门禁越假绿**。
    """
    mod = _load()
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "DOCS", docs)
    monkeypatch.setattr(mod, "ANCHOR_ALLOW", {})
    (docs / "a.md").write_text("见 `run_review.sh`。\n", encoding="utf-8")
    trash = tmp_path / "artifacts" / "trash" / "2026-09-12" / "scripts"
    trash.mkdir(parents=True)
    (trash / "run_review.sh").write_text("", encoding="utf-8")   # 本地有、但未跟踪

    # ① 旧口径（os.walk 全盘扫描）：被回收站副本遮盖 ⇒ **假绿**（这就是缺陷本身）
    monkeypatch.setattr(mod, "_tracked_basenames", lambda: None)
    assert mod.check_doc_anchors()[0] == [], "旧口径下应复现假绿，否则本测试失去了对照意义"

    # ② 新口径（git 跟踪清单）：仓库里没有这个文件 ⇒ 暴露真漂移
    monkeypatch.setattr(mod, "_tracked_basenames", lambda: {"unrelated.py"})
    hits, _ = mod.check_doc_anchors()
    assert [a for _, _, a, _ in hits] == ["run_review.sh"]


# ---------------------------------------------------------------- 结论行必须由实际结果派生


_NEUTRAL = {
    "check_unregistered": (),
    "check_dead_links": (),
    "check_over_limit": (),
    "check_kb_entries": (),
    "kb_file_coverage_gap": (),
    "check_missing_abstract": (),
    "check_clusters": (),
    "check_code_refs": ((), ()),        # 第二项是**已登记例外列表**，不是布尔
    "check_legacy_slugs": ((), ()),
    "check_kb_pointer_files": (),
    "check_kb_orphans": (),
    "check_stale_anchors": (),
    "check_claim_entries_missing_falsifier": (),
    "check_claim_exempt_ids": (),
    "check_empty_sections": ((), ()),
    # 2026-09-14 GOV-010：K / L / M / N 四项是 J 之后新增的，原先**漏在中性表外**
    # ⇒ 它们对**真实仓库**跑，任何并发文档写入都会让本文件的结论行用例假红
    # （当日 15:10 全量门禁 2 红，成因即另一会话正在改 docs/，非代码回归）。
    "check_table_delimiters": (),
    "check_task_ids_defined": (),
    "check_task_carrier_pointers": (),
    "check_memory_index": ((), ()),
    "kb_file_advisories": (),
    "check_catalog_closure": ((), ()),
    "check_docs_taxonomy": ((), ()),
    # 2026-09-16 `GOV-014`：P 项（交接索引）新增时**本守卫再次真命中**（第二次），
    # 报红并点名 `check_handoff_index` —— 正是 GOV-010 加这颗 AST 反查钉的用途。
    # ⚠️ 返回值形状按签名给：第 4 位是**保险丝说明**（`str | None`），中性值必须为 `None`；
    # 若误写成 `""`，`bool("") is False` 会让「保险丝生效」与「中性打桩」变得无法区分，
    # 以后改判据时这条用例就再也测不出东西了（[[KB-ENG-98]]：先问"这一层有没有被行使"）。
    "check_phase_index": (),
    # 2026-09-16 `GOV-015`：Q 项（账本档位一致性）新增时本守卫**第三次真命中**并点名。
    # ⚠️ 返回形状 `(冲突列表, 保险丝说明)`：中性值必须是 `((), None)` ——
    # 保险丝写成 `""` 会让 `bool("") is False` 使"保险丝未触发"与"中性打桩"无法区分。
    "check_phase_tasks": (),
    # 2026-09-19 v9.7：S 项（阶段门治理）加入 main 后必须同步纳入 hermetic 中性表。
    "check_stage_gates": (),
    # 2026-09-19 v9.4：R 项（重大决策传播）加入 main 后必须同步纳入 hermetic 中性表；
    # 否则结论行测试会偷偷读取真实仓库，正是本测试要阻止的覆盖漂移。
    "check_decision_propagation": (),
}


def _checks_called_by_main(mod: ModuleType) -> set[str]:
    """用 AST 读出 `main()` 实际调用了哪些检查函数（不执行它）。

    用途：钉住「中性表必须覆盖 main 的全部检查」。手写枚举是**会腐化的清单**——
    main 每加一项检查，未登记项就会悄悄回到真实仓库上跑（[[KB-ENG-72]] 同族：
    清单覆盖了什么 ≠ 真实覆盖面），而这正是本文件结论行用例非 hermetic 的成因。
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(mod.main)))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and (n.func.id.startswith("check_") or n.func.id.startswith("kb_"))
    }
    # 只收「确实是模块级函数」的名字：`kb_over` / `kb_big` 这类**局部变量**同名混入
    # 会让守卫自己假红（守卫假红比漏报更易被当成噪声关掉）。
    return {n for n in called if callable(getattr(mod, n, None))}


def _stub_all_but_anchors(mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """把 J 以外的检查全部打成"空结果"，以隔离结论行本身。

    ⚠️ 覆盖度即正确性（GOV-010）：只打桩**已知**的检查等于把「新加的检查」
    留给真实仓库 ⇒ 隔离是假的。故打桩后立即用 AST 反查 main 的调用面，
    遗漏即报红并点名，避免下次新增检查时重复踩同一个坑。
    """
    for name, ret in _NEUTRAL.items():
        monkeypatch.setattr(mod, name, lambda *a, _r=ret, **k: _r)

    unstubbed = _checks_called_by_main(mod) - set(_NEUTRAL) - {"check_doc_anchors"}
    assert not unstubbed, (
        f"main() 调用了未打桩的检查 {sorted(unstubbed)}——它们会对**真实仓库**执行，"
        "使结论行用例再次变成非 hermetic（并发改 docs/ 即假红）。"
        "请在 _NEUTRAL 补上它们的中性返回值（返回值形状照该函数签名给）。"
    )


def _conclusion(stdout: str) -> str:
    lines = [ln for ln in stdout.splitlines() if ln.startswith("结论：")]
    assert len(lines) == 1, f"结论行应恰好 1 条，实得 {lines}"
    return lines[0]


def test_conclusion_line_names_the_failing_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """**汇总行必须由实际结果派生**——不得把常驻局限写成失败项的原因。

    回归点：结论行原先无条件追加「（C 的日志单轮 ≤80 行属过程指标…）」，而那句是
    **常驻局限**、与本次失败项无关 ⇒ J 项变红时会读成"C 项待处理"。
    实测已导致排查方向跑偏（2026-09-12 CI：J 红，据结论行去查 C）。
    """
    mod = _load()
    _stub_all_but_anchors(mod, monkeypatch)
    monkeypatch.setattr(mod, "check_doc_anchors", lambda: ([("docs/a.md", 1, "gone.py", "正文")], []))

    rc = mod.main()
    concl = _conclusion(capsys.readouterr().out)

    assert rc == 1
    assert "1 项待处理" in concl
    assert "J 文档代码锚点" in concl, f"结论行必须点名失败的检查项，实得：{concl}"
    assert "C 的日志单轮" not in concl, f"常驻局限不得冒充失败原因，实得：{concl}"


def test_conclusion_line_on_clean_run_says_all_passed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """全绿时结论行只能是"全部通过"，同样不得挂任何单项描述。"""
    mod = _load()
    _stub_all_but_anchors(mod, monkeypatch)
    monkeypatch.setattr(mod, "check_doc_anchors", lambda: ([], []))

    assert mod.main() == 0
    concl = _conclusion(capsys.readouterr().out)

    assert concl == "结论：全部通过", f"实得：{concl}"


# ---------------------------------------------------------------- 豁免治理（§6.5b #8）

def test_anchor_allow_entries_carry_reason_and_stay_pinned() -> None:
    """**豁免治理钉子**（§6.5b #8，2026-09-13 裁定）：J 项豁免必须带理由，且数量钉死。

    现有 2 条均属「叙述已处置物」（KB-ENG-62 拆除记录的处置留痕与成因复述）——
    是**预期残留**，不是待清理项；但豁免本质是检查器的**声明式盲区**，增长必须
    是有意识的决策：第 3 条出现时本用例变红，要求先评估是否应改走
    「记录性标记」（行内状态词，见 ANCHOR_RECORD_MARKERS），而不是悄悄加豁免。
    """
    mod = _load()
    entries = mod.ANCHOR_ALLOW
    assert len(entries) == 2, (
        f"ANCHOR_ALLOW 出现第 {len(entries)} 条豁免——豁免是检查器的声明式盲区，"
        "增长必须过评审：先评估能否改走「记录性标记」（ANCHOR_RECORD_MARKERS 行内状态词），"
        "确需豁免时请同步更新本钉子并在账本 §6.5b #8 留痕。"
    )
    for key, reason in entries.items():
        assert isinstance(key, tuple) and len(key) == 2, f"豁免键必须是 (文件, 锚点)：{key!r}"
        assert reason and len(reason) >= 12, f"豁免 {key} 缺少有信息量的理由：{reason!r}"
