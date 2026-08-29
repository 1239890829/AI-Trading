"use client";

import { useState } from "react";
import { QualityBadge } from "@/components/quality-badge";
import { fmt, pctColor, pctText } from "@/lib/format";
import type { Quote } from "@/types/market";

/** 指数迷你卡（3 列，可展开/收起）——对标 klineshare 左栏范式。 */
export function IndexCards({ indices }: { indices: Quote[] }) {
  const [open, setOpen] = useState(true);

  return (
    <div className="shrink-0 rounded-xl border border-zinc-200 p-2 dark:border-zinc-800">
      <button onClick={() => setOpen(!open)} className="mb-1 flex w-full items-center justify-between px-1 text-xs text-zinc-400 hover:text-zinc-200">
        <span>指数{!open && indices.length > 0 ? `（${indices.length}）` : ""}</span>
        <span>{open ? "收起 ▲" : "展开 ▼"}</span>
      </button>
      {open && (
        <div className="grid grid-cols-3 gap-1">
          {indices.map((q) => (
            <div key={q.symbol} className="rounded-lg bg-zinc-100/60 px-2 py-1.5 dark:bg-zinc-900/60" title={`${q.name} ${q.source}`}>
              <div className="flex items-center justify-between">
                <span className="truncate text-[10px] text-zinc-400">{q.name ?? q.symbol}</span>
                <QualityBadge quality={q.quality} reasons={q.quality_reasons} />
              </div>
              <div className="font-mono text-sm font-semibold tabular-nums">{fmt(q.price)}</div>
              <div className={`font-mono text-[11px] tabular-nums ${pctColor(q.change_pct)}`}>{pctText(q.change_pct)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
