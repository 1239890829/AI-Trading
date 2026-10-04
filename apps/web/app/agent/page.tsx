"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { EvolutionTab } from "@/components/agent/evolution-tab";
import { KbBrowserTab } from "@/components/agent/kb-browser-tab";
import { RepoTrackerTab } from "@/components/agent/repo-tracker-tab";
import { ParamsTab } from "@/components/agent/params-tab";
import { StrategyHealthTab } from "@/components/agent/strategy-health-tab";
import { TaskCenter } from "@/components/agent/task-center";
import { AlertsTab } from "@/components/research/alerts-tab";
import { ProductionOperations } from "@/components/agent/production-operations";
import { agentArea, patchWorkspaceUrl } from "@/lib/task-navigation";
import { useExitPresence } from "@/hooks/use-exit-presence";
import Link from "next/link";
import { type MotionOrigin } from "@/lib/surface-motion";
import { WorkspaceDeck } from "@/components/ui/workspace-deck";
import { ModalShell } from "@/components/ui/modal-shell";
import { RESEARCH_TOOLS, MAINTENANCE_TOOLS } from "@/lib/workspace-tools";
import { LeaderResearchPanel } from "@/components/hunting/leader-research-panel";
import { ReviewTab } from "@/components/research/review-tab";
import { PageSkeletonFallback } from "@/components/ui/loading";

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

function AgentInner() {
  const router = useRouter();
  const [origin, setOrigin] = useState<MotionOrigin | null>(null);
  const sp = useSearchParams();
  const raw = sp.get("tab");
  const area = agentArea(sp.get("area"), raw);
  const maintenance = area === "maintenance";
  const tools = maintenance ? MAINTENANCE_TOOLS : RESEARCH_TOOLS;
  const presence = useExitPresence(tools.find(tool => tool.key === raw) ?? null);
  const selected = presence.value;
  const rawDate = sp.get("date");
  const reviewDate = rawDate && /^(?:\d{4}-\d{2}-\d{2}|\d{8})$/.test(rawDate) ? rawDate : undefined;
  function openTool(key: string, origin: MotionOrigin | null) {
    setOrigin(origin);
    router.push(patchWorkspaceUrl("/agent", sp.toString(), {area, tab: key}), {scroll: false});
  }
  function closeTool() {
    router.replace(patchWorkspaceUrl("/agent", sp.toString(), {area, tab: maintenance ? "operations" : "review"}), {scroll: false});
  }
  const toolShelf = <WorkspaceDeck key={area} activeTool={raw} tools={tools} onOpen={openTool} compact />;
  return <main data-workspace={maintenance ? "maintenance" : "research"} className="task-page adaptive-page mx-auto flex h-full w-full max-w-[1600px] flex-col gap-3 px-4 py-3">
    <div className="workspace-masthead">
      <div><h1>{maintenance ? "系统维护" : "复盘研究"}</h1><p className="workspace-kicker">{maintenance ? "任务、生产与运行记录" : "回看判断，找到下一次改进的依据"}</p></div>
      <div className="workspace-context">{maintenance ? <Link href="/agent?area=research&tab=review" className="quiet-action">返回复盘研究 ↗</Link> : <span>研究参考 · 不构成买卖建议</span>}</div>
    </div>
    <div className="agent-composition">
    <section className="workspace-stage min-h-0 flex-1 flex flex-col" aria-label={maintenance ? "运行状态" : "日度复盘主工作区"}>
      <div className={`min-h-0 flex-1 flex flex-col overflow-hidden ${maintenance ? "" : "p-3"}`}>{maintenance ? <ProductionOperations /> : <ReviewTab key={reviewDate ?? "latest"} focusDate={reviewDate} allowDispose={false} />}</div>
    </section>
    {toolShelf}
    </div>
    {selected && <ModalShell motionOrigin={origin} open={presence.active} label={selected.label} size={["params", "alerts", "repos"].includes(selected.key) ? "md" : "lg"} presentation="drawer" expandable onClose={closeTool} header={<div><p className="workspace-kicker">{selected.group}</p><h2 className="text-lg font-semibold">{selected.label}</h2><p className="text-xs text-zinc-600 dark:text-zinc-400">{selected.description}</p></div>} bodyClassName="overflow-hidden p-3" footer={maintenance ? "维护视图不授予权限；命令继续由后端鉴权、预算和批准链约束。" : "只读研究；样本不足与缺失产物不代表已经验证。"}>
      {presence.active && selected.key === "evolution" && <EvolutionTab />}
      {presence.active && selected.key === "tasks" && <TaskCenter />}
      {presence.active && selected.key === "review" && <ReviewTab key={`maintenance:${reviewDate ?? "latest"}`} focusDate={reviewDate} allowDispose />}
      {presence.active && selected.key === "leaders" && <div className="min-h-0 flex-1 overflow-auto"><LeaderResearchPanel /></div>}
      {presence.active && selected.key === "alerts" && <AlertsTab />}
      {presence.active && selected.key === "params" && <ParamsTab />}
      {presence.active && selected.key === "repos" && <RepoTrackerTab />}
      {presence.active && selected.key === "kb" && <KbBrowserTab />}
      {presence.active && selected.key === "strategies" && <StrategyHealthTab />}
    </ModalShell>}
  </main>;
}

export default function AgentPage() {
  return (
    <Suspense fallback={<PageSkeletonFallback label="交易智能体加载中" />}>
      <AgentInner />
    </Suspense>
  );
}
