import { formatMinuteAxisTick } from "./minute-axis";
// @vitest-environment node
import { describe, expect, it } from "vitest";
import { computeMinuteAxis, formatMinutePercentTick, minuteLimitPriceState } from "./minute-axis";

describe("分时百分比标签", () => {
  it("低波动刻度固定两位且逐点可区分", () => {
    const values = [-0.12, -0.1, -0.08, -0.06, -0.04, -0.02, 0, 0.02, 0.04, 0.06, 0.08, 0.1, 0.12];
    const labels = values.map(formatMinutePercentTick);
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels).toEqual([
      "−0.12%", "−0.10%", "−0.08%", "−0.06%", "−0.04%", "−0.02%", "0.00%",
      "0.02%", "0.04%", "0.06%", "0.08%", "0.10%", "0.12%",
    ]);
  });

  it("半个最小刻度以内归零，永不输出负零", () => {
    expect(formatMinutePercentTick(-0)).toBe("0.00%");
    expect(formatMinutePercentTick(-0.0049)).toBe("0.00%");
    expect(formatMinutePercentTick(0.0049)).toBe("0.00%");
    expect(formatMinutePercentTick(-0.0051)).toBe("−0.01%");
  });
});

describe("实际限价四态", () => {
  it.each([
    [110, 90, "both"],
    [110, null, "upper-only"],
    [null, 90, "lower-only"],
    [-1, 0, "none"],
  ] as const)("%s / %s → %s", (upper, lower, state) => {
    expect(minuteLimitPriceState(upper, lower)).toBe(state);
  });
});

describe("computeMinuteAxis 分时图纵轴区间", () => {
  it.each([10, 20, 30])("小波动不再由名义限制%s或真实上下停价撑满", limitPct => {
    const a = computeMinuteAxis({prevClose: 10, limitPct, prices: [9.99, 10.01], upperPrice: 11, lowerPrice: 9});
    expect(a.min).toBeLessThan(9.99); expect(a.max).toBeGreaterThan(10.01);
    expect(a.max - a.min).toBeLessThan(0.1);
    expect(a.outOfBand).toBe(false);
  });
  it.each([10, 100, 0.85, 0.005])("横盘%s保留约千分之二总域及至少两个价格刻度，低价下沿为正", prevClose => {
    const a = computeMinuteAxis({prevClose, limitPct: 10, prices: [prevClose, null, 0, -1, Infinity, NaN], comparisonPrices: [undefined, NaN]});
    expect(a.min).toBeGreaterThan(0); expect(a.min).toBeLessThan(prevClose); expect(a.max).toBeGreaterThan(prevClose);
    expect(a.max - a.min).toBeGreaterThanOrEqual(Math.max(prevClose * 0.002, 0.02));
    expect(Number.isFinite(a.min) && Number.isFinite(a.max)).toBe(true); expect(a.outOfBand).toBe(false);
  });
  it("比较线不会隐藏个股真实越界，实际触及限价也不会被误报", () => {
    const touched = computeMinuteAxis({prevClose: 0.85, limitPct: 10, prices: [0.77, 0.94], upperPrice: 0.94, lowerPrice: 0.77});
    expect(touched.min).toBeLessThan(0.77); expect(touched.max).toBeGreaterThan(0.94); expect(touched.outOfBand).toBe(false);
    expect(computeMinuteAxis({prevClose: 10, limitPct: 10, prices: [11.5], comparisonPrices: [10]}).outOfBand).toBe(true);
  });
  it("可见指数只扩显示域，不冒充个股越出涨跌停参考", () => {
    const a = computeMinuteAxis({prevClose: 10, limitPct: 10, prices: [10.01], comparisonPrices: [12], upperPrice: 11, lowerPrice: 9});
    expect(a.max).toBeGreaterThan(12); expect(a.min).toBeLessThan(10);
    expect(a.outOfBand).toBe(false);
  });
  it("昨收仅作真实基准，不把单边成交镜像成另一侧同幅波动", () => {
    const a = computeMinuteAxis({prevClose: 4.91, limitPct: 10, prices: [4.5]});
    expect(a.min).toBeCloseTo(4.4836); expect(a.max).toBeCloseTo(4.9264);
    expect(a.pctMax).toBeLessThan(1); expect(a.pctMin).toBeLessThan(-8);
  });
  it.each([
    [10, 10, [11.5]], [10, 10, [8]], [10, 20, [13]], [10, 30, [6]],
    [3000, null, [3001, 2999]], [10, null, [25]], [0.85, 10, [0.94, 0.77]],
  ])("价格与百分比在整个区间同位置：%s/%s/%s", (prevClose, limitPct, prices) => {
    const a = computeMinuteAxis({ prevClose, limitPct, prices });
    for (const fraction of [0, 0.25, 0.5, 0.75, 1]) {
      const price = a.min + (a.max - a.min) * fraction;
      const pct = a.pctMin + (a.pctMax - a.pctMin) * fraction;
      expect(pct).toBeCloseTo((price / prevClose - 1) * 100, 8);
    }
  });

  it("零、负数与非有限价格不参与轴范围", () => {
    const a = computeMinuteAxis({ prevClose: 10, limitPct: null, prices: [10.1, 0, -1, NaN, Infinity], auctionPrice: -10 });
    expect(a.min).toBeCloseTo(9.996);
    expect(a.max).toBeCloseTo(10.104);
  });

  it.each([0, -1, NaN, Infinity])("非法参考价 %s 不生成百分比轴", (prevClose) => {
    expect(() => computeMinuteAxis({ prevClose, limitPct: 10, prices: [10] })).toThrow(RangeError);
  });

  it("有名义限制仍按实际极值与昨收留白，不强行居中", () => {
    const a = computeMinuteAxis({ prevClose: 10, limitPct: 10, prices: [10.1, 9.95, 10.3] });
    expect(a.min).toBeCloseTo(9.936, 6);
    expect(a.max).toBeCloseTo(10.314, 6);
    expect(a.pctBand).toBeCloseTo(3.14);
    expect(a.outOfBand).toBe(false);
  });

  it("ST名义限制只作参考，不把低波动域拉到整带", () => {
    const a = computeMinuteAxis({ prevClose: 2.6, limitPct: 10, prices: [2.69] });
    expect(a.max).toBeCloseTo(2.6936); expect(a.pctBand).toBeCloseTo(3.6);
  });

  it("无名义限制也按实际两侧极值留白", () => {
    const a = computeMinuteAxis({ prevClose: 100, limitPct: null, prices: [101, 99] });
    expect(a.min).toBeCloseTo(98.92, 6);
    expect(a.max).toBeCloseTo(101.08, 6);
    expect(a.pctBand).toBeCloseTo(1.08, 6);
  });

  it("回归：价格序列含 null 不得毒化区间（曾放大到 ±100%）", () => {
    // 旧实现：`null < Infinity === true` → lo 被赋成 null → |null - 10| = 10
    // → span 由真实的 0.1 被放大成 10 → 左轴刻度出现 ±100%。
    const a = computeMinuteAxis({ prevClose: 10, limitPct: null, prices: [9.9, 10.1, null] });
    expect(a.pctBand).toBeCloseTo(1.08, 6);
    expect(a.min).toBeCloseTo(9.892, 6);
    expect(a.max).toBeCloseTo(10.108, 6);
  });

  it("回归：全空价格序列只有昨收的小域，不得放大或造价格", () => {
    const a = computeMinuteAxis({ prevClose: 10, limitPct: null, prices: [null, undefined] });
    expect(a.min).toBeCloseTo(9.9892, 6);
    expect(a.max).toBeCloseTo(10.0108, 6);
    expect(a.pctBand).toBeCloseTo(0.108, 6);
  });

  it("竞价点纳入极值（回退模式防裁剪）", () => {
    const a = computeMinuteAxis({ prevClose: 10, limitPct: null, prices: [10.01], auctionPrice: 9.5 });
    expect(a.min).toBeCloseTo(9.4796, 6);
  });

  it("越界数据并入区间（不再裁到图外），并同步放大百分比带", () => {
    // 主板名义 ±10，但真实涨到 +15%（除权/换源/昨收口径不一致）
    const a = computeMinuteAxis({ prevClose: 10, limitPct: 10, prices: [11.5] });
    expect(a.outOfBand).toBe(true);
    expect(a.max).toBeCloseTo(11.56, 6);
    expect(a.pctBand).toBeCloseTo(15.6, 6); // 左右轴不脱锚
  });

  it("留白可以越过参考边界，只有真实数据超出才报越界", () => {
    const inside = computeMinuteAxis({ prevClose: 10, limitPct: 10, prices: [10.99, 9.01] });
    expect(inside.outOfBand).toBe(false);
    expect(inside.max).toBeCloseTo(11.0692, 6);
    expect(inside.min).toBeCloseTo(8.9308, 6);
  });

  it("低价股跌停：下沿不为负", () => {
    const a = computeMinuteAxis({ prevClose: 0.85, limitPct: 10, prices: [0.77] });
    expect(a.min).toBeGreaterThanOrEqual(0);
    expect(a.min).toBeCloseTo(0.7668, 6);
  });

  it("指数和非数值混合不产生NaN，也不额外扩大到旧0.5%地板", () => {
    const a = computeMinuteAxis({ prevClose: 3000, limitPct: null, prices: [3010, null, 2990] });
    expect(Number.isNaN(a.min)).toBe(false);
    expect(Number.isNaN(a.pctBand)).toBe(false);
    expect(a.pctBand).toBeCloseTo(0.36, 6);
  });
});


describe("price scale separated from volume padding", () => {
  it("does not mark an actual rounded limit price as outside the band", () => {
    const axis = computeMinuteAxis({prevClose:16.97, limitPct:10, prices:[15.27,16.73], upperPrice:18.67, lowerPrice:15.27});
    expect(axis.outOfBand).toBe(false);
    expect(axis.min).toBeCloseTo(15.202);
    expect(axis.max).toBeCloseTo(17.038);
  });
  it("hides only padding ticks and preserves genuinely observed prices beyond limits", () => {
    expect(formatMinuteAxisTick(-15, -10.02, 10.02, true)).toBe("");
    expect(formatMinuteAxisTick(-10, -10.02, 10.02, true)).toBe("−10.00%");
    const axis = computeMinuteAxis({prevClose:10, limitPct:10, prices:[8.5], lowerPrice:9, upperPrice:11});
    expect(axis.outOfBand).toBe(true);
    expect(formatMinuteAxisTick(-15, axis.pctMin, axis.pctMax, true)).toBe("−15.00%");
  });
});
