"use client";

import {useMemo, useState} from "react";
import {useResource} from "@/hooks/use-resource";
import {getIndexMembers} from "@/lib/api";
import {dateTimeTextBJ} from "@/lib/format";

/** Membership only: opening a large index never subscribes its whole universe. */
export function IndexMembersPanel({symbol, active, onSelect}: {symbol: string; active: boolean; onSelect: (symbol: string) => void}) {
  const resource = useResource(() => getIndexMembers(symbol), {key: symbol, enabled: active, intervalMs: null, marketHours: false});
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const rows = useMemo(() => {
    const term = query.trim().toLowerCase();
    return (resource.data?.items ?? []).filter(row => !term || `${row.name}${row.symbol}`.toLowerCase().includes(term));
  }, [resource.data, query]);
  const pages = Math.max(1, Math.ceil(rows.length / 30));
  const current = Math.min(page, pages - 1);
  return <div className="index-members flex h-full min-h-0 flex-col">
    <div className="flex shrink-0 flex-col gap-2 border-b border-zinc-200 p-3 dark:border-zinc-800">
      <div className="flex items-center justify-between gap-2 text-xs"><span>{resource.data?.name ?? "指数成分"}</span><span className="tabular-nums text-zinc-500">{resource.data ? `${resource.data.items.length} 只` : resource.error ? "暂不可用" : "读取中"}</span></div>
      <input aria-label="查找指数成分股" type="search" value={query} placeholder="名称或代码" onChange={event => {setQuery(event.target.value); setPage(0);}} className="w-full min-w-0 rounded-lg border border-zinc-200 bg-transparent px-3 py-2 text-xs outline-offset-2 dark:border-zinc-700"/>
      {resource.error != null && <div role="status" className="flex items-center justify-between gap-2 text-xs text-amber-700 dark:text-amber-400"><span>成分股读取失败{resource.data ? "，保留上次列表" : ""}</span><button type="button" data-action="secondary" onClick={resource.refresh} disabled={resource.pending}>重试</button></div>}
    </div>
    <div className="data-scroll min-h-0 flex-1 overflow-y-auto" aria-label="指数成分股列表">
      {!resource.data && !resource.error && <p className="p-3 text-xs text-zinc-500">正在读取成分股…</p>}
      {resource.data && !rows.length && <p className="p-3 text-xs text-zinc-500">没有匹配的成分股</p>}
      {rows.slice(current * 30, (current + 1) * 30).map(row => <button key={row.symbol} type="button" onClick={() => onSelect(row.symbol)} className="index-member-row flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-xs hover:bg-zinc-500/10 focus-visible:outline-offset-[-2px]" title={`查看${row.name}盘口`}><span className="min-w-0 truncate">{row.name}</span><span className="shrink-0 tabular-nums text-zinc-500">{row.symbol}</span></button>)}
    </div>
    {resource.data && <div className="flex shrink-0 flex-col gap-2 border-t border-zinc-200 p-3 text-[11px] text-zinc-500 dark:border-zinc-800">
      <div className="flex items-center justify-between gap-2"><button type="button" data-action="secondary" disabled={!current} onClick={() => setPage(current - 1)}>上一页</button><span className="whitespace-nowrap tabular-nums">{current + 1} / {pages}</span><button type="button" data-action="secondary" disabled={current + 1 >= pages} onClick={() => setPage(current + 1)}>下一页</button></div>
      <span>同花顺 · {dateTimeTextBJ(resource.data.snapshot_at)}<br/>当前成员关系，非涨幅排名。点股票查看盘口。</span>
    </div>}
  </div>;
}
