"use client";

import { useState } from "react";
import Link from "next/link";
import { useResource } from "@/hooks/use-resource";
import { getStrategyRegistry, type StrategyRegistryItem } from "@/lib/api/picks";
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
  ["active", "现役"], ["observing", "观察"], ["rejected", "已否决"], ["all", "全部"],
] as const;

function discoveryMetadata(row: StrategyRegistryItem): MethodDiscovery {
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
  for (const key of ["view", "sec", "panel", "discover", "browse", "disc_kind", "disc_range", "disc_code", "disc_q", "disc_sort", "selection"]) params.delete(key);
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
    location.filter = "selected";
  }
  if (date !== undefined && date !== location.date && (!methodRunDay(date) || methodRunDay(date) !== methodRunDay(location.date))) {
    location.date = date;
    clearIdentity();
  }
  return methodRunUrl("/agent", params.toString(), method, location);
}

function SavedRunSummary({ row, date }: {row: StrategyRegistryItem; date?: string}) {
  const run = row.runtime;
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

/** One read of the registered methods; this surface never runs a strategy or model. */
export function DiscoveryMethods({active = true, date, search = ""}: {active?: boolean; date?: string; search?: string}) {
  const resource = useResource(signal => getStrategyRegistry({signal}), {enabled: active, marketHours: false, abortable: true});
  const [lifecycle, setLifecycle] = useState("active");
  const [category, setCategory] = useState("all");
  const [session, setSession] = useState("all");
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [composing, setComposing] = useState(false);
  if (!active) return null;

  const malformed = resource.data !== undefined && !Array.isArray(resource.data);
  const originals = Array.isArray(resource.data) ? resource.data : [];
  const rows = originals.filter(row => row && typeof row.key === "string" && row.key.trim())
    .map(row => ({row, metadata: discoveryMetadata(row)}));
  const invalidRows = rows.length !== originals.length;
  const categories = new Map(rows.map(({metadata}) => [metadata.category, metadata.category_label]));
  const sessions = new Map(rows.map(({metadata}) => [metadata.session, metadata.session_label]));
  const searchText = query.trim().toLocaleLowerCase();
  const visible = rows.filter(({row, metadata}) => (lifecycle === "all" || row.status === lifecycle)
    && (category === "all" || metadata.category === category)
    && (session === "all" || metadata.session === session)
    && `${row.name || row.key} ${row.key} ${metadata.category_label} ${metadata.session_label} ${metadata.summary}`.toLocaleLowerCase().includes(searchText));
  const clear = () => {setLifecycle("all"); setCategory("all"); setSession("all"); setDraft(""); setQuery("");};

  return <section className="discovery-methods min-w-0 space-y-3" aria-label="战法分类">
    <header className="flex flex-wrap items-center justify-between gap-2">
      <div><h3 className="text-sm font-medium">方法分类</h3><p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">用途、时段与原始依据；仅查看已登记方法。</p></div>
      <button type="button" className="quiet-action" disabled={resource.pending} onClick={resource.refresh}>
        {resource.pending ? "读取中…" : "刷新方法"}
      </button>
    </header>

    <div className="discovery-method-filters flex flex-wrap items-center gap-2">
      <div className="flex flex-wrap gap-1" aria-label="方法生命周期">
        {LIFECYCLES.map(([key, label]) => <button key={key} type="button" aria-pressed={lifecycle === key}
          className={`ui-filter rounded-md px-2 py-1 text-xs ${lifecycle === key ? "ui-filter-selected" : "border border-zinc-200 dark:border-zinc-700"}`}
          onClick={() => setLifecycle(key)}>{label}{resource.data !== undefined && !malformed ? ` ${key === "all" ? rows.length : rows.filter(({row}) => row.status === key).length}` : ""}</button>)}
      </div>
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

    {(resource.error != null || malformed || invalidRows) && <p role="alert" className="text-xs text-amber-800 dark:text-amber-300">
      {malformed || invalidRows ? "方法返回结构不完整，当前分类未核实。" : "方法读取失败。"}
      {rows.length > 0 && (malformed || invalidRows ? " 以下仅列可读取登记。" : " 保留上次成功读取的登记，当前状态待重试。")}
    </p>}
    {resource.data === undefined && resource.pending && <div role="status" aria-label="正在读取方法" className="space-y-2" aria-busy="true">
      {[0, 1].map(key => <div key={key} className="h-14 animate-pulse rounded bg-zinc-100 dark:bg-zinc-900" aria-hidden="true"/>)}
    </div>}
    {resource.data !== undefined && rows.length === 0 && !malformed && !resource.error && <p className="text-xs text-zinc-600 dark:text-zinc-400">暂无已登记方法。</p>}
    {rows.length > 0 && visible.length === 0 && <div className="flex flex-wrap items-center gap-2 py-4 text-xs text-zinc-600 dark:text-zinc-400">
      <span>没有匹配方法。</span><button type="button" className="quiet-action" onClick={clear}>清除筛选</button>
    </div>}

    <ul className="discovery-method-list divide-y divide-zinc-200 dark:divide-zinc-800" aria-label="已登记方法">
      {visible.map(({row, metadata}) => {
        const supportsRuns = metadata.supports_saved_runs && (row.key === "daily_picks" || row.key === "intraday_watch");
        const statusLabel = LIFECYCLES.find(([key]) => key !== "all" && key === row.status)?.[1] || "状态未登记";
        const verificationLabel = row.verification?.available === true ? "有核验产物"
          : row.verification?.available === false ? "尚无核验产物" : "核验状态未记录";
        return <li key={row.key} className="discovery-method-row py-3 first:pt-0 last:pb-0">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <div className="flex min-w-0 flex-wrap items-baseline gap-2">
              <span className="text-sm font-medium">{row.name || row.key}</span>
              <span className="text-xs text-zinc-600 dark:text-zinc-400">{metadata.category_label} · {metadata.session_label}</span>
            </div>
            <span className="text-xs text-zinc-600 dark:text-zinc-400">{statusLabel}</span>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">{metadata.summary}</p>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-zinc-600 dark:text-zinc-400">
            {supportsRuns ? <SavedRunSummary row={row} date={date}/> : <span>{verificationLabel}{row.verification?.stale ? " · 需复核" : ""}</span>}
            <Link className="quiet-action ml-auto" href={discoveryMethodHref(row.key, search, date)}>
              {supportsRuns ? "查看原运行与版本" : "查看核验与反例"}<span className="sr-only">：{row.name || row.key}</span>
            </Link>
          </div>
        </li>;
      })}
    </ul>
    <p className="text-[11px] text-zinc-600 dark:text-zinc-400">分类不生成新名单；观察与已否决方法不参与当前选股，原入选不等于模拟成交。</p>
  </section>;
}
