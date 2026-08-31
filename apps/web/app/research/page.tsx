"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { BacktestTab } from "@/components/research/backtest-tab";
import { AlertsTab } from "@/components/research/alerts-tab";

/**
 * 研究页（2026-09-01 系统重构，docs/architecture-redesign.md §一.1.3）：
 * 回测 / 预警 等低频研究工具的折叠入口，不占一级导航黄金位——
 * 预警的价值在"触发时通知"，回测的价值在"策略验证"，都不需要每天打开。
 *
 * 复盘报告已并入每日精选页（复盘归因区）；参数扫描为后端脚本
 * （backend/scripts/replay_sweep.py），不设页面。
 */

const TABS = [
  { key: "backtest", label: "回测" },
  { key: "alerts", label: "预警" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function ResearchInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const raw = sp.get("tab");
  const tab: TabKey = TABS.some((t) => t.key === raw) ? (raw as TabKey) : "backtest";

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

      <div className="min-h-0 flex-1">
        {tab === "backtest" && <BacktestTab />}
        {tab === "alerts" && <AlertsTab />}
      </div>
    </main>
  );
}

export default function ResearchPage() {
  return (
    <Suspense fallback={<main className="p-6 text-sm text-zinc-400">加载中…</main>}>
      <ResearchInner />
    </Suspense>
  );
}
