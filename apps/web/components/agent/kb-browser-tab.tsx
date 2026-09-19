"use client";

/**
 * 知识库浏览面板（2026-09-09 用户指令⑤②；2026-09-12 分层重构）：
 * docs/ 全部 Markdown 的树形目录 + 渲染阅读 + [[KB-ID]]/相对链接面板内跳转。
 * 数据源：/api/agent/kb/tree + /api/agent/kb/file（路径白名单在服务端）。
 *
 * **分层呈现（2026-09-12）**：原先 `kb/` 置顶、其余目录平铺 ⇒ 79 份里 canonical 知识库
 * 只占 11 份，且 22 份归档件与 13 份逐日日志与它并列，用户无法分辨「现行规则」与
 * 「历史结论」（违反 `kb/07-doc-curation.md` 「状态语义不得混用」）。
 * 现按 `tier` 分层：canonical 置顶 → 现役按目录 → **历史与日志折叠**。
 * ⚠️ 折叠只改默认呈现、**不减少可见内容**：折叠区可展开，且**搜索时自动展开**
 * （搜索不展开会让人误判「查不到」，那就把「全量可查」的承诺打掉了）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { MarkdownView } from "@/components/agent/markdown-view";
import { getAgentKbFile, getAgentKbTree, type KbFileMeta } from "@/lib/api";

const dirOf = (f: KbFileMeta) => f.dir || "docs/";

export function KbBrowserTab() {
  const [files, setFiles] = useState<KbFileMeta[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [query, setQuery] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [anchor, setAnchor] = useState("");
  const reader = useRef<HTMLElement>(null);
  const request = useRef(0);

  const openFile = useCallback(async (path: string, fragment = "") => {
    const current = ++request.current;
    setSelected(path);
    setContent("");
    setAnchor("");
    try {
      const f = await getAgentKbFile(path);
      if (current !== request.current) return;
      setContent(f.content);
      setAnchor(fragment);
    } catch {
      if (current !== request.current) return;
      setContent(`> 文档加载失败：${path}`);
    }
  }, []);

  useEffect(() => {
    if (!anchor) return;
    const heading = [...(reader.current?.querySelectorAll<HTMLElement>("[data-md-anchor]") ?? [])]
      .find((node) => node.dataset.mdAnchor === anchor);
    heading?.scrollIntoView({ block: "start" });
  }, [content, anchor]);

  useEffect(() => {
    (async () => {
      try {
        const tree = await getAgentKbTree();
        setFiles(tree.files);
        // 默认打开知识库总索引（kb 目录优先）
        const first = tree.files.find((f) => f.path === "kb/00-INDEX.md") ?? tree.files[0];
        if (first) void openFile(first.path);
        setLoaded(true);
      } catch {
        setFailed(true);
        setLoaded(true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // KB-ID → 所在文件索引（[[KB-XXX]] 跳转用）
  const kbIdIndex = useMemo(() => {
    const m = new Map<string, string>();
    for (const f of files) for (const id of f.kb_ids) if (!m.has(id)) m.set(id, f.path);
    return m;
  }, [files]);

  const onNavigate = useCallback(
    (target: string) => {
      const idMatch = /^(KB-(?:STOCK|TRADE|ENG|DEC)-\d+)$/.exec(target.trim());
      if (idMatch) {
        const path = kbIdIndex.get(idMatch[1]);
        if (path) void openFile(path);
        return;
      }
      // Resolve relative to the open document; only files in the served tree are navigable.
      try {
        const base = "https://docs.invalid/";
        const rooted = target.startsWith("docs/");
        const url = new URL(rooted ? target.slice(5) : target, base + (rooted ? "" : selected ?? ""));
        if (url.origin !== new URL(base).origin) return;
        const relative = decodeURIComponent(url.pathname.slice(1));
        const legacyRoot = target.split("#")[0].replace(/^\.\//, "");
        const path = [relative, legacyRoot].find((p) => files.some((f) => f.path === p));
        if (path) void openFile(path, decodeURIComponent(url.hash.slice(1)));
      } catch {
        // Malformed links in a document must not break the reader.
      }
    },
    [files, kbIdIndex, openFile, selected],
  );

  const searching = query.trim().length > 0;
  const historyVisible = historyOpen || searching;

  // 分层分组：canonical 置顶 → 现役按目录 → 历史与日志（归档 + 时间序列）折叠
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const hit = (f: KbFileMeta) =>
      !q || f.name.toLowerCase().includes(q) || f.kb_ids.some((id) => id.toLowerCase().includes(q));
    const byDir = (fs: KbFileMeta[]) => {
      const m = new Map<string, KbFileMeta[]>();
      for (const f of fs) {
        const d = dirOf(f);
        if (!m.has(d)) m.set(d, []);
        m.get(d)!.push(f);
      }
      return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
    };
    const matched = files.filter(hit);
    const canonical = matched.filter((f) => f.tier === "canonical");
    const current = matched.filter((f) => f.tier === "current");
    const history = matched.filter((f) => f.tier === "history" || f.tier === "timeline");
    return {
      canonical,
      currentByDir: byDir(current),
      historyByDir: byDir(history),
      historyCount: history.length,
      total: matched.length,
    };
  }, [files, query]);

  const FileRow = ({ f }: { f: KbFileMeta }) => (
    <button
      onClick={() => void openFile(f.path)}
      className={`block w-full truncate rounded px-2 py-1 text-left text-xs transition-colors ${
        selected === f.path
          ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
          : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
      }`}
      title={`${f.path}${f.kb_ids.length ? ` · ${f.kb_ids.length} 条 KB` : ""}`}
    >
      {f.name}
      {f.kb_ids.length > 0 && <span className="ml-1 text-[10px] text-sky-700 dark:text-sky-500">KB{f.kb_ids.length}</span>}
    </button>
  );

  return (
    <div className="flex min-h-0 flex-1 gap-3">
      {/* 树形目录 */}
      <aside className="flex w-60 shrink-0 flex-col overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800">
        <div className="border-b border-zinc-100 p-2 dark:border-zinc-800">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜文档 / KB-ID"
            className="h-7 w-full rounded border border-zinc-200 bg-transparent px-2 text-xs outline-none focus:border-up/60 dark:border-zinc-700"
          />
        </div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
          {!loaded ? (
            <p className="px-2 py-1 text-xs text-zinc-600 dark:text-zinc-400">加载中…</p>
          ) : failed ? (
            <p className="px-2 py-1 text-xs text-amber-800 dark:text-amber-500">文档树加载失败（后端不可达）</p>
          ) : groups.total === 0 ? (
            <p className="px-2 py-1 text-xs text-zinc-600 dark:text-zinc-400">无匹配文档（含历史与日志）</p>
          ) : (
            <>
              {groups.canonical.length > 0 && (
                <>
                  <p className="px-2 pt-1 text-[10px] font-medium text-zinc-600 dark:text-zinc-400">
                    知识库（唯一权威）· {groups.canonical.length}
                  </p>
                  {groups.canonical.map((f) => (
                    <FileRow key={f.path} f={f} />
                  ))}
                </>
              )}
              {groups.currentByDir.map(([dir, fs]) => (
                <div key={dir}>
                  <p className="px-2 pt-2 text-[10px] font-medium text-zinc-600 dark:text-zinc-400">
                    现役 · {dir} · {fs.length}
                  </p>
                  {fs.map((f) => (
                    <FileRow key={f.path} f={f} />
                  ))}
                </div>
              ))}
              {groups.historyCount > 0 && (
                <div className="pt-2">
                  <button
                    onClick={() => setHistoryOpen((v) => !v)}
                    disabled={searching}
                    aria-expanded={historyVisible}
                    aria-controls="kb-history-list"
                    title={searching ? "搜索中已自动展开全部目录" : undefined}
                    className="flex w-full items-center justify-between rounded px-2 py-1 text-left text-[10px] font-medium text-zinc-600 transition-colors hover:bg-zinc-100 disabled:cursor-default disabled:hover:bg-transparent dark:text-zinc-400 dark:hover:bg-zinc-900 dark:disabled:hover:bg-transparent"
                  >
                    <span>历史与日志 · {groups.historyCount} 份{searching ? "（已展开）" : ""}</span>
                    <svg
                      viewBox="0 0 12 12"
                      aria-hidden
                      className={`h-3 w-3 shrink-0 transition-transform ${historyVisible ? "rotate-90" : ""}`}
                    >
                      <path
                        d="M4 2l4 4-4 4"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </button>
                  {historyVisible && (
                    <div id="kb-history-list">
                      <p className="px-2 pt-1 text-[10px] text-zinc-600 dark:text-zinc-400">
                        只读历史 / 逐日记录 · 引用前先确认未过时
                      </p>
                      {groups.historyByDir.map(([dir, fs]) => (
                        <div key={dir}>
                          <p className="px-2 pt-2 text-[10px] font-medium text-zinc-600 dark:text-zinc-400">
                            {dir} · {fs.length}
                          </p>
                          {fs.map((f) => (
                            <FileRow key={f.path} f={f} />
                          ))}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </aside>

      {/* 渲染阅读区 */}
      <section ref={reader} className="min-w-0 flex-1 overflow-y-auto rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
        {!selected ? (
          <p className="py-8 text-center text-sm text-zinc-600 dark:text-zinc-400">左侧选择一篇文档</p>
        ) : (
          <>
            <p className="mb-2 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">docs/{selected}</p>
            <MarkdownView content={content} onNavigate={onNavigate} />
          </>
        )}
      </section>
    </div>
  );
}
