"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { useResource } from "@/hooks/use-resource";
import { useIncremental } from "@/hooks/use-incremental";
import { IncrementalSentinel } from "@/components/ui/incremental-sentinel";
import { StockLink } from "@/components/stock-link";
import { DiscoveryMethods } from "./discovery-methods";
import type { DiscoveryDetailTarget } from "./discovery-detail";
import { getBoardFundFlow, getDiscoveryMemberships, getThemesHot, type BoardFlowKind, type BoardFlowRange, type BoardFlowRow, type HotTheme, type ThemesHotPayload, type BoardFundFlowPayload } from "@/lib/api";
import { dateTimeTextBJ, pctColor, pctText } from "@/lib/format";

const MODES = [["selected", "入选名单"], ["hot", "热门题材"], ["catalog", "行业概念"], ["funds", "资金方向"], ["methods", "战法分类"]] as const;
export type DiscoveryMode = typeof MODES[number][0];
export type DiscoverySelection = {date: string | null; loaded: boolean; entries: readonly {symbol: string; name?: string | null; open: () => void}[]};

export function discoveryLocation(search: string) {
  const params = new URLSearchParams(search);
  const raw = params.get("browse");
  const mode = MODES.find(([key]) => key === raw)?.[0] ?? "selected";
  const kind: BoardFlowKind = params.get("disc_kind") === "industry" ? "industry" : params.get("disc_kind") === "concept" ? "concept" : mode === "catalog" ? "industry" : "concept";
  const range: BoardFlowRange = mode === "catalog" ? "intraday" : (["5d", "10d", "20d"].includes(params.get("disc_range") ?? "") ? params.get("disc_range") as BoardFlowRange : "intraday");
  const code = params.get("disc_code") ?? "";
  return {mode, kind, range, code, query: params.get("disc_q") ?? "", sort: params.get("disc_sort") ?? "desc",
    scope: JSON.stringify([mode, kind, range, code])};
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
      href={discoveryUrl(search, {browse: key === "selected" ? null : key, disc_code: null, disc_q: null, disc_sort: null, disc_kind: null, disc_range: null, selection: null})}>{name}</Link>)}
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
    || data.themes.some(row => !object(row) || !text(row.theme) || !(row.theme_code == null || text(row.theme_code))
      || !nullableNumber(row.heat) || !Number.isSafeInteger(row.hot_count) || (row.hot_count as number) < 0
      || !(row.best == null || object(row.best) && text(row.best.symbol) && validNumber(row.best.rank)))
    || data.stocks.some(row => !object(row) || !text(row.symbol) || !validNumber(row.rank) || !nullableNumber(row.heat)
      || !Array.isArray(row.themes) || row.themes.some(name => !text(name)))) throw new DiscoveryReadError("热榜返回结构不完整");
  return data;
}
function checkBoards(data: BoardFundFlowPayload, kind: string, range: string) {
  if (!object(data) || data.kind !== kind || data.range !== range || typeof data.available !== "boolean" || !Array.isArray(data.rows)
    || !Array.isArray(data.degraded) || data.degraded.some(item => typeof item !== "string")
    || data.rows.some(row => !object(row) || !text(row.name) || !text(row.board_code) || row.kind !== kind
      || ![row.change_pct, row.main_net_yi, row.main_net_5d_yi, row.main_net_10d_yi, row.main_net_20d_yi].every(nullableNumber))) throw new DiscoveryReadError("板块返回身份或结构不一致");
  return data;
}
function heatReading(row: HotTheme, stocks: ThemesHotPayload["stocks"]) {
  const members = stocks.filter(stock => stock.themes.includes(row.theme));
  const known = members.filter(stock => validNumber(stock.heat)).length;
  return {value: members.length === row.hot_count && known === row.hot_count && known > 0 && validNumber(row.heat) ? row.heat : null,
    known, total: row.hot_count};
}

/** One active source read, local filters, and a separately scoped catalog projection. */
export function DiscoveryBrowser({search, selection, onNavigate, onTarget}: {
  search: string; selection: DiscoverySelection;
  onNavigate: (href: string, push?: boolean) => void;
  onTarget: (scope: string, target: DiscoveryDetailTarget | null) => void;
}) {
  const location = discoveryLocation(search);
  const {mode, kind, range, code, query, sort, scope} = location;
  const hot = useResource(async signal => checkHot(await getThemesHot(30, {signal})), {enabled: mode === "hot", key: "hot/30", abortable: true});
  const boards = useResource(async signal => checkBoards(await getBoardFundFlow(kind, range, {signal}), kind, range), {enabled: mode === "catalog" || mode === "funds", key: `${kind}/${range}`, abortable: true});
  const symbols = [...new Set(selection.entries.map(entry => entry.symbol).filter(symbol => /^\d{6}$/.test(symbol)))].sort();
  const membershipKey = JSON.stringify([selection.date, symbols]);
  const memberships = useResource(signal => getDiscoveryMemberships(symbols, selection.date, {signal}), {
    enabled: mode === "hot" && selection.loaded && symbols.length > 0 && symbols.length <= 200, key: membershipKey, abortable: true,
  });
  const [draft, setDraft] = useState({query, value: query});
  const [composing, setComposing] = useState(false);
  if (draft.query !== query) setDraft({query, value: query});
  const change = (changes: Record<string, string | null>) => onNavigate(discoveryUrl(search, {disc_code: null, selection: null, ...changes}));
  const malformedHot = hot.error instanceof DiscoveryReadError;
  const boardMismatch = boards.error instanceof DiscoveryReadError;
  const hotRows = useMemo(() => malformedHot ? [] : hot.data?.themes ?? [], [malformedHot, hot.data]);
  const boardRows = useMemo(() => boardMismatch ? [] : boards.data?.available ? boards.data.rows : [], [boardMismatch, boards.data]);
  const targetName = !code ? undefined : mode === "hot" ? hotRows.find(row => row.theme_code === code)?.theme : mode === "catalog" || mode === "funds" ? boardRows.find(row => row.board_code === code)?.name : undefined;
  const targetKind = mode === "hot" ? "theme" : "board";
  useEffect(() => {onTarget(scope, targetName ? {kind: targetKind, code, name: targetName} : null);}, [onTarget, scope, targetKind, code, targetName]);
  const q = query.trim().toLocaleLowerCase();
  const isHot = mode === "hot";
  const source = isHot ? hot : boards;
  const ascending = sort === "asc";
  const filteredHot = [...hotRows].filter(row => `${row.theme} ${row.theme_code ?? ""} ${row.best?.name ?? ""} ${row.best?.symbol ?? ""}`.toLocaleLowerCase().includes(q))
    .sort((a, b) => discoveryCompare(heatReading(a, hot.data?.stocks ?? []).value, heatReading(b, hot.data?.stocks ?? []).value, ascending));
  const filteredBoards = [...boardRows].filter(row => `${row.name} ${row.board_code} ${row.leader_name ?? ""}`.toLocaleLowerCase().includes(q))
    .sort((a, b) => discoveryCompare(mode === "catalog" ? a.change_pct : discoveryFlowValue(a, range), mode === "catalog" ? b.change_pct : discoveryFlowValue(b, range), ascending));
  const rows: {key: string; hot: HotTheme | null; board: BoardFlowRow | null}[] = isHot ? filteredHot.map(row => ({key: `theme:${row.theme_code ?? row.theme}`, hot: row, board: null}))
    : filteredBoards.map(row => ({key: `board:${row.board_code}`, hot: null, board: row}));
  const list = useIncremental(rows, {pageSize: 24, step: 24, resetKey: JSON.stringify([mode, kind, range, query, sort]), active: mode !== "methods"});
  if (mode === "methods") return <DiscoveryMethods active date={new URLSearchParams(search).get("date") ?? selection.date ?? undefined} search={search}/>;
  const malformed = isHot ? malformedHot : boardMismatch;
  const pending = source.pending && source.data === undefined;
  const sourceAvailable = isHot ? !malformed : !malformed && boards.data?.available === true;
  const membershipKnown = selection.loaded && memberships.data && !memberships.pending && !memberships.error && ["ready", "partial"].includes(memberships.data.state);
  const refresh = () => {source.refresh(); if (isHot && selection.loaded && symbols.length > 0 && symbols.length <= 200) memberships.refresh();};
  const select = (next: DiscoveryDetailTarget) => onNavigate(discoveryUrl(search, {disc_code: next.code, selection: null}), true);
  return <section className="discovery-browser" aria-label={isHot ? "热门题材发现" : mode === "catalog" ? "行业概念发现" : "资金方向发现"}>
    <header className="discovery-heading"><div><h2>{isHot ? "热门题材" : mode === "catalog" ? "行业与概念" : "资金方向"}</h2>
      <p>{isHot ? "热股共同归属，包含题材与通用标签" : mode === "catalog" ? "先看板块强弱，再核对成分与原名单" : "按真实主力净额看流入与流出"}</p></div>
      <button className="quiet-action" type="button" disabled={source.pending} onClick={refresh}>{source.pending ? "读取中…" : "刷新分类"}</button></header>
    <div className="discovery-controls">
      {!isHot && <div className="discovery-kinds" aria-label="板块类别">{(["industry", "concept"] as const).map(value => <button key={value} type="button" aria-pressed={kind === value} onClick={() => change({disc_kind: value})}>{value === "industry" ? "行业" : "概念"}</button>)}</div>}
      {mode === "funds" && <label>区间<select aria-label="资金区间" value={range} onChange={event => change({disc_range: event.target.value})}>
        <option value="intraday">本次榜单</option><option value="5d">5日</option><option value="10d">10日</option><option value="20d">20日存档</option></select></label>}
      <label>排序<select aria-label="分类排序" value={ascending ? "asc" : "desc"} onChange={event => change({disc_sort: event.target.value})}>
        <option value="desc">{isHot ? "人气优先" : mode === "catalog" ? "涨幅优先" : "净流入优先"}</option>
        <option value="asc">{isHot ? "人气反向" : mode === "catalog" ? "跌幅优先" : "净流出优先"}</option></select></label>
      <label className="discovery-search"><span className="sr-only">搜索分类</span><input aria-label="搜索分类" placeholder="名称、代码或领涨股" value={draft.value}
        onCompositionStart={() => setComposing(true)} onCompositionEnd={event => {setComposing(false); change({disc_q: event.currentTarget.value});}}
        onChange={event => {setDraft({query, value: event.target.value}); if (!composing) change({disc_q: event.target.value});}}/></label>
      {query && <button className="quiet-action" type="button" onClick={() => change({disc_q: null})}>清除搜索</button>}
    </div>
    <div className="discovery-basis" role="note">
      {isHot ? <><span>同花顺24小时热股 × 当前目录</span><span title={hot.data?.ts ?? undefined}>榜单时刻 {hot.data?.ts ? dateTimeTextBJ(hot.data.ts) : "未提供"}（北京）</span><span>返回 {hot.data?.stocks?.length ?? "—"} 只热股，非全市场热点全集</span></>
        : <><span>东财板块口径 · {kind === "industry" ? "行业" : "概念"}</span><span>{range === "20d" ? `本地存档截至 ${boards.data?.as_of || "未提供"}` : "源时点未提供"}</span>
          {boards.data?.received_at && <span title={boards.data.received_at}>源读取完成 {dateTimeTextBJ(boards.data.received_at)}（北京）</span>}
          <span>已返回 {boardRows.length} 个板块{range === "20d" ? " · 有限本地样本" : " · 全市场覆盖未核"}</span></>}
      <span>分类浏览不改变原入选，不构成买卖建议。</span>
    </div>
    {(source.error != null || malformed || (!isHot && boards.data?.available === false)) && <div className="discovery-error" role="alert">
      {malformed ? "分类返回身份或结构不一致，已停止展示。" : source.error ? `分类读取失败。${source.data ? "以下为上次成功结果，当前时效待核。" : "请重试，不能据此判断没有分类。"}` : `资金资料暂不可用：${boards.data?.reason || "原因未提供"}`}
      <button type="button" className="quiet-action" disabled={source.pending} onClick={refresh}>重试分类</button></div>}
    {!!boards.data?.degraded?.length && !isHot && <p className="discovery-warning">{boards.data.degraded.join("；")}</p>}
    {pending && <div role="status" className="discovery-skeleton" aria-busy="true">正在读取分类…</div>}
    {!pending && sourceAvailable && source.data && rows.length === 0 && <p className="discovery-empty">{query ? "没有匹配分类。可清除搜索查看已返回范围。" : isHot ? "热榜暂未映射到可确认题材；不是没有市场热点。" : "本次范围没有可展示板块；缺数不按零处理。"}</p>}
    {code && !pending && source.data && !targetName && <p role="status" className="discovery-warning">该条目不在当前返回范围，未用其他分类替代。请重新选择。</p>}
    <ul className="discovery-results" aria-label="分类结果">
      {list.shown.map(({key, hot: row, board}, index) => {
        const name = row?.theme ?? board!.name;
        const rowCode = row?.theme_code ?? board?.board_code ?? null;
        const matched = rowCode && membershipKnown ? memberships.data!.items.filter(item => item.state === "matched" && item.themes.some(theme => theme.theme_code === rowCode)).length : null;
        const net = board ? discoveryFlowValue(board, range) : null;
        const heat = row ? heatReading(row, hot.data?.stocks ?? []) : null;
        return <li key={key} className="discovery-result" data-selected={!!rowCode && code === rowCode}>
          <button className="discovery-result-open" type="button" disabled={!rowCode} data-discovery-code={rowCode ?? undefined} aria-label={`查看${name}成分`} aria-pressed={!!rowCode && code === rowCode}
            onClick={() => rowCode && select({kind: row ? "theme" : "board", code: rowCode, name})}>
            <span className="discovery-order">{index + 1}</span><span className="discovery-name"><strong>{name}</strong><small>{rowCode || "代码未确认"}</small></span>
            <span title={heat?.value != null ? heat.value.toLocaleString("zh-CN") : undefined} className={`discovery-measure ${board ? pctColor(board.change_pct) : ""}`}>{row ? (heat?.value != null ? heat.value.toLocaleString("zh-CN", {notation: "compact", maximumFractionDigits: 1}) : "未齐") : pctText(board!.change_pct)}<small>{row ? heat?.value != null ? "榜内人气合计" : `${heat?.known ?? 0}/${heat?.total ?? 0}股提供人气` : "板块涨跌幅"}</small></span>
            <span className={`discovery-measure ${board && net != null ? net >= 0 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down" : ""}`}>{row ? `${row.hot_count}只` : signed(net)}<small>{row ? "榜内热股" : range === "intraday" ? "主力净额" : `${range.replace("d", "日")}主力净额`}</small></span>
            <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m9 5 7 7-7 7"/></svg>
          </button>
          <div className="discovery-result-context">
            {row?.best && /^\d{6}$/.test(row.best.symbol) ? <span>榜内靠前 <StockLink symbol={row.best.symbol}>{row.best.name || row.best.symbol}</StockLink> · 第{row.best.rank}名</span>
              : board?.leader_symbol && /^\d{6}$/.test(board.leader_symbol) ? <span>源列领涨 <StockLink symbol={board.leader_symbol}>{board.leader_name || board.leader_symbol}</StockLink></span>
                : <span>{row ? "榜内靠前成员未确认" : board?.leader_name ? `源列领涨 ${board.leader_name} · 证券代码未确认` : "领涨成员未提供"}</span>}
            {row && <span>{!selection.loaded ? "原名单待核" : matched !== null ? `已核匹配 ${matched} 只${memberships.data!.unknown_count > 0 ? ` · ${memberships.data!.unknown_count}只归属未核` : ""}` : selection.entries.length === 0 ? "本组原名单已确认空" : symbols.length > 200 ? "对照超过200只，归属未核" : "原名单归属未核"}</span>}
          </div>
        </li>;
      })}
    </ul>
    <IncrementalSentinel sentinelRef={list.sentinelRef} visible={Math.min(list.visible, rows.length)} total={rows.length} unit="个分类" onLoadMore={list.loadMore}/>
    {isHot && selection.entries.length > 0 && <p className="discovery-footnote">对照名单 {selection.date || "日期未核"} · {membershipKnown ? `${memberships.data!.unknown_count}只归属未核 · 对照读取于 ${dateTimeTextBJ(memberships.data!.observed_at)}（北京）` : "本次归属对照未核，可刷新重试"}。当前目录对照不是当时归属或入选依据。</p>}
    {isHot && hot.data?.stocks?.some(stock => stock.themes.length === 0) && <details className="discovery-unmapped"><summary>热榜中归属未核的股票</summary>
      <ul>{hot.data.stocks.filter(stock => stock.themes.length === 0).map(stock => <li key={stock.symbol}>第{stock.rank}名 · {/^\d{6}$/.test(stock.symbol) ? <StockLink symbol={stock.symbol}>{stock.name || stock.symbol}</StockLink> : "证券身份未核"}</li>)}</ul></details>}
  </section>;
}
