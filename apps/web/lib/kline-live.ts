import type { Kline, Quote } from "@/types/market";
import { bjDate, bjHHMM } from "@/lib/format";
import { isContinuousSession } from "@/lib/market-hours";

// bjDate / bjHHMM 已收口到 lib/format（2026-09-11 冗余清理，此前两处逐字节同体）

/** 北京 HH:MM → 当日分钟数；非法（"NaN"/空）返回 -1。 */
function hhmmToMin(hhmm: string): number {
  const h = Number(hhmm.slice(0, 2));
  const m = Number(hhmm.slice(3, 5));
  if (!Number.isFinite(h) || !Number.isFinite(m) || hhmm.length !== 5) return -1;
  return h * 60 + m;
}

// 连续竞价判定已收口到 lib/market-hours 的 `isContinuousSession`
// （2026-09-12 评审 R-2：本文件原有一份 09:30-11:30/13:00-15:00 的私有实现，
// 与 market-hours 的宽松口径 09:15-11:35/12:55-15:15 构成「同一问题两个答案」。
// 两者意图不同**不可合并**，但边界必须同源——现由 market-hours 单点持有。）

interface MinuteLivePoint {
  volume_basis?: "observed_only";
  ts: string;
  price: number;
  source?: string;
  volume?: number | null;
  cum_volume?: number | null;
  cum_amount?: number | null;
  avg?: number | null;
  minute_amount?: number | null;
  /** Only a same-response source quote can prove a forming interval's end label. */
  observation_ts?: string | null;
  time_label?: "interval_end" | null;
}

function nonnegative(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function sameSource(a: { source?: string }, b: { source?: string }): boolean {
  return typeof a.source === "string" && a.source.trim() !== "" && a.source === b.source;
}

/** A receive clock and a retained quote cannot establish a new source observation. */
export function hasTrustedQuoteObservation(quote: Quote | null | undefined): quote is Quote & {data_timestamp: string; price: number} {
  return !!quote && (quote.quality === "high" || quote.quality === "medium")
    && typeof quote.source === "string" && quote.source.trim().length > 0
    && typeof quote.data_timestamp === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(quote.data_timestamp)
    && Number.isFinite(Date.parse(quote.data_timestamp))
    && typeof quote.price === "number" && Number.isFinite(quote.price) && quote.price > 0;
}

function adjacentMinutes(previous: MinuteLivePoint | undefined, point: MinuteLivePoint): boolean {
  if (previous?.volume_basis === "observed_only" || point.volume_basis === "observed_only") return false;
  if (!previous || !bjDate(point.ts) || bjDate(previous.ts) !== bjDate(point.ts)) return false;
  // A forming end-labelled point is not a sealed cumulative baseline. This
  // applies to both the chart amount fallback and subsequent live updates.
  if (previous.time_label === "interval_end"
    && (!previous.observation_ts || new Date(previous.observation_ts).getTime() !== new Date(previous.ts).getTime())) return false;
  return new Date(point.ts).getTime() - new Date(previous.ts).getTime() === 60_000
    && isContinuousSession(hhmmToMin(bjHHMM(previous.ts)))
    && isContinuousSession(hhmmToMin(bjHHMM(point.ts)));
}

/** 元；显式合成缺值不回落REST差分，午休/缺口/跨源差额也不当作分钟额。 */
export function getMinuteAmount(point: MinuteLivePoint, previous?: MinuteLivePoint): number | null {
  if (Object.prototype.hasOwnProperty.call(point, "minute_amount")) {
    return nonnegative(point.minute_amount) ? point.minute_amount : null;
  }
  if (!adjacentMinutes(previous, point) || !previous || !sameSource(previous, point)) return null;
  return nonnegative(point.cum_amount) && nonnegative(previous.cum_amount)
    && point.cum_amount >= previous.cum_amount ? point.cum_amount - previous.cum_amount : null;
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
  if (bars.length === 0 || !hasTrustedQuoteObservation(quote)) return null;
  const price = quote.price;
  const last = bars[bars.length - 1];
  if (last.symbol !== quote.symbol || !["high", "medium"].includes(last.quality)) return null;
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

/**
 * 盘中分时实时合成：用 WS 最新 quote 更新最后一根分钟点（对标 K 线合成）。
 *
 * 分时数据源粒度是分钟级、走 60s REST 校准；不合成的话曲线右端点一分钟才动
 * 一次，与 1s 的列表/K线不同步。价格与累计量（quote.volume 与 cum_volume 同为
 * 股，口径依据见 mergeQuoteIntoBars 注释）跟随 WS。
 *
 * 时间基座只使用 quote.data_timestamp（带时区的真实源观察钟）。
 * 低质量、陈旧、无效或缺源时刻时保留已有分钟；received_at 不补造当前分钟。
 *
 * 股票均价只用同一quote的累计额/累计股数；指数没有这条均价线。
 * 同分钟量额须有同源、同日相邻REST基准，尾量与原累计差配对；缺项留空。
 * 跨分钟（刚过整分、REST 还没补点）：**追加点**而不是放弃——原实现跨分钟
 * 返回 null，整分钟内右端点静止 50s+（2026-09-02 用户反馈"分时不及时"）。
 * 跨分钟旧尾没有封口身份，累计差可能包含上一分钟余量或整个缺口。
 * 因此新点的分钟量/额留空，价格与全日累计仍更新，待REST校准恢复。
 *
 * 跨分钟追加门槛（2026-09-04 用户反馈"新线段等很久且与实际涨跌幅不对应"的
 * 根因修复）：原实现硬编码「quote 分钟 − 尾点分钟 > 2 → 放弃」——只要官方
 * 分时源滞后超过 2 分钟（腾讯 WAF 封禁、TDX 备源接管、校准连续失败均为本项目
 * 实测先例），追加通道整个死掉，曲线冻结在旧尾点、只能干等 60s 校准，而校准
 * 拉回的还是滞后数据 → 线段迟迟不出且与报价对不上。现改为**同交易时段即可
 * 追加**：上午/下午时段内（含跨午休 11:30→13:00 的相邻衔接）都允许，缺口
 * 分钟在图表上留空白断线（数据诚实，不臆造直线），官方校准追上后自然弥合。
 * 时段外的报价（竞价 09:25、盘后 15:00+ 定价成交、跨日旧数据）仍一律拒绝。
 *
 * 追加点的 ts 由 quote 时间截秒（UTC ISO），60s 校准拉到官方点后整体覆盖。
 *
 * 返回新数组（不 mutate）；无需更新返回 null。
 */
export function mergeQuoteIntoMinutes<T extends MinuteLivePoint>(
  points: T[],
  quote: Quote | null | undefined,
  isIndex = false,
): T[] | null {
  if (!hasTrustedQuoteObservation(quote)) return null;
  if (points.length === 0) {
    const clock = quote.data_timestamp;
    const hhmm = bjHHMM(clock);
    if (!clock || !hhmm || !isContinuousSession(hhmmToMin(hhmm)) || !nonnegative(quote.price) || !quote.price) return null;
    const slot = new Date(clock); slot.setSeconds(0, 0);
    const volume = nonnegative(quote.volume) ? quote.volume : null;
    const amount = nonnegative(quote.amount) ? quote.amount : null;
    const avg = !isIndex && volume != null && volume > 0 && amount != null ? amount / volume : null;
    // A price observation is not a reconstructed historical minute or minute volume.
    return [{ts: slot.toISOString(), price: quote.price, source: quote.source,
      volume: null, minute_amount: null, cum_volume: volume, cum_amount: amount,
      volume_basis: "observed_only", observation_ts: clock,
      avg: avg != null && Number.isFinite(avg) && avg > 0 ? +avg.toFixed(3) : null} as unknown as T];
  }
  const price = quote.price;
  if (price == null || !Number.isFinite(price) || price <= 0) return null;
  const last = points[points.length - 1];
  const quoteTs = quote.data_timestamp;
  const qHHMM = bjHHMM(quoteTs);
  const lastHHMM = bjHHMM(last.ts);
  const qDate = bjDate(quoteTs);
  const lastDate = bjDate(last.ts);
  if (!qHHMM || !lastHHMM || !qDate || !lastDate || qDate !== lastDate) return null;
  const qMin = hhmmToMin(qHHMM);
  const lastMin = hhmmToMin(lastHHMM);
  if (!isContinuousSession(qMin) || !isContinuousSession(lastMin)) return null;

  // Tencent can label the forming minute by its end (13:48 for a 13:47 quote).
  // Preserve that label; compare real observation times only when the provider
  // proved the same-response interval. Never infer this from received_at alone.
  const observationMs = last.observation_ts ? new Date(last.observation_ts).getTime() : NaN;
  const lastMs = new Date(last.ts).getTime();
  const markedEnd = last.time_label === "interval_end" && last.source === "tencent";
  if (markedEnd && !quote.data_timestamp) return null;
  const endLabel = markedEnd && sameSource(last, quote) && Number.isFinite(observationMs)
    && bjDate(last.observation_ts) === lastDate && lastMs - observationMs >= 0
    && lastMs - observationMs <= 60_000;
  const quoteMs = new Date(quoteTs as string).getTime();
  if (endLabel && quoteMs < observationMs) return null;
  const slotMin = endLabel ? (qMin === 690 || qMin === 900 ? qMin : qMin + 1) : qMin;
  if (slotMin < lastMin) return null;

  const comparable = sameSource(last, quote);
  const cumVolume = nonnegative(quote.volume)
    && (!comparable || !nonnegative(last.cum_volume) || quote.volume >= last.cum_volume) ? quote.volume : null;
  const cumAmount = nonnegative(quote.amount)
    && (!comparable || !nonnegative(last.cum_amount) || quote.amount >= last.cum_amount) ? quote.amount : null;
  const ratio = !isIndex && cumVolume != null && cumVolume > 0 && cumAmount != null
    ? cumAmount / cumVolume : null;
  const avg = ratio != null && Number.isFinite(ratio) && ratio > 0 ? +ratio.toFixed(3) : null;
  let volume: number | null = null;
  let minuteAmount: number | null = null;
  if (slotMin === lastMin) {
    const previous = points[points.length - 2];
    if (previous && adjacentMinutes(previous, last) && sameSource(previous, last) && comparable) {
      if (cumVolume != null && nonnegative(previous.cum_volume) && nonnegative(last.cum_volume)
        && nonnegative(last.volume) && last.cum_volume >= previous.cum_volume
        && last.volume === last.cum_volume - previous.cum_volume) {
        volume = cumVolume - previous.cum_volume;
      }
      if (cumAmount != null && nonnegative(previous.cum_amount) && nonnegative(last.cum_amount)
        && last.cum_amount >= previous.cum_amount) {
        minuteAmount = cumAmount - previous.cum_amount;
      }
    }
    if (last.price === price && (last.cum_volume ?? null) === cumVolume
      && (last.cum_amount ?? null) === cumAmount && (last.avg ?? null) === avg
      && (last.volume ?? null) === volume && getMinuteAmount(last, previous) === minuteAmount
      && last.source === quote.source && (!endLabel || last.observation_ts === quoteTs)) return null;
    const next = { ...last, price, volume, cum_volume: cumVolume, cum_amount: cumAmount,
      avg, minute_amount: minuteAmount, source: quote.source,
      ...(endLabel ? { observation_ts: quoteTs, time_label: "interval_end" as const }
        : markedEnd ? { observation_ts: null, time_label: null } : {}) };
    return [...points.slice(0, -1), next] as T[];
  }

  // 只追加最新观察点，缺口不填造价格或分钟量额。
  const slotTs = new Date(quoteTs as string);
  if (isNaN(slotTs.getTime())) return null;
  slotTs.setSeconds(0, 0);
  if (endLabel && slotMin !== qMin) slotTs.setMinutes(slotTs.getMinutes() + 1);
  const next = { ...last, ts: slotTs.toISOString(), price, volume: null, cum_volume: cumVolume,
    cum_amount: cumAmount, avg, minute_amount: null, source: quote.source,
    ...(endLabel ? { observation_ts: quoteTs, time_label: "interval_end" as const }
      : markedEnd ? { observation_ts: null, time_label: null } : {}) } as T;
  return [...points, next];
}
