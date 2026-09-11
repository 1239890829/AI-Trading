"use client";

/**
 * 仓库追踪面板（2026-09-09 用户指令⑤①；13:4x 改版：左右分栏 + 分条目详情）。
 *
 * 左右分栏交互与知识库模块同构（交互一致性纪律）：
 * - 左列条目上下结构：上行 仓库名 + 星数，下行 分类标签（结论状态 + 语言）
 * - 点击条目 → 右侧渲染该仓库完整详情（用途/评估/可借鉴/使用轨迹/结论）
 * - 「筛选经验」「发现日志」等元段落作为列表尾部的特殊条目
 *
 * 数据源：docs/kb/05-repo-tracker.md（agent 分组台账，周度发现轮回填）。
 *
 * 布局纪律（本文件曾两次踩坑，记录在案）：本组件根节点必须 `h-full min-h-0`，
 * 左右两列必须 `min-w-0/min-h-0 + overflow-y-auto`——高度链一旦在中间层断掉
 * （父级非 flex/未约束高），overflow-auto 永远不生效，内容超高不可滚。
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { MarkdownView } from "@/components/agent/markdown-view";
import { getAgentKbFile } from "@/lib/api";

const DOC_PATH = "kb/05-repo-tracker.md";

interface Section {
  /** 条目标题原文（### 或 ## 后的内容） */
  title: string;
  /** 渲染用正文（含标题行） */
  body: string;
  /** 仓库条目为 true（含 full_name/stars 解析）；元段落为 false */
  isRepo: boolean;
  status: string | null;
}

const STATUS_LABEL: Record<string, string> = {
  "✅": "已采纳",
  "🔶": "试用中",
  "⏳": "候选",
  "❌": "淘汰",
};
const STATUS_CLS: Record<string, string> = {
  "✅": "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  "🔶": "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  "⏳": "bg-amber-500/10 text-amber-800 dark:text-amber-300",
  "❌": "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400",
};

/** 解析台账：### 开头=仓库条目，## 开头=元段落（筛选经验/发现日志等）。 */
function parseSections(content: string): Section[] {
  const lines = content.split("\n");
  const sections: Section[] = [];
  let cur: { title: string; body: string[]; isRepo: boolean } | null = null;
  const flush = () => {
    if (cur) {
      const body = cur.body.join("\n").trim();
      const status = (["✅", "🔶", "⏳", "❌"].find((s) => body.includes(s)) ?? null);
      sections.push({ title: cur.title, body, isRepo: cur.isRepo, status });
    }
  };
  for (const line of lines) {
    if (line.startsWith("### ")) {
      flush();
      cur = { title: line.slice(4).trim(), body: [line], isRepo: true };
    } else if (line.startsWith("## ")) {
      flush();
      cur = { title: line.slice(3).trim(), body: [line], isRepo: false };
    } else if (cur) {
      cur.body.push(line);
    }
  }
  flush();
  return sections;
}

/** 仓库标题形如 `owner/repo — 7.5k★ Go`；解析出全名/星数/语言。 */
function parseTitle(title: string): { name: string; stars: string; lang: string } {
  const m = /^(.+?)\s+—\s*(.+?★)\s*(\S*)$/.exec(title);
  if (m) return { name: m[1].trim(), stars: m[2].trim(), lang: m[3].trim() };
  return { name: title, stars: "", lang: "" };
}

export function RepoTrackerTab() {
  const [content, setContent] = useState<string>("");
  const [loaded, setLoaded] = useState(false);
  const [missing, setMissing] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

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

  // P1-27 收编 usePollingFetch（挂载即拉 + 定时轮询），语义不变
  usePollingFetch(load, 60_000); // 评估轮回填后自动刷新

  const sections = useMemo(() => parseSections(content), [content]);
  const repos = useMemo(() => sections.filter((s) => s.isRepo), [sections]);
  const metas = useMemo(() => sections.filter((s) => !s.isRepo && !["分组收录", "状态定义"].includes(s.title)), [sections]);

  // 默认选中第一个仓库：渲染期 adjust-state（当帧生效）。
  // 原写法是 effect + `eslint-disable exhaustive-deps`，同时踩两条 hook 规则（P1-27）。
  if (sections.length && selected == null) setSelected(sections[0].title);

  const selectedSection = sections.find((s) => s.title === selected) ?? null;

  const Item = ({ s }: { s: Section }) => {
    const active = selected === s.title;
    if (!s.isRepo) {
      return (
        <button
          onClick={() => setSelected(s.title)}
          className={`block w-full rounded-lg px-2 py-1.5 text-left text-xs transition-colors ${
            active ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900"
          }`}
        >
          {s.title}
        </button>
      );
    }
    const { name, stars, lang } = parseTitle(s.title);
    return (
      <button
        onClick={() => setSelected(s.title)}
        className={`block w-full rounded-lg px-2 py-1.5 text-left transition-colors ${
          active ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : "hover:bg-zinc-100 dark:hover:bg-zinc-900"
        }`}
      >
        {/* 上：仓库名 + 星数 */}
        <div className="flex items-baseline justify-between gap-2">
          <span className={`truncate font-mono text-xs font-medium ${active ? "text-white dark:text-zinc-900" : "text-zinc-800 dark:text-zinc-100"}`}>
            {name}
          </span>
          <span className={`shrink-0 font-mono text-[10px] ${active ? "text-zinc-600 dark:text-zinc-400" : "text-amber-800 dark:text-amber-300"}`}>
            {stars}
          </span>
        </div>
        {/* 下：分类标签（结论状态 + 语言） */}
        <div className="mt-1 flex flex-wrap items-center gap-1">
          {s.status && (
            <span className={`rounded px-1 text-[10px] ${STATUS_CLS[s.status] ?? ""} ${active ? "opacity-90" : ""}`}>
              {STATUS_LABEL[s.status] ?? s.status}
            </span>
          )}
          {lang && (
            <span className={`rounded border px-1 text-[10px] ${active ? "border-zinc-500 text-zinc-700 dark:border-zinc-600 dark:text-zinc-400" : "border-zinc-200 text-zinc-600 dark:border-zinc-700"}`}>
              {lang}
            </span>
          )}
        </div>
      </button>
    );
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-400">
          GitHub「agent」star 分组仓库台账：用途 / 服务功能 / 使用轨迹 / 评估结论（仅 ≥1000★ 实拉实跑评估）
        </p>
        {loaded && !missing && (
          <span className="rounded bg-sky-500/10 px-2 py-0.5 text-[10px] text-sky-700 dark:text-sky-300">
            {repos.length} 个仓库
          </span>
        )}
      </div>

      {(!loaded || missing) ? (
        <section className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto rounded-xl border border-zinc-200 dark:border-zinc-800">
          <p className="py-8 text-center text-sm text-zinc-600 dark:text-zinc-400">
            {loaded ? "台账尚未生成——完成一轮 agent 分组评估后写入 docs/kb/05-repo-tracker.md" : "加载中…"}
          </p>
        </section>
      ) : (
        <div className="flex min-h-0 flex-1 gap-3">
          {/* 左：条目列表（上下结构：名称+星数 / 分类标签） */}
          <aside className="flex w-64 shrink-0 flex-col overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800">
            <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto p-2">
              <p className="px-2 pb-1 pt-0.5 text-[10px] font-medium text-zinc-600 dark:text-zinc-400">仓库（agent 分组）</p>
              {repos.map((s) => (
                <Item key={s.title} s={s} />
              ))}
              {metas.length > 0 && (
                <>
                  <p className="px-2 pb-1 pt-2 text-[10px] font-medium text-zinc-600 dark:text-zinc-400">闭环机制</p>
                  {metas.map((s) => (
                    <Item key={s.title} s={s} />
                  ))}
                </>
              )}
            </div>
          </aside>

          {/* 右：所选仓库详情（用途/评估/可借鉴/轨迹/结论） */}
          <section className="min-w-0 flex-1 overflow-y-auto rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
            {selectedSection ? (
              <MarkdownView content={selectedSection.body} />
            ) : (
              <p className="py-8 text-center text-sm text-zinc-600 dark:text-zinc-400">左侧选择一个仓库</p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
