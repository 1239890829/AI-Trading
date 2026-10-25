"use client";

import { useId, useMemo, useState } from "react";
import { StockLink } from "@/components/stock-link";
import { IncrementalSentinel } from "@/components/ui/incremental-sentinel";
import { useIncremental } from "@/hooks/use-incremental";
import { useResource } from "@/hooks/use-resource";
import { getBoardFlowMembers, getConceptDetail, type BoardFlowMembersPayload, type ConceptDetail } from "@/lib/api";
import { dateTimeTextBJ, fmt, pctColor, pctText } from "@/lib/format";

export interface DiscoveryDetailTarget {
  kind: "theme" | "board";
  code: string;
  name: string;
}

export interface DiscoveryDetailSelection {
  date: string | null;
  loaded: boolean;
  entries: readonly { symbol: string; name?: string | null; open: () => void }[];
}

interface DiscoveryDetailProps {
  target: DiscoveryDetailTarget;
  active?: boolean;
  selection: DiscoveryDetailSelection;
  onClose: () => void;
}

type DetailRead = { kind: "theme"; detail: ConceptDetail } | { kind: "board"; detail: BoardFlowMembersPayload };
type Member = { symbol: string; name: string; price: number | null; change: number | null; net: number | null };
class DetailIdentityError extends Error {}
const finite = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : null;
const isSymbol = (value: string) => /^\d{6}$/.test(value);

function readMoment(value?: string) {
  if (!value) return "未提供";
  return /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) && Number.isFinite(Date.parse(value))
    ? dateTimeTextBJ(value) : `${value}（时区未核）`;
}

/** Current membership is a reading reference, never a historic selection or a research command. */
export function DiscoveryDetail({ target, active = true, selection, onClose }: DiscoveryDetailProps) {
  const scope = `${target.kind}:${target.code}`;
  const searchId = useId();
  const resource = useResource<DetailRead>(async signal => {
    if (target.kind === "theme") {
      const detail = await getConceptDetail(target.code, { signal });
      if (detail.code !== target.code) throw new DetailIdentityError("成分身份不符");
      if (!Array.isArray(detail.members) || detail.members.some(member => !member || typeof member.symbol !== "string")) throw new Error("成分内容格式不符");
      return { kind: "theme", detail };
    }
    const detail = await getBoardFlowMembers(target.code, { signal });
    if (detail.board_code !== target.code) throw new DetailIdentityError("成分身份不符");
    if (!Array.isArray(detail.rows) || detail.rows.some(member => !member || typeof member.symbol !== "string")) throw new Error("成分内容格式不符");
    return { kind: "board", detail };
  }, { key: scope, enabled: active, intervalMs: null, abortable: true });
  const [filters, setFilters] = useState({ scope, query: "", ascending: false });
  if (filters.scope !== scope) setFilters({ scope, query: "", ascending: false });
  const query = filters.scope === scope ? filters.query : "";
  const ascending = filters.scope === scope && filters.ascending;
  const mismatch = resource.error instanceof DetailIdentityError;
  const read = mismatch ? undefined : resource.data;
  const unavailable = read?.kind === "board" && !read.detail.available;
  const members = useMemo<Member[]>(() => {
    if (!read || unavailable) return [];
    return read.kind === "theme"
      ? read.detail.members.map(member => ({ symbol: member.symbol, name: typeof member.name === "string" ? member.name : "",
        price: finite(member.price), change: finite(member.change_pct), net: null }))
      : read.detail.rows.map(member => ({ symbol: member.symbol, name: typeof member.name === "string" ? member.name : "",
        price: finite(member.price), change: finite(member.change_pct), net: finite(member.main_net_yi) }));
  }, [read, unavailable]);
  const invalidCount = members.filter(member => !isSymbol(member.symbol)).length;
  const shown = useMemo(() => {
    const term = query.trim().toLowerCase();
    return members.filter(member => isSymbol(member.symbol)
      && (!term || `${member.symbol} ${member.name}`.toLowerCase().includes(term))).sort((a, b) => {
      if (a.change === null) return b.change === null ? a.symbol.localeCompare(b.symbol) : 1;
      if (b.change === null) return -1;
      return (ascending ? 1 : -1) * (a.change - b.change) || a.symbol.localeCompare(b.symbol);
    });
  }, [members, query, ascending]);
  const incremental = useIncremental(shown, { resetKey: `${scope}/${query}/${ascending}`, active });
  const selected = useMemo(() => {
    const entries = new Map<string, DiscoveryDetailSelection["entries"][number]>();
    if (selection.loaded) for (const entry of selection.entries) {
      if (isSymbol(entry.symbol) && !entries.has(entry.symbol)) entries.set(entry.symbol, entry);
    }
    return entries;
  }, [selection.loaded, selection.entries]);
  const matches = new Set(members.filter(member => selected.has(member.symbol)).map(member => member.symbol)).size;
  const degraded = read?.kind === "board" ? read.detail.degraded : [];
  const declaredTotal = read?.kind === "theme" ? finite(read.detail.total) : null;

  if (!active) return null;

  return <section data-testid="discovery-detail" className="min-w-0 space-y-3" aria-label={`${target.name}成分与原名单对照`}>
    <header className="flex min-w-0 items-start justify-between gap-2">
      <div className="min-w-0"><h2 tabIndex={-1} className="break-words text-sm font-semibold">{target.name}</h2>
        <p className="mt-1 break-all font-mono text-[11px] text-zinc-600 dark:text-zinc-400">{target.code}</p></div>
      <button type="button" className="quiet-action shrink-0" onClick={onClose}>返回环境</button>
    </header>
    <div className="space-y-1 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">
      <p>{target.kind === "theme" ? "当前目录成分" : "资金成员样本，非完整成分全集"} · 目录归属与报价时点未核</p>
      {read?.kind === "board" && <p>读取记录时间：{readMoment(read.detail.updated_at)}；不等于来源时刻。{read.detail.delayed && "资金为延迟口径。"}</p>}
      <p>原名单日期：{selection.loaded ? selection.date || "未记录" : "待读取"}
        {selection.loaded && read && !unavailable ? ` · 代码相同 ${matches} 只` : ""}</p>
      <p>仅代码对照，非当时归属；成分展示不产生入选、研究或模拟资格。</p>
    </div>
    {resource.error != null && <div role="alert" className="space-y-1 text-xs text-amber-800 dark:text-amber-300">
      <p>{mismatch ? "返回成分身份不符，未展示该返回。" : read ? "成分刷新失败，保留上次读取；当前状态未核。" : "成分读取失败，当前无法确认成员。"}</p>
      <button type="button" className="quiet-action" onClick={resource.refresh}>重试成分读取</button>
    </div>}
    {unavailable && read?.kind === "board" && <div role="alert" className="space-y-1 text-xs text-amber-800 dark:text-amber-300">
      <p>成分来源暂不可用。{read.detail.reason || "来源未提供原因。"}未将附带记录当作当前成分。</p>
      <button type="button" className="quiet-action" onClick={resource.refresh}>重试成分读取</button>
    </div>}
    {!!degraded.length && <p role="status" className="text-xs text-amber-800 dark:text-amber-300">来源限制：{degraded.join("；")}</p>}
    {resource.pending && !read && !resource.error && <div aria-label="正在读取成分" className="space-y-2 py-1">
      {[0, 1, 2].map(index => <div key={index} className="h-10 animate-pulse rounded bg-zinc-100 dark:bg-zinc-800"/>)}</div>}
    {read && !unavailable && <>
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1 text-xs" htmlFor={searchId}>查找成分
          <input id={searchId} type="search" value={query} placeholder="代码 / 名称"
            onChange={event => setFilters(current => ({...current, query: event.target.value}))}
            className="mt-1 min-h-8 w-full min-w-0 rounded border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700"/>
        </label>
        <button type="button" className="quiet-action" aria-label={`涨跌幅${ascending ? "升序" : "降序"}，点击切换`}
          onClick={() => setFilters(current => ({...current, ascending: !ascending}))}>
          涨跌幅 {ascending ? "从低到高" : "从高到低"}
        </button>
      </div>
      <p className="text-[11px] text-zinc-600 dark:text-zinc-400">匹配 {shown.length} / 已读 {members.length - invalidCount} 条
        {declaredTotal !== null && ` · 目录声明 ${declaredTotal} 只`}
        {invalidCount > 0 && `；${invalidCount} 条证券身份无效，未展示`}</p>
      {!shown.length && <p role="status" className="py-3 text-xs text-zinc-600 dark:text-zinc-400">
        {members.length === 0 ? "本次返回没有成员记录，不表示历史上没有成分。"
          : members.length === invalidCount ? "返回记录缺少有效证券代码，未展示成员。"
          : "当前搜索无匹配，可清除关键词。"}</p>}
      <ul className="divide-y divide-zinc-200 dark:divide-zinc-800" aria-label="已读取成分">
        {incremental.shown.map((member, index) => {
          const original = selected.get(member.symbol);
          return <li key={`${scope}:${member.symbol}:${index}`} className="py-3">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-2">
              <StockLink symbol={member.symbol} className="min-w-0 break-words text-xs font-medium">{member.name || member.symbol}</StockLink>
              <span className={`font-mono text-xs tabular-nums ${pctColor(member.change)}`} title={member.change === null ? "涨跌幅未提供" : "成分报价涨跌幅，来源时刻未核"}>{pctText(member.change)}</span>
              <span className="mt-1 font-mono text-[11px] text-zinc-600 dark:text-zinc-400">{member.symbol}</span>
              <span className="mt-1 text-right font-mono text-[11px] tabular-nums text-zinc-600 dark:text-zinc-400">{fmt(member.price)}</span>
            </div>
            {target.kind === "board" && <p className="mt-1 text-[11px] text-zinc-600 dark:text-zinc-400">主力净额 {member.net === null ? "未提供" : `${member.net > 0 ? "+" : ""}${fmt(member.net)} 亿元`}</p>}
            {original && <div className="mt-2 flex flex-wrap items-center justify-between gap-1 text-[11px]">
              <span className="text-zinc-600 dark:text-zinc-400">原名单含该股</span>
              <button type="button" className="quiet-action" onClick={original.open} aria-label={`返回原名单核对${member.name || member.symbol}`}>返回原名单核对</button>
            </div>}
          </li>;
        })}
      </ul>
      <IncrementalSentinel sentinelRef={incremental.sentinelRef} visible={incremental.visible} total={shown.length}
        unit="条成分" testId="discovery-member-sentinel" onLoadMore={incremental.loadMore}/>
    </>}
  </section>;
}
