"use client";

import { useId, useMemo, useRef, useState } from "react";
import { StockLink } from "@/components/stock-link";
import { IncrementalSentinel } from "@/components/ui/incremental-sentinel";
import { useIncremental } from "@/hooks/use-incremental";
import { useResource } from "@/hooks/use-resource";
import { getBoardFlowMembers, getSinaBoardConstituents, getConceptDetail,
  type BoardFlowMembersPayload, type SinaBoardConstituentsPayload, type ConceptDetail } from "@/lib/api";
import { dateTimeTextBJ, fmt, pctColor, pctText } from "@/lib/format";

export interface DiscoveryDetailTarget {
  kind: "theme" | "board";
  code: string;
  name: string;
  catalogKind?: "concept" | "industry";
  source?: "sina" | "eastmoney";
}

export interface DiscoveryDetailSelection {
  date: string | null;
  loaded: boolean;
  readError?: boolean;
  pending?: boolean;
  retry?: () => void;
  entries: readonly { symbol: string; name?: string | null; open: () => void }[];
}

interface DiscoveryDetailProps {
  target: DiscoveryDetailTarget;
  active?: boolean;
  selection: DiscoveryDetailSelection;
  onClose: () => void;
}

type DetailRead = { kind: "theme"; detail: ConceptDetail }
  | { kind: "sina"; detail: SinaBoardConstituentsPayload }
  | { kind: "board"; detail: BoardFlowMembersPayload };
type Member = { symbol: string; name: string; price: number | null; change: number | null;
  net?: number | null; mainNet?: number | null };
class DetailIdentityError extends Error {}
const finite = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : null;
const isSymbol = (value: string) => /^\d{6}$/.test(value);

function readMoment(value?: string | null) {
  if (!value) return "未提供";
  return /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) && Number.isFinite(Date.parse(value))
    ? dateTimeTextBJ(value) : `${value}（时区未核）`;
}

/** Current membership is a reading reference, never a historic selection or a research command. */
export function DiscoveryDetail({ target, active = true, selection, onClose }: DiscoveryDetailProps) {
  const sinaTarget = target.kind === "board" && target.source === "sina";
  const rankingTarget = target.kind === "board" && !sinaTarget;
  const scope = `${target.kind}:${sinaTarget ? "sina" : target.kind === "theme" ? "ths" : "eastmoney"}:${target.catalogKind ?? "concept"}:${target.code}`;
  const searchId = useId();
  const freshRead = useRef<string | null>(null);
  const resource = useResource<DetailRead>(async signal => {
    const fresh = freshRead.current === scope;
    freshRead.current = null;
    if (target.kind === "theme") {
      const detail = await getConceptDetail(target.code, { signal, catalogKind: target.catalogKind });
      if (detail.code !== target.code) throw new DetailIdentityError("成分身份不符");
      if (detail.kind && detail.kind !== (target.catalogKind ?? "concept")) throw new DetailIdentityError("成分类别不符");
      if (!Array.isArray(detail.members) || detail.members.some(member => !member || typeof member.symbol !== "string")) throw new Error("成分内容格式不符");
      return { kind: "theme", detail };
    }
    if (sinaTarget) {
      if (/^BK/i.test(target.code)) throw new DetailIdentityError("成分来源身份不符");
      const detail = await getSinaBoardConstituents(target.code, target.catalogKind ?? "concept", { signal, ...(fresh ? {fresh: true} : {}) });
      if (detail.source !== "sina_board_funds" || detail.category !== target.code || detail.kind !== (target.catalogKind ?? "concept")) throw new DetailIdentityError("成分来源身份或类别不符");
      if (typeof detail.available !== "boolean" || detail.source_as_of !== null || !Array.isArray(detail.rows)
        || detail.rows.some(member => !member || !isSymbol(member.symbol) || typeof member.name !== "string"
          || [member.price, member.change_pct, member.net_inflow_yi, member.r0_net_yi].some(value => value !== null && finite(value) === null)
          || member.source_as_of !== null)
        || new Set(detail.rows.map(member=>member.symbol)).size !== detail.rows.length
        || !Array.isArray(detail.degraded) || detail.degraded.some(message=>typeof message!=="string")
        || !detail.coverage || !Number.isSafeInteger(detail.coverage.returned) || detail.coverage.returned !== detail.rows.length
        || !(detail.coverage.total === null || Number.isSafeInteger(detail.coverage.total) && detail.coverage.total >= detail.coverage.returned)
        || typeof detail.coverage.complete !== "boolean" || detail.coverage.complete && detail.coverage.total !== detail.coverage.returned
        || detail.available && (typeof detail.name !== "string" || !detail.name.trim())) throw new Error("成分内容或覆盖格式不符");
      return { kind: "sina", detail };
    }
    if (!/^BK\d{3,4}$/.test(target.code)) throw new DetailIdentityError("资金排行来源身份不符");
    const detail = await getBoardFlowMembers(target.code, {signal});
    if (detail.board_code !== target.code) throw new DetailIdentityError("资金排行身份不符");
    if (typeof detail.available !== "boolean" || !Array.isArray(detail.rows)
      || detail.rows.some(member=>!member || !isSymbol(member.symbol) || typeof member.name !== "string")
      || new Set(detail.rows.map(member=>member.symbol)).size !== detail.rows.length
      || !Array.isArray(detail.degraded) || detail.degraded.some(message=>typeof message!=="string")) throw new Error("资金排行内容格式不符");
    return {kind:"board",detail};
  }, { key: scope, enabled: active, intervalMs: null, abortable: true });
  const [filters, setFilters] = useState({ scope, query: "", ascending: false });
  if (filters.scope !== scope) setFilters({ scope, query: "", ascending: false });
  const query = filters.scope === scope ? filters.query : "";
  const ascending = filters.scope === scope && filters.ascending;
  const mismatch = resource.error instanceof DetailIdentityError;
  const read = mismatch ? undefined : resource.data;
  const unavailable = read && read.kind !== "theme" && !read.detail.available;
  const members = useMemo<Member[]>(() => {
    if (!read || unavailable) return [];
    return read.kind === "theme"
      ? read.detail.members.map(member => ({ symbol: member.symbol, name: typeof member.name === "string" ? member.name : "",
        price: finite(member.price), change: finite(member.change_pct) }))
      : read.kind === "sina" ? read.detail.rows.map(member => ({symbol:member.symbol,name:member.name,
        price:finite(member.price),change:finite(member.change_pct),net:finite(member.net_inflow_yi),mainNet:finite(member.r0_net_yi)}))
      : read.detail.rows.map(member => ({symbol:member.symbol,name:member.name,
        price:finite(member.price),change:finite(member.change_pct),mainNet:finite(member.main_net_yi)}));
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
    if (selection.loaded && !selection.readError) for (const entry of selection.entries) {
      if (isSymbol(entry.symbol) && !entries.has(entry.symbol)) entries.set(entry.symbol, entry);
    }
    return entries;
  }, [selection.loaded, selection.readError, selection.entries]);
  const matches = new Set(members.filter(member => selected.has(member.symbol)).map(member => member.symbol)).size;
  const degraded = read && read.kind !== "theme" ? read.detail.degraded : [];
  const declaredTotal = read?.kind === "theme" ? finite(read.detail.total) : read?.kind === "sina" ? finite(read.detail.coverage.total) : null;
  const retry = () => {if (sinaTarget) freshRead.current = scope; resource.refresh();};
  const subject = rankingTarget ? "资金排行" : "成分";
  const daily = read?.kind === "sina" ? read.detail.daily_snapshot : null;
  const dailyDay = daily?.available === true && typeof daily.source_day === "string" && /^\d{4}-\d{2}-\d{2}$/.test(daily.source_day) ? daily.source_day : null;
  const dailyReasons = Array.isArray(daily?.degraded) ? daily.degraded.filter((reason):reason is string=>typeof reason === "string" && !!reason.trim()).slice(0,3) : [];

  if (!active) return null;

  return <section data-testid="discovery-detail" className="min-w-0 space-y-3" aria-label={`${target.name}${subject}与原名单对照`}>
    <header className="flex min-w-0 items-start justify-between gap-2">
      <div className="min-w-0"><h2 tabIndex={-1} className="break-words text-sm font-semibold">{target.name}</h2>
        <p className="mt-1 break-all font-mono text-[11px] text-zinc-600 dark:text-zinc-400">{target.code}</p></div>
      <button type="button" className="quiet-action shrink-0" onClick={onClose}>返回环境</button>
    </header>
    <div className="space-y-1 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">
      <p>{target.kind === "theme" ? "来源目录成分记录 · 目录归属与报价时点未核" : sinaTarget ? "新浪板块同源成分 · 全部净流入与主力口径分开" : "东财个股资金排行 Top20 · 不是成分全集"}</p>
      {read?.kind === "theme" && <>
        {read.detail.membership_state === "stale" && <p role="status">成员记录已过保鲜期，当前变动待核；不当作最新归属。</p>}
        {read.detail.catalog_state === "stale" && <p role="status">目录同步已过保鲜期，保留来源原名与原代码。</p>}
        {read.detail.quote_freshness && read.detail.quote_freshness.state !== "ready" && <p>报价状态：{read.detail.quote_freshness.state === "stale" ? "已存旧快照" : "当前时效未核"}。{read.detail.quote_freshness.reason}</p>}
      </>}
      {read?.kind === "sina" && <>
        <p>成分来源：新浪资金 · 来源时刻 {readMoment(read.detail.source_as_of)}</p>
        {read.detail.available && read.detail.name !== target.name && <p>成员来源名称：{read.detail.name}；代码保持 {target.code}。</p>}
        <p>读取完成：{readMoment(read.detail.received_at)}；不等于来源时刻或历史归属。</p>
        {read.detail.available && <p>覆盖 {read.detail.coverage.returned} / {read.detail.coverage.total ?? "未核"} 只 · {read.detail.coverage.complete ? "本次来源范围已读全" : "来源覆盖尚不完整"}；报价缺失不代表没有成分。</p>}
      </>}
      {read?.kind === "sina" && <p role={daily?.available === false ? "status" : undefined}>金额为亿元；全部净流入与主力（单笔≥100万元）分开。
        {daily?.available === false ? "日线核对未完成。" : dailyDay ? `日线核对日期 ${dailyDay}，不作为盘中来源时刻。` : "日线核对状态未核。"}
        {dailyReasons.length > 0 && `日线限制：${dailyReasons.join("；")}。`}</p>}
      {read?.kind === "board" && <p>东方财富主力资金排行样本，仅返回接口前20名；不能据此判断板块全部成员。{read.detail.delayed ? "来源标记延迟。" : ""}读取记录 {readMoment(read.detail.updated_at)}，来源时点未核。</p>}
      <p>原名单日期：{selection.readError ? "读取失败，未作对照" : selection.loaded ? selection.date || "未记录" : "待读取"}
        {selection.loaded && !selection.readError && read && !unavailable ? ` · 代码相同 ${matches} 只` : ""}</p>
      {selection.readError && selection.retry && <p role="alert"><button type="button" className="quiet-action" disabled={selection.pending} onClick={selection.retry}>{selection.pending ? "正在重试原名单…" : "重试原名单"}</button></p>}
      <p>仅代码对照，非当时归属；展示不产生入选、研究或模拟资格。</p>
    </div>
    {resource.error != null && <div role="alert" className="space-y-1 text-xs text-amber-800 dark:text-amber-300">
      <p>{mismatch ? `返回${subject}身份不符，未展示该返回。` : read ? `${subject}刷新失败，保留上次读取；当前状态未核。` : `${subject}读取失败，当前无法确认${rankingTarget ? "排行" : "成员"}。`}</p>
      <button type="button" className="quiet-action" disabled={resource.pending} onClick={retry}>重试{subject}读取</button>
    </div>}
    {unavailable && read && <div role="alert" className="space-y-1 text-xs text-amber-800 dark:text-amber-300">
      <p>{subject}来源暂不可用。{read.detail.reason || "来源未提供原因。"}未将附带记录当作当前{subject}。</p>
      <button type="button" className="quiet-action" disabled={resource.pending} onClick={retry}>重试{subject}读取</button>
    </div>}
    {!!degraded.length && <p role="status" className="text-xs text-amber-800 dark:text-amber-300">来源限制：{degraded.join("；")}</p>}
    {resource.pending && !read && !resource.error && <div aria-label={`正在读取${subject}`} className="space-y-2 py-1">
      {[0, 1, 2].map(index => <div key={index} className="h-10 animate-pulse rounded bg-zinc-100 dark:bg-zinc-800"/>)}</div>}
    {read && !unavailable && <>
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1 text-xs" htmlFor={searchId}>查找{subject}
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
      <ul className="divide-y divide-zinc-200 dark:divide-zinc-800" aria-label={`已读取${subject}`}>
        {incremental.shown.map((member, index) => {
          const original = selected.get(member.symbol);
          return <li key={`${scope}:${member.symbol}:${index}`} className="py-3">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-2">
              <StockLink symbol={member.symbol} className="min-w-0 break-words text-xs font-medium">{member.name || member.symbol}</StockLink>
              <span className={`font-mono text-xs tabular-nums ${pctColor(member.change)}`} title={member.change === null ? "涨跌幅未提供" : "成分报价涨跌幅，来源时刻未核"}>{pctText(member.change)}</span>
              <span className="mt-1 font-mono text-[11px] text-zinc-600 dark:text-zinc-400">{member.symbol}</span>
              <span className="mt-1 text-right font-mono text-[11px] tabular-nums text-zinc-600 dark:text-zinc-400">{fmt(member.price)}</span>
            </div>
            {read.kind === "sina" && <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-zinc-600 dark:text-zinc-400">
              <span>全部净流入 <b className={`font-mono font-medium tabular-nums ${pctColor(member.net ?? null)}`}>{fmt(member.net ?? null)}亿</b></span>
              <span>主力 <b className={`font-mono font-medium tabular-nums ${pctColor(member.mainNet ?? null)}`}>{fmt(member.mainNet ?? null)}亿</b></span>
            </div>}
            {read.kind === "board" && <p className="mt-1 text-[11px] text-zinc-600 dark:text-zinc-400">排行主力净额 <span className="font-mono tabular-nums">{fmt(member.mainNet ?? null)}亿</span></p>}
            {original && <div className="mt-2 flex flex-wrap items-center justify-between gap-1 text-[11px]">
              <span className="text-zinc-600 dark:text-zinc-400">原名单含该股</span>
              <button type="button" className="quiet-action" onClick={original.open} aria-label={`返回原名单核对${member.name || member.symbol}`}>返回原名单核对</button>
            </div>}
          </li>;
        })}
      </ul>
      <IncrementalSentinel sentinelRef={incremental.sentinelRef} visible={incremental.visible} total={shown.length}
        unit={`条${subject}`} testId="discovery-member-sentinel" onLoadMore={incremental.loadMore}/>
    </>}
  </section>;
}
