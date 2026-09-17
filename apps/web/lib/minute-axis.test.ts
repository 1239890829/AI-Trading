// @vitest-environment node
import { describe, expect, it } from "vitest";
import { computeMinuteAxis } from "./minute-axis";

describe("computeMinuteAxis 分时图纵轴区间", () => {
  it("限制模式：锚定名义 ±lim，昨收居中、刻度恰为 ±lim", () => {
    const a = computeMinuteAxis({ prevClose: 10, limitPct: 10, prices: [10.1, 9.95, 10.3] });
    expect(a.min).toBeCloseTo(9, 6);
    expect(a.max).toBeCloseTo(11, 6);
    expect(a.pctBand).toBe(10);
    expect(a.outOfBand).toBe(false);
  });

  it("限制模式：主板 ST 并轨后 ±10（不再是 ±5）", () => {
    const a = computeMinuteAxis({ prevClose: 2.6, limitPct: 10, prices: [2.69] });
    expect(a.pctBand).toBe(10);
  });

  it("回退模式（limitPct=null）：以当日波幅对称", () => {
    const a = computeMinuteAxis({ prevClose: 100, limitPct: null, prices: [101, 99] });
    expect(a.min).toBeCloseTo(99, 6);
    expect(a.max).toBeCloseTo(101, 6);
    expect(a.pctBand).toBeCloseTo(1, 6);
  });

  it("回归：价格序列含 null 不得毒化区间（曾放大到 ±100%）", () => {
    // 旧实现：`null < Infinity === true` → lo 被赋成 null → |null - 10| = 10
    // → span 由真实的 0.1 被放大成 10 → 左轴刻度出现 ±100%。
    const a = computeMinuteAxis({ prevClose: 10, limitPct: null, prices: [9.9, 10.1, null] });
    expect(a.pctBand).toBeCloseTo(1, 6);
    expect(a.min).toBeCloseTo(9.9, 6);
    expect(a.max).toBeCloseTo(10.1, 6);
  });

  it("回归：全空价格序列退回 ±0.5% 地板，不得放大", () => {
    const a = computeMinuteAxis({ prevClose: 10, limitPct: null, prices: [null, undefined] });
    expect(a.min).toBeCloseTo(9.95, 6);
    expect(a.max).toBeCloseTo(10.05, 6);
    expect(a.pctBand).toBeCloseTo(0.5, 6);
  });

  it("竞价点纳入极值（回退模式防裁剪）", () => {
    const a = computeMinuteAxis({ prevClose: 10, limitPct: null, prices: [10.01], auctionPrice: 9.5 });
    expect(a.min).toBeCloseTo(9.5, 6);
  });

  it("越界数据并入区间（不再裁到图外），并同步放大百分比带", () => {
    // 主板名义 ±10，但真实涨到 +15%（除权/换源/昨收口径不一致）
    const a = computeMinuteAxis({ prevClose: 10, limitPct: 10, prices: [11.5] });
    expect(a.outOfBand).toBe(true);
    expect(a.max).toBeCloseTo(11.5, 6);
    expect(a.pctBand).toBeCloseTo(15, 6); // 左右轴不脱锚
  });

  it("越界只发生在真实数据超出时（带内数据保持精确 ±lim）", () => {
    const inside = computeMinuteAxis({ prevClose: 10, limitPct: 10, prices: [10.99, 9.01] });
    expect(inside.outOfBand).toBe(false);
    expect(inside.max).toBeCloseTo(11, 6);
    expect(inside.min).toBeCloseTo(9, 6);
  });

  it("低价股跌停：下沿不为负", () => {
    const a = computeMinuteAxis({ prevClose: 0.85, limitPct: 10, prices: [0.77] });
    expect(a.min).toBeGreaterThanOrEqual(0);
    expect(a.min).toBeCloseTo(0.765, 6);
  });

  it("指数（limitPct=null）+ 非数值混合：不产生 NaN（0.5% 地板生效）", () => {
    const a = computeMinuteAxis({ prevClose: 3000, limitPct: null, prices: [3010, null, 2990] });
    expect(Number.isNaN(a.min)).toBe(false);
    expect(Number.isNaN(a.pctBand)).toBe(false);
    // 实际波幅 10 元 < 3000×0.5%=15 元地板 → 取地板
    expect(a.pctBand).toBeCloseTo(0.5, 6);
  });
});
