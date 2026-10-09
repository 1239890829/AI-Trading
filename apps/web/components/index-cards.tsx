"use client";

import { memo, useState } from "react";
import { HugeiconsIcon } from "@hugeicons/react";
import ArrowDown01Icon from "@hugeicons/core-free-icons/ArrowDown01Icon";
import { indexDetailSymbol } from "@/lib/api";
import { QualityBadge } from "@/components/quality-badge";
import { fmt, pctColor, pctText, sourceLabel } from "@/lib/format";
import type { Quote } from "@/types/market";

interface Props {
  indices: Quote[];
  /** 当前右面板展开的 symbol（带前缀指数形态），用于选中高亮 */
  selected?: string;
  /** 点击指数 → 右面板展开该指数详情（与自选股行点击同一交互） */
  onSelect?: (symbol: string) => void;
}

// 以带市场身份的指数代码固定常用基准，不能把股票撞码或数组位置当成指数身份。
const COLLAPSED_INDICES = [
  { detail: "sh000001", name: "上证指数" },
  { detail: "sh000688", name: "科创50" },
  { detail: "sz399006", name: "创业板指" },
] as const;

/** 指数迷你卡（可展开/收起；收起保留三项常用基准）。
 *  点击卡片与点击自选股行等效：右侧面板展开该指数的分时/K线详情。
 *
 *  P1-1（2026-09-11）：包 `memo`。行情 3s 一 tick 会让 workbench 整体重渲染，
 *  而 `indices` 只在 loadBase（10s）时换引用、`onSelect` 是 useCallback、
 *  `selected` 是字符串 ⇒ 3s tick 上本组件可整体跳过。 */
export const IndexCards = memo(function IndexCards({ indices, selected, onSelect }: Props) {
  const [open, setOpen] = useState(false);
  const cards = open
    ? indices.map((quote) => ({ detail: indexDetailSymbol(quote.symbol, quote.market), name: quote.name ?? quote.symbol, quote }))
    : COLLAPSED_INDICES.map((index) => ({
      ...index,
      quote: indices.find((quote) => indexDetailSymbol(quote.symbol, quote.market) === index.detail),
    }));

  return (
    <div className="ui-card shrink-0 rounded-xl border border-zinc-200 p-2 dark:border-zinc-800">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="index-disclosure mb-1 flex w-full items-center justify-between px-1 text-xs text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200">
        <span>指数{!open && indices.length > 0 ? `（${indices.length}）` : ""}</span>
        <span className="index-disclosure-action">{open ? "收起" : "展开"}<HugeiconsIcon icon={ArrowDown01Icon} size={14} strokeWidth={1.6} aria-hidden="true" /></span>
      </button>
      <div className="grid grid-cols-3 gap-1" data-collapsed={!open}>
        {cards.map(({ quote: q, detail, name }) => {
          const active = selected === detail;
          return (
            <button
              type="button"
              key={detail}
              disabled={!q}
              aria-pressed={active}
              onClick={() => onSelect?.(detail)}
              title={q ? `${name} · 来源 ${sourceLabel(q.source)}${onSelect ? " · 点击查看详情" : ""}` : `${name} · 本轮未返回该指数`}
              className={`rounded-lg px-2 py-1.5 text-left transition-colors ${
                active
                  ? "bg-zinc-200/80 dark:bg-zinc-800"
                  : "bg-zinc-100/60 hover:bg-zinc-200/60 dark:bg-zinc-900/60 dark:hover:bg-zinc-800/60"
              } ${q && onSelect ? "cursor-pointer" : "cursor-default"}`}
            >
              <div className="flex min-w-0 items-center justify-between gap-1">
                <span className="min-w-0 truncate text-[10px] text-zinc-600 dark:text-zinc-400">{name}</span>
                {/* 可见性策略单点在 QualityBadge 内部（shouldShowQualityBadge）：正常出静音灰「正常」，
                    low/medium 瞬态不出（2026-09-02 闪烁修复），stale/invalid 常显。调用点不再自行判断。 */}
                <QualityBadge quality={q?.quality} reasons={q?.quality_reasons} />
              </div>
              <div className="whitespace-nowrap font-mono text-sm font-semibold tabular-nums">
                {!q ? <span className="text-xs font-normal text-zinc-600 dark:text-zinc-400">未返回</span> : q.price == null ? <span className="text-xs font-normal text-zinc-600 dark:text-zinc-400">未开盘</span> : fmt(q.price)}
              </div>
              <div className={`whitespace-nowrap font-mono text-[11px] tabular-nums ${pctColor(q?.change_pct)}`}>{pctText(q?.change_pct)}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
});
