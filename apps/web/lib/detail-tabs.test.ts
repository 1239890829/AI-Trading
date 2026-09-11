import { describe, expect, it } from "vitest";
import { INDEX_DEFAULT_RIGHT_TAB, INDEX_RIGHT_TABS, rightTabsFor } from "./detail-tabs";

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
