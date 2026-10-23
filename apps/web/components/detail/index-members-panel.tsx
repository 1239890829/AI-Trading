"use client";

import {useId, useMemo, useState} from "react";
import {useResource} from "@/hooks/use-resource";
import {getIndexMembers} from "@/lib/api";
import {dateTimeTextBJ, pctColor, pctText, sourceLabel} from "@/lib/format";

const finitePct = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value) ? value : null;

/** Membership only: opening a large index never subscribes its whole universe. */
export function IndexMembersPanel({symbol, active, onSelect}: {symbol: string; active: boolean; onSelect: (symbol: string) => void}) {
  const resource = useResource(() => getIndexMembers(symbol), {key: symbol, enabled: active, intervalMs: null, marketHours: false});
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [direction, setDirection] = useState<"desc" | "asc">("desc");
  const orderNote = useId();
  const rows = useMemo(() => {
    const term = query.trim().toLowerCase();
    return (resource.data?.items ?? []).map((row, order) => ({row, order})).sort((a, b) => {
      const left = finitePct(a.row.change_pct), right = finitePct(b.row.change_pct);
      if (left === null) return right === null ? a.order - b.order : 1;
      if (right === null) return -1;
      return (direction === "desc" ? right - left : left - right) || a.order - b.order;
    }).map(({row}) => row).filter(row => !term || `${row.name}${row.symbol}`.toLowerCase().includes(term));
  }, [resource.data, query, direction]);
  const known = resource.data?.items.filter(row => finitePct(row.change_pct) !== null).length ?? 0;
  const pages = Math.max(1, Math.ceil(rows.length / 30));
  const current = Math.min(page, pages - 1);
  return <div className="index-members flex h-full min-h-0 flex-col">
    <div className="flex shrink-0 flex-col gap-2 border-b border-zinc-200 p-3 dark:border-zinc-800">
      <div className="flex items-center justify-between gap-2 text-xs"><span>{resource.data?.name ?? "指数成分"}</span><span className="tabular-nums text-zinc-500">{resource.data ? `${resource.data.items.length} 只` : resource.error ? "暂不可用" : "读取中"}</span></div>
      <input aria-label="查找指数成分股" type="search" value={query} placeholder="名称或代码" onChange={event => {setQuery(event.target.value); setPage(0);}} className="w-full min-w-0 rounded-lg border border-zinc-200 bg-transparent px-3 py-2 text-xs outline-offset-2 dark:border-zinc-700"/>
      <div role="group" aria-label="成分股排序" aria-describedby={orderNote} className="flex flex-wrap gap-2 text-xs">{(["desc", "asc"] as const).map(value => <button key={value} type="button" data-action="secondary" aria-pressed={direction === value} onClick={() => {setDirection(value); setPage(0);}}>涨跌幅{value === "desc" ? "降序" : "升序"}</button>)}</div>
      {resource.error != null && <div role="status" className="flex items-center justify-between gap-2 text-xs text-amber-700 dark:text-amber-400"><span>成分股读取失败{resource.data ? "，保留上次列表" : ""}</span><button type="button" data-action="secondary" onClick={resource.refresh} disabled={resource.pending}>重试</button></div>}
    </div>
    <div className="data-scroll min-h-0 flex-1 overflow-y-auto" aria-label="指数成分股列表">
      {!resource.data && !resource.error && <p className="p-3 text-xs text-zinc-500">正在读取成分股…</p>}
      {resource.data && !rows.length && <p className="p-3 text-xs text-zinc-500">没有匹配的成分股</p>}
      {rows.slice(current * 30, (current + 1) * 30).map(row => <button key={row.symbol} type="button" onClick={() => onSelect(row.symbol)} className="index-member-row flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-xs hover:bg-zinc-500/10 focus-visible:outline-offset-[-2px]" title={`查看${row.name}盘口`}><span className="min-w-0 flex-1 truncate">{row.name}</span><span className="shrink-0 tabular-nums text-zinc-500">{row.symbol}</span><span className={`w-16 shrink-0 text-right tabular-nums ${pctColor(finitePct(row.change_pct))}`} title={`${sourceLabel(row.quote_source)} · 源时刻 ${row.source_clock ? dateTimeTextBJ(row.source_clock, {seconds: true}) : "未提供"} · 质量 ${row.quote_quality ?? "unknown"}${row.quote_observed_at ? ` · 观察 ${dateTimeTextBJ(row.quote_observed_at, {seconds: true})}` : ""}`}>{pctText(finitePct(row.change_pct))}</span></button>)}
    </div>
    {resource.data && <div className="flex shrink-0 flex-col gap-2 border-t border-zinc-200 p-3 text-[11px] text-zinc-500 dark:border-zinc-800">
      <div className="flex items-center justify-between gap-2"><button type="button" data-action="secondary" disabled={!current} onClick={() => setPage(current - 1)}>上一页</button><span className="whitespace-nowrap tabular-nums">{current + 1} / {pages}</span><button type="button" data-action="secondary" disabled={current + 1 >= pages} onClick={() => setPage(current + 1)}>下一页</button></div>
      <span id={orderNote}>涨跌幅已知 {known} / {resource.data.items.length} · 全部返回成员排序，未知置后；搜索后分页。<br/>已存行情观察，非全体实时保证；{resource.data.quote_snapshot?.observed_at ? `观察于 ${dateTimeTextBJ(resource.data.quote_snapshot.observed_at, {seconds: true})} · ` : ""}{resource.data.quote_snapshot?.source_clock ? `源时刻 ${dateTimeTextBJ(resource.data.quote_snapshot.source_clock, {seconds: true})}` : "源时刻未提供"} · 质量 {resource.data.quote_snapshot?.quality ?? "unknown"}。<br/>成员关系：同花顺 · {dateTimeTextBJ(resource.data.snapshot_at)}。点股票查看盘口。</span>
    </div>}
  </div>;
}
