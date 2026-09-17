"""`scripts/doc-health.py` 的「N 索引体积与指针」检查器自证测试（2026-09-14）。

## 为什么单独立一个测试文件

N 项服务的对象不是 `docs/` 正文，而是**索引自身**（`.workbuddy/memory/MEMORY.md`
与它的展开版 `docs/INDEX.md` §0）——它是 2026-09-14「记忆只作索引器」改造的**机制承载**。
规则若只写在 `kb/07` §4.4 里就是摆设（§9 自我淘汰条款），所以固化为机检 + 本测试。

N 项**自身失效的方式**恰好与它要防的缺陷同形（都是"看起来是绿的"）：

1. **体积上限失守** ⇒ 索引重新长回内容库 ⇒ 触发平台配额 ⇒ 关键约束被截断，
   而 `doc-health` 其余各项**全绿**（内容写得再长也不违反任何一条既有检查）；
2. **指针闭包盲区** ⇒ 被引用文件搬家/改名后，索引里的路径**静默指向空处**，
   而 B 项只认 `docs/**.md` 形态，**对 `.workbuddy/`、仓库根文件、代码路径一条都扫不到**。

⇒ 按项目纪律把**注入验证固化成常驻测试**（同 K/I/J 项的做法）：只手动注入一次的话，
下次有人改判据时没人会再注入。

## 本文件主要钉住的分界点

| # | 分界点 | 钉子 |
|---|---|---|
| 1 | 体积上限**只约束入口** `MEMORY.md`，不约束 `kb/12`（后者是 L3 文档，长度归 `kb/07` §7） | `test_cap_applies_to_entry_only` |
| 2 | **裸文件名不算指针**（无 `/` ⇒ 不可唯一定位）；首版收裸名 ⇒ 7 处里 4 处是假阳性 | `test_bare_filename_is_not_a_pointer` |
| 3 | **`docs/` 前缀不由本项管**（B 项已覆盖；两处都管 = 两套口径，必漂移） | `test_docs_prefix_is_left_to_check_b` |
| 4 | 索引文件**缺失 ⇒ 判红**，而不是"没得查就跳过"（守卫静默失效比误报危险） | `test_missing_index_file_fails_loud` |
| 5 | **记录性引用豁免**（`已删除 xx.py` 是本仓通行的在案引述写法） | `test_record_marker_is_exempt` |
| 6 | 正向对照：合法索引**不得误报**（防"一律报红"的假守卫） | `test_clean_index_passes` |

⚠️ **路径可解的双基准**（实现细节，但会决定误报率）：索引里写相对 `docs/` 的简写
（`kb/08-tooling-pitfalls.md`）与仓库相对写法（`docs/kb/08-tooling-pitfalls.md`）**都合法**，
故判据同时尝试 `ROOT/` 与 `DOCS/` 两个基准——只认一种会把另一种全判成误报。
"""
from __future__ import annotations

import importlib.util
import subprocess
import types
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "doc-health.py"

#: 临时索引面：与 `INDEX_FILES` 同形，但落在 tmp_path 下（绝不碰真实仓库）。
ENTRY = "AGENTS.md"
ROUTER = "docs/INDEX.md"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("doc_health_memory_probe", SCRIPT)
    assert spec is not None and spec.loader is not None, f"加载失败：{SCRIPT}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Probe:
    """把 N 项的扫描面指向临时仓库，避免测试污染真实索引。"""

    def __init__(self, mod: ModuleType, root: Path) -> None:
        self.mod = mod
        self.root = root

    def write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel == ENTRY:
            text = self.mod.ENTRY_START + "\n" + text + "\n" + self.mod.ENTRY_END
        p.write_text(text, encoding="utf-8")

    def touch(self, rel: str) -> None:
        """造一个"真实存在"的被引用文件。"""
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n", encoding="utf-8")

    def oversized(self) -> list[tuple[str, int]]:
        return self.mod.check_memory_index()[0]

    def dead(self) -> list[tuple[str, int, str]]:
        return self.mod.check_memory_index()[1]

    def dead_tokens(self) -> list[str]:
        return [tok for _, _, tok in self.dead()]


@pytest.fixture()
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Probe:
    mod = _load()
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "DOCS", docs)
    monkeypatch.setattr(mod, "INDEX_FILES", (ENTRY, ROUTER))
    monkeypatch.setattr(mod, "INDEX_CHAR_CAP_FILE", ENTRY)
    p = Probe(mod, tmp_path)
    # 默认先造出**两个索引文件本体**：否则「索引缺失即判红」这条保险丝会先于其他判据命中，
    # 每个用例都被它污染（实测：首版 fixture 漏建 ROUTER ⇒ 8 例全红，而报错原因各不相同）。
    p.write(ENTRY, "# 入口索引\n")
    p.write(ROUTER, "# 主题路由\n")
    return p


# --------------------------------------------------------------------- 体积上限


def test_clean_index_passes(probe: Probe) -> None:
    """正向对照：干净的小索引 + 合法指针 ⇒ 两项都不报（防"一律报红"的假守卫）。"""
    probe.touch(".workbuddy/tools/md-report-html.py")
    probe.write(ENTRY, "| 我要… | 去 |\n|---|---|\n| 跑门禁 | `.workbuddy/tools/md-report-html.py` |\n")
    probe.write(ROUTER, "# 12\n\n见 `.workbuddy/tools/md-report-html.py`。\n")
    assert probe.oversized() == []
    assert probe.dead() == []


def test_entry_over_cap_is_flagged(probe: Probe) -> None:
    """入口索引超出字符上限 ⇒ 命中，且带上实际字数（便于判断"多了多少"）。"""
    probe.write(ENTRY, "内" * (probe.mod.INDEX_CHAR_CAP + 1))
    hits = probe.oversized()
    assert len(hits) == 1
    assert hits[0][0] == ENTRY
    assert hits[0][1] == probe.mod.INDEX_CHAR_CAP + 1


def test_entry_at_cap_boundary_passes(probe: Probe) -> None:
    """**边界钉**：恰好等于上限**不报**（判据是 `>`，不是 `>=`）。

    边界写错会让"刚好合规"的索引恒红——而红满天的门禁会被整体无视（KB-ENG-58）。
    """
    probe.write(ENTRY, "内" * probe.mod.INDEX_CHAR_CAP)
    assert probe.oversized() == []


def test_cap_applies_to_entry_only(probe: Probe) -> None:
    """**判据分界点**：体积上限**只管入口**；`kb/12` 是 L3 文档，长度归 `kb/07` §7。

    若这条变红，说明有人把上限套到了展开版索引上 ⇒ 展开版被逼着"不敢写动线"，
    等于把本项从"防内容混入"变成"防索引有用"（与本项动机相反）。
    """
    probe.write(ROUTER, "内" * (probe.mod.INDEX_CHAR_CAP + 500))
    assert probe.oversized() == [], "展开版索引不该受入口的体积上限约束"


# ------------------------------------------------------------------ 指针闭包


def test_dead_pointer_is_flagged(probe: Probe) -> None:
    """**注入验证**：指向不存在的代码路径 ⇒ 命中（这正是 B 项扫不到的那类）。"""
    probe.write(ENTRY, "见 `.workbuddy/tools/does-not-exist.py`。\n")
    assert probe.dead_tokens() == [".workbuddy/tools/does-not-exist.py"]


def test_dead_dir_pointer_is_flagged(probe: Probe) -> None:
    """目录式引用（以 `/` 结尾）按**目录**判存在性，不是按文件。"""
    probe.write(ENTRY, "见 `.workbuddy/skills/no-such-skill/`。\n")
    assert probe.dead_tokens() == [".workbuddy/skills/no-such-skill/"]


def test_bare_filename_is_not_a_pointer(probe: Probe) -> None:
    """**判据分界点**：裸文件名（无 `/`）**不收**。

    首版收了裸名，实测 7 处命中里 4 处是**"指代"而非"指针"**（索引正文自称 `MEMORY.md`、
    上文已给全路径后行文用简称）⇒ 纯假阳性。裸名本就不具备"可唯一定位"的性质。
    若这条变红，说明判据又退回了"把指代当指针"，误报会把 N 项淹掉。
    """
    probe.write(ENTRY, "本文件是 `MEMORY.md`，渲染用 `md-html-parity.py`。\n")
    assert probe.dead() == []


def test_docs_prefix_is_left_to_check_b(probe: Probe) -> None:
    """**判据分界点**：`docs/` 前缀的指针由 **B 项**负责，N 项不重复要求。

    两处都管 = 两套口径（一处放宽一处收紧时必漂移，KB-ENG-26 同族）。
    """
    probe.write(ENTRY, "见 `docs/kb/definitely-missing.md`。\n")
    assert probe.dead() == [], "docs/ 前缀应留给 B 项，N 项不重复要求"


def test_docs_relative_short_form_resolves(probe: Probe) -> None:
    """**双基准钉**：`kb/08-tooling-pitfalls.md`（相对 docs 的简写）**应当可解**。

    只认 `ROOT/` 单一基准会把这类合法简写全判成误报 ⇒ 本钉子防止"误报倒逼改判据"。
    """
    (probe.root / "docs" / "kb").mkdir(parents=True, exist_ok=True)
    (probe.root / "docs" / "kb" / "08-tooling-pitfalls.md").write_text("x\n", encoding="utf-8")
    probe.write(ENTRY, "坑见 `kb/08-tooling-pitfalls.md`。\n")
    assert probe.dead() == []


def test_record_marker_is_exempt(probe: Probe) -> None:
    """**豁免钉**：带记录性标记的行是"在案引述"，不算断链（与 B 项同口径）。"""
    probe.write(ENTRY, "`scripts/old-tool.py` 已删除（精华并入 kb/07）。\n")
    assert probe.dead() == []


def test_placeholder_path_is_exempt(probe: Probe) -> None:
    """模板占位名（`YYYY-MM-DD.md`）不是真引用。"""
    probe.write(ENTRY, "日志写 `.workbuddy/memory/YYYY-MM-DD.md`。\n")
    assert probe.dead() == []


def test_missing_index_file_fails_loud(probe: Probe) -> None:
    """**守卫的守卫**：索引文件缺失 ⇒ **判红**，不得静默跳过。

    静默跳过的症状是"全绿"，而全绿会被读成"索引没问题"——
    守卫覆盖面失效比误报危险得多（KB-ENG-72）。
    """
    (probe.root / ENTRY).unlink()
    assert ENTRY in [tok for _, _, tok in probe.dead()]


# ------------------------------------------------- B / N 分工交接（GOV-009，2026-09-14）

def test_scan_files_cover_the_index_entry() -> None:
    """**交接前提钉（GOV-009）**：索引入口 `MEMORY.md` 必须在 **B 项的扫描面**内。

    分工是「**N 判非 docs 前缀、B 判 docs 前缀**」，两半**都不完整**——
    索引里 `docs/…` 形态的指针**只有 B 能看见**（N 刻意跳过 docs/，见
    `test_docs_prefix_is_left_to_check_b`）。若哪天有人把 `MEMORY.md` 从
    `SCAN_FILES` 挪走（"它只是索引、不是文档"是很自然的误判），**N 全绿、B 不再扫它**
    ⇒ 索引里的 docs 死链**没有任何一项检查会报**，而门禁依旧全绿。

    这正是 [[KB-ENG-72]] 的典型形态：**A 假定 B 覆盖，而 B 的覆盖面由第三处的常量决定**，
    任一侧都不会自己变红。2026-09-14 双向注入已确认交接**当前成立**，缺的只是这颗钉子。
    """
    mod = _load()
    assert "AGENTS.md" in mod.SCAN_FILES, (
        "索引入口已不在 B 项扫描面（SCAN_FILES）内 ⇒ 索引里的 `docs/**.md` 死链"
        "将无人判（N 跳过 docs/，B 不扫该文件）。分工交接断裂，请恢复该条目。"
    )
    # 顺带钉住另一半：`docs/INDEX.md` 同属 B 面（它是 MEMORY.md 的展开版，同为索引）。
    assert "docs/INDEX.md" in mod.SCAN_FILES


def test_n_does_not_judge_docs_prefix_statically() -> None:
    """**分工边界的结构钉**：N 的判定面**在结构上**就不含 `docs/`。

    `test_docs_prefix_is_left_to_check_b` 是**行为**钉（改判据会红）；本例补**结构**钉：
    直接断言常量面不含 `docs/`。行为钉防"改了判据"，结构钉防"改了配置"——
    若有人把 `docs/` 加进 `INDEX_DIR_PREFIXES`，行为钉仍绿（那个用例写的是**不存在**的
    docs 路径，加 `docs/` 只会让它**变红**……除非同时被 `RECORD_MARKERS` 之类豁免），
    而"两套口径"这种漂移**最常发生在常量层**，故两颗钉子都要有。
    """
    mod = _load()
    assert "docs/" not in mod.INDEX_DIR_PREFIXES, (
        "`docs/` 进入 N 的目录前缀 ⇒ N 与 B 对同一形态的指针各判一次，"
        "两套口径必漂移（KB-ENG-26 同族）。docs/ 前缀归 B，见 §6.0 GOV-009。"
    )
    # 反向也钉：B 的指针正则只认 docs/ 形态 ⇒ `.workbuddy/` 指针确实由 N 独占。
    assert mod.REF_RE.search(".workbuddy/memory/MEMORY.md") is None
    assert mod.REF_RE.search("docs/kb/07-doc-curation.md") is not None


# --------------------------- 判定面 = CI 检出内容（2026-09-16，PR #19 事故）
#
# 背景：`.workbuddy/` 是 gitignored 的工作区目录，`_in_checkout_universe` 原本靠
# 「顶层段在 + 父目录在」这套**两级结构代理**把它整棵跳过。2026-09-16 `PR #19`
# 往 `.workbuddy/skills/` 强提交了 2 个技能文件 ⇒ `.workbuddy` **首次进入 `tops`**
# ⇒ 门① 放行；而门② 对**目录形态**取的是**父目录**（`.workbuddy`，它正好在）⇒ 也放行
# ⇒ `N 6 处 + O 4 条`**只在 CI 红**（本机这 4 个目录都在，故本地恒绿）。
#
# ⚠️ 上面那批用例**全部跑在 `tmp_path` 合成树**里：那里 `_tracked_paths()` 拿不到 git
# ⇒ `tops is None` ⇒ 直接 `return True` ⇒ 走的是**文件系统回退路径**。也就是说
# 「跟踪清单路径」此前**一颗钉子都没有**——缺陷恰好长在没人钉的那半边。
# ⇒ 本节用**构造的 tops/dirs** 直测判定函数，把它补上。

#: 构造的判定面：`data` / `.workbuddy` / `scripts` 在检出里；
#: `.workbuddy` 下**只有 `skills/`**（正是 PR #19 强提交 2 个技能文件后的形状）。
_TOPS = {"data", ".workbuddy", "scripts"}
_DIRS = {"data", ".workbuddy", ".workbuddy/skills", "scripts"}


def _judge(mod: ModuleType, monkeypatch: pytest.MonkeyPatch,
           tok: str, ignored: set[str]) -> bool:
    """直测 `_in_checkout_universe`：`_is_gitignored` 换桩，隔离本仓 `.gitignore` 的当下取值。

    用例要表达的是**规则**（"ignored ⇒ 不判"），不是"本仓此刻恰好如此"——
    后者由本文件末尾的端到端用例负责。
    """
    monkeypatch.setattr(mod, "_is_gitignored", lambda rel, is_dir: rel in ignored)
    return mod._in_checkout_universe(tok, _TOPS, _DIRS)


def test_dir_pointer_anchor_is_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """**根因钉**：目录形态（尾斜杠）的锚点是**它自己**，不是父目录。

    `.workbuddy/memory/` 的父目录 `.workbuddy` 在检出里，但 `.workbuddy/memory`
    不在（gitignored）⇒ 必须**不判**。改回"取父目录"这条立刻红。
    """
    mod = _load()
    assert _judge(mod, monkeypatch, ".workbuddy/memory/",
                  {".workbuddy/memory"}) is False, (
        "目录形态取父目录 ⇒ `.workbuddy/**` 的任意子路径都蒙混过关"
        "（PR #19 的 CI 红正是这么来的）"
    )
    assert _judge(mod, monkeypatch, ".workbuddy/skills/",
                  {".workbuddy/memory"}) is True, (
        "`.workbuddy/skills` 真在检出里 ⇒ 照判；不得因它是 `.workbuddy` 下就一刀切跳过"
    )


def test_absent_dir_is_still_judged_when_not_ignored(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """**防撤守卫钉**（本节重点）：第三级不得放宽成"凡缺席即不判"。

    `scripts/no-such-dir/` 既不在检出里、也**不是** gitignored ⇒ 它是**真问题**
    （写错的目录名 / 已删的目录）⇒ 必须**照判**。
    只把②改成"目录形态取自身"、而**不补**"能不能进检出"这一级的实现，本条会红
    ——那是把"修判据"做成了"撤守卫"（KB-ENG-65）。
    """
    mod = _load()
    assert _judge(mod, monkeypatch, "scripts/no-such-dir/", set()) is True, (
        "非 gitignored 的缺席目录被静默跳过 ⇒ 目录名写错将无人发现"
    )


def test_file_pointer_anchor_is_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    """**回归钉**：文件形态仍按**父目录**判（2026-09-15 起就是对的，不得被顺手改掉）。"""
    mod = _load()
    assert _judge(mod, monkeypatch, ".workbuddy/skills/ashare-ledger-continue/SKILL.md",
                  set()) is True, "父目录在检出里 ⇒ 照判"
    assert _judge(mod, monkeypatch, ".workbuddy/memory/MEMORY.md",
                  {".workbuddy/memory/MEMORY.md"}) is False, "gitignored 的索引本体 ⇒ 不判"


def test_gitignored_asks_dir_form_with_trailing_slash(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """**尾斜杠钉**（2026-09-16 实测）：目录形态必须**带 `/`** 去问 git。

    实测 `git check-ignore data/picks/` 命中，而 `data/picks`（无斜杠）**不命中**
    ——`.gitignore` 的 `data/` 规则覆盖其下的文件，**不含目录节点自身**。
    少一个 `/` 就答反，而答反的方向恰好是"本该跳过的变成判红"。
    """
    mod = _load()
    seen: list[list[str]] = []

    class _Done:
        returncode = 0

    def fake(cmd: list[str], **kw: object) -> object:
        seen.append(cmd)
        return _Done()

    monkeypatch.setattr(mod, "subprocess",
                        types.SimpleNamespace(run=fake,
                                              SubprocessError=subprocess.SubprocessError))
    mod._is_gitignored("data/picks", True)
    mod._is_gitignored("data/picks", False)
    assert seen[0][-1] == "data/picks/", "目录形态必须带尾斜杠去问"
    assert seen[1][-1] == "data/picks", "文件形态不得凭空多出尾斜杠"


def test_gitignored_fails_open_when_git_unavailable(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """**fail-open 钉**：拿不准（无 git / 命令失败 / 超时）⇒ **照判**，不得静默跳过。

    方向不能反：跳过 = 守卫覆盖面静默失效（KB-ENG-72），误报至少有人看得见。
    """
    mod = _load()

    def boom(cmd: list[str], **kw: object) -> object:
        raise OSError("git 不可用")

    monkeypatch.setattr(mod, "subprocess",
                        types.SimpleNamespace(run=boom,
                                              SubprocessError=subprocess.SubprocessError))
    assert mod._is_gitignored(".workbuddy/memory", True) is False


def test_real_repo_gitignored_workbuddy_dirs_are_skipped() -> None:
    """**端到端钉（真实仓库 + 真实 `.gitignore`）**：`.workbuddy/` 下的目录指针不得被判。

    前面几条用构造面钉**规则**，本条钉**本仓当下的前提**：`.workbuddy/` 确实在
    `.gitignore` 里、其子目录进不了检出。前提被改（有人把 `.workbuddy/` 移出
    `.gitignore`，或把判定面换回文件系统口径）⇒ 本条立刻红。
    **代理成立的前提变了必须有人喊**——这正是 PR #19 事故的教训：
    前提的判定面由第三处的常量（`.gitignore` + 谁被强提交）决定，
    而代理自己不会因此变红。
    """
    mod = _load()
    tops, dirs = mod._tracked_tops(), mod._tracked_dirs()
    assert tops is not None and dirs is not None, "本用例要求在 git 仓库内运行"
    skipped = (".workbuddy/memory/", ".workbuddy/artifacts/",
               ".workbuddy/reports/", ".workbuddy/trash/")
    for tok in skipped:
        assert mod._in_checkout_universe(tok, tops, dirs) is False, (
            f"{tok} 被判成「检出里可能有」⇒ 该目录只存在于本机 ⇒ CI 上必然红"
        )
    # 反向：真在检出里的目录照判（不得为了修前面这条把整棵 `.workbuddy` 一刀切跳过）。
    assert mod._in_checkout_universe("skills/", tops, dirs) is True, (
        "`skills/` 下有已跟踪的技能文件 ⇒ 它在检出里 ⇒ 必须照判"
    )


@pytest.mark.parametrize("form", ["missing", "duplicate", "reversed", "empty"])
def test_project_entry_markers_fail_loud(probe: Probe, form: str) -> None:
    start, end = probe.mod.ENTRY_START, probe.mod.ENTRY_END
    content = {"missing": "No entry", "duplicate": start + start + "body" + end,
               "reversed": end + "body" + start, "empty": start + "\n\n" + end}[form]
    (probe.root / ENTRY).write_text(content)
    assert probe.dead()


def test_entry_cap_does_not_constrain_the_rest_of_agents(probe: Probe) -> None:
    probe.write(ENTRY, "See `scripts/check.py`")
    probe.touch("scripts/check.py")
    with (probe.root / ENTRY).open("a") as stream:
        stream.write("\n" + "正文" * probe.mod.INDEX_CHAR_CAP)
    assert probe.oversized() == [] and probe.dead() == []
