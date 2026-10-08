import { cleanup, fireEvent, render, within } from "@testing-library/react";
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

vi.mock("@/components/detail/book-trades-view", () => ({
  BookTradesView: ({showBook}: {showBook: boolean}) => <div data-testid="market-prints">{showBook ? "盘口消费者" : "逐笔消费者"}</div>,
}));

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


describe("StockDetailPanel · 核对视角入口", () => {
  it("only mounts market prints in their own perspective", () => {
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" />);
    expect(view.getByTestId("market-prints").textContent).toBe("盘口消费者");
    fireEvent.click(view.getByRole("tab", {name:"逐笔"}));
    expect(view.getByTestId("market-prints").textContent).toBe("逐笔消费者");
    fireEvent.click(view.getByRole("tab", {name:"资料"}));
    expect(view.queryByTestId("market-prints")).toBeNull();
    fireEvent.click(view.getByRole("tab", {name:"资讯"}));
    expect(view.queryByTestId("market-prints")).toBeNull();
  });

  it("点击和方向键切换复用受控视角回调", () => {
    const onRightTabChange = vi.fn();
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" onRightTabChange={onRightTabChange} />);
    const controls = within(view.getByRole("tablist", { name: "核对视角" }));
    fireEvent.click(controls.getByRole("tab", { name: "资料" }));
    expect(onRightTabChange).toHaveBeenLastCalledWith("profile");
    fireEvent.keyDown(controls.getByRole("tab", { name: "资料" }), { key: "Home" });
    expect(onRightTabChange).toHaveBeenLastCalledWith("book");
    fireEvent.keyDown(controls.getByRole("tab", { name: "盘口" }), { key: "ArrowLeft" });
    expect(onRightTabChange).toHaveBeenLastCalledWith("info");
    expect(document.activeElement).toBe(controls.getByRole("tab", { name: "资讯" }));
    expect(controls.getAllByRole("tab")).toHaveLength(7);
  });

  it("指数只提供适用视角，不能从入口进入股票交易", () => {
    const view = render(<StockDetailPanel symbol="sh000001" chartTab="flow" />);
    const controls = within(view.getByRole("tablist", { name: "核对视角" }));
    expect(controls.getAllByRole("tab").map(button => button.textContent)).toEqual(["涨速", "板块"]);
    expect(controls.queryByRole("tab", { name: "模拟交易" })).toBeNull();
    expect(controls.queryByRole("tab", { name: "手工记账" })).toBeNull();
    const active = controls.getByRole("tab", {selected: true});
    expect(active.tabIndex).toBe(0);
    expect(view.getByRole("tabpanel").getAttribute("aria-labelledby")).toBe(active.id);
  });
});
