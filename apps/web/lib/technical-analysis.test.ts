/** 技术分析引擎测试：MA/EMA 数学正确性 + analyze 多因子共振判定 + 样本边界。 */
import { describe, expect, it } from "vitest";
import { analyze, calcEMA, calcMA } from "./technical-analysis";
import type { Bar } from "./technical-analysis";

function maOf(values: number[], n: number): number {
  const win = values.slice(-n);
  return win.reduce((s, v) => s + v, 0) / n;
}

describe("calcMA", () => {
  it("computes simple moving average", () => {
    const closes = [1, 2, 3, 4, 5];
    const ma = calcMA(closes, 3);
    expect(ma[2]).toBe(2);
    expect(ma[4]).toBe(4);
    expect(ma[0]).toBeNull(); // 窗口不足
  });

  it("matches window mean for large series", () => {
    const closes = Array.from({ length: 60 }, (_, i) => 10 + i * 0.5);
    const ma = calcMA(closes, 20);
    expect(Math.abs((ma[59] as number) - maOf(closes, 20))).toBeLessThan(1e-9);
  });
});

describe("calcEMA", () => {
  it("seeds with first value and converges to recent data", () => {
    const values = [10, 10, 10, 10, 20, 20, 20, 20, 20, 20];
    const ema = calcEMA(values, 3);
    expect(ema[0]).toBe(10);
    expect(ema[ema.length - 1]).toBeGreaterThan(18); // 收敛到 20
    expect(ema[ema.length - 1]).toBeLessThan(20);
  });
});

function mkBars(closes: number[]): Bar[] {
  return closes.map((c, i) => ({
    ts: `2026-${String(Math.floor(i / 21) + 1).padStart(2, "0")}-${String((i % 21) + 1).padStart(2, "0")}`,
    open: c * 0.99, high: c * 1.01, low: c * 0.98, close: c, volume: 1_000_000,
  }));
}

function trend(n: number, daily: number, base = 10): number[] {
  return Array.from({ length: n }, (_, i) => +(base * (1 + daily) ** i).toFixed(4));
}

describe("analyze", () => {
  it("returns null below 30 bars", () => {
    expect(analyze(mkBars(trend(29, 0.01)))).toBeNull();
  });

  it("uptrend: majority signals bull and MA排列 bullish", () => {
    const r = analyze(mkBars(trend(120, 0.012)))!;
    // 已知口径：bias 规则要求 bull >= bear+2，而强趋势中 KDJ/RSI 超买天然反向
    // （振荡指标 vs 趋势指标的哲学冲突），故 bias 可能是 neutral——这里锁定计数关系
    expect(r.bullCount).toBeGreaterThan(r.bearCount);
    const ma = r.signals.find((s) => s.name === "MA排列");
    expect(ma?.bias).toBe("bull");
    const macd = r.signals.find((s) => s.name === "MACD");
    expect(macd?.bias).toBe("bull");
  });

  it("downtrend: trend signals bear, oversold oscillators decayed (fly-catching guard)", () => {
    // 防飞刀对齐（与后端 tech_score 同口径）：空头排列时 KDJ/RSI 超卖降 neutral
    const r = analyze(mkBars(trend(120, -0.011)))!;
    const ma = r.signals.find((s) => s.name === "MA排列");
    expect(ma?.bias).toBe("bear");
    const ma20 = r.signals.find((s) => s.name === "MA20位置");
    expect(ma20?.bias).toBe("bear");
    for (const s of r.signals) {
      if (s.bias === "bull") {
        // 空头排列下不允许残留超卖类 bull（衰减后应为 neutral）
        expect(s.name === "KDJ" && s.detail.includes("超卖")).toBe(false);
        expect(s.name === "RSI14" && s.detail.includes("超卖")).toBe(false);
      }
    }
  });

  it("oscillator conflict: overbought KDJ/RSI stay bear even in strong uptrend", () => {
    // 趋势指标看多、振荡指标看空的冲突不应被抹平（勿为了让结论变多而"修"振荡指标）
    const r = analyze(mkBars(trend(120, 0.012)))!;
    const overshoot = r.signals.filter((s) => s.bias === "bear" && (s.name === "KDJ" || s.name === "RSI14"));
    expect(overshoot.length).toBeGreaterThan(0);
    expect(r.signals.find((s) => s.name === "MACD")?.bias).toBe("bull");
  });

  it("volume confirmation is what tips a strong uptrend over the bull threshold", () => {
    const bars = mkBars(trend(120, 0.012));
    const withVol = analyze(bars)!;
    // volume 全 null → 量价维度跳过，领先优势应回落
    const noVol = analyze(bars.map((b) => ({ ...b, volume: null })))!;
    expect(withVol.signals.some((s) => s.name === "量价")).toBe(true);
    expect(noVol.signals.some((s) => s.name === "量价")).toBe(false);
    expect(withVol.bullCount - withVol.bearCount).toBeGreaterThan(noVol.bullCount - noVol.bearCount);
  });

  describe("量价维度（防飞刀修正之三，阈值对齐后端 tech_score）", () => {
    it("放量下杀 is bear, never counted as 温和放量", () => {
      const bars = mkBars(trend(120, -0.011));
      bars[bars.length - 1].volume = 1_800_000; // 量比 1.8，落在 [1.0, 2.5]，且当日收跌
      const v = analyze(bars)!.signals.find((s) => s.name === "量价");
      expect(v?.bias).toBe("bear");
      expect(v?.detail).toContain("放量下杀");
    });

    it("温和放量上行 is bull", () => {
      const bars = mkBars(trend(120, 0.012));
      bars[bars.length - 1].volume = 1_800_000; // 量比 1.8 且当日收涨
      const v = analyze(bars)!.signals.find((s) => s.name === "量价");
      expect(v?.bias).toBe("bull");
      expect(v?.detail).toContain("温和放量上行");
    });

    it("量比显示精度不违背判定分支（回归：0.9986 曾显示成 1.00 却判为平稳）", () => {
      // 阈值沿用后端（<1.0 即量能平稳），因此量比必须显示到 3 位，
      // 否则会出现"量比 1.00 → 量能平稳"的自相矛盾文案
      const bars = mkBars(trend(120, 0.012));
      bars[bars.length - 1].volume = 998_637; // 量比 0.9986 < 1.0
      const v = analyze(bars)!.signals.find((s) => s.name === "量价");
      expect(v?.bias).toBe("neutral");
      expect(v?.detail).toContain("0.999");
      expect(v?.detail).not.toContain("1.00");
    });

    it("爆量 neutral / 显著缩量 bear", () => {
      const boom = mkBars(trend(120, 0.012));
      boom[boom.length - 1].volume = 5_000_000; // 量比 5.0
      const b = analyze(boom)!.signals.find((s) => s.name === "量价");
      expect(b?.bias).toBe("neutral");
      expect(b?.detail).toContain("爆量");

      const quiet = mkBars(trend(120, 0.012));
      quiet[quiet.length - 1].volume = 300_000; // 量比 0.3
      const q = analyze(quiet)!.signals.find((s) => s.name === "量价");
      expect(q?.bias).toBe("bear");
      expect(q?.detail).toContain("缩量");
    });
  });

  it("never emits buy/sell advice (red line 3)", () => {
    const r = analyze(mkBars(trend(120, 0.012)))!;
    const all = [r.summary, ...r.signals.map((s) => s.detail)].join(" ");
    expect(all).not.toContain("建议");
    expect(all).not.toContain("买入");
    expect(all).not.toContain("卖出");
  });
});
