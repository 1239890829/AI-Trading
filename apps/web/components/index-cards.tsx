"use client";

import { useState } from "react";
import { indexDetailSymbol } from "@/lib/api";
import { QualityBadge } from "@/components/quality-badge";
import { fmt, pctColor, pctText, isHardQuality, sourceLabel } from "@/lib/format";
import type { Quote } from "@/types/market";

interface Props {
  indices: Quote[];
  /** 当前右面板展开的 symbol（带前缀指数形态），用于选中高亮 */
  selected?: string;
  /** 点击指数 → 右面板展开该指数详情（与自选股行点击同一交互） */
  onSelect?: (symbol: string) => void;
}

/** 指数迷你卡（3 列，可展开/收起）——对标 klineshare 左栏范式。
 *  点击卡片与点击自选股行等效：右侧面板展开该指数的分时/K线详情。 */
export function IndexCards({ indices, selected, onSelect }: Props) {
  const [open, setOpen] = useState(true);

  return (
    <div className="shrink-0 rounded-xl border border-zinc-200 p-2 dark:border-zinc-800">
      <button onClick={() => setOpen(!open)} className="mb-1 flex w-full items-center justify-between px-1 text-xs text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200">
        <span>指数{!open && indices.length > 0 ? `（${indices.length}）` : ""}</span>
        <span>{open ? "收起 ▲" : "展开 ▼"}</span>
      </button>
      {open && (
        <div className="grid grid-cols-3 gap-1">
          {indices.map((q) => {
            const detail = indexDetailSymbol(q.symbol, q.market);
            const active = selected === detail;
            return (
              <button
                key={q.symbol}
                onClick={() => onSelect?.(detail)}
                title={`${q.name} · 来源 ${sourceLabel(q.source)}${onSelect ? " · 点击查看详情" : ""}`}
                className={`rounded-lg px-2 py-1.5 text-left transition-colors ${
                  active
                    ? "bg-zinc-200/80 dark:bg-zinc-800"
                    : "bg-zinc-100/60 hover:bg-zinc-200/60 dark:bg-zinc-900/60 dark:hover:bg-zinc-800/60"
                } ${onSelect ? "cursor-pointer" : "cursor-default"}`}
              >
                <div className="flex items-center justify-between">
                  <span className="truncate text-[10px] text-zinc-600 dark:text-zinc-400">{q.name ?? q.symbol}</span>
                  {/* 仅硬质量问题（过期/休市/非法）出徽标：正常态徽标是视觉噪音，
                      low/medium 瞬态抖动会闪（2026-09-02 可疑标签修复，口径同列表行） */}
                  {isHardQuality(q.quality) && <QualityBadge quality={q.quality} reasons={q.quality_reasons} />}
                </div>
                <div className="font-mono text-sm font-semibold tabular-nums">
                  {q.price == null ? <span className="text-xs font-normal text-zinc-600 dark:text-zinc-400">未开盘</span> : fmt(q.price)}
                </div>
                <div className={`font-mono text-[11px] tabular-nums ${pctColor(q.change_pct)}`}>{pctText(q.change_pct)}</div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
