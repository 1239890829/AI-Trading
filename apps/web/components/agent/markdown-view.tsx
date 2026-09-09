"use client";

/**
 * 极简 Markdown 渲染器（AI 控制台「知识库 / 仓库追踪」面板专用，2026-09-09）。
 *
 * 零依赖（package.json 无 markdown 库）：覆盖 KB 文档实际使用的语法子集——
 * 标题 / 表格 / 有序无序列表 / 围栏代码块 / 行内代码 / 粗体 / 引用 / 分隔线 /
 * 链接 / [[KB-XXX]] 关联标记。
 *
 * [[KB-XXX]] 与 .md 相对链接 → onNavigate 回调（浏览器面板内跳转，不离开页面）。
 * 安全：纯文本解析后逐节点 React 渲染，无 dangerouslySetInnerHTML，无 XSS 面。
 */
import { ReactNode, useMemo } from "react";

type Props = {
  content: string;
  /** [[KB-ID]] 或 [文本](docs/xx.md) 点击回调 */
  onNavigate?: (target: string) => void;
};

/** 行内标记：[[wiki]] / `code` / **bold** / [label](href) */
function inline(text: string, onNavigate?: Props["onNavigate"], keyBase = ""): ReactNode[] {
  const nodes: ReactNode[] = [];
  const re = /(\[\[[^\]]+\]\])|(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyBase}-${i}`;
    if (tok.startsWith("[[")) {
      const id = tok.slice(2, -2);
      nodes.push(
        <button
          key={key}
          onClick={() => onNavigate?.(id)}
          className="mx-0.5 rounded bg-sky-500/10 px-1 align-baseline text-[11px] font-medium text-sky-600 transition-colors hover:bg-sky-500/20 dark:text-sky-300"
          title={`跳转：${id}`}
        >
          {id}
        </button>,
      );
    } else if (tok.startsWith("`")) {
      nodes.push(
        <code key={key} className="rounded bg-zinc-100 px-1 font-mono text-[11px] dark:bg-zinc-800">
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (tok.startsWith("**")) {
      nodes.push(<strong key={key}>{tok.slice(2, -2)}</strong>);
    } else {
      const mm = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok);
      if (mm) {
        const [, label, href] = mm;
        if (href.endsWith(".md")) {
          nodes.push(
            <button
              key={key}
              onClick={() => onNavigate?.(href)}
              className="text-sky-600 hover:underline dark:text-sky-300"
            >
              {label}
            </button>,
          );
        } else {
          nodes.push(<span key={key}>{label}</span>);
        }
      }
    }
    last = m.index + tok.length;
    i += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function MarkdownView({ content, onNavigate }: Props) {
  const blocks = useMemo(() => {
    const lines = content.split("\n");
    const out: ReactNode[] = [];
    let i = 0;
    let k = 0;
    const kb = () => `b${k++}`;
    while (i < lines.length) {
      const line = lines[i];
      // 围栏代码块
      if (line.startsWith("```")) {
        const buf: string[] = [];
        i += 1;
        while (i < lines.length && !lines[i].startsWith("```")) {
          buf.push(lines[i]);
          i += 1;
        }
        i += 1;
        out.push(
          <pre
            key={kb()}
            className="my-2 overflow-x-auto rounded-lg bg-zinc-100 p-3 font-mono text-[11px] leading-relaxed dark:bg-zinc-900"
          >
            {buf.join("\n")}
          </pre>,
        );
        continue;
      }
      // 表格（连续 | 行；第二行是分隔行）
      if (line.trim().startsWith("|") && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        const cells = (row: string) =>
          row
            .trim()
            .replace(/^\||\|$/g, "")
            .split("|")
            .map((c) => c.trim());
        const header = cells(line);
        i += 2;
        const rows: string[][] = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) {
          rows.push(cells(lines[i]));
          i += 1;
        }
        out.push(
          <div key={kb()} className="my-2 overflow-x-auto">
            <table className="w-full border-collapse text-[11px]">
              <thead>
                <tr className="border-b border-zinc-200 dark:border-zinc-800">
                  {header.map((h, hi) => (
                    <th key={hi} className="px-2 py-1 text-left font-medium text-zinc-500">
                      {inline(h, onNavigate, `th${hi}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, ri) => (
                  <tr key={ri} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                    {r.map((c, ci) => (
                      <td key={ci} className="px-2 py-1 align-top text-zinc-700 dark:text-zinc-200">
                        {inline(c, onNavigate, `td${ri}-${ci}`)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>,
        );
        continue;
      }
      // 标题
      const hm = /^(#{1,4})\s+(.*)$/.exec(line);
      if (hm) {
        const level = hm[1].length;
        const size = level === 1 ? "text-base font-semibold" : level === 2 ? "text-sm font-semibold" : "text-xs font-semibold";
        out.push(
          <div key={kb()} className={`mt-3 mb-1 ${size} text-zinc-900 dark:text-zinc-50`}>
            {inline(hm[2], onNavigate, `h${level}`)}
          </div>,
        );
        i += 1;
        continue;
      }
      // 无序/有序列表（连续段）
      if (/^\s*[-*]\s+/.test(line) || /^\s*\d+\.\s+/.test(line)) {
        const ordered = /^\s*\d+\.\s+/.test(line);
        const items: string[] = [];
        while (i < lines.length && (/^\s*[-*]\s+/.test(lines[i]) || /^\s*\d+\.\s+/.test(lines[i]))) {
          items.push(lines[i].replace(/^\s*(?:[-*]|\d+\.)\s+/, ""));
          i += 1;
        }
        const ListTag = ordered ? "ol" : "ul";
        out.push(
          <ListTag
            key={kb()}
            className={`my-1.5 space-y-1 pl-4 text-xs leading-relaxed text-zinc-700 dark:text-zinc-200 ${
              ordered ? "list-decimal" : "list-disc"
            }`}
          >
            {items.map((it, ii) => (
              <li key={ii}>{inline(it, onNavigate, `li${ii}`)}</li>
            ))}
          </ListTag>,
        );
        continue;
      }
      // 引用
      if (line.startsWith(">")) {
        const buf: string[] = [];
        while (i < lines.length && lines[i].startsWith(">")) {
          buf.push(lines[i].replace(/^>\s?/, ""));
          i += 1;
        }
        out.push(
          <blockquote
            key={kb()}
            className="my-2 border-l-2 border-zinc-300 pl-2 text-[11px] text-zinc-500 dark:border-zinc-700 dark:text-zinc-400"
          >
            {inline(buf.join(" "), onNavigate, `q${k}`)}
          </blockquote>,
        );
        continue;
      }
      // 分隔线
      if (/^\s*---+\s*$/.test(line)) {
        out.push(<hr key={kb()} className="my-3 border-zinc-200 dark:border-zinc-800" />);
        i += 1;
        continue;
      }
      // 空行
      if (line.trim() === "") {
        i += 1;
        continue;
      }
      // 普通段落（连续非特殊行）
      const buf: string[] = [];
      while (
        i < lines.length &&
        lines[i].trim() !== "" &&
        !lines[i].startsWith("#") &&
        !lines[i].trim().startsWith("|") &&
        !lines[i].startsWith("```") &&
        !/^\s*[-*]\s+/.test(lines[i]) &&
        !/^\s*\d+\.\s+/.test(lines[i]) &&
        !lines[i].startsWith(">") &&
        !/^\s*---+\s*$/.test(lines[i])
      ) {
        buf.push(lines[i]);
        i += 1;
      }
      out.push(
        <p key={kb()} className="my-1.5 text-xs leading-relaxed text-zinc-700 dark:text-zinc-200">
          {inline(buf.join(" "), onNavigate, `p${k}`)}
        </p>,
      );
    }
    return out;
  }, [content, onNavigate]);

  return <div className="max-w-none">{blocks}</div>;
}
