#!/usr/bin/env python3
"""md → html 内容完整性对账器（离线，零依赖）。

背景：`scripts/reports/md-report-html.py` 是本仓报告的**唯一**渲染器，渲染结果
是无外链的单文件 HTML。报告里常出现**裸尖括号**（`均 < 24h`、`abs_i < 0`、
`observed < today`）与**字面量星号/竖线/反斜杠**（`NEXT_PUBLIC_*`、`tests/*.py`、
`12 * 3600`、`is_*`、`grep "\\|"`、`"\\|err\\|"`）。

这个工具存在的理由，是一次**自造的假缺陷**：曾用一次性脚本比对 md 与 html，
脚本把 `*` `|` `\\` 一律当成 markdown 标记删掉，于是 md 侧被吃字、html 侧没有，
比对结果呈现为「HTML 丢内容」，**差点据此去修一个本来正确的渲染器**。
⇒ 判据本身的失真是**假阴性之外的第二种危险**：它不是漏看，而是**诱发主动的错误修正**。

因此本工具的核心纪律是：**归一化必须对字面量诚实**——只去真正的 markdown 标记
（成对 `**`、行内 ` 反引号、行首列表/标题/引用标记、`[文字](链接)` 的链接部分），
其余字符**原样保留**；html 侧先反转义（`&lt;` → `<`）再比。

用法：
    python3 scripts/reports/md-html-parity.py <input.md> [output.html]
    # output.html 省略时取同名 .html

判据（四项全过才算无损；任一不过 ⇒ 退出码 1）：
  1. **逐条目文本命中**：md 的每个「正文行 / 表格单元格」归一化后，必须能在 html 的
     可见文本（去 script/style → 去标签 → 反转义 → 去空白）中**完整**找到。
  2. **结构计数相等**：`<table>` 数 = md 对齐行数；`<li>` 数 = md 列表项数；
     `<h1..h4>` 数 = md 同级别标题数。
  3'. **表格列数一致**（2026-09-14 补）：每个 `<table>` 的数据行 `<td>` 数必须等于表头
     `<th>` 数；md 侧每行也须与表头列数相等。**这一项与第 1 项不重复**：若 md 与 html
     用同一条错误判据切分，第 1 项会双向自洽地通过，只有列数能独立暴露切分错位
     （实例：渲染器不识别转义竖线 `\\|` ⇒ `boolean \\| null` 被切成两格、该行 4 个 `<td>`）。
  3. **时间戳新鲜**：`.md` 的 mtime 必须**不晚于** `.html`（本仓曾出现 md 晚于 html
     ⇒ HTML 静默停在旧版）。

失败时打印**第一个断裂点**（保留前缀 / 断裂片段 / md 原文），便于定位。
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

def _norm(s: str) -> str:
    """去掉全部空白字符（md 与 html 两侧同口径）。"""
    return re.sub(r"\s+", "", s)


def _split_cells(row: str) -> list[str]:
    """按**未转义**的竖线切分表格行，并还原单元格内的转义竖线。

    必须与 `md-report-html.py` 的 `_split_row` + `_cell_inline` 用**同一条口径**：
    切分时排除 `\\|`，切分后把每个单元格里的 `\\|` 一律还原为 `|`（GFM 也是
    在行内解析之前先做这一步）。若一边还原、另一边不还原，单元格文本就会对不上，
    比对结果会变成"凭空多出一格/丢一个字符"的假缺口。
    """
    s = row.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def strip_md(s: str) -> str:
    """只去**真正的** markdown 标记；字面量 `*` `|` `\\` 原样保留。

    这里每一行都是在还债——任何"顺手多删一点"都会制造假缺口。
    """
    s = re.sub(r"^\s*[-*+]\s+", "", s)  # 无序列表标记
    s = re.sub(r"^\s*\d+\.\s+", "", s)  # 有序列表标记
    s = re.sub(r"^\s*#{1,6}\s+", "", s)  # ATX 标题
    s = re.sub(r"^\s*>\s?", "", s)  # 引用标记
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)  # 仅**成对**的 ** 视为 bold
    # 转义竖线 `\|` 在**非代码段**是 markdown 转义 ⇒ 渲染后为字面 `|`，须还原；
    # 代码段内的 `\|`（如 grep 的 BRE 交替）是字面量，还原就会失真。
    parts = re.split(r"(`[^`]*`)", s)
    s = "".join(p if k % 2 else p.replace("\\|", "|") for k, p in enumerate(parts))
    s = s.replace("`", "")  # 行内代码标记（内容保留）
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)  # 链接只取显示文字
    return _norm(s)


def visible_text(html_doc: str) -> str:
    """html 侧取可见文本：去 script/style → 去标签 → 反转义 → 去空白。"""
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_doc)
    body = re.sub(r"(?s)<[^>]*>", " ", body)
    return _norm(html.unescape(body))


def html_col_issues(html_doc: str) -> list[tuple[int, int, int]]:
    """检查每个 `<table>` 的**数据行列数是否等于表头列数**，返回 (表序, 表头列数, 实际列数)。

    为什么必须单独查（[[KB-ENG-72]] 守卫覆盖面）：md 侧与 html 侧若用**同一条错误判据**
    切分（例如两边都不识别转义竖线 `\\|`），逐条目比对会**双向自洽地通过** —— 错得一样
    就查不出错。只有"每行 `<td>` 数 ≠ `<th>` 数"能独立暴露切分错位。
    2026-09-14 实测：渲染器把 `` `boolean \\| null` `` 切成两格，该行 **4 个 `<td>`** 而
    表头 **3 个 `<th>`**，页面还伴随 `<code>` 未闭合与反引号泄露。
    """
    issues: list[tuple[int, int, int]] = []
    for ti, block in enumerate(re.findall(r"(?is)<table\b.*?</table>", html_doc), 1):
        n_col = len(re.findall(r"(?is)<th\b[^>]*>", block))
        if not n_col:
            continue
        for row in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", block):
            n_td = len(re.findall(r"(?is)<td\b[^>]*>", row))
            if n_td and n_td != n_col:  # n_td==0 是表头行
                issues.append((ti, n_col, n_td))
    return issues


def collect_items(md: str) -> tuple[list[tuple[int, str, str]], dict[str, int], list[tuple[int, int, int]]]:
    """把 md 拆成可逐条核对的条目 + 结构计数（表格/列表/标题）+ 表格列数错位清单。"""
    items: list[tuple[int, str, str]] = []
    struct = {"tables": 0, "li": 0, "h": 0}
    col_issues: list[tuple[int, int, int]] = []
    raw = md.split("\n")
    in_fence = False
    i = 0
    while i < len(raw):
        line = raw[i]
        ln = i + 1
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            i += 1
            continue
        s = line.strip()
        if not s:
            i += 1
            continue
        if re.match(r"^\s*(---|\*\*\*)\s*$", line):  # 水平线不产生文本
            i += 1
            continue
        if re.match(r"^#{1,4}\s+", line):
            struct["h"] += 1
        elif re.match(r"^\s*([-*+]|\d+\.)\s+", line):
            struct["li"] += 1

        # 表格整块（表头 + 对齐行 + 数据行），逐单元格登记
        if s.startswith("|") and i + 1 < len(raw) and re.match(
            r"^\s*\|[\s:|-]+\|\s*$", raw[i + 1]
        ):
            struct["tables"] += 1
            head_cells = _split_cells(raw[i])
            for k, cell in enumerate(head_cells):
                if cell and not re.match(r"^[\s:-]+$", cell):
                    items.append((i + 1, f"表格单元[{k}]", cell))
            j = i + 2  # 跳过表头行与对齐行
            while j < len(raw) and raw[j].strip().startswith("|"):
                cells = _split_cells(raw[j])
                if len(cells) != len(head_cells):
                    col_issues.append((j + 1, len(head_cells), len(cells)))
                for k, cell in enumerate(cells):
                    if cell and not re.match(r"^[\s:-]+$", cell):
                        items.append((j + 1, f"表格单元[{k}]", cell))
                j += 1
            i = j
            continue

        items.append((ln, "正文", s))
        i += 1
    return items, struct, col_issues


def first_break(needle: str, hay: str) -> int:
    """二分求「needle 的最长、且在 hay 中出现的**前缀**」长度。"""
    lo, hi = 0, len(needle)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if needle[:mid] in hay:
            lo = mid
        else:
            hi = mid - 1
    return lo


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    src = Path(args[0])
    dst = Path(args[1]) if len(args) > 1 else src.with_suffix(".html")
    if not src.exists() or not dst.exists():
        print(f"❌ 文件不存在：{src if not src.exists() else dst}")
        return 2

    md = src.read_text(encoding="utf-8")
    html_doc = dst.read_text(encoding="utf-8")
    hay = visible_text(html_doc)
    items, struct, md_col_issues = collect_items(md)

    misses = [
        (ln, kind, orig, strip_md(orig))
        for ln, kind, orig in items
        if strip_md(orig) and strip_md(orig) not in hay
    ]

    # 结构计数：md 侧
    md_tables = struct["tables"]
    md_li = struct["li"]
    md_h = struct["h"]
    ht_tables = len(re.findall(r"(?i)<table\b", html_doc))
    ht_li = len(re.findall(r"(?i)<li\b", html_doc))
    ht_h = len(re.findall(r"(?i)<h[1-4]\b", html_doc))

    ok = True
    print(f"① 逐条目文本命中：{len(items) - len(misses)}/{len(items)}")
    if misses:
        ok = False
        print(f"   ❌ 未命中 {len(misses)} 处：")
        for ln, kind, orig, needle in misses:
            cut = first_break(needle, hay)
            print(f"   L{ln} [{kind}]  命中前缀 {cut}/{len(needle)}")
            print(f"      保留到: …{needle[max(0, cut - 24):cut]!r}")
            print(f"      断裂起: {needle[cut:cut + 60]!r}")
            print(f"      md 原文: {orig[:120]}")
    else:
        print("   ✅ 全部完整命中（无内容丢失）")

    t_ok = md_tables == ht_tables
    l_ok = md_li == ht_li
    h_ok = md_h == ht_h
    ht_col_issues = html_col_issues(html_doc)
    c_ok = not ht_col_issues and not md_col_issues
    ok = ok and t_ok and l_ok and h_ok and c_ok
    flag = "✅" if (t_ok and l_ok and h_ok and c_ok) else "❌"
    print(f"② 结构计数：{flag} table {md_tables}={ht_tables} · li {md_li}={ht_li} · h {md_h}={ht_h}")
    if not c_ok:
        print(f"   ❌ 表格列数错位：html {len(ht_col_issues)} 处 · md {len(md_col_issues)} 处")
        for ti, n_col, n_td in ht_col_issues[:5]:
            print(f"      html 表#{ti}：表头 {n_col} 列 vs 数据行 {n_td} 列")
        for ln, n_col, n_cell in md_col_issues[:5]:
            print(f"      md L{ln}：表头 {n_col} 列 vs 本行 {n_cell} 格")

    s_t, h_t = src.stat().st_mtime, dst.stat().st_mtime
    fresh = s_t <= h_t
    ok = ok and fresh
    print(
        f"③ 时间戳新鲜：{'✅' if fresh else '❌'} md = {_ts(s_t)} / html = {_ts(h_t)}"
    )

    print("—" * 32)
    print(f"{'✅ 无损' if ok else '❌ 有差异'}：{src.name} → {dst.name}")
    return 0 if ok else 1


def _ts(t: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
