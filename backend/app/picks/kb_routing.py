r"""场景化知识库路由（蓝图 §5，`RSH-027` 切片 1）。

## 缺口

蓝图 §5（`docs/summary/system-final-blueprint.md`）要求把知识库升级为
「**按场景检索指定 KB → 记录所用条目 → 量化其增益**」的完整闭环，并写明
「每次决策快照记录 `scenario`、`kb_ids`、引用状态、支持/冲突依据、特征版本和 `as_of`」。

蓝图同一节也**自述了现状**：「当前选股运行时**没有**……完整闭环。因此它**有用但尚未证明
能提高选股结果**」。本模块据此划定切片边界：

- 本切片交付**路由契约 + 引用记录 + 引用校验 + 覆盖度自证**。它**不做检索本身**
  （检索 = 按册取正文，由调用方做），也**不做消融**——有/无 KB 的影子消融属切片 2，
  与 `RSH-026` 剩余部分同因（**须等样本积累**，当前可成交样本远低于
  `opportunity_learning.MIN_LABELS_FOR_VERDICT`，硬跑 verdict 就是「用不足样本装判据」）。
- 故本切片**不把 KB 接进任何决策**：`enters_scoring` 全部为 `False`，且由
  `assert_scoring_admission_is_evidence_gated()` 机制化——**没有消融证据就不许改真**
  （蓝图：「没有稳定增益时，KB 只保留解释/治理作用，不强行入模」）。

## 四场景 ⇄ 蓝图 §5 表格逐行对应

蓝图 §5 表格的「可调用知识」列**不是**统一的册清单，逐行读法不同（这点踩过坑，
第一版把它一律当册过滤，与原文不符）：

| 本模块 `key` | 蓝图场景 | 蓝图「可调用知识」原文 | 机制化判据 |
|---|---|---|---|
| `pre_open_event` | 盘前/事件映射 | `KB-STOCK`、**已验证的** `KB-TRADE` | `KB-STOCK` 不限状态；`KB-TRADE` **限 ✅** |
| `intraday_pick` | 盘中候选/买点 | **已落地或试验中的**交易纪律 | `KB-STOCK`/`KB-TRADE` **限 ✅/🔶**（状态过滤） |
| `post_close_review` | 盘后复盘 | `KB-STOCK`、`KB-TRADE`、复盘框架 | 两册不限状态 + `review-framework` |
| `system_evolution` | 系统进化 | `KB-DEC`、`KB-ENG`、治理册 | 两册不限状态 + `governance` |

**注意两处易错**：

1. 盘中的约束是**状态**（「已落地或试验中」），不是「只准 KB-TRADE」——
   `KB-STOCK-07`（嗅到≠买入）、`KB-STOCK-11/12/13/21` 都是 `✅` 交易纪律，
   按册过滤会把它们一并挡掉。
2. 盘前允许 `KB-STOCK` **含 `📎` 示例**（KB-STOCK-01~04 正是产业链扩散的参考输入）——
   「示例条目不得当硬规则」禁的是**当判据**，不是**不许读**。故 `📎` 由
   `validate_kb_citations` 的**身份**判据（状态档）而非「从允许面排除」来拦。

## 两条禁止事项为什么必须**机制化**

蓝图的两条禁令此前**只是文字**，全仓无任何代码承载 ⇒ 与 [[KB-ENG-72]]
（「文档承诺 ≠ 实际覆盖」）同族。本模块把它们落成**可断言**的判据：

1. **示例条目不得当硬规则**：`📎` 状态即「示例/参考输入」身份，纪律出自
   [[KB-DEC-018]] / [[KB-DEC-019]]（**不得当判定标准**）⇒ `is_hard_rule_source()`
   对示例身份一律否决，并给出驳回理由。
2. **`KB-DEC` / `KB-ENG` 不进入个股收益打分**：`SCORING_BOOKS` 是**个股收益打分**唯一
   允许的册集合，与 `system_evolution` 的允许册**交集为空**——该恒等式由守卫逐项断言。

## KB 索引的唯一解析实现（**别处不得再写第二份**）

`load_kb_index()` 是 `docs/kb/00-INDEX.md` 索引表的**唯一**解析器，
`app/services/evolution.py` 的议程第八路复用本函数。

**为什么值得单独立**：实测（2026-09-16，`RSH-027`）发现议程第八路原来的私有正则
`^\| (KB-...-\d+) \| (.+?) \| ([✅🔶⏳❌]) \| (\d{4}-\d{2}-\d{2}) \|` **静默漏掉
16 / 178 条**（实测口径：旧正则匹配 162，本解析器 178）。两处成因：

1. 它在状态列后要求紧跟 ` | `，而实际状态列**允许多词备注**
   （`✅ 已测否` / `⏳ 方法论已沉淀` / `✅ 一期已落地`）⇒ 漏 12 条：
   `KB-STOCK-27` `KB-STOCK-29` `KB-STOCK-30` `KB-STOCK-31` `KB-STOCK-32`
   `KB-STOCK-33` `KB-STOCK-34` `KB-STOCK-35` `KB-STOCK-36` `KB-DEC-003`
   `KB-ENG-79` `KB-ENG-82`——**恰是最经过实证的一批**（KB-STOCK-27/29~33 都带实测结论）。
2. 它不认 `📎`，且册前缀写死四册 ⇒ 漏 4 条示例（`KB-STOCK-01`~`04`），
   并连带漏掉册级行 `KB-REPO-*`（第五个册族 `05-repo-tracker.md`）。

后果：议程的 `⏳` 候选池与 `by_status` 统计**系统性偏低**，
且**没有任何断言会发现**（与 [[KB-ENG-97]]「增量是没跑的用例」同族：
**过滤面 ≠ 表格面，且静默**）。

⇒ 因此除了放宽状态列与册前缀，本解析器**把「漏检」变成可核验的恒等式**：

```text
candidate_rows == entries(total) + book_level_rows + unparsed_rows
```

**判据面必须等于表格面**——这是那条缺陷的正面修法，不只是改宽正则。
`index_overview()` 把这三个数一起暴露到读侧，守卫断言恒等式成立且 `unparsed_rows` 为空。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

#: 仓库根（`backend/app/picks/kb_routing.py` → `parents[3]`）。
#: 与 `app/services/evolution.py:64` 同口径；`docs/**` 属 git 跟踪面 ⇒ CI 检出里也在
#: （[[KB-ENG-95]]：判定面必须等于 CI 检出内容，故**不得**指向 `data/`、`.workbuddy/`）。
PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: 知识库总索引（状态表与⏳候选池的唯一真相源）。
KB_INDEX_PATH = PROJECT_ROOT / "docs" / "kb" / "00-INDEX.md"

#: 知识库**真实存在的册族**（= `docs/kb/` 下有对应文件、索引表里有条目的前缀）。
#: 前四个是蓝图 §5 点名的那四册；`KB-REPO` 是第五个（`05-repo-tracker.md`
#: 「agent 分组 10 仓台账」），蓝图未点名但**索引表里确有其条** ⇒ 必须登记，
#: 否则引用它会被误报成「不是合法的 KB 条目 ID」（理由错，应报「未授权册」）。
#: 两者之差由守卫 `test_kb_index_books_are_all_registered` 拦住。
KB_BOOKS: tuple[str, ...] = ("KB-STOCK", "KB-TRADE", "KB-DEC", "KB-ENG", "KB-REPO")

#: 「复盘框架」与「治理册」不是 `KB-XXX-nn` 编号册，但蓝图 §5 明确列为可调用知识
#: ⇒ 单独登记；`parse_kb_id` **不得**把它们误当成册（它们没有序号）。
NON_BOOK_SOURCES: tuple[str, ...] = ("review-framework", "governance")

#: **个股收益打分**唯一允许的册集合（蓝图 §5：`KB-DEC`/`KB-ENG` 只防重复踩坑与约束
#: 实验和改码，**不进入个股收益打分**）。打分面的唯一真相源，别处不得再写一份。
SCORING_BOOKS: frozenset[str] = frozenset({"KB-STOCK", "KB-TRADE"})

# ---- 知识库 5 档状态（`docs/kb/00-INDEX.md`「状态定义」表逐字）--------------------
#: 已落地：有组件/流程承载。
STATUS_LANDED = "✅"
#: 试验中：影子运行/实验窗口内。
STATUS_TRIAL = "🔶"
#: 待落地：已有设计，未实现或未接线。
STATUS_PENDING = "⏳"
#: 被取代：保留条目防回退。
STATUS_SUPERSEDED = "❌"
#: **示例/参考输入**：题材案例、外部材料等——**不构成规范，不得当判定标准**。
STATUS_EXAMPLE = "📎"

KNOWN_STATUSES: tuple[str, ...] = (
    STATUS_LANDED, STATUS_TRIAL, STATUS_PENDING, STATUS_SUPERSEDED, STATUS_EXAMPLE,
)

#: 索引表里「已落地或试验中」——蓝图 §5 对**盘中**场景的原文限定。
INTRADAY_TRADE_STATUSES: tuple[str, ...] = (STATUS_LANDED, STATUS_TRIAL)

#: 索引表**条目行**：`| <KB-ID> | <一句话> | <状态><可选备注> | <YYYY-MM-DD> |`。
#: 状态列**允许多词备注**（`✅ 已测否`）——这正是旧实现漏检的成因，见模块 docstring。
#: 册前缀写成通用的 `KB-[A-Z]+`（不是把四册写死）：写死会让**新册**的条目
#: 连"候选行"都算不上、静默消失（`KB-REPO-*` 就是这样被漏掉的）。
_INDEX_ROW_RE = re.compile(
    r"^\|\s*(KB-[A-Z]+-\d+)\s*\|(.+?)\|\s*"
    r"([✅🔶⏳❌📎])([^|]*)\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*$"
)

#: 索引表**册级汇总行**：`| KB-REPO-* | <一句话> | <状态> | <日期> |`。
#: 这类行**不是条目**（ID 是通配），但也不能静默丢掉——否则「解析面 = 表格面」不成立。
_INDEX_BOOK_ROW_RE = re.compile(r"^\|\s*(KB-[A-Z]+)-\*\s*\|")

#: 「看起来是索引行」的判据——覆盖度自证的分母（含条目行与册级汇总行）。
_INDEX_ROW_PREFIX_RE = re.compile(r"^\|\s*KB-[A-Z]+-(?:\d+|\*)\s*\|")


class KbRoutingError(ValueError):
    """路由拒绝（未知场景 / 未授权册 / 状态不允许 / 示例充当硬规则 / 越权入模）。"""


# ---------------------------------------------------------------- 4 场景路由表


@dataclass(frozen=True)
class KbAllowRule:
    """一个场景对**某册**的引用许可：该册下**只允许这些状态**的条目被引用。

    `statuses=()` = 不限状态（含 `📎` 示例——「可以读」与「可以当判据」是两件事，
    见模块 docstring 的易错点 2）。
    """

    book: str
    statuses: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioRoute:
    """一个场景的 KB 路由条目（allow-list + 用途 + 禁止事项）。"""

    key: str
    #: 蓝图 §5 表格里的场景名（**逐字**，守卫会与蓝图原文逐行比对）
    blueprint_label: str
    #: 允许调用的册及其状态许可
    allow: tuple[KbAllowRule, ...]
    #: 蓝图 §5「可调用知识」列的**原文**（逐字，供机械比对）
    knowledge_label: str
    #: 允许调用的非册知识面
    extra_sources: tuple[str, ...] = ()
    #: 用途（蓝图 §5 原文）
    purpose: str = ""
    #: 禁止事项（蓝图 §5 原文）
    prohibition: str = ""
    #: 该场景的 KB 引用**是否已获准进入个股收益打分**。
    #: ⚠️ 蓝图 §5：须先由有/无 KB 影子消融证明稳定增益；否则只保留解释/治理作用。
    #: ⇒ 置 `True` 必须同时给 `ablation_evidence`，由 `assert_scoring_admission_is_evidence_gated()` 拦。
    enters_scoring: bool = False
    #: 支持 `enters_scoring=True` 的消融证据（样本数 + 出处）。无证据不得置 True。
    ablation_evidence: str = ""

    @property
    def books(self) -> tuple[str, ...]:
        return tuple(rule.book for rule in self.allow)

    def statuses_for(self, book: str) -> tuple[str, ...]:
        for rule in self.allow:
            if rule.book == book:
                return rule.statuses
        return ()


SCENARIOS: dict[str, ScenarioRoute] = {
    "pre_open_event": ScenarioRoute(
        key="pre_open_event",
        blueprint_label="盘前/事件映射",
        knowledge_label="`KB-STOCK`、已验证的 `KB-TRADE`",
        allow=(
            # `KB-STOCK` 不限状态：产业链扩散的参考输入（KB-STOCK-01~04）本就是 📎，
            # 盘前生成候选时**可以读**，只是不得当硬规则（禁令由身份判据拦）。
            KbAllowRule("KB-STOCK"),
            # 「已验证的 KB-TRADE」⇒ 只准 ✅ 已落地。
            KbAllowRule("KB-TRADE", statuses=(STATUS_LANDED,)),
        ),
        purpose="生成产业链与扩散候选、反例清单",
        prohibition="示例条目不得当硬规则",
    ),
    "intraday_pick": ScenarioRoute(
        key="intraday_pick",
        blueprint_label="盘中候选/买点",
        knowledge_label="已落地或试验中的交易纪律",
        allow=(
            KbAllowRule("KB-STOCK", statuses=INTRADAY_TRADE_STATUSES),
            KbAllowRule("KB-TRADE", statuses=INTRADAY_TRADE_STATUSES),
        ),
        purpose="补充解释、风险与失效条件",
        prohibition="不得越过数据/交易硬门",
        # ⚠️ 刻意保持 False：KB 尚未证明增益，按蓝图「不强行入模」。
        enters_scoring=False,
    ),
    "post_close_review": ScenarioRoute(
        key="post_close_review",
        blueprint_label="盘后复盘",
        knowledge_label="`KB-STOCK`、`KB-TRADE`、复盘框架",
        allow=(
            KbAllowRule("KB-STOCK"),
            KbAllowRule("KB-TRADE"),
        ),
        extra_sources=("review-framework",),
        purpose="归因、发现反例、提出假设",
        prohibition="不得用事后信息改写当时决策",
    ),
    "system_evolution": ScenarioRoute(
        key="system_evolution",
        blueprint_label="系统进化",
        knowledge_label="`KB-DEC`、`KB-ENG`、治理册",
        allow=(
            KbAllowRule("KB-DEC"),
            KbAllowRule("KB-ENG"),
        ),
        extra_sources=("governance",),
        purpose="防重复踩坑、约束实验和改码",
        prohibition="不进入个股收益打分",
        enters_scoring=False,
    ),
}

#: 既有代码里实际出现过的 `scenario` 字面量 → canonical key。
#: **两套命名都认**（`run_id` 哈希一套、落库列一套），**刻意不改既有字面量**——
#: 统一口径应由单独一次改动做，且要先回扫断言。
ALIASES: dict[str, str] = {
    # canonical 自身（幂等）
    "pre_open_event": "pre_open_event",
    "intraday_pick": "intraday_pick",
    "post_close_review": "post_close_review",
    "system_evolution": "system_evolution",
    # `run_id` 哈希口径（`app/picks/opportunity_learning.py`）
    "intraday": "intraday_pick",
    "notification": "intraday_pick",
    "pre_open": "pre_open_event",
    "review": "post_close_review",
    "evolution": "system_evolution",
    # 落库列口径（`app/picks/opportunity_learning.py`）
    "intraday_opportunity": "intraday_pick",
    "buy_point": "intraday_pick",
}

# ---- 快照引用状态三态（**必须可区分**，`BUG-016` 同族教训）------------------------
#: 该阶段**没有**引用任何 KB（= 消融的「无 KB」臂；属设计现状，不是坏了）。
REF_STATE_NOT_CONSULTED = "not_consulted"
#: 至少一条引用通过校验；其余驳回仍记录在 conflict。
REF_STATE_CITED = "cited"
#: 有引用但**全部被驳回**（形如把 `📎` 当硬规则、引了未授权册）——这是**异常**。
REF_STATE_REJECTED = "rejected"
REF_STATES: tuple[str, ...] = (
    REF_STATE_NOT_CONSULTED, REF_STATE_CITED, REF_STATE_REJECTED,
)


# ---------------------------------------------------------------- KB 索引解析（唯一实现）


@dataclass(frozen=True)
class KbEntry:
    """索引表的一行（`title` 已去除首尾空白；`status_note` 为状态列备注）。"""

    id: str
    title: str
    status: str
    status_note: str
    since: str
    book: str


@dataclass
class KbIndex:
    """`docs/kb/00-INDEX.md` 索引表的解析结果（含**覆盖度自证**）。"""

    entries: dict[str, KbEntry] = field(default_factory=dict)
    available: bool = False
    note: str = ""
    #: 「像索引行但未解析成条目」的**条目行**原始文本 —— **判据面 ≠ 表格面**的直接证据。
    #: 守卫断言其为空；非空即说明有新写法被静默漏掉（旧实现正是这样漏了 16 条）。
    unparsed_rows: tuple[str, ...] = ()
    #: 表格里候选索引行的总数（= 条目行 + 册级汇总行 + 未解析行），覆盖度自证的分母。
    candidate_rows: int = 0
    #: **册级汇总行**的册前缀（形如 `KB-REPO-*`，ID 是通配、不是条目）。
    #: 显式登记而非静默跳过——它们同样是"表格面"的一部分。
    book_level_rows: tuple[str, ...] = ()
    #: 索引里出现、但没在 `KB_BOOKS` 登记的册前缀（应为空；非空说明新册未登记）。
    unknown_books: tuple[str, ...] = ()

    def status_of(self, kb_id: str) -> str | None:
        entry = self.entries.get(kb_id)
        return entry.status if entry else None

    def note_of(self, kb_id: str) -> str:
        entry = self.entries.get(kb_id)
        return entry.status_note if entry else ""

    def by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries.values():
            counts[entry.status] = counts.get(entry.status, 0) + 1
        return counts

    def ids_by_status(self, status: str) -> list[str]:
        return sorted(e.id for e in self.entries.values() if e.status == status)

    @property
    def total(self) -> int:
        return len(self.entries)

    def coverage_identity_holds(self) -> bool:
        """覆盖度恒等式：`candidate_rows == total + 册级行 + 未解析行`。

        这条恒等式是本模块对「静默漏检」的**正面修法**——只要表格里出现
        既非条目、又非册级行、又解析不出的新写法，恒等式立刻不成立。
        """
        return self.candidate_rows == self.total + len(self.book_level_rows) + len(
            self.unparsed_rows
        )


def load_kb_index(path: Path | None = None) -> KbIndex:
    """解析 `docs/kb/00-INDEX.md` 索引表；**唯一实现**，别处不得再写一份。

    - 状态列**允许多词备注**（`✅ 已测否`）并认 `📎`——旧实现两处都不认，静默漏 15 条。
    - 册前缀**通用匹配**（`KB-[A-Z]+`）而非写死四册——`KB-REPO` 这一族就是被写死漏掉的。
    - 册级汇总行（`KB-REPO-*`）显式登记为 `book_level_rows`，不静默跳过。
    - 读不到/解析不出条目 ⇒ `available=False` + `note`（三态，不抛异常；
      知识库缺失不应让议程整条崩掉），但**覆盖度异常照样被记下**
      （`unparsed_rows` / `coverage_identity_holds()`）。
    """
    target = path or KB_INDEX_PATH
    if not target.exists():
        return KbIndex(available=False, note=f"{target} 不存在（知识库未初始化）")
    try:
        text = target.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001  证据缺席不阻塞调用方
        return KbIndex(available=False, note=f"索引读取失败: {exc}")

    entries: dict[str, KbEntry] = {}
    unparsed: list[str] = []
    book_level: list[str] = []
    unknown_books: set[str] = set()
    candidates = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not _INDEX_ROW_PREFIX_RE.match(line):
            continue
        candidates += 1
        book_row = _INDEX_BOOK_ROW_RE.match(line)
        if book_row:
            book_level.append(book_row.group(1))
            continue
        m = _INDEX_ROW_RE.match(line)
        if not m:
            unparsed.append(line)
            continue
        kb_id, title, status, status_note, since = m.groups()
        book = f"KB-{kb_id.split('-')[1]}"
        if book not in KB_BOOKS:
            unknown_books.add(book)
        entries[kb_id] = KbEntry(
            id=kb_id,
            title=title.strip(),
            status=status,
            status_note=status_note.strip(),
            since=since,
            book=book,
        )
    notes: list[str] = []
    if unparsed:
        notes.append(f"{len(unparsed)} 行像索引行但未解析成条目")
    if unknown_books:
        notes.append(f"以下册未在 KB_BOOKS 登记：{sorted(unknown_books)}")
    if not entries:
        return KbIndex(
            available=False,
            note="00-INDEX.md 无可解析条目（表格格式漂移？）",
            unparsed_rows=tuple(unparsed),
            candidate_rows=candidates,
            book_level_rows=tuple(book_level),
            unknown_books=tuple(sorted(unknown_books)),
        )
    return KbIndex(
        entries=entries,
        available=True,
        note="；".join(notes),
        unparsed_rows=tuple(unparsed),
        candidate_rows=candidates,
        book_level_rows=tuple(book_level),
        unknown_books=tuple(sorted(unknown_books)),
    )


# ---------------------------------------------------------------- 路由查询


def route(scenario: str) -> ScenarioRoute:
    """场景字面量（含别名）→ 路由条目。未知场景**显式报错**，不静默兜底。

    为什么 fail-loud：静默兜底成某个默认场景，会让「新场景忘了登记」表现为
    「路由到了别处、且看不出来」——与 `BUG-016` 那类
    「规则缺失与无事件在读取侧同形」是同一类可诊断性缺陷。
    """
    key = ALIASES.get(scenario)
    if key is None:
        raise KbRoutingError(
            f"未登记的场景 {scenario!r}；请在 kb_routing.SCENARIOS 登记"
            "（并同步 docs/summary/system-final-blueprint.md §5 表格），"
            "再在 ALIASES 里登记该字面量"
        )
    return SCENARIOS[key]


def allowed_books(scenario: str) -> frozenset[str]:
    """该场景允许调用的**册**集合（不含 `extra_sources`）。"""
    return frozenset(route(scenario).books)


def scoring_books() -> frozenset[str]:
    """**个股收益打分**允许的册集合（`SCORING_BOOKS` 的取值入口，便于守卫注入替换）。"""
    return SCORING_BOOKS


def parse_kb_id(kb_id: str) -> str | None:
    """条目 ID → 册名；非 `KB-<册>-<序号>` 形态返回 `None`（**不猜**）。"""
    parts = kb_id.split("-")
    if len(parts) != 3 or parts[0] != "KB" or not parts[2].isdigit():
        return None
    book = f"KB-{parts[1]}"
    return book if book in KB_BOOKS else None


# ---------------------------------------------------------------- 引用校验


def _citation_index_error(index: KbIndex) -> str:
    """Reading partial diagnostics is allowed; certifying citations is not."""
    if not index.available or not index.entries:
        return "知识库索引不可用，无法验证引用"
    if index.unparsed_rows or index.unknown_books or not index.coverage_identity_holds():
        # A duplicate ID reduces entries without reducing the source-row count.
        return "知识库索引完整性异常，无法验证引用"
    return ""


def validate_kb_citations(
    scenario: str, kb_ids: Iterable[str], index: KbIndex | None = None,
) -> tuple[list[str], dict[str, str]]:
    """按场景校验一组 KB 引用（册许可 + 状态许可 + 条目必须真实存在）。

    返回 `(accepted, rejected)`：`accepted` 为**保序去重**后的合法条目 ID；
    `rejected` 为 `{条目: 驳回理由}`——**驳回必须带理由**，否则与「没传」不可区分
    （`BUG-016` 的同族教训：区分不了「设计如此」与「坏了」）。

    **为什么状态也参与校验**：蓝图 §5 对盘中场景的限定是「**已落地或试验中的**
    交易纪律」——这是**状态**过滤。若只校验册，就会出现「引用 `📎` 示例或
    `⏳` 待落地条目来支撑盘中买点」，即蓝图禁止的「示例当硬规则」与
    「拿未落地的东西当判据」。

    BUG-025：索引不可用、空或完整性异常时拒绝认证；不能退化为只校验册名。
    `index` 可注入（默认读磁盘）：便于守卫构造**假的**索引来注入自证。
    """
    rt = route(scenario)
    kb_index = index if index is not None else load_kb_index()
    allowed = set(rt.books)
    index_error = _citation_index_error(kb_index)
    accepted: list[str] = []
    rejected: dict[str, str] = {}
    for raw in kb_ids:
        kb_id = str(raw).strip()
        if not kb_id:
            continue
        if kb_id in accepted:  # 幂等：同一轮重复引用只记一次
            continue
        book = parse_kb_id(kb_id)
        if book is None:
            rejected[kb_id] = "不是合法的 KB 条目 ID（应形如 KB-STOCK-11）"
            continue
        if book not in allowed:
            rejected[kb_id] = (
                f"{rt.blueprint_label} 场景未授权册 {book}"
                f"（允许：{'、'.join(rt.books)}）"
            )
            continue
        if index_error:
            rejected[kb_id] = index_error
            continue
        entry = kb_index.entries.get(kb_id)
        if entry is None:
            rejected[kb_id] = "知识库索引中不存在该条目（不得引用未登记的 ID）"
            continue
        if (entry.id != kb_id or entry.book != book
                or entry.status not in KNOWN_STATUSES
                or not isinstance(entry.title, str) or not entry.title.strip()):
            rejected[kb_id] = "知识库条目身份、状态或标题异常，无法验证引用"
            continue
        statuses = rt.statuses_for(book)
        if statuses and entry.status not in statuses:
            allowed_txt = "、".join(statuses)
            rejected[kb_id] = (
                f"{rt.blueprint_label} 场景只允许 {allowed_txt} 状态的条目，"
                f"而 {kb_id} 为 {entry.status}"
                + (f"（{entry.status_note}）" if entry.status_note else "")
            )
            continue
        accepted.append(kb_id)
    return accepted, rejected


def is_hard_rule_source(kb_status: str) -> bool:
    """该状态的知识**能否充当硬规则**。`📎`（示例/参考输入）⇒ 否。

    对应蓝图 §5「**示例条目不得当硬规则**」，依据是 [[KB-DEC-018]] /
    [[KB-DEC-019]]：题材案例只以「示例」身份存在，**不得当已验证知识使用**。
    """
    return kb_status.strip() != STATUS_EXAMPLE


def assert_hard_rule_allowed(kb_id: str, kb_status: str) -> None:
    """示例身份的知识被当硬规则用时**显式报错**（fail-loud，不静默降级）。"""
    if not is_hard_rule_source(kb_status):
        raise KbRoutingError(
            f"{kb_id} 的状态为 {STATUS_EXAMPLE}（示例/参考输入）⇒ 不得当硬规则"
            "（蓝图 §5；[[KB-DEC-018]] / [[KB-DEC-019]]）"
        )


def snapshot_citations(
    scenario: str, kb_ids: Iterable[str] = (), index: KbIndex | None = None,
) -> tuple[str, str]:
    """决策快照的 KB 引用字段 → `(kb_ids_json, kb_refs_json)`（蓝图 §5 的记录项）。

    `requested` 单独保存实际请求，不代表检索正文已读取；旧快照不回写。
    `kb_ids_json` = 被采纳的条目（保序去重）；`kb_refs_json` = 引用状态 + 支持/冲突依据，
    形如 `{"state": ..., "status": {id: "✅"}, "support": [...], "conflict": {id: 理由}}`。

    **三态显式**（`not_consulted` / `cited` / `rejected`）：未引用与"引用了但被驳回"
    必须在读取侧可区分——`kb_ids == []` 本身区分不了这两者，而前者是现状、后者是异常。
    """
    kb_index = index if index is not None else load_kb_index()
    # Materialize once: callers may supply a one-shot generator.
    requested = list(dict.fromkeys(value for raw in kb_ids if (value := str(raw).strip())))
    accepted, rejected = validate_kb_citations(scenario, requested, kb_index)
    if accepted:
        state = REF_STATE_CITED
    elif rejected:
        state = REF_STATE_REJECTED
    else:
        state = REF_STATE_NOT_CONSULTED
    payload: dict[str, Any] = {
        "state": state,
        "requested": requested,  # Requests are not evidence of verified citations.
        "status": {kb_id: kb_index.status_of(kb_id) for kb_id in accepted},
        # 「支持依据」= 被采纳条目的状态档 + 索引里的一句话（可回溯到具体条目）
        "support": [
            {"kb_id": kb_id, "status": kb_index.status_of(kb_id),
             "title": (kb_index.entries[kb_id].title if kb_id in kb_index.entries else "")}
            for kb_id in accepted
        ],
        # 「冲突依据」= 被驳回的条目 + 理由（驳回一定要有理由，见 validate_kb_citations）
        "conflict": rejected,
    }
    index_error = _citation_index_error(kb_index)
    if index_error:
        payload["index_note"] = kb_index.note or index_error
    return _json(accepted), _json(payload)


def _json(value: Any) -> str:
    """与 `opportunity_learning._json` 同口径（稳定分隔符 + 排序键，便于逐字比对）。"""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )


# ---------------------------------------------------------------- 不变式（守卫调用面）


def assert_scoring_excludes_governance_books(scenario: str = "system_evolution") -> None:
    """恒等式守卫：`system_evolution` 的允许册与**打分册**交集必须为空。

    即蓝图 §5「**不进入个股收益打分**」。写成可调用函数是为了让守卫能**注入自证**
    （改 `SCORING_BOOKS` 应当让守卫变红），而不是靠人读表格。
    """
    overlap = allowed_books(scenario) & scoring_books()
    if overlap:
        raise KbRoutingError(
            f"{route(scenario).blueprint_label} 的允许册与个股收益打分册相交："
            f"{sorted(overlap)} —— 违反蓝图 §5「不进入个股收益打分」"
        )


def assert_scoring_admission_is_evidence_gated() -> None:
    """`enters_scoring=True` **必须**有消融证据支撑。

    蓝图 §5：「用有/无 KB 的影子消融比较候选召回、Precision@K 与净期望；
    **没有稳定增益时，KB 只保留解释/治理作用，不强行入模**」。
    ⇒ 把「未验证就入模」变成会报红的错误，而不是靠人记得没做消融。
    """
    unguarded = [
        rt.key for rt in SCENARIOS.values() if rt.enters_scoring and not rt.ablation_evidence
    ]
    if unguarded:
        raise KbRoutingError(
            f"以下场景声明 KB 引用进入个股收益打分，却没有消融证据：{unguarded}。"
            "先在 opportunity_learning.opportunity_scorecard 口径下跑有/无 KB 影子对照"
            "（可成交样本 ≥ MIN_LABELS_FOR_VERDICT）再置 enters_scoring=True（蓝图 §5）"
        )


def assert_aliases_resolve(literals: Sequence[str]) -> None:
    """给定一批字面量，**每一个都必须能被解析**成已知场景。

    用途：底账里出现新的 `scenario` 字面量却忘了登记别名时，此处报红
    ——「守卫指向一个够不到的落点」的反面（见 `IMP-034` 的教训）。
    """
    unknown = sorted({lit for lit in literals if lit not in ALIASES})
    if unknown:
        raise KbRoutingError(
            f"以下 scenario 字面量未登记别名：{unknown}。"
            "请在 kb_routing.ALIASES 登记（否则该场景的路由在运行时才炸）"
        )


# ---------------------------------------------------------------- 读侧


def routing_table() -> list[dict]:
    """读侧：四场景路由表的 JSON 友好形态（供端点暴露与蓝图比对）。"""
    return [
        {
            "scenario": rt.key,
            "blueprint_label": rt.blueprint_label,
            "knowledge_label": rt.knowledge_label,
            "books": list(rt.books),
            "allow": [
                {"book": rule.book, "statuses": list(rule.statuses)} for rule in rt.allow
            ],
            "extra_sources": list(rt.extra_sources),
            "purpose": rt.purpose,
            "prohibition": rt.prohibition,
            "enters_scoring": rt.enters_scoring,
        }
        for rt in SCENARIOS.values()
    ]


def index_overview(index: KbIndex | None = None) -> dict:
    """读侧：KB 索引覆盖度概览（**含未解析行与恒等式**——自证不能只留在测试里）。"""
    kb_index = index if index is not None else load_kb_index()
    return {
        "available": kb_index.available,
        "note": kb_index.note,
        "total": kb_index.total,
        "candidate_rows": kb_index.candidate_rows,
        "book_level_rows": list(kb_index.book_level_rows),
        "unparsed_rows": list(kb_index.unparsed_rows),
        "unknown_books": list(kb_index.unknown_books),
        # 覆盖度自证：判据面（candidate_rows）必须等于 条目 + 册级行 + 未解析行
        "coverage_identity_holds": kb_index.coverage_identity_holds(),
        "by_status": kb_index.by_status(),
        "example_count": len(kb_index.ids_by_status(STATUS_EXAMPLE)),
        "registered_books": list(KB_BOOKS),
        "scoring_books": sorted(SCORING_BOOKS),
        "known_statuses": list(KNOWN_STATUSES),
    }
