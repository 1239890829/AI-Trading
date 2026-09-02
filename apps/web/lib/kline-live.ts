import type { Kline, Quote } from "@/types/market";

/** quote.data_timestamp（UTC ISO）→ 北京时间 YYYY-MM-DD；缺失/非法返回空串。 */
function bjDate(ts: string | null | undefined): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" });
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

/** quote.data_timestamp（UTC ISO）→ 北京时间 HH:MM；缺失/非法返回空串。
 *  非法日期必须返回空串而不是 "Inval" 之类的垃圾串——否则两个非法时间戳
 *  会"相等"并误触发同分钟合成（kline-live.test.ts 跨分钟用例的教训）。 */
function bjHHMM(ts: string | null | undefined): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("sv-SE", { timeZone: "Asia/Shanghai", hour12: false }).slice(0, 5);
}

/**
 * 盘中分时实时合成：用 WS 最新 quote 更新最后一根分钟点（对标 K 线合成）。
 *
 * 分时数据源粒度是分钟级、走 60s REST 校准；不合成的话曲线右端点一分钟才动
 * 一次，与 1s 的列表/K线不同步。价格与累计量（quote.volume 与 cum_volume 同为
 * 股，口径依据见 mergeQuoteIntoBars 注释）跟随 WS。
 *
 * 同分钟：原地更新价格/累计量；均价线用 quote.amount/quote.volume 精算
 * （两者同为全日累计口径，实测与后端 avg 偏差 <0.01），不臆造。
 * 跨分钟（刚过整分、REST 还没补点）：**追加点**而不是放弃——原实现跨分钟
 * 返回 null，整分钟内右端点静止 50s+（2026-09-02 用户反馈"分时不及时"）。
 * 分钟量 = quote.volume − 上一点 cum_volume（负值截 0，源快照竞态防护）。
 * 间隙 >2 分钟（午休/停牌/断流）不追：臆造中间点比缺点更误导，交给 60s 校准。
 * 追加点的 ts 由 quote 时间截秒（UTC ISO），60s 校准拉到官方点后整体覆盖。
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
  const qHHMM = bjHHMM(quote.data_timestamp);
  const lastHHMM = bjHHMM(last.ts);
  if (!qHHMM || !lastHHMM) return null;

  if (qHHMM === lastHHMM) {
    const cumVolume = quote.volume ?? last.cum_volume;
    if (last.price === price && last.cum_volume === cumVolume) return null; // 无变化不重渲染
    const next = { ...last, price, cum_volume: cumVolume };
    return [...points.slice(0, -1), next] as T[];
  }

  // 跨分钟：quote 分钟晚于最后点 ≤2 分钟才追加（快照竞态/断流防护）
  const qMin = Number(qHHMM.slice(0, 2)) * 60 + Number(qHHMM.slice(3, 5));
  const lastMin = Number(lastHHMM.slice(0, 2)) * 60 + Number(lastHHMM.slice(3, 5));
  if (qMin <= lastMin || qMin - lastMin > 2) return null;
  const q = quote as Quote & { amount?: number | null };
  const cumVolume = quote.volume ?? null;
  const cumAmount = q.amount ?? null;
  const avg = cumVolume != null && cumVolume > 0 && cumAmount != null && cumAmount > 0 ? +(cumAmount / cumVolume).toFixed(3) : (last as { avg?: number }).avg ?? null;
  const prevCum = (last as { cum_volume?: number | null }).cum_volume ?? null;
  const minuteVol = cumVolume != null && prevCum != null ? Math.max(cumVolume - prevCum, 0) : null;
  const slotTs = new Date(quote.data_timestamp as string);
  if (isNaN(slotTs.getTime())) return null;
  slotTs.setSeconds(0, 0);
  const next = { ...last, ts: slotTs.toISOString(), price, volume: minuteVol, cum_volume: cumVolume, avg } as unknown as T;
  return [...points, next];
}
