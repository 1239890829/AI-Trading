import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StockDetailPanel } from "@/components/stock-detail";

/**
 * P1-5（2026-09-11）：同一只股票不再被两条 WS 订阅。
 *
 * 契约：上游（工作台）传下 `liveQuote` 时，详情面板**不得**再为同一标的建连接
 * ——实现方式是给 `useQuoteStream` 传空订阅集（hook 在 hasSymbols=false 时直接 return）。
 * 不传 prop 时保持原行为（自带连接），供独立嵌入使用。
 *
 * 这里只钉「订阅集参数」这一个可观测契约，不渲染图表：
 * `chartTab="flow"` 走资金图分支，避开 lightweight-charts 在 jsdom 下的实例化。
 */

const calls = vi.hoisted(() => ({ symbols: [] as string[][] }));

vi.mock("@/hooks/use-quote-stream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-quote-stream")>();
  return {
    ...actual,
    useQuoteStream: (symbols: string[], opts?: { throttleMs?: number }) => {
      calls.symbols.push(symbols);
      // 不转调真实实现：本用例只关心订阅集参数，真实现会去建 WS/发请求
      return { quotes: {}, status: "live" as const };
    },
  };
});

afterEach(() => {
  cleanup();
  calls.symbols.length = 0;
});

describe("StockDetailPanel · WS 订阅复用（P1-5）", () => {
  it("上游传下 liveQuote ⇒ 订阅集为空（不再自建连接）", () => {
    render(
      <StockDetailPanel
        symbol="600519"
        chartTab="flow"
        liveQuote={{ symbol: "600519", price: 1500 } as never}
        streamStatus="live"
      />
    );
    expect(calls.symbols.length).toBeGreaterThan(0);
    for (const s of calls.symbols) expect(s).toEqual([]);
  });

  it("未传 liveQuote ⇒ 保持自带连接（订阅自身 symbol）", () => {
    render(<StockDetailPanel symbol="600519" chartTab="flow" />);
    expect(calls.symbols.length).toBeGreaterThan(0);
    for (const s of calls.symbols) expect(s).toEqual(["600519"]);
  });
});
