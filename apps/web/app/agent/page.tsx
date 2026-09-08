"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { TaskCenter } from "@/components/agent/task-center";
import { AlertsTab } from "@/components/research/alerts-tab";
import { ReviewTab } from "@/components/research/review-tab";
import { FadeSwap, PageSkeletonFallback } from "@/components/ui/loading";

/**
 * AI 控制台（docs/ai-agent-console-plan.md P0）。
 *
 * 定位：助手从"只问答"升级为"大脑 + 执行层"的系统侧面板——任务、复盘、告警、
 * 历史统一收纳在这里；悬浮球保留轻量问答与即时提醒。
 *
 * 2026-09-08 研究页（/research）下线：回测取消（后端引擎保留，改由任务中心
 * 以任务形态调用），复盘与预警原样迁入本面板（避免能力空窗）；AI 判读层、
 * 数据源、参数配置、审计视图为 P1/P2（见方案 §3.5）。
 */

const TABS = [
  { key: "tasks", label: "任务中心" },
  { key: "review", label: "复盘" },
  { key: "alerts", label: "提醒与告警" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function AgentInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const raw = sp.get("tab");
  const tab: TabKey = TABS.some((t) => t.key === raw) ? (raw as TabKey) : "tasks";
  // 复盘深链日期（助手跳转 / 分享）：只接受 YYYY-MM-DD，非法值当没传
  const rawDate = sp.get("date");
  const reviewDate = rawDate && /^\d{4}-\d{2}-\d{2}$/.test(rawDate) ? rawDate : undefined;

  function switchTab(k: TabKey) {
    const p = new URLSearchParams(sp.toString());
    p.set("tab", k);
    router.replace(`/agent?${p.toString()}`, { scroll: false });
  }

  return (
    <main className="mx-auto flex h-full w-full max-w-[1600px] flex-col px-4 py-3">
      <div className="mb-3 flex shrink-0 flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-4">
          <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">AI 控制台</h1>
          <nav className="flex items-center gap-1" aria-label="AI 控制台子页签">
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
          大脑 + 执行层 · 首批仅 L0 只读/生成任务 · 每次执行全程留痕 · 不构成买卖建议
        </span>
      </div>

      <FadeSwap swapKey={tab} className="min-h-0 flex-1">
        {tab === "tasks" && <TaskCenter />}
        {tab === "review" && <ReviewTab focusDate={reviewDate} />}
        {tab === "alerts" && <AlertsTab />}
      </FadeSwap>
    </main>
  );
}

export default function AgentPage() {
  return (
    <Suspense fallback={<PageSkeletonFallback label="AI 控制台加载中" />}>
      <AgentInner />
    </Suspense>
  );
}
