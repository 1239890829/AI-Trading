"use client";

/**
 * 仓库追踪面板（2026-09-09 用户指令⑤①）：
 * GitHub「agent」star 分组内各仓库的用途 / 服务功能 / 使用轨迹 / 评估结论。
 * 数据源：docs/kb/05-repo-tracker.md（评估轮后回填，git 留痕）。
 */
import { useCallback, useEffect, useState } from "react";

import { MarkdownView } from "@/components/agent/markdown-view";
import { getAgentKbFile } from "@/lib/api";

const DOC_PATH = "kb/05-repo-tracker.md";

export function RepoTrackerTab() {
  const [content, setContent] = useState<string>("");
  const [loaded, setLoaded] = useState(false);
  const [missing, setMissing] = useState(false);

  const load = useCallback(async () => {
    try {
      const f = await getAgentKbFile(DOC_PATH);
      setContent(f.content);
      setMissing(false);
    } catch {
      setMissing(true);
    }
    setLoaded(true);
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 60_000); // 评估轮回填后自动刷新
    return () => clearInterval(t);
  }, [load]);

  const repos = (content.match(/^## /gm) ?? []).length;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[10px] text-zinc-400">
          GitHub「agent」star 分组的仓库台账：用途 / 服务功能 / 使用轨迹 / 评估结论（<code className="font-mono">docs/kb/05-repo-tracker.md</code>
          ，git 留痕）。仅收录 ≥1000★ 且经实际拉取运行评估的仓库。
        </p>
        {loaded && !missing && (
          <span className="rounded bg-sky-500/10 px-2 py-0.5 text-[10px] text-sky-600 dark:text-sky-300">
            {repos} 个仓库
          </span>
        )}
      </div>
      <section className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
        {!loaded ? (
          <p className="py-8 text-center text-sm text-zinc-400">加载中…</p>
        ) : missing ? (
          <p className="py-8 text-center text-sm text-zinc-400">
            台账尚未生成——完成一轮 agent 分组评估后会写入 docs/kb/05-repo-tracker.md
          </p>
        ) : (
          <MarkdownView content={content} />
        )}
      </section>
    </div>
  );
}
