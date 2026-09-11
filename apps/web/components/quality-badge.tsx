import { qualityLabel } from "@/lib/format";
import type { Quality } from "@/types/market";

const STYLES: Record<Quality, string> = {
  high: "text-zinc-600 dark:text-zinc-400",
  medium: "text-sky-700 dark:text-sky-400",
  low: "text-amber-800 dark:text-amber-400",
  stale: "text-orange-800 dark:text-orange-400",
  invalid: "text-red-700 dark:text-red-400",
};

/** quality_reasons 的英文 key → 展示中文（tooltip 用）。 */
const REASON_LABELS: Record<string, string> = {
  off_session: "非交易时段",
  market_closed: "休市——展示最近交易日数据",
  refresh_failed: "行情刷新失败",
  missing_price: "缺最新价",
  non_positive_price: "价格非正",
  high_below_low: "最高价低于最低价",
  price_above_high: "价格高于最高价",
  price_below_low: "价格低于最低价",
  change_pct_mismatch: "涨跌幅与昨收不匹配",
  timestamp_in_future: "数据时间戳在未来",
  time_regress: "数据时间倒退",
  negative_volume: "成交量为负",
  negative_amount: "成交额为负",
  invalid_symbol: "代码非法",
  unset_high_low: "最高/最低未建立（开盘初源形态，降级观察）",
  empty_order_book: "盘口为空",
};

export function QualityBadge({ quality, reasons }: { quality: Quality; reasons?: string[] }) {
  // 休市（QuoteHub 盘外标记 market_closed）：常态而非故障，展示灰色"休市"，
  // 与刷新失败（橙"过期"）区分开——2026-09-01 盘前误标"可疑/非法"修复的展示层。
  const closed = quality === "stale" && !!reasons?.includes("market_closed");
  const title = reasons && reasons.length > 0 ? reasons.map((r) => REASON_LABELS[r] ?? r).join("; ") : undefined;
  if (closed) {
    return (
      <span title={title} className="whitespace-nowrap rounded px-1.5 py-0.5 text-xs text-zinc-600 dark:text-zinc-400">
        休市
      </span>
    );
  }
  const flag = quality !== "high";
  return (
    <span
      title={title}
      className={`rounded px-1.5 py-0.5 text-xs ${flag ? "bg-amber-500/10" : ""} ${STYLES[quality]}`}
    >
      {qualityLabel(quality)}
    </span>
  );
}
