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
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "doc-health.py"

#: 临时索引面：与 `INDEX_FILES` 同形，但落在 tmp_path 下（绝不碰真实仓库）。
ENTRY = "index/MEMORY.md"
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
    assert ".workbuddy/memory/MEMORY.md" in mod.SCAN_FILES, (
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
