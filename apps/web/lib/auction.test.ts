import { describe, expect, it } from "vitest";
import { sortAuctionBenchmark } from "./auction";
import type { AuctionBenchmarkItem } from "./api";

const item = (symbol: string, pct: number | null): AuctionBenchmarkItem => ({
  symbol,
  name: symbol,
  auction_pct: pct,
  tags: [],
});

describe("sortAuctionBenchmark", () => {
  it("按竞价涨幅降序", () => {
    const out = sortAuctionBenchmark([item("A", -1), item("B", 5), item("C", 0)]);
    expect(out.map((x) => x.symbol)).toEqual(["B", "C", "A"]);
  });

  it("缺失（null）沉底，不与 0（平开）混淆", () => {
    // 回归：实测 2026-07-15 榜单含真实 +0.00% 平开条目。
    // 若把 null 当 0 参与比较，缺失条目会挤进榜单中部、伪装成"平开"。
    const out = sortAuctionBenchmark([
      item("MISS", null),
      item("FLAT", 0),
      item("UP", 3),
      item("DOWN", -2),
    ]);
    expect(out.map((x) => x.symbol)).toEqual(["UP", "FLAT", "DOWN", "MISS"]);
  });

  it("多个缺失项全部沉底且保持原相对顺序", () => {
    const out = sortAuctionBenchmark([item("M1", null), item("UP", 1), item("M2", null)]);
    expect(out.map((x) => x.symbol)).toEqual(["UP", "M1", "M2"]);
  });

  it("空值安全，且不原地改动入参", () => {
    expect(sortAuctionBenchmark(null)).toEqual([]);
    expect(sortAuctionBenchmark(undefined)).toEqual([]);
    expect(sortAuctionBenchmark([])).toEqual([]);

    // 入参来自 React state，原地 sort 会变异 state（React 检测不到引用变化）
    const src = [item("A", 1), item("B", 9)];
    sortAuctionBenchmark(src);
    expect(src.map((x) => x.symbol)).toEqual(["A", "B"]);
  });
});
