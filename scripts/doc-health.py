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


def check_kb_entries() -> list[tuple[str, str, int]]:
    """§7 L0 硬门：单条 KB 条目 ≤60 行（`### KB-` 到下一条目起点的行距）。"""
    out = []
    for p in sorted((DOCS / "kb").glob("0[1-9]*.md")):
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
    for p in sorted((DOCS / "kb").glob("0[1-9]*.md")):
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
    abstract = check_missing_abstract()
    clusters = check_clusters()
    coderef, coderef_ok = check_code_refs()
    legacy, legacy_ok = check_legacy_slugs()

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
        print(f"{'-' * 60}\n结论：{'全部通过' if problems == 0 else f'{problems} 项待处理'}"
              f"（C 的日志单轮 ≤80 行属过程指标，需人工/议程核对）")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
