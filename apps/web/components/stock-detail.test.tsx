import { act, cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StockDetailPanel, type ChartTab, type RightTab } from "@/components/stock-detail";
import type { KlinePayload, MinutePoint, PaperAccountInfo, PaperFill, PaperPositionInfo } from "@/lib/api";
import type { Kline, OrderBook, Quote, Trade } from "@/types/market";

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
const api = vi.hoisted(() => ({
  getKlinePayload: vi.fn(),
  getOrderBook: vi.fn(),
  getMinuteLineWithBaseline: vi.fn(),
  getTrades: vi.fn(),
  getQuote: vi.fn(),
  getWatchlist: vi.fn(),
  getStockThemes: vi.fn(),
  getEventsForSymbol: vi.fn(),
  getMinuteLine: vi.fn(),
  getMarketOverview: vi.fn(),
  getCapitalFlow: vi.fn(),
  getAuction: vi.fn(),
  getFinancials: vi.fn(),
  getCompanyProfile: vi.fn(),
  getNewsDigest: vi.fn(),
  getThemesCatalog: vi.fn(),
  getPaperAccount: vi.fn(),
  getPaperPositions: vi.fn(),
  getPaperOrders: vi.fn(),
  getPaperFills: vi.fn(),
  cancelPaperOrder: vi.fn(),
  checkOrderRisk: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  ...api,
}));

vi.mock("@/lib/market-hours", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/market-hours")>(),
  isTradingSession: () => true,
}));

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

// Canvas is outside this request-count check; book/trades use their real view.
vi.mock("@/components/kline-chart-pro", () => ({
  KlineChartPro: ({ bars, costPrice, tradeMarks }: { bars: Kline[]; costPrice?: number | null; tradeMarks?: PaperFill[] }) => <div data-testid="kline-price" data-cost-price={costPrice ?? "none"} data-fill-count={tradeMarks?.length ?? 0}>{bars.at(-1)?.close}</div>,
}));
vi.mock("@/components/minute-chart", () => ({
  MinuteChart: ({ points }: { points: MinutePoint[] }) => <div data-testid="minute-price">{points.at(-1)?.price}</div>,
}));

const audit = { source: "tdx", quality: "high" as const, quality_reasons: [], received_at: "2026-10-09T02:00:00Z" };
function book(symbol = "600519", price = 20): OrderBook {
  return { ...audit, symbol, bids: [{ price, volume: 100 }], asks: [] };
}
function kline(symbol = "600519", price = 20): KlinePayload {
  return { symbol, timeframe: "1d", trading_status: null, bars: [{ ...audit, symbol, timeframe: "1d", ts: "2026-10-09", close: price, open: price, high: price, low: price }] };
}
function minute(price = 20): { points: MinutePoint[]; vr_baseline_5m: null } {
  return { points: [{ ts: "2026-10-09T02:00:00Z", price, source: "tdx" }], vr_baseline_5m: null };
}
function trades(symbol = "600519", price = 20): Trade[] {
  return [{ ...audit, symbol, ts: "2026-10-09T02:00:00Z", price, volume: 100, side: "buy" }];
}
function account(total = 100): PaperAccountInfo {
  return { total, cash: total, market_value: 0, total_pnl: 0, total_pnl_pct: 0 };
}
function position(symbol = "600519", quantity = 100, cost_price = 20): PaperPositionInfo {
  return { symbol, quantity, available: quantity, cost_price };
}
function fill(symbol = "600519", price = 20): PaperFill {
  return { symbol, date: "2026-10-09", side: "buy", price, quantity: 100, fee: 5 };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
async function settle() { await act(async () => {}); }
async function advance(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-09T02:00:00Z"));
  for (const fn of Object.values(api)) fn.mockReset();
  api.getKlinePayload.mockImplementation(async (symbol: string) => kline(symbol));
  api.getOrderBook.mockImplementation(async (symbol: string) => book(symbol));
  api.getMinuteLineWithBaseline.mockResolvedValue(minute());
  api.getTrades.mockResolvedValue([]);
  api.getQuote.mockImplementation(async (symbol: string) => ({ ...audit, symbol, price: 20 }));
  api.getWatchlist.mockResolvedValue([]);
  api.getStockThemes.mockResolvedValue(null);
  api.getEventsForSymbol.mockResolvedValue({ items: [] });
  api.getMinuteLine.mockResolvedValue([]);
  api.getMarketOverview.mockResolvedValue({ indices: [] });
  api.getCapitalFlow.mockResolvedValue(null);
  api.getAuction.mockResolvedValue(null);
  api.getFinancials.mockResolvedValue(null);
  api.getCompanyProfile.mockResolvedValue(null);
  api.getNewsDigest.mockResolvedValue({ announcements: [], news: [], announcements_error: null, news_error: null });
  api.getThemesCatalog.mockResolvedValue([]);
  api.getPaperAccount.mockResolvedValue(account());
  api.getPaperPositions.mockResolvedValue([]);
  api.getPaperOrders.mockResolvedValue([]);
  api.getPaperFills.mockResolvedValue([]);
  api.cancelPaperOrder.mockResolvedValue(undefined);
  api.checkOrderRisk.mockImplementation(async () => ({ allowed: false, checked_at: new Date().toISOString(), estimated_fee: 5, max_qty: 0, reasons: ["测试不允许下单"], warnings: [], state: "blocked" }));
});

describe("StockDetailPanel · 估值和模拟持仓回包归属", () => {
  it.each([false, true])("旧估值成功不能覆盖当前行情，包含 A→B→A=%s；同股补源保留 WS 价格", async (returnToOriginal) => {
    const old = deferred<Quote>();
    let first = true;
    api.getQuote.mockImplementation((symbol: string) => {
      if (symbol === "600519" && first) { first = false; return old.promise; }
      return Promise.resolve({ ...audit, symbol, name: "当前标的", price: 220, pe_ttm: 32 });
    });
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" />);
    view.rerender(<StockDetailPanel symbol="000001" chartTab="flow" liveQuote={{ ...audit, symbol: "000001", price: 222 }} />);
    await settle();
    if (returnToOriginal) {
      view.rerender(<StockDetailPanel symbol="600519" chartTab="flow" liveQuote={{ ...audit, symbol: "600519", price: 222 }} />);
      await settle();
    }
    expect(view.getByText("当前标的")).toBeTruthy();
    expect(view.container.querySelector(".quote-sheet")?.textContent).toContain("222.00");
    expect(within(view.getByRole("region", { name: "行情详细指标" })).getByText("32.00")).toBeTruthy();
    await act(async () => { old.resolve({ ...audit, symbol: "600519", name: "旧标的回包", price: 111, pe_ttm: 11 }); });
    expect(view.getByText("当前标的")).toBeTruthy();
    expect(view.queryByText("旧标的回包")).toBeNull();
    expect(view.container.querySelector(".quote-sheet")?.textContent).toContain("222.00");
    expect(within(view.getByRole("region", { name: "行情详细指标" })).getByText("32.00")).toBeTruthy();
  });

  it("旧估值失败不能结束新标的尚在途的首取", async () => {
    const old = deferred<Quote>();
    const current = deferred<Quote>();
    api.getQuote.mockImplementation((symbol: string) => symbol === "600519" ? old.promise : current.promise);
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" />);
    view.rerender(<StockDetailPanel symbol="000001" chartTab="flow" />);
    await act(async () => { old.reject(new Error("old failure")); });
    expect(view.queryByText("行情数据暂不可用（数据源失败，稍后自动重试）")).toBeNull();
    await act(async () => { current.resolve({ ...audit, symbol: "000001", name: "当前标的", price: 222 }); });
    expect(view.getByText("当前标的")).toBeTruthy();
  });

  it.each(["success", "failure"])("旧模拟账户 %s 回包不能覆盖新标的账户、持仓与成交记录", async (outcome) => {
    const old = deferred<PaperFill[]>();
    api.getPaperAccount.mockResolvedValueOnce(account(111)).mockResolvedValue(account(222));
    api.getPaperPositions.mockResolvedValueOnce([position("600519", 100, 11)]).mockResolvedValue([position("000001", 300, 22)]);
    api.getPaperFills.mockImplementation((symbol: string) => symbol === "600519" ? old.promise : Promise.resolve([fill(symbol, 22)]));
    const view = render(<StockDetailPanel symbol="600519" rightTab="trade" />);
    view.rerender(<StockDetailPanel symbol="000001" rightTab="trade" />);
    await settle();
    expect(within(view.getByRole("tabpanel")).getByText("300股 · 可卖300")).toBeTruthy();
    expect(within(view.getByRole("tabpanel")).getAllByText("222.00")).toHaveLength(2);
    await act(async () => { if (outcome === "success") old.resolve([fill("600519", 11)]); else old.reject(new Error("old failure")); });
    expect(view.queryByText("模拟账户数据加载失败（稍后自动重试）")).toBeNull();
    expect(within(view.getByRole("tabpanel")).getByText("300股 · 可卖300")).toBeTruthy();
    expect(within(view.getByRole("tabpanel")).queryByText("11.00")).toBeNull();
    expect(within(view.getByRole("tabpanel")).getAllByText("222.00")).toHaveLength(2);
    expect(view.getByTestId("kline-price").getAttribute("data-cost-price")).toBe("22");
    expect(view.getByTestId("kline-price").getAttribute("data-fill-count")).toBe("1");
  });

  it("当前模拟账户首取失败仍提示错误，旧标的成功不能把错误改成旧账户", async () => {
    const old = deferred<PaperFill[]>();
    api.getPaperFills.mockImplementation((symbol: string) => symbol === "600519" ? old.promise : Promise.reject(new Error("current failure")));
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="trade" />);
    view.rerender(<StockDetailPanel symbol="000001" chartTab="flow" rightTab="trade" />);
    await settle();
    expect(view.getByText("模拟账户数据加载失败（稍后自动重试）")).toBeTruthy();
    await act(async () => { old.resolve([fill("600519", 11)]); });
    expect(view.getByText("模拟账户数据加载失败（稍后自动重试）")).toBeTruthy();
  });

  it("撤单后仍立即同步模拟账户、持仓、成交和图表成本线；离开交易页签保留快照", async () => {
    api.getPaperPositions.mockResolvedValueOnce([position("600519", 200, 17)]).mockResolvedValue([position("600519", 300, 19)]);
    api.getPaperOrders.mockResolvedValueOnce([{ id: 7, symbol: "600519", side: "buy", price: 19, quantity: 100, status: "pending" }]);
    api.getPaperFills.mockResolvedValueOnce([]).mockResolvedValue([fill("600519", 19)]);
    const view = render(<StockDetailPanel symbol="600519" rightTab="trade" />);
    await settle();
    expect(view.getByText("200股 · 可卖200")).toBeTruthy();
    expect(view.getByTestId("kline-price").getAttribute("data-cost-price")).toBe("17");
    expect(view.getByTestId("kline-price").getAttribute("data-fill-count")).toBe("0");
    fireEvent.click(view.getByRole("button", { name: "撤" }));
    await settle();
    expect(api.cancelPaperOrder).toHaveBeenCalledWith(7);
    for (const endpoint of [api.getPaperAccount, api.getPaperPositions, api.getPaperOrders, api.getPaperFills]) expect(endpoint).toHaveBeenCalledTimes(2);
    expect(view.getByText("300股 · 可卖300")).toBeTruthy();
    expect(view.getByText("2026-10-09")).toBeTruthy();
    expect(view.getByTestId("kline-price").getAttribute("data-cost-price")).toBe("19");
    expect(view.getByTestId("kline-price").getAttribute("data-fill-count")).toBe("1");
    fireEvent.click(view.getByRole("tab", { name: "盘口" }));
    await advance(30_000);
    expect(api.getPaperPositions).toHaveBeenCalledTimes(2);
    expect(view.getByTestId("kline-price").getAttribute("data-cost-price")).toBe("19");
    expect(view.getByTestId("kline-price").getAttribute("data-fill-count")).toBe("1");
  });
});

afterEach(() => {
  cleanup();
  calls.symbols.length = 0;
  vi.useRealTimers();
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
  it("only mounts market prints in their own perspective", async () => {
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" />);
    await settle();
    expect(within(view.getByRole("tabpanel")).getByText("买1")).toBeTruthy();
    fireEvent.click(view.getByRole("tab", {name:"逐笔"}));
    await settle();
    expect(within(view.getByRole("tabpanel")).getByText("暂无逐笔（盘中看分时）")).toBeTruthy();
    fireEvent.click(view.getByRole("tab", {name:"资料"}));
    expect(view.queryByText("买1")).toBeNull();
    expect(view.queryByText("暂无逐笔（盘中看分时）")).toBeNull();
    fireEvent.click(view.getByRole("tab", {name:"资讯"}));
    expect(view.queryByText("买1")).toBeNull();
    expect(view.queryByText("暂无逐笔（盘中看分时）")).toBeNull();
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

describe("StockDetailPanel · 每个端点只读取一次首屏", () => {
  it("默认 K线/盘口立即读取一次，非激活分时/逐笔仍在 idle 预取，页签切入继续立即轮询", async () => {
    const view = render(<StockDetailPanel symbol="600519" />);
    await settle();
    expect(api.getKlinePayload).toHaveBeenCalledTimes(1);
    expect(api.getOrderBook).toHaveBeenCalledTimes(1);
    expect(view.getByTestId("kline-price").textContent).toBe("20");
    expect(within(view.getByRole("tabpanel")).getByText("买1")).toBeTruthy();
    expect(api.getMinuteLineWithBaseline).not.toHaveBeenCalled();
    expect(api.getTrades).not.toHaveBeenCalled();
    await advance(900);
    expect(api.getMinuteLineWithBaseline).toHaveBeenCalledTimes(1);
    expect(api.getTrades).toHaveBeenCalledTimes(1);
    await advance(4100);
    expect(api.getOrderBook).toHaveBeenCalledTimes(2);
    expect(api.getKlinePayload).toHaveBeenCalledTimes(1);
    fireEvent.click(view.getByRole("tab", { name: "逐笔" }));
    fireEvent.click(view.getByRole("button", { name: "分时" }));
    await settle();
    expect(api.getTrades).toHaveBeenCalledTimes(2);
    expect(api.getMinuteLineWithBaseline).toHaveBeenCalledTimes(2);
    await advance(10_000);
    expect(api.getTrades).toHaveBeenCalledTimes(3);
    expect(api.getOrderBook).toHaveBeenCalledTimes(2);
  });

  it("初始分时/逐笔由激活轮询立即读取，idle 不重发；离开页签暂停轮询", async () => {
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" rightTab="trades" />);
    await settle();
    expect(api.getMinuteLineWithBaseline).toHaveBeenCalledTimes(1);
    expect(api.getTrades).toHaveBeenCalledTimes(1);
    expect(api.getKlinePayload).toHaveBeenCalledTimes(1);
    expect(api.getOrderBook).toHaveBeenCalledTimes(1);
    expect(view.getByTestId("minute-price").textContent).toBe("20");
    await advance(900);
    expect(api.getMinuteLineWithBaseline).toHaveBeenCalledTimes(1);
    expect(api.getTrades).toHaveBeenCalledTimes(1);
    await advance(59_100);
    expect(api.getMinuteLineWithBaseline).toHaveBeenCalledTimes(2);
    expect(api.getTrades).toHaveBeenCalledTimes(7);
    fireEvent.click(view.getByRole("button", { name: "资金图" }));
    fireEvent.click(view.getByRole("tab", { name: "资料" }));
    await advance(120_000);
    expect(api.getMinuteLineWithBaseline).toHaveBeenCalledTimes(2);
    expect(api.getTrades).toHaveBeenCalledTimes(7);
  });

  it.each([
    { endpoint: "getKlinePayload" as const, chartTab: "kline" as const, rightTab: "profile" as const, result: kline("600519", 77), interval: 60_000 },
    { endpoint: "getOrderBook" as const, chartTab: "flow" as const, rightTab: "book" as const, result: book("600519", 77), interval: 5_000 },
    { endpoint: "getMinuteLineWithBaseline" as const, chartTab: "minute" as const, rightTab: "profile" as const, result: minute(77), interval: 60_000 },
    { endpoint: "getTrades" as const, chartTab: "flow" as const, rightTab: "trades" as const, result: trades("600519", 77), interval: 10_000 },
  ])("$endpoint 非激活预取仍在途时，切入复用首取，完成后恢复轮询", async ({ endpoint, chartTab, rightTab, result, interval }) => {
    const pending = deferred<typeof result>();
    api[endpoint].mockReturnValueOnce(pending.promise);
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="profile" />);
    await advance(900);
    expect(api[endpoint]).toHaveBeenCalledTimes(1);
    if (chartTab !== "flow") fireEvent.click(view.getByRole("button", { name: chartTab === "kline" ? "K线" : "分时" }));
    if (rightTab !== "profile") fireEvent.click(view.getByRole("tab", { name: rightTab === "book" ? "盘口" : "逐笔" }));
    await settle();
    expect(api[endpoint]).toHaveBeenCalledTimes(1);
    await act(async () => { pending.resolve(result); });
    if (chartTab !== "flow") {
      expect(view.getByTestId(chartTab === "kline" ? "kline-price" : "minute-price").textContent).toBe("77");
    } else {
      expect(within(view.getByRole("tabpanel")).getByText("77.00")).toBeTruthy();
    }
    await advance(interval);
    expect(api[endpoint]).toHaveBeenCalledTimes(2);
  });

  it("盘口首取失败结束骨架，后续恢复；刷新失败保留快照，合法空档位不会变成加载中", async () => {
    api.getOrderBook.mockRejectedValueOnce(new Error("unavailable"));
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" />);
    await settle();
    const panel = within(view.getByRole("tabpanel"));
    expect(panel.getByText("盘口数据不可用（免费源仅盘中提供）")).toBeTruthy();
    expect(api.getOrderBook).toHaveBeenCalledTimes(1);
    await advance(5000);
    expect(panel.getByText("买1")).toBeTruthy();
    api.getOrderBook.mockRejectedValueOnce(new Error("refresh unavailable"));
    await advance(5000);
    expect(panel.getByText("买1")).toBeTruthy();
    api.getOrderBook.mockResolvedValueOnce({ ...book(), asks: [], bids: [] });
    await advance(5000);
    expect(panel.getByRole("table").querySelectorAll("tr")).toHaveLength(0);
    expect(panel.queryByText("盘口数据不可用（免费源仅盘中提供）")).toBeNull();
  });

  it("逐笔首取失败立即结束骨架，成功空数组也保持确认空态", async () => {
    api.getTrades.mockRejectedValueOnce(new Error("unavailable"));
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" rightTab="trades" />);
    await settle();
    const panel = within(view.getByRole("tabpanel"));
    expect(panel.getByText("暂无逐笔（盘中看分时）")).toBeTruthy();
    await advance(10_000);
    expect(panel.getByText("暂无逐笔（盘中看分时）")).toBeTruthy();
    expect(api.getTrades).toHaveBeenCalledTimes(2);
  });

  it.each<[ChartTab, RightTab]>([["kline", "book"], ["minute", "trades"]])("切股后旧 %s/%s 回包不能覆盖新标的", async (chartTab, rightTab) => {
    const old = { kline: deferred<KlinePayload>(), book: deferred<OrderBook>(), minute: deferred<ReturnType<typeof minute>>(), trades: deferred<Trade[]>() };
    api.getKlinePayload.mockImplementation((symbol: string) => symbol === "600519" ? old.kline.promise : Promise.resolve(kline(symbol, 222)));
    api.getOrderBook.mockImplementation((symbol: string) => symbol === "600519" ? old.book.promise : Promise.resolve(book(symbol, 222)));
    api.getMinuteLineWithBaseline.mockImplementation((symbol: string) => symbol === "600519" ? old.minute.promise : Promise.resolve(minute(222)));
    api.getTrades.mockImplementation((symbol: string) => symbol === "600519" ? old.trades.promise : Promise.resolve(trades(symbol, 222)));
    const view = render(<StockDetailPanel symbol="600519" chartTab={chartTab} rightTab={rightTab} />);
    await advance(900);
    view.rerender(<StockDetailPanel symbol="000001" chartTab={chartTab} rightTab={rightTab} />);
    await advance(900);
    const chartId = chartTab === "kline" ? "kline-price" : "minute-price";
    expect(view.getByTestId(chartId).textContent).toBe("222");
    expect(within(view.getByRole("tabpanel")).getByText("222.00")).toBeTruthy();
    await act(async () => {
      old.kline.resolve(kline("600519", 111));
      old.book.resolve(book("600519", 111));
      old.minute.resolve(minute(111));
      old.trades.resolve(trades("600519", 111));
    });
    expect(view.getByTestId(chartId).textContent).toBe("222");
    expect(within(view.getByRole("tabpanel")).queryByText("111.00")).toBeNull();
    expect(within(view.getByRole("tabpanel")).getByText("222.00")).toBeTruthy();
  });

  it("指数继续跳过股票盘口/逐笔端点", async () => {
    render(<StockDetailPanel symbol="sh000001" />);
    await advance(900);
    expect(api.getOrderBook).not.toHaveBeenCalled();
    expect(api.getTrades).not.toHaveBeenCalled();
    expect(api.getKlinePayload).toHaveBeenCalledTimes(1);
    expect(api.getMinuteLineWithBaseline).toHaveBeenCalledTimes(1);
  });
});
