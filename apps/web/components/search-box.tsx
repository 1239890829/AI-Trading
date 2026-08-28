"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { searchSymbols } from "@/lib/api";
import type { SymbolSearchItem } from "@/types/market";

export function SearchBox() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<SymbolSearchItem[]>([]);
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (q.trim().length < 2) {
      setItems([]);
      return;
    }
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
    router.push(`/stock/${item.symbol}`);
  }

  return (
    <div ref={boxRef} className="relative w-44 md:w-64">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => items.length > 0 && setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && items.length > 0) go(items[0]);
          if (e.key === "Escape") setOpen(false);
        }}
        placeholder="搜索代码 / 名称"
        className="w-full rounded-md border border-zinc-200 bg-transparent px-3 py-1.5 text-sm outline-none placeholder:text-zinc-400 focus:border-up/60 dark:border-zinc-700"
      />
      {open && items.length > 0 && (
        <ul className="absolute left-0 right-0 top-10 z-50 overflow-hidden rounded-md border border-zinc-200 bg-white shadow-lg dark:border-zinc-700 dark:bg-zinc-900">
          {items.map((it) => (
            <li key={`${it.source}-${it.symbol}`}>
              <button
                onClick={() => go(it)}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
              >
                <span className="font-mono text-xs text-zinc-400">{it.symbol}</span>
                <span className="flex-1 px-2">{it.name}</span>
                <span className="text-xs text-zinc-400">{it.market}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
