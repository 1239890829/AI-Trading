import { qualityLabel, shouldShowQualityBadge } from "@/lib/format";
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
  quote_age_exceeded: "报价事件时间已超出实时窗口，展示最近可信值",
  index_batch_missing: "本轮未返回该指数，展示旧行情",
  missing_price: "缺最新价",
  non_positive_price: "价格非正",
  high_below_low: "最高价低于最低价",
  price_above_high: "价格高于最高价",
  price_below_low: "价格低于最低价",
  change_pct_mismatch: "涨跌幅与昨收不匹配",
  timestamp_in_future: "数据时间戳在未来",
  time_regress: "数据时间倒退",
  source_time_regress_ignored: "源返回晚到旧行情，保留最近可信值",
  source_time_unknown_ignored: "源行情缺源时间，保留最近可信值",
  source_invalid_ignored: "源返回非法行情，保留最近可信值",
  source_missing_price_ignored: "源本轮缺最新价，保留最近可信值",
  source_identity_duplicate_ignored: "源返回重复标的身份，本轮未接纳",
  source_identity_market_mismatch_ignored: "源返回标的市场身份不一致，本轮未接纳",
  source_identity_unexpected_ignored: "源返回未登记指数身份，本轮未接纳",
  source_identity_unrequested_ignored: "源返回未请求标的，本轮未接纳",
  negative_volume: "成交量为负",
  negative_amount: "成交额为负",
  invalid_symbol: "代码非法",
  unset_high_low: "最高/最低未建立（开盘初源形态，降级观察）",
  empty_order_book: "盘口为空",
};

export function QualityBadge({ quality, reasons }: { quality?: Quality | null; reasons?: string[] }) {
  // 可见性策略的**唯一决策点**在 `shouldShowQualityBadge`（2026-09-14 口径统一）：
  // 组件自门控，调用点一律直接渲染，不得再自己加 `isHardQuality(...) &&`——
  // 那正是此前「盘面显示正常 / 工作台不显示」漂移的成因。
  // `!quality` 先行只为**类型收窄**（策略判据本身已能处理缺失值，见 shouldShowQualityBadge）。
  if (!quality || !shouldShowQualityBadge(quality)) return null;
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
