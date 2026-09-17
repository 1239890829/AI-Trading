/**
 * 分时图纵轴区间计算（2026-09-11 从 components/minute-chart.tsx 抽出为纯函数）。
 *
 * 抽出的理由：这段逻辑曾两次出错（2026-09-07 刻度锚定、2026-09-11 空值毒化），
 * 而它埋在图表 effect 里无法单测——只能靠渲染后读 `__minuteRange` 挂点验证。
 *
 * 两种模式：
 * - **限制模式**（limitPct 有效）：以名义 ±lim 为参考，保留真实越界点。
 * - **回退模式**（指数 / 识别不出板块的代码，如 ETF·LOF·可转债）：以当日波幅对称，
 *   0.5% 地板防抖（横盘日轴不塌成一条线）。
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
  /** 左轴上下界与价格域逐端对应；越界时可以不对称。 */
  pctMin: number;
  pctMax: number;
  /** 最大绝对涨跌幅，仅作摘要，不用作对称坐标域。 */
  pctBand: number;
  /** 限制模式下真实行情越出名义带 */
  outOfBand: boolean;
}

/** 价格须为正有限数；原始异常由调用方保留并披露。 */
export function isPositivePrice(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v) && v > 0;
}

export function computeMinuteAxis(opts: {
  prevClose: number;
  limitPct: number | null;
  prices: unknown[];
  auctionPrice?: unknown;
}): MinuteAxis {
  const { prevClose, limitPct } = opts;
  if (!isPositivePrice(prevClose)) throw new RangeError("Invalid minute reference price");
  const lim = isPositivePrice(limitPct) ? limitPct : null;
  const result = (min: number, max: number, outOfBand: boolean): MinuteAxis => {
    const pctMin = (min / prevClose - 1) * 100;
    const pctMax = (max / prevClose - 1) * 100;
    return { min, max, pctMin, pctMax, pctBand: Math.max(Math.abs(pctMin), Math.abs(pctMax)), outOfBand };
  };

  let hi = -Infinity;
  let lo = Infinity;
  const consider = (v: unknown) => {
    if (!isPositivePrice(v)) return;
    if (v > hi) hi = v;
    if (v < lo) lo = v;
  };
  for (const p of opts.prices ?? []) consider(p);
  consider(opts.auctionPrice);
  const hasExtrema = Number.isFinite(hi) && Number.isFinite(lo);

  if (lim != null) {
    const limUp = prevClose * (1 + lim / 100);
    const limDn = Math.max(prevClose * (1 - lim / 100), 0);
    const beyond = hasExtrema && (hi > limUp || lo < limDn);
    // 常规情形直接返回名义带：pctBand 恒等于 lim（不经过浮点换算，
    // 否则 (11/10-1)*100 会得到 10.000000000000002，刻度出现尾差）
    if (!beyond) {
      return { ...result(limDn, limUp, false), pctBand: lim };
    }
    const min = Math.min(limDn, lo);
    const max = Math.max(limUp, hi);
    return result(min, max, true);
  }

  // 回退模式：无有效极值时退回 ±0.5% 地板 —— 绝不因缺数据把轴拉到 ±100%
  const span = hasExtrema ? Math.max(Math.abs(hi - prevClose), Math.abs(lo - prevClose)) : 0;
  const half = Math.max(span, prevClose * 0.005);
  const min = Math.max(prevClose - half, 0);
  const max = prevClose + half;
  return result(min, max, false);
}
