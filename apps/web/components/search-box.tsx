"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { ApiError, addToWatchlist, searchSymbols } from "@/lib/api";
import { notifyWatchlistChanged } from "@/lib/watchlist-sync";
import { useSymbolDetail } from "@/components/detail/symbol-detail-context";
import type { SymbolSearchItem } from "@/types/market";

/** 输入停稳后的防抖时长（ms）。Enter 可跳过防抖立即搜索。 */
const DEBOUNCE_MS = 250;
/** 触发搜索的最小关键词长度（少于 2 字符不发请求）。 */
const MIN_QUERY_LEN = 2;
// 中文 IME：组合（拼音）期间不调度搜索——拼音片段（"xing"/"xingw"…）是垃圾查询，
// 搜不到结果还消耗上游配额；选字 Enter 属于 IME 操作不是搜索指令。
// compositionEnd 后用最终上屏词立即搜索（跳过防抖）。

export function SearchBox({collapsible = false}: {collapsible?: boolean}) {
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
  const [items, setItems] = useState<SymbolSearchItem[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 是否已对当前关键词发起过搜索：空结果提示只在「确实搜过且没搜到」时出现。 */
  const [searched, setSearched] = useState(false);
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
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 中文 IME 组合中（拼音未上屏）：期间不调度搜索、Enter/Escape 归 IME 所有。 */
  const composingRef = useRef(false);

  const runSearch = useCallback((kw: string) => {
    const seq = ++seqRef.current;
    setItems([]);
    setResultQuery(kw);
    setLoading(true);
    setError(null);
    setOpen(true); // 请求飞行中立即打开面板显示「搜索中…」，不等响应
    searchSymbols(kw)
      .then((res) => {
        if (seq !== seqRef.current) return; // 已被更新的输入取代，丢弃旧响应
        setItems(res);
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
  }, []);

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
  const results = q.trim().length >= MIN_QUERY_LEN && resultQuery === q.trim() ? items : [];

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

  function go(item: SymbolSearchItem) {
    setOpen(false);
    setQ("");
    resetTransient(); // 弹窗后清空，飞行中的旧请求若返回不得再弹开面板
    openSymbolDetail({ symbol: item.symbol });
  }

  /** 关键词变短到阈值以下时的状态清理（onChange 删字 / go 跳转清空两条路径）。 */
  function resetTransient() {
    seqRef.current += 1;
    setLoading(false);
    setError(null);
    setSearched(false);
  }

  async function quickAdd(e: React.MouseEvent, item: SymbolSearchItem) {
    e.stopPropagation();
    if (addLock.current) return;
    addLock.current = true;
    setAdding(item.symbol);
    setAddNote(null);
    const seq = seqRef.current;
    try {
      await addToWatchlist(item.symbol, item.name ?? undefined);
      if (seq === seqRef.current) {
        setItems((prev) => prev.map((i) => (i.symbol === item.symbol ? { ...i, is_realtime: true } : i)));
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
  const showPanel =
    open &&
    kw.length >= MIN_QUERY_LEN &&
    (results.length > 0 || loading || error !== null || searched);

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
      }}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">{expanded ? <path d="m7 7 10 10M17 7 7 17"/> : <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></>}</svg><span>{expanded ? "收起" : "搜索证券"}</span></button>}
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
          seqRef.current += 1;
          setResultQuery("");
        }}
        onCompositionEnd={(e) => {
          if (!composingRef.current) return;
          composingRef.current = false;
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
        onFocus={() => results.length > 0 && setOpen(true)}
        onKeyDown={onKeyDown}
        aria-label="搜索证券代码或名称"
        placeholder="搜索代码 / 名称"
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
      {showPanel && (
        <section id={panelId} className="ui-glass-overlay search-popover" aria-label="证券搜索结果">
          <header className="search-popover-heading">
            <div><strong>证券搜索</strong><span>匹配代码或名称</span></div>
            <button type="button" className="quiet-action" onClick={closeAndFocus} aria-label="关闭搜索结果">关闭</button>
          </header>
        <ul className="search-result-list">
          {addNote && <li role="status" className="px-3 py-2 text-xs">{addNote}</li>}
          {error !== null && (
            <li className="px-3 py-2 text-xs text-red-700 dark:text-red-400" role="alert">
              <p>{error}</p>
              <button type="button" className="quiet-action search-retry" onClick={() => runSearch(kw)}>重新搜索</button>
            </li>
          )}
          {loading && results.length === 0 && error === null && (
            <li className="px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400" role="status">
              搜索中…
            </li>
          )}
          {!loading && error === null && searched && results.length === 0 && (
            <li className="px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400" role="status">
              未找到与「{kw}」匹配的股票
            </li>
          )}
          {results.map((it) => (
            <li key={`${it.source}-${it.symbol}`}>
              <div className="search-result-row">
                <button type="button" onClick={() => go(it)} className="search-result-open">
                  <span className="search-result-name">{it.name ?? it.symbol}</span>
                  <span className="search-result-code">{it.symbol}<span>{it.market}</span></span>
                </button>
                {it.is_realtime ? (
                  <span className="search-watch-state">已自选 ✓</span>
                ) : (
                  <button type="button" disabled={adding !== null} aria-label={`加入自选 ${it.name ?? it.symbol}`} onClick={(e) => void quickAdd(e, it)} className="search-watch-action" title="加入自选">
                    {adding === it.symbol ? "保存中" : "＋"}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
        </section>
      )}
    </div>
  );
}
