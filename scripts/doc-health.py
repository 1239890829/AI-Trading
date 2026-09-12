#!/usr/bin/env python3
"""文档体检（docs 治理的「一条命令」版，kb/07-doc-curation.md §8.2 的可执行实现）。

为什么有它：`kb/07-doc-curation.md` 写明了「新建必登记 / 解引用 / 摘要前置 / 按层配额」，
但规则只是**文字**——没有载体时，收尾是否整理全靠人记得。本脚本把 §8.2 月度体检
变成一条命令，使「任务完成后自动整理」有机制可依（§9 自我淘汰条款的落地）。

用法：
    python3 scripts/doc-health.py            # 全量体检，有问题 exit 1
    python3 scripts/doc-health.py --quiet    # 只输出结论行（收尾自检用）
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

局限（诚实声明）：C 的"日志单轮新增 ≤80 行"是**过程指标**，静态扫描判不出，需人工/议程侧核对。
"""

from __future__ import annotations

import re
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
#: 目前为**空**——2026-09-12 那条 `nfp-ashare-validation@test_chains.py` 例外随着该断言
#: 改写（不再依赖文档名做标记）已经**不再命中**，属死配置故删除：**过期的允许列表本身
#: 就是「看着有守卫、实际不设防」**，与「永不触发的门禁」同类。
LEGACY_SLUG_ALLOW: dict[tuple[str, str], str] = {}

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
    skip_dirs = {"node_modules", ".next", ".turbo", "__pycache__", ".venv", "dist", "build"}
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
    skip_dirs = {"node_modules", ".next", ".turbo", "__pycache__", ".venv", "dist", "build"}
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
    skip_dirs = {"node_modules", ".next", ".turbo", "__pycache__", ".venv",
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

    skip_dirs = {"node_modules", ".next", ".turbo", "__pycache__", ".venv",
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
    """§7 L0 硬门：单条 KB 条目 ≤60 行（`### KB-` 到下一条目起点的行距）。"""
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


def main() -> int:
    quiet = "--quiet" in sys.argv
    scan_all = "--all" in sys.argv
    problems = 0

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

    def line(label: str, ok: bool, detail: str = "") -> None:
        nonlocal problems
        if not ok:
            problems += 1
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
        print(f"{'-' * 60}\n结论：{'全部通过' if problems == 0 else f'{problems} 项待处理'}"
              f"（C 的日志单轮 ≤80 行属过程指标，需人工/议程核对）")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
