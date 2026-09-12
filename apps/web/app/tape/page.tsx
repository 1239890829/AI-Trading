"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ThemesTab } from "@/components/tape/themes-tab";
import { LimitUpTab } from "@/components/tape/limit-up-tab";
import { LimitDownTab } from "@/components/tape/limit-down-tab";
import { LonghuTab } from "@/components/tape/longhu-tab";
import { FadeSwap, PageSkeletonFallback } from "@/components/ui/loading";

/**
 * 盘面页（2026-09-01 系统重构，docs/archive/architecture-redesign.md §一.1.2）：
 * 涨停池 / 题材 / 龙虎榜 合并为一个入口的三个 tab——
 * 同属"盘面生态"参考，分开看要来回切，且除龙虎榜外都消费同一份涨停数据。
 *
 * 2026-09-01 评审 D1：「板块排行」tab 移除——板块数据与工作台详情右列
 * 「板块」页签同源重复（同一条 /api/boards 链路），板块是行业横截面，
 * 与盘面页的短线生态定位不契合；能力保留在工作台详情，零损失。
 *
 * tab 以 ?tab= 查询参数为真相源（可分享、可回退）；其余查询参数由各 tab 自治。
 */

const TABS = [
  { key: "themes", label: "题材梯队" },
  { key: "limitup", label: "涨停生态" },
  { key: "limitdown", label: "跌停" },
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
                    : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
        </div>
        <span className="hidden text-xs text-zinc-600 dark:text-zinc-400 lg:inline">梯队结构 · 涨停证据 · 资金关注</span>
      </div>

      {/* tab 切换统一 fade 过渡（2026-09-04）：h-full 保持子 tab 内部 flex 布局 */}
      <FadeSwap swapKey={tab} className="min-h-0 flex-1">
        {tab === "themes" && <ThemesTab />}
        {tab === "limitup" && <LimitUpTab />}
        {tab === "limitdown" && <LimitDownTab />}
        {tab === "longhu" && <LonghuTab />}
      </FadeSwap>
    </main>
  );
}

export default function TapePage() {
  return (
    <Suspense fallback={<PageSkeletonFallback label="盘面页加载中" />}>
      <TapeInner />
    </Suspense>
  );
}
