"use client";

/**
 * 助手回复渲染器：轻量 Markdown（标题/列表/表格/引用/行内代码/加粗/链接/围栏代码）
 * + 实体链接（个股/题材命中 → 可点击跳转）。
 *
 * 不引第三方 markdown 库：实体链接需要接管行内文本渲染，第三方渲染器的
 * AST 钩子反而绕；且助手回复的 markdown 面很窄，自写 ~150 行可控可测。
 * 解析是纯函数（parseBlocks），行内渲染是组件——流式追加时全量重解析，
 * 聊天文本量级（几 KB）下开销可忽略。
 */
import { Fragment, type ReactNode } from "react";
import type { EntityMatch, EntityMatcher } from "@/lib/entity-links";
import { NAV_LABELS, isAllowedNav, type NavKey } from "@/lib/nav-targets";

// ---------------------------------------------------------------- 块解析

export type Block =
  | { kind: "code"; content: string }
  | { kind: "heading"; level: number; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "quote"; lines: string[] }
  | { kind: "table"; header: string[]; rows: string[][] }
  | { kind: "para"; lines: string[] };

export function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  const lines = text.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trimStart().startsWith("```")) {
      const content: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) {
        content.push(lines[i]);
        i += 1;
      }
      i += 1; // 吃掉闭合围栏（没有就到尾）
      blocks.push({ kind: "code", content: content.join("\n") });
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] });
      i += 1;
      continue;
    }
    if (/^\|.*\|?\s*$/.test(line.trim())) {
      const tableLines: string[] = [];
      while (i < lines.length && /^\|.*\|?\s*$/.test(lines[i].trim())) {
        tableLines.push(lines[i].trim());
        i += 1;
      }
      blocks.push(parseTable(tableLines));
      continue;
    }
    const bullet = /^[-*]\s+(.*)$/.exec(line.trim());
    const ordered = /^\d+[.、)]\s+(.*)$/.exec(line.trim());
    if (bullet || ordered) {
      const items: string[] = [];
      const isOrdered = Boolean(ordered);
      const itemRe = isOrdered ? /^\d+[.、)]\s+/ : /^[-*]\s+/;
      while (i < lines.length) {
        const m2 = itemRe.exec(lines[i].trim());
        if (!m2) break;
        items.push(lines[i].trim().replace(itemRe, ""));
        i += 1;
      }
      blocks.push({ kind: "list", ordered: isOrdered, items });
      continue;
    }
    if (line.trimStart().startsWith(">")) {
      const quoteLines: string[] = [];
      while (i < lines.length && lines[i].trimStart().startsWith(">")) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ""));
        i += 1;
      }
      blocks.push({ kind: "quote", lines: quoteLines });
      continue;
    }
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length
      && lines[i].trim()
      && !/^(#{1,4}\s|[-*]\s|\d+[.、)]\s|>|\||```)/.test(lines[i].trim())
    ) {
      para.push(lines[i].trim());
      i += 1;
    }
    blocks.push({ kind: "para", lines: para });
  }
  return blocks;
}

function parseTable(lines: string[]): Block {
  const splitRow = (row: string): string[] =>
    row.replace(/^\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
  // 第二行是分隔行（---）则首行为表头，否则全部是数据行
  const hasHeader = lines.length >= 2 && /^\|?[\s:|-]+\|?$/.test(lines[1]) && lines[1].includes("-");
  const header = hasHeader ? splitRow(lines[0]) : [];
  const bodyLines = hasHeader ? lines.slice(2) : lines;
  const rows = bodyLines.map(splitRow).filter((r) => r.some((c) => c !== ""));
  return { kind: "table", header, rows };
}

// ---------------------------------------------------------------- 行内渲染

interface InlineProps {
  text: string;
  match: EntityMatcher;
  onNavigate: (m: EntityMatch) => void;
}

/** 行内 token：行内代码 > markdown 链接 > 加粗 > 实体匹配（只作用于纯文本段）。 */
function renderInline({ text, match, onNavigate }: InlineProps): ReactNode[] {
  const nodes: ReactNode[] = [];
  const re = /(`[^`]+`)|(\[[^\]]*\]\([^)\s]+\))|(\*\*[^*]+\*\*)/g;
  let last = 0;
  let key = 0;
  let m: RegExpExecArray | null;
  const pushPlain = (seg: string) => {
    if (!seg) return;
    const hits = match(seg);
    if (!hits.length) {
      nodes.push(seg);
      return;
    }
    let pos = 0;
    for (const h of hits) {
      const idx = seg.indexOf(h.text, pos);
      if (idx < 0) continue;
      if (idx > pos) nodes.push(seg.slice(pos, idx));
      if (h.type === "nav") {
        nodes.push(
          <button
            key={`e${key++}`}
            type="button"
            title={`${h.name} — 点击跳转到${NAV_LABELS[h.key as NavKey] ?? "对应功能"}`}
            className="mx-0.5 rounded bg-emerald-500/10 px-1 font-medium text-emerald-600 underline decoration-dotted underline-offset-2 hover:bg-emerald-500/20 dark:text-emerald-400"
            onClick={() => onNavigate(h)}
          >
            {h.text}
            <span aria-hidden className="ml-0.5 text-[0.7em]">↗</span>
          </button>,
        );
      } else {
        nodes.push(
          h.type === "stock" ? (
            <button
              key={`e${key++}`}
              type="button"
              title={`${h.name}（${h.code}）— 点击打开个股详情`}
              className="mx-0.5 rounded bg-sky-500/10 px-1 font-medium text-sky-600 underline decoration-dotted underline-offset-2 hover:bg-sky-500/20 dark:text-sky-400"
              onClick={() => onNavigate(h)}
            >
              {h.text}
            </button>
          ) : (
            <button
              key={`e${key++}`}
              type="button"
              title={`${h.name} — 点击查看题材梯队`}
              className="mx-0.5 rounded bg-violet-500/10 px-1 font-medium text-violet-600 underline decoration-dotted underline-offset-2 hover:bg-violet-500/20 dark:text-violet-400"
              onClick={() => onNavigate(h)}
            >
              {h.text}
            </button>
          ),
        );
      }
      pos = idx + h.text.length;
    }
    if (pos < seg.length) nodes.push(seg.slice(pos));
  };
  while ((m = re.exec(text)) !== null) {
    pushPlain(text.slice(last, m.index));
    const token = m[0];
    if (token.startsWith("`")) {
      nodes.push(
        <code
          key={`c${key++}`}
          className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[0.85em] dark:bg-zinc-800"
        >
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith("[")) {
      const lm = /^\[([^\]]*)\]\(([^)\s]+)\)$/.exec(token);
      // 守卫（2026-09-06）：模型输出/正文里的链接原本是任意 href + target=_blank，
      // 等于给回复内容开了站外跳转口子。现在只有**站内白名单路径**才渲染成链接，
      // 其余一律降级为纯文本（保留可见文字，去掉可点击性）。
      if (lm && isAllowedNav(lm[2])) {
        nodes.push(
          <a
            key={`l${key++}`}
            href={lm[2]}
            className="text-sky-600 underline underline-offset-2 dark:text-sky-400"
          >
            {lm[1] || lm[2]}
          </a>,
        );
      } else {
        nodes.push(lm ? lm[1] || token : token);
      }
    } else {
      nodes.push(
        <strong key={`b${key++}`} className="font-semibold text-zinc-900 dark:text-zinc-50">
          {token.slice(2, -2)}
        </strong>,
      );
    }
    last = m.index + token.length;
  }
  pushPlain(text.slice(last));
  return nodes;
}

// ---------------------------------------------------------------- 组件

interface RichTextProps {
  text: string;
  matcher: EntityMatcher;
  onNavigate: (m: EntityMatch) => void;
}

export function RichText({ text, matcher, onNavigate }: RichTextProps) {
  const blocks = parseBlocks(text);
  const inline = (t: string) => renderInline({ text: t, match: matcher, onNavigate });
  return (
    <div className="space-y-2 text-sm leading-relaxed">
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "code":
            return (
              <pre
                key={i}
                className="overflow-x-auto rounded-lg bg-zinc-900 p-3 font-mono text-xs text-zinc-100 dark:bg-black/60"
              >
                <code>{b.content}</code>
              </pre>
            );
          case "heading": {
            const cls =
              b.level <= 2
                ? "text-[0.95rem] font-semibold text-zinc-900 dark:text-zinc-50"
                : "text-sm font-semibold text-zinc-900 dark:text-zinc-50";
            return (
              <div key={i} className={cls}>
                {inline(b.text)}
              </div>
            );
          }
          case "list":
            return b.ordered ? (
              <ol key={i} className="list-decimal space-y-1 pl-5">
                {b.items.map((it, j) => (
                  <li key={j}>{inline(it)}</li>
                ))}
              </ol>
            ) : (
              <ul key={i} className="list-disc space-y-1 pl-5">
                {b.items.map((it, j) => (
                  <li key={j}>{inline(it)}</li>
                ))}
              </ul>
            );
          case "quote":
            return (
              <blockquote
                key={i}
                className="border-l-2 border-zinc-300 pl-3 text-zinc-500 dark:border-zinc-700 dark:text-zinc-400"
              >
                {b.lines.map((l, j) => (
                  <Fragment key={j}>
                    {j > 0 && <br />}
                    {inline(l)}
                  </Fragment>
                ))}
              </blockquote>
            );
          case "table":
            return (
              <div key={i} className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
                <table className="w-full text-xs">
                  {b.header.length > 0 && (
                    <thead className="bg-zinc-50 dark:bg-zinc-900">
                      <tr>
                        {b.header.map((c, j) => (
                          <th key={j} className="border-b border-zinc-200 px-2 py-1.5 text-left font-medium text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
                            {inline(c)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                  )}
                  <tbody>
                    {b.rows.map((r, j) => (
                      <tr key={j} className={j % 2 ? "bg-zinc-50/60 dark:bg-zinc-900/40" : ""}>
                        {r.map((c, k) => (
                          <td key={k} className="border-b border-zinc-100 px-2 py-1.5 last:border-b-0 dark:border-zinc-800/60">
                            {inline(c)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "para":
          default:
            return (
              <p key={i}>
                {b.lines.map((l, j) => (
                  <Fragment key={j}>
                    {j > 0 && <br />}
                    {inline(l)}
                  </Fragment>
                ))}
              </p>
            );
        }
      })}
    </div>
  );
}
