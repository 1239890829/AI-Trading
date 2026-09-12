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

/**
 * 盘中分时实时合成：用 WS 最新 quote 更新最后一根分钟点（对标 K 线合成）。
 *
 * 分时数据源粒度是分钟级、走 60s REST 校准；不合成的话曲线右端点一分钟才动
 * 一次，与 1s 的列表/K线不同步。价格与累计量（quote.volume 与 cum_volume 同为
 * 股，口径依据见 mergeQuoteIntoBars 注释）跟随 WS。
 *
 * 时间基座：优先 quote.data_timestamp（源报价时间）；**缺失/非法回退
 * received_at**（QuoteHub 收到时刻，盘中 ≈ 当前时间）——部分源/降级路径不带
 * 报价时间戳，没有回退时 merge 恒返回 null，分时在两次校准之间完全冻结。
 *
 * 同分钟：原地更新价格/累计量；均价线用 quote.amount/quote.volume 精算
 * （两者同为全日累计口径，实测与后端 avg 偏差 <0.01），不臆造。
 * 跨分钟（刚过整分、REST 还没补点）：**追加点**而不是放弃——原实现跨分钟
 * 返回 null，整分钟内右端点静止 50s+（2026-09-02 用户反馈"分时不及时"）。
 * 分钟量 = quote.volume − 上一点 cum_volume（负值截 0，源快照竞态防护）。
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
export function mergeQuoteIntoMinutes<T extends { ts: string; price: number; cum_volume?: number | null }>(
  points: T[],
  quote: Quote | null | undefined
): T[] | null {
  if (points.length === 0 || !quote) return null;
  const price = quote.price;
  if (price == null || price <= 0) return null;
  const last = points[points.length - 1];
  const qHHMM = bjHHMM(quote.data_timestamp || quote.received_at);
  const lastHHMM = bjHHMM(last.ts);
  if (!qHHMM || !lastHHMM) return null;

  if (qHHMM === lastHHMM) {
    const cumVolume = quote.volume ?? last.cum_volume;
    if (last.price === price && last.cum_volume === cumVolume) return null; // 无变化不重渲染
    const next = { ...last, price, cum_volume: cumVolume };
    return [...points.slice(0, -1), next] as T[];
  }

  // 跨分钟：同交易日 + 双方都落在连续交易时段内才追加。
  // （旧门槛「间隔 >2 分钟放弃」在官方分时源滞后时把合成通道整个冻死——
  // 2026-09-04 受控实验复现：WS quote 每 1s 正常推送，曲线静止 6 分钟不动。）
  const qDate = bjDate(quote.data_timestamp || quote.received_at);
  const lastDate = bjDate(last.ts);
  if (!qDate || !lastDate || qDate !== lastDate) return null;
  const qMin = hhmmToMin(qHHMM);
  const lastMin = hhmmToMin(lastHHMM);
  if (qMin <= lastMin) return null;
  if (!isContinuousSession(qMin) || !isContinuousSession(lastMin)) return null;
  const q = quote as Quote & { amount?: number | null };
  const cumVolume = quote.volume ?? null;
  const cumAmount = q.amount ?? null;
  const avg = cumVolume != null && cumVolume > 0 && cumAmount != null && cumAmount > 0 ? +(cumAmount / cumVolume).toFixed(3) : (last as { avg?: number }).avg ?? null;
  const prevCum = (last as { cum_volume?: number | null }).cum_volume ?? null;
  const minuteVol = cumVolume != null && prevCum != null ? Math.max(cumVolume - prevCum, 0) : null;
  const slotTs = new Date((quote.data_timestamp || quote.received_at) as string);
  if (isNaN(slotTs.getTime())) return null;
  slotTs.setSeconds(0, 0);
  const next = { ...last, ts: slotTs.toISOString(), price, volume: minuteVol, cum_volume: cumVolume, avg } as unknown as T;
  return [...points, next];
}
