"use client";

import {useEffect, useMemo, useRef, useState} from "react";
import {getOfficialBoardQuotes, getOfficialCatalog, type OfficialBoardQuotes, type OfficialCatalogItem, type OfficialCatalogKind, type OfficialCatalogPayload} from "@/lib/api/themes";
import {useResource} from "@/hooks/use-resource";
import {useIncremental} from "@/hooks/use-incremental";
import {IncrementalSentinel} from "@/components/ui/incremental-sentinel";
import {dateTimeTextBJ, fmt, pctColor, pctText} from "@/lib/format";
import type {DiscoveryDetailTarget} from "./discovery-detail";

class DirectoryIdentityError extends Error {}
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const codeText = (value: unknown): value is string => typeof value === "string" && /^\d{6}\.TI$/.test(value);
const named = (value: unknown): value is string => typeof value === "string" && !!value.trim();
const numeric = (value: unknown) => value == null || typeof value === "number" && Number.isFinite(value);
function checkedCatalog(value: OfficialCatalogPayload, kind: OfficialCatalogKind) {
  if (!object(value) || value.kind !== kind || value.source !== "ths_official" || !Array.isArray(value.items)
    || !Number.isSafeInteger(value.total) || value.total < value.items.length
    || value.items.some(item => !object(item) || item.kind !== kind || item.source !== "ths_official" || !codeText(item.code) || !named(item.name))
    || new Set(value.items.map(item => item.code)).size !== value.items.length) throw new DirectoryIdentityError("官方目录身份或结构不符");
  return value;
}
function checkedQuotes(value: OfficialBoardQuotes, kind: OfficialCatalogKind, codes: readonly string[]) {
  if (!object(value) || value.kind !== kind || value.source !== "ths_official" || value.source_basis !== "index_snapshot" || typeof value.available !== "boolean"
    || !Array.isArray(value.rows) || !Array.isArray(value.missing_codes)
    || value.rows.some(row => !object(row) || !codeText(row.code) || !codes.includes(row.code) || !numeric(row.change_pct) || !numeric(row.price))
    || value.missing_codes.some(code => !codeText(code) || !codes.includes(code))) throw new DirectoryIdentityError("报价批次身份或结构不符");
  const received = [...value.rows.map(row => row.code), ...value.missing_codes];
  if (value.available && (new Set(received).size !== received.length || received.length !== codes.length)) throw new DirectoryIdentityError("报价批次覆盖不符");
  return value;
}
function clockText(value: string | null | undefined) {
  return value && /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) && Number.isFinite(Date.parse(value)) ? `${dateTimeTextBJ(value)}（北京）` : "未提供";
}
type QuoteFact = {signature: string; state: "value" | "missing" | "error"; change: number | null; price: number | null; received?: string; sourceTime?: string | null; reason?: string};
type QuoteRequest = {kind: OfficialCatalogKind; signature: string; codes: string[]; controller: AbortController};

export interface DiscoveryOfficialDirectoryProps {
  kind: OfficialCatalogKind;
  search: string;
  scope?: string;
  onNavigate: (href: string, push?: boolean) => void;
  onTarget: (scope: string, target: DiscoveryDetailTarget | null) => void;
  selection?: {date: string | null; loaded: boolean; readError?: boolean; pending?: boolean; retry?: () => void};
}

/** Names and codes own their read. Optional quote batches cannot hold the directory hostage. */
export function DiscoveryOfficialDirectory({kind, search, scope: suppliedScope, onNavigate, onTarget, selection}: DiscoveryOfficialDirectoryProps) {
  const params = new URLSearchParams(search);
  const query = params.get("disc_q") ?? "";
  const code = params.get("disc_code") ?? "";
  const byName = params.get("disc_sort") === "name";
  const scope = suppliedScope ?? JSON.stringify(["catalog", kind, params.get("disc_range") === "latest_saved" ? "latest_saved" : "intraday", code]);
  const catalog = useResource(signal => getOfficialCatalog(kind, {signal}).then(data => checkedCatalog(data, kind)), {key:kind, abortable:true});
  const identityError = catalog.error instanceof DirectoryIdentityError;
  const data = identityError ? undefined : catalog.data;
  const [draft, setDraft] = useState({query, value:query});
  const [composing, setComposing] = useState(false);
  if (draft.query !== query) setDraft({query, value:query});
  const rows = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    const values = (data?.items ?? []).filter(row => `${row.name} ${row.code}`.toLocaleLowerCase().includes(term));
    return byName ? values.sort((a, b) => a.name.localeCompare(b.name, "zh-CN") || a.code.localeCompare(b.code)) : values;
  }, [data, query, byName]);
  const list = useIncremental(rows, {pageSize:24, step:24, resetKey:JSON.stringify([kind, query, byName])});
  const signature = useMemo(() => JSON.stringify([kind, data?.synced_at, data?.items.map(row => [row.code, row.name])]), [kind, data]);
  const [quotes, setQuotes] = useState<Map<string, QuoteFact>>(() => new Map());
  const requests = useRef(new Map<string, QuoteRequest>());
  const visibleCodes = list.shown.map(row => row.code);
  const visibleKey = JSON.stringify(visibleCodes);
  useEffect(() => {
    const wanted = JSON.parse(visibleKey) as string[];
    for (const [key, request] of requests.current) {
      if (request.kind !== kind || request.signature !== signature || !request.codes.some(code => wanted.includes(code))) {
        request.controller.abort(); requests.current.delete(key);
      }
    }
    if (!data || requests.current.size > 0) return;
    const pending = wanted.filter(code => quotes.get(`${kind}:${code}`)?.signature !== signature).slice(0, 48);
    if (!pending.length) return;
    const controller = new AbortController();
    const key = JSON.stringify([kind, signature, pending]);
    requests.current.set(key, {kind, signature, codes:pending, controller});
    const current = () => !controller.signal.aborted && requests.current.get(key)?.controller === controller;
    const settle = (facts: [string, QuoteFact][]) => {
      if (requests.current.get(key)?.controller === controller) requests.current.delete(key);
      setQuotes(previous => {
      const next = new Map(previous);
      for (const [code, fact] of facts) {const key = `${kind}:${code}`; next.delete(key); next.set(key, fact);}
      while (next.size > 512) next.delete(next.keys().next().value!);
        return next;
      });
    };
    void getOfficialBoardQuotes(kind, pending, {signal:controller.signal}).then(reply => {
      if (!current()) return;
      const result = checkedQuotes(reply, kind, pending);
      const values = new Map(result.rows.map(row => [row.code, row]));
      settle(pending.map(code => {
        const row = result.available ? values.get(code) : undefined;
        return [code, {signature, state:!result.available ? "error" : row ? "value" : "missing", change:row?.change_pct ?? null, price:row?.price ?? null,
          received:result.received_at, sourceTime:result.source_as_of, reason:result.available ? undefined : result.reason || "报价来源暂不可用"}];
      }));
    }).catch(problem => {
      if (current()) settle(pending.map(code => [code, {signature, state:"error", change:null, price:null, reason:problem instanceof Error ? problem.message : "报价读取失败"}]));
    }).finally(() => {if (requests.current.get(key)?.controller === controller) requests.current.delete(key);});
  }, [kind, data, signature, visibleKey, quotes]);
  useEffect(() => {const active = requests.current; return () => {for (const request of active.values()) request.controller.abort(); active.clear();};}, []);
  const chosen = data?.items.find(row => row.code === code);
  const target = useMemo(() => chosen ? {kind:"theme" as const, code:chosen.code, name:chosen.name, catalogKind:kind} : null, [chosen, kind]);
  useEffect(() => {onTarget(scope, target);}, [onTarget, scope, target]);
  const factFor = (rowCode: string) => {const fact = quotes.get(`${kind}:${rowCode}`); return fact?.signature === signature ? fact : undefined;};
  const known = visibleCodes.filter(rowCode => factFor(rowCode)?.change != null).length;
  const quotePending = visibleCodes.some(rowCode => !factFor(rowCode));
  const hasQuoteGap = visibleCodes.some(rowCode => {const fact = factFor(rowCode); return fact && (fact.state !== "value" || fact.change === null && fact.price === null);});
  const quoteError = visibleCodes.map(factFor).find(fact => fact?.state === "error");
  const navigate = (changes: Record<string, string | null>, push = false) => {
    const next = new URLSearchParams(search); next.set("view", "discover"); next.set("browse", "catalog"); next.delete("disc_method");
    for (const [key, value] of Object.entries(changes)) value === null || value === "" ? next.delete(key) : next.set(key, value);
    onNavigate(`/hunting?${next}`, push);
  };
  const retryQuotes = () => setQuotes(previous => {
    const next = new Map(previous);
    for (const rowCode of visibleCodes) {const key = `${kind}:${rowCode}`, fact = next.get(key); if (!hasQuoteGap || fact?.state !== "value" || fact.change === null && fact.price === null) next.delete(key);}
    return next;
  });
  return <section className="discovery-browser" aria-label="官方行业概念目录">
    <header className="discovery-heading"><div><h2>行业与概念</h2><p>正式名称与代码；报价按已展开范围补充</p></div>
      <button type="button" className="quiet-action" disabled={catalog.pending} onClick={catalog.refresh}>{catalog.pending ? "读取目录…" : "刷新目录"}</button></header>
    <div className="discovery-controls">
      <div className="discovery-kinds" aria-label="官方目录类别">{(["concept", "industry"] as const).map(value => <button key={value} type="button" aria-pressed={kind === value}
        onClick={() => navigate({disc_kind:value, disc_code:null, disc_q:null})}>{value === "concept" ? "概念" : "行业"}</button>)}</div>
      <label>顺序<select aria-label="目录顺序" value={byName ? "name" : "source"} onChange={event => navigate({disc_sort:event.target.value === "name" ? "name" : null, disc_code:null})}>
        <option value="source">原目录顺序</option><option value="name">名称</option></select></label>
      <label className="discovery-search"><span className="sr-only">搜索官方目录</span><input aria-label="搜索官方目录" placeholder="正式名称或代码" value={draft.value}
        onCompositionStart={() => setComposing(true)} onCompositionEnd={event => {setComposing(false); navigate({disc_q:event.currentTarget.value, disc_code:null});}}
        onChange={event => {setDraft({query, value:event.target.value}); if (!composing) navigate({disc_q:event.target.value, disc_code:null});}}/></label>
      {query && <button type="button" className="quiet-action" onClick={() => navigate({disc_q:null, disc_code:null})}>清除搜索</button>}
    </div>
    <div className="discovery-basis" role="note"><span>同花顺正式{kind === "concept" ? "概念" : "行业"}目录</span>
      <span>已返回 {data?.items.length ?? "—"} / {data?.total ?? "—"} 个</span><span>目录同步 {clockText(data?.synced_at)}</span>
      <span>已展开报价有值 {known} / {visibleCodes.length} · 报价时点未核，不是全市场榜</span></div>
    {data?.catalog_state && data.catalog_state !== "ready" && <p role="status" className="discovery-warning">{data.catalog_state === "stale" ? "目录记录已过保鲜期" : "目录同步时点未核"}；保留来源正式名称与代码，不冒充最新全市场状态。</p>}
    {catalog.error != null && <div role="alert" className="discovery-error"><span>{identityError ? "官方目录身份或结构不符，已停止展示。" : data ? "目录刷新失败，保留上次正式目录；当前同步状态未核。" : "官方目录读取失败；报价失败不代表目录为空。"}</span>
      <button type="button" className="quiet-action" disabled={catalog.pending} onClick={catalog.refresh}>重试目录</button></div>}
    {catalog.pending && !data && <div role="status" className="discovery-skeleton">正在读取正式名称和代码…</div>}
    {data && visibleCodes.length > 0 && <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-zinc-600 dark:text-zinc-400">
      <span>{quotePending ? "报价正在补充，可先浏览目录。" : quoteError ? `报价读取失败：${quoteError.reason}；名称代码仍可浏览。` : hasQuoteGap ? "部分报价未提供，缺数不补0。" : "报价仅对应已展开目录，来源时点未核。"}</span>
      <button className="quiet-action" type="button" disabled={quotePending} onClick={retryQuotes}>{quotePending ? "补充报价中…" : hasQuoteGap ? "重试缺失报价" : "刷新已展开报价"}</button>
    </div>}
    {data && rows.length === 0 && <p role="status" className="discovery-empty">{query ? "当前目录没有匹配名称或代码，可清除搜索。" : "本次目录未返回条目，不据此判断市场没有板块。"}</p>}
    {code && data && !chosen && <p role="status" className="discovery-warning">该代码不在本次正式目录，未换成同名板块。</p>}
    <ul className="discovery-results" aria-label="官方目录结果">{list.shown.map((row, index) => <DirectoryRow key={row.code} row={row} index={index} selected={row.code === code} quote={factFor(row.code)} onChoose={() => navigate({disc_code:row.code}, true)}/>)}</ul>
    <IncrementalSentinel sentinelRef={list.sentinelRef} visible={Math.min(list.visible, rows.length)} total={rows.length} unit="个目录" onLoadMore={list.loadMore}/>
    {selection?.readError && <div role="alert" className="discovery-error"><span>原名单读取失败，目录浏览不代表已完成入选对照。</span>{selection.retry && <button type="button" className="quiet-action" disabled={selection.pending} onClick={selection.retry}>{selection.pending ? "正在重试原名单…" : "重试原名单"}</button>}</div>}
    <p className="discovery-footnote">目录与报价分开读取；不按局部报价排名，不产生研究或模拟资格。{selection?.date ? `原名单日期 ${selection.date}，不代表当时归属。` : ""}</p>
  </section>;
}

function DirectoryRow({row, index, selected, quote, onChoose}: {row: OfficialCatalogItem; index: number; selected: boolean; quote?: QuoteFact; onChoose: () => void}) {
  return <li className="discovery-result" data-selected={selected}>
    <button className="discovery-result-open" type="button" data-discovery-code={row.code} aria-label={`查看${row.name}成分`} aria-pressed={selected} onClick={onChoose}>
      <span className="discovery-order" title="当前展示序号，不是板块强度排名">{index + 1}</span>
      <span className="discovery-name"><strong>{row.name}</strong><small>{row.code}</small></span>
      <span className={`discovery-measure ${pctColor(quote?.change)}`} title={quote?.received ? `读取记录 ${clockText(quote.received)}；来源行情时点${quote.sourceTime ? clockText(quote.sourceTime) : "未提供"}` : "尚未取得报价，不补0"}>{pctText(quote?.change)}<small>已读快照涨跌幅</small></span>
      <span className="discovery-measure">{fmt(quote?.price)}<small>板块指数快照</small></span>
      <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m9 5 7 7-7 7"/></svg>
    </button>
  </li>;
}
