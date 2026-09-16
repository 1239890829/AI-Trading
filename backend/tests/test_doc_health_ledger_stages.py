"""`scripts/doc-health.py` 的「Q 档位一致性」检查器自证测试（2026-09-16，`GOV-015`）。

## 为什么需要它：账本的失真方式与守卫同形

Q 项抓的是「**已闭环的任务仍留在未完成档**」。它不改变任何代码行为——只让**下一个执行者**
按账本自己的排序规则（状态档 → 优先级 → 编号）领到一个**早已做完**的任务，翻完代码才发现无事可做。

实测来源（2026-09-16，接手第一轮）：`BUG-014` 的两处迁移修复与两条守卫都在 `80c6428` 交付，
`§6.0-H` 也标了 `✅ 闭环`，**唯独 A 档那行没销账**（行文还是「**修法**：…」的待办语气）。
因它是 A 档 P1 里**编号最小**的一项 ⇒ 必然被最先领到。

这类"账本说自己还有活儿"的失真**读不出来**：行文完全正常、甚至像一条标准待办。
只能机械判 ⇒ 按项目纪律把注入验证固化成常驻测试（同 J / I / K / N / O 各项的做法）。

## 本文件钉住的分界点

1. **双向都判**：方向一「谎报可做」+ 方向二「闭环记录不完整」。只判方向一会漏掉
   「A 档删干净了、F 档忘了加」这种半成品销账——而那正是本次的真身形态（两向同时命中）；
2. **`🟡 部分闭环` 不是闭环**：部分闭环按规则**应留在原档**（如 `RSH-026`）⇒ 一并判红会逼人做假账；
3. **划销是豁免不是违规**：首列含 `~~` 是在案留痕（B 档已有 3 例）；
4. **`§6.0-H` 索引表必须收边界**：它紧跟 F 档、形态同为 `| ID | … |`，不收边界会把索引里
   每个 ID 都当成"闭环记录"——首版即如此，会把 `RSH-026` 误判为已闭环（见下 `test_...index...`）；
5. **保险丝要真响**：账本缺失 / A 档标题改名 / `§6.0-H` 缺失 / **闭环记录总数为 0** ⇒ 全部判红。
   最后一条最要紧：闭环集合为空时"未完成档未命中"是**恒真**的，本项会静默变摆设。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "doc-health.py"

_A_TITLE = "**A 可做（无外部阻塞，现在就能开工）**"
_F_TITLE = "**F ✅ 本轮已闭环（移出 A 档；号码保留、永不复用）**"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("doc_health_ledger_probe", SCRIPT)
    assert spec is not None and spec.loader is not None, f"加载失败：{SCRIPT}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ledger(
    open_rows: str = "",
    closed_rows: str = "",
    index_rows: str = "",
    *,
    a_title: str = _A_TITLE,
    with_index: bool = True,
) -> str:
    """拼一份**最小但形状真实**的账本 §6.0（档标题与表格列头都照抄真账本）。"""
    index = (
        "#### 6.0-H 交接索引（任务 ⇄ `docs/handoff.md` 明细条目）\n\n"
        "| 任务 ID | 状态 | 日期 | 明细条目 |\n|---|---|---|---|\n" + index_rows
        if with_index else ""
    )
    return (
        "### 6.0 ⭐ 任务登记总表（全仓唯一任务清单）\n\n"
        f"{a_title}\n\n"
        "| ID | 任务 | 优先级 | 前置 / 注意 | 出处 | 旧代号 |\n|---|---|---|---|---|---|\n"
        f"{open_rows}\n"
        f"{_F_TITLE}\n\n"
        "| ID | 任务 | 闭环结论与证据 | 出处 | 旧代号 |\n|---|---|---|---|---|\n"
        f"{closed_rows}\n"
        f"{index}\n"
        "### 6.1 P0 — 立即做（无外部阻塞）\n"
    )


def _row(tid: str, *rest: str, struck: bool = False) -> str:
    first = f"~~**{tid}**~~" if struck else f"**{tid}**"
    cells = "".join(f" {c} |" for c in rest)
    return f"| {first} |{cells}\n"


class Probe:
    """把 Q 项的扫描面指向临时仓库，避免自证用例污染真实账本。"""

    def __init__(self, mod: ModuleType, docs: Path) -> None:
        self.mod = mod
        self.docs = docs

    def write_ledger(self, text: str) -> None:
        (self.docs / "retro-and-gaps.md").write_text(text, encoding="utf-8")

    def check(self) -> tuple[list[tuple[str, str]], str | None]:
        return self.mod.check_ledger_stage_consistency()

    def conflicts(self) -> list[tuple[str, str]]:
        return self.check()[0]


@pytest.fixture()
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Probe:
    mod = _load()
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "DOCS", docs)
    return Probe(mod, docs)


# ---------------------------------------------------------------- 方向一：谎报可做

def test_closed_item_left_in_open_stage_is_flagged(probe: Probe) -> None:
    """**本项的核心命中**：F 档已登记闭环，可它还在 A 档按「在列」计数。"""
    probe.write_ledger(_ledger(
        open_rows=_row("BUG-014", "迁移建表连接错误", "P1", "", "旧", "—"),
        closed_rows=_row("BUG-014", "同上", "✅ 已闭环（2026-09-16）", "", "—"),
    ))
    bad = probe.conflicts()
    assert len(bad) == 1 and bad[0][0] == "BUG-014", bad
    assert "A 档" in bad[0][1] and "谎报可做" in bad[0][1], bad[0][1]


def test_unclosed_item_in_open_stage_is_not_flagged(probe: Probe) -> None:
    """反向对照：真未完成的任务必须放过（否则守卫会变成噪声而被关掉）。"""
    probe.write_ledger(_ledger(
        open_rows=_row("RSH-027", "场景化 KB 路由", "P1", "", "旧", "—"),
        closed_rows=_row("GOV-014", "交接机制", "✅ 已闭环", "", "—"),
    ))
    assert probe.conflicts() == []


def test_struck_row_is_exempt(probe: Probe) -> None:
    """**划销 = 在案留痕，不是违规**：B 档已有 3 例（`BUG-002` / `BUG-003` / `BUG-007`）。"""
    probe.write_ledger(_ledger(
        open_rows=_row("BUG-002", "ntile 无 tie-break", "P0", "", "旧", "—", struck=True),
        closed_rows=_row("BUG-002", "同上", "✅ 已闭环", "", "—"),
    ))
    assert probe.conflicts() == []


def test_partial_closure_stays_in_open_stage(probe: Probe) -> None:
    """`🟡 部分闭环` **不是闭环**：按规则应留在原档，判红会逼人做假账。"""
    probe.write_ledger(_ledger(
        open_rows=_row("RSH-026", "个股机会学习闭环", "P1", "", "旧", "—"),
        closed_rows=_row("GOV-014", "交接机制", "✅ 已闭环", "", "—"),
        index_rows="| `RSH-026` | 🟡 部分闭环 | 2026-09-16 | `§RSH-026` |\n",
    ))
    assert probe.conflicts() == []


# ---------------------------------------------------------------- 方向二：闭环记录不完整

def test_index_marked_closed_but_missing_from_closed_stage_is_flagged(probe: Probe) -> None:
    """**本次真身的第二半**：`§6.0-H` 标了 ✅，F 闭环登记档却没有这行。

    只判方向一会漏掉它——那种情形下 A 档已删干净、看起来"销账完成"，
    但读者从唯一入口（`§6.0-H`）跳去 F 档会**扑空**。
    """
    probe.write_ledger(_ledger(
        index_rows="| `BUG-014` | ✅ 闭环 | 2026-09-16 | `§BUG-014` |\n",
    ))
    bad = probe.conflicts()
    assert len(bad) == 1 and bad[0][0] == "BUG-014", bad
    assert "F 闭环登记档无该行" in bad[0][1], bad[0][1]


def test_half_finished_writeoff_hits_both_directions(probe: Probe) -> None:
    """**实测形态的完整复刻**：既留在 A 档、又没进 F 档 ⇒ 两条判据同时命中。"""
    probe.write_ledger(_ledger(
        open_rows=_row("BUG-014", "迁移建表连接错误", "P1", "", "旧", "—"),
        index_rows="| `BUG-014` | ✅ 闭环 | 2026-09-16 | `§BUG-014` |\n",
    ))
    bad = probe.conflicts()
    assert [t for t, _ in bad] == ["BUG-014", "BUG-014"], bad
    assert "谎报可做" in bad[0][1] and "不完整" in bad[1][1], bad


def test_index_rows_are_not_mistaken_for_closed_rows(probe: Probe) -> None:
    """**实现期抓出的真缺陷（回归钉）**：`§6.0-H` 索引表紧跟 F 档、形态同为 `| ID | … |`。

    首版没在 `§6.0-H` 标题处**收扫描边界** ⇒ 索引行被当成"F 档行的延续"、
    索引里**每个 ID 都变成闭环记录** ⇒ `RSH-026`（H 里是 `🟡 部分闭环`、账本里本项仍开放）
    被误判成已闭环并报红。本用例同时摆一个**真闭环**（`GOV-014`）保证闭环集合非空，
    从而把"索引行混入"与"闭环集合为空的保险丝"两件事分开。
    """
    probe.write_ledger(_ledger(
        open_rows=_row("RSH-026", "个股机会学习闭环", "P1", "", "旧", "—"),
        closed_rows=_row("GOV-014", "交接机制", "✅ 已闭环", "", "—"),
        index_rows=(
            "| `GOV-014` | ✅ 闭环 | 2026-09-16 | `§GOV-014` |\n"
            "| `RSH-026` | 🟡 部分闭环 | 2026-09-16 | `§RSH-026` |\n"
        ),
    ))
    assert probe.conflicts() == []


# ---------------------------------------------------------------- 保险丝：判定面不可用要判红

def test_missing_ledger_file_is_fail_loud(probe: Probe) -> None:
    bad, sentinel = probe.check()
    assert bad == [] and sentinel and "不存在" in sentinel, sentinel


def test_renamed_open_stage_title_is_fail_loud(probe: Probe) -> None:
    """A 档标题**形态**被改 ⇒ 判定面没了。**必须判红，不得当成"无冲突"**。

    ⚠️ 判据锚定的是 `**A `（`A` 后带空格的档标题形态），不是"A 可做"这四个字：
    ⚠️ 本用例第一版把标题写成 `**A 可做（措辞改动…）**` —— 那**仍然匹配**，于是用例
    自己假红。**"改了措辞"与"改了形态"要分开**：前者是正常编辑、后者才断守卫。
    """
    probe.write_ledger(_ledger(
        closed_rows=_row("GOV-014", "交接机制", "✅ 已闭环", "", "—"),
        a_title="**A. 可做（分隔符被改，档标题形态失效）**",
    ))
    bad, sentinel = probe.check()
    assert bad == [] and sentinel and "A 可做档标题" in sentinel, sentinel


def test_missing_handoff_index_section_is_fail_loud(probe: Probe) -> None:
    probe.write_ledger(_ledger(
        closed_rows=_row("GOV-014", "交接机制", "✅ 已闭环", "", "—"),
        with_index=False,
    ))
    bad, sentinel = probe.check()
    assert bad == [] and sentinel and "§6.0-H" in sentinel, sentinel


def test_empty_closed_set_is_fail_loud(probe: Probe) -> None:
    """**最隐蔽的失守形态**：闭环记录为 0 时"未完成档未命中"恒真 ⇒ 本项会静默变摆设。"""
    probe.write_ledger(_ledger(
        open_rows=_row("RSH-027", "场景化 KB 路由", "P1", "", "旧", "—"),
    ))
    bad, sentinel = probe.check()
    assert bad == [] and sentinel and "合计为 0" in sentinel, sentinel


# ---------------------------------------------------------------- 真实仓库必须干净

def test_real_ledger_has_no_stage_conflict() -> None:
    """钉住「仓库当前干净」。若这条与 `doc-health` 同时变红，说明账本真出现了档位冲突。

    这是**唯一**对真实仓库取证的用例（其余全部走临时账本）：它有意与 `doc-health`
    同源，因为它们要回答的是同一个问题——**现在这份账本到底自洽不自洽**。
    """
    mod = _load()
    bad, sentinel = mod.check_ledger_stage_consistency()
    assert sentinel is None, f"真实账本的判定面不可用：{sentinel}"
    assert bad == [], f"真实账本存在档位冲突：{bad}"
