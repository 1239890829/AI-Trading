"use client";

import { HugeiconsIcon } from "@hugeicons/react";
import Search01Icon from "@hugeicons/core-free-icons/Search01Icon";
import Cancel01Icon from "@hugeicons/core-free-icons/Cancel01Icon";
import { CloseButton } from "@/components/ui/icon-button";

import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useExitPresence } from "@/hooks/use-exit-presence";
import { ApiError, addToWatchlist, searchSymbols } from "@/lib/api";
import {indexDetailSymbol} from "@/lib/api/client";
import { notifyWatchlistChanged } from "@/lib/watchlist-sync";
import { useSymbolDetail } from "@/components/detail/symbol-detail-context";
import type { SymbolSearchItem } from "@/types/market";
import {useRouter} from "next/navigation";
import {getAssetRecords, getFundCatalog, saveAssetRecord, searchAssets, type AssetInstrument, type FundCatalog} from "@/lib/api/assets";
import {ASSET_LABELS, assetWorkbenchUrl, fundIdentityMatches, notifyAssetRecordsChanged, validFundSymbol, type WorkbenchAsset} from "@/lib/asset-identity";
import {searchUsAssets, validUsSourceSymbol} from "@/lib/api/us-assets";

/** 输入停稳后的防抖时长（ms）。Enter 可跳过防抖立即搜索。 */
const DEBOUNCE_MS = 250;
/** 触发搜索的最小关键词长度（少于 2 字符不发请求）。 */
const MIN_QUERY_LEN = 2;
const HISTORY_KEY = "ashare-security-search-history-v1";
const HISTORY_LIMIT = 12;
type SelectedHistory = {query: string; symbol: string; name: string; market: string; instrument?: AssetInstrument; source_candidate?: true; us_candidate?: true};
type SearchHistory = SelectedHistory | {query: string};
type SearchItem = SymbolSearchItem & {instrument?: AssetInstrument; source_candidate?: true; us_candidate?: true};
function selectedHistory(entry: SearchHistory): entry is SelectedHistory {return "symbol" in entry;}
function historyIdentity(entry: SearchHistory) {return selectedHistory(entry) ? (entry.us_candidate ? `us:${entry.symbol}` : entry.instrument || entry.source_candidate ? `fund:${entry.symbol}` : `${entry.market}:${entry.symbol}`) : `query:${entry.query}`;}

/** Market-qualified index segments use the existing detail contract; names never supply identity. */
function historySymbol(symbol: string, market: string): string | null {
  const match = /^(?:(sh|sz|bj))?(\d{6})$/i.exec(symbol);
  if (!match || match[1] && match[1].toUpperCase() !== market) return null;
  const code = match[2];
  const index = market === "SH" && /^(000|880|950)/.test(code)
    || market === "SZ" && code.startsWith("399") || market === "BJ" && code.startsWith("899");
  if (index) return market === "BJ" ? `bj${code}` : indexDetailSymbol(code, market);
  const knownMarket = /^(6|5|900)/.test(code) ? "SH" : /^[0123]/.test(code) ? "SZ" : /^(4|8|920)/.test(code) ? "BJ" : null;
  return knownMarket === market ? code : null;
}
function validHistory(value: unknown, asset: WorkbenchAsset = "cn_equity"): SearchHistory[] {
  if (!Array.isArray(value)) return [];
  const result: SearchHistory[] = [];
  for (const raw of value.slice(0, HISTORY_LIMIT * 4)) {
    const entry = typeof raw === "string" ? {query: raw} : raw;
    if (!entry || typeof entry !== "object" || typeof entry.query !== "string" || entry.query.trim().length < MIN_QUERY_LEN || entry.query.length > 64) continue;
    const query = entry.query.trim();
    let record: SearchHistory;
    if (asset !== "cn_equity") {
      const instrument = entry.instrument;
      if (asset === "us_equity") {
        if (entry.us_candidate !== true || instrument || entry.market !== "US" || !validUsSourceSymbol(entry.symbol)
          || typeof entry.name !== "string" || entry.name.length > 160) continue;
        record = {query, symbol: entry.symbol, name: entry.name, market: "US", us_candidate: true};
      } else if (entry.source_candidate === true && !instrument && validFundSymbol(entry.symbol) && entry.market === "OF"
        && typeof entry.name === "string" && entry.name.length <= 160) {
        record = {query, symbol: entry.symbol, name: entry.name, market: "OF", source_candidate: true};
      } else {
        if (!instrument || typeof instrument !== "object" || !fundIdentityMatches(instrument)
          || typeof instrument.name !== "string" || instrument.name.length > 160 || entry.symbol !== instrument.source_symbol) continue;
        record = {query, symbol: instrument.source_symbol, name: typeof entry.name === "string" && entry.name.length <= 160 ? entry.name : instrument.name, market: instrument.market, instrument};
      }
    } else {
    // Never migrate a non-A-share identity into the legacy six-digit history.
    if (entry.instrument || entry.source_candidate || entry.us_candidate) continue;
    if (!("symbol" in entry) && !("market" in entry)) record = {query};
    else {
      if (typeof entry.symbol !== "string" || !["SH", "SZ", "BJ"].includes(entry.market)
        || typeof entry.name !== "string" || entry.name.length > 80) continue;
      const symbol = historySymbol(entry.symbol, entry.market);
      record = symbol ? {query, symbol, name: entry.name, market: entry.market} : {query};
    }
    }
    if (result.some(row => historyIdentity(row) === historyIdentity(record))) continue;
    result.push(record);
    if (result.length === HISTORY_LIMIT) break;
  }
  return result;
}
// 中文 IME：组合（拼音）期间不调度搜索——拼音片段（"xing"/"xingw"…）是垃圾查询，
// 搜不到结果还消耗上游配额；选字 Enter 属于 IME 操作不是搜索指令。
// compositionEnd 后用最终上屏词立即搜索（跳过防抖）。

export function SearchBox({collapsible = false, asset = "cn_equity"}: {collapsible?: boolean; asset?: WorkbenchAsset}) {
  const router = useRouter();
  const historyKey = asset === "cn_equity" ? HISTORY_KEY : `ashare-asset-search-history-v1:${asset}`;
  const [expanded,setExpanded] = useState(!collapsible);
  const triggerRef = useRef<HTMLButtonElement>(null);
  // 选中搜索结果 → **就地弹窗**看详情（2026-09-15 详情弹窗化）。
  // 工作台页内则由 Provider 回落为「切换右栏」（见 symbol-detail-modal 的 open）。
  const { open: openSymbolDetail } = useSymbolDetail();
  const [q, setQ] = useState("");
  const [resultQuery, setResultQuery] = useState("");
  const [adding, setAdding] = useState<string | null>(null);
  const [addNote, setAddNote] = useState<string | null>(null);
  const addLock = useRef(false);
  const [items, setItems] = useState<SearchItem[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 是否已对当前关键词发起过搜索：空结果提示只在「确实搜过且没搜到」时出现。 */
  const [searched, setSearched] = useState(false);
  const [history, setHistory] = useState<SearchHistory[]>([]);
  useEffect(() => {
    let alive = true;
    queueMicrotask(() => {
      try {
        const saved = localStorage.getItem(historyKey);
        if (alive) setHistory(saved && saved.length <= 16_384 ? validHistory(JSON.parse(saved), asset) : []);
      } catch {}
    });
    return () => {alive = false;};
  }, [historyKey, asset]);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelId = useId();
  useLayoutEffect(() => {if(expanded && collapsible)inputRef.current?.focus();},[expanded,collapsible]);
  /**
   * 竞态守卫：每次发起请求自增，响应返回时 seq 不匹配即丢弃。
   * 旧实现只有 clearTimeout（只能取消尚未发出的请求），已发出的慢响应会
   * 后发先至覆盖新结果——表现就是「输入后搜索无响应/结果不对」。
   */
  const seqRef = useRef(0);
  // Reuse a bounded source list while its evidence is within the backend cache lifetime.
  const fundCatalogRead = useRef<{promise: Promise<FundCatalog>; expires: number} | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 中文 IME 组合中（拼音未上屏）：期间不调度搜索、Enter/Escape 归 IME 所有。 */
  const composingRef = useRef(false);
  const [composing, setComposing] = useState(false);

  const runSearch = useCallback((kw: string) => {
    const seq = ++seqRef.current;
    setItems([]);
    setResultQuery(kw);
    setLoading(true);
    setError(null);
    setOpen(true); // 请求飞行中立即打开面板显示「搜索中…」，不等响应
    const request: Promise<SearchItem[]> = asset === "cn_equity" ? searchSymbols(kw) : (async () => {
      if (asset === "us_equity") {
        const result = await searchUsAssets(kw);
        if (result.asset_type !== "us_equity" || result.query !== kw) throw new ApiError(502, "美股搜索返回范围不一致，请重试", "asset_identity_mismatch");
        if (result.state === "blocked") {
          if (seq === seqRef.current) setError(result.reason ?? "美股来源未启用");
          return [];
        }
        if (seq === seqRef.current && result.state === "partial") setAddNote(result.reason ?? "来源只返回部分美股结果");
        return result.items.filter(item => item.market === "US" && item.source === "tencent"
          && (item.source_kind === "GP" && item.security_type === "stock" || item.source_kind === "GP-ETF" && item.security_type === "etf")
          && validUsSourceSymbol(item.source_symbol)
          && item.symbol === item.source_symbol.replace(/\.(OQ|N|AM)$/, ""))
          .map(item => ({symbol: item.source_symbol, name: item.name, market: "US", source: "tencent", is_realtime: false, us_candidate: true as const}));
      }
      if (asset === "fund" && (!fundCatalogRead.current || fundCatalogRead.current.expires <= Date.now())) {
        const entry = {promise: getFundCatalog("all"), expires: Infinity};
        fundCatalogRead.current = entry;
        void entry.promise.then(catalog => {
          const ttl = catalog.state === "unavailable" || !catalog.items.length ? 30_000 : 300_000;
          const received = (catalog.evidence ?? []).map(item => Date.parse(item.received_at)).filter(Number.isFinite);
          entry.expires = Math.min(Date.now() + ttl, ...received.map(time => time + ttl));
        }, () => {if (fundCatalogRead.current === entry) fundCatalogRead.current = null;});
      }
      let publishedVerified = false;
      let invalidAssetResponse = false;
      const publishEarly = (rows: SearchItem[]) => {
        if (seq !== seqRef.current || !rows.length) return;
        setItems(rows); setSearched(true); setLoading(false);
      };
      const assetRead = searchAssets(kw, asset).then(result => {
        if (result.asset_type !== asset || result.query !== kw) {invalidAssetResponse = true; throw new ApiError(502, "搜索返回的资产或关键词不匹配，请重新搜索", "asset_identity_mismatch");}
        if (seq === seqRef.current && result.state === "partial") setAddNote(result.reason ?? "来源只返回部分结果，请核对覆盖范围");
        if (result.state !== "blocked" && result.items.length) {
          publishedVerified = true;
          publishEarly(result.items.map(instrument => ({symbol: instrument.source_symbol, name: instrument.name, market: instrument.market, source: instrument.source, is_realtime: false, instrument})));
        }
        return result;
      });
      const sourceRead = asset === "fund" ? fundCatalogRead.current!.promise.then(catalog => {
        if (!publishedVerified && catalog.tab === "meiguo" && catalog.purchase === "all" && catalog.source === "ths_official" && catalog.state !== "unavailable") {
          publishEarly(catalog.items.filter(item => item.state === "candidate" && validFundSymbol(item.source_symbol)
            && (item.source_symbol.toLowerCase().includes(kw.toLowerCase()) || item.source_name?.toLowerCase().includes(kw.toLowerCase()))).slice(0, 20)
            .map(item => ({symbol: item.source_symbol, name: item.source_name ?? item.source_symbol, market: "OF", source: "ths_official", is_realtime: false, source_candidate: true})));
        }
        return catalog;
      }) : Promise.resolve(null);
      const [searchedAssets, catalogRead] = await Promise.allSettled([assetRead, sourceRead]);
      if (searchedAssets.status === "rejected" && (invalidAssetResponse || searchedAssets.reason instanceof ApiError && [401, 403].includes(searchedAssets.reason.status))) throw searchedAssets.reason;
      const result = searchedAssets.status === "fulfilled" ? searchedAssets.value : null;
      if (result && (result.asset_type !== asset || result.query !== kw)) throw new ApiError(502, "搜索返回的资产或关键词不匹配，请重新搜索", "asset_identity_mismatch");
      const catalog = catalogRead.status === "fulfilled" ? catalogRead.value : null;
      const sourceItems = asset === "fund" && catalog?.tab === "meiguo" && catalog.purchase === "all" && catalog.source === "ths_official" && catalog.state !== "unavailable"
        ? catalog.items.filter(item => item.state === "candidate" && validFundSymbol(item.source_symbol)) : [];
      const matched = sourceItems.filter(item => item.source_symbol.toLowerCase().includes(kw.toLowerCase()) || item.source_name?.toLowerCase().includes(kw.toLowerCase()));
      const verified = result?.state !== "blocked" ? result?.items ?? [] : [];
      if (!result && !matched.length) throw searchedAssets.status === "rejected" ? searchedAssets.reason : new Error("基金来源未返回结果");
      const unverified = matched.filter(item => !verified.some(instrument => instrument.source_symbol === item.source_symbol));
      if (seq === seqRef.current) {
        if (result?.state === "blocked") setError(result.reason ?? "该资产来源尚未通过核验");
        else if (result?.state === "partial") setAddNote(result.reason ?? "来源只返回部分结果，请核对覆盖范围");
        else if (!result) setAddNote("标的搜索暂不可用；以下为来源栏目候选，点选后核对份额资料。");
        else if (asset === "fund" && (!catalog || catalog.state === "unavailable")) setAddNote("候选目录暂不可用；仅展示已取得的标的搜索结果。");
        if (unverified.length > 20) setAddNote(previous => `${previous ? `${previous} ` : ""}来源候选匹配 ${unverified.length} 只，仅展示前 20 只；完整名单可在工作台查看。`);
      }
      if (result?.state === "blocked") return [];
      return [...verified.map(instrument => ({symbol: instrument.source_symbol, name: sourceItems.find(item => item.source_symbol === instrument.source_symbol)?.source_name ?? instrument.name, market: instrument.market,
        source: instrument.source, is_realtime: false, instrument})),
        ...unverified.slice(0, 20).map(item => ({symbol: item.source_symbol, name: item.source_name ?? item.source_symbol, market: "OF", source: "ths_official", is_realtime: false, source_candidate: true as const}))];
    })();
    request
      .then((res) => {
        if (seq !== seqRef.current) return; // 已被更新的输入取代，丢弃旧响应
        setItems(previous => res.map(item => ({...item, is_realtime: item.is_realtime || previous.some(row => row.symbol === item.symbol && row.market === item.market && row.is_realtime)})));
        setSearched(true);
        setOpen(true);
      })
      .catch((e: unknown) => {
        if (seq !== seqRef.current) return;
        setItems([]);
        setSearched(true);
        // 旧实现 catch 后静默 setItems([])，接口报错时表现为「没反应」
        setError(e instanceof ApiError && e.message ? e.message : "搜索失败，请稍后重试");
        setOpen(true);
      })
      .finally(() => {
        if (seq === seqRef.current) setLoading(false);
      });
  }, [asset]);

  useEffect(() => {
    const kw = q.trim();
    if (composingRef.current) {
      // IME 组合中：q 是拼音片段，不调度搜索（compositionEnd 会用上屏词立即搜）。
      // 已有的待发 timer 也要清掉——比如用户先输英文停稳后又切输入法开始组合。
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      return;
    }
    if (kw.length < MIN_QUERY_LEN) {
      seqRef.current += 1; // 作废飞行中的请求（状态清理在 onChange/go 的 resetTransient）
      return;
    }
    const t = setTimeout(() => runSearch(kw), DEBOUNCE_MS);
    timerRef.current = t;
    return () => {
      clearTimeout(t);
      timerRef.current = null;
      // 输入变化 → 作废飞行中的旧请求（其响应回来时 seq 不匹配被丢弃）
      seqRef.current += 1;
    };
  }, [q, runSearch]);

  // 少于 2 字时直接派生空列表（旧写法在 effect 里同步 setItems([])，会触发级联渲染）
  const results = useMemo(() => q.trim().length >= MIN_QUERY_LEN && resultQuery === q.trim() ? items : [], [q, resultQuery, items]);

  const dismiss = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    seqRef.current += 1;
    setOpen(false);
    setLoading(false);
    setError(null);
    setSearched(false);
  }, []);

  function closeAndFocus() {
    inputRef.current?.focus();
    dismiss();
  }

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) dismiss();
    }
    document.addEventListener("mousedown", onClick);
    return () => { document.removeEventListener("mousedown", onClick); seqRef.current += 1; };
  }, [dismiss]);

  function remember(entry: SearchHistory) {
    const next = [entry, ...history.filter(row => historyIdentity(row) !== historyIdentity(entry))].slice(0, HISTORY_LIMIT);
    setHistory(next);
    try {localStorage.setItem(historyKey, JSON.stringify(next));} catch {}
  }

  function go(item: Pick<SearchItem, "symbol" | "name" | "market" | "instrument" | "source_candidate" | "us_candidate">, recalled?: SelectedHistory) {
    const parsed = validHistory([{query: q.trim(), symbol: item.symbol, name: item.name ?? item.symbol, market: item.market, ...(item.instrument ? {instrument: item.instrument} : item.us_candidate ? {us_candidate: true} : item.source_candidate ? {source_candidate: true} : {})}], asset)[0];
    const entry = recalled ?? (parsed && selectedHistory(parsed) ? parsed : undefined);
    if (!entry) {
      const term = parsed ?? validHistory([q.trim()])[0];
      if (term) remember(term);
      setError("证券身份待核：代码与市场不一致或代码段未确认，暂不能打开详情");
      setOpen(true);
      return;
    }
    if (recalled || resultQuery === q.trim() && results.some(row => row.symbol === item.symbol && row.market === item.market)) remember(entry);
    // Both a current result and a proved selection history use this one detail path.
    inputRef.current?.focus();
    dismiss();
    setQ("");
    if (entry.us_candidate) router.push(assetWorkbenchUrl("us_equity", entry.symbol));
    else if (entry.source_candidate) router.push(assetWorkbenchUrl("fund", entry.symbol, "research"));
    else if (entry.instrument) router.push(assetWorkbenchUrl("fund", entry.instrument.source_symbol, "research"));
    else openSymbolDetail({symbol: entry.symbol});
  }

  function recall(entry: SearchHistory) {
    if (selectedHistory(entry)) {go(entry, entry); return;}
    resetTransient(); setItems([]); setResultQuery(""); setQ(entry.query); setOpen(true);
    inputRef.current?.focus();
  }

  function moveChoice(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (event.nativeEvent.isComposing || event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const choices = [...(boxRef.current?.querySelectorAll<HTMLButtonElement>(".search-result-open, .search-history-open") ?? [])];
    const next = choices.indexOf(event.currentTarget) + (event.key === "ArrowDown" ? 1 : -1);
    (choices[next] ?? inputRef.current)?.focus();
  }

  /** 关键词变短到阈值以下时的状态清理（onChange 删字 / go 跳转清空两条路径）。 */
  function resetTransient() {
    seqRef.current += 1;
    setLoading(false);
    setError(null);
    setSearched(false);
  }

  async function quickAdd(e: React.MouseEvent, item: SearchItem) {
    e.stopPropagation();
    if (item.source_candidate || item.us_candidate) return;
    if (item.instrument) {
      if (!fundIdentityMatches(item.instrument) || addLock.current) return;
      addLock.current = true; setAdding(item.instrument.instrument_id); setAddNote(null);
      const seq = seqRef.current;
      try {
        const record = (await getAssetRecords()).items.find(row => row.instrument_id === item.instrument!.instrument_id);
        if (!record?.is_watched) await saveAssetRecord(item.instrument.instrument_id, {source_symbol: item.instrument.source_symbol,
          name: record?.name ?? (item.instrument.name.length <= 80 ? item.instrument.name : null), is_watched: true, units: record?.units ?? null,
          cost_total: record?.cost_total ?? null, note: record?.note ?? null, expected_version: record?.version ?? 0});
        if (seq === seqRef.current) {setItems(previous => previous.map(row => row.instrument?.instrument_id === item.instrument!.instrument_id ? {...row, is_realtime: true} : row)); setAddNote(`${item.name} 已加入基金自选`);}
        notifyAssetRecordsChanged();
      } catch (exc) {if (seq === seqRef.current) setAddNote(`加入自选失败：${exc instanceof Error ? exc.message : "请重试"}`);}
      finally {addLock.current = false; setAdding(null);}
      return;
    }
    const symbol = historySymbol(item.symbol, item.market ?? "");
    // WatchlistAdd accepts bare six-digit securities, never canonical index IDs.
    if (!symbol || !/^\d{6}$/.test(symbol) || addLock.current) return;
    addLock.current = true;
    setAdding(`${item.market}:${symbol}`);
    setAddNote(null);
    const seq = seqRef.current;
    try {
      await addToWatchlist(symbol, item.name ?? undefined);
      if (seq === seqRef.current) {
        setItems((prev) => prev.map((i) => (i.market === item.market && historySymbol(i.symbol, i.market ?? "") === symbol ? { ...i, is_realtime: true } : i)));
        setAddNote(`${item.name ?? item.symbol} 已加入自选`);
      }
      notifyWatchlistChanged();
    } catch (exc) {
      if (seq === seqRef.current) setAddNote(`加入自选失败：${exc instanceof Error ? exc.message : "请重试"}`);
    } finally {
      addLock.current = false;
      setAdding(null);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (composingRef.current || e.nativeEvent.isComposing || e.keyCode === 229) return; // IME 组合中：Enter=选字上屏、Escape=取消组合，不是搜索指令
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && open && (results.length || q.trim().length < MIN_QUERY_LEN && history.length)) {
      e.preventDefault();
      const choices = boxRef.current?.querySelectorAll<HTMLButtonElement>(".search-result-open, .search-history-open");
      choices?.[e.key === "ArrowDown" ? 0 : choices.length - 1]?.focus();
      return;
    }
    if (e.key === "Enter") {
      if (results.length > 0) {
        go(results[0]);
        return;
      }
      // 旧实现 Enter 只在已有结果时生效——输完代码立刻回车（结果尚未返回）
      // 会被吞掉，是「输入后搜索无响应」最直观的场景。此处跳过防抖立即搜索。
      const kw = q.trim();
      if (kw.length >= MIN_QUERY_LEN) {
        if (timerRef.current) {
          clearTimeout(timerRef.current);
          timerRef.current = null;
        }
        runSearch(kw);
      }
    }
    if (e.key === "Escape") { e.stopPropagation(); dismiss(); }
  }

  const kw = q.trim();
  const historyMode = kw.length < MIN_QUERY_LEN && !composing && history.length > 0;
  const showPanel =
    open &&
    (historyMode || kw.length >= MIN_QUERY_LEN && (results.length > 0 || loading || error !== null || searched));
  // Closing cancels the query immediately; its last visible reading stays intact during exit.
  const panelSnapshot = useMemo(() => ({results, loading, error, searched, addNote, adding, kw, historyMode, history}), [results, loading, error, searched, addNote, adding, kw, historyMode, history]);
  const panelPresence = useExitPresence(showPanel ? panelSnapshot : null);
  const panel = panelPresence.value;

  return (
    <div ref={boxRef} data-search-expanded={expanded || undefined} data-collapsible={collapsible || undefined} className="search-field relative w-32 min-w-0 sm:w-44 md:w-52" onKeyDown={event => {
      if (event.key === "Escape" && !composingRef.current && !event.nativeEvent.isComposing && event.keyCode !== 229) {
        event.stopPropagation();
        closeAndFocus();
      }
    }}>
      {collapsible && <button ref={triggerRef} type="button" className="search-expand" aria-label={expanded ? "收起证券搜索" : "展开证券搜索"} aria-expanded={expanded} onClick={() => {
        if(expanded){dismiss();setExpanded(false);triggerRef.current?.focus();}
        else setExpanded(true);
      }}><HugeiconsIcon icon={expanded ? Cancel01Icon : Search01Icon} size={18} strokeWidth={1.6} aria-hidden="true" /><span>{expanded ? "收起" : "搜索证券"}</span>{!expanded && <span className="search-prompt">代码 / 名称</span>}</button>}
      <div className="search-input-wrap relative" hidden={!expanded}>
      <input
        ref={inputRef}
        aria-controls={showPanel ? panelId : undefined}
        value={q}
        onChange={(e) => {
          const v = e.target.value;
          seqRef.current += 1;
          setResultQuery("");
          setSearched(false);
          setError(null);
          setAddNote(null);
          setQ(v);
          // 删短到阈值以下：立即作废飞行中请求并清理搜索状态
          if (v.trim().length < MIN_QUERY_LEN) resetTransient();
        }}
        onCompositionStart={() => {
          composingRef.current = true;
          setComposing(true);
          seqRef.current += 1;
          setResultQuery("");
        }}
        onCompositionEnd={(e) => {
          if (!composingRef.current) return;
          composingRef.current = false;
          setComposing(false);
          const v = e.currentTarget.value;
          if (v !== q) {
            // 个别环境（Safari 部分版本）组合结束不补发 change：把终值写回状态，
            // 交给 q 的 effect 走防抖搜索
            setQ(v);
            return;
          }
          // 常规路径：上屏词已在状态（组合中被门控跳过调度）→ 跳过防抖立即搜
          const kw = v.trim();
          if (kw.length >= MIN_QUERY_LEN) runSearch(kw);
        }}
        onFocus={() => (results.length > 0 || historyMode) && setOpen(true)}
        onKeyDown={onKeyDown}
        aria-label="搜索证券代码或名称"
        placeholder="搜索代码 / 名称"
        title={asset === "cn_equity" ? "支持证券代码、名称、拼音全拼与缩写" : `搜索${ASSET_LABELS[asset]}官方名称或代码；结果不混入 A 股`}
        aria-busy={loading}
        className="w-full rounded-md border border-zinc-200 bg-transparent px-3 py-1.5 pr-8 text-sm outline-none placeholder:text-zinc-600 dark:placeholder:text-zinc-400 focus:border-up/60 dark:border-zinc-700"
      />
      {loading && (
        <span
          aria-hidden
          className="absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 animate-spin rounded-full border-2 border-zinc-300 border-t-zinc-500 dark:border-zinc-600 dark:border-t-zinc-300"
        />
      )}
      </div>
      {panel && (
        <section id={panelId} data-motion-state={showPanel ? "open" : "closed"} aria-hidden={!showPanel || undefined} inert={!showPanel} className="ui-glass-overlay search-popover motion-float" aria-label="证券搜索结果">
          <header className="search-popover-heading">
            <div><strong>{asset === "cn_equity" ? "证券搜索" : `${ASSET_LABELS[asset]}搜索`}</strong><span>{panel.historyMode ? "最近查看 / 检索词" : asset === "cn_equity" ? "代码、名称或拼音" : "官方名称或完整代码"}</span></div>
            <CloseButton onClose={closeAndFocus} label="关闭搜索结果" autoFocus={false} />
          </header>
        <ul className="search-result-list">
          {panel.historyMode && <li className="flex items-center justify-between px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400"><span>仅保存在本机</span><button type="button" className="quiet-action" aria-label="清除搜索历史" onClick={() => {setHistory([]); try {localStorage.removeItem(historyKey);} catch {} inputRef.current?.focus();}}>清除历史</button></li>}
          {panel.historyMode && panel.history.map(entry => <li key={historyIdentity(entry)}><button type="button" className="search-result-open search-history-open"
            aria-label={selectedHistory(entry) ? `查看 ${entry.name} ${entry.symbol} ${entry.market}` : `检索词 ${entry.query}`}
            title={selectedHistory(entry) ? `直接查看详情；原检索词 ${entry.query}` : "仅保存了检索词，尚未确认证券身份"}
            onKeyDown={moveChoice} onClick={() => recall(entry)}>
            <span className="search-result-name">{selectedHistory(entry) ? entry.name : entry.query}</span>
            <span className="search-result-code">{selectedHistory(entry) ? entry.symbol : "检索词"}<span>{selectedHistory(entry) ? entry.market : "重新搜索"}</span></span>
          </button></li>)}
          {panel.addNote && <li role="status" className="px-3 py-2 text-xs">{panel.addNote}</li>}
          {panel.error !== null && (
            <li className="px-3 py-2 text-xs text-red-700 dark:text-red-400" role="alert">
              <p>{panel.error}</p>
              <button type="button" className="quiet-action search-retry" onClick={() => {fundCatalogRead.current = null; runSearch(kw);}}>重新搜索</button>
            </li>
          )}
          {panel.loading && panel.results.length === 0 && panel.error === null && (
            <li className="px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400" role="status">
              搜索中…
            </li>
          )}
          {!panel.loading && panel.error === null && panel.searched && panel.results.length === 0 && (
            <li className="px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400" role="status">
              未找到与「{panel.kw}」匹配的{asset === "fund" ? "基金" : "股票"}
            </li>
          )}
          {panel.results.map((it) => {
            const symbol = it.instrument ? fundIdentityMatches(it.instrument) ? it.instrument.source_symbol : null : it.us_candidate && validUsSourceSymbol(it.symbol) ? it.symbol : historySymbol(it.symbol, it.market ?? "");
            const index = !it.instrument && !it.source_candidate && !it.us_candidate && symbol !== null && !/^\d{6}$/.test(symbol);
            return <li key={`${it.source}-${it.market ?? "unknown"}-${it.symbol}`}>
              <div className="search-result-row">
                <button type="button" aria-label={it.us_candidate ? `${it.name ?? it.symbol} ${it.symbol} ${it.market ?? ""}` : undefined} onClick={() => go(it)} className="search-result-open" onKeyDown={moveChoice}>
                  <span className="search-result-name">{it.name ?? it.symbol}</span>
                  <span className="search-result-code">{it.symbol}<span>{it.market}</span></span>
                </button>
                {it.us_candidate ? <span className="search-watch-state">美股来源 · 点选核对</span> : it.source_candidate ? <span className="search-watch-state">来源候选 · 份额待核</span> : index ? <span className="search-watch-state">指数 · 仅查看</span> : symbol === null ? <span className="search-watch-state">身份待核</span> : it.is_realtime ? (
                  <span className="search-watch-state">已自选 ✓</span>
                ) : (
                  <button type="button" disabled={panel.adding !== null} aria-label={`加入自选 ${it.name ?? it.symbol}`} onClick={(e) => void quickAdd(e, it)} className="search-watch-action" title="加入自选">
                    {panel.adding === (it.instrument?.instrument_id ?? `${it.market}:${symbol}`) ? "保存中" : "＋"}
                  </button>
                )}
              </div>
            </li>;
          })}
        </ul>
        </section>
      )}
    </div>
  );
}
