#!/usr/bin/env python3
"""Markdown → 单文件 HTML 报告渲染器（离线，零依赖）。

用途：把 `artifacts/reports/*.md` 这类中文技术报告渲染成**自包含**的单页 HTML
（内联 CSS、无外链、无 CDN），便于直接双击查看或作为交付物。

为什么不用 pandoc / markdown 库：
  - 环境里没有 pandoc；装 markdown 库又要往 venv 里加依赖（本项目要求新依赖同步 lock 文件）。
  - 本仓报告只用到 markdown 的一个**有限子集**，自写 100 行比引依赖更可控。

样式来源：复用 `artifacts/reports/outputs/project-code-review-20260914.html` 的
内联样式（深色、与 IDE 主题一致），保证同一份报告在多次渲染间观感一致。

用法：
    python3 scripts/reports/md-report-html.py <input.md> [output.html] [--title 标题]

支持的语法子集：ATX 标题 1-4 级、表格（含对齐行）、有序/无序列表、引用块、
代码围栏、行内 `code` 与 **bold**、水平线。
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

STYLE = Path(__file__).resolve().parent / "report_style.css"


def _restore_escaped_pipes(text: str) -> str:
    """还原转义竖线 `\\|` → 字面 `|`；**只还原反引号代码段之外的部分**。

    用于非表格语境（普通段落）。那里 `\\|` 是 markdown 转义 ⇒ 还原；而代码 span
    内的 `\\|` 是字面量（本仓常见：BSD grep 不支持交替的 ``grep "\\|"``），保留。
    """
    parts = re.split(r"(`[^`]*`)", text)
    return "".join(p if k % 2 else p.replace("\\|", "|") for k, p in enumerate(parts))


def _inline(text: str) -> str:
    """行内语法：先转义，再还原 code / bold / 链接，避免 HTML 注入。"""
    out = html.escape(text, quote=True)
    out = _restore_escaped_pipes(out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    def link(match: re.Match) -> str:
        label, target = match.groups()
        scheme = urlsplit(html.unescape(target)).scheme.lower()
        if scheme not in {"", "https", "http", "mailto"}:
            return label
        return f'<a href="{target}">{label}</a>'

    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, out)
    return out


def _cell_inline(text: str) -> str:
    """表格单元格的行内渲染：**转义竖线一律还原**，含行内代码 span 内。

    与 GFM 对齐，且有实测依据：GFM 的表格解析在**行内解析之前**就把 `\\|` 还原为
    `|`，因此单元格内 `` `boolean \\| null` `` 在 GitHub 上渲染为 `boolean | null`
    （不是 `boolean \\| null`）。早期版本漏了这一步，渲染结果与读者预期不符。
    """
    return _inline(text.replace("\\|", "|"))


def _split_row(line: str) -> list[str]:
    """按**未转义**的竖线切分单元格。

    裸 `split("|")` 会把转义竖线 `\\|` 也当分隔符 —— 后果不是"少一格"，而是
    整行比表头多出若干 `<td>`、`<code>` 未闭合、反引号原样泄露到页面
    （2026-09-14 实测：报告里 `boolean \\| null` 被切成两格、`<td>` 数 4 vs 表头 3）。
    ⇒ 用负向后行断言排除 `\\|`，并先剥掉最外层的**裸**竖线。
    """
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def render(md: str) -> str:
    lines = md.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]

        # 代码围栏
        if line.lstrip().startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].lstrip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + html.escape("\n".join(buf)) + "</code></pre>")
            continue

        # 表格：当前行是 |...| 且下一行是对齐行
        if line.strip().startswith("|") and i + 1 < n and re.match(
            r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]
        ):
            head = _split_row(line)
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            th = "".join(f"<th>{_cell_inline(c)}</th>" for c in head)
            body = "".join(
                "<tr>" + "".join(f"<td>{_cell_inline(c)}</td>" for c in r) + "</tr>"
                for r in rows
            )
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>")
            continue

        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        # 水平线
        if re.match(r"^\s*(---|\*\*\*)\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # 引用块（连续 > 行合并为一个 blockquote）
        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            inner = "<br>".join(_inline(x) if x.strip() else "" for x in buf)
            out.append(f"<blockquote>{inner}</blockquote>")
            continue

        # 列表（无序 / 有序，支持一层续行）
        if re.match(r"^\s*([-*+]|\d+\.)\s+", line):
            ordered = bool(re.match(r"^\s*\d+\.\s+", line))
            tag = "ol" if ordered else "ul"
            items = []
            while i < n and re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*+]|\d+\.)\s+", "", lines[i]))
                i += 1
            body = "".join(f"<li>{_inline(x)}</li>" for x in items)
            out.append(f"<{tag}>{body}</{tag}>")
            continue

        # 空行
        if not line.strip():
            i += 1
            continue

        # 普通段落（连续非空行合并）
        buf = []
        while i < n and lines[i].strip() and not re.match(
            r"^\s*(#{1,4}\s|>|\||```|---|\*\*\*|([-*+]|\d+\.)\s)", lines[i]
        ):
            buf.append(lines[i].strip())
            i += 1
        if not buf:
            # A leading pipe without a table (or a partial horizontal rule)
            # must still consume input; otherwise this loop never advances.
            buf.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline(' '.join(buf))}</p>")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--title", default="报告")
    args = parser.parse_args(argv[1:])
    src, title = args.input, args.title
    dst = args.output or src.with_suffix(".html")
    md = src.read_text(encoding="utf-8")
    m = re.search(r"^#\s+(.+)$", md, re.M)
    if m and title == "报告":
        title = m.group(1).strip()
    css = STYLE.read_text(encoding="utf-8")
    doc = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="dark light">'
        f"<title>{html.escape(title)}</title><style>{css}</style></head>"
        f"<body><main>{render(md)}</main></body></html>"
    )
    dst.write_text(doc, encoding="utf-8")
    print(f"✅ {src} → {dst}（{len(doc):,} 字符，样式源：{STYLE.name}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
