"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { EvolutionTab } from "@/components/agent/evolution-tab";
import { KbBrowserTab } from "@/components/agent/kb-browser-tab";
import { RepoTrackerTab } from "@/components/agent/repo-tracker-tab";
import { ParamsTab } from "@/components/agent/params-tab";
import { StrategyHealthTab } from "@/components/agent/strategy-health-tab";
import { TaskCenter } from "@/components/agent/task-center";
import { AlertsTab } from "@/components/research/alerts-tab";
import { ProductionOperations } from "@/components/agent/production-operations";
import { agentArea } from "@/lib/task-navigation";
import Link from "next/link";
import { LeaderResearchPanel } from "@/components/hunting/leader-research-panel";
import { ReviewTab } from "@/components/research/review-tab";
import { FadeSwap, PageSkeletonFallback } from "@/components/ui/loading";

/**
 * AI 控制台（docs/summary/ai-evolution.md P0）。
 *
 * 定位：助手从"只问答"升级为"大脑 + 执行层"的系统侧面板——任务、复盘、告警、
 * 历史统一收纳在这里；悬浮球保留轻量问答与即时提醒。
 *
 * 2026-09-08 研究页（/research）下线：回测取消（后端引擎保留，改由任务中心
 * 以任务形态调用），复盘与预警原样迁入本面板（避免能力空窗）；AI 判读层、
 * 数据源、参数配置、审计视图为 P1/P2（见方案 §3.5）。
 */

const MAINTENANCE_TABS = [
  { key: "operations", label: "生产兜底" },
  { key: "evolution", label: "进化" },
  { key: "tasks", label: "任务中心" },
  { key: "review", label: "改进项处置" },
  { key: "alerts", label: "提醒与告警" },
  { key: "params", label: "参数配置" },
  { key: "strategies", label: "策略健康" },
  { key: "repos", label: "仓库追踪" },

] as const;

const RESEARCH_TABS = [{ key: "review", label: "日度复盘" }, { key: "leaders", label: "龙头研究" }, { key: "kb", label: "知识与反证" }, { key: "strategies", label: "方法状态" }] as const;
type TabKey = (typeof MAINTENANCE_TABS)[number]["key"] | (typeof RESEARCH_TABS)[number]["key"];

function AgentInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const raw = sp.get("tab");
  const area = agentArea(sp.get("area"), raw);
  const TABS = area === "maintenance" ? MAINTENANCE_TABS : RESEARCH_TABS;
  const tab: TabKey = TABS.some((t) => t.key === raw) ? (raw as TabKey) : area === "maintenance" ? "operations" : "review";
  // 复盘深链日期（助手跳转 / 分享）：只接受 YYYY-MM-DD，非法值当没传
  const rawDate = sp.get("date");
  const reviewDate = rawDate && /^(?:\d{4}-\d{2}-\d{2}|\d{8})$/.test(rawDate) ? rawDate : undefined;

  function switchTab(k: TabKey) {
    const p = new URLSearchParams(sp.toString());
    p.set("tab", k);
    p.set("area", area);
    router.replace(`/agent?${p.toString()}`, { scroll: false });
  }

  return (
    <main className="task-page mx-auto flex h-full w-full max-w-[1600px] flex-col px-4 py-3">
      <div className="mb-3 flex shrink-0 flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="flex items-baseline gap-2 text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            {area === "maintenance" ? "系统维护" : "复盘研究"}
          </h1>
          <nav className="flex items-center gap-1" aria-label={area === "maintenance" ? "系统维护视图" : "复盘研究视图"}>
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
        <span className="hidden text-xs text-zinc-600 dark:text-zinc-400 lg:inline">
          {area === "maintenance" ? "受控命令 · 后端鉴权与预算 · 作者提案不等于执行" : "冻结判断 · 失败与反证 · 不构成买卖建议"}
        </span>
      </div>

      <Link href={area === "maintenance" ? "/agent?area=research&tab=review" : "/agent?area=maintenance&tab=operations"} className="mb-2 text-xs text-zinc-600 dark:text-zinc-400">{area === "maintenance" ? "返回复盘研究" : "系统维护与受控处置"}</Link>
      {/* 高度链纪律：wrapper 必须 flex flex-col + bounded——块级会让子面板的
          overflow-y-auto 全部失效（内容超高不可滚，两次踩坑）。
          各 tab 根节点约定 h-full min-h-0 + 自管滚动。 */}
      <FadeSwap swapKey={tab} className="task-scroll min-h-0 flex-1 flex flex-col overflow-hidden">
        {tab === "evolution" && <EvolutionTab />}
        {tab === "tasks" && <TaskCenter />}
        {tab === "review" && <ReviewTab key={`${area}:${reviewDate ?? "latest"}`} focusDate={reviewDate} allowDispose={area === "maintenance"} />}
        {tab === "operations" && <ProductionOperations />}
        {tab === "leaders" && <div className="min-h-0 flex-1 overflow-auto"><LeaderResearchPanel /></div>}
        {tab === "alerts" && <AlertsTab />}
        {tab === "params" && <ParamsTab />}
        {tab === "repos" && <RepoTrackerTab />}
        {tab === "kb" && <KbBrowserTab />}
        {tab === "strategies" && <StrategyHealthTab />}
      </FadeSwap>
    </main>
  );
}

export default function AgentPage() {
  return (
    <Suspense fallback={<PageSkeletonFallback label="交易智能体加载中" />}>
      <AgentInner />
    </Suspense>
  );
}
