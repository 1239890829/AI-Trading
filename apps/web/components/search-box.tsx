"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { addToWatchlist, searchSymbols } from "@/lib/api";
import type { SymbolSearchItem } from "@/types/market";

export function SearchBox() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<SymbolSearchItem[]>([]);
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (q.trim().length < 2) return;
    const t = setTimeout(async () => {
      try {
        const res = await searchSymbols(q.trim());
        setItems(res);
        setOpen(true);
      } catch {
        setItems([]);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  // 少于 2 字时直接派生空列表（旧写法在 effect 里同步 setItems([])，会触发级联渲染）
  const results = q.trim().length >= 2 ? items : [];

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
    router.push(`/workbench?symbol=${item.symbol}`);
  }

  async function quickAdd(e: React.MouseEvent, item: SymbolSearchItem) {
    e.stopPropagation();
    try {
      await addToWatchlist(item.symbol, item.name ?? undefined);
      setItems((prev) => prev.map((i) => (i.symbol === item.symbol ? { ...i, is_realtime: true } : i)));
      window.dispatchEvent(new CustomEvent("watchlist-changed"));
    } catch {}
  }

  return (
    <div ref={boxRef} className="relative w-44 md:w-64">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => results.length > 0 && setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && results.length > 0) go(results[0]);
          if (e.key === "Escape") setOpen(false);
        }}
        placeholder="搜索代码 / 名称"
        className="w-full rounded-md border border-zinc-200 bg-transparent px-3 py-1.5 text-sm outline-none placeholder:text-zinc-400 focus:border-up/60 dark:border-zinc-700"
      />
      {open && results.length > 0 && (
        <ul className="absolute left-0 right-0 top-10 z-50 overflow-hidden rounded-md border border-zinc-200 bg-white shadow-lg dark:border-zinc-700 dark:bg-zinc-900">
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
