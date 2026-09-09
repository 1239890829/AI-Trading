"use client";

/**
 * 知识库浏览面板（2026-09-09 用户指令⑤②）：
 * docs/ 全部 Markdown 的树形目录 + 渲染阅读 + [[KB-ID]]/相对链接面板内跳转。
 * 数据源：/api/agent/kb/tree + /api/agent/kb/file（路径白名单在服务端）。
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { MarkdownView } from "@/components/agent/markdown-view";
import { getAgentKbFile, getAgentKbTree, type KbFileMeta } from "@/lib/api";

export function KbBrowserTab() {
  const [files, setFiles] = useState<KbFileMeta[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [query, setQuery] = useState("");

  const openFile = useCallback(async (path: string) => {
    setSelected(path);
    try {
      const f = await getAgentKbFile(path);
      setContent(f.content);
    } catch {
      setContent(`> 文档加载失败：${path}`);
    }
  }, []);

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
      const path = idMatch ? kbIdIndex.get(idMatch[1]) : target.replace(/^\.\//, "");
      if (path) void openFile(path);
    },
    [kbIdIndex, openFile],
  );

  // 树形分组：kb/ 知识库置顶，其余按目录
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const hit = (f: KbFileMeta) =>
      !q || f.name.toLowerCase().includes(q) || f.kb_ids.some((id) => id.toLowerCase().includes(q));
    const kbFiles = files.filter((f) => f.dir === "kb" && hit(f));
    const rest = files.filter((f) => f.dir !== "kb" && hit(f));
    const byDir = new Map<string, KbFileMeta[]>();
    for (const f of rest) {
      const d = f.dir || "docs/";
      if (!byDir.has(d)) byDir.set(d, []);
      byDir.get(d)!.push(f);
    }
    return { kbFiles, byDir: [...byDir.entries()].sort(([a], [b]) => a.localeCompare(b)) };
  }, [files, query]);

  const FileRow = ({ f }: { f: KbFileMeta }) => (
    <button
      onClick={() => void openFile(f.path)}
      className={`block w-full truncate rounded px-2 py-1 text-left text-xs transition-colors ${
        selected === f.path
          ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
          : "text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
      }`}
      title={`${f.path}${f.kb_ids.length ? ` · ${f.kb_ids.length} 条 KB` : ""}`}
    >
      {f.name}
      {f.kb_ids.length > 0 && <span className="ml-1 text-[10px] text-sky-500">KB{f.kb_ids.length}</span>}
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
            <p className="px-2 py-1 text-xs text-zinc-400">加载中…</p>
          ) : failed ? (
            <p className="px-2 py-1 text-xs text-amber-500">文档树加载失败（后端不可达）</p>
          ) : (
            <>
              {groups.kbFiles.length > 0 && (
                <>
                  <p className="px-2 pt-1 text-[10px] font-medium text-zinc-400">知识库（canonical）</p>
                  {groups.kbFiles.map((f) => (
                    <FileRow key={f.path} f={f} />
                  ))}
                </>
              )}
              {groups.byDir.map(([dir, fs]) => (
                <div key={dir}>
                  <p className="px-2 pt-2 text-[10px] font-medium text-zinc-400">{dir}</p>
                  {fs.map((f) => (
                    <FileRow key={f.path} f={f} />
                  ))}
                </div>
              ))}
            </>
          )}
        </div>
      </aside>

      {/* 渲染阅读区 */}
      <section className="min-w-0 flex-1 overflow-y-auto rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
        {!selected ? (
          <p className="py-8 text-center text-sm text-zinc-400">左侧选择一篇文档</p>
        ) : (
          <>
            <p className="mb-2 font-mono text-[10px] text-zinc-400">docs/{selected}</p>
            <MarkdownView content={content} onNavigate={onNavigate} />
          </>
        )}
      </section>
    </div>
  );
}
