"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { BacktestTab } from "@/components/research/backtest-tab";
import { AlertsTab } from "@/components/research/alerts-tab";
import { ReviewTab } from "@/components/research/review-tab";
import { FadeSwap, PageSkeletonFallback } from "@/components/ui/loading";

/**
 * 研究页（2026-09-01 系统重构，docs/architecture-redesign.md §一.1.3）：
 * 回测 / 预警 / 复盘 等低频研究工具的折叠入口，不占一级导航黄金位——
 * 预警的价值在"触发时通知"，回测的价值在"策略验证"，都不需要每天打开。
 *
 * 复盘双轨：每日精选页展示 picks 归因（当日操作层面）；本页复盘 tab 展示
 * 方法论闭环（报告→改进项→采纳统计，review.py 6 端点，评审 A2）。
 */

const TABS = [
  { key: "backtest", label: "回测" },
  { key: "alerts", label: "预警" },
  { key: "review", label: "复盘" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function ResearchInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const raw = sp.get("tab");
  const tab: TabKey = TABS.some((t) => t.key === raw) ? (raw as TabKey) : "backtest";
  // 复盘深链日期（助手跳转 / 分享）：只接受 YYYY-MM-DD，非法值当没传
  const rawDate = sp.get("date");
  const reviewDate = rawDate && /^\d{4}-\d{2}-\d{2}$/.test(rawDate) ? rawDate : undefined;

  function switchTab(k: TabKey) {
    const p = new URLSearchParams(sp.toString());
    p.set("tab", k);
    router.replace(`/research?${p.toString()}`, { scroll: false });
  }

  return (
    <main className="mx-auto flex h-full w-full max-w-[1600px] flex-col px-4 py-3">
      <div className="mb-3 flex shrink-0 flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-4">
          <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">研究</h1>
          <nav className="flex items-center gap-1" aria-label="研究子页签">
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
          低频研究工具 · 复盘归因见每日精选页 · 不构成买卖建议
        </span>
      </div>

      <FadeSwap swapKey={tab} className="min-h-0 flex-1">
        {tab === "backtest" && <BacktestTab />}
        {tab === "alerts" && <AlertsTab />}
        {tab === "review" && <ReviewTab focusDate={reviewDate} />}
      </FadeSwap>
    </main>
  );
}

export default function ResearchPage() {
  return (
    <Suspense fallback={<PageSkeletonFallback label="研究页加载中" />}>
      <ResearchInner />
    </Suspense>
  );
}
