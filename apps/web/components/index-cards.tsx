"use client";

import { memo, useState, type CSSProperties } from "react";
import { HugeiconsIcon } from "@hugeicons/react";
import ArrowDown01Icon from "@hugeicons/core-free-icons/ArrowDown01Icon";
import { indexDetailSymbol } from "@/lib/api";
import { QualityBadge } from "@/components/quality-badge";
import { useExitPresence } from "@/hooks/use-exit-presence";
import { dateTimeTextBJ, fmt, pctColor, pctText, sourceLabel } from "@/lib/format";
import type { Quote } from "@/types/market";
import styles from "./index-cards.module.css";

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
  const [inspected, setInspected] = useState<string | null>(null);
  const extrasPresence = useExitPresence(open ? true : null);
  const primary = COLLAPSED_INDICES.map((index) => ({
      ...index,
      quote: indices.find((quote) => indexDetailSymbol(quote.symbol, quote.market) === index.detail),
    }));
  const extras = indices.map((quote) => ({detail: indexDetailSymbol(quote.symbol, quote.market), name: quote.name ?? quote.symbol, quote}))
    .filter(index => !COLLAPSED_INDICES.some(primary => primary.detail === index.detail));
  const all = [...primary, ...extras];
  const reading = all.find(item => item.detail === inspected) ?? all.find(item => item.detail === selected) ?? primary[0];
  const maxAbs = Math.max(0, ...indices.map(q => typeof q.change_pct === "number" && Number.isFinite(q.change_pct) ? Math.abs(q.change_pct) : 0));
  const clock = (value: string | null | undefined) => value && Number.isFinite(Date.parse(value)) ? dateTimeTextBJ(value, {seconds: true}) : "未提供";
  const renderCard = ({quote: q, detail, name}: {quote?: Quote; detail: string; name: string}) => {
    const active = selected === detail;
    const pct = typeof q?.change_pct === "number" && Number.isFinite(q.change_pct) ? q.change_pct : null;
    const width = pct !== null && maxAbs > 0 ? Math.abs(pct) / maxAbs * 50 : 0;
    const range = {"--index-range-width": `${width}%`, "--index-range-start": `${pct !== null && pct < 0 ? 50 - width : 50}%`} as CSSProperties;
    return (
      <button type="button" key={detail} disabled={!q} aria-pressed={active} onClick={() => onSelect?.(detail)}
        onMouseEnter={() => setInspected(detail)} onFocus={() => setInspected(detail)} data-inspected={reading.detail === detail || undefined}
        title={q ? `${name} · 来源 ${sourceLabel(q.source)}${onSelect ? " · 点击查看详情" : ""}` : `${name} · 本轮未返回该指数`}
        className={`${styles.row} ${active ? "bg-zinc-200/80 dark:bg-zinc-800" : ""} ${q && onSelect ? "cursor-pointer" : "cursor-default"}`}>
        <span className={styles.identity}><strong>{name}</strong><small>{!q ? "未返回" : q.price == null ? "未开盘" : fmt(q.price)}</small></span>
        <span className={`${styles.range} ${pctColor(pct)}`} style={range} aria-hidden="true"><i/>{pct !== null && <b/>}</span>
        <span className={styles.reading}><strong className={pctColor(pct)}>{pctText(pct)}</strong>
          <QualityBadge compact align="start" quality={q?.quality} reasons={q?.quality_reasons} symbol={detail} market={q?.market} dataTimestamp={q?.data_timestamp} session={q?.display_session} />
        </span>
      </button>
    );
  };

  return (
    <div data-index-comparison className={`${styles.comparison} ui-card shrink-0 rounded-xl border border-zinc-200 p-2 dark:border-zinc-800`}>
      <button type="button" onClick={() => {setOpen(!open); if (open && !primary.some(item => item.detail === inspected)) setInspected(null);}} aria-expanded={open} className="index-disclosure mb-1 flex w-full items-center justify-between px-1 text-xs text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200">
        <span>指数{!open && indices.length > 0 ? `（${indices.length}）` : ""}</span>
        <span className="index-disclosure-action">{open ? "收起" : "展开"}<HugeiconsIcon icon={ArrowDown01Icon} size={14} strokeWidth={1.6} aria-hidden="true" /></span>
      </button>
      <p className={styles.scale} aria-label="指数涨跌幅共同零轴">共同零轴 <span>−{maxAbs.toFixed(2)}%</span><span>0</span><span>+{maxAbs.toFixed(2)}%</span></p>
      <div className={`${styles.rows} index-comparison-rows`} data-collapsed="true">
        {primary.map(renderCard)}
      </div>
      {extrasPresence.value && <div className="index-extras motion-disclosure" data-open={open} aria-hidden={!open || undefined} inert={!open}>
        <div className="motion-disclosure-body">
          <div className={`${styles.rows} index-extras-grid`}>{extras.map(renderCard)}</div>
        </div>
      </div>}
      <div className={styles.evidence} aria-label="当前指数来源与时点">
        <strong>查阅 · {reading.name}<span>{reading.quote ? sourceLabel(reading.quote.source) : "本轮未返回"}</span></strong>
        <dl><div><dt>源时刻</dt><dd>{clock(reading.quote?.data_timestamp)}</dd></div><div><dt>接收时刻</dt><dd>{clock(reading.quote?.received_at)}</dd></div></dl>
      </div>
    </div>
  );
});
