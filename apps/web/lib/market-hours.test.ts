import { describe, expect, it } from "vitest";

import {
  CONTINUOUS_SESSIONS,
  POLLING_SESSIONS,
  TRADING_MINUTES,
  isContinuousSession,
  isPollingSession,
  isTradingSession,
  tradingSeqFromHHMM,
} from "@/lib/market-hours";

/**
 * 本文件钉住 2026-09-12 评审 R-2 / R-3 收敛后的两条契约。
 *
 * 1. **两个意图、一份边界（R-2）**：严格口径（连续竞价，用于 WS tick 合成分钟点）
 *    与宽松口径（轮询节奏）回答的是两个不同问题，**不可合并**；但宽松必须
 *    **完全包含**严格——否则盘中会被判成盘外、轮询降频漏刷新。后者用全天逐分钟
 *    扫描的关系守卫钉住，取代此前"两处各写一份区间、靠人记得同步"。
 * 2. **一条交易分钟轴（R-3）**：`tradingSeqFromHHMM` 整日**单调不减**。
 *    旧实现在午休段（11:31–12:59）会算出倒退的序（11:31→31 而 11:30→120），
 *    在资金流曲线上表现为折线往回画。
 */

const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

describe("时段判定（R-2：两个意图，一份边界）", () => {
  it("严格口径 = 连续竞价 09:30-11:30 / 13:00-15:00", () => {
    expect(CONTINUOUS_SESSIONS).toEqual([
      [570, 690],
      [780, 900],
    ]);
    for (const m of [570, 600, 690, 780, 900]) expect(isContinuousSession(m)).toBe(true);
    expect(isContinuousSession(569)).toBe(false); // 集合竞价内，但未连续竞价
    expect(isContinuousSession(691)).toBe(false); // 午休
    expect(isContinuousSession(779)).toBe(false);
    expect(isContinuousSession(901)).toBe(false);
  });

  it("宽松口径 = 严格口径向两侧松弛（09:15-11:35 / 12:55-15:15）", () => {
    expect(POLLING_SESSIONS).toEqual([
      [555, 695],
      [775, 915],
    ]);
    for (const m of [555, 695, 775, 915]) expect(isPollingSession(m)).toBe(true);
    expect(isPollingSession(554)).toBe(false);
    expect(isPollingSession(916)).toBe(false);
  });

  it("**包含关系**：全天逐分钟扫描，连续竞价 ⇒ 轮询（否则盘中漏刷新）", () => {
    const violations = Array.from({ length: 24 * 60 }, (_, m) => m).filter(
      (m) => isContinuousSession(m) && !isPollingSession(m)
    );
    expect(violations).toEqual([]);
  });

  it("两个口径**确实不同**（把它们合并成一个函数必然给其中一方错答案）", () => {
    expect(POLLING_SESSIONS).not.toEqual(CONTINUOUS_SESSIONS);
  });

  it("isTradingSession：周末恒 false，工作日按宽松口径（与机器时区无关）", () => {
    // 2026-09-12 周六、2026-09-11 周五；02:00Z = 北京 10:00
    expect(isTradingSession(new Date("2026-09-12T02:00:00Z"))).toBe(false);
    expect(isTradingSession(new Date("2026-09-11T02:00:00Z"))).toBe(true);
    // 周五 09:15 北京（01:15Z）在宽松区间内、严格区间外
    expect(isTradingSession(new Date("2026-09-11T01:15:00Z"))).toBe(true);
    expect(isTradingSession(new Date("2026-09-11T00:00:00Z"))).toBe(false); // 北京 08:00
  });
});

describe("交易分钟轴（R-3：一份映射）", () => {
  it("与后端 _sina_bar_seq 同口径（对齐 backend/tests/test_fund_flow.py）", () => {
    expect(tradingSeqFromHHMM("09:30")).toBe(0);
    expect(tradingSeqFromHHMM("09:35")).toBe(5);
    expect(tradingSeqFromHHMM("11:30")).toBe(120);
    expect(tradingSeqFromHHMM("13:05")).toBe(125);
    expect(tradingSeqFromHHMM("15:00")).toBe(240);
  });

  it("**整日单调不减**：午休折叠不得倒退（旧实现在 11:31–12:59 往回走）", () => {
    let prev = -1;
    const backSteps: string[] = [];
    for (let m = 0; m < 24 * 60; m++) {
      const t = hhmm(m);
      const seq = tradingSeqFromHHMM(t);
      if (seq < prev) backSteps.push(`${t}→${seq}(<${prev})`);
      prev = seq;
    }
    expect(backSteps).toEqual([]);
  });

  it("午休折叠到上午收盘位 120（与 13:00 同位）", () => {
    expect(tradingSeqFromHHMM("11:31")).toBe(120);
    expect(tradingSeqFromHHMM("12:00")).toBe(120);
    expect(tradingSeqFromHHMM("12:59")).toBe(120);
  });

  it("值域 [0,240]：盘前归 0、收盘后封顶、非法输入不产生 NaN", () => {
    expect(tradingSeqFromHHMM("09:00")).toBe(0);
    expect(tradingSeqFromHHMM("15:30")).toBe(TRADING_MINUTES);
    expect(tradingSeqFromHHMM("23:59")).toBe(TRADING_MINUTES);
    // 非法输入旧实现返回 NaN，会被原样写进 SVG 的 d 属性
    expect(tradingSeqFromHHMM("")).toBe(0);
    expect(tradingSeqFromHHMM("xx:yy")).toBe(0);
  });
});
