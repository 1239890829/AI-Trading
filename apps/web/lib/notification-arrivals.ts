import type {NotificationItem, NotificationArrival as NotificationPhase} from "@/lib/api";
import {parseTs} from "@/lib/notification-read";

export type ArrivalKind = "selection" | "confirmation" | "research_ready" | "paper_status" | "risk" | "reminder";
export type ArrivalNotification = NotificationItem & {arrival?: NotificationPhase; arrivals?: NotificationPhase[]};
export interface NotificationArrival {
  key: string;
  version: string;
  kind: ArrivalKind;
  label: string;
  source: string;
  at: number;
  item: NotificationItem;
}
type ArrivalEntry = {key: string; at: number; state: "baseline" | "pending" | "claimed" | "displayed" | "deferred" | "dismissed"; owner?: string; until?: number; displayedAt?: number};
export interface ArrivalLedger {version: 1; initialized: boolean; baselineAt: number; entries: ArrivalEntry[]}
export const ARRIVAL_STORAGE_KEY = "ashare.notifications.arrivals.v1";
export const MAX_ARRIVAL_QUEUE = 12;
export const MAX_ARRIVAL_LEDGER = 512;
const LEASE_MS = 5_000;
const CHANGE_EVENT = "ashare-notification-arrivals";
const kinds = new Set<ArrivalKind>(["selection", "confirmation", "research_ready", "paper_status", "risk", "reminder"]);
const states = new Set<ArrivalEntry["state"]>(["baseline", "pending", "claimed", "displayed", "deferred", "dismissed"]);
const labels: Record<ArrivalKind, string> = {selection: "新入选", confirmation: "条件确认", research_ready: "研究完成", paper_status: "模拟状态", risk: "风险提醒", reminder: "条件提醒"};
const empty = (): ArrivalLedger => ({version: 1, initialized: false, baselineAt: 0, entries: []});

/** Stable phase identities contain no prose. A later phase may reuse its message ID. */
export function notificationArrivals(items: readonly ArrivalNotification[]): NotificationArrival[] {
  const result = new Map<string, NotificationArrival>();
  const add = (item: NotificationItem, identity: string[], kind: ArrivalKind, time: string | null | undefined, source: string, label = labels[kind]) => {
    const at = typeof time === "string" ? parseTs(time) : null;
    if (at === null || !item.id || !identity.every(value => typeof value === "string" && value.length > 0)) return;
    const key = JSON.stringify([item.id, ...identity]);
    if (key.length <= 768) result.set(key, {key, version:identity.at(-1)!, kind, label, source, at, item});
  };
  for (const item of items) {
    const phases = item.arrivals !== undefined ? item.arrivals : item.arrival !== undefined ? [item.arrival] : undefined;
    if (phases !== undefined) {
      if (!Array.isArray(phases)) continue;
      for (const phase of phases) {
        if (!phase || typeof phase.phase !== "string") continue;
        const kind = phase.phase === "selected" ? "selection" : phase.phase === "confirmed" ? "confirmation" : phase.phase;
        if (kinds.has(kind)) add(item, [phase.origin_id, phase.phase, phase.version], kind, phase.source_as_of, item.source || "站内原记录");
      }
      continue;
    }
    for (const ref of item.references ?? []) {
      const kind = ref.kind === "selection" ? "selection" : ref.kind === "buy_point" ? "confirmation" : null;
      if (kind) add(item, [kind, String(ref.event_id), ref.source_version || ref.source_id || "original"], kind, ref.source_as_of ?? ref.recorded_at ?? item.recorded_at ?? item.ts,
        kind === "selection" ? ref.selection_source === "daily" ? "每日精选" : "盘中选股" : "盘中条件判定");
    }
    if (!(item.references?.length) && ["opportunity", "daily_picks", "risk", "reminder"].includes(item.category)) {
      const kind = item.category === "risk" ? "risk" : item.category === "reminder" ? "reminder" : item.category === "daily_picks" || item.label.includes("入选") ? "selection" : "confirmation";
      add(item, [kind, "original"], kind, item.recorded_at ?? item.ts, item.source || "站内原记录");
    }
  }
  return [...result.values()].sort((a, b) => a.at - b.at || a.key.localeCompare(b.key));
}

/** Read only bounded identities and presentation clocks; never restore notification bodies. */
export function parseArrivalLedger(raw: string | null): ArrivalLedger {
  try {
    const value = JSON.parse(raw || "null") as ArrivalLedger | null;
    if (value?.version !== 1 || value.initialized !== true || !Number.isFinite(value.baselineAt) || !Array.isArray(value.entries)) return empty();
    const entries = value.entries.filter(entry => entry && typeof entry.key === "string" && entry.key.length <= 768 && Number.isFinite(entry.at) && states.has(entry.state))
      .slice(-MAX_ARRIVAL_LEDGER).map(entry => ({key: entry.key, at: entry.at, state: entry.state,
        ...(typeof entry.owner === "string" && entry.owner.length <= 80 ? {owner: entry.owner} : {}),
        ...(Number.isFinite(entry.until) ? {until: entry.until} : {}),
        ...(Number.isFinite(entry.displayedAt) ? {displayedAt: entry.displayedAt} : {})}));
    return {version: 1, initialized: true, baselineAt: value.baselineAt, entries};
  } catch {return empty();}
}

/** First successful read establishes history. Later arrivals keep their original phase clocks. */
export function reconcileArrivalLedger(ledger: ArrivalLedger, arrivals: readonly NotificationArrival[], now: number): ArrivalLedger {
  const entries = new Map(ledger.entries.map(entry => [entry.key, {...entry}]));
  const validClocks = arrivals.filter(arrival => arrival.at <= now + 60_000);
  const baselineAt = ledger.initialized ? ledger.baselineAt : validClocks.length ? Math.max(...validClocks.map(arrival => arrival.at)) : now;
  for (const arrival of arrivals) {
    if (arrival.at > now + 60_000) continue;
    if (!entries.has(arrival.key)) entries.set(arrival.key, {key: arrival.key, at: arrival.at,
      state: ledger.initialized && arrival.at >= baselineAt && arrival.at <= now + 60_000 ? "pending" : "baseline"});
  }
  for (const entry of entries.values()) if (entry.state === "claimed" && (entry.until ?? 0) <= now) {
    entry.state = "pending"; delete entry.owner; delete entry.until;
  }
  const pending = [...entries.values()].filter(entry => entry.state === "pending").sort((a, b) => b.at - a.at || a.key.localeCompare(b.key));
  for (const entry of pending.slice(MAX_ARRIVAL_QUEUE)) entry.state = "deferred";
  const bounded = [...entries.values()].sort((a, b) => {
    const active = (entry: ArrivalEntry) => entry.state === "pending" || entry.state === "claimed" ? 1 : 0;
    return active(a) - active(b) || a.at - b.at;
  }).slice(-MAX_ARRIVAL_LEDGER);
  return {version: 1, initialized: true, baselineAt, entries: bounded};
}

export function reserveArrival(ledger: ArrivalLedger, key: string, owner: string, now: number): ArrivalLedger {
  return {...ledger, entries: ledger.entries.map(entry => entry.key === key && entry.state === "pending"
    ? {...entry, state: "claimed", owner, until: now + LEASE_MS} : entry)};
}
export function confirmArrivalDisplay(ledger: ArrivalLedger, keys: readonly string[], owner: string, now: number): ArrivalLedger {
  return {...ledger, entries: ledger.entries.map(entry => entry.state === "claimed" && entry.owner === owner && keys.includes(entry.key)
    ? {key: entry.key, at: entry.at, state: "displayed", owner, displayedAt: now} : entry)};
}

let memory = empty();
let storageFailed = false;
function stored(): ArrivalLedger {
  if (storageFailed) return memory;
  try {return parseArrivalLedger(window.localStorage.getItem(ARRIVAL_STORAGE_KEY));} catch {storageFailed = true; return memory;}
}
/** Web Locks serializes claims across same-origin tabs; storage events also retire competing UI. */
export async function updateArrivalLedger(update: (current: ArrivalLedger) => ArrivalLedger): Promise<ArrivalLedger> {
  const run = () => {
    const previous = stored();
    const next = update(previous);
    memory = next;
    const raw = JSON.stringify(next);
    try {if (!storageFailed && window.localStorage.getItem(ARRIVAL_STORAGE_KEY) !== raw) window.localStorage.setItem(ARRIVAL_STORAGE_KEY, raw);} catch {storageFailed = true; /* Keep current-tab identities when persistence is unavailable. */}
    if (raw !== JSON.stringify(previous)) window.dispatchEvent(new Event(CHANGE_EVENT));
    return next;
  };
  return typeof navigator !== "undefined" && navigator.locks?.request
    ? navigator.locks.request(ARRIVAL_STORAGE_KEY, run) : run();
}
export function subscribeArrivalLedger(listener: () => void): () => void {
  const storage = (event: StorageEvent) => {if (event.key === ARRIVAL_STORAGE_KEY || event.key === null) listener();};
  window.addEventListener("storage", storage);
  window.addEventListener(CHANGE_EVENT, listener);
  return () => {window.removeEventListener("storage", storage); window.removeEventListener(CHANGE_EVENT, listener);};
}
