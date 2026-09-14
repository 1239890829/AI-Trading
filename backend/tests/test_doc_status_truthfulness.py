"""文档**能力描述真实性**守卫（`GOV-002` / 报告 O4，2026-09-14）。

## 为什么需要它：`doc-health` 查不出这一类失真

`doc-health` 的 A–N 各项查的是**结构与引用**（未登记 / 死链 / 超长 / 摘要前置 / 锚点 / 表格 /
任务指针…）。它们全都**不会**因为下面这句话而变红：

    「**P1/P2 未实施（保留待办）**：题材徽标 / watcher / …」

而实测（2026-09-14）这两个项**早已闭环**（`P1-5`/`P1-4`/`P1-1`）。同理
`docs/summary/architecture-design.md` §4 的 4 项"待办"实为 `P0-2`~`P0-5` **全部 ✅**；
`docs/summary/stock-strategy.md` 正文写「未落地项：止盈 tracker」，**同一文件的文末**
却写着「~~止盈 tracker~~ ✅ 已完成」——**同文档自相矛盾**。

⇒ 这就是报告 O4 说的「说明文字重复承诺，行为守卫未覆盖同一语义」：
**文档写得再错，所有门禁都是绿的**。危害不是"文档不好看"，而是**诱发重复开发**
（照文档去做一件已经做完的事），以及在验收时把已闭环项当成欠债重新排期。

## 两条判据（互补，缺一不可）

| # | 判据 | 防什么 |
|---|---|---|
| 1 | **定点回归**：已实测确认的失真表述不得再出现 | 这 4 类具体写法复发 |
| 2 | **结构约束**：`docs/summary/*.md` 里出现**状态词**时，该文件必须同时含指向**账本 §6.0** 的指针 | 新增"没出处的状态断言"——状态只能有一个权威出口 |

判据 2 是本条守卫的**通用面**：它不枚举具体句子，而是要求"凡谈状态，必指账本"。
这与用户 2026-09-14 的指令一致（「都要进入账本……不要在单个文档里有各自要执行的任务」）。

## 为什么不用"状态词一律禁止"

因为 summary 层**合法地**需要谈状态（"已落地"本身也是状态），把它一刀禁掉只会逼出
更含糊的措辞。要求"谈状态就必须带权威指针"，才是既可执行又不误伤的口径。

## 判据自身的边界（诚实声明）

- **只扫 `docs/summary/`**：这一层是"设计方案的结论汇总"，最容易被当成现状读。
  其他层（`kb/` / `archive/`）天然带历史语义，不在本判据扫描面内。
- **判据 2 是"存在性"检查**（文件里有没有 §6.0 指针），**不校验指针是否语义正确**——
  它挡不住"指针写了但状态仍错"的情形，那一层靠判据 1 的定点回归与人工复核。
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUMMARY = REPO / "docs" / "summary"

#: 状态词：出现它们就意味着这份文档在**对完成度下断言**。
STATUS_WORDS = ("未实施", "未做", "未落地", "待办", "⏳", "尚未")

#: 权威出口（唯一任务清单）的指针形态。任一变体命中即算有出处。
LEDGER_POINTERS = ("账本 §6.0", "§6.0", "retro-and-gaps.md §6.0")

#: **记录性引用豁免**（与 `doc-health` N 项同惯例）：改正失真时**必须能引原话做溯源**
#: （否则证据链断掉，"当初写错成什么样"就查不到了）。故当禁用片段出现在**含这些标记的行**上，
#: 视为"记录/订正"而非"仍在主张"。
#:
#: ⚠️ 这条豁免**不能放宽到整段**：句级豁免会让"同一段里既贴原话又照抄一遍"蒙混过关。
#: 判据按**行**判——原话与订正写在**同一行**才算记录。
RECORD_MARKERS = ("原写", "曾写", "旧表述", "已改正", "已按实测改正", "原文记", "~~")


def _line_of(text: str, needle: str) -> str | None:
    """返回 `needle` 所在的那一行（未命中返回 None）。"""
    for line in text.splitlines():
        if needle in line:
            return line
    return None


def scan_forbidden(text: str, path_rel: str) -> list[str]:
    """判据 1（**纯函数**）：返回该文本里命中的**仍在主张**的禁用表述。

    带记录标记的行（"原写『…』" / 删除线）豁免——那是溯源，不是主张。
    """
    out: list[str] = []
    for p, frag in FORBIDDEN_STRINGS:
        if p != path_rel:
            continue
        line = _line_of(text, frag)
        if line is None:
            continue
        if any(m in line for m in RECORD_MARKERS):
            continue
        out.append(frag)
    return out

#: ── 判据 1：定点回归 ─────────────────────────────────────────────
#: 已实测确认「已闭环却被写成待办」的表述（2026-09-14 `GOV-002`）。
#: 每项 = (相对路径, 不得再出现的片段)。**加项时须附实测证据**，不要凭感觉加。
FORBIDDEN_STRINGS: tuple[tuple[str, str], ...] = (
    # §2 原写"P1/P2 未实施（保留待办）"，而题材徽标 / watcher 当时已闭环
    ("docs/summary/architecture-design.md", "P1/P2 未实施"),
    # §2 把"watcher"列进未实施清单（实为 `P1-1` ✅）
    ("docs/summary/architecture-design.md", "未实施（保留待办）"),
    # §4 的 4 项"待办"实为 `P0-2`~`P0-5` 全部 ✅
    ("docs/summary/architecture-design.md", "待办（⏳ 保留）"),
    # stock-strategy 正文断言"未落地"，而同文件文末已标 ✅
    ("docs/summary/stock-strategy.md", "未落地项：止盈 tracker"),
    ("docs/summary/stock-strategy.md", "本文档为方案，未写代码"),
    # 头部把未做项指向 §6.2/6.3（唯一清单是 §6.0）
    ("docs/summary/architecture-design.md", "§6.2/6.3"),
)


def scan_missing_ledger_pointer(text: str) -> list[str]:
    """判据 2（**纯函数**）：谈状态却没有指向账本 §6.0 ⇒ 违规。

    返回命中的状态词（空列表 = 通过）。**不误伤**：一份完全不谈状态的文档直接通过。
    """
    if not any(w in text for w in STATUS_WORDS):
        return []
    if any(p in text for p in LEDGER_POINTERS):
        return []
    return [w for w in STATUS_WORDS if w in text]


def _summary_files() -> list[Path]:
    return sorted(p for p in SUMMARY.glob("*.md"))


# ---------------------------------------------------------------- 纯函数自证

def test_scan_forbidden_detects_and_ignores():
    """判据 1 能变红、也不误报（[[KB-ENG-65]]：判据必须能变红）。"""
    text = "**P1/P2 未实施（保留待办）**：题材徽标 / watcher。"
    hit = scan_forbidden(text, "docs/summary/architecture-design.md")
    assert "P1/P2 未实施" in hit and "未实施（保留待办）" in hit
    # 同样文本出现在**别的**文件里不算违规（定点判据绑定文件）。
    # ⚠️ 这里必须用**真实存在**的路径：doc-health 的 F 项（代码注释死引用）扫 `tests/`，
    # 写一个不存在的假路径会直接把门禁判红（2026-09-14 实测踩到）。
    assert scan_forbidden(text, "docs/summary/ai-evolution.md") == []


def test_scan_pointer_requires_status_word():
    """判据 2：**不谈状态的文档直接通过**——不制造无谓约束。"""
    assert scan_missing_ledger_pointer("本模块负责 X 与 Y 的职责划分。") == []


def test_scan_pointer_flags_status_without_ledger():
    """判据 2 能变红：谈状态但没有权威指针 ⇒ 报出命中词。"""
    assert scan_missing_ledger_pointer("剩余 3 项未实施。") == ["未实施"]


def test_scan_pointer_accepts_any_pointer_variant():
    """三种指针变体都合法（避免把合法写法判成违规）。"""
    for variant in LEDGER_POINTERS:
        assert scan_missing_ledger_pointer(f"1 项未实施 → {variant}") == []


# ---------------------------------------------------------------- 真实仓库锚定

def test_summary_files_exist():
    """扫描面非空（防"目录改名后判据静默变成空扫"——守卫失效比误报更危险）。"""
    files = _summary_files()
    assert files, f"docs/summary/ 下没有 .md：{SUMMARY}"
    assert len(files) >= 6, f"summary 文档数异常偏少（{len(files)}）"


def test_no_forbidden_status_claims_in_summary():
    """**判据 1 对真实仓库**：已确认的失真表述不得复发。"""
    offenders: list[str] = []
    for path in _summary_files():
        rel = path.relative_to(REPO).as_posix()
        offenders += [f"{rel}: 「{frag}」" for frag in scan_forbidden(
            path.read_text(encoding="utf-8"), rel)]
    assert not offenders, "summary 文档又出现「已闭环却写成待办」的表述：\n" + "\n".join(offenders)


def test_summary_status_claims_point_to_ledger():
    """**判据 2 对真实仓库**：凡谈状态，必须指向账本 §6.0。"""
    offenders: list[str] = []
    for path in _summary_files():
        text = path.read_text(encoding="utf-8")
        missing = scan_missing_ledger_pointer(text)
        if missing:
            offenders.append(f"{path.relative_to(REPO).as_posix()}: 命中 {missing} 但无 §6.0 指针")
    assert not offenders, "summary 文档含状态断言却没有指向唯一任务清单：\n" + "\n".join(offenders)
