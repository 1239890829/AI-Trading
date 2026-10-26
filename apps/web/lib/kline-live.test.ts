// @vitest-environment node
import { describe, expect, it } from "vitest";
import { getMinuteAmount, mergeQuoteIntoBars, mergeQuoteIntoMinutes } from "@/lib/kline-live";
import type { MinutePoint } from "@/lib/api";
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
  volume: number | null;
  cum_amount: number | null;
  cum_volume: number | null;
  avg: number | null;
  minute_amount?: number | null;
  source: string;
  observation_ts?: string | null;
  time_label?: "interval_end" | null;
}

describe("腾讯有源时刻证明的分钟结束桶", () => {
  const tail = (label = "13:44", observed = "13:43:22"): MinutePointFixture => ({
    ...point(label, 15.27, 1_000_000),
    observation_ts: `${TODAY}T${observed}+08:00`, time_label: "interval_end",
  });
  const tick = (time: string, price = 15.28) => ({
    ...quoteAt(time.slice(0, 5), price), data_timestamp: `${TODAY}T${time}+08:00`,
  });
  it("同桶新报价早于结束标签也原位更新，原始label和真实源时刻分别保留", () => {
    const input = [tail()];
    const out = mergeQuoteIntoMinutes(input, tick("13:43:50"));
    expect(out).not.toBeNull();
    expect(out).toHaveLength(1);
    expect(out![0].price).toBe(15.28);
    expect(out![0].ts).toBe(input[0].ts);
    expect(out![0].observation_ts).toBe(`${TODAY}T13:43:50+08:00`);
    expect(input[0].price).toBe(15.27);
  });
  it("拒绝较旧、跨日、午休、缺源时刻和跨源报价，不借接收时刻绕过", () => {
    expect(mergeQuoteIntoMinutes([tail()], tick("13:43:20"))).toBeNull();
    expect(mergeQuoteIntoMinutes([tail()], { ...tick("13:43:50"), source: "sina" })).toBeNull();
    expect(mergeQuoteIntoMinutes([tail()], { ...tick("13:43:50"), data_timestamp: null, received_at: `${TODAY}T13:43:51+08:00` })).toBeNull();
    expect(mergeQuoteIntoMinutes([tail()], { ...tick("13:43:50"), data_timestamp: `${YESTERDAY}T13:43:50+08:00` })).toBeNull();
    expect(mergeQuoteIntoMinutes([tail("11:30", "11:29:50")], tick("12:30:00"))).toBeNull();
  });
  it("新桶继续保持结束标签，缺口留空且不继承分钟量额", () => {
    const out = mergeQuoteIntoMinutes([tail()], tick("13:46:10"))!;
    expect(out).toHaveLength(2);
    expect(out[1].ts).toBe(`${TODAY}T05:47:00.000Z`);
    expect(out[1].observation_ts).toBe(`${TODAY}T13:46:10+08:00`);
    expect(out[1].time_label).toBe("interval_end");
    expect(out[1].volume).toBeNull();
    expect(out[1].minute_amount).toBeNull();
  });
  it("午收恰好封口后，下午新桶仍保持结束标签", () => {
    const closed = mergeQuoteIntoMinutes([tail("11:30", "11:29:50")], tick("11:30:00"))!;
    const afternoon = mergeQuoteIntoMinutes(closed, tick("13:00:01"))!;
    expect(afternoon[1].ts).toBe(`${TODAY}T05:01:00.000Z`);
    expect(afternoon[1].time_label).toBe("interval_end");
    expect(afternoon[1].observation_ts).toBe(`${TODAY}T13:00:01+08:00`);
  });
  it("跨桶后再次更新不能把旧形成中累计额当作封口基准", () => {
    const input = [{ ...tail(), cum_amount: 15_000_000 }];
    const appended = mergeQuoteIntoMinutes(input, { ...tick("13:44:10"), amount: 15_001_000 })!;
    const updated = mergeQuoteIntoMinutes(appended, { ...tick("13:44:20"), amount: 15_003_057 })!;
    expect(updated[1].price).toBe(15.28);
    expect(updated[1].minute_amount).toBeNull();
    expect(updated[1].volume).toBeNull();
    expect(getMinuteAmount({ ...updated[1], minute_amount: undefined }, input[0])).toBeNull();
    const unmarkedAmount = { ...updated[1] };
    delete unmarkedAmount.minute_amount;
    expect(getMinuteAmount(unmarkedAmount, input[0])).toBeNull();
  });
  it.each([["09:31", "09:30:01", "09:30:50"], ["13:01", "13:00:01", "13:00:50"],
    ["11:30", "11:29:50", "11:30:00"], ["15:00", "14:59:50", "15:00:00"]])(
    "%s 桶接纳有证明的新源时刻，不推到非交易槽", (label, observed, latest) => {
      const out = mergeQuoteIntoMinutes([tail(label, observed)], tick(latest))!;
      expect(out).toHaveLength(1);
      expect(out[0].ts).toBe(tail(label, observed).ts);
      expect(out[0].price).toBe(15.28);
    });
});

function point(bjHHMM: string, price: number, cumVolume: number): MinutePointFixture {
  // 北京时间 → UTC（小时补零：单数字小时的 ISO 串在 V8 是 Invalid Date）
  const ts = `${TODAY}T${String((Number(bjHHMM.slice(0, 2)) - 8 + 24) % 24).padStart(2, "0")}:${bjHHMM.slice(3)}:00+00:00`;
  return { ts, price, volume: 100_000, cum_amount: cumVolume * 104.8, cum_volume: cumVolume, avg: 104.8, source: "tencent" };
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
  it("同分钟quote更新股/元统计，前序点与兼容source不变", () => {
    const out = mergeQuoteIntoMinutes(minutesFixture(), quoteAt("14:29", 105.3, 1_100_000, 115_830_000));
    expect(out).not.toBeNull();
    expect(out![0]).toEqual(minutesFixture()[0]);
    expect(out![1].price).toBe(105.3);
    expect(out![1].cum_volume).toBe(1_100_000);
    expect(out![1].avg).toBe(105.3);
    expect(out![1].source).toBe("tencent");
  });

  it("完整显示字段都没动 → 返回null保持引用稳定", () => {
    expect(mergeQuoteIntoMinutes(minutesFixture(), quoteAt("14:29", 105.0, 1_000_000, 104_800_000))).toBeNull();
  });

  it("跨分钟（刚跳到下一分钟、REST 未补点）→ 追加新点而非放弃（2026-09-02 及时性修复）", () => {
    const out = mergeQuoteIntoMinutes(minutesFixture(), quoteAt("14:30", 105.3));
    expect(out).not.toBeNull();
    expect(out).toHaveLength(3);
    const added = out![2];
    expect(added.price).toBe(105.3);
    expect(added.cum_volume).toBe(1_100_000);
    // 旧尾未封口；新quote缺金额，不继承旧分钟量或旧均价。
    expect(added.volume).toBeNull();
    expect(added.avg).toBeNull();
    expect(added.cum_amount).toBeNull();
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

  it("跨分钟超过 2 分钟（同交易时段内，官方源滞后）→ 仍追加（2026-09-04 冻结修复）", () => {
    // 旧门槛「间隔 >2 分钟放弃」在官方分时源滞后 >2min 时把合成通道整个冻死：
    // 受控实验复现 WS quote 每秒正常推送、曲线 6 分钟纹丝不动。改为同时段即追加。
    const out = mergeQuoteIntoMinutes(minutesFixture(), quoteAt("14:33", 105.3));
    expect(out).not.toBeNull();
    expect(out).toHaveLength(3);
    expect(out![2].price).toBe(105.3);
    expect(out![2].ts.startsWith(`${TODAY}T06:33:00`)).toBe(true);
  });

  it("跨午休衔接：上午尾点 11:30 + 下午 quote 13:01 → 追加", () => {
    const am = [point("11:29", 104.9, 900_000), point("11:30", 105.0, 1_000_000)];
    const out = mergeQuoteIntoMinutes(am, quoteAt("13:01", 105.3));
    expect(out).not.toBeNull();
    expect(out).toHaveLength(3);
    expect(out![2].price).toBe(105.3);
    expect(out![2].ts.startsWith(`${TODAY}T05:01:00`)).toBe(true); // 13:01 北京 = 05:01 UTC
  });

  it("午休中（12:30）与收盘后（15:01）的 quote → 时段外不追加", () => {
    const am = [point("11:29", 104.9, 900_000), point("11:30", 105.0, 1_000_000)];
    expect(mergeQuoteIntoMinutes(am, quoteAt("12:30", 105.3))).toBeNull();
    expect(mergeQuoteIntoMinutes(minutesFixture(), quoteAt("15:01", 105.3))).toBeNull();
  });

  it("quote 与尾点不是同一交易日（昨日尾点 + 今日 quote）→ 不追加", () => {
    const yesterdayTail = [{ ...point("14:29", 104.9, 900_000), ts: `${YESTERDAY}T06:29:00+00:00` }];
    expect(mergeQuoteIntoMinutes(yesterdayTail, quoteAt("14:30", 105.3))).toBeNull();
  });

  it("跨日但 HH:MM 相同也不得覆盖昨日尾点", () => {
    const yesterdayTail = [{ ...point("14:29", 104.9, 900_000), ts: `${YESTERDAY}T06:29:00+00:00` }];
    expect(mergeQuoteIntoMinutes(yesterdayTail, quoteAt("14:29", 105.3))).toBeNull();
  });

  it("缺源时刻不能用 received_at 补造当前分钟", () => {
    const q = { ...quoteAt("14:29", 105.3), data_timestamp: null, received_at: `${TODAY}T06:29:59+00:00` };
    const out = mergeQuoteIntoMinutes(minutesFixture(), q);
    expect(out).toBeNull();
  });

  it("quote 分钟早于最后点（快照竞态）→ 不合成", () => {
    expect(mergeQuoteIntoMinutes(minutesFixture(), quoteAt("14:27", 105.3))).toBeNull();
  });

  it("quote.volume缺失 → 不拿旧累计量配新价格/金额", () => {
    const out = mergeQuoteIntoMinutes(minutesFixture(), quoteAt("14:29", 105.3, null));
    expect(out![1].price).toBe(105.3);
    expect(out![1].cum_volume).toBeNull();
    expect(out![1].avg).toBeNull();
    expect(out![1].volume).toBeNull();
  });

  it("无时间戳/无价/非正价 → 返回 null", () => {
    expect(mergeQuoteIntoMinutes(minutesFixture(), { ...quoteAt("14:29", 105.3), data_timestamp: null })).toBeNull();
    expect(mergeQuoteIntoMinutes(minutesFixture(), { ...quoteAt("14:29", 105.3), price: null })).toBeNull();
    expect(mergeQuoteIntoMinutes(minutesFixture(), { ...quoteAt("14:29", 105.3), price: 0 })).toBeNull();
  });

  it("空数组 / quote 未到达 → 返回 null 不崩溃", () => {
    expect(mergeQuoteIntoMinutes([], quoteAt("14:29", 105.3))).toHaveLength(1);
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

describe("图表合成只采用可信源观察", () => {
  it.each(["low", "stale", "invalid", undefined] as const)("质量 %s 即使源钟前移也不能推进K线/分钟", quality => {
    const rejected = {...quoteAt("14:30", 107.5), quality} as Quote;
    expect(mergeQuoteIntoBars(barsToday(), rejected)).toBeNull();
    expect(mergeQuoteIntoMinutes(minutesFixture(), rejected)).toBeNull();
  });

  it.each([null, "2026-08-31T14:30:00", "2026-08-31", "invalid"])("源钟 %s 不能由接收钟补造", timestamp => {
    const rejected = {...quoteAt("14:30", 107.5), data_timestamp: timestamp, received_at: `${TODAY}T14:30:01+08:00`};
    expect(mergeQuoteIntoBars(barsToday(), rejected)).toBeNull();
    expect(mergeQuoteIntoMinutes(minutesFixture(), rejected)).toBeNull();
  });

  it.each([NaN, Infinity, -Infinity, 0])("非有效现价 %s 不进入图表", price => {
    const rejected = quoteAt("14:30", price);
    expect(mergeQuoteIntoBars(barsToday(), rejected)).toBeNull();
    expect(mergeQuoteIntoMinutes(minutesFixture(), rejected)).toBeNull();
  });

  it("缺来源或另一证券报价不能形成当前Kbar", () => {
    expect(mergeQuoteIntoBars(barsToday(), {...quote(107.5, TODAY), source: ""})).toBeNull();
    expect(mergeQuoteIntoBars(barsToday(), {...quote(107.5, TODAY), symbol: "600000"})).toBeNull();
    expect(mergeQuoteIntoMinutes(minutesFixture(), {...quoteAt("14:30", 107.5), source: " "})).toBeNull();
  });

  it("medium使用其真实源钟，但不能升级陈旧Kbar基座", () => {
    expect(mergeQuoteIntoBars(barsToday(), {...quote(107.5, TODAY), quality: "medium"})?.at(-1)?.close).toBe(107.5);
    const staleBars = barsToday().map(row => ({...row, quality: "stale" as const}));
    expect(mergeQuoteIntoBars(staleBars, quote(107.5, TODAY))).toBeNull();
    expect(mergeQuoteIntoMinutes(minutesFixture(), {...quoteAt("14:30", 107.5), quality: "medium"})?.at(-1)?.price).toBe(107.5);
  });
});

describe("分时合成的股/元与分钟窗口回归", () => {
  const snapshot = () => [
    { ...point("14:28", 104.9, 900_000), volume: 100_000, cum_amount: 94_320_000 },
    { ...point("14:29", 105, 1_000_000), volume: 100_000, cum_amount: 104_800_000 },
  ];
  const current = (time = "14:29") => quoteAt(time, 105.3, 1_100_000, 115_830_000);

  it("同分钟均价、量和累计额来自最新报价，已有分钟量不丢失", () => {
    const points = snapshot();
    const out = mergeQuoteIntoMinutes(points, current())!;
    expect(out[1]).toMatchObject({ price: 105.3, avg: 105.3, volume: 200_000,
      cum_volume: 1_100_000, cum_amount: 115_830_000, minute_amount: 21_510_000 });
    expect(points).toEqual(snapshot());
  });

  it("只有金额变化也更新均价和累计额", () => {
    const out = mergeQuoteIntoMinutes(snapshot(), quoteAt("14:29", 105, 1_000_000, 105_000_000));
    expect(out).not.toBeNull();
    expect(out![1]).toMatchObject({ avg: 105, cum_amount: 105_000_000, volume: 100_000 });
  });

  it.each(["14:30", "14:35"])("旧尾未封口，%s新点不能把累计差当成分钟量", time => {
    const out = mergeQuoteIntoMinutes(snapshot(), current(time))!;
    expect(out[2]).toMatchObject({ price: 105.3, avg: 105.3, cum_amount: 115_830_000,
      volume: null, minute_amount: null });
  });

  it("原REST已有缺口时，同分钟也没有可靠分钟量基准", () => {
    const points = snapshot();
    points[0].ts = point("14:26", 104.9, 900_000).ts;
    expect(mergeQuoteIntoMinutes(points, current())![1].volume).toBeNull();
  });

  it("尾量与原累计差不配对时，不生成精确分钟量", () => {
    const points = snapshot();
    points[1].volume = 100;
    expect(mergeQuoteIntoMinutes(points, current())![1].volume).toBeNull();
  });

  it("跨源累计覆盖不能相减，但同一报价均价仍可计算", () => {
    const points = snapshot().map(p => ({ ...p, source: "tdx_m1" }));
    expect(mergeQuoteIntoMinutes(points, current())![1]).toMatchObject({
      avg: 105.3, volume: null, cum_amount: 115_830_000, source: "tencent",
    });
  });

  it("指数累计额/量不生成指数点位均价，同分钟和跨分钟都保持空", () => {
    const points = snapshot().map(p => ({ ...p, avg: null }));
    expect(mergeQuoteIntoMinutes(points, current(), true)![1].avg).toBeNull();
    expect(mergeQuoteIntoMinutes(points, current("14:30"), true)![2].avg).toBeNull();
  });

  it("同源累计倒退保持未知，不能用截零假装没有成交", () => {
    const out = mergeQuoteIntoMinutes(snapshot(), quoteAt("14:29", 105.3, 950_000, 100_000_000))!;
    expect(out[1]).toMatchObject({ price: 105.3, volume: null, cum_volume: null,
      cum_amount: null, minute_amount: null, avg: null });
  });

  it("缺量时金额仍可独立归属，但不得拼旧量计算均价", () => {
    const out = mergeQuoteIntoMinutes(snapshot(), quoteAt("14:29", 105.3, null, 115_830_000))!;
    expect(out[1]).toMatchObject({ volume: null, cum_volume: null, avg: null,
      cum_amount: 115_830_000, minute_amount: 21_510_000 });
  });

  it("零累计与零分钟量/额合法；均价不做除零", () => {
    const points = snapshot().map(p => ({ ...p, volume: 0, cum_volume: 0, cum_amount: 0, avg: null }));
    const out = mergeQuoteIntoMinutes(points, quoteAt("14:29", 105.3, 0, 0))!;
    expect(out[1]).toMatchObject({ volume: 0, cum_volume: 0, cum_amount: 0, minute_amount: 0, avg: null });
  });

  it("累计没有增加不抹掉REST尾点已经发生的分钟量", () => {
    const out = mergeQuoteIntoMinutes(snapshot(), quoteAt("14:29", 105.3, 1_000_000, 104_800_000))!;
    expect(out[1].volume).toBe(100_000);
  });

  it("没有前一点基准，单尾点不能把全日累计当分钟量", () => {
    const out = mergeQuoteIntoMinutes([snapshot()[1]], current())!;
    expect(out[0]).toMatchObject({ price: 105.3, avg: 105.3, volume: null, minute_amount: null });
  });

  it("午休前后的累计差不归属下午一个分钟", () => {
    const points = [point("11:29", 105, 900_000), point("11:30", 105, 1_000_000)];
    const out = mergeQuoteIntoMinutes(points, current("13:00"))!;
    expect(out[2]).toMatchObject({ price: 105.3, volume: null, minute_amount: null });
  });

  it.each([NaN, Infinity, -Infinity])("非有限价%s不得进入图表", price => {
    expect(mergeQuoteIntoMinutes(snapshot(), quoteAt("14:29", price))).toBeNull();
  });

  it.each([NaN, Infinity, -1])("非法累计%s不污染均价、量柱及金额", value => {
    const out = mergeQuoteIntoMinutes(snapshot(), quoteAt("14:29", 105.3, value, value))!;
    expect(out[1]).toMatchObject({ avg: null, volume: null, cum_volume: null,
      cum_amount: null, minute_amount: null });
  });
});

describe("分钟额消费者的窗口与显式缺值", () => {
  const before = () => point("14:28", 105, 900_000);
  const tail = () => point("14:29", 105, 1_000_000);
  it("REST同源相邻额差是元，零差额也保留", () => {
    expect(getMinuteAmount(tail(), before())).toBe(10_480_000);
    expect(getMinuteAmount({ ...tail(), cum_amount: before().cum_amount }, before())).toBe(0);
  });
  it("显式合成null不能回落到跨窗口累计额差", () => {
    expect(getMinuteAmount({ ...tail(), minute_amount: null }, before())).toBeNull();
    expect(getMinuteAmount({ ...tail(), minute_amount: 123 }, before())).toBe(123);
  });
  it("缺口、跨源、午休、倒退累计额均留空", () => {
    expect(getMinuteAmount(point("14:35", 105, 1_100_000), before())).toBeNull();
    expect(getMinuteAmount({ ...tail(), source: "tdx_m1" }, before())).toBeNull();
    expect(getMinuteAmount(point("13:00", 105, 1_100_000), point("11:30", 105, 1_000_000))).toBeNull();
    expect(getMinuteAmount({ ...tail(), cum_amount: 1 }, before())).toBeNull();
    expect(getMinuteAmount(tail())).toBeNull();
  });
});

it("首个可信实时报价可先建立分时观察点，不伪造此前分钟或分钟量", () => {
  const q = quoteAt("14:29", 105.3, 1_100_000, 115_830_000);
  expect(mergeQuoteIntoMinutes([], q)).toEqual([expect.objectContaining({price:105.3, volume:null, cum_volume:1_100_000, avg:105.3})]);
  expect(mergeQuoteIntoMinutes([], {...q,quality:"stale"})).toBeNull();
  expect(mergeQuoteIntoMinutes([], {...q,data_timestamp:null})).toBeNull();
});

it.each(["09:30", "09:31"])("真实连续盘%s观察不继承竞价锚点标记，也不重写原竞价事实", clock => {
  const anchor: MinutePoint = {ts: `${TODAY}T09:30:00+08:00`, price: 4.5, source: "tencent", time_label: "opening_anchor",
    observation_ts: `${TODAY}T09:25:19+08:00`, cum_volume: 12_880_400, cum_amount: 57_961_800};
  const before = JSON.stringify(anchor), fresh = quoteAt(clock, 4.6, 13_000_000, 59_000_000);
  const out = mergeQuoteIntoMinutes([anchor], fresh)!;
  expect(out).toHaveLength(2);
  expect(out[0]).toBe(anchor);
  expect(out[1]).toMatchObject({price: 4.6, source: "tencent", observation_ts: fresh.data_timestamp,
    volume: null, minute_amount: null, cum_volume: 13_000_000});
  expect(out[1].time_label).not.toBe("opening_anchor");
  expect(JSON.stringify(anchor)).toBe(before);
  const old = {...fresh, data_timestamp: anchor.observation_ts};
  expect(mergeQuoteIntoMinutes([anchor], old)).toBeNull();
  expect(mergeQuoteIntoMinutes([anchor], {...fresh, quality: "stale"})).toBeNull();
  expect(mergeQuoteIntoMinutes([anchor], {...fresh, data_timestamp: null, received_at: fresh.data_timestamp!})).toBeNull();
  expect(getMinuteAmount(out[1], out[0])).toBeNull();
});

it("竞价累计额不能成为后续连续分钟的差分基线", () => {
  const anchor: MinutePoint = {ts: `${TODAY}T09:30:00+08:00`, price: 4.5, source: "tencent", time_label: "opening_anchor",
    observation_ts: `${TODAY}T09:25:19+08:00`, cum_amount: 57_961_800};
  const actual: MinutePoint = {...anchor, ts: `${TODAY}T09:31:00+08:00`, time_label: null,
    observation_ts: null, cum_amount: 58_961_800};
  expect(getMinuteAmount(actual, anchor)).toBeNull();
});

it("报价首点不是完整分钟的累计基线，后续不能把部分区间当分钟成交额", () => {
  const q = {symbol:"600127",price:10,source:"tencent",quality:"high",quality_reasons:[],received_at:"2026-10-15T10:00:30+08:00",data_timestamp:"2026-10-15T10:00:30+08:00",volume:1000,amount:10000} as Quote;
  const first = mergeQuoteIntoMinutes<MinutePoint>([], q)!;
  const next = mergeQuoteIntoMinutes(first, {...q,data_timestamp:"2026-10-15T10:01:10+08:00",volume:1500,amount:15000})!;
  const live = mergeQuoteIntoMinutes(next, {...q,data_timestamp:"2026-10-15T10:01:20+08:00",volume:1700,amount:17000})!;
  expect(live.at(-1)?.minute_amount).toBeNull();
  expect(live.at(-1)?.volume).toBeNull();
});
