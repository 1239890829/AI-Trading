import { describe, expect, it } from "vitest";
import { mergeQuoteIntoBars, mergeQuoteIntoMinutes } from "@/lib/kline-live";
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

// ---- 分时端点实时合成（2026-09-01 秒级化配套）----

interface MinutePointFixture {
  ts: string;
  price: number;
  volume: number;
  cum_amount: number;
  cum_volume: number | null;
  avg: number;
  source: string;
}

function point(bjHHMM: string, price: number, cumVolume: number): MinutePointFixture {
  // 北京时间 → UTC（小时补零：单数字小时的 ISO 串在 V8 是 Invalid Date）
  const ts = `${TODAY}T${String((Number(bjHHMM.slice(0, 2)) - 8 + 24) % 24).padStart(2, "0")}:${bjHHMM.slice(3)}:00+00:00`;
  return { ts, price, volume: 100, cum_amount: 5_000_000, cum_volume: cumVolume, avg: 104.8, source: "tencent" };
}

function minutesFixture(): MinutePointFixture[] {
  return [point("14:28", 104.9, 900_000), point("14:29", 105.0, 1_000_000)];
}

function quoteAt(bjHHMM: string, price: number, volume: number | null = 1_100_000, amount: number | null = null): Quote {
  const hh = Number(bjHHMM.slice(0, 2));
  return {
    ...baseAudit,
    symbol: "600519",
    price,
    volume,
    ...(amount != null ? { amount } : {}),
    data_timestamp: `${TODAY}T${String((hh - 8 + 24) % 24).padStart(2, "0")}:${bjHHMM.slice(3)}:30+00:00`,
  } as Quote;
}

describe("mergeQuoteIntoMinutes", () => {
  it("同分钟 quote → 更新最后一点 price 与 cum_volume，前序点与 avg/source 原样", () => {
    const out = mergeQuoteIntoMinutes(minutesFixture(), quoteAt("14:29", 105.3));
    expect(out).not.toBeNull();
    expect(out![0]).toEqual(minutesFixture()[0]);
    expect(out![1].price).toBe(105.3);
    expect(out![1].cum_volume).toBe(1_100_000);
    expect(out![1].avg).toBe(104.8); // 均价线由后端口径算出，端上不臆造
    expect(out![1].source).toBe("tencent");
  });

  it("价格与累计量都没动 → 返回 null（不触发重渲染）", () => {
    expect(mergeQuoteIntoMinutes(minutesFixture(), quoteAt("14:29", 105.0, 1_000_000))).toBeNull();
  });

  it("跨分钟（刚跳到下一分钟、REST 未补点）→ 追加新点而非放弃（2026-09-02 及时性修复）", () => {
    const out = mergeQuoteIntoMinutes(minutesFixture(), quoteAt("14:30", 105.3));
    expect(out).not.toBeNull();
    expect(out).toHaveLength(3);
    const added = out![2];
    expect(added.price).toBe(105.3);
    expect(added.cum_volume).toBe(1_100_000);
    // 分钟量 = cum 差；quote 无 amount → avg 回退继承最后点
    expect((added as { volume?: number }).volume).toBe(100_000);
    expect((added as { avg?: number }).avg).toBe(104.8);
    // 新点 ts 是 quote 时间截秒（14:30 北京 = 06:30 UTC）
    expect(added.ts.startsWith(`${TODAY}T06:30:00`)).toBe(true);
    // 前序点原样
    expect(out![0]).toEqual(minutesFixture()[0]);
    expect(out![1]).toEqual(minutesFixture()[1]);
  });

  it("跨分钟且 quote 带 amount → avg = amount/volume 精算", () => {
    const out = mergeQuoteIntoMinutes(minutesFixture(), quoteAt("14:30", 105.3, 1_100_000, 115_830_000));
    expect(out).not.toBeNull();
    expect((out![2] as { avg?: number }).avg).toBeCloseTo(115_830_000 / 1_100_000, 3);
  });

  it("跨分钟超过 2 分钟（午休/断流）→ 不追加，交给 60s 校准", () => {
    expect(mergeQuoteIntoMinutes(minutesFixture(), quoteAt("14:33", 105.3))).toBeNull();
  });

  it("quote 分钟早于最后点（快照竞态）→ 不合成", () => {
    expect(mergeQuoteIntoMinutes(minutesFixture(), quoteAt("14:27", 105.3))).toBeNull();
  });

  it("quote.volume 缺失 → 保留原 cum_volume", () => {
    const out = mergeQuoteIntoMinutes(minutesFixture(), quoteAt("14:29", 105.3, null));
    expect(out![1].price).toBe(105.3);
    expect(out![1].cum_volume).toBe(1_000_000);
  });

  it("无时间戳/无价/非正价 → 返回 null", () => {
    expect(mergeQuoteIntoMinutes(minutesFixture(), { ...quoteAt("14:29", 105.3), data_timestamp: null })).toBeNull();
    expect(mergeQuoteIntoMinutes(minutesFixture(), { ...quoteAt("14:29", 105.3), price: null })).toBeNull();
    expect(mergeQuoteIntoMinutes(minutesFixture(), { ...quoteAt("14:29", 105.3), price: 0 })).toBeNull();
  });

  it("空数组 / quote 未到达 → 返回 null 不崩溃", () => {
    expect(mergeQuoteIntoMinutes([], quoteAt("14:29", 105.3))).toBeNull();
    expect(mergeQuoteIntoMinutes(minutesFixture(), undefined)).toBeNull();
    expect(mergeQuoteIntoMinutes(minutesFixture(), null)).toBeNull();
  });

  it("不 mutate 原数组", () => {
    const src = minutesFixture();
    mergeQuoteIntoMinutes(src, quoteAt("14:29", 105.3));
    expect(src[1].price).toBe(105.0);
    expect(src[1].cum_volume).toBe(1_000_000);
  });
});
