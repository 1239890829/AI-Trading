"use client";

import { createContext, useContext } from "react";
import { motionOrigin, type MotionOrigin } from "@/lib/surface-motion";

type Origin = { motionOrigin?: MotionOrigin | null };
export type InspectionRequest = Origin & (
  | { kind: "limit-up"; date?: string; theme?: string; symbols?: string[] }
  | { kind: "limit-down"; date?: string }
  | { kind: "themes"; date?: string; focus?: string; brokenOnly?: boolean }
  | { kind: "longhu"; date?: string }
  | { kind: "heatmap" }
  | { kind: "fund" }
  | { kind: "events"; target?: string; filter?: { four?: string; tag?: string; sort?: "relevance" | "time" | "impact"; l1Only?: boolean } }
  | { kind: "selection-review"; date: string; version: string }
  | { kind: "selection-preview"; view?: "discover" | "evidence" | "tracking" | "research" | "review"; section?: string; theme?: string; date?: string }
  | { kind: "review-report"; date?: string }
  | { kind: "account"; scope: "daily" | "hunting" | "manual" | "main" }
  | { kind: "system-status" }
);

export type InspectionOf<K extends InspectionRequest["kind"]> = Extract<InspectionRequest, { kind: K }>;
type InspectionContext = { open: (request: InspectionRequest) => void; close: () => void };
export const InspectionContext = createContext<InspectionContext>({ open: () => {}, close: () => {} });
export const InspectionStateContext = createContext<InspectionRequest | null>(null);

export function useInspection() { return useContext(InspectionContext); }

/** Preserve copy-link and modified-click navigation while ordinary inspection stays local. */
export function inspectionClick(open: InspectionContext["open"], request: InspectionRequest) {
  return (event: React.MouseEvent<HTMLElement>) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    event.stopPropagation();
    open({ ...request, motionOrigin: motionOrigin(event) });
  };
}

function validDate(value: string | null) {
  if (!value || !/^(?:\d{4}-\d{2}-\d{2}|\d{8})$/.test(value)) return undefined;
  const compact = value.replaceAll("-", "");
  const year = Number(compact.slice(0, 4));
  const month = Number(compact.slice(4, 6));
  const day = Number(compact.slice(6, 8));
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1] ? value : undefined;
}

/** Only existing read-only contextual targets are eligible; unknown and primary navigation stay links. */
export function inspectionRequestForHref(href: string): InspectionRequest | null {
  if (!href.startsWith("/") || href.startsWith("//") || /[\\\u0000-\u001f\u007f]/.test(href)) return null;
  let url: URL;
  try { url = new URL(href, "https://inspection.invalid"); } catch { return null; }
  // Browsers normalize a leading slash/backslash into a protocol-relative host.
  if (url.origin !== "https://inspection.invalid") return null;
  const params = url.searchParams;
  const date = validDate(params.get("date"));
  if (params.has("date") && !date) return null;
  if (url.pathname === "/tape") {
    const tab = params.get("tab");
    if (tab === "limitup") {
      const symbols = params.has("symbols") ? params.get("symbols")!.split(",") : [];
      if (symbols.some(symbol => !/^\d{6}$/.test(symbol))) return null;
      return { kind: "limit-up", date, theme: params.get("theme") || undefined, symbols };
    }
    if (tab === "limitdown") return { kind: "limit-down", date };
    if (tab === "themes") {
      // Keep unsupported reader filters on their original route, never widen them.
      if (["sort", "min_boards", "min_count"].some(key => params.has(key))) return null;
      const broken = params.get("broken");
      if (broken !== null && broken !== "0" && broken !== "1") return null;
      return { kind: "themes", date, focus: params.get("focus") || undefined, ...(broken === "1" ? {brokenOnly: true} : {}) };
    }
    if (tab === "longhu") return { kind: "longhu", date };
    return null;
  }
  if (url.pathname === "/market") {
    // These readers expose current scope only; an explicit historical intent
    // must remain a real link rather than silently opening current data.
    if (date) return null;
    if (params.get("tab") === "heatmap" && !date) return { kind: "heatmap" };
    if (params.get("tab") === "fund") return { kind: "fund" };
    if (params.get("tab") === "events") {
      const four = params.get("four") ?? undefined;
      const tag = params.get("tag") ?? undefined;
      const sort = params.get("sort") ?? undefined;
      const l1 = params.get("l1");
      if (four && !["all", "material", "policy", "international", "hot"].includes(four)) return null;
      if (tag && !["all", "业绩", "公告", "异动", "资金", "行业"].includes(tag)) return null;
      if (sort && !["relevance", "time", "impact"].includes(sort)) return null;
      if (l1 !== null && !["0", "1"].includes(l1)) return null;
      return { kind: "events", target: params.get("target") || undefined,
        ...(four || tag || sort || l1 !== null ? {filter: {four, tag, sort: sort as "relevance" | "time" | "impact" | undefined, l1Only: l1 === "1"}} : {}) };
    }
    return null;
  }
  if (url.pathname === "/hunting") {
    const version = params.get("version");
    const view = params.get("view");
    if (version || (date && (view === "review" || params.get("review") === "1"))) {
      if (!date || !version || !/^[a-f0-9]{64}$/i.test(version) || params.get("view") !== "review") return null;
      return { kind: "selection-review", date, version };
    }
    const known = ["discover", "evidence", "tracking", "research", "review"] as const;
    if (view && !known.includes(view as typeof known[number])) return null;
    const section = params.get("sec") || (params.get("tag") === "pick" ? "daily" : undefined);
    if (section && !["brief", "reminders", "watcher", "opportunity", "overview", "postmarket", "daily", "candidates"].includes(section)) return null;
    return { kind: "selection-preview", view: known.includes(view as typeof known[number]) ? view as typeof known[number] : params.get("review") === "1" ? "review" : "discover", section, theme: params.get("theme") || undefined, date };
  }
  if (url.pathname === "/agent") {
    const tab = params.get("tab");
    if (tab === "operations") return { kind: "system-status" };
    if (tab === "review") return { kind: "review-report", date };
    return null;
  }
  if (url.pathname === "/workbench" && params.get("mode") === "positions" && !params.has("symbol")) {
    const account = params.get("account");
    if (account && !["daily", "hunting", "manual", "paper", "main"].includes(account)) return null;
    const scope = account === "daily" || account === "hunting" || account === "manual" ? account : account ? "main" : "manual";
    return { kind: "account", scope };
  }
  return null;
}
