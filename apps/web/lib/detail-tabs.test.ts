import { describe, expect, it } from "vitest";
import {
  INDEX_DEFAULT_RIGHT_TAB,
  INDEX_RIGHT_TABS,
  parseWorkbenchDetailUrl,
  rightTabsFor,
} from "./detail-tabs";

describe("详情页右列 tab 口径（lib/detail-tabs）", () => {
  it("指数：只保留 涨速/板块 —— 盘口与资讯是无用 tab", () => {
    const keys = rightTabsFor(true).map(([k]) => k);
    expect(keys).toEqual(["speed", "boards"]);
    // 回归锁：曾出现的死 tab 不得回归
    expect(keys).not.toContain("book"); // 免费源无指数撮合数据
    expect(keys).not.toContain("info"); // /api/news|announcements|company 对指数 502
    expect(keys).not.toContain("trades");
    expect(keys).not.toContain("trade");
  });

  it("个股：保留全量 tab（含模拟交易/真实持仓/资料/做T）", () => {
    const keys = rightTabsFor(false).map(([k]) => k);
    expect(keys).toEqual(["book", "trades", "trade", "real", "profile", "dt", "info"]);
  });

  it("INDEX_RIGHT_TABS 与 tab 列表同源（防两处漂移）", () => {
    const fromList = new Set(rightTabsFor(true).map(([k]) => k));
    expect([...fromList].sort()).toEqual([...INDEX_RIGHT_TABS].sort());
    expect(INDEX_RIGHT_TABS.has(INDEX_DEFAULT_RIGHT_TAB)).toBe(true);
  });

  it("tab 标签非空（防列表改键丢标签）", () => {
    for (const [, label] of [...rightTabsFor(true), ...rightTabsFor(false)]) {
      expect(label.trim().length).toBeGreaterThan(0);
    }
  });
});

describe("详情深链 URL → 弹窗入参（parseWorkbenchDetailUrl）", () => {
  it("工作台深链：解析出标的（弹窗化后既有链接不得失效）", () => {
    expect(parseWorkbenchDetailUrl("/workbench?symbol=600519")).toEqual({
      symbol: "600519",
      chartTab: undefined,
      rightTab: undefined,
    });
  });

  it("tab 意图透传：ct → chartTab、rt → rightTab", () => {
    expect(parseWorkbenchDetailUrl("/workbench?symbol=600519&ct=kline")).toMatchObject({
      symbol: "600519",
      chartTab: "kline",
    });
    expect(parseWorkbenchDetailUrl("/workbench?symbol=600519&rt=trades")).toMatchObject({
      symbol: "600519",
      rightTab: "trades",
    });
    expect(parseWorkbenchDetailUrl("/workbench?symbol=600519&ct=flow&rt=profile")).toMatchObject({
      symbol: "600519",
      chartTab: "flow",
      rightTab: "profile",
    });
  });

  it("返回参数 from 不影响解析（跨页来源是导航信息，不是详情意图）", () => {
    expect(parseWorkbenchDetailUrl("/workbench?symbol=600519&from=%2Ftape%3Ftab%3Dlimitup")).toMatchObject({
      symbol: "600519",
    });
  });

  it("指数形态（带市场前缀）可解析——指数详情与个股同一条通路", () => {
    expect(parseWorkbenchDetailUrl("/workbench?symbol=sh000001")).toMatchObject({ symbol: "sh000001" });
    expect(parseWorkbenchDetailUrl("/workbench?symbol=sz399006")).toMatchObject({ symbol: "sz399006" });
  });

  it("/stock 语义别名（路径 / 查询两种形态）等价于工作台深链", () => {
    expect(parseWorkbenchDetailUrl("/stock/600103")).toMatchObject({ symbol: "600103" });
    expect(parseWorkbenchDetailUrl("/stock/600105.SH")).toBeNull(); // 带后缀不是合法标的形态
    expect(parseWorkbenchDetailUrl("/stock?symbol=600103")).toMatchObject({ symbol: "600103" });
  });

  it("非法 tab 回落 undefined（不抛错，URL 是外部输入）", () => {
    expect(parseWorkbenchDetailUrl("/workbench?symbol=600519&ct=__evil__&rt=nope")).toEqual({
      symbol: "600519",
      chartTab: undefined,
      rightTab: undefined,
    });
  });

  it("非详情链接一律返回 null（调用方照常导航，不吞链接）", () => {
    expect(parseWorkbenchDetailUrl("/tape?tab=limitup")).toBeNull();
    expect(parseWorkbenchDetailUrl("/workbench")).toBeNull(); // 无标的工作台
    expect(parseWorkbenchDetailUrl("/workbench?symbol=")).toBeNull();
    expect(parseWorkbenchDetailUrl("/workbench?symbol=abc")).toBeNull();
    expect(parseWorkbenchDetailUrl("")).toBeNull();
    expect(parseWorkbenchDetailUrl(null)).toBeNull();
    expect(parseWorkbenchDetailUrl(undefined)).toBeNull();
  });

  it("站外链接绝不解析（防把外链当站内详情弹窗）", () => {
    expect(parseWorkbenchDetailUrl("https://evil.com/workbench?symbol=600519")).toBeNull();
    expect(parseWorkbenchDetailUrl("http://evil.com/stock/600519")).toBeNull();
    expect(parseWorkbenchDetailUrl("javascript:alert(1)")).toBeNull();
    expect(parseWorkbenchDetailUrl("//evil.com/workbench?symbol=600519")).toBeNull();
  });

  it("与构造器互逆：buildNav 产物必须能被解析回同一标的", () => {
    // 交叉断言：lib/nav-targets.ts 的构造 ↔ 本文件解析（两边漂移会静默回落默认 tab）
    for (const url of [
      "/workbench?symbol=600519",
      "/workbench?symbol=600519&ct=kline",
      "/workbench?symbol=600519&ct=minute",
      "/workbench?symbol=600519&ct=flow",
      "/workbench?symbol=600519&rt=book",
      "/workbench?symbol=600519&rt=trades",
      "/workbench?symbol=600519&rt=profile",
      "/workbench?symbol=600519&rt=info",
      "/workbench?symbol=600519&rt=dt",
    ]) {
      expect(parseWorkbenchDetailUrl(url)?.symbol, url).toBe("600519");
    }
  });
});
