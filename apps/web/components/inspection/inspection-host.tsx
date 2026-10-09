"use client";

import dynamic from "next/dynamic";
import "../../app/market/market-bc.css";
import "../../app/hunting/hunting.css";
import "../../app/agent/agent-layout.css";
import { createContext, Suspense, useCallback, useContext, useMemo, useState } from "react";
import { ModalShell } from "@/components/ui/modal-shell";
import { PanelBoundary } from "@/components/ui/panel-boundary";
import { PageSkeletonFallback } from "@/components/ui/loading";
import { useExitPresence } from "@/hooks/use-exit-presence";
import { InspectionContext, InspectionStateContext, type InspectionRequest } from "./inspection-context";
import { InspectionScope } from "./surface-scope";

const loading = () => <PageSkeletonFallback label="关联内容加载中" />;
const LimitUp = dynamic(() => import("@/components/tape/limit-up-tab").then(module => module.LimitUpTab), { loading });
const LimitDown = dynamic(() => import("@/components/tape/limit-down-tab").then(module => module.LimitDownTab), { loading });
const Themes = dynamic(() => import("@/components/tape/themes-tab").then(module => module.ThemesTab), { loading });
const Longhu = dynamic(() => import("@/components/tape/longhu-tab").then(module => module.LonghuTab), { loading });
const Heatmap = dynamic(() => import("@/components/market/heatmap-tab").then(module => module.HeatmapTab), { loading });
const Fund = dynamic(() => import("@/components/market/fund-tab").then(module => module.FundTab), { loading });
const Events = dynamic(() => import("@/components/market/events-tab").then(module => module.EventsTab), { loading });
const SelectionReview = dynamic(() => import("./selection-panels").then(module => module.SelectionReviewPanel), { loading });
const SelectionPreview = dynamic(() => import("./selection-panels").then(module => module.SelectionPreviewPanel), { loading });
const ReviewReport = dynamic(() => import("./selection-panels").then(module => module.ReviewReportPanel), { loading });
const Account = dynamic(() => import("./account-status-panels").then(module => module.AccountInspectionPanel), { loading });
const SystemStatus = dynamic(() => import("./account-status-panels").then(module => module.SystemStatusPanel), { loading });

const LABELS: Record<InspectionRequest["kind"], string> = {
  "limit-up": "涨停池", "limit-down": "跌停池", themes: "题材梯队", longhu: "龙虎榜", heatmap: "板块分布", fund: "市场资金", events: "事件线索",
  "selection-review": "原版本精选复盘", "selection-preview": "选股旁览", "review-report": "复盘报告", account: "账户记录", "system-status": "生产状态",
};
const InspectionActivationContext = createContext(0);

/** State is separate from the render host so linked readers can reach every existing detail provider. */
export function InspectionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<{request: InspectionRequest | null; activation: number}>({request: null, activation: 0});
  const request = state.request;
  const open = useCallback((next: InspectionRequest) => setState(previous => ({request: next, activation: previous.activation + 1})), []);
  const close = useCallback(() => setState(previous => ({...previous, request: null})), []);
  const value = useMemo(() => ({ open, close }), [open, close]);
  return <InspectionContext.Provider value={value}><InspectionActivationContext.Provider value={state.activation}><InspectionStateContext.Provider value={request}>{children}</InspectionStateContext.Provider></InspectionActivationContext.Provider></InspectionContext.Provider>;
}

export function inspectionSearch(request: InspectionRequest) {
  const params = new URLSearchParams();
  if ("date" in request && request.date) params.set("date", request.date);
  if (request.kind === "limit-up") {
    if (request.theme) params.set("theme", request.theme);
    if (request.symbols?.length) params.set("symbols", request.symbols.join(","));
  }
  if (request.kind === "themes") {
    if (request.focus) params.set("focus", request.focus);
    if (request.brokenOnly) params.set("broken", "1");
  }
  if (request.kind === "events") {
    if (request.target) params.set("target", request.target);
    if (request.filter?.four) params.set("four", request.filter.four);
    if (request.filter?.tag) params.set("tag", request.filter.tag);
    if (request.filter?.sort) params.set("sort", request.filter.sort);
    if (request.filter?.l1Only) params.set("l1", "1");
  }
  return params.toString();
}

function InspectionReader({ request }: { request: InspectionRequest }) {
  switch (request.kind) {
    case "limit-up": return <LimitUp />;
    case "limit-down": return <LimitDown />;
    case "themes": return <Themes />;
    case "longhu": return <Longhu />;
    case "heatmap": return <Heatmap />;
    case "fund": return <Fund />;
    case "events": return <Events />;
    case "selection-review": return <SelectionReview request={request} />;
    case "selection-preview": return <SelectionPreview request={request} />;
    case "review-report": return <ReviewReport request={request} />;
    case "account": return <Account request={request} />;
    case "system-status": return <SystemStatus />;
  }
}

/** Closed surfaces retain only the shell for exit motion; readers and their polling unmount immediately. */
export function InspectionHost() {
  const request = useContext(InspectionStateContext);
  const activation = useContext(InspectionActivationContext);
  const { close } = useContext(InspectionContext);
  const presence = useExitPresence(request);
  if (!presence.value) return null;
  const shown = presence.value;
  const label = LABELS[shown.kind];
  const key = JSON.stringify({ ...shown, motionOrigin: undefined });
  const huntingReader = shown.kind === "selection-preview" && ["evidence", "tracking", "research"].includes(shown.view ?? "");
  const huntingFrame = shown.kind === "selection-preview" || shown.kind === "selection-review";
  const leafScroll = shown.kind === "selection-review" || (shown.kind === "selection-preview" && !huntingReader) || shown.kind === "account";
  const tapeReader = ["themes", "longhu", "limit-up", "limit-down"].includes(shown.kind);
  const marketReader = tapeReader || ["fund", "events", "heatmap"].includes(shown.kind);
  const frameClass = marketReader ? `bc-market-page ${tapeReader ? "bc-tape-page" : ""}` : huntingFrame ? "task-page hunting-workspace" : "";
  const surfaceClass = huntingFrame ? "hunting-content flex flex-col" : tapeReader ? `bc-market-view bc-tape-view bc-tape-view-${shown.kind.replace("-", "")}` : marketReader ? `bc-market-view bc-market-view-${shown.kind} flex flex-col` : shown.kind === "system-status" ? "agent-page flex flex-col" : "flex flex-col";
  return <ModalShell key={activation} open={presence.active} onClose={close} label={label} testid="inspection-modal" size="lg" presentation="drawer" expandable motionOrigin={shown.motionOrigin}
    header={<div><h2 className="text-base font-semibold">{label}</h2><p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">关联旁览 · 保留当前工作位置</p></div>}
    bodyClassName="overflow-hidden p-3" footer="读取与操作沿用原模块的权限、来源与范围；研究记录不构成买卖建议。">
    {presence.active && <PanelBoundary key={key} label={label}><InspectionScope initialSearch={inspectionSearch(shown)}><Suspense fallback={loading()}><div style={huntingFrame ? {padding: 0, gap: 0} : undefined} className={`flex min-h-0 flex-1 flex-col overflow-hidden ${frameClass}`}><div style={{overflow: leafScroll ? "auto" : "hidden", ...(huntingFrame ? {padding: 0, gap: 0} : {})}} className={`min-h-0 flex-1 ${leafScroll ? "overflow-auto overscroll-contain" : "overflow-hidden"} ${surfaceClass}`}><InspectionReader request={shown} /></div></div></Suspense></InspectionScope></PanelBoundary>}
  </ModalShell>;
}
