"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, addToWatchlist, searchSymbols } from "@/lib/api";
import { notifyWatchlistChanged } from "@/lib/watchlist-sync";
import { workbenchUrl } from "@/lib/routing";
import type { SymbolSearchItem } from "@/types/market";

/** 输入停稳后的防抖时长（ms）。Enter 可跳过防抖立即搜索。 */
const DEBOUNCE_MS = 250;
/** 触发搜索的最小关键词长度（少于 2 字符不发请求）。 */
const MIN_QUERY_LEN = 2;

export function SearchBox() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<SymbolSearchItem[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 是否已对当前关键词发起过搜索：空结果提示只在「确实搜过且没搜到」时出现。 */
  const [searched, setSearched] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  /**
   * 竞态守卫：每次发起请求自增，响应返回时 seq 不匹配即丢弃。
   * 旧实现只有 clearTimeout（只能取消尚未发出的请求），已发出的慢响应会
   * 后发先至覆盖新结果——表现就是「输入后搜索无响应/结果不对」。
   */
  const seqRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runSearch = useCallback((kw: string) => {
    const seq = ++seqRef.current;
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
  const results = q.trim().length >= MIN_QUERY_LEN ? items : [];

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  function go(item: SymbolSearchItem) {
    setOpen(false);
    setQ("");
    resetTransient(); // 跳转清空后，飞行中的旧请求若返回不得再弹开面板
    router.push(workbenchUrl(item.symbol));
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
    try {
      await addToWatchlist(item.symbol, item.name ?? undefined);
      setItems((prev) => prev.map((i) => (i.symbol === item.symbol ? { ...i, is_realtime: true } : i)));
      notifyWatchlistChanged();
    } catch (exc) {
      // 失败不再完全静默：留 console 痕迹便于排查（主链路错误在下拉面板内提示）
      console.warn("quickAdd failed:", exc);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
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
    if (e.key === "Escape") setOpen(false);
  }

  const kw = q.trim();
  const showPanel =
    open &&
    kw.length >= MIN_QUERY_LEN &&
    (results.length > 0 || loading || error !== null || searched);

  return (
    <div ref={boxRef} className="relative w-44 md:w-64">
      <input
        value={q}
        onChange={(e) => {
          const v = e.target.value;
          setQ(v);
          // 删短到阈值以下：立即作废飞行中请求并清理搜索状态
          if (v.trim().length < MIN_QUERY_LEN) resetTransient();
        }}
        onFocus={() => results.length > 0 && setOpen(true)}
        onKeyDown={onKeyDown}
        placeholder="搜索代码 / 名称"
        aria-busy={loading}
        className="w-full rounded-md border border-zinc-200 bg-transparent px-3 py-1.5 pr-8 text-sm outline-none placeholder:text-zinc-400 focus:border-up/60 dark:border-zinc-700"
      />
      {loading && (
        <span
          aria-hidden
          className="absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 animate-spin rounded-full border-2 border-zinc-300 border-t-zinc-500 dark:border-zinc-600 dark:border-t-zinc-300"
        />
      )}
      {showPanel && (
        <ul className="absolute left-0 right-0 top-10 z-50 overflow-hidden rounded-md border border-zinc-200 bg-white shadow-lg dark:border-zinc-700 dark:bg-zinc-900">
          {error !== null && (
            <li className="px-3 py-2 text-xs text-red-400" role="alert">
              {error}
            </li>
          )}
          {loading && results.length === 0 && error === null && (
            <li className="px-3 py-2 text-xs text-zinc-400" role="status">
              搜索中…
            </li>
          )}
          {!loading && error === null && searched && results.length === 0 && (
            <li className="px-3 py-2 text-xs text-zinc-400" role="status">
              未找到与「{kw}」匹配的股票
            </li>
          )}
          {results.map((it) => (
            <li key={`${it.source}-${it.symbol}`}>
              <div
                role="button"
                tabIndex={0}
                onClick={() => go(it)}
                onKeyDown={(e) => e.key === "Enter" && go(it)}
                className="flex w-full cursor-pointer items-center justify-between px-3 py-2 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
              >
                <span className="font-mono text-xs text-zinc-400">{it.symbol}</span>
                <span className="flex-1 px-2">{it.name}</span>
                {it.is_realtime ? (
                  <span className="text-xs text-zinc-400">已加自选 ✓</span>
                ) : (
                  <button onClick={(e) => void quickAdd(e, it)} className="mr-2 rounded border border-up/50 px-1.5 text-xs text-up hover:bg-up/10" title="加入自选">
                    ＋
                  </button>
                )}
                <span className="text-xs text-zinc-400">{it.market}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
