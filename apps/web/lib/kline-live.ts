import type { Kline, Quote } from "@/types/market";

/** quote.data_timestamp（UTC ISO）→ 北京时间 YYYY-MM-DD；缺失返回空串。 */
function bjDate(ts: string | null | undefined): string {
  if (!ts) return "";
  return new Date(ts).toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" });
}

/**
 * 盘中 K 线实时合成：用 WS 最新 quote 更新最后一根日 K（当日 bar）。
 *
 * 为什么不整根重拉：K 线 REST 一次 120 根，实时性靠轮询最快也只能到请求间隔；
 * 而 quote 走 WS 秒级推送（QuoteHub poll_interval 节奏），拿它合成最后一根 bar
 * 是行情软件的标准做法——历史 bar 来自 REST（60s 校准防漂移），当日 bar 来自 WS。
 *
 * 口径依据（2026-08-31 实抓核对）：
 * - 腾讯日 K volume = 手×100 = 股，quote.volume = 手×100 = 股，口径一致，直接覆盖；
 * - 腾讯日 K（fqkline）不带 amount，合成时保持 last.amount，避免"成交额副图
 *   只有最后一根有值"的孤立柱；
 * - 前复权（qfq）以最新价为基准，最新 bar 用真实价合成不产生复权偏差。
 *
 * 返回新数组（不 mutate）；无需更新（盘后价格未动/跨日/无价）返回 null。
 */
export function mergeQuoteIntoBars(bars: Kline[], quote: Quote | null | undefined): Kline[] | null {
  if (bars.length === 0 || !quote) return null;
  const price = quote.price;
  if (price == null || price <= 0) return null;
  const last = bars[bars.length - 1];
  // quote 与最后一根 bar 不是同一交易日（隔夜/停牌/新交易日尚未出 bar）不合成，
  // 否则会把今天的价画到上一根 bar 上
  const qDate = bjDate(quote.data_timestamp);
  if (!qDate || qDate !== last.ts.slice(0, 10)) return null;

  const high = Math.max(last.high ?? price, price);
  const low = Math.min(last.low ?? price, price);
  const volume = quote.volume ?? last.volume;
  if (last.close === price && last.high === high && last.low === low && last.volume === volume) {
    return null; // 无变化（盘后/未开盘/quote 未动），不触发重渲染
  }
  const next: Kline = { ...last, close: price, high, low, volume };
  return [...bars.slice(0, -1), next];
}

/** quote.data_timestamp（UTC ISO）→ 北京时间 HH:MM；缺失返回空串。 */
function bjHHMM(ts: string | null | undefined): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString("sv-SE", { timeZone: "Asia/Shanghai", hour12: false }).slice(0, 5);
}

/**
 * 盘中分时实时合成：用 WS 最新 quote 更新最后一根分钟点（对标 K 线合成）。
 *
 * 分时数据源粒度是分钟级、走 60s REST 校准；不合成的话曲线右端点一分钟才动
 * 一次，与 1s 的列表/K线不同步。价格与累计量（quote.volume 与 cum_volume 同为
 * 股，口径依据见 mergeQuoteIntoBars 注释）跟随 WS；均价线由后端口径算出，
 * 不在端上臆造，保持 last.avg。跨分钟不新增点（60s 校准负责补点防漂移）。
 *
 * 返回新数组（不 mutate）；无需更新返回 null。
 */
export function mergeQuoteIntoMinutes<T extends { ts: string; price: number; cum_volume?: number | null }>(
  points: T[],
  quote: Quote | null | undefined
): T[] | null {
  if (points.length === 0 || !quote) return null;
  const price = quote.price;
  if (price == null || price <= 0) return null;
  const last = points[points.length - 1];
  // quote 与最后一个分钟点不在同一分钟（刚跨分钟、REST 还没补点）不合成
  const qHHMM = bjHHMM(quote.data_timestamp);
  const lastHHMM = bjHHMM(last.ts);
  if (!qHHMM || qHHMM !== lastHHMM) return null;

  const cumVolume = quote.volume ?? last.cum_volume;
  if (last.price === price && last.cum_volume === cumVolume) return null; // 无变化不重渲染
  const next = { ...last, price, cum_volume: cumVolume };
  return [...points.slice(0, -1), next] as T[];
}
