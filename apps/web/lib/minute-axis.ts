/**
 * 分时图纵轴区间计算（2026-09-11 从 components/minute-chart.tsx 抽出为纯函数）。
 *
 * 抽出的理由：这段逻辑曾两次出错（2026-09-07 刻度锚定、2026-09-11 空值毒化），
 * 而它埋在图表 effect 里无法单测——只能靠渲染后读 `__minuteRange` 挂点验证。
 *
 * 价格/均价/竞价、可见比较线与昨收共同定域，每侧保留 4% 留白。
 * 横盘总域至少约 0.2% 且容纳两个 0.01 价格刻度；不把缺失数据画成波动。
 * 涨跌停价只核个股越界，不强行撑域；比较指数不冒充个股价格。
 *
 * ⚠️ 两个必须守住的不变量：
 * 1. **非数值价格不得参与极值**。JS 把 `null` 强转为 0 参与比较，而
 *    `null < Infinity === true`，于是 `lo` 会被赋成 `null`；随后
 *    `Math.abs(null - prevClose) === prevClose` → 区间被放大到 ±100%
 *    （用户看到的「刻度出现百分之十几甚至几十」）。
 * 2. **越界数据不得被裁掉**。限制模式若只认名义带，真实越界行情（除权/换源/
 *    昨收口径不一致）会被 autoscale 裁到图外——用户以为「没有异常」，实为数据被
 *    判无物。百分比上下界由最终价格域逐端派生，不另设对称轴。
 */

export interface MinuteAxis {
  /** 右轴（价格）区间下沿 */
  min: number;
  /** 右轴（价格）区间上沿 */
  max: number;
  /** 左轴上下界与非对称价格域逐端对应。 */
  pctMin: number;
  pctMax: number;
  /** 最大绝对涨跌幅，仅作摘要，不用作对称坐标域。 */
  pctBand: number;
  /** 个股价格/均价/竞价越出实际限价或名义参考；比较线不参与。 */
  outOfBand: boolean;
}

/** 价格须为正有限数；原始异常由调用方保留并披露。 */
export function isPositivePrice(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v) && v > 0;
}

/**
 * 左轴百分比标签：固定两位且把不足半个最小刻度的浮点噪声归零。
 *
 * lightweight-charts 4.2.x 的 `percent` formatter 把 `precision` 当作
 * priceScale，而不是“小数位数”；传 precision=2 实际只显示 1 位小数，
 * 低波动指数会把多个不同 tick 压成相同 0.1%/0.0%，并可产生 −0.0%。
 */
export function formatMinutePercentTick(value: number): string {
  if (!Number.isFinite(value)) return "--";
  const normalized = Math.abs(value) < 0.005 ? 0 : value;
  const sign = normalized < 0 ? "−" : "";
  return `${sign}${Math.abs(normalized).toFixed(2)}%`;
}

/** Price/percent axes share a canvas with volume. Do not label volume padding as prices. */
export function formatMinuteAxisTick(value: number, min: number, max: number, percent = false): string {
  const tolerance = Math.max(1, Math.abs(min), Math.abs(max)) * 1e-9;
  if (!Number.isFinite(value) || value < min - tolerance || value > max + tolerance) return "";
  return percent ? formatMinutePercentTick(value) : value.toFixed(2);
}

export type MinuteLimitPriceState = "both" | "upper-only" | "lower-only" | "none";

/** 实际涨跌停价四态；0/-1/null/NaN 都是“未提供”，不得拿名义比例补造。 */
export function minuteLimitPriceState(upperPrice: unknown, lowerPrice: unknown): MinuteLimitPriceState {
  const upper = isPositivePrice(upperPrice);
  const lower = isPositivePrice(lowerPrice);
  if (upper && lower) return "both";
  if (upper) return "upper-only";
  if (lower) return "lower-only";
  return "none";
}

export function computeMinuteAxis(opts: {
  prevClose: number;
  limitPct: number | null;
  prices: unknown[];
  /** 仅用于显示域的可见归一化比较线，不参与个股越界判断。 */
  comparisonPrices?: unknown[];
  auctionPrice?: unknown;
  upperPrice?: unknown;
  lowerPrice?: unknown;
}): MinuteAxis {
  const { prevClose, limitPct } = opts;
  if (!isPositivePrice(prevClose)) throw new RangeError("Invalid minute reference price");
  const lim = isPositivePrice(limitPct) ? limitPct : null;
  const result = (min: number, max: number, outOfBand: boolean): MinuteAxis => {
    const pctMin = (min / prevClose - 1) * 100;
    const pctMax = (max / prevClose - 1) * 100;
    return { min, max, pctMin, pctMax, pctBand: Math.max(Math.abs(pctMin), Math.abs(pctMax)), outOfBand };
  };

  let hi = prevClose;
  let lo = prevClose;
  let stockHi = -Infinity;
  let stockLo = Infinity;
  const consider = (v: unknown) => {
    if (!isPositivePrice(v)) return;
    if (v > hi) hi = v;
    if (v < lo) lo = v;
  };
  const considerStock = (v: unknown) => {
    if (!isPositivePrice(v)) return;
    stockHi = Math.max(stockHi, v);
    stockLo = Math.min(stockLo, v);
    consider(v);
  };
  for (const p of opts.prices ?? []) considerStock(p);
  considerStock(opts.auctionPrice);
  for (const p of opts.comparisonPrices ?? []) consider(p);
  const limUp = isPositivePrice(opts.upperPrice) && opts.upperPrice >= prevClose ? opts.upperPrice
    : lim !== null ? prevClose * (1 + lim / 100) : null;
  const limDn = isPositivePrice(opts.lowerPrice) && opts.lowerPrice <= prevClose ? opts.lowerPrice
    : lim !== null ? Math.max(prevClose * (1 - lim / 100), 0) : null;
  const beyond = (limUp !== null && stockHi > limUp) || (limDn !== null && stockLo < limDn);

  const rawSpan = hi - lo;
  const span = Math.max(rawSpan, prevClose * 0.002, 0.02);
  const extra = (span - rawSpan) / 2;
  const padding = span * 0.04;
  // A very low positive reference must not give a zero/negative lower price.
  const min = Math.max(lo - extra - padding, lo / 2);
  const max = Math.max(hi + extra + padding, min + span * 1.08);
  return result(min, max, beyond);
}
