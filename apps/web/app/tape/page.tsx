"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ThemesTab } from "@/components/tape/themes-tab";
import { LimitUpTab } from "@/components/tape/limit-up-tab";
import { BoardsTab } from "@/components/tape/boards-tab";
import { LonghuTab } from "@/components/tape/longhu-tab";

/**
 * 盘面页（2026-09-01 系统重构，docs/architecture-redesign.md §一.1.2）：
 * 涨停池 / 题材 / 板块 / 龙虎榜 四页合并为一个入口的四个 tab——
 * 它们同属"盘面生态"参考，分开看要来回切，且除龙虎榜外都消费同一份涨停数据。
 *
 * tab 以 ?tab= 查询参数为真相源（可分享、可回退）；其余查询参数由各 tab 自治。
 */

const TABS = [
  { key: "themes", label: "题材梯队" },
  { key: "limitup", label: "涨停生态" },
  { key: "boards", label: "板块排行" },
  { key: "longhu", label: "龙虎榜" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function TapeInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const raw = sp.get("tab");
  const tab: TabKey = TABS.some((t) => t.key === raw) ? (raw as TabKey) : "themes";

  function switchTab(k: TabKey) {
    // 保留其余查询参数（日期/聚焦等在 tab 间共享 URL 空间）
    const p = new URLSearchParams(sp.toString());
    p.set("tab", k);
    const qs = p.toString();
    router.replace(`/tape?${qs}`, { scroll: false });
  }

  return (
    <main className="mx-auto flex h-full w-full max-w-[1600px] flex-col px-4 py-3">
      <div className="mb-3 flex shrink-0 flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-4">
          <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">盘面</h1>
          <nav className="flex items-center gap-1" aria-label="盘面子页签">
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => switchTab(t.key)}
                aria-current={tab === t.key ? "page" : undefined}
                className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                  tab === t.key
                    ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
        </div>
        <span className="hidden text-xs text-zinc-400 lg:inline">
          梯队结构 · 涨停证据 · 板块排行 · 资金关注
        </span>
      </div>

      <div className="min-h-0 flex-1">
        {tab === "themes" && <ThemesTab />}
        {tab === "limitup" && <LimitUpTab />}
        {tab === "boards" && <BoardsTab />}
        {tab === "longhu" && <LonghuTab />}
      </div>
    </main>
  );
}

export default function TapePage() {
  return (
    <Suspense fallback={<main className="p-6 text-sm text-zinc-400">加载中…</main>}>
      <TapeInner />
    </Suspense>
  );
}
