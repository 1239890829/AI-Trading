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

  it("downtrend: MA排列 bearish; known gap — oversold KDJ/RSI read bull (fly-catching)", () => {
    // 已知缺陷（与后端 tech_score 的防飞刀修正不一致）：持续下跌中 KDJ/RSI 超卖
    // 计为 bull，可反超趋势信号。此处锁定现状；对齐修复需单独任务（会影响徽章行为）。
    const r = analyze(mkBars(trend(120, -0.011)))!;
    const ma = r.signals.find((s) => s.name === "MA排列");
    expect(ma?.bias).toBe("bear");
    const ma20 = r.signals.find((s) => s.name === "MA20位置");
    expect(ma20?.bias).toBe("bear");
    const kdj = r.signals.find((s) => s.name === "KDJ");
    if (kdj && kdj.detail.includes("超卖")) {
      expect(kdj.bias).toBe("bull"); // 当前行为：超卖=修复需求
    }
  });

  it("trend-following vs oscillator conflict keeps bias neutral in strong trend", () => {
    // 单边上行：KDJ/RSI 超买反向，bull 领先不足 2 → neutral（既有设计，勿"修"）
    const r = analyze(mkBars(trend(120, 0.012)))!;
    const overshoot = r.signals.filter((s) => s.bias === "bear" && (s.name === "KDJ" || s.name === "RSI14"));
    if (overshoot.length > 0) {
      expect(r.bullCount - r.bearCount).toBeLessThan(2);
    }
  });

  it("never emits buy/sell advice (red line 3)", () => {
    const r = analyze(mkBars(trend(120, 0.012)))!;
    const all = [r.summary, ...r.signals.map((s) => s.detail)].join(" ");
    expect(all).not.toContain("建议");
    expect(all).not.toContain("买入");
    expect(all).not.toContain("卖出");
  });
});
