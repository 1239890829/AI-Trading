#!/usr/bin/env python3
"""文档体检（docs 治理的「一条命令」版，kb/07-doc-curation.md §8.2 的可执行实现）。

为什么有它：`kb/07-doc-curation.md` 写明了「新建必登记 / 解引用 / 摘要前置 / 按层配额」，
但规则只是**文字**——没有载体时，收尾是否整理全靠人记得。本脚本把 §8.2 月度体检
变成一条命令，使「任务完成后自动整理」有机制可依（§9 自我淘汰条款的落地）。

用法：
    python3 scripts/doc-health.py            # 全量体检，有问题 exit 1
    python3 scripts/doc-health.py --quiet    # 静默模式：全绿时**零输出**，只在有失败项时打印
    python3 scripts/doc-health.py --all      # 额外扫描 archive/ 与历史日志（默认跳过，见下）

检查项（对应 §8.2）：
  A 未登记文档   docs 根 *.md 未出现在 docs/INDEX.md
  B 死链         AGENTS.md / README.md / docs 活文档 / .workbuddy/memory/MEMORY.md 里
                 `docs/xxx.md` 指向不存在的文件（**排除记录性引用**：同句含
                 已删除/已归档/被取代/归档/移入/待建 等 的引述，以及占位名如 `YYYY-MM-DD.md`）
                 **默认不扫 archive/ 与逐日 memory 日志**——它们是只读历史/append-only 记录，
                 内部引用天然指向"当时存在的文件"（`--all` 可强制全扫）
  C 超层配额     KB **条目级 >60 行**（§7 L0 硬门）/ 框架·总纲 >500 / 报告 >600
                 （已判定的容忍项见 TOLERATED / TOLERATED_ENTRIES）
                 KB 文件 >800 行 = **非阻断提示**（§7：L0 约束维度是条目级、总行数不限，
                 800 只是「评估按子类拆文件」的触发条件，不算 FAIL——2026-09-12 与规范对齐）
  D 摘要缺失     >150 行且前 40 行无结论摘要（§7 摘要前置）
                 豁免：L4 时间序列（daily-review/ evolution/ repo-watch/，按时间消费）
                       与 KB 分类文件（00-INDEX 用「条目级」约束，见 §7 表）
  E 同类聚集     同前缀（取 `-` 前段）≥3 份 → 提示评估 §5.1 合并 / §5.2 共同索引页
  F  代码注释死引用  app/tests/web/scripts 里 `docs/xxx.md` 指向不存在的文件
  F2 代码裸名死引用  已删/已归档方案文档的**裸名**（如 `linkage-design §3`），F 扫不到
  F3 KB 指针错册   `见 kb/NN-x.md … KB-ENG-NN` 的条目号必须真的在那个册里
                 （KB 分册后「条目搬家」会让只写册名的指针静默指错；触发刻意写窄，见函数说明）
  F4 废弃锚名死引用  文档里以**裸名**指向上册（如「（full.md §2.2）」）但该文件全仓不存在
                 ——B 只认 Markdown 链接、F 只认 `docs/xxx.md`，两条都扫不到它；
                 首次上线即实测出 **8 处 / 8 文件**（6 份 docs 正文 + INDEX + README），
                 死锚存活 10+ 天无人发现（2026-09-12）
  G KB 孤儿条目   某条 KB 条目在全仓**零外部引用**（Karpathy wiki 的 orphan-page lint）
                 ——「沉淀了但没人用」的唯一可量化信号；排除定义行自身
  J 文档代码锚点  文档点名的**仓库代码路径必须存在**（两个子面）：
                 J1 行内 `` `app/x/y.py` ``（首段属已知根）；
                 J2 围栏代码块里的**目录树行** `├── x.py`（末段文件名全仓须存在）。
                 动机 = §6.12「设计文档能力失真」的可判子集（F-10）；先量三个原型才定档，
                 宽口径已**实测否决**（见函数 docstring）。上线时 4 处命中 / 0 误报
                 **判定面 = git 跟踪清单**（= CI 检出内容），不是本地文件系统
                 ——否则回收站副本会把死引用持续"喂绿"（2026-09-12 修正，见 `_repo_basenames`、KB-ENG-70）
  K 表格分隔行   以 `|` 开头的**行块首行**，其下一行必须是 GFM 分隔行（`|---|---|`）。
                 动机 = §6.14 的渲染器死循环事故：`docs/` 里曾用**空行给同一张表分组**，
                 在严格 GFM 下必然打碎。渲染器那半已有 `markdown-view.test.tsx` 守住，
                 **文档这半此前没有任何机制**（这类书写不会让任何既有检查变红）。
                 上线前实测活文档面 **292 个 `|` 起始块全部合法、0 处可疑** ⇒ 零假阳性可设硬门。
                 围栏代码块内不判（目录树/示例里的 `|`）。

局限（诚实声明）：C 的"日志单轮新增 ≤80 行"是**过程指标**，静态扫描判不出，需人工/议程侧核对。
J 只回答「点名的代码资产还在不在」，**不回答**「文档对某能力的描述是否过时」——
后者（如「禁止交易名单」不存在、「复权方式切换」不存在）的锚点是**中文子能力描述**，
与代码无机械映射，三个原型实测真阳性≈0 ⇒ 明确判定**不可自动对账**，不设门禁（KB-ENG-68）。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# 引用扫描面（按 §8.2：文档 / AGENTS.md / MEMORY.md）
SCAN_FILES = ["AGENTS.md", "README.md", "docs/INDEX.md", "docs/plan-registry.md",
              "docs/retro-and-gaps.md", ".workbuddy/memory/MEMORY.md"]
REF_RE = re.compile(r"docs/([\w\-./]+\.md)")
#: 代码注释里的 docs 引用：断言 `docs/` 不是更长路径（如 skills/hithink-finance/docs/）的尾巴
CODE_REF_RE = re.compile(r"(?<![\w/.-])docs/([\w\-./]+\.md)")
# 记录性引用标记（同句出现即视为"在案引述"，不算断链）
RECORD_MARKERS = ("已删除", "已归档", "被取代", "归档", "移入", "存档", "只读",
                  "删除", "待建", "尚未创建", "已并入", "精华见")
# 占位/模板文件名（不是真引用）
PLACEHOLDER_RE = re.compile(r"(YYYY|MM|DD|<|\{|\}|当天|xxx|X{3,})")

# 摘要前置的识别词（前 40 行出现任一即视为有摘要/定位）
ABSTRACT_MARKERS = ("速览", "摘要", "定位", "导览", "导航")
# L4 时间序列目录（豁免摘要前置）
L4_DIRS = ("daily-review", "evolution", "repo-watch", "push-templates")
# 层配额：框架/总纲文件名单 → 软上限 500；报告按命名 + 日期判定 → 600
FRAMEWORK_FILES = ("theme-sentiment-methodology.md", "data-source-comparison.md",
                   "factor-lifecycle-governance.md", "factor-candidates.md",
                   "daily-review-sop.md", "daily-review-checklist.md")
FRAMEWORK_CAP, REPORT_CAP, KB_FILE_CAP = 500, 600, 800
KB_ENTRY_CAP = 60  # §7 L0 硬门：单条 KB 条目 ≤60 行
#: KB 分类文件的**唯一扫描入口**（两处检查共用，避免各自写 glob 而漏改一处）
#: ⚠️ 原为 `glob("0[1-9]*.md")`——只覆盖 01~09，**新建 `10-*.md` 会被静默漏检**
#: （既不算 C 的条目超长，也不出 >800 行提示），属「扫描面被写窄」类缺陷（[[KB-ENG-54]]）。
#: 改正为「两位数字开头」并对全部 kb/*.md 做**覆盖断言**（见 kb_file_coverage_gap）。
KB_SCAN_RE = re.compile(r"^\d{2}-.+\.md$")


def kb_classified_files() -> list[Path]:
    """docs/kb/ 下所有「两位数字前缀」的分类文件（00-INDEX 不计：它是索引不是分类）。"""
    return sorted(p for p in (DOCS / "kb").glob("*.md")
                  if KB_SCAN_RE.match(p.name) and not p.name.startswith("00-"))


def kb_file_coverage_gap() -> list[str]:
    """覆盖守卫：docs/kb/*.md 里**没被任何检查覆盖**的文件（防扫描面被写窄）。

    为什么需要：条目超长与文件超长两道检查都靠 glob 选文件——glob 写窄时，
    新文件不是「检查失败」而是**根本不进检查**，症状恰好是「全绿」。
    要求「新分类文件加进来即自动纳入」，不依赖有人记得改 glob。
    """
    covered = {p.name for p in kb_classified_files()}
    return [p.name for p in sorted((DOCS / "kb").glob("*.md"))
            if p.name not in covered and not p.name.startswith("00-")]
# 已判定的容忍项（显式登记，避免每跑一次就重新争论一次）
TOLERATED = {
    "theme-sentiment-methodology.md":
        "584 行 ≤600；主题不可干净分离（题材与情绪在本项目耦合），§11 第三轮 #5 已判定不拆",
}
# L0 条目级容忍项（同上原则：判一次、留痕，不反复争论）
TOLERATED_ENTRIES = {
    "01-stock-picking.md:KB-STOCK-29":
        "69 行：原版实测+候选B 两条证据链一体，拆开断因果；待蒸馏时处理",
    "04-decisions.md:KB-DEC-019":
        "63 行：治理层决策（准入五条+反固化条款）语义完整优先；待蒸馏时处理",
}
# I 空章节：**有意留空的**标题（键 = 相对路径 + 完整标题文本；行号会漂故不进口）。
# 用完整标题而非前缀：标题一旦被改写，键就对不上 → 由 ghost 反向断言暴露（防容忍名单静默失效）。
EMPTY_SECTION_ALLOW = {
    ("docs/daily-review/2026-09-08.md", "6.3 今日未执行 §7（取长补短层）——非周五"):
        "标题本身即结论（非周五故未执行 §7）；L4 历史快照，补正文＝改历史",
}
#: 结构性书写扫描面：docs/ 全量（archive 除外——只读历史）+ 仓库根的操作手册。
#: ⚠️ **I 空章节与 K 表格分隔行共用这一份**。名字里的 `EMPTY_SECTION` 是历史遗留（最初只服务 I 项）
#: —— **不要为 K 再建一份同名清单**（两份清单必漂移，KB-ENG-26）。
EMPTY_SECTION_ROOT_FILES = ("AGENTS.md", "README.md")
HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*?)\s*$")
_HR_LINES = {"---", "***", "___"}
_FENCE = "\x00fence\x00"


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def check_unregistered() -> list[str]:
    idx = _read(DOCS / "INDEX.md")
    out = []
    for p in sorted(DOCS.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        if p.name not in idx:
            out.append(f"docs/{p.name}")
    return out


def check_dead_links(scan_all: bool = False) -> list[tuple[str, int, str]]:
    targets = [ROOT / f for f in SCAN_FILES]
    for p in sorted(DOCS.rglob("*.md")):
        rel = p.relative_to(DOCS)
        if not scan_all:
            if "archive" in rel.parts:
                continue                      # 只读历史：内部引用指向"当时存在的文件"，不改
            if rel.parts and rel.parts[0] in L4_DIRS:
                continue                      # L4 逐日记录：append-only，不改历史
        targets.append(p)
    if scan_all:
        targets += sorted((ROOT / ".workbuddy" / "memory").glob("*.md"))
    out: list[tuple[str, int, str]] = []
    for t in targets:
        if not t.exists():
            continue
        txt = _read(t)
        lines = txt.splitlines()
        for m in REF_RE.finditer(txt):
            rel = m.group(1)
            if (DOCS / rel).exists() or (ROOT / "docs" / rel).exists():
                continue
            if PLACEHOLDER_RE.search(rel):
                continue  # 模板占位名（YYYY-MM-DD.md / 当天.md / xxx.md）
            ln = txt[: m.start()].count("\n")
            seg = lines[ln] if ln < len(lines) else ""
            if any(k in seg for k in RECORD_MARKERS):
                continue  # 记录性引用：在案引述，不算断链
            out.append((str(t.relative_to(ROOT)), ln + 1, rel))
    return out


#: F 检查的**已登记例外**：(文件, 引用) → 理由。这些不是「指向文档的指针」，而是
#: CLI 示例的 `--out` 输出路径 / 测试输入串 / Markdown 语法示例——**改掉反而失真**，
#: 故显式登记留痕（同 TOLERATED 的做法），而不是把它们从扫描里悄悄排除。
CODE_REF_ALLOW = {
    ("backend/tests/test_code_executor.py", "plan.md"):
        "测试输入串（`.replace('.md','.py')` 后作白名单校验用例，不是文档指针）",
    ("backend/scripts/replay_picks.py", "ablation-report.md"):
        "CLI 示例里的 `--out` **输出路径**（未来产物，报告尚未生成）",
    ("backend/scripts/backtest_picks.py", "x.md"):
        "CLI 用法示例的 `--out` 输出路径占位",
    ("apps/web/components/agent/markdown-view.tsx", "xx.md"):
        "docstring 里的 Markdown 链接语法示例（形如 `[文本](docs/<名字>.md)`）",
    ("backend/tests/test_doc_health_empty_sections.py", "a.md"):
        "**合成文档名**：该测试把扫描面 monkeypatch 到 `tmp_path`，临时树里那个 `a.md`"
        "不是仓库文档指针（它只用来钉 I 项判据）。真实扫描面由同文件末条"
        "`test_real_repo_has_no_unregistered_empty_section` 在真仓库上覆盖",
    ("backend/tests/test_doc_health_anchors.py", "a.md"):
        "**合成文档名**（同上一行的 `test_doc_health_empty_sections.py`）：扫描面 monkeypatch "
        "到 `tmp_path`，临时树里的 `a.md` 只是宽容名单的**键**——键的形状必须是「文档相对路径」，"
        "所以它长得像文档指针但不是。真实扫描面由同文件末条 "
        "`test_real_repo_has_no_dead_doc_anchor` 在真仓库上覆盖",
    ("backend/tests/test_doc_health_memory_index.py", "kb/definitely-missing.md"):
        "**合成文档名**（同上面两条 `a.md`）：该测试把索引面 monkeypatch 到 `tmp_path`，"
        "临时树里这个凭空造出的路径用来钉「`docs/` 前缀的指针**由 B 项管、N 项不重复要求**」"
        "这条判据，不是仓库文档指针。真实索引面由 N 项在真仓库上覆盖",
    # ⚠️ 本文件自身也吃过同一次亏：上面这条理由的第一版把 `docs/` 前缀写了出来，
    # 结果 `check_code_refs` 把**自己的说明文字**判成死引用（`scripts/doc-health.py:184`）。
    # 与 `STALE_ANCHOR_ALLOW` 的取舍同源：**描述缺陷的文字本身必须点名缺陷名**，
    # 处置是"换个不会命中自己的写法"，不是把检查器自己的扫描面关掉。
}


#: 已删除/已归档**方案文档**的 slug。代码注释里出现**裸名**（不带 `docs/` 前缀、
#: 不带 `.md`）同样是失效指针——读者按名字找不到文件，而 `check_code_refs` 只认
#: `docs/xxx.md` 形式、扫不到它。值为真身指针（改写时照此改）。
LEGACY_DOC_SLUGS = {
    "architecture-redesign": "docs/archive/architecture-redesign.md",
    "assistant-optimization-plan": "docs/archive/assistant-optimization-plan.md",
    "minute-chart-plan": "docs/archive/minute-chart-plan.md",
    "ui-redesign-plan": "docs/archive/ui-redesign-plan.md",
    "plan-review": "docs/archive/plan-review.md",
    "linkage-design": "docs/summary/architecture-design.md",
    "fund-flow-redesign": "docs/summary/architecture-design.md",
    "hotspot-pipeline-design": "docs/summary/architecture-design.md",
    "news-event-module-redesign": "docs/summary/architecture-design.md",
    "ai-agent-console-plan": "docs/summary/ai-evolution.md",
    "ai-brain-plan": "docs/summary/ai-evolution.md",
    "evolution-brain-plan": "docs/summary/ai-evolution.md",
    "research-autonomous-agent": "docs/summary/ai-evolution.md",
    "llm-finetune-research": "docs/summary/ai-evolution.md",
    "system-audit-20260908": "docs/summary/review-governance.md",
    "system-review-2026-09-02": "docs/summary/review-governance.md",
    "system-review-20260909": "docs/summary/review-governance.md",
    "console-three-modules-review": "docs/summary/review-governance.md",
    "board-fund-page-audit": "docs/summary/review-governance.md",
    "hunting-review": "docs/summary/review-governance.md",
    "factor-library-design": "docs/summary/factor-system.md",
    "factor-ic-review-20260908": "docs/summary/factor-system.md",
    "stock-picking-system-2026-09-02": "docs/summary/stock-strategy.md",
    "halt-check-risk-analysis": "docs/summary/stock-strategy.md",
    "sentiment-phase-review": "docs/sentiment.md（历史误判案例库）",
    "nfp-ashare-validation": "docs/summary/data-market.md",
    "repo-deep-research": "docs/summary/data-market.md",
    "document-consolidation-plan": "docs/kb/07-doc-curation.md",
    "orderbook-source-evaluation": "docs/archive/orderbook-source-evaluation.md",
    "full-project-review-2026-09-01": "docs/archive/full-project-review-2026-09-01.md",
}

#: 裸名检查的**已登记例外**：(文件, slug) → 理由。
#: ⚠️ 加例外前先分辨「这到底是不是一个引用」——把真引用登记成例外 = 亲手关掉守卫。
#: 空配置的历史：2026-09-12 那条 `nfp-ashare-validation@test_chains.py` 例外随着该断言
#: 改写（不再依赖文档名做标记）已经**不再命中**，属死配置故删除：**过期的允许列表本身
#: 就是「看着有守卫、实际不设防」**，与「永不触发的门禁」同类。
LEGACY_SLUG_ALLOW: dict[tuple[str, str], str] = {
    ("apps/web/components/agent/kb-browser-tab.test.tsx", "plan-review"):
        "输入搜索框的**关键词**（验证「搜索穿透折叠」能命中归档件）——"
        "该用例的断言目的**正是**命中归档件，不是指向已删文档的指针",
}

#: 裸名检查**整体跳过的文件**：检查器自身——`LEGACY_DOC_SLUGS` 的**定义**里必然
#: 逐个写着这些名字（引用 vs 定义的区分靠语义，脚本判不了）。代价：本文件里真写错
#: 一个裸名不会被抓；但该文件是人工策展的检查器，收益大于代价。
LEGACY_SLUG_SKIP_FILES = {"scripts/doc-health.py"}

LEGACY_SLUG_RE = re.compile(
    r"(?<![\w/.-])(" + "|".join(map(re.escape, sorted(LEGACY_DOC_SLUGS, key=len, reverse=True))) + r")(?![\w.])"
)


#: **F4 废弃锚名登记表**：锚名 → 真身/处置说明。
#: 这些名字曾在现役文档里被当作"上/下册指针"使用（形如「（full.md §2.2）」），
#: 但**锚名对应的文件从未进入 git 历史的任何一次提交**。
#: B 项只认 Markdown 链接、F 项只认 `docs/xxx.md` 形式，两者都扫不到这种裸名
#: ⇒ 6 份现役文档带着死锚活了 10+ 天，读者按名字找不到任何东西（2026-09-12 发现）。
#:
#: **为什么用登记表而不是"裸名存在性"全扫**：全扫实测误报 114 处 ——
#: `AGENTS.md` 在仓库根（合法）、`API_REFERENCE.md` 属被调研的外部仓库、
#: INDEX/plan-registry/summary 的「已删除 · 去向」表更是**故意**写着不存在的名字。
#: 登记表零误报、可注入验证，与 `LEGACY_DOC_SLUGS` 同一思路；
#: 「过宽的触发等于没有触发」（KB-ENG-58）——宁可窄而准。
STALE_ANCHORS: dict[str, str] = {
    "full.md": "该文件从未存在；08-29 的「全文」由 PROJECT-MASTER.md 承载，非本名",
}

#: F4 的**已登记例外**：(文件, 锚名) → 理由。
#: 两处都是**机制自身的说明文**：`kb/07-doc-curation.md` 是 F4 的规格（必须点名这个锚名，
#: 否则规则无法被读者对准），`INDEX.md` 是本次变更日志（记录"清除了 8 处该锚名"）。
#: 与 `LEGACY_SLUG_SKIP_FILES` 同类取舍：**描述缺陷的文字本身必然写到缺陷名**，
#: 代价是这两个文件里真写错锚名不会被抓——由人工策展保证（收尾体检仍看得到变化）。
STALE_ANCHOR_ALLOW: dict[tuple[str, str], str] = {
    ("docs/kb/07-doc-curation.md", "full.md"): "F4 项的规格说明（须点名被检查的锚名）",
    ("docs/INDEX.md", "full.md"): "变更日志：记录本轮清除了 8 处该锚名",
}

STALE_ANCHOR_RE = re.compile(
    r"(?<![\w/.\-])("
    + "|".join(re.escape(n) for n in sorted(STALE_ANCHORS, key=len, reverse=True))
    + r")(?![\w])"
)


def check_stale_anchors() -> list[tuple[str, int, str]]:
    """F4：**废弃锚名**死引用（裸名指向全仓不存在的文件）。

    扫描面与 B 一致（SCAN_FILES + docs 活文档面），跳过 archive/ 与 L4 逐日目录——
    那些是只读历史，内部引用天然指向"当时存在的文件"。含记录标记（已删除/已归档…）
    的行同样跳过：INDEX / plan-registry 的「原件已删除」去向表是合法提及。
    """
    out: list[tuple[str, int, str]] = []
    targets: list[Path] = [ROOT / f for f in SCAN_FILES]
    targets += sorted(DOCS.rglob("*.md"))
    for t in targets:
        if not t.exists() or t.name in LEGACY_SLUG_SKIP_FILES:
            continue
        rel_parts = t.relative_to(DOCS).parts if DOCS in t.parents else ()
        if "archive" in rel_parts:
            continue
        if rel_parts and rel_parts[0] in L4_DIRS:
            continue
        rel = str(t.relative_to(ROOT))
        txt = _read(t)
        lines = txt.splitlines()
        for m in STALE_ANCHOR_RE.finditer(txt):
            name = m.group(1)
            if (rel, name) in STALE_ANCHOR_ALLOW:
                continue
            ln = txt[: m.start()].count("\n")
            seg = lines[ln] if ln < len(lines) else ""
            if any(k in seg for k in RECORD_MARKERS):
                continue
            out.append((rel, ln + 1, name))
    return out


def check_legacy_slugs() -> tuple[list[tuple[str, int, str]], list[tuple[str, str]]]:
    """F2 代码注释里的**裸名**死引用（已删/已归档方案文档的名字）。

    与 F 互补：F 管 `docs/xxx.md` 形式，F2 管「只写了名字」的形式——
    后者更隐蔽，因为 `docs/` 都不出现，肉眼与脚本都容易漏。

    **只扫代码，不扫 docs**：docs 面的同名出现绝大多数是**记录性引用**
    （删档去向表、「原件已删除、精华并入本文」的注记）——那是治理痕迹，
    改了反而抹掉溯源，故豁免。
    """
    skip_dirs = {"node_modules", ".next", ".turbo", "__pycache__", ".venv", ".venv-research", "dist", "build"}
    out: list[tuple[str, int, str]] = []
    allowed: list[tuple[str, str]] = []
    for base in ("backend", "apps/web", "scripts"):
        root = ROOT / base
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.suffix not in (".py", ".ts", ".tsx", ".mjs", ".js"):
                continue
            if any(d in p.parts for d in skip_dirs):
                continue
            relf = str(p.relative_to(ROOT))
            if relf in LEGACY_SLUG_SKIP_FILES:
                continue
            txt = _read(p)
            for i, ln in enumerate(txt.splitlines(), 1):
                for m in LEGACY_SLUG_RE.finditer(ln):
                    slug = m.group(1)
                    if (relf, slug) in LEGACY_SLUG_ALLOW:
                        allowed.append((relf, slug))
                        continue
                    out.append((relf, i, slug))
    return out, allowed


def check_code_refs() -> tuple[list[tuple[str, int, str]], list[tuple[str, str]]]:
    """F 代码注释里的 docs 引用必须可解（2026-09-12 新增，§七 #28 的根治）。

    **为什么 B 死链管不到**：`check_dead_links` 只扫 `*.md`。删档时「全仓 grep 解引用」
    若只覆盖 md，`app/` `tests/` 里的 docstring 指针会留下来 —— 把复核者引向不存在的文件。

    排除项（防误报）：
    - `skills/hithink-finance/docs/...` —— 那是 **fuyao 官方 API 文档**，不在本仓 docs 面；
      故用 `(?<![\\w/.-])` 断言 `docs/` 不是某个更长路径的尾巴；
    - 构建产物 / 虚拟环境；
    - 模板占位名（YYYY-MM-DD.md / xxx.md）；
    - 记录性语句（"已删除/已归档" 等，在案引述不算断链）；
    - `CODE_REF_ALLOW` 里已登记理由的示例/输出路径。

    :returns: (违规列表, 命中登记例外列表)
    """
    skip_dirs = {"node_modules", ".next", ".turbo", "__pycache__", ".venv", ".venv-research", "dist", "build"}
    out: list[tuple[str, int, str]] = []
    allowed: list[tuple[str, str]] = []
    for base in ("backend", "apps/web", "scripts"):
        root = ROOT / base
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.suffix not in (".py", ".ts", ".tsx", ".mjs", ".js"):
                continue
            if any(d in p.parts for d in skip_dirs):
                continue
            relf = str(p.relative_to(ROOT))
            txt = _read(p)
            lines = txt.splitlines()
            for m in CODE_REF_RE.finditer(txt):
                rel = m.group(1)
                if (DOCS / rel).exists():
                    continue
                if (relf, rel) in CODE_REF_ALLOW:
                    allowed.append((relf, rel))
                    continue
                if PLACEHOLDER_RE.search(rel):
                    continue
                ln = txt[: m.start()].count("\n")
                seg = lines[ln] if ln < len(lines) else ""
                if any(k in seg for k in RECORD_MARKERS):
                    continue
                out.append((relf, ln + 1, rel))
    return out, allowed


#: 指针句式（F3 窄触发）：`见 kb/NN-x.md` 与紧随其后的 KB-ID 必须同册
KB_POINTER_RE = re.compile(r"(?:见|参见|详见)\s*`?(kb/\d{2}-[\w\-]+\.md)`?[^）)\n]{0,40}?(KB-[A-Z]+-\d+)")


def kb_entry_owner() -> dict[str, str]:
    """KB-ID → 所在文件名。**从 `### KB-` 标题实读**，不手工维护映射表（避免第二份真相源）。"""
    out: dict[str, str] = {}
    for p in (DOCS / "kb").glob("*.md"):
        for m in re.finditer(r"^### (KB-[A-Z]+-\d+)", _read(p), re.M):
            out.setdefault(m.group(1), p.name)
    return out


def check_kb_pointer_files() -> list[tuple[str, int, str, str, str]]:
    """F3 指针写错册：`见 kb/NN-x.md` 后面跟的 KB-ID 必须**真的在那个文件里**。

    为什么需要：KB-ENG 按子类分册后，「引用某条目」与「写对册名」变成两件事——
    只写 ID 的引用（`[[KB-ENG-NN]]`）不受搬家影响，但**带文件路径的指针会静默指错册**
    （条目移走、句子还停在原册名上），而 B/F/F2 都只管「文件是否存在」、不管「条目在不在里面」。

    ⚠️ **触发刻意写窄，且宽版已实测否决**：放宽为「同行出现即比对」时全仓 23 处命中里
    **真误指针 0 处**（全是逐日 memory 的记录性引用、以及长句里两个不相干短语的巧合）
    ⇒ 会变成「永远红的门禁」，按 [[KB-ENG-58]] 不可取。窄触发（只认「见/参见/详见 + 路径 + 40 字内 ID」）
    实测 1 处命中 / 0 处误报 —— **宁可少报也不制造噪音**。
    """
    owner = kb_entry_owner()
    skip_dirs = {"node_modules", ".next", ".turbo", "__pycache__", ".venv", ".venv-research",
                 "dist", "build", "archive", "trash", ".workbuddy"}
    targets: list[Path] = []
    for name in ("AGENTS.md", "README.md"):
        if (ROOT / name).exists():
            targets.append(ROOT / name)
    for base in ("docs", "backend", "apps/web", "scripts"):
        root = ROOT / base
        if root.exists():
            targets += [p for p in root.rglob("*") if p.is_file()]
    out: list[tuple[str, int, str, str, str]] = []
    for p in targets:
        if p.suffix not in (".md", ".py", ".ts", ".tsx", ".mjs", ".js"):
            continue
        if any(d in p.parts for d in skip_dirs):
            continue  # 逐日 memory 是 append-only 记录，天然指向"当时"的册，不算漂移
        rel = str(p.relative_to(ROOT))
        for i, line in enumerate(_read(p).splitlines(), 1):
            for m in KB_POINTER_RE.finditer(line):
                said, kid = m.group(1).split("/")[-1], m.group(2)
                real = owner.get(kid)
                if real and real != said:
                    out.append((rel, i, said, kid, real))
    return out


#: G 项豁免：这些条目零外部引用属「设计如此」，不是待办（判一次、留痕，不反复争论）。
KB_ORPHAN_ALLOW: dict[str, str] = {}


def check_kb_orphans() -> list[tuple[str, str, str]]:
    """G 项：KB 条目的**孤儿检测**（零外部引用）。

    为什么需要：B/F/F2 只管「文件还在不在」，F3 只管「指针册名对不对」——
    **没有任何检查回答「这条知识还有人引用吗」**。Karpathy 的 wiki lint 把
    orphan page 列为必查项：孤儿 = 沉淀了但没人用，是「知识躺在角落」的**唯一可量化信号**。

    口径（两条，都刻意选择以免制造噪音）：
    - **排除定义行自身**：`### KB-XXX-NN` 那一行必然含自己，不是引用（否则恒不自 0，检查失效）。
    - **册内互引算引用**：03 册的条目被 09 册引用是真实引用，不因「同一册」而降级。

    扫描面与 F3 一致（代码 + 文档），但**排除** archive / 逐日 memory / trash：
    那些是只读历史或 append-only 记录，它们的引用不能证明「今天还有人用」。

    ⚠️ **本检查上线时实测为 0 条**（2026-09-12）——它不修 bug，而是**防退化**：
    此后新沉淀的条目若无人引用，会在收尾体检时立即暴露。
    """
    owner_counts: dict[str, int] = {}
    for p in (DOCS / "kb").glob("*.md"):
        for m in re.finditer(r"^### (KB-[A-Z]+-\d+)", _read(p), re.M):
            owner_counts[m.group(1)] = 0

    skip_dirs = {"node_modules", ".next", ".turbo", "__pycache__", ".venv", ".venv-research",
                 "dist", "build", "archive", "trash", ".workbuddy"}
    targets: list[Path] = []
    for name in ("AGENTS.md", "README.md", "CONTEXT.md"):
        if (ROOT / name).exists():
            targets.append(ROOT / name)
    for base in ("docs", "backend", "apps/web", "scripts"):
        root = ROOT / base
        if root.exists():
            targets += [p for p in root.rglob("*") if p.is_file()]

    for p in targets:
        if p.suffix not in (".md", ".py", ".ts", ".tsx", ".mjs", ".js", ".sh"):
            continue
        if any(d in p.parts for d in skip_dirs):
            continue
        for line in _read(p).splitlines():
            if re.match(r"^### KB-[A-Z]+-\d+", line):
                continue  # 定义行：条目在自我介绍，不是被引用
            for kid in owner_counts:
                if kid in line:
                    owner_counts[kid] += 1

    out: list[tuple[str, str, str]] = []
    for p in kb_classified_files():
        for m in re.finditer(r"^### (KB-[A-Z]+-\d+)\s+(.*)", _read(p), re.M):
            kid, title = m.group(1), m.group(2).strip()
            if owner_counts.get(kid, 0) == 0 and kid not in KB_ORPHAN_ALLOW:
                out.append((p.name, kid, title))
    return out


def check_kb_entries() -> list[tuple[str, str, int]]:
    """§7 L0 硬门：单条 KB 条目 ≤60 行（`### KB-` 到下一条目起点的行距）。

    ⚠️ **量的是「到下一条目起点的行距」⇒ 两条目之间的分隔行（空行 / `---` / 空行，共 3 行）
    被计入上一条**；而**文件最后一条**没有后继标题、`end` 取到 EOF ⇒ **它天然少算这 3 行**，
    即同一份正文会因「后面有没有条目」而量出不同数值（位置相关）。
    ⇒ 实测陷阱（2026-09-15）：某条正文恰好卡在 60（以末条身份判绿），**在其后追加新条目即变 63 判红**，
    现象是「我没动它、它却红了」。**处置 = 对该条做等义压缩（不删事实），不要改本判据**
    （实现与上述 docstring 一致，属规格而非缺陷）；**追加新条目前先核上一条的余量 ≥ 3 行**。
    """
    out = []
    for p in kb_classified_files():
        lines = _read(p).splitlines()
        starts = [i for i, l in enumerate(lines) if re.match(r"^### KB-[A-Z]+-\d+", l)]
        for j, s in enumerate(starts):
            end = starts[j + 1] if j + 1 < len(starts) else len(lines)
            size = end - s
            if size <= KB_ENTRY_CAP:
                continue
            m = re.match(r"^### (KB-[A-Z]+-\d+)", lines[s])
            kb_id = m.group(1) if m else f"L{s + 1}"
            if f"{p.name}:{kb_id}" in TOLERATED_ENTRIES:
                continue
            out.append((p.name, kb_id, size))
    return out


# ---------------------------------------------------------------- H. 主张类条目缺「失效条件」
#
# 【为什么只扫这两册】KB 的价值分两类，对「失效条件」的需求完全不同：
#   ① **主张类**（对市场/现象的判断、规则、阈值）——它的价值就在「什么条件下不再成立」；
#      缺了这句，模型会把**示例当规律**引用（[[KB-DEC-018]] 同一病灶）。
#   ② **做法类**（工程/操作纪律、定义、框架）——条目**本身就是做法**，没有可证伪的命题，
#      强行要求「失效条件」只会催生凑数文字，反而稀释信噪比。
#   ⇒ 主张类恰好集中在 `01-stock-picking`（选股）与 `02-trading-lessons`（交易教训）两册；
#     其余册（决策/工程/文档治理/工具坑/验证坑/数据契约）以做法为主，**不纳入**。
#
# 【为什么以告警起步、且要豁免名单】2026-09-12 首次量测时，两册 44 条里仅 5 条带失效表述。
#   若直接立硬门，一上线就红 39 条，整份体检立刻失去信噪比，下场是被整体无视
#   （同「永不触发的门禁」与「过宽的触发等于没有触发」[[KB-ENG-58]]）。
#   ⇒ 先补存量（本轮已补 18 条）→ 再以 **WARN** 上线，只提示、不阻断；
#     同时用 **显式豁免名单**把「纪律 / 定义 / 框架」类条目排除在外。
CLAIM_KB_FILES = ("01-stock-picking.md", "02-trading-lessons.md")

#: 认定「已交代失效条件」的标记词。刻意放宽——只要作者**以任何形式**交代了边界即可，
#: 不强制统一措辞（`边界` / `不适用` / `未获支持` / `已否决` 都是合法交代）。
CLAIM_MARKERS = ("失效条件", "失效", "边界", "不适用", "未获支持", "已否决", "被取代", "局限")

#: 豁免名单：**做法 / 定义 / 框架**类条目——无可证伪命题，不要求「失效条件」。
#: ⚠️ 这份名单必须与条目**同时维护**：条目改名或删除后，ID 会变成幽灵豁免，
#:    因此下方 `check_claim_exempt_ids` 会反向断言「名单里的每个 ID 都真实存在」。
CLAIM_EXEMPT = {
    # —— 01-stock-picking.md ——
    "KB-STOCK-01": "📎 示例 + 已有实测否决结论（比一般失效条件更强）",
    "KB-STOCK-03": "📎 示例（与 02 同构，仅作类比，不另计证据）",
    "KB-STOCK-04": "📎 示例·历史参照（无对应实测）",
    "KB-STOCK-07": "纪律：发现与买入分离",
    "KB-STOCK-08": "纪律：必须有低风险参与档位",
    "KB-STOCK-10": "定义：梯队七角色",
    "KB-STOCK-13": "定义：强度四档",
    "KB-STOCK-16": "纪律：题材唯一归属",
    "KB-STOCK-18": "纪律：预判以实测为准",
    "KB-STOCK-19": "框架：稳定核 + 灵活壳",
    "KB-STOCK-21": "纪律：一字板参与",
    "KB-STOCK-25": "主张类，但已有「反例边界」段（标记词已覆盖）",
    "KB-STOCK-26": "框架：消息驱动五段流水线",
    "KB-STOCK-27": "已有「边界声明」段（标记词已覆盖）",
    "KB-STOCK-28": "方法论：策略生命周期管理",
    "KB-STOCK-29": "已有口径声明与「须样本外复验」标注",
    "KB-STOCK-30": "已有「裁决」与适用范围段",
    "KB-STOCK-31": "已有「已知边界 / 残余诚实口径」段",
    # —— 02-trading-lessons.md ——
    "KB-TRADE-01": "做法：先实测时间再判断",
    "KB-TRADE-02": "做法：日期源必须同源",
    "KB-TRADE-05": "做法：三态纪律",
    "KB-TRADE-06": "做法：验收以实际渲染为准",
    "KB-TRADE-07": "做法：盘点三原则",
    "KB-TRADE-09": "做法：瞬态状态不入异常白名单",
    "KB-TRADE-12": "做法：清除类操作用时间切线",
    "KB-TRADE-13": "做法：周度统计闭环（本身是待实现项）",
}


def check_claim_entries_missing_falsifier() -> list[tuple[str, str, str]]:
    """H 项（**告警级**）：主张类条目未交代「什么条件下不再成立」。

    只扫 `CLAIM_KB_FILES`，并按 `CLAIM_EXEMPT` 豁免做法/定义/框架类。
    返回 (文件, KB-ID, 标题) 列表——**不计入 problems**，见 main() 中的 WARN 处理。
    """
    out: list[tuple[str, str, str]] = []
    for name in CLAIM_KB_FILES:
        p = DOCS / "kb" / name
        if not p.exists():
            continue
        lines = _read(p).splitlines()
        starts = [i for i, l in enumerate(lines) if re.match(r"^### KB-[A-Z]+-\d+", l)]
        for j, s in enumerate(starts):
            end = starts[j + 1] if j + 1 < len(starts) else len(lines)
            m = re.match(r"^### (KB-[A-Z]+-\d+)\s+(.*)", lines[s])
            if not m:
                continue
            kid, title = m.group(1), m.group(2).strip()
            if kid in CLAIM_EXEMPT:
                continue
            body = "\n".join(lines[s:end])
            if not any(mk in body for mk in CLAIM_MARKERS):
                out.append((name, kid, title))
    return out


def check_claim_exempt_ids() -> list[str]:
    """H 的**守卫的守卫**：豁免名单里的 ID 必须真实存在，否则是幽灵豁免。

    为什么必须有这一条：豁免名单的价值全在「它排除的是真实条目」。条目被改名/删除后，
    名单不会报错，只会**静默失去作用**——而失去作用的方向恰好是**放宽**检查，
    症状又是「全绿」。这正是 KB-ENG-54「扫描面被写窄」的**名单版**。
    """
    alive: set[str] = set()
    for p in kb_classified_files():
        alive |= set(re.findall(r"^### (KB-[A-Z]+-\d+)", _read(p), re.M))
    return [kid for kid in CLAIM_EXEMPT if kid not in alive]


def kb_file_advisories() -> list[tuple[str, int]]:
    """§7：L0 文件总行数不限，>800 只是「评估按子类拆文件」的触发 → 非阻断提示。"""
    out = []
    for p in kb_classified_files():
        n = len(_read(p).splitlines())
        if n > KB_FILE_CAP:
            out.append((f"docs/kb/{p.name}", n))
    return out


def check_over_limit() -> list[tuple[str, int, int]]:
    out = []
    for p in sorted(DOCS.glob("*.md")):
        if p.name in TOLERATED:
            continue  # 已登记的容忍项
        n = len(_read(p).splitlines())
        cap = None
        if p.name in FRAMEWORK_FILES:
            cap = FRAMEWORK_CAP
        elif re.search(r"(review|audit|research|sweep|report)", p.stem) and re.search(r"\d{6,8}", p.name):
            cap = REPORT_CAP
        if cap and n > cap:
            out.append((f"docs/{p.name}", n, cap))
    return out


def check_missing_abstract() -> list[tuple[str, int]]:
    out = []
    for p in sorted(DOCS.rglob("*.md")):
        rel = p.relative_to(DOCS)
        if "archive" in rel.parts:
            continue
        if rel.parts and rel.parts[0] in L4_DIRS:
            continue  # L4 时间序列按时间消费，豁免
        if rel.parts and rel.parts[0] == "kb" and p.name != "00-INDEX.md":
            continue  # KB 分类文件用条目级约束（§7 表），豁免文件级摘要
        lines = _read(p).splitlines()
        if len(lines) <= 150:
            continue
        head = "\n".join(lines[:40])
        if not any(k in head for k in ABSTRACT_MARKERS):
            out.append((f"docs/{rel}", len(lines)))
    return out


def check_clusters() -> list[tuple[str, int]]:
    stems = sorted(p.stem for p in DOCS.glob("*.md") if p.name != "INDEX.md")
    buckets: dict[str, list[str]] = {}
    for s in stems:
        buckets.setdefault(s.split("-")[0], []).append(s)
    return [(k, len(v)) for k, v in sorted(buckets.items()) if len(v) >= 3]


def _strip_fences(lines: list[str]) -> list[str]:
    """围栏代码块内的行替换成哨兵——否则代码示例里的 `# 注释` 会被当成标题。"""
    out: list[str] = []
    infence = False
    for ln in lines:
        if ln.strip().startswith(("```", "~~~")):
            infence = not infence
            out.append(_FENCE)
            continue
        out.append(_FENCE if infence else ln)
    return out


def check_empty_sections() -> tuple[list[tuple[str, int, str]], list[tuple[str, str]]]:
    """I 空章节：**标题在、正文 0 行**。

    为什么单列一项：这是「**内容被搬错位置**」的典型形态，而它发生时其余检查**全绿**——
    `docs/retro-and-gaps.md` 的 §6.6 正文曾被 §6.7 的标题截断、落到 6.7 的表下方，
    表现为「6.6 标题空着、正文挂在别的章节里」；按目录点进去只有一行标题，
    读者既看不到内容、也无从判断是"还没写"还是"写丢了"。

    判据：本标题之后、到**下一个同级或更高级标题**之前，没有任何正文行。
    ⚠️ **不是**「到下一个任意标题之前」——`## 7` 紧跟 `### 7.1` 是合法容器写法，
    按后者判会把所有"带子标题的父章节"全部误报成空章节。

    返回 `(hits, ghost)`：`ghost` 是已失效的容忍项（标题被改 / 文件被搬），
    反向断言避免容忍名单像 `CLAIM_EXEMPT` 那样**静默失效**后仍被当作有效。
    """
    hits: list[tuple[str, int, str]] = []
    used: set[tuple[str, str]] = set()
    targets: list[tuple[str, Path]] = [
        (f"docs/{p.relative_to(DOCS)}", p)
        for p in sorted(DOCS.rglob("*.md"))
        if "archive" not in p.relative_to(DOCS).parts
    ]
    targets += [(n, ROOT / n) for n in EMPTY_SECTION_ROOT_FILES if (ROOT / n).exists()]
    for rel, p in targets:
        lines = _strip_fences(_read(p).splitlines())
        heads = [
            (i, len(m.group(1)), m.group(2))
            for i, ln in enumerate(lines)
            if ln != _FENCE and (m := HEADING_RE.match(ln))
        ]
        for k, (i, lvl, txt) in enumerate(heads):
            j = next((h[0] for h in heads[k + 1:] if h[1] <= lvl), len(lines))
            body = lines[i + 1:j]
            if any(
                b.strip() and b.strip() not in _HR_LINES and not b.strip().startswith("<!--")
                for b in body
            ):
                continue
            key = (rel, txt)
            if key in EMPTY_SECTION_ALLOW:
                used.add(key)
                continue
            hits.append((rel, i + 1, txt))
    ghost = sorted(
        f"{f} → {t}（原登记理由：{EMPTY_SECTION_ALLOW[(f, t)]}）"
        for f, t in (set(EMPTY_SECTION_ALLOW) - used)
    )
    return hits, ghost


# ---------------------------------------------------------------- J. 文档代码锚点
#
#: 参与判定的后缀：**只认源码/配置**。数据产物（json/jsonl/parquet/duckdb）刻意排除 ——
#: 它们是 gitignored 运行时文件，「本地在、CI 不在」，纳入会造成 CI 假红（KB-ENG-57）。
ANCHOR_SUFFIX = r"(?:py|ts|tsx|js|mjs|sh|yml|yaml|toml|html|css|plist)"
#: J1 行内锚点：反引号里的路径/文件名
ANCHOR_INLINE_RE = re.compile(rf"`([\w./\-]+\.{ANCHOR_SUFFIX})`")
#: J2 树锚点：围栏代码块里目录树行的**末段文件名**（树靠缩进表达层级，不还原完整路径）
ANCHOR_TREE_RE = re.compile(rf"^[\s│├└─+`|]*([\w.\-]+\.{ANCHOR_SUFFIX})\s*(?:#.*)?$")
#: 记录性引用标记（与 RECORD_MARKERS 同族，按锚点面增补"计划/迁出/移除"等状态词）
ANCHOR_RECORD_MARKERS = RECORD_MARKERS + (
    "退役", "已删", "迁出", "移除", "从未", "计划", "规划", "拟建", "不再",
    # `~~` = markdown 删除线，本仓用它标「已完成 / 已作废」的**路线条目**
    # （范本 `~~**Phase 5 选股器 + 评分系统**~~ ✅ 已完成（2026-08-30）：… `screener_service.py`…`）。
    # 这类行是 **changelog**：与 `ANCHOR_SKIP_FILES` 排除账本同属「对 changelog 做存在性
    # 对账属范畴错误」——写"那天建了 X"，X 后来被删，历史依然为真。
    # ⚠️ **实测边界（2026-09-12）**：该标记**恰好且仅**移除 1 条命中（`docs/PROJECT-MASTER.md:371`
    # 那条 08-30 路线，其中 `screener_service.py` 09-01 已彻底删除）；另测 `✅` 与 `已完成`
    # **无任何额外收益** ⇒ 按最小改动**只收 `~~`**。少收一个标记 = 少一处未来的假阴性。
    "~~")
#: 占位/示例名（不是真引用）。⚠️ **必须按"整段词"判**，不能用 `(?:^|/)a+` 这类
#: 开放正则——它会把**所有 `app/...` 锚点**当成占位名静默跳过（实测：J 项的行内
#: 覆盖一度等于失效，靠自证测试抓出）。判据 = 末段词干整体落在集合内。
ANCHOR_PLACEHOLDER_STEMS = frozenset({
    "foo", "bar", "baz", "tmp", "temp", "example", "sample", "demo",
    "x", "xx", "xxx", "xxxx", "a", "aa", "nn", "n", "当天", "yyyymmdd"})
ANCHOR_PLACEHOLDER_RE = re.compile(r"^(?:x+|a+|n+|y{4}[\w-]*)$", re.I)
#: 视为「仓库相对路径首段」的目录；**不在其中的含 `/` 锚点**视为外部路径或文档自造简写，不判
ANCHOR_ROOTS = ("app", "backend", "apps", "scripts", "docs", "tests", "data", "skills")
#: 全仓文件名清点时剪掉的目录（`rglob` 会钻进 node_modules/.next 造成秒级开销）
#: ⚠️ **只在 `_tracked_basenames()` 失败的回退路径上生效**——正常路径走 git 跟踪清单，
#: 而跟踪清单天然不含这些目录。**不要再往这里补目录来修"本地绿 / CI 红"**
#: （那是枚举，治不了根；理由见 `_repo_basenames` docstring）。
ANCHOR_SKIP_DIRS = frozenset({
    "node_modules", ".next", ".turbo", "__pycache__", ".venv", ".venv-research", "dist", "build", ".git"})
#: 排除的文档位（**口径**，见 `check_doc_anchors` docstring 末段）
ANCHOR_SKIP_DIR_PARTS = ("archive", "trash", "daily-review")
ANCHOR_SKIP_FILES = ("docs/retro-and-gaps.md",)
#: 已登记容忍项（键 = 相对路径 + 完整锚点）。空 = 上线实测 0 误报；机制保留供后续登记。
#: ⚠️ 键用**完整锚点**而非截断，否则同一锚点会既进 hits 又进 ghost（F-4 踩过）。
ANCHOR_ALLOW: dict[tuple[str, str], str] = {
    # J 项判的是「点名的资产**还在不在**」，而这里点名的恰恰是**已处置物**——属误报面。
    # 该行是 KB-ENG-62 的**处置记录**（2026-09-12 裁定 B）：独立复盘通道已拆除
    # （`bootout` + 安装位 plist 与 `run_review.sh` 等四文件入回收站，能力由既有
    # `review-scheduler` 承担）。**不选"改文档"**：那次拆除就是该条教训的本体，
    # 点名文件是必要信息，改掉反而削弱记录（拆除叙述本就属 J 的"在案引述"范畴）。
    # ⚠️ 也**不选**往 `ANCHOR_RECORD_MARKERS` 加"回收站"：标记是全行级宽口径，
    # 会连带豁免"规则类"行（如"禁止把 `app/x.py` 放进回收站"），制造新的假阴性。
    ("docs/kb/00-INDEX.md", "run_review.sh"):
        "KB-ENG-62 处置记录：2026-09-12 已随独立通道拆除入回收站，是在案叙述而非在用资产",
    # 同一条教训在 KB 正文里的复述（[[KB-ENG-70]] 的"成因 2"）：该条目**必须**点名它，
    # 否则读者无法核验"文档点名的资产已处置"这件事。键是 `(文件, 锚点)` 而非行号 ⇒
    # 一处登记覆盖该文件内全部提及（此处 2 行）。
    ("docs/kb/09-verification-pitfalls.md", "run_review.sh"):
        "KB-ENG-70 成因叙述：该脚本已随拆除入回收站，本条目的主题正是「它为何会被误报」",
}


def _tracked_paths() -> set[str] | None:
    """**git 跟踪**的相对路径集合 = **CI 检出里存在的路径**；非 git 环境返回 `None`。

    先验 `rev-parse --show-toplevel == ROOT`：只有在 `ROOT` 本身就是仓库根时才认，
    避免"`ROOT` 恰好落在别的仓库内、拿到隔壁仓库的文件清单"这种静默错面。
    """
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)
        if Path(top.stdout.strip()).resolve() != ROOT.resolve():
            return None
        ls = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT, capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    paths = {p for p in ls.stdout.decode("utf-8", "replace").split("\0") if p}
    return paths or None


def _tracked_basenames() -> set[str] | None:
    """**git 跟踪**文件名集合；`ROOT` 不是 git 仓库根时返回 `None`（调用方回退 `os.walk`）。

    判定面的理由见 `_repo_basenames` 的 docstring（**门禁的判定面必须等于 CI 的检出内容**）。
    """
    paths = _tracked_paths()
    return {Path(p).name for p in paths} if paths else None


def _tracked_tops() -> set[str] | None:
    """CI 检出里**可能存在**的顶层条目名（路径首段）。`None` = 无 git 环境（合成树单测）。"""
    paths = _tracked_paths()
    return {p.split("/", 1)[0] for p in paths} if paths else None


def _tracked_dirs() -> set[str] | None:
    """CI 检出里**存在**的目录集合（含全部祖先）。`None` = 无 git 环境（合成树单测）。"""
    paths = _tracked_paths()
    if paths is None:
        return None
    dirs: set[str] = set()
    for p in paths:
        parts = p.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    return dirs


def _in_checkout_universe(tok: str, tops: set[str] | None, dirs: set[str] | None) -> bool:
    """该路径**能不能被仓库证明**？不能就不判它（判了就是"本地绿 / CI 红"）。

    为什么必须有这条（2026-09-15 实测）：`.workbuddy/` 是 **gitignored 的工作区目录**
    （`git ls-files .workbuddy` = **0 条**）⇒ CI 检出里根本没有这棵树，而本地有。
    于是 N（索引指针）/ O（编目闭包）**本地恒绿、CI 恒红**：CI 上实测 `N 14 处指针失效`
    + `O 幽灵条目 5 条`（全是 `.workbuddy/**`），而后端与前端 job 全绿 ⇒ 正是
    `_repo_basenames` 早就写明的通例被违反（**判定面必须等于检出内容**）。

    判据取**两级**，缺一不可（单用任一级都实测出误报）：
      ① **顶层段在检出里**（`.workbuddy` ∉ ⇒ 整棵树都不判）；
      ② **父目录在检出里**（`data/picks/x.md`：`data` 在、但 `data/picks` 不在
         —— 它是 gitignored 的运行产物目录 ⇒ 不判；而 `docs/kb/xxx.md` 的父目录
         `docs/kb` 在 ⇒ **照判**，于是"指针指向已删的文档"这个主用途不受影响）。
         ⚠️ 这里的示例名必须用占位词（`xxx`）：写真实形态会被 **F 项**当成代码注释里的
         死引用判红（2026-09-15 实测，写了字面量示例当场红 1 处）。
      仓库根下的裸名（`AGENTS.md` 等）父目录即根 ⇒ 一律判。
    `tops/dirs = None`（无 git 环境，如合成树单测）⇒ 返回 True ⇒ 退回文件系统口径。
    """
    if tops is None or dirs is None:
        return True
    t = tok.strip("/")
    if t.split("/", 1)[0] not in tops:
        return False
    if "/" not in t:
        return True          # 仓库根下的裸名（AGENTS.md / CONTEXT.md / .env.example …）
    return t.rsplit("/", 1)[0] in dirs


def _repo_basenames() -> set[str]:
    """全仓文件名集合 —— **判定面 = git 跟踪清单**（即 CI 检出内容），非 git 才回退 `os.walk`。

    为什么不用 `os.walk` 直接当判定面（2026-09-12 定位的真因，见 KB-ENG-70）：

    这条检查问的是「文档点名的代码路径**在仓库里**还在不在」，而 **CI 检出的只有 git
    跟踪文件**。用 `os.walk` 会把**本地独有**的文件一并算作"存在"——本仓实测
    `os.walk` 清点 10409 个 basename，`git ls-files` 只有 999 个，其中**后缀落在
    `ANCHOR_SUFFIX` 内、因而真能"伪造通过"的有 7 个**（`run_review.sh` 等）。

    致命之处在于它与本仓的**删除纪律互相削弱**：文件处置一律先进
    `.workbuddy/trash/`（用户要求，禁止 `rm`），而该目录是 gitignored、`os.walk`
    却照走 ⇒ **回收站里的副本持续把死引用"喂绿"** ⇒ 本地全绿、CI 假红，且
    `test_real_repo_has_no_dead_doc_anchor` 这条守卫**在本地是瞎的**（实测：
    `docs/kb/00-INDEX.md:192 → run_review.sh` 被回收站副本完整遮盖）。

    ⇒ 通例：**门禁的判定面必须等于 CI 的检出内容**。靠逐个往 `ANCHOR_SKIP_DIRS`
    补目录（补 `.workbuddy`，明天再补 `data/`、`logs/`…）治不了根——那是枚举，
    而"跟踪清单"是**定义**。
    """
    tracked = _tracked_basenames()
    if tracked is not None:
        return tracked
    out: set[str] = set()          # 回退：无 git 环境（打包件 / 沙箱）
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in ANCHOR_SKIP_DIRS]
        out.update(filenames)
    return out


def _is_placeholder(cand: str) -> bool:
    """占位/示例名：`foo.py` / `xxx.md` / `NN-x.md` / `当天.md`。

    判据走**末段词干**（去掉扩展名）而不是在整条路径上开放匹配——后者会误吞
    真实路径（见 `ANCHOR_PLACEHOLDER_STEMS` 上方注释）。
    """
    stem = cand.split("/")[-1].split(".")[0]
    return stem.lower() in ANCHOR_PLACEHOLDER_STEMS or bool(ANCHOR_PLACEHOLDER_RE.match(stem))


def _is_repo_path(cand: str) -> bool:
    """行内锚点是否**值得判**：HTTP 路径 / 外部域名 / 非仓库首段 一律放过。

    放过（返回 False）≠ 通过，而是**不参与判定**——它们不是"文档对仓库代码的点名"。
    实例：`/openapi.json`（HTTP）、`d.10jqka.com.cn/...`（外部站）、`kb/NN-x.md`（自造占位）。
    """
    if cand.startswith("/"):
        return False
    head = cand.split("/")[0]
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", head) or re.match(r"^[a-z0-9-]+\.[a-z]{2,}$", head):
        return False
    return "/" not in cand or head in ANCHOR_ROOTS


def check_doc_anchors() -> tuple[list[tuple[str, int, str, str]], list[str]]:
    """J 文档代码锚点：文档点名的**仓库代码路径必须存在**。

    为什么有它（F-10，2026-09-12）：§6.12 归纳出「设计文档能力失真」的根因是
    **只加不改、无机制防止**。本轮先量了三个原型才定档，**两个宽口径实测否决**：

    · **v1 句级共现**（"未实现/不存在"同句 + 反引号锚点）：66 + 40 条，真阳性≈0。
      败因：把"锚点"与"断言"错误配对 —— 实例 `Auditor` 不存在（仅 `risk/engine.py` 模块头…）
      一句里两个锚点**真值相反**；`out` / `retro` / `status` 这类 2~5 字符弱锚点满地都是。
    · **v2 收窄后**（强标识符 + ±16 字邻近配对 + 排除已更正行）：2 + 3 条，仍近乎全假。
      败因是**根本性的**：断言的宾语常是**中文子能力**（「禁止交易名单」「复权方式切换」），
      而可机械核验的只有它**所在的文件** ⇒ **语义不可配对**，不是调参能解决的。
    · ⇒ 结论：**「文档对某能力的描述是否过时」不可自动对账**，不设门禁（KB-ENG-68）。

    但 v3 找到一个**判据零歧义**的可判子集：**点名的代码路径是否还在**。
    实测 459 个行内锚点 / 3 处失败，扩到**围栏代码块里的目录树行**后共 **4 处真漂移**、
    **0 误报**，且全部集中在权威架构文档 `docs/PROJECT-MASTER.md`：
    `screener.py` / `screener_service.py`（09-01 选股器彻底删除）、`predict.py`（09-08 P0-4）、
    `minute_backtest.py`（09-08 P0-3 随唯一消费方删除）——**死于模块被删，树却没人改**。

    上线后修掉一处**自身缺陷**（占位名正则吞掉所有 `app/...` 锚点，见
    `ANCHOR_PLACEHOLDER_STEMS` 注释），行内覆盖**恢复真实**后又抓出 **3 处同类漂移**：
    `PROJECT-MASTER.md:371`（08-30 路线条目点名已删的 `screener_service.py`——属 changelog，
    已由 `~~` 标记排除）、`docs/factor-lifecycle-governance.md:5`（把已迁出为纯文档的
    `candidates.py` 仍列为代码资产）、同文件 `:175`（**把计划写成既成事实**：点名
    `app/factors/evaluate_event.py`，实测该文件**全仓零引用、从未存在**，且该待办
    **在账本里没有任何出口**——违反「待办必须有出口」[[KB-DEC-020]]，已在 §6.2 登记 P1-42）。
    ⇒ **"修好守卫后召回变真，又冒出新命中"是正常顺序**，不要反过来当成误报证据。

    口径（三处刻意选择，都是为了**零误报优先**）：
    · **只认"点名"**：锚点形式限定为反引号路径与树行末段；不做"清单完整性"校验
      （树靠缩进表达层级，不还原完整路径；缺项方向无声明式依据可判）。
    · **只认源码/配置后缀**：`py/ts/tsx/js/mjs/sh/yml/yaml/toml/html/css/plist`。
      **刻意排除数据产物**（`json/jsonl/parquet/duckdb`）——它们是 gitignored 运行时文件，
      **本地在、CI 不在**，纳入会让门禁在 CI 上假红（[[KB-ENG-57]] 同族）。
    · **排除账本与逐日复盘**：`docs/retro-and-gaps.md` 是**变更叙述 + 计划登记**
      （P1 行点名"计划中"的模块是合法写法），`docs/daily-review/` 是 L4 历史快照。
      对 changelog 做存在性对账属**范畴错误**（v1 的 47/66 条命中全部来自账本）。
      同名裸锚另有 F3/F4 覆盖。
      **粒度修正（2026-09-12）**：该排除原先**按文件**实现，而 `docs/PROJECT-MASTER.md`
      是**混合体裁**文档——既有权威结构树（必须判），也有「近期路线」changelog 段
      （不该判）⇒ **文件级代理在它身上失效**，于是改为**行级**：含 `~~` 删除线的行
      视为 changelog 条目（适用边界见 `ANCHOR_RECORD_MARKERS` 的实测注释）。
      ⇒ 通例：**排除规则要按"内容性质"实现，不要按"文件身份"实现**，否则遇到
      混合体裁文件不是误报、就是漏报。

    返回 `(hits, ghost)`：`hits` = `(相对路径, 行号, 锚点, 行文摘要)`；
    `ghost` = 已失效的容忍项（**反向断言**，防容忍名单像 `CLAIM_EXEMPT` 那样静默腐烂）。
    """
    basenames = _repo_basenames()
    hits: list[tuple[str, int, str, str]] = []
    used: set[tuple[str, str]] = set()
    for p in sorted(DOCS.rglob("*.md")):
        rel = str(p.relative_to(ROOT))
        if any(d in p.relative_to(DOCS).parts for d in ANCHOR_SKIP_DIR_PARTS):
            continue
        if rel in ANCHOR_SKIP_FILES:
            continue
        infence = False
        for i, line in enumerate(_read(p).splitlines(), 1):
            if line.lstrip().startswith(("```", "~~~")):
                infence = not infence
                continue
            if any(m in line for m in ANCHOR_RECORD_MARKERS):
                continue          # 记录性引用：在案引述"曾存在/曾不存在"，不算失真
            cands = [(m.group(1), "inline") for m in ANCHOR_INLINE_RE.finditer(line)]
            if infence:
                # 树锚点**只在围栏内认**：正文里的 `├── x.py` 是插画/引用，不是结构声明。
                cands += [(m.group(1), "tree") for m in ANCHOR_TREE_RE.finditer(line.rstrip())]
            for cand, kind in cands:
                if _is_placeholder(cand):
                    continue
                if kind == "inline" and not _is_repo_path(cand):
                    continue
                if cand.split("/")[-1] in basenames:
                    continue
                key = (rel, cand)
                if key in ANCHOR_ALLOW:
                    used.add(key)
                    continue
                hits.append((rel, i, cand, line.strip()[:96]))
    ghost = sorted(
        f"{f} → {a}（原登记理由：{ANCHOR_ALLOW[(f, a)]}）" for f, a in (set(ANCHOR_ALLOW) - used)
    )
    return hits, ghost


# ---------------------------------------------------------------- K. 表格分隔行


def _is_table_delim(line: str) -> bool:
    """GFM 分隔行：只由 `|` `-` `:` 与空白组成，且**同时**含 `|` 与 `-`。

    两个"必须有"都不是装饰：`| |`（两列皆空）不是分隔行；`---` 是**水平分隔线**
    （`_HR_LINES`），不是表分隔行——少了 `|` 这一条，`---` 之后的块首行会被误判为通过。
    """
    s = line.strip()
    return bool(s) and "|" in s and "-" in s and set(s) <= set("|-: ")


def check_table_delimiters() -> list[tuple[str, int, str]]:
    """K 表格分隔行：以 `|` 开头的行块，其**首行的下一行必须是分隔行**。

    动机（2026-09-12 §6.14 事故）：自研 Markdown 渲染器曾因「以 `|` 开头却不构成表格的行」
    走进**零消费死循环**，把知识库面板整页卡死——而单测 / `tsc` / eslint **全绿照不出**。
    事后两条修法缺一不可：①渲染器兜底改 `do...while`（**无条件消费一行**）；
    ②**文档改合法 GFM**（`docs/` 下 4 份 / 49 行曾用**空行给同一张表分组**，严格 GFM 下必然打碎）。
    ①已有 `components/agent/markdown-view.test.tsx` 守住；**②此前没有任何机制**——
    这类书写不会让任何既有检查变红，只能靠人记得 ⇒ 本项补的就是这个缺口。

    判据（只判**结构**，零歧义）：`|` 起始的**行块首行**，其下一行不是分隔行 ⇒ 命中。
      · 正常表头 = 首行 + 分隔行 ⇒ 通过；
      · **空行分组后**那批 `|` 行 = 新块首行但下一行是数据行 ⇒ 命中（正是事故形态）；
      · 表头忘写分隔行 ⇒ 命中。
    排除：**围栏代码块内**（`_strip_fences` 置哨兵 ⇒ 目录树 / 示例里的 `|` 不参与）。

    **为什么敢设硬门（先量噪声再定档）**：上线前实测活文档面
    **292 个 `|` 起始块全部是合法表头、0 处可疑**（docs 287 + AGENTS.md 4 + README.md 1）
    ⇒ 判据在全部现有数据上**零假阳性**，不会出现「一上线就红满天 ⇒ 被整体无视」（KB-ENG-58）。
    注入验证见 `tests/test_doc_health_tables.py`：合成「空行分组 + 表头缺分隔行」⇒ 精确 2 处命中；
    围栏内与行内管道符均不误伤。

    局限（诚实声明）：只判**结构**，不判列数一致性 / 内容；也不覆盖 `|` 不出现在行首的变体写法
    （本仓统一行首带 `|`）。若将来出现**合法的非表格 `|` 块**（如 ASCII 图），
    正解是给它补表头或放进围栏——**不要为它放宽判据**。
    """
    hits: list[tuple[str, int, str]] = []
    targets: list[tuple[str, Path]] = [
        (f"docs/{p.relative_to(DOCS)}", p)
        for p in sorted(DOCS.rglob("*.md"))
        if "archive" not in p.relative_to(DOCS).parts
    ]
    targets += [(n, ROOT / n) for n in EMPTY_SECTION_ROOT_FILES if (ROOT / n).exists()]
    for rel, p in targets:
        lines = _strip_fences(_read(p).splitlines())
        for i, ln in enumerate(lines):
            if not ln.strip().startswith("|"):
                continue
            if i > 0 and lines[i - 1].strip().startswith("|"):
                continue                      # 非块首行 ⇒ 已由块首行代表
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if _is_table_delim(nxt):
                continue
            hits.append((rel, i + 1, ln.strip()[:80]))
    return hits


# ---------------------------------------------------------------------------
# L / M 项：任务清单的**唯一性**与**指针完备性**（2026-09-14 · kb/07 §3.3 的机制化）
#
# 动机：用户 2026-09-14 确立「全仓任务一律登记账本 §6.0，单文档不得各自维护任务清单」。
# 规则若只写在 md 里就是摆设（kb/07 §9 自我淘汰条款）⇒ 固化为两项机检。
# 上游教训：当日两轮全仓复查**共漏登记 42 项**，成因是「按来源判覆盖面」+「单一指针」。
# ---------------------------------------------------------------------------

#: 统一任务 ID 形态（定义于账本 §6.0，规则见 kb/07 §3.3 ①）。
TASK_ID_RE = re.compile(r"\b(?:BUG|IMP|RSH|GOV|OPS)-\d{3}\b")
TASK_SEC_START = re.compile(r"^### 6\.0 ", re.M)
TASK_SEC_END = re.compile(r"^### 6\.1 ", re.M)


def active_md_targets() -> list[Path]:
    """活文档面 = `SCAN_FILES` + `docs/` 下非 archive / 非 L4 的 `*.md`。

    ⚠️ **刻意排除 `.workbuddy/`**：那里的 reports / artifacts 是**过程快照**（一次性），
    其未完成项已归集账本；要求快照加指针等于**改写历史**（同 `archive/` 只读原则）。
    新报告若含任务清单，靠 kb/07 §3.3 ③ 的执行点保证（**新增当轮登记**）。
    """
    out = [ROOT / f for f in SCAN_FILES]
    for p in sorted(DOCS.rglob("*.md")):
        rel = p.relative_to(DOCS)
        if "archive" in rel.parts:
            continue
        if rel.parts and rel.parts[0] in L4_DIRS:
            continue
        out.append(p)
    return out


def ledger_task_ids() -> set[str]:
    """账本 §6.0 区段中**已定义**的任务 ID 集合。

    ⚠️ 区段找不到时返回**空集**（fail-loud）：届时所有 ID 引用都会被判红，逼人先修账本结构——
    **而不是静默放行**（[[KB-ENG-72]]：守卫覆盖面失效比误报危险得多）。
    """
    txt = _read(DOCS / "retro-and-gaps.md")
    m = TASK_SEC_START.search(txt)
    if not m:
        return set()
    rest = txt[m.end():]
    e = TASK_SEC_END.search(rest)
    return set(TASK_ID_RE.findall(rest[: e.start()] if e else rest))


#: L 项的已登记例外：`(文件, ID) → 理由`。**必须写明理由**（同 `CODE_REF_ALLOW` 的做法）。
#: 目前两处：讲 L 项判据自身时举的**示例 ID**——它不是对任务的引用，而是**判据的说明材料**
#: （`\d{3}` 之外无法用形态区分"示例"与"真引用"，故显式留痕而非把扫描放宽）。
TASK_ID_ALLOW: dict[tuple[str, str], str] = {
    ("docs/kb/07-doc-curation.md", "BUG-999"):
        "L 项判据文档里的**示例文本**（举一个未定义 ID 说明判据会精确判红），非任务引用",
    ("docs/handoff.md", "OPS-999"):
        "`GOV-014` 条目里**注入自证**用的幽灵 ID（说明 P 项能抓到「索引有、条目无」），非任务引用",
}


def check_task_ids_defined() -> list[tuple[str, int, str]]:
    """L 项：文档里引用的任务 ID 必须在账本 §6.0 **查得到**——否则就是失效指针。

    为什么需要：ID 一旦被改名/删除，旧引用会**静默指向另一个任务或空处**
    （本仓已踩过：`B`/`C`/`F` 三字母各有 2–3 种含义，跨文档引用必然错引）。
    """
    defined = ledger_task_ids()
    out: list[tuple[str, int, str]] = []
    for t in active_md_targets():
        if not t.exists():
            continue
        rel = str(t.relative_to(ROOT))
        for ln, line in enumerate(_read(t).splitlines(), 1):
            for tid in TASK_ID_RE.findall(line):
                if tid in defined or (rel, tid) in TASK_ID_ALLOW:
                    continue
                out.append((rel, ln, tid))
    return out


#: M 项判据：文档里出现这类**标题**即视为「承载任务清单」，必须带归集指针。
#: 收紧依据（2026-09-14 实测扫描面 8 份命中，其中 3 份误报，逐条排除）：
#:  · `(?!KB-)` —— KB 条目标题里的「待办」是**知识命名**（如 KB-DEC-020「待办必须有出口」），不是清单；
#:  · 排除标题含「已实施 / 已交付 / 已闭环 / 已完成 / 已销账」——那是**结果记录**，不是待办
#:    （如 `architecture-design.md` §2「资金流三级重构——**P0 已实施**」）。
TASK_CARRIER_HEAD_RE = re.compile(
    r"^#{2,4}\s+(?!KB-)"
    r"(?![^\n]*(?:已实施|已交付|已闭环|已完成|已销账))"
    r"[^\n]*(?:待办|未做|待实施|待完成|TODO|行动项|任务清单|后续规划|待决)"
)

#: 归集指针标记：**必须出现在任务清单标题行或其下 3 行内**（见 check 函数）。
TASK_CARRIER_POINTER = "§6.0"

#: 指针的**就近窗口**：标题行 + 往下 3 行。
#: ⚠️ 为什么必须靠近（2026-09-14 注入验证暴露的盲区）：初版判据是「文档**任意位置**含 §6.0
#: ⇒ 整篇豁免」，结果文档末尾一句「新增待办请直接登记账本 §6.0」就把整篇洗白了——
#: 注入 I2（删掉标题里的指针）**未变红**。⇒ 判据必须锚定在**清单所在处**，
#: 而不是「文档提到过 §6.0」（[[KB-ENG-72]]：守卫全绿 ≠ 判定面完整；**注入后仍全绿先怀疑判据**）。
TASK_CARRIER_WINDOW = 4

#: 已登记例外（确属误报或按只读原则豁免）——**必须写明理由**（同 `CODE_REF_ALLOW` 的做法：
#: 显式留痕而非静默排除）。
TASK_CARRIER_ALLOW: dict[tuple[str, str], str] = {}

#: M 项扫描面的**结构性排除**——不是"这次算了"，而是这两份**在定义上**就不该被本项要求
#: （排除须有理由，同 `LEGACY_SLUG_SKIP_FILES` / `ANCHOR_SKIP_FILES` 的做法）：
#:  · `docs/retro-and-gaps.md` —— **它就是清单本体**（§6.0 正是被指向的目标），要求它指向自己无意义；
#:  · `docs/kb/07-doc-curation.md` —— **它就是本条规则的正文**（§3.3），其标题里出现「任务清单」
#:    是在**定义规则**，不是在承载清单。
TASK_CARRIER_SKIP_FILES = frozenset({
    "docs/retro-and-gaps.md",
    "docs/kb/07-doc-curation.md",
})


def check_task_carrier_pointers() -> list[tuple[str, int, str]]:
    """M 项：承载任务清单的**活文档**必须带「归集至账本 §6.0」的指针。

    为什么需要：**任务分散在多份文档正是 2026-09-14 本条规则的起因**——
    实测 5 份文档各有自己的待办清单（`summary/` 3 份 + `kb/11` + `AGENTS.md`），
    与账本并存 ⇒ 两处维护、必有一处滞后。规则只写 md 里无机制承载（kb/07 §9）。
    """
    out: list[tuple[str, int, str]] = []
    for t in active_md_targets():
        if not t.exists():
            continue
        rel = str(t.relative_to(ROOT))
        if rel in TASK_CARRIER_SKIP_FILES:
            continue
        lines = _read(t).splitlines()
        for ln, line in enumerate(lines, 1):
            if not TASK_CARRIER_HEAD_RE.match(line):
                continue
            if (rel, line.strip()) in TASK_CARRIER_ALLOW:
                continue
            window = lines[ln - 1: ln - 1 + TASK_CARRIER_WINDOW]
            if any(TASK_CARRIER_POINTER in w for w in window):
                continue
            out.append((rel, ln, line.strip()))
    return out


# ---------------------------------------------------------------------------
# N 项：**记忆索引自身**的体检（2026-09-14 · kb/07 §4.4 的机制化）
#
# 动机（用户 2026-09-14 指令）：*「让 memory 只作为索引器和入口指引，不再承担完整内容存储，
# 从而避免经常超出限制。」* 规则若只写在 md 里就是摆设（kb/07 §9 自我淘汰条款）⇒ 固化为机检。
# 两层防线缺一不可：
#   ① **体积上限**——体积是"是否在存内容"的**代理判据**。健康的索引只会因「主题变多」而变长，
#      不会因「某个主题的内容变多」而变长；后者必是内容混入（§4.4 ③ 反固化条款）。
#   ② **指针闭包**——B 项只认 `docs/**.md` 形态，**索引里指向 `.workbuddy/`、仓库根文件、
#      代码路径的指针它一条都扫不到**；而索引烂掉的主要方式恰恰是这些指针失效（文件搬家/改名）。
# ---------------------------------------------------------------------------

#: 入口索引面：会话入口（`MEMORY.md`）+ 它的**展开版**（`docs/INDEX.md` §0 主题路由）。
#: 二者共用一套指针判据。
#: ⚠️ 2026-09-14 记录：展开版曾短暂另立为 `kb/12-topic-router.md`，因与 `INDEX.md` §0
#: 回答的是同一个问题（「我要查 X → 去 Y」）而被否决、已并入 `INDEX.md` §0 并撤销
#: ⇒ 本项**不要**再去索引第三个文件：**索引面越宽，越容易把重复当成互补**（`kb/07` §4.1）。
INDEX_FILES = (".workbuddy/memory/MEMORY.md", "docs/INDEX.md")

#: **字符数上限**，只约束**入口** MEMORY.md。
#: 依据：平台对 MEMORY.md 的会话配额（3000 chars）；重构后实测 ≈1900 字符 ⇒ 留有余量。
#: 展开版 `docs/INDEX.md` 的长度归 `kb/07` §7 的层配额，**不套用本条**。
INDEX_CHAR_CAP = 3000
INDEX_CHAR_CAP_FILE = ".workbuddy/memory/MEMORY.md"

#: 目录式引用的允许前缀（避免把 `kb/`、`docs/kb/` 这类简写当成待验目录）。
INDEX_DIR_PREFIXES = (".workbuddy/", "scripts/", "apps/", "backend/", "data/", "skills/")

#: 带扩展名的路径片段。`docs/` 开头的**刻意排除**——B 项已覆盖，两处重复会造出"两套口径"。
INDEX_PTR_RE = re.compile(
    r"(?<![\w/.\-])([.\w\-][\w\-./]*\.(?:md|py|ts|tsx|js|mjs|json|sh|css|html|ya?ml|toml))")
#: 目录式引用：`…/xxx/`（只认白名单前缀，防止把普通词组当路径）。
#: 尾部负向前瞻 `(?![\w\-./])` 不是装饰：少了它，`.workbuddy/memory/YYYY-MM-DD.md`
#: 会被截成 `.workbuddy/memory/` —— 一个**被凭空造出来的目录指针**（且恰好是合法的），
#: 于是占位名豁免失效、判据开始验一个从没被写过的东西。
INDEX_DIR_RE = re.compile(
    r"(?<![\w/.\-])((?:\.workbuddy|scripts|apps|backend|data|skills)/[\w\-./]*/)(?![\w\-./])")


def index_pointer_candidates(line: str) -> list[str]:
    """提取一行里**待验**的指针（去重保序）。

    ⚠️ **只收"带路径结构"的 token**（含 `/`），**裸文件名一律不收**——判据依据（2026-09-14 首跑实测）：
    首版把裸名也算作指针，7 处命中里 **4 处是"指代"而非"指针"**（索引正文自称 `MEMORY.md`、
    上文已给全路径后行文用简称 `md-html-parity.py`）⇒ 纯假阳性。
    而**红满天的门禁会被整体无视**（[[KB-ENG-58]]）。裸名本就不具备"可唯一定位"的性质，
    验它等于自造噪声。⇒ 真正含糊的裸名引用改由**文档侧**修（补全为完整路径），
    与判据放宽**分开处理**（[[KB-ENG-65]]：修判据 ≠ 修守卫）。
    """
    toks = [m.group(1) for m in INDEX_PTR_RE.finditer(line)
            if "/" in m.group(1) and not m.group(1).startswith("docs/")]
    toks += [m.group(1) for m in INDEX_DIR_RE.finditer(line)]
    seen: set[str] = set()
    out: list[str] = []
    for t in toks:
        t = t.rstrip(".,;:)]}")
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def check_memory_index() -> tuple[list[tuple[str, int]], list[tuple[str, int, str]]]:
    """N 项：入口索引的**体积**与**指针闭包** → (超限文件, 死指针)。

    ⚠️ 两根"保险丝"（都不是装饰）：
      · 索引文件**不存在** ⇒ 直接判红（fail-loud），而不是"没得查于是跳过"——
        跳过等于守卫静默失效（[[KB-ENG-72]]：守卫覆盖面失效比误报危险得多）。
      · 路径可解的双基准 `ROOT/` 与 `docs/`：索引里写 `kb/08-tooling-pitfalls.md`
        （相对 docs 的简写）与 `docs/kb/08-tooling-pitfalls.md`（仓库相对）**都合法**，
        只认一种会把另一种全判成误报 ⇒ **修文档之前先确认判据本身是否诚实**。
    """
    oversized: list[tuple[str, int]] = []
    dead: list[tuple[str, int, str]] = []
    tops = _tracked_tops()          # 判定面 = CI 检出内容（见 _in_checkout_universe）
    ck_dirs = _tracked_dirs()
    for rel in INDEX_FILES:
        p = ROOT / rel
        if not p.exists():
            # ⚠️ 保险丝（"索引缺失要响亮"）只对该仓库**拥有**的索引生效：
            # `.workbuddy/memory/MEMORY.md` 是 gitignored 的本地索引 ⇒ CI 检出里必然缺席，
            # 判红等于让 docs job 恒红（2026-09-15 实测）。
            if not _in_checkout_universe(rel, tops, ck_dirs):
                continue
            dead.append((rel, 1, rel))
            continue
        txt = _read(p)
        if rel == INDEX_CHAR_CAP_FILE and len(txt) > INDEX_CHAR_CAP:
            oversized.append((rel, len(txt)))
        for ln, line in enumerate(txt.splitlines(), 1):
            if any(k in line for k in RECORD_MARKERS):
                continue                      # 记录性引用（"已删除/已归档"）不算断链，同 B 项口径
            for tok in index_pointer_candidates(line):
                if PLACEHOLDER_RE.search(tok):
                    continue                  # 模板占位名（YYYY-MM-DD.md 等）
                if not _in_checkout_universe(tok, tops, ck_dirs):
                    continue      # 该路径在检出里本就不存在（gitignored：.workbuddy/ data/picks/ …）
                if tok.endswith("/"):
                    ok = (ROOT / tok).is_dir() or (DOCS / tok).is_dir()
                else:
                    ok = (ROOT / tok).exists() or (DOCS / tok).exists()
                if not ok:
                    dead.append((rel, ln, tok))
    return oversized, dead


#: O 项（GOV-008，2026-09-14）：编目表所在的**章节标题**与 KB 分表的表头特征。
#: 「可定位」是「一份好文档」第一条（**找不到 = 等于不存在**），而 `docs/INDEX.md`
#: §0.0 是唯一的编目面；它长期**只靠人守**——新增文档忘了登记，没有任何一项检查会红。
CATALOG_SECTION = "## 0.0 书库编目"
CATALOG_KB_SUBTABLE = "| 编号 | 册 |"


def catalog_entries() -> tuple[set[str], set[str]]:
    """解析 `docs/INDEX.md` §0.0 编目表 → (文件条目集, 目录条目集)，路径相对 `docs/`。

    三种写法都要认（编目表实际三种都在用，只认一种会把其余全判成"未登记"）：
      · `AGENTS.md`（根） / `.workbuddy/memory/` ⇒ 解析为**仓库相对**路径；
      · `kb/00-INDEX.md` ⇒ docs 相对；
      · 区间行（`SM-01..06` 的 `summary/stock-strategy` · `factor-system` · …）
        ⇒ 后续**裸名继承前一个带路径 token 的目录**。
    """
    txt = _read(DOCS / "INDEX.md")
    if CATALOG_SECTION not in txt:
        return set(), set()
    body = txt.split(CATALOG_SECTION, 1)[1]
    nxt = re.search(r"\n## ", body)
    if nxt:
        body = body[: nxt.start()]
    main_tbl, _, kb_tbl = body.partition(CATALOG_KB_SUBTABLE)

    files: set[str] = set()
    dirs: set[str] = set()
    for kb, tbl in ((False, main_tbl), (True, kb_tbl)):
        for line in tbl.splitlines():
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2 or set(cells[0]) <= set("-: "):
                continue
            last_dir = ""
            for tok in re.findall(r"`([^`]+)`", cells[1]):
                if PLACEHOLDER_RE.search(tok):
                    continue                      # 模板占位名（`overview-*.md` 等）
                tok = tok.strip()
                if tok.endswith("/"):
                    d = ("kb/" if kb else "") + tok
                    dirs.add(d)
                    last_dir = d
                    continue
                if "/" in tok:
                    last_dir = tok.rsplit("/", 1)[0] + "/"
                    if not tok.endswith(".md"):
                        tok += ".md"
                    files.add(tok if tok.startswith(".workbuddy/") else ("kb/" if kb else "") + tok)
                else:
                    stem = tok if tok.endswith(".md") else tok + ".md"
                    files.add(("kb/" if kb else "") + last_dir + stem)
    return files, dirs


def check_catalog_closure() -> tuple[list[str], list[str]]:
    """O 项：`docs/INDEX.md` 编目表 ⇄ `docs/**.md` 的**双向闭包** → (未登记, 幽灵条目)。

    两个方向都是真缺口（GOV-008）：
      · **未登记**：文件在、编目里没有 ⇒ 找不到 = 等于不存在（`kb/11` 判据一）；
      · **幽灵条目**：编目里有、文件不在 ⇒ 读者按编目跳过去是空处，且与 B 项的
        "死链"不是同一面（B 只认 `docs/**.md` 形态的**正文引用**，不认编目表）。

    ⚠️ **为什么必须先实测再上哨兵**（[[KB-ENG-68]]）：编目表的写法有**三种基线**
    （docs 相对 / 仓库相对 / 区间行裸名），朴素抽取会把 `kb/*` 12 份、`summary/*` 6 份、
    `evolution/`、`repo-watch/` 全部误判成"未登记"——**先把错误编目固化成门禁，
    比没有门禁更糟**（它会逼人去"修"一份本来正确的文档）。
    故本项上线前已按当前仓库实测：**83 份 md / 48 条文件条目 / 10 条目录条目，双向 0 命中**。

    ⚠️ **保险丝**：编目表/文件缺失 ⇒ 判红，不静默跳过（守卫覆盖面失效比误报危险）。
    """
    unregistered: list[str] = []
    ghosts: list[str] = []

    idx = DOCS / "INDEX.md"
    if not idx.exists():
        return ["docs/INDEX.md（编目表本体缺失）"], []
    files, dirs = catalog_entries()
    if not files and not dirs:
        return ["docs/INDEX.md §0.0 编目表解析为空"], []

    tops = _tracked_tops()          # 判定面 = CI 检出内容（见 _in_checkout_universe）
    # ⚠️ 变量名**不得用 `dirs`**：上面 `files, dirs = catalog_entries()` 的 `dirs` 是
    # **编目表里的目录条目**，撞名覆盖会让"区间行裸名继承"整段失效（本轮实测假报 38 处未登记）。
    ck_dirs = _tracked_dirs()
    tracked = _tracked_paths() or set()
    for rel in files:
        if not _in_checkout_universe(rel, tops, ck_dirs):
            continue
        if not (DOCS / rel).exists() and not (ROOT / rel).exists():
            ghosts.append(rel)
    for d in dirs:
        if not _in_checkout_universe(d, tops, ck_dirs):
            continue
        if not (DOCS / d).is_dir() and not (ROOT / d).is_dir():
            ghosts.append(d)

    for p in sorted(DOCS.rglob("*.md")):
        rel = p.relative_to(DOCS).as_posix()
        # 反向同理：**只判检出里的文件**，否则本地未提交的草稿会被报成"未登记"
        # （本地红 / CI 绿，同样属判定面不一致）
        if tops is not None and f"docs/{rel}" not in tracked:
            continue
        if rel in files or any(rel.startswith(d) for d in dirs):
            continue
        unregistered.append(rel)
    return unregistered, ghosts


# ---------------------------------------------------------------------------
# P 项：账本 §6.0-H「交接索引」⇄ `docs/handoff.md` 明细条目的**双向闭包**
#
# 动机（用户 2026-09-16 指令）：*「在账本中为每个任务建立索引，指向交接文档中对应的任务条目……
# 确保这种对应关系清晰、一致，覆盖所有后续任务。」* —— 规则只写在 md 里就是摆设
# （`kb/07` §9 自我淘汰条款）⇒ 固化为机检。
#
# 分工（**不是重复**，两处回答不同问题）：
#   · 账本 §6.0     = 任务清单（有哪些 / 优先级 / 状态 / 前置）；
#   · 账本 §6.0-H   = 「任务 → 明细」的**索引**；
#   · `docs/handoff.md` = 明细（做了什么 / 凭什么算完 / 门禁 / 遗留）。
#
# 三个方向都是真缺口：
#   · **索引无条目**：账本说"明细在 handoff §X"，读者跳过去是空处（= 死指针）；
#   · **条目无索引**：handoff 里有条目但索引查不到 ⇒ 从唯一入口**找不到** = 等于不存在；
#   · **条目缺反链**：只读到明细的人不知道任务**状态与前置**，也无法核对是否已闭环。
#
# 保险丝（fail-loud，不静默跳过，口径同 O 项）：账本无 §6.0-H 小节 / handoff 文件缺失 /
# 条目数为 0 ⇒ **判红**。理由：**守卫覆盖面失效比误报危险得多**（[[KB-ENG-72]]）。
#
# ⚠️ **本项上线时靠「注入自证」抓出两个真实盲区**（2026-09-16，三路注入 [A]/[B]/[C]）：
#   本来三路**全部判绿** —— 即守卫看起来在工作，其实两路是摆设。根因是**同一类错**：
#   判据用「词边界 `\b`」与「子串包含」表达"是同一个东西"，而 `-` 既是 ID 分隔符
#   又是非词字符、又是 `§6.0-H` 的合法字符 ⇒ 多一个后缀仍被认作命中。
#   · [A] 把标题改成 `## BUG-014-X` ⇒ 仍被认成 `BUG-014` 条目存在；
#   · [B] 把回链改成 `§6.0-HHH` ⇒ 仍"包含" `§6.0`；
#   · [C] 账本加幽灵行 `OPS-999` ⇒ ✅ 正常判红。
#   ⇒ **判据一律写「后向断言」`(?![\w-])`，不许用 `\b`、不许用 `in`**。
#   ⇒ 更一般的教训：**守卫上线必须做注入自证，且三路都要能红**；只验一路（[C]）会把
#     两路摆设当成"守卫已验证"（见 `kb/09` 同族：**看不见的失效**）。
# ---------------------------------------------------------------------------

HANDOFF_FILE = "docs/handoff.md"
#: 账本里的交接索引小节标题（形态固定，供机器定位；改标题即断守卫，故写死在此并留痕）。
#: ⚠️ **收尾用 `(?![\w-])` 而不是 `\b`**：`\b` 在 `-` 处也成立 ⇒ `#### 6.0-HHH` 会被误判为
#: 「小节在」⇒ 小节被改名后守卫**静默失守**（2026-09-16 注入自证实测，见下方长注释）。
HANDOFF_INDEX_HEAD_RE = re.compile(r"^#{3,5}\s*6\.0-H(?![\w-])")
#: 条目标题：`## <TASK-ID> …`（允许前置一个显式 HTML 锚点 ⇒ 便于跨文档点击跳转）。
#: ⚠️ **ID 收尾同样不能用 `\b`**：`## BUG-014-X` 里 `BUG-014` 后面是 `-`（非词字符）⇒
#: `\b` 成立 ⇒ 标题被改残后仍算"条目存在"，`ho_missing` 与 `ho_unindexed` **两路同时失守**。
HANDOFF_ENTRY_HEAD_RE = re.compile(
    r"^##\s+(?:<a\s+id=\"[^\"]+\"></a>\s*)?((?:BUG|IMP|RSH|GOV|OPS)-\d{3})(?![\w-])"
)
#: 条目**反链**判据：正文须出现该标记（与任务 ID 同现），否则读者回不到账本行。
#: ⚠️ **必须是正则而非「子串包含」**：本文件（`doc-health.py`）自己的说明里就反复写着 `§6.0-H`，
#: 条目正文只要提到小节名就能"满足"包含判据 ⇒ 真正丢掉账本回链反而判绿（2026-09-16 实测）。
HANDOFF_BACKLINK_RE = re.compile(r"§6\.0(?![\w-])")


def ledger_handoff_index_ids() -> set[str] | None:
    """账本 §6.0-H 小节里**已登记**的任务 ID；小节缺失 ⇒ `None`（fail-loud）。"""
    lines = _read(DOCS / "retro-and-gaps.md").splitlines()
    head = next((i for i, ln in enumerate(lines) if HANDOFF_INDEX_HEAD_RE.match(ln)), None)
    if head is None:
        return None
    ids: set[str] = set()
    for ln in lines[head + 1:]:
        if HEADING_RE.match(ln):            # 遇到下一个标题 ⇒ 本小节结束
            break
        if ln.lstrip().startswith("|"):
            # ⚠️ **只取第一列（任务 ID 列）**：若整行取 ID，"明细条目"列里的 `§RSH-026`
            # 会让"条目在索引里"恒真 ⇒ 判据自证失效（无法再抓「条目没登记」）。
            first_cell = ln.split("|")[1] if ln.count("|") >= 2 else ""
            ids.update(TASK_ID_RE.findall(first_cell))
    return ids


def handoff_entry_ids() -> tuple[dict[str, int], list[str]]:
    """`docs/handoff.md` 的条目 → (ID→行号, 缺反链的 ID 列表)；文件缺失 ⇒ (`{}`, [])。"""
    path = ROOT / HANDOFF_FILE
    if not path.exists():
        return {}, []
    lines = _read(path).splitlines()
    starts: list[tuple[int, str]] = []
    for i, ln in enumerate(lines):
        m = HANDOFF_ENTRY_HEAD_RE.match(ln)
        if m:
            starts.append((i, m.group(1)))
    ids: dict[str, int] = {}
    no_backlink: list[str] = []
    for k, (i, tid) in enumerate(starts):
        ids[tid] = i + 1
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        body = "\n".join(lines[i:end])
        if HANDOFF_BACKLINK_RE.search(body) is None:
            no_backlink.append(tid)
    return ids, no_backlink


def check_handoff_index() -> tuple[list[str], list[str], list[str], str | None]:
    """P 项：双向闭包 + 反链 → (索引无条目, 条目无索引, 条目缺反链, 保险丝说明)。

    ⚠️ **为什么条目用「标题前缀匹配任务 ID」而不是 URL 锚点**：Markdown 的标题锚点由
    渲染器 slug 化（中文标题的 slug 规则各平台不同、且标题一改锚点即变）⇒ 拿锚点当判据
    会把"文档改个措辞"变成门禁失败。判据锚定在**任务 ID** 这个稳定键上，
    跨文档点击则由条目标题里的**显式 `<a id>`**（可选）承担。

    ⚠️ **保险丝单独走第 4 个返回值，不塞进前三个列表里**：首版把哨兵串（如
    `"（账本 §6.0-H 小节缺失）"`）直接放进列表 ⇒ 调用方按常规差异打印，输出
    「**账本索引已登记 （账本 §6.0-H 小节缺失），但 handoff 无该条目**」这种自相矛盾的话
    （把"判据找不到"说成"某 ID 已登记"，2026-09-16 注入自证 [D] 实测）。
    **fail-loud 的文案必须比正常分支更清楚**，否则排障时先被自己的日志误导。
    """
    if not (ROOT / HANDOFF_FILE).exists():
        return [], [], [], f"{HANDOFF_FILE} 不存在（明细层缺失，守卫无判定面）"
    indexed = ledger_handoff_index_ids()
    if indexed is None:
        return [], [], [], "账本 §6.0-H 小节缺失（索引层缺失，守卫无判定面）"
    entries, no_backlink = handoff_entry_ids()
    if not entries:
        return [], [], [], f"{HANDOFF_FILE} 条目数为 0（明细层为空，守卫无判定面）"
    missing_entry = sorted(indexed - set(entries))
    unindexed = sorted(set(entries) - indexed)
    return missing_entry, unindexed, sorted(no_backlink), None


def main() -> int:
    quiet = "--quiet" in sys.argv
    scan_all = "--all" in sys.argv
    problems = 0
    failed: list[str] = []          # 失败的检查项标签，供结论行**如实**列举（见下）

    unreg = check_unregistered()
    dead = check_dead_links(scan_all)
    over = check_over_limit()
    kb_over = check_kb_entries()
    coverage = kb_file_coverage_gap()
    abstract = check_missing_abstract()
    clusters = check_clusters()
    coderef, coderef_ok = check_code_refs()
    legacy, legacy_ok = check_legacy_slugs()
    ptr = check_kb_pointer_files()
    orphans = check_kb_orphans()
    stale = check_stale_anchors()
    claim_miss = check_claim_entries_missing_falsifier()
    claim_ghost = check_claim_exempt_ids()
    empty, empty_ghost = check_empty_sections()
    anchors, anchor_ghost = check_doc_anchors()
    tables = check_table_delimiters()
    task_ids = check_task_ids_defined()
    carriers = check_task_carrier_pointers()
    idx_over, idx_dead = check_memory_index()
    cat_unreg, cat_ghost = check_catalog_closure()
    ho_missing, ho_unindexed, ho_noback, ho_sentinel = check_handoff_index()

    def line(label: str, ok: bool, detail: str = "") -> None:
        nonlocal problems
        if not ok:
            problems += 1
            failed.append(label)
        if ok and quiet:
            return
        print(f"[{'OK ' if ok else 'FAIL'}] {label} {detail}")

    if not quiet:
        scope = "全量（含 archive + 历史日志）" if scan_all else "活文档面（archive/日志已跳过）"
        print(f"doc-health @ {ROOT}\n扫描范围：{scope}\n{'-' * 60}")
    line("A 未登记文档", not unreg, f"{len(unreg)} 份" + (" → " + ", ".join(unreg) if unreg else ""))
    line("B 死链(活引用)", not dead, f"{len(dead)} 处")
    if dead and not quiet:
        for f, ln, rel in dead:
            print(f"       {f}:{ln} → docs/{rel}（不存在）")
    line("C 超层配额", not over, f"{len(over)} 份" + (" → " + ", ".join(f"{p}({n}>{c})" for p, n, c in over) if over else ""))
    line("C-KB 条目超长", not kb_over, f"{len(kb_over)} 条"
         + (" → " + ", ".join(f"{f}:{i}({n})" for f, i, n in kb_over) if kb_over else "（≤60，§7 L0 硬门）"))
    line("C-KB 扫描覆盖", not coverage,
         f"{len(coverage)} 份未纳入 KB 检查"
         + (" → " + ", ".join(coverage) if coverage else "（docs/kb/*.md 全部纳入）"))
    line("D 摘要前置缺失", not abstract, f"{len(abstract)} 份")
    if abstract and not quiet:
        for p, n in abstract:
            print(f"       {p}（{n} 行，前 40 行无摘要/定位块）")
    line("F 代码注释死引用", not coderef, f"{len(coderef)} 处（B 只扫 *.md，本项扫 app/tests/web/scripts）")
    if coderef and not quiet:
        for f, ln, rel in coderef[:15]:
            print(f"       {f}:{ln} → docs/{rel}（不存在）")
        if len(coderef) > 15:
            print(f"       …另有 {len(coderef) - 15} 处")
    line("F2 代码裸名死引用", not legacy,
         f"{len(legacy)} 处（已删/已归档方案文档的**裸名**，F 扫不到）")
    if legacy and not quiet:
        for f, ln, slug in legacy[:12]:
            print(f"       {f}:{ln} → {slug}（已删/归档，真身：{LEGACY_DOC_SLUGS[slug]}）")
        if len(legacy) > 12:
            print(f"       …另有 {len(legacy) - 12} 处")
    line("F3 KB 指针错册", not ptr,
         f"{len(ptr)} 处（`见 kb/NN-x.md … KB-ID` 必须同册；KB 分册后条目搬家会静默指错）")
    if ptr and not quiet:
        for f, ln, said, kid, real in ptr[:12]:
            print(f"       {f}:{ln} → 写 kb/{said}，实为 {real} ← {kid}")
    line("F4 废弃锚名死引用", not stale,
         f"{len(stale)} 处（裸名指向全仓不存在的文件；B/F 均扫不到）")
    if stale and not quiet:
        for f, ln, name in stale[:12]:
            print(f"       {f}:{ln} → {name}（{STALE_ANCHORS[name]}）")
        if len(stale) > 12:
            print(f"       …另有 {len(stale) - 12} 处")
    line("G KB 孤儿条目", not orphans,
         f"{len(orphans)} 条零外部引用（沉淀了但没人用）")
    if orphans and not quiet:
        for f, kid, title in orphans[:12]:
            print(f"       {f}:{kid} {title[:48]}")
        if len(orphans) > 12:
            print(f"       …另有 {len(orphans) - 12} 条")
    i_detail = f"{len(empty)} 处（标题在、正文 0 行）"
    if EMPTY_SECTION_ALLOW:
        i_detail += f"；已登记容忍 {len(EMPTY_SECTION_ALLOW)} 条"
    if empty_ghost:
        i_detail += f"；⚠️ 容忍项已失效 {len(empty_ghost)} 条"
    line("I 空章节", not empty and not empty_ghost, i_detail)
    if empty and not quiet:
        for f, ln, txt in empty[:12]:
            print(f"       {f}:{ln} → {txt[:60]}")
        if len(empty) > 12:
            print(f"       …另有 {len(empty) - 12} 处")
    if empty_ghost and not quiet:
        for g in empty_ghost:
            print(f"       容忍项失效（标题改了/文件搬了，须删该条）：{g}")
    j_detail = f"{len(anchors)} 处（文档点名的仓库代码路径不存在）"
    if ANCHOR_ALLOW:
        j_detail += f"；已登记容忍 {len(ANCHOR_ALLOW)} 条"
    if anchor_ghost:
        j_detail += f"；⚠️ 容忍项已失效 {len(anchor_ghost)} 条"
    line("J 文档代码锚点", not anchors and not anchor_ghost, j_detail)
    if anchors and not quiet:
        for f, ln, a, txt in anchors[:12]:
            print(f"       {f}:{ln} → {a}（全仓不存在）")
        if len(anchors) > 12:
            print(f"       …另有 {len(anchors) - 12} 处")
    if anchor_ghost and not quiet:
        for g in anchor_ghost:
            print(f"       容忍项失效（锚点已改/文件已搬，须删该条）：{g}")
    line("K 表格分隔行", not tables,
         f"{len(tables)} 处（`|` 行块首行的下一行不是 GFM 分隔行；"
         "空行分组会把表打碎，曾致渲染器死循环 §6.14）")
    if tables and not quiet:
        for f, ln, txt in tables[:12]:
            print(f"       {f}:{ln} → {txt[:64]}")
        if len(tables) > 12:
            print(f"       …另有 {len(tables) - 12} 处")
    l_detail = (f"{len(task_ids)} 处引用了账本 §6.0 未定义的 ID"
                f"（任务改名/删除后旧引用会静默指错；规则见 kb/07 §3.3）")
    if TASK_ID_ALLOW:
        l_detail += f"；已登记例外 {len(TASK_ID_ALLOW)} 处"
    line("L 任务 ID 指针", not task_ids, l_detail)
    if task_ids and not quiet:
        for f, ln, tid in task_ids[:12]:
            print(f"       {f}:{ln} → {tid}（账本 §6.0 无此 ID）")
        if len(task_ids) > 12:
            print(f"       …另有 {len(task_ids) - 12} 处")
    m_detail = (f"{len(carriers)} 处任务清单缺「归集至账本 §6.0」指针"
                f"（任务不得分散在多份文档，见 kb/07 §3.3）")
    if TASK_CARRIER_ALLOW:
        m_detail += f"；已登记例外 {len(TASK_CARRIER_ALLOW)} 处"
    line("M 任务载体指针", not carriers, m_detail)
    if carriers and not quiet:
        for f, ln, txt in carriers[:12]:
            print(f"       {f}:{ln} → {txt[:64]}")
        if len(carriers) > 12:
            print(f"       …另有 {len(carriers) - 12} 处")
    n_detail = (f"{len(idx_dead)} 处指针失效"
                f"（B 只认 `docs/*.md`；本项扫 `.workbuddy/`、根文件、代码路径）")
    if idx_over:
        n_detail += f"；⚠️ 体积超限 {len(idx_over)} 份"
    line("N 索引体积与指针", not idx_dead and not idx_over, n_detail)
    for f, n in idx_over:
        print(f"       {f}：{n} 字符 > {INDEX_CHAR_CAP}"
              f" ⇒ 内容应进 L3 文档、索引只留指针（kb/07 §4.4 反固化条款）")
    # ⚠️ 明细打印**不得嵌在上面的 for 里**（2026-09-15 修）：原先它嵌在 `for f, n in idx_over`
    # 内部，于是"体积不超限、只有死指针"时（= 最需要明细的情形）**一条明细都不打** ——
    # CI 日志只剩 FAIL 行，看不到是哪 14 处，排查被迫改走 worktree 复现。诊断信息必须在
    # 最需要它的时候在场。
    if idx_dead and not quiet:
        for f, ln, tok in idx_dead[:12]:
            print(f"       {f}:{ln} → {tok}（全仓不存在）")
        if len(idx_dead) > 12:
            print(f"       …另有 {len(idx_dead) - 12} 处")
    line("O 编目完整性", not cat_unreg and not cat_ghost,
         f"未登记 {len(cat_unreg)} 份 / 幽灵条目 {len(cat_ghost)} 条"
         + ("（docs/INDEX.md §0.0 ⇄ docs/**.md 双向闭包）" if not (cat_unreg or cat_ghost) else ""))
    if cat_unreg and not quiet:
        for rel in cat_unreg[:12]:
            print(f"       + {rel}（在 docs/ 下但编目表未登记）")
        if len(cat_unreg) > 12:
            print(f"       …另有 {len(cat_unreg) - 12} 份")
    if cat_ghost and not quiet:
        for rel in cat_ghost[:12]:
            print(f"       - {rel}（编目表登记但全仓不存在）")
    p_bad = bool(ho_missing or ho_unindexed or ho_noback or ho_sentinel)
    p_detail = (f"账本 §6.0-H ⇄ {HANDOFF_FILE} 双向闭包"
                + ("（保险丝：判定面不可用）" if ho_sentinel else
                   "" if p_bad else
                   f"（条目 {len(handoff_entry_ids()[0])} 条，全部登记且带反链）"))
    line("P 交接索引", not p_bad, p_detail)
    if ho_sentinel and not quiet:
        # 保险丝与"真的对不上"是**两种不同的病**，文案必须分得开（否则排障会先被日志误导）。
        print(f"       ⛔ {ho_sentinel}")
        print("          ⇒ 这是「守卫没有判定面」，不等于「文档没问题」；先修好该载体再复跑")
    if p_bad and not ho_sentinel and not quiet:
        for tid in ho_missing:
            print(f"       账本索引已登记 {tid}，但 {HANDOFF_FILE} 无 `## {tid}` 条目")
        for tid in ho_unindexed:
            print(f"       {HANDOFF_FILE} 有 {tid} 条目，但账本 §6.0-H 未登记")
        for tid in ho_noback:
            print(f"       {tid} 条目缺回链（正文须出现「§6.0 <任务 ID>」形态的账本指针）")
    # H 项**刻意走 WARN 而非 FAIL**：先补存量再上哨兵，且只作提示。
    # 若计 FAIL，未补完的条目会让整份体检长期挂红 ⇒ 被整体无视（KB-ENG-58）。
    line("H-KB 豁免名单有效", not claim_ghost,
         f"{len(claim_ghost)} 个幽灵 ID（条目已改名/删除，豁免已静默失效）"
         + (" → " + ", ".join(claim_ghost) if claim_ghost else
            f"（{len(CLAIM_EXEMPT)} 条豁免全部指向在册条目）"))
    if not quiet:
        print(f"[{'WARN' if claim_miss else 'OK '}] H-KB 主张类失效条件 "
              f"{len(claim_miss)} 条未交代（**告警级，不计 FAIL**；"
              f"扫 {', '.join(CLAIM_KB_FILES)} 并按 CLAIM_EXEMPT 豁免做法/定义/框架类）")
        for f, kid, title in claim_miss[:12]:
            print(f"       {f}:{kid} {title[:48]}")
        if len(claim_miss) > 12:
            print(f"       …另有 {len(claim_miss) - 12} 条")
    if not quiet:
        print("[INFO] E 同类聚集（同前缀 ≥3 份，评估是否需共同索引页）："
              + (", ".join(f"{k}×{v}" for k, v in clusters) if clusters else "无"))
        kb_big = kb_file_advisories()
        print("[INFO] KB 文件 >800 行（§7：L0 总行数不限，仅触发「评估按子类拆文件」，不计 FAIL）："
              + (", ".join(f"{p}（{n} 行）" for p, n in kb_big) or "无"))
        print(f"[INFO] C 已登记容忍项：文件 {len(TOLERATED)} 份 / 条目 {len(TOLERATED_ENTRIES)} 条"
              f"（{', '.join(list(TOLERATED) + list(TOLERATED_ENTRIES))}）")
        print(f"[INFO] F 已登记例外：{len(coderef_ok)} 处示例/输出路径"
              + (f"（{'、'.join(r for _, r in coderef_ok)}）" if coderef_ok else ""))
        print(f"[INFO] F2 已登记例外：{len(legacy_ok)} 处"
              + (f"（{'、'.join(f'{s}@{f}' for f, s in legacy_ok)}）" if legacy_ok else "（无）"))
        print(f"[INFO] F4 登记锚名 {len(STALE_ANCHORS)} 个 / 例外 {len(STALE_ANCHOR_ALLOW)} 处"
              + (f"（{'、'.join(f'{n}@{f}' for f, n in STALE_ANCHOR_ALLOW)}）" if STALE_ANCHOR_ALLOW else "（无）"))
        print(f"[INFO] L/M 任务单点归集：ID 例外 {len(TASK_ID_ALLOW)} 处 · "
              f"载体例外 {len(TASK_CARRIER_ALLOW)} 处 · 结构性排除 {len(TASK_CARRIER_SKIP_FILES)} 份"
              f"（{'、'.join(sorted(TASK_CARRIER_SKIP_FILES))}）")
        n_claim = len(re.findall(r"^### KB-[A-Z]+-\d+", _read(DOCS / "kb" / "01-stock-picking.md"), re.M)) \
            + len(re.findall(r"^### KB-[A-Z]+-\d+", _read(DOCS / "kb" / "02-trading-lessons.md"), re.M))
        print(f"[INFO] H 主张类覆盖面：{n_claim} 条中 {len(CLAIM_EXEMPT)} 条豁免（做法/定义/框架），"
              f"{n_claim - len(CLAIM_EXEMPT)} 条应带失效条件，实际缺 {len(claim_miss)} 条")
        # 结论行**按实际失败项列举**。此前这里硬编码「（C 的日志单轮 ≤80 行属过程指标…）」——
        # 那句是**常驻局限**（见模块 docstring），与本次失败项无关，却无条件挂在结论后，
        # 失败项是 J 时会被读成"C 项待处理"。实测已导致排查方向跑偏（2026-09-12 CI：J 项红，
        # 日志显示"结论：1 项待处理（C 的日志单轮…）"，据此去查 C 而非 J）。
        # ⇒ 通例：**汇总行必须由实际结果派生，不得写死任何单项的描述**。
        concl = "全部通过" if not failed else f"{len(failed)} 项待处理（{'、'.join(failed)}）"
        print(f"{'-' * 60}\n结论：{concl}")
        print("[INFO] 常驻局限：C 的「日志单轮新增 ≤80 行」属过程指标，静态扫描判不出，"
              "需人工/议程核对（见 kb/07 §8.2）")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
