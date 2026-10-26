"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useResource } from "@/hooks/use-resource";
import { useIncremental } from "@/hooks/use-incremental";
import { IncrementalSentinel } from "@/components/ui/incremental-sentinel";
import { StockLink } from "@/components/stock-link";
import { DiscoveryMethods } from "./discovery-methods";
import { DiscoveryOfficialDirectory } from "./discovery-official-directory";
import { groupHotThemes } from "./discovery-hot-groups";
import type { StrategyRegistryItem } from "@/lib/api/picks";
import type { DiscoveryDetailTarget } from "./discovery-detail";
import { getBoardFundFlow, getSinaBoardFundFlow, getDiscoveryMemberships, getThemesHot, type BoardFlowKind, type BoardFlowRange, type BoardFlowRow, type HotTheme, type ThemesHotPayload, type BoardFundFlowPayload, type SinaBoardFundFlowPayload, type SinaBoardFlowRow } from "@/lib/api";
import { dateTimeTextBJ, pctColor, pctText } from "@/lib/format";

const MODES = [["selected", "入选名单"], ["hot", "热门题材"], ["catalog", "行业概念"], ["funds", "资金方向"], ["methods", "战法分类"]] as const;
export type DiscoveryMode = typeof MODES[number][0];
export type DiscoverySelection = {date: string | null; loaded: boolean; readError?: boolean; pending?: boolean; retry?: () => void; entries: readonly {symbol: string; name?: string | null; open: () => void}[]};

export function discoveryLocation(search: string) {
  const params = new URLSearchParams(search);
  const raw = params.get("browse");
  const mode = MODES.find(([key]) => key === raw)?.[0] ?? "selected";
  const kind: BoardFlowKind = params.get("disc_kind") === "industry" ? "industry" : params.get("disc_kind") === "concept" ? "concept" : mode === "catalog" ? "industry" : "concept";
  const rawRange = params.get("disc_range");
  const code = params.get("disc_code") ?? "";
  const explicitSource = params.get("disc_source");
  const legacyFundsRange = ["intraday", "5d", "10d", "20d", "latest_saved", "changes"].includes(rawRange ?? "");
  const fundsSource: "sina" | "eastmoney" = explicitSource === "sina" ? "sina"
    : explicitSource === "eastmoney" || !params.has("disc_source") && (/^BK\d{3,4}$/.test(code) || legacyFundsRange) ? "eastmoney" : "sina";
  const range: BoardFlowRange = mode === "catalog" ? rawRange === "latest_saved" ? "latest_saved" : "intraday"
    : mode === "funds" && fundsSource === "sina" ? "intraday"
    : mode === "funds" ? ["intraday", "5d", "10d", "20d", "latest_saved", "changes"].includes(rawRange ?? "") ? rawRange as BoardFlowRange : "changes" : "intraday";
  return {mode, kind, range, code, fundsSource, query: params.get("disc_q") ?? "", sort: params.get("disc_sort") ?? "desc",
    scope: JSON.stringify(mode === "funds" ? [mode, kind, range, code, fundsSource] : [mode, kind, range, code])};
}

export function discoveryUrl(search: string, changes: Record<string, string | null>) {
  const params = new URLSearchParams(search);
  params.set("view", "discover");
  params.delete("review"); params.delete("panel");
  for (const [key, value] of Object.entries(changes)) value === null || value === "" ? params.delete(key) : params.set(key, value);
  return `/hunting?${params}`;
}

function moveFocus(event: KeyboardEvent<HTMLElement>) {
  if (event.nativeEvent.isComposing || event.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const links = Array.from(event.currentTarget.querySelectorAll<HTMLAnchorElement>("a"));
  const index = links.indexOf(event.target as HTMLAnchorElement);
  if (index < 0) return;
  event.preventDefault();
  const next = event.key === "Home" ? 0 : event.key === "End" ? links.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + links.length) % links.length;
  links[next]?.focus({preventScroll: true});
}

export function DiscoveryNavigation({search}: {search: string}) {
  const {mode} = discoveryLocation(search);
  return <nav className="discovery-nav" aria-label="选股分类发现" onKeyDown={moveFocus}>
    {MODES.map(([key, name]) => <Link key={key} scroll={false} aria-current={mode === key ? "page" : undefined}
      href={discoveryUrl(search, {browse: key === "selected" ? null : key, disc_code: null, disc_method: null, disc_group: null, disc_q: null, disc_sort: null, disc_kind: null, disc_range: null, disc_source: null, selection: null})}>{name}</Link>)}
  </nav>;
}

function validNumber(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
export function discoveryFlowValue(row: BoardFlowRow, range: BoardFlowRange): number | null {
  const value = range === "5d" ? row.main_net_5d_yi : range === "10d" ? row.main_net_10d_yi : range === "20d" ? row.main_net_20d_yi : row.main_net_yi;
  return validNumber(value) ? value : null;
}
export function discoveryCompare(a: number | null | undefined, b: number | null | undefined, ascending = false) {
  if (!validNumber(a)) return validNumber(b) ? 1 : 0;
  if (!validNumber(b)) return -1;
  return ascending ? a - b : b - a;
}
function signed(value: number | null | undefined) {return validNumber(value) ? `${value > 0 ? "+" : ""}${value.toLocaleString("zh-CN", {maximumFractionDigits: 2})}亿` : "未提供";}

class DiscoveryReadError extends Error {}
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const nullableNumber = (value: unknown) => value == null || validNumber(value);
function checkHot(data: ThemesHotPayload) {
  if (!object(data) || !Array.isArray(data.themes) || !Array.isArray(data.stocks)
    || data.ranking_basis !== "hot_stock_concentration"
    || data.themes.some(row => !object(row) || !text(row.theme) || !(row.theme_code == null || text(row.theme_code))
      || row.source !== "ths_official" || !text(row.theme_code) || !/^\d{6}\.TI$/.test(row.theme_code)
      || !nullableNumber(row.heat) || !nullableNumber(row.concentration_pct) || !nullableNumber(row.member_count)
      || !Array.isArray(row.hot_symbols) || row.hot_symbols.some(symbol => typeof symbol !== "string" || !/^\d{6}$/.test(symbol))
      || new Set(row.hot_symbols).size !== row.hot_count || !Number.isSafeInteger(row.hot_count) || (row.hot_count as number) < 0
      || !(row.best == null || object(row.best) && text(row.best.symbol) && validNumber(row.best.rank)))
    || data.stocks.some(row => !object(row) || !text(row.symbol) || !validNumber(row.rank) || !nullableNumber(row.heat)
      || !Array.isArray(row.themes) || row.themes.some(name => !text(name)))) throw new DiscoveryReadError("热榜返回结构不完整");
  return data;
}
function checkBoards(data: BoardFundFlowPayload, kind: string, range: string) {
  if (!object(data) || data.kind !== kind || data.range !== range || typeof data.available !== "boolean" || !Array.isArray(data.rows)
    || !Array.isArray(data.degraded) || data.degraded.some(item => typeof item !== "string")
    || data.rows.some(row => !object(row) || !text(row.name) || !text(row.board_code) || row.kind !== kind
      || ![row.change_pct, row.main_net_yi, row.main_net_5d_yi, row.main_net_10d_yi, row.main_net_20d_yi].every(nullableNumber))
    || range === "intraday" && data.historical === true
    || range === "changes" && data.available && (data.source !== "eastmoney_board_changes" || data.coverage_state !== "source_sample"
      || !text(data.source_as_of) || !/(?:Z|[+-]\d{2}:?\d{2})$/.test(data.source_as_of) || !Number.isFinite(Date.parse(data.source_as_of))
      || !text(data.as_of) || data.source_as_of.slice(0, 10) !== data.as_of
      || !Number.isSafeInteger(data.source_total) || (data.source_total as number) < 1 || data.source_returned !== data.source_total
      || !Number.isSafeInteger(data.directory_total) || (data.directory_total as number) < data.rows.length
      || !Number.isSafeInteger(data.unknown_count) || (data.unknown_count as number) < 0
      || !["current_batch", "stale_batch", "closed_observation", "historical_observation", "clock_unknown"].includes(String(data.observation_state))
      || !validNumber(data.source_age_seconds) || !validNumber(data.source_age_limit_seconds) || data.source_age_limit_seconds !== 300
      || data.quality !== (data.observation_state === "current_batch" ? "medium" : "stale")
      || data.observation_state === "current_batch" && (data.source_age_seconds < 0 || data.source_age_seconds >= data.source_age_limit_seconds))
    || range === "latest_saved" && data.available && (data.historical !== true || data.coverage_state !== "local_sample" || data.quality !== "stale"
      || !text(data.as_of) || !/^\d{4}-\d{2}-\d{2}$/.test(data.as_of) || !Number.isFinite(Date.parse(`${data.as_of}T00:00:00Z`))
      || new Date(`${data.as_of}T00:00:00Z`).toISOString().slice(0, 10) !== data.as_of)) throw new DiscoveryReadError("板块返回身份或结构不一致");
  return data;
}
function checkSinaBoards(data: SinaBoardFundFlowPayload, kind: BoardFlowKind) {
  if (!object(data) || data.source !== "sina_board_funds" || data.kind !== kind || typeof data.available !== "boolean"
    || data.source_as_of !== null || !Array.isArray(data.rows) || !Array.isArray(data.degraded)
    || data.degraded.some(item=>typeof item!=="string")
    || data.rows.some(row=>!object(row) || !text(row.category) || /^BK/i.test(row.category) || !text(row.name) || row.kind!==kind
      || ![row.change_pct,row.net_inflow_yi,row.net_inflow_ratio_pct].every(nullableNumber)
      || !(row.leader_name===null || typeof row.leader_name==="string") || !(row.leader_symbol===null || typeof row.leader_symbol==="string"))
    || new Set(data.rows.map(row=>row.category)).size!==data.rows.length
    || !object(data.coverage) || !Number.isSafeInteger(data.coverage.returned) || data.coverage.returned!==data.rows.length
    || !(data.coverage.total===null || Number.isSafeInteger(data.coverage.total) && (data.coverage.total as number)>=data.rows.length)
    || typeof data.coverage.complete!=="boolean" || data.coverage.complete && data.coverage.total!==data.coverage.returned) throw new DiscoveryReadError("新浪板块返回身份或覆盖不一致");
  return data;
}
/** One active source read, local filters, and a separately scoped catalog projection. */
export function DiscoveryBrowser({search, selection, onNavigate, onTarget, onMethodTarget, methodDate}: {
  search: string; selection: DiscoverySelection;
  onNavigate: (href: string, push?: boolean) => void;
  onTarget: (scope: string, target: DiscoveryDetailTarget | null) => void;
  onMethodTarget?: (scope: string, row: StrategyRegistryItem | null) => void;
  methodDate?: string;
}) {
  const location = discoveryLocation(search);
  const {mode, kind, range, code, fundsSource, query, sort, scope} = location;
  const sinaFunds = mode === "funds" && fundsSource === "sina";
  const savedDirectory = (mode === "catalog" || mode === "funds") && range === "latest_saved";
  const freshBoardRead = useRef<string | null>(null);
  const hot = useResource(async signal => checkHot(await getThemesHot(30, {signal})), {enabled: mode === "hot", key: "hot/30", abortable: true});
  const boards = useResource(async signal => {
    const key = `${kind}/${range}`;
    const fresh = freshBoardRead.current === key;
    freshBoardRead.current = null;
    return checkBoards(await getBoardFundFlow(kind, range, {signal, fresh}), kind, range);
  }, {enabled: savedDirectory || mode === "funds" && !sinaFunds, key: `${kind}/${range}`, abortable: true});
  const sina = useResource(async signal => {
    const fresh = freshBoardRead.current === `sina/${kind}`;
    freshBoardRead.current = null;
    return checkSinaBoards(await getSinaBoardFundFlow(kind, {signal, ...(fresh ? {fresh:true} : {})}), kind);
  }, {enabled:sinaFunds,key:`sina/${kind}`,abortable:true});
  useEffect(() => {freshBoardRead.current = null;}, [kind, range, fundsSource]);
  const symbols = [...new Set(selection.entries.map(entry => entry.symbol).filter(symbol => /^\d{6}$/.test(symbol)))].sort();
  const membershipKey = JSON.stringify([selection.date, symbols]);
  const memberships = useResource(signal => getDiscoveryMemberships(symbols, selection.date, {signal}), {
    enabled: mode === "hot" && selection.loaded && !selection.readError && symbols.length > 0 && symbols.length <= 200, key: membershipKey, abortable: true,
  });
  const [draft, setDraft] = useState({query, value: query});
  const [composing, setComposing] = useState(false);
  if (draft.query !== query) setDraft({query, value: query});
  const change = (changes: Record<string, string | null>) => onNavigate(discoveryUrl(search, {disc_code: null, selection: null, ...changes}));
  const changeDirectoryRange = (next: "intraday" | "latest_saved") => onNavigate(discoveryUrl(search, {disc_range: next === "intraday" ? null : next, disc_code: null}));
  const malformedHot = hot.error instanceof DiscoveryReadError;
  const boardMismatch = boards.error instanceof DiscoveryReadError;
  const sinaMismatch = sina.error instanceof DiscoveryReadError;
  const hotRows = useMemo(() => malformedHot ? [] : hot.data?.themes ?? [], [malformedHot, hot.data]);
  const boardRows = useMemo(() => boardMismatch ? [] : boards.data?.available ? boards.data.rows : [], [boardMismatch, boards.data]);
  const sinaRows = useMemo(()=>sinaMismatch ? [] : sina.data?.available ? sina.data.rows : [],[sinaMismatch,sina.data]);
  const targetName = !code ? undefined : mode === "hot" ? hotRows.find(row => row.theme_code === code)?.theme : sinaFunds ? sinaRows.find(row=>row.category===code)?.name : mode === "catalog" || mode === "funds" ? boardRows.find(row => row.board_code === code)?.name : undefined;
  const targetKind = mode === "hot" ? "theme" : "board";
  useEffect(() => {if (mode !== "methods" && !(mode === "catalog" && !savedDirectory)) onTarget(scope, targetName ? {kind: targetKind, code, name: targetName, ...(targetKind === "board" ? {catalogKind: kind, source:sinaFunds?"sina" as const:"eastmoney" as const} : {})} : null);}, [onTarget, scope, targetKind, code, targetName, mode, savedDirectory, kind, sinaFunds]);
  const q = query.trim().toLocaleLowerCase();
  const isHot = mode === "hot";
  const source = isHot ? hot : sinaFunds ? sina : boards;
  const currentBoards = sinaFunds ? sina.data : boards.data;
  const ascending = sort === "asc";
  const filteredHot = [...hotRows].filter(row => `${row.theme} ${row.theme_code ?? ""} ${row.best?.name ?? ""} ${row.best?.symbol ?? ""}`.toLocaleLowerCase().includes(q))
    .sort((a, b) => discoveryCompare(a.concentration_pct, b.concentration_pct, ascending) || (ascending ? a.hot_count - b.hot_count : b.hot_count - a.hot_count) || (a.theme_code ?? "").localeCompare(b.theme_code ?? ""));
  const filteredBoards = [...boardRows].filter(row => `${row.name} ${row.board_code} ${row.leader_name ?? ""}`.toLocaleLowerCase().includes(q))
    .sort((a, b) => discoveryCompare(mode === "catalog" ? a.change_pct : discoveryFlowValue(a, range), mode === "catalog" ? b.change_pct : discoveryFlowValue(b, range), ascending));
  const compact = new URLSearchParams(search).get("disc_group") !== "all";
  const filteredSina = [...sinaRows].filter(row=>`${row.name} ${row.category} ${row.leader_name??""}`.toLocaleLowerCase().includes(q))
    .sort((a,b)=>discoveryCompare(a.net_inflow_yi,b.net_inflow_yi,ascending)||a.category.localeCompare(b.category));
  const rows: {key: string; hot: HotTheme | null; board: BoardFlowRow | null; sinaRow?: SinaBoardFlowRow; related?: HotTheme[]}[] = isHot ? groupHotThemes(filteredHot, compact).map(group => ({key: `theme:${group.primary.theme_code}`, hot: group.primary, board: null, related: group.related}))
    : sinaFunds ? filteredSina.map(row=>({key:`sina:${kind}:${row.category}`,hot:null,board:null,sinaRow:row}))
    : filteredBoards.map(row => ({key: `eastmoney:${kind}:${row.board_code}`, hot: null, board: row}));
  const list = useIncremental(rows, {pageSize: 24, step: 24, resetKey: JSON.stringify([mode, kind, range, fundsSource, query, sort]), active: mode !== "methods"});
  if (mode === "methods") return <DiscoveryMethods active date={methodDate ?? new URLSearchParams(search).get("date") ?? undefined} search={search} onNavigate={onNavigate} onMethodTarget={onMethodTarget}/>;
  if (mode === "catalog" && !savedDirectory) return <DiscoveryOfficialDirectory kind={kind} search={search} scope={scope} selection={selection} onNavigate={onNavigate} onTarget={onTarget}/>;
  const malformed = isHot ? malformedHot : sinaFunds ? sinaMismatch : boardMismatch;
  const pending = source.pending && source.data === undefined;
  const sourceAvailable = isHot ? !malformed : !malformed && currentBoards?.available === true;
  const membershipKnown = selection.loaded && !selection.readError && memberships.data && !memberships.pending && !memberships.error && ["ready", "partial"].includes(memberships.data.state);
  const refresh = () => {if (!isHot) freshBoardRead.current = sinaFunds?`sina/${kind}`:`${kind}/${range}`; source.refresh(); if (isHot && selection.loaded && !selection.readError && symbols.length > 0 && symbols.length <= 200) memberships.refresh();};
  const select = (next: DiscoveryDetailTarget) => onNavigate(discoveryUrl(search, {disc_code: next.code, selection: null}), true);
  return <section className="discovery-browser" aria-label={isHot ? "热门题材发现" : mode === "catalog" ? "行业概念发现" : "资金方向发现"}>
    <header className="discovery-heading"><div><h2>{isHot ? "热门题材" : mode === "catalog" ? "行业与概念" : "资金方向"}</h2>
      <p>{isHot ? "同花顺正式概念 · 按热股集中度观察" : mode === "catalog" ? "先看板块强弱，再核对成分与原名单" : sinaFunds ? "按新浪全部净流入看方向，再核对同源成员" : "按东财统计口径查看资金方向"}</p></div>
      <button className="quiet-action" type="button" disabled={source.pending} onClick={refresh}>{source.pending ? "读取中…" : "刷新分类"}</button></header>
    <div className="discovery-controls">
      {!isHot && <div className="discovery-kinds" aria-label="板块类别">{(["industry", "concept"] as const).map(value => <button key={value} type="button" aria-pressed={kind === value} onClick={() => change({disc_kind: value})}>{value === "industry" ? "行业" : "概念"}</button>)}</div>}
      {mode === "catalog" && <label>目录范围<select aria-label="目录范围" value={range} onChange={event => changeDirectoryRange(event.target.value === "latest_saved" ? "latest_saved" : "intraday")}>
        <option value="intraday">本次榜单</option><option value="latest_saved">最近存档</option></select></label>}
      {mode === "funds" && <label>来源<select aria-label="资金来源" value={fundsSource} onChange={event=>change({disc_source:event.target.value,disc_range:null,disc_q:null})}><option value="sina">新浪资金</option><option value="eastmoney">东财异动统计</option></select></label>}
      {mode === "funds" && !sinaFunds && <label>区间<select aria-label="资金区间" value={range} onChange={event => change({disc_range: event.target.value})}>
        <option value="changes">东财异动统计资金</option><option value="intraday">原资金榜（clist）</option><option value="latest_saved">最近已存单日资金</option><option value="5d">5日</option><option value="10d">10日</option><option value="20d">20日存档</option></select></label>}
      <label>排序<select aria-label="分类排序" value={ascending ? "asc" : "desc"} onChange={event => change({disc_sort: event.target.value})}>
        <option value="desc">{isHot ? "集中度优先" : mode === "catalog" ? "涨幅优先" : "净流入优先"}</option>
        <option value="asc">{isHot ? "集中度反向" : mode === "catalog" ? "跌幅优先" : "净流出优先"}</option></select></label>
      {isHot && <button className="quiet-action" type="button" aria-pressed={compact} onClick={() => onNavigate(discoveryUrl(search, {disc_group: compact ? "all" : null}))}>{compact ? "重合热股已收拢" : "显示全部概念"}</button>}
      <label className="discovery-search"><span className="sr-only">搜索分类</span><input aria-label="搜索分类" placeholder="名称、代码或领涨股" value={draft.value}
        onCompositionStart={() => setComposing(true)} onCompositionEnd={event => {setComposing(false); change({disc_q: event.currentTarget.value});}}
        onChange={event => {setDraft({query, value: event.target.value}); if (!composing) change({disc_q: event.target.value});}}/></label>
      {query && <button className="quiet-action" type="button" onClick={() => change({disc_q: null})}>清除搜索</button>}
    </div>
    <div className="discovery-basis" role="note">
      {isHot ? <><span>同花顺24小时热股 × 正式目录</span><span title={hot.data?.ts ?? undefined}>榜单时刻 {hot.data?.ts ? dateTimeTextBJ(hot.data.ts) : "未提供"}（北京）</span><span>返回 {hot.data?.stocks?.length ?? "—"} 只热股，非全市场热点全集</span>
        <span>集中度＝榜内热股／已核成分。本系统排序，不是来源官方热榜或全市场最强主线。</span>
        {compact && <span>仅将热股样本重合≥75%的条目收拢；各名称、代码和成分独立保留，不合并为新题材。</span>}
        {hot.data?.catalog_state !== "ready" && <span>目录或成员保鲜未核；缺失集中度不参与领先判断。</span>}
        {hot.data?.membership_state === "unavailable" && <span>本次归属读取失败，不能判断没有热门题材。</span>}</>
        : sinaFunds ? <><span>新浪资金 · {kind === "industry" ? "行业" : "概念"}</span><span>{sina.error ? "上次已读" : "覆盖"} {sinaRows.length}/{!sinaMismatch&&sina.data?.available?sina.data.coverage.total??"未核":"未核"} 个板块 · {!sinaMismatch&&sina.data?.available&&sina.data.coverage.complete?"本次来源范围已读全":"覆盖待核"}</span>
          <span>接收于 {sina.data?.received_at?dateTimeTextBJ(sina.data.received_at):"未提供"}（北京） · 来源时点未提供</span>
          <span>全部净流入：元÷1亿。成员主力为单笔≥100万元，定义不同于东财，不混算；概念可重叠，金额不跨板块相加。</span></>
        : <><span>{range === "changes" ? "东财异动统计资金" : "东财板块口径"} · {kind === "industry" ? "行业" : "概念"}</span><span>{savedDirectory ? `原榜单日期 ${boards.data?.as_of || "未提供"}` : range === "20d" ? `本地存档截至 ${boards.data?.as_of || "未提供"}` : range === "changes" ? `来源批次 ${boards.data?.source_as_of ? dateTimeTextBJ(boards.data.source_as_of) : "未提供"}（北京）` : "源时点未提供"}</span>
          {savedDirectory ? <><span>Top50净流入存档样本，非全目录</span>
            <span title={boards.data?.stored_at ?? undefined}>存档保存于 {boards.data?.stored_at && /(?:Z|[+-]\d{2}:?\d{2})$/i.test(boards.data.stored_at) && Number.isFinite(Date.parse(boards.data.stored_at)) ? `${dateTimeTextBJ(boards.data.stored_at)}（北京）` : "未提供"} · 来源时刻未核</span>
            <span>历史参考，不表示即时源已恢复。下钻另读当前资金排行样本，不是原日归属。</span></>
            : boards.data?.received_at && <span title={boards.data.received_at}>源读取完成 {dateTimeTextBJ(boards.data.received_at)}（北京）</span>}
          <span>已返回 {boardRows.length} 个板块{range === "20d" || savedDirectory ? " · 有限本地样本" : " · 全市场覆盖未核"}</span>
          {range === "changes" && <><span>原批 {boards.data?.source_returned ?? "—"}/{boards.data?.source_total ?? "—"} · 正式目录匹配 {boardRows.length}/{boards.data?.directory_total ?? "—"} · 未核归属 {boards.data?.unknown_count ?? "—"}</span>
            {boards.data?.observation_state && <span role="status">{boards.data.observation_state === "closed_observation" ? "收盘批次观察 · 非即时" : boards.data.observation_state === "historical_observation" ? "历史批次观察 · 非即时" : boards.data.observation_state === "stale_batch" ? "来源批次已超5分钟 · 原值仅供参考" : boards.data.observation_state === "clock_unknown" ? "源批钟异常 · 即时状态未知" : "5分钟内来源批次 · 逐行时点未核"}</span>}
            <span>官网主力净流入：万元÷10000→亿元。独立统计口径；概念可重叠，净额不相加，不作策略依据。</span></>}</>}
      {!isHot&&!sinaFunds&&<span>下钻为东财个股资金排行Top20，不是成分全集。</span>}
      <span>分类浏览不改变原入选，不构成买卖建议。</span>
    </div>
    {selection.readError && <div role="alert" className="discovery-error">
      <span>原名单读取失败，归属对照未完成。</span>
      {selection.retry && <button type="button" className="quiet-action" disabled={selection.pending} onClick={selection.retry}>{selection.pending ? "正在重试原名单…" : "重试原名单"}</button>}
    </div>}
    {(source.error != null || malformed || (!isHot && currentBoards?.available === false)) && <div className="discovery-error" role="alert">
      {malformed ? "分类返回身份或结构不一致，已停止展示。" : source.error ? `分类读取失败。${source.data ? "以下为上次成功结果，当前时效待核。" : "请重试，不能据此判断没有分类。"}` : `板块资料暂不可用：${currentBoards?.reason || "原因未提供"}`}
      <button type="button" className="quiet-action" disabled={source.pending} onClick={refresh}>重试分类</button>
      {!sinaFunds && (mode === "catalog" || mode === "funds") && range === "intraday" && <button type="button" className="quiet-action" onClick={() => changeDirectoryRange("latest_saved")}>查看最近存档</button>}</div>}
    {!!currentBoards?.degraded?.length && !isHot && <p className="discovery-warning">{currentBoards.degraded.join("；")}</p>}
    {pending && <div role="status" className="discovery-skeleton" aria-busy="true">正在读取分类…</div>}
    {!pending && sourceAvailable && source.data && rows.length === 0 && <p className="discovery-empty">{query ? "没有匹配分类。可清除搜索查看已返回范围。" : isHot ? "热榜暂未映射到可确认题材；不是没有市场热点。" : "本次范围没有可展示板块；缺数不按零处理。"}</p>}
    {code && !pending && source.data && !targetName && <p role="status" className="discovery-warning">该条目不在当前返回范围，未用其他分类替代。请重新选择。</p>}
    <ul className="discovery-results" aria-label="分类结果">
      {list.shown.map(({key, hot: row, board, sinaRow, related}, index) => {
        const name = row?.theme ?? sinaRow?.name ?? board!.name;
        const rowCode = row?.theme_code ?? sinaRow?.category ?? board?.board_code ?? null;
        const matched = rowCode && membershipKnown ? memberships.data!.items.filter(item => item.state === "matched" && item.themes.some(theme => theme.theme_code === rowCode)).length : null;
        const net = sinaRow ? sinaRow.net_inflow_yi : board ? discoveryFlowValue(board, range) : null;
        const changePct = sinaRow?.change_pct ?? board?.change_pct ?? null;
        const leaderSymbol = sinaRow?.leader_symbol ?? board?.leader_symbol;
        const leaderName = sinaRow?.leader_name ?? board?.leader_name;
        return <li key={key} className="discovery-result" data-selected={!!rowCode && code === rowCode || !!related?.some(item => item.theme_code === code)}>
          <button className="discovery-result-open" type="button" disabled={!rowCode} data-discovery-code={rowCode ?? undefined} aria-label={`查看${name}${!row&&!sinaFunds ? "资金排行" : "成分"}`} aria-pressed={!!rowCode && code === rowCode}
            onClick={() => rowCode && select({kind: row ? "theme" : "board", code: rowCode, name})}>
            <span className="discovery-order">{index + 1}</span><span className="discovery-name"><strong>{name}</strong><small>{rowCode || "代码未确认"}</small></span>
            <span className={`discovery-measure ${!row ? pctColor(changePct) : ""}`}>{row ? validNumber(row.concentration_pct) ? `${row.concentration_pct.toLocaleString("zh-CN", {maximumFractionDigits: 2})}%` : "未核" : pctText(changePct)}<small>{row ? "热股集中度" : savedDirectory ? "原日涨跌幅" : "板块涨跌幅"}</small></span>
            <span className={`discovery-measure ${!row && net != null ? net >= 0 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down" : ""}`}>{row ? `${row.hot_count}只` : signed(net)}<small>{row ? "榜内热股" : sinaRow ? "全部净流入" : savedDirectory ? "原当日净额" : range === "changes" ? "异动统计主力净额" : range === "intraday" ? "主力净额" : `${range.replace("d", "日")}主力净额`}</small></span>
            <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m9 5 7 7-7 7"/></svg>
          </button>
          <div className="discovery-result-context">
            {row && <span title={row.member_as_of ?? undefined}>同花顺正式概念 · {row.member_count == null ? "成分总数未核" : `已核成分${row.member_count}只`}</span>}
            {row?.best && /^\d{6}$/.test(row.best.symbol) ? <span>榜内靠前 <StockLink symbol={row.best.symbol}>{row.best.name || row.best.symbol}</StockLink> · 第{row.best.rank}名</span>
              : leaderSymbol && /^\d{6}$/.test(leaderSymbol) ? <span>源列领涨 <StockLink symbol={leaderSymbol}>{leaderName || leaderSymbol}</StockLink></span>
                : <span>{row ? "榜内靠前成员未确认" : leaderName ? `源列领涨 ${leaderName} · 证券代码未确认` : "领涨成员未提供"}</span>}
            {row && <span>{selection.readError ? "原名单读取失败" : !selection.loaded ? "原名单待核" : matched !== null ? `已核匹配 ${matched} 只${memberships.data!.unknown_count > 0 ? ` · ${memberships.data!.unknown_count}只归属未核` : ""}` : selection.entries.length === 0 ? "本组原名单已确认空" : symbols.length > 200 ? "对照超过200只，归属未核" : "原名单归属未核"}</span>}
          </div>
          {!!related?.length && <div className="discovery-result-context" aria-label={`${name}重合热股归属`}><span>热股样本重合</span>{related.map(item => <button type="button" className="quiet-action" key={item.theme_code} data-discovery-code={item.theme_code ?? undefined}
            aria-label={`查看${item.theme}成分`} aria-pressed={code === item.theme_code} onClick={() => item.theme_code && select({kind:"theme",code:item.theme_code,name:item.theme})} title={`${item.theme_code} · ${item.hot_count}只热股 · 名称与成分独立保留`}>{item.theme} <span className="font-mono text-[10px]">{item.theme_code}</span></button>)}</div>}
        </li>;
      })}
    </ul>
    <IncrementalSentinel sentinelRef={list.sentinelRef} visible={Math.min(list.visible, rows.length)} total={rows.length} unit="个分类" onLoadMore={list.loadMore}/>
    {isHot && selection.entries.length > 0 && <p className="discovery-footnote">对照名单 {selection.date || "日期未核"} · {membershipKnown ? `${memberships.data!.unknown_count}只归属未核 · 对照读取于 ${dateTimeTextBJ(memberships.data!.observed_at)}（北京）` : "本次归属对照未核，可刷新重试"}。当前目录对照不是当时归属或入选依据。</p>}
    {isHot && hot.data?.stocks?.some(stock => stock.themes.length === 0) && <details className="discovery-unmapped"><summary>热榜中归属未核的股票</summary>
      <ul>{hot.data.stocks.filter(stock => stock.themes.length === 0).map(stock => <li key={stock.symbol}>第{stock.rank}名 · {/^\d{6}$/.test(stock.symbol) ? <StockLink symbol={stock.symbol}>{stock.name || stock.symbol}</StockLink> : "证券身份未核"}</li>)}</ul></details>}
  </section>;
}
