import { describe, expect, it } from "vitest";
import { mergeQuoteIntoBars } from "@/lib/kline-live";
import type { Kline, Quote } from "@/types/market";

const baseAudit = { source: "tencent", quality: "high" as const, quality_reasons: [], received_at: "t0" };

function bar(ts: string, close: number, high: number, low: number, volume: number | null = 1_000_000): Kline {
  return { ...baseAudit, symbol: "600519", timeframe: "1d", ts, open: close - 1, close, high, low, volume };
}

function quote(price: number, bjDate: string, volume: number | null = 2_000_000): Quote {
  // 北京时间 14:30 → UTC 06:30
  return {
    ...baseAudit,
    symbol: "600519",
    price,
    volume,
    data_timestamp: `${bjDate}T06:30:00+00:00`,
  };
}

const YESTERDAY = "2026-08-28";
const TODAY = "2026-08-31";

function barsToday(): Kline[] {
  return [bar(YESTERDAY, 100, 101, 99), bar(TODAY, 105, 106, 104)];
}

describe("mergeQuoteIntoBars", () => {
  it("quote 当日价格抬高了最高价 → 更新最后一根 bar，历史 bar 原样", () => {
    const out = mergeQuoteIntoBars(barsToday(), quote(107.5, TODAY));
    expect(out).not.toBeNull();
    expect(out![0]).toEqual(barsToday()[0]);
    expect(out![1].close).toBe(107.5);
    expect(out![1].high).toBe(107.5);
    expect(out![1].low).toBe(104);
    expect(out![1].volume).toBe(2_000_000);
  });

  it("quote 当日价格低于最低价 → 下探 low；收盘价随之更新", () => {
    const out = mergeQuoteIntoBars(barsToday(), quote(103.2, TODAY));
    expect(out![1].low).toBe(103.2);
    expect(out![1].close).toBe(103.2);
    expect(out![1].high).toBe(106);
  });

  it("不 mutate 原数组", () => {
    const src = barsToday();
    mergeQuoteIntoBars(src, quote(107.5, TODAY));
    expect(src[1].close).toBe(105);
    expect(src[1].high).toBe(106);
  });

  it("盘后价格未动 → 返回 null（不触发重渲染）", () => {
    // bar close=105 high=106 low=104 volume=1e6；quote price=105 volume 同 → 无变化
    expect(mergeQuoteIntoBars(barsToday(), quote(105, TODAY, 1_000_000))).toBeNull();
  });

  it("quote 是隔夜时间戳（非当日）→ 不合成，防止昨价画上今日 bar", () => {
    expect(mergeQuoteIntoBars(barsToday(), quote(107.5, YESTERDAY))).toBeNull();
  });

  it("quote 无 data_timestamp 或无价格 → 返回 null", () => {
    expect(mergeQuoteIntoBars(barsToday(), { ...quote(107.5, TODAY), data_timestamp: null })).toBeNull();
    expect(mergeQuoteIntoBars(barsToday(), { ...quote(107.5, TODAY), price: null })).toBeNull();
    expect(mergeQuoteIntoBars(barsToday(), { ...quote(107.5, TODAY), price: 0 })).toBeNull();
  });

  it("bars 为空 → 返回 null", () => {
    expect(mergeQuoteIntoBars([], quote(107.5, TODAY))).toBeNull();
  });

  it("quote 未到达（undefined/null，WS 连接窗口期）→ 返回 null 不崩溃", () => {
    expect(mergeQuoteIntoBars(barsToday(), undefined)).toBeNull();
    expect(mergeQuoteIntoBars(barsToday(), null)).toBeNull();
  });

  it("quote.volume 缺失 → 保留原 bar volume", () => {
    const out = mergeQuoteIntoBars(barsToday(), quote(107.5, TODAY, null));
    expect(out![1].volume).toBe(1_000_000);
  });
});
