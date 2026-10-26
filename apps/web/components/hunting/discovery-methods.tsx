"use client";

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { useResource } from "@/hooks/use-resource";
import { getStrategyRegistry, isReferenceResearchMethod, type StrategyRegistryItem } from "@/lib/api/picks";
import { methodRunDay, methodRunUrl, readMethodRunLocation } from "@/components/agent/method-run-location";

interface MethodDiscovery {
  category: string;
  category_label: string;
  session: string;
  session_label: string;
  summary: string;
  supports_saved_runs: boolean;
}

const LIFECYCLES = [
  ["all", "全部"], ["active", "现役"], ["research_ready", "研究可运行"], ["candidate", "来源候选"], ["out_of_scope", "范围未开放"], ["observing", "观察"], ["rejected", "已否决"],
] as const;

export function discoveryMetadata(row: StrategyRegistryItem): MethodDiscovery {
  const raw = row.discovery;
  const data = raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : {};
  const text = (key: string, fallback: string) => {
    const value = data[key];
    return typeof value === "string" && value.trim() ? value.trim() : fallback;
  };
  return {
    category: text("category", "unknown"), category_label: text("category_label", "未分类"),
    session: text("session", "unknown"), session_label: text("session_label", "时段未登记"),
    summary: text("summary", "用途未登记，查看原方法记录。"),
    supports_saved_runs: data.supports_saved_runs === true,
  };
}

/** Keep a chosen original run only when its method and date still match. */
export function discoveryMethodHref(method: string, search: string, date?: string): string {
  const params = new URLSearchParams(search);
  const returnSearch = params.toString();
  params.set("from", `/hunting${returnSearch ? `?${returnSearch}` : ""}`);
  params.set("area", "research");
  params.set("tab", "strategies");
  for (const key of ["view", "sec", "panel", "discover", "browse", "disc_kind", "disc_range", "disc_code", "disc_q", "disc_sort", "disc_method", "selection"]) params.delete(key);
  const location = readMethodRunLocation(search, method);
  const clearIdentity = () => {
    location.runId = "";
    location.contentVersion = "";
    location.sourceVersion = "";
    location.missingSource = false;
    location.strategyVersion = "";
    location.row = "";
  };
  if (params.get("method") !== method) {
    clearIdentity();
    location.filter = isReferenceResearchMethod(method) ? "all" : "selected";
  }
  if (date !== undefined && date !== location.date && (!methodRunDay(date) || methodRunDay(date) !== methodRunDay(location.date))) {
    location.date = date;
    clearIdentity();
  }
  return methodRunUrl("/agent", params.toString(), method, location);
}

export function SavedRunSummary({ row, date }: {row: StrategyRegistryItem; date?: string}) {
  const run = row.runtime;
  if (!run && methodSupportsManualResearch(row)) return <span>保存研究结果按需读取</span>;
  if (!run || run.state === "missing") return <span>尚无保存运行</span>;
  if (run.state === "error") return <span>原运行读取异常</span>;
  if (run.state !== "saved" && run.state !== "partial") return <span>原运行状态未核</span>;
  const day = methodRunDay(run.trade_date);
  const selected = run.counts?.selected;
  const knownCount = typeof selected === "number" && Number.isSafeInteger(selected) && selected >= 0;
  const requestedDay = methodRunDay(date);
  const sameDay = !date || !!requestedDay && requestedDay === day;
  return <>
    <span>{sameDay ? "最近保存" : "最近保存（非所选日期）"} {day || "日期未记录"}</span>
    <span>{run.state === "partial" ? "部分记录" : "已保存"}{knownCount ? ` · 入选 ${selected} 只` : " · 入选数未记录"}</span>
    {run.strategy_version && <span title={`原方法版本：${run.strategy_version}`}>方法版本 {run.strategy_version}</span>}
  </>;
}

export function discoveryMethodScope(date: string | undefined, key: string) {return JSON.stringify(["methods", date ?? null, key]);}

export function methodSupportsManualResearch(row: StrategyRegistryItem): boolean {
  const execution = row.candidate?.execution;
  return row.catalog_kind === "reference" && row.status === "research_ready" && isReferenceResearchMethod(row.key) && row.candidate?.asset === "stock"
    && row.candidate.runnable === true && execution?.mode === "manual_research" && execution.production_enabled === false
    && typeof execution.version === "string" && !!execution.version.trim()
    && typeof execution.definition === "string" && !!execution.definition.trim()
    && typeof execution.definition_supplement === "string" && !!execution.definition_supplement.trim()
    && typeof execution.definition_digest === "string" && /^[0-9a-f]{64}$/.test(execution.definition_digest);
}

export function validMethodRow(row: StrategyRegistryItem): boolean {
  if (!row || typeof row.key !== "string" || !/^[a-zA-Z0-9_:-]{1,128}$/.test(row.key)) return false;
  if (row.name !== undefined && (typeof row.name !== "string" || !row.name.trim())) return false;
  if (row.status !== undefined && typeof row.status !== "string") return false;
  if (row.catalog_kind !== "reference") return !row.key.startsWith("reference:") && (row.catalog_kind === undefined || row.catalog_kind === "registered");
  const value = row.candidate;
  return !!value && (value.runnable === false || methodSupportsManualResearch(row)) && ["stock", "etf"].includes(value.asset) && row.key === `reference:${value.source_code}`
    && row.status === (value.asset === "etf" ? "out_of_scope" : value.runnable === true ? "research_ready" : "candidate")
    && [value.source_label, value.source_code, value.source_version].every(item => typeof item === "string" && !!item.trim())
    && [value.criteria, value.existing_coverage, value.limitations].every(items => Array.isArray(items) && items.every(item => typeof item === "string" && !!item.trim()));
}

export function methodSupportsRuns(row: StrategyRegistryItem) {
  return discoveryMetadata(row).supports_saved_runs && (methodSupportsManualResearch(row)
    || row.catalog_kind !== "reference" && ["daily_picks", "intraday_watch"].includes(row.key));
}
export function methodStatusLabel(row: StrategyRegistryItem) {return LIFECYCLES.find(([key]) => key !== "all" && key === row.status)?.[1] || "状态未登记";}

const FAVORITES_KEY = "ashare.discovery.methods.favorites.v1";
const FAVORITES_EVENT = "ashare:discovery-method-favorites";
type FavoriteSnapshot = {keys: readonly string[]; saved: boolean};
const EMPTY_FAVORITES: FavoriteSnapshot = {keys: [], saved: true};
let favoriteSnapshot = EMPTY_FAVORITES;
let favoriteRaw: string | null | undefined;
let memoryOnly = false;
function boundedFavorites(value: unknown): string[] {
  return Array.isArray(value) ? [...new Set(value.filter((key): key is string => typeof key === "string" && /^[a-zA-Z0-9_:-]{1,128}$/.test(key)))].slice(0, 40) : [];
}
function readFavorites() {
  if (memoryOnly) return favoriteSnapshot;
  try {
    const raw = window.localStorage.getItem(FAVORITES_KEY);
    if (raw !== favoriteRaw) {
      favoriteRaw = raw;
      let parsed: unknown = [];
      try {parsed = raw ? JSON.parse(raw) : [];} catch { /* Ignore malformed local preferences. */ }
      favoriteSnapshot = {keys: boundedFavorites(parsed), saved: true};
    }
  } catch {memoryOnly = true; favoriteSnapshot = {...favoriteSnapshot, saved: false};}
  return favoriteSnapshot;
}
function subscribeFavorites(listener: () => void) {
  const storage = (event: StorageEvent) => {if (event.key === null || event.key === FAVORITES_KEY) {memoryOnly = false; favoriteRaw = undefined; listener();}};
  window.addEventListener("storage", storage);
  window.addEventListener(FAVORITES_EVENT, listener);
  return () => {window.removeEventListener("storage", storage); window.removeEventListener(FAVORITES_EVENT, listener);};
}
function saveFavorites(keys: readonly string[]) {
  const bounded = boundedFavorites(keys);
  const raw = JSON.stringify(bounded);
  try {window.localStorage.setItem(FAVORITES_KEY, raw); favoriteRaw = raw; memoryOnly = false; favoriteSnapshot = {keys: bounded, saved: true};}
  catch {memoryOnly = true; favoriteSnapshot = {keys: bounded, saved: false};}
  window.dispatchEvent(new Event(FAVORITES_EVENT));
}
/** A bounded local preference, shared by the list and its open detail; never a strategy status. */
export function useMethodFavorites() {
  const snapshot = useSyncExternalStore(subscribeFavorites, readFavorites, () => EMPTY_FAVORITES);
  const toggle = (key: string) => {
    const current = readFavorites().keys;
    if (!current.includes(key) && current.length >= 40) return false;
    saveFavorites(current.includes(key) ? current.filter(item => item !== key) : [...current, key]);
    return true;
  };
  return {...snapshot, toggle, clear: () => saveFavorites([])};
}

export function MethodFavoriteButton({name, followed, onToggle}: {name: string; followed: boolean; onToggle: () => void}) {
  return <button type="button" className="quiet-action inline-flex min-h-8 shrink-0 items-center gap-1" aria-pressed={followed}
    aria-label={`${followed ? "取消关注" : "关注"}${name}`} onClick={onToggle} title="仅保存本机关注，不启用方法">
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill={followed ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.5"><path d="m12 3 2.8 5.7 6.3.9-4.6 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L2.9 9.6l6.3-.9Z"/></svg>
    {followed ? "已关注" : "关注"}
  </button>;
}

/** One catalog read. Selection and local favorites never run a method or model. */
export function DiscoveryMethods({active = true, date, search = "", onNavigate, onMethodTarget}: {
  active?: boolean; date?: string; search?: string;
  onNavigate?: (href: string, push?: boolean) => void;
  onMethodTarget?: (scope: string, row: StrategyRegistryItem | null) => void;
}) {
  const resource = useResource(signal => getStrategyRegistry({signal, includeCandidates: true}), {enabled: active, marketHours: false, abortable: true});
  const [lifecycle, setLifecycle] = useState("all");
  const [category, setCategory] = useState("all");
  const [session, setSession] = useState("all");
  const [asset, setAsset] = useState("all");
  const [followedOnly, setFollowedOnly] = useState(false);
  const [notice, setNotice] = useState("");
  const favorites = useMethodFavorites();
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [composing, setComposing] = useState(false);
  const malformed = resource.data !== undefined && !Array.isArray(resource.data);
  const originals = resource.data;
  const rows = useMemo(() => {
    const values = Array.isArray(originals) ? originals : [];
    const counts = new Map<string, number>();
    for (const row of values) if (row && typeof row.key === "string") counts.set(row.key, (counts.get(row.key) ?? 0) + 1);
    return values.filter(row => validMethodRow(row) && counts.get(row.key) === 1).map(row => ({row, metadata: discoveryMetadata(row)}));
  }, [originals]);
  const selectedKey = new URLSearchParams(search).get("disc_method") ?? "";
  const chosen = rows.find(({row}) => row.key === selectedKey)?.row ?? null;
  const target = active && !resource.error && !malformed ? chosen : null;
  const scope = discoveryMethodScope(date, selectedKey);
  useEffect(() => {onMethodTarget?.(scope, target);}, [onMethodTarget, scope, target]);
  if (!active) return null;
  const invalidRows = Array.isArray(originals) && rows.length !== originals.length;
  const categories = new Map(rows.map(({metadata}) => [metadata.category, metadata.category_label]));
  const sessions = new Map(rows.map(({metadata}) => [metadata.session, metadata.session_label]));
  const searchText = query.trim().toLocaleLowerCase();
  const visible = rows.filter(({row, metadata}) => (lifecycle === "all" || row.status === lifecycle)
    && (category === "all" || metadata.category === category)
    && (session === "all" || metadata.session === session)
    && (asset === "all" || (row.catalog_kind === "reference" ? row.candidate?.asset : "stock") === asset)
    && (!followedOnly || favorites.keys.includes(row.key))
    && `${row.name || row.key} ${row.key} ${metadata.category_label} ${metadata.session_label} ${metadata.summary}`.toLocaleLowerCase().includes(searchText));
  const clear = () => {setLifecycle("all"); setCategory("all"); setSession("all"); setAsset("all"); setFollowedOnly(false); setDraft(""); setQuery("");};
  const chooseHref = (key: string) => {const params = new URLSearchParams(search); params.set("view", "discover"); params.set("browse", "methods"); params.set("disc_method", key); params.delete("disc_code"); return `/hunting?${params}`;};
  const toggleFavorite = (key: string) => setNotice(favorites.toggle(key) ? "" : "本机最多关注40个方法，请先取消一项。");

  return <section className="discovery-methods min-w-0 space-y-3" aria-label="战法分类">
    <header className="flex flex-wrap items-center justify-between gap-2">
      <div><h3 className="text-sm font-medium">方法目录</h3><p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">选一项看规则与覆盖；来源候选不运行。</p></div>
      <button type="button" className="quiet-action" disabled={resource.pending} onClick={resource.refresh}>
        {resource.pending ? "读取中…" : "刷新方法"}
      </button>
    </header>

    <div className="discovery-method-filters flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-1 text-xs">状态<select aria-label="方法状态" value={lifecycle} onChange={event => setLifecycle(event.target.value)} className="min-w-0 rounded border border-zinc-300 bg-transparent px-1.5 py-1 dark:border-zinc-600">
        {LIFECYCLES.map(([key, label]) => <option key={key} value={key}>{label}{resource.data !== undefined && !malformed ? ` ${key === "all" ? rows.length : rows.filter(({row}) => row.status === key).length}` : ""}</option>)}
      </select></label>
      <label className="flex items-center gap-1 text-xs">资产<select aria-label="方法资产" value={asset} onChange={event => setAsset(event.target.value)} className="min-w-0 rounded border border-zinc-300 bg-transparent px-1.5 py-1 dark:border-zinc-600">
        <option value="all">全部</option><option value="stock">A股</option><option value="etf">ETF参考</option></select></label>
      <button type="button" className="quiet-action" aria-pressed={followedOnly} onClick={() => setFollowedOnly(value => !value)}>我关注 {favorites.keys.length}</button>
      <label className="flex items-center gap-1 text-xs text-zinc-600 dark:text-zinc-400">类别
        <select aria-label="方法类别" value={category} onChange={event => setCategory(event.target.value)}
          className="min-w-0 rounded border border-zinc-300 bg-transparent px-1.5 py-1 text-zinc-900 dark:border-zinc-600 dark:text-zinc-100">
          <option value="all">全部类别</option>{[...categories].map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
      </label>
      <label className="flex items-center gap-1 text-xs text-zinc-600 dark:text-zinc-400">时段
        <select aria-label="方法时段" value={session} onChange={event => setSession(event.target.value)}
          className="min-w-0 rounded border border-zinc-300 bg-transparent px-1.5 py-1 text-zinc-900 dark:border-zinc-600 dark:text-zinc-100">
          <option value="all">全部时段</option>{[...sessions].map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
      </label>
      <label className="min-w-32 flex-1"><span className="sr-only">搜索战法</span>
        <input aria-label="搜索战法" value={draft} placeholder="名称或用途" className="w-full rounded border border-zinc-300 bg-transparent px-2 py-1 text-xs dark:border-zinc-600"
          onCompositionStart={() => setComposing(true)} onCompositionEnd={event => {setComposing(false); setQuery(event.currentTarget.value);}}
          onChange={event => {setDraft(event.target.value); if (!composing) setQuery(event.target.value);}}/>
      </label>
    </div>

    {!favorites.saved && <p role="status" className="text-xs text-amber-800 dark:text-amber-300">本机存储不可用，关注仅保留在当前页面，尚未保存。</p>}
    {notice && favorites.keys.length >= 40 && <p role="status" className="text-xs text-amber-800 dark:text-amber-300">{notice}</p>}
    {selectedKey && !resource.pending && !resource.error && !chosen && <p role="status" className="text-xs text-zinc-600 dark:text-zinc-400">链接指定的方法不在本次目录，未换成其他方法。</p>}

    {(resource.error != null || malformed || invalidRows) && <p role="alert" className="text-xs text-amber-800 dark:text-amber-300">
      {malformed || invalidRows ? "方法返回结构不完整，当前分类未核实。" : "方法读取失败。"}
      {rows.length > 0 && (malformed || invalidRows ? " 以下仅列可读取登记。" : " 保留上次成功读取的登记，当前状态待重试。")}
    </p>}
    {resource.data === undefined && resource.pending && <div role="status" aria-label="正在读取方法" className="space-y-2" aria-busy="true">
      {[0, 1].map(key => <div key={key} className="h-14 animate-pulse rounded bg-zinc-100 dark:bg-zinc-900" aria-hidden="true"/>)}
    </div>}
    {resource.data !== undefined && rows.length === 0 && !malformed && !resource.error && <p className="text-xs text-zinc-600 dark:text-zinc-400">暂无已登记方法。</p>}
    {rows.length > 0 && visible.length === 0 && <div className="flex flex-wrap items-center gap-2 py-4 text-xs text-zinc-600 dark:text-zinc-400">
      <span>{followedOnly ? "当前关注没有匹配方法。" : "没有匹配方法。"}</span><button type="button" className="quiet-action" onClick={clear}>清除筛选</button>
      {followedOnly && favorites.keys.length > 0 && <button type="button" className="quiet-action" onClick={() => {favorites.clear(); setNotice("");}}>清空本机关注</button>}
    </div>}

    <ul className="discovery-method-list divide-y divide-zinc-200 dark:divide-zinc-800" aria-label="已登记方法">
      {visible.map(({row, metadata}) => {
        const supportsRuns = methodSupportsRuns(row);
        const statusLabel = methodStatusLabel(row);
        const verificationLabel = row.verification?.available === true ? "有核验产物"
          : row.verification?.available === false ? "尚无核验产物" : "核验状态未记录";
        return <li key={row.key} data-method-key={row.key} data-selected={selectedKey === row.key} className="discovery-method-row py-3 first:pt-0 last:pb-0">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <div className="flex min-w-0 flex-wrap items-baseline gap-2">
              <Link href={chooseHref(row.key)} data-discovery-method={row.key} scroll={false} aria-current={selectedKey === row.key ? "true" : undefined} aria-label={`查看方法规则：${row.name || row.key}`} className="quiet-action text-sm font-medium"
                onClick={event => {if(onNavigate && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {event.preventDefault(); onNavigate(chooseHref(row.key), true);}}}>{row.name || row.key}</Link>
              <span className="text-xs text-zinc-600 dark:text-zinc-400">{metadata.category_label} · {metadata.session_label}</span>
            </div>
            <MethodFavoriteButton name={row.name || row.key} followed={favorites.keys.includes(row.key)} onToggle={() => toggleFavorite(row.key)}/>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">{metadata.summary}</p>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-zinc-600 dark:text-zinc-400">
            <span>{statusLabel}{row.catalog_kind === "reference" ? ` · ${row.candidate?.asset === "etf" ? "ETF参考" : methodSupportsManualResearch(row) ? "仅手动研究" : "待评价，不可运行"}` : ""}</span>
            {supportsRuns ? <SavedRunSummary row={row} date={date}/> : row.catalog_kind !== "reference" && <span>{verificationLabel}{row.verification?.stale ? " · 需复核" : ""}</span>}
            {row.catalog_kind !== "reference" && <Link className="quiet-action ml-auto" href={discoveryMethodHref(row.key, search, date)}>
              {supportsRuns ? "查看原运行与版本" : "查看核验与反例"}<span className="sr-only">：{row.name || row.key}</span>
            </Link>}
          </div>
        </li>;
      })}
    </ul>
    <p className="text-[11px] text-zinc-600 dark:text-zinc-400">关注仅本机偏好；来源候选、手动研究、观察及否决方法不参与当前选股。</p>
  </section>;
}
