import { act, cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StockDetailPanel, type ChartTab, type RightTab } from "@/components/stock-detail";
import type { KlinePayload, MinutePoint, PaperAccountInfo, PaperFill, PaperPositionInfo } from "@/lib/api";
import { mergeQuoteIntoMinutes } from "@/lib/kline-live";
import { getWatchlist as readActualWatchlist } from "@/lib/api/watchlist";
import { notifyWatchlistChanged } from "@/lib/watchlist-sync";
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
  getTradesPayload: vi.fn(),
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
  getRealPositions: vi.fn(),
  getLimitUpPoolSnapshot: vi.fn(),
  getSpeedRank: vi.fn(),
  getBoards: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  ...api,
}));

vi.mock("@/lib/market-hours", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/market-hours")>(),
  isTradingSession: () => true,
}));

vi.mock("@/lib/kline-live", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/kline-live")>();
  return { ...actual, mergeQuoteIntoMinutes: vi.fn(actual.mergeQuoteIntoMinutes) };
});

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
  MinuteChart: ({ points, index }: { points: MinutePoint[]; index?: {points: MinutePoint[]} | null }) => <div data-testid="minute-price" data-index-last={index?.points.at(-1)?.price ?? "none"} data-avg={points.at(-1)?.avg ?? "none"}>{points.at(-1)?.price}</div>,
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

function testQuoteFeed() {
  const data = new Map<string, Quote | null>();
  const listeners = new Map<string, Set<() => void>>();
  return {
    subscribe(symbol: string, listener: () => void) {
      const group = listeners.get(symbol) ?? new Set<() => void>();
      group.add(listener); listeners.set(symbol, group);
      return () => {group.delete(listener); if (!group.size) listeners.delete(symbol);};
    },
    getSnapshot: (symbol: string) => data.get(symbol),
    publish(symbol: string, value: Quote | null) {
      data.set(symbol, value); listeners.get(symbol)?.forEach(listener => listener());
    },
    listenerCount: (symbol: string) => listeners.get(symbol)?.size ?? 0,
  };
}

describe("StockDetailPanel · 即时报价复用", () => {
  it.each(["low", "stale", "invalid"] as const)("%s报价可作为保留读数但不能推进K线或分钟", async (quality) => {
    const quote = {...audit, quality, symbol: "600519", price: 88, data_timestamp: "2026-10-09T02:01:30Z"};
    const view = render(<StockDetailPanel symbol="600519" chartTab="kline" liveQuote={quote} />);
    await settle();
    expect(view.getByTestId("kline-price").textContent).toBe("20");
    expect(view.getByText(/未推进图表尾点/)).toBeTruthy();
    fireEvent.click(view.getByRole("button", {name: "分时"}));
    await settle();
    expect(view.getByTestId("minute-price").textContent).toBe("20");
    expect(view.getByText(/未推进图表尾点/)).toBeTruthy();
  });

  it("K线首取失败可重试，后续失败保留旧图且不显示确认空态", async () => {
    api.getKlinePayload.mockRejectedValueOnce(new Error("synthetic_failure"));
    const view = render(<StockDetailPanel symbol="600519" chartTab="kline" />);
    await settle();
    expect(view.getByRole("alert").textContent).toContain("K线读取失败");
    expect(view.queryByText(/本次未返回 K 线/)).toBeNull();
    fireEvent.click(view.getByRole("button", {name: "重新读取K线"}));
    await settle();
    expect(view.getByTestId("kline-price").textContent).toBe("20");
    api.getKlinePayload.mockRejectedValueOnce(new Error("synthetic_refresh_failure"));
    await advance(60_000);
    expect(view.getByRole("alert").textContent).toContain("保留上次结果");
    expect(view.getByTestId("kline-price").textContent).toBe("20");
  });

  it("财报首次失败不冒充缺数据，重试成功后有报告可读", async () => {
    api.getFinancials.mockRejectedValueOnce(new Error("synthetic_financial_failure"));
    const view = render(<StockDetailPanel symbol="600519" chartTab="kline" rightTab="profile" />);
    await settle();
    const panel = within(view.getByRole("tabpanel"));
    expect(panel.getByRole("alert").textContent).toContain("财报读取失败");
    expect(panel.queryByText("本次未返回财报数据。")).toBeNull();
    api.getFinancials.mockResolvedValueOnce([{report_date: "2026-06-30", revenue: 123, source: "synthetic_fixture"}]);
    fireEvent.click(panel.getByRole("button", {name: "重新读取财报"}));
    await settle();
    expect(panel.getByText("报告期 2026-06-30")).toBeTruthy();
    expect(panel.queryByRole("alert")).toBeNull();
  });
  it.each(["kline", "minute"] as const)("%s follows the selected feed immediately and never regresses to the delayed list prop", async (chartTab) => {
    const feed = testQuoteFeed();
    const first = {...audit, symbol: "600519", price: 20, volume: 100, amount: 2000, data_timestamp: "2026-10-09T02:00:00Z"};
    feed.publish("600519", first);
    const props = {symbol: "600519", chartTab, liveQuote: first, quoteFeed: feed};
    const view = render(<StockDetailPanel {...props} />); await settle();
    const second = {...first, price: 22, volume: 200, amount: 4400, data_timestamp: "2026-10-09T02:00:30Z", received_at: "2026-10-09T02:00:31Z"};
    await act(async () => {feed.publish("600519", second);});
    expect(view.getByTestId(`${chartTab}-price`).textContent).toBe("22");
    expect(view.container.querySelector(".quote-sheet")?.textContent).toContain("22.00");
    view.rerender(<StockDetailPanel {...props} liveQuote={{...first, price: 21}} />);
    expect(view.getByTestId(`${chartTab}-price`).textContent).toBe("22");
    if (chartTab === "minute") {
      expect(mergeQuoteIntoMinutes).toHaveBeenLastCalledWith(minute().points, second, false);
      expect(view.getByTestId("minute-price").getAttribute("data-avg")).toBe("22");
    }
    expect(calls.symbols.every(symbols => symbols.length === 0)).toBe(true);
  });

  it("hidden detail stops the selected subscription, preserves its chart and resumes from the current feed without a new stream", async () => {
    const feed = testQuoteFeed();
    const first = {...audit, symbol: "600519", price: 21, data_timestamp: "2026-10-09T02:00:00Z"};
    feed.publish("600519", first);
    const props = {symbol: "600519", quoteFeed: feed};
    const view = render(<StockDetailPanel {...props} />); await settle();
    expect(feed.listenerCount("600519")).toBe(1);
    expect(view.getByTestId("kline-price").textContent).toBe("21");
    view.rerender(<StockDetailPanel {...props} active={false} />);
    expect(feed.listenerCount("600519")).toBe(0);
    const merges = vi.mocked(mergeQuoteIntoMinutes).mock.calls.length;
    await act(async () => {feed.publish("600519", {...first, price: 22, data_timestamp: "2026-10-09T02:00:30Z"});});
    expect(view.getByTestId("kline-price").textContent).toBe("21");
    expect(mergeQuoteIntoMinutes).toHaveBeenCalledTimes(merges);
    view.rerender(<StockDetailPanel {...props} active />); await settle();
    expect(feed.listenerCount("600519")).toBe(1);
    expect(view.getByTestId("kline-price").textContent).toBe("22");
    view.rerender(<StockDetailPanel {...props} symbol="000001" />); await settle();
    expect(feed.listenerCount("600519")).toBe(0);
    expect(feed.listenerCount("000001")).toBe(1);
    await act(async () => {feed.publish("600519", {...first, price: 99}); feed.publish("000001", {...first, symbol: "000001", price: 23});});
    expect(view.getByTestId("kline-price").textContent).toBe("23");
    expect(calls.symbols.every(symbols => symbols.length === 0)).toBe(true);
    view.unmount(); expect(feed.listenerCount("000001")).toBe(0);
  });

  it("a missing latest frame keeps the last selected chart with an explicit hint instead of reviving the slow prop", async () => {
    const feed = testQuoteFeed();
    const old = {...audit, symbol: "600519", price: 20, data_timestamp: "2026-10-09T02:00:00Z"};
    const latest = {...old, price: 22, data_timestamp: "2026-10-09T02:00:30Z"};
    feed.publish("600519", latest);
    const props = {symbol: "600519", quoteFeed: feed, liveQuote: old};
    const view = render(<StockDetailPanel {...props} />); await settle();
    await act(async () => {feed.publish("600519", null);});
    view.rerender(<StockDetailPanel {...props} liveQuote={{...old, price: 21}} />);
    expect(view.getByTestId("kline-price").textContent).toBe("22");
    expect(view.container.querySelector(".quote-sheet")?.textContent).toContain("22.00");
    expect(view.getByText(/最近行情帧缺少该证券/)).toBeTruthy();
    await act(async () => {feed.publish("600519", {...latest, price: 23});});
    expect(view.getByTestId("kline-price").textContent).toBe("23");
    expect(view.queryByText(/最近行情帧缺少该证券/)).toBeNull();
  });

  it("a missing quote frame suspends precheck eligibility, preserves the trading draft and rechecks on recovery", async () => {
    api.checkOrderRisk.mockImplementation(async () => ({allowed: true, checked_at: new Date().toISOString(), estimated_fee: 5, max_qty: 1000, reasons: [], warnings: [], state: "allowed"}));
    const feed = testQuoteFeed();
    const latest = {...audit, symbol: "600519", price: 22, data_timestamp: "2026-10-09T02:00:30Z"};
    feed.publish("600519", latest);
    const view = render(<StockDetailPanel symbol="600519" rightTab="trade" quoteFeed={feed} />); await settle();
    fireEvent.change(view.getByLabelText("价格"), {target: {value: "21.8"}});
    fireEvent.change(view.getByLabelText("数量"), {target: {value: "200"}});
    await advance(300);
    const submit = () => view.getByRole("button", {name: "买入 600519"}) as HTMLButtonElement;
    expect(submit().disabled).toBe(false);
    await act(async () => {feed.publish("600519", null);});
    expect(submit().disabled).toBe(true);
    const checks = api.checkOrderRisk.mock.calls.length;
    await advance(1000); expect(api.checkOrderRisk).toHaveBeenCalledTimes(checks);
    expect((view.getByLabelText("价格") as HTMLInputElement).value).toBe("21.8");
    expect((view.getByLabelText("数量") as HTMLInputElement).value).toBe("200");
    await act(async () => {feed.publish("600519", {...latest, price: 23});});
    expect(submit().disabled).toBe(true);
    await advance(300);
    expect(api.checkOrderRisk.mock.calls.length).toBeGreaterThan(checks);
    expect(submit().disabled).toBe(false);
  });

  it("late valuation enrichment cannot mix older REST flow fields or provenance into the latest accepted quote", async () => {
    const enrichment = deferred<Quote>(); api.getQuote.mockReturnValueOnce(enrichment.promise);
    const feed = testQuoteFeed();
    const first = {...audit, symbol: "600519", price: 20, volume: 100_000, amount: 2_000_000, data_timestamp: "2026-10-09T02:00:00Z"};
    feed.publish("600519", first);
    const view = render(<StockDetailPanel symbol="600519" quoteFeed={feed} />); await settle();
    const latest = {...first, price: 22, volume: 200_000, amount: 4_400_000, high: 23, data_timestamp: "2026-10-09T02:00:30Z", received_at: "2026-10-09T02:00:31Z"};
    await act(async () => {feed.publish("600519", latest); enrichment.resolve({...first, price: 19, source: "tencent", received_at: "2026-10-09T02:00:45Z", pe_ttm: 32});});
    const metrics = view.getByRole("region", {name: "行情详细指标"});
    const metric = (name: string) => [...metrics.querySelectorAll(".quote-metric")].find(node => node.firstElementChild?.textContent === name)?.textContent;
    expect(metric("成交量")).toContain("2,000手");
    expect(metric("成交额")).toContain("440.00 万");
    expect(metric("最高")).toContain("23.00");
    expect(metric("PE")).toContain("32.00");
    expect(view.container.querySelector(".quote-source")?.textContent).toContain("通达信");
    expect(view.getByTestId("kline-price").textContent).toBe("22");
  });
});

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-09T02:00:00Z"));
  vi.mocked(mergeQuoteIntoMinutes).mockClear();
  for (const fn of Object.values(api)) fn.mockReset();
  api.getKlinePayload.mockImplementation(async (symbol: string) => kline(symbol));
  api.getOrderBook.mockImplementation(async (symbol: string) => book(symbol));
  api.getMinuteLineWithBaseline.mockResolvedValue(minute());
  api.getTrades.mockResolvedValue([]);
  api.getTradesPayload.mockImplementation(async (symbol: string, limit: number) => ({
    items: await api.getTrades(symbol, limit), source: null, empty_reason: null,
    meta: {provider: "synthetic_fixture", is_realtime: false, is_stale: false, last_success_refresh: null, generated_at: "2026-10-09T02:00:00Z"},
  }));
  api.getQuote.mockImplementation(async (symbol: string) => ({ ...audit, symbol, price: 20 }));
  api.getWatchlist.mockResolvedValue([]);
  api.getStockThemes.mockResolvedValue(null);
  api.getEventsForSymbol.mockImplementation(async (symbol: string) => ({ symbol, themes: [], count: 0, items: [] }));
  api.getMinuteLine.mockResolvedValue([]);
  api.getMarketOverview.mockResolvedValue({ indices: [] });
  api.getCapitalFlow.mockResolvedValue(null);
  api.getAuction.mockResolvedValue(null);
  api.getFinancials.mockResolvedValue(null);
  api.getCompanyProfile.mockResolvedValue(null);
  api.getNewsDigest.mockResolvedValue({ announcements: [], news: [], announcements_error: null, news_error: null });
  api.getThemesCatalog.mockResolvedValue([]);
  api.getRealPositions.mockResolvedValue({items: [], cleared: [], total: {market_value: 0, cost_total: 0, unrealized_pnl: 0, realized_pnl: 0}, count: 0});
  api.getLimitUpPoolSnapshot.mockResolvedValue({trade_date: "2026-10-09", pool: []});
  api.getSpeedRank.mockResolvedValue({theme: "881156.TI", theme_name: "粮食概念", window: "5m", basis: "最近5分钟", items: []});
  api.getBoards.mockResolvedValue([]);
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

  it.each(["success", "failure"])("same-scope old paper poll %s cannot overwrite the post-cancel refresh", async (outcome) => {
    const old = deferred<PaperFill[]>();
    api.getPaperAccount.mockResolvedValueOnce(account(100)).mockResolvedValueOnce(account(111)).mockResolvedValue(account(222));
    api.getPaperPositions.mockResolvedValueOnce([position("600519", 200, 17)]).mockResolvedValueOnce([position("600519", 100, 11)]).mockResolvedValue([position("600519", 300, 19)]);
    api.getPaperOrders.mockResolvedValueOnce([{id: 7, symbol: "600519", side: "buy", price: 19, quantity: 100, status: "pending"}]).mockResolvedValueOnce([{id: 7, symbol: "600519", side: "buy", price: 11, quantity: 100, status: "pending"}]).mockResolvedValue([]);
    api.getPaperFills.mockResolvedValueOnce([]).mockReturnValueOnce(old.promise).mockResolvedValue([fill("600519", 19)]);
    const view = render(<StockDetailPanel symbol="600519" rightTab="trade" />); await settle();
    await advance(30_000);
    fireEvent.click(view.getByRole("button", {name: "撤"})); await settle();
    expect(view.getByText("300股 · 可卖300")).toBeTruthy();
    await act(async () => {if (outcome === "success") old.resolve([fill("600519", 11)]); else old.reject(new Error("old failure"));});
    expect(view.getByText("300股 · 可卖300")).toBeTruthy();
    expect(view.queryByRole("button", {name: "撤"})).toBeNull();
    expect(view.queryByText("模拟账户数据加载失败（稍后自动重试）")).toBeNull();
    expect(view.queryByText(/保留上次读取的模拟账户/)).toBeNull();
    expect(view.getByTestId("kline-price").getAttribute("data-cost-price")).toBe("19");
    expect(view.getByTestId("kline-price").getAttribute("data-fill-count")).toBe("1");
  });

  it("paper refresh failure preserves the readable snapshot but blocks stale pending-order actions", async () => {
    api.checkOrderRisk.mockImplementation(async () => ({ allowed: true, checked_at: new Date().toISOString(), estimated_fee: 5, max_qty: 1000, reasons: [], warnings: [], state: "allowed" }));
    api.getPaperPositions.mockResolvedValue([position("600519", 200, 17)]);
    api.getPaperOrders.mockResolvedValue([{id: 7, symbol: "600519", side: "buy", price: 19, quantity: 100, status: "pending"}]);
    api.getPaperFills.mockResolvedValueOnce([fill("600519", 17)]).mockRejectedValue(new Error("current failure"));
    const view = render(<StockDetailPanel symbol="600519" rightTab="trade" />); await settle();
    fireEvent.change(view.getByLabelText("价格"), {target: {value: "19.8"}});
    fireEvent.change(view.getByLabelText("数量"), {target: {value: "200"}});
    await advance(300);
    const submit = () => view.getByRole("button", {name: "买入 600519"}) as HTMLButtonElement;
    expect(submit().disabled).toBe(false);
    fireEvent.click(view.getByRole("button", {name: "撤"})); await settle();
    expect(view.getByText(/保留上次读取的模拟账户/)).toBeTruthy();
    expect(view.getByText("200股 · 可卖200")).toBeTruthy();
    expect((view.getByRole("button", {name: "撤"}) as HTMLButtonElement).disabled).toBe(true);
    expect(submit().disabled).toBe(true);
    const checks = api.checkOrderRisk.mock.calls.length;
    await advance(1000);
    expect(api.checkOrderRisk).toHaveBeenCalledTimes(checks);
    expect(view.getByTestId("kline-price").getAttribute("data-fill-count")).toBe("1");
    api.getPaperFills.mockResolvedValue([fill("600519", 19)]);
    api.getPaperOrders.mockResolvedValue([]);
    api.getPaperPositions.mockResolvedValue([position("600519", 300, 19)]);
    await advance(30_000);
    expect(view.getByText("300股 · 可卖300")).toBeTruthy();
    expect(view.queryByText(/保留上次读取的模拟账户/)).toBeNull();
    expect(view.queryByRole("button", {name: "撤"})).toBeNull();
    expect((view.getByLabelText("价格") as HTMLInputElement).value).toBe("19.8");
    expect((view.getByLabelText("数量") as HTMLInputElement).value).toBe("200");
    expect(submit().disabled).toBe(true);
    await advance(300);
    expect(api.checkOrderRisk.mock.calls.length).toBeGreaterThan(checks);
    expect(submit().disabled).toBe(false);
  });
});

afterEach(() => {
  cleanup();
  calls.symbols.length = 0;
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("StockDetailPanel · 自选读取真实请求边界", () => {
  it("post-add failed calibration retains confirmed membership, while a later successful empty list removes it", async () => {
    api.getWatchlist.mockResolvedValueOnce([]).mockRejectedValueOnce(new Error("unavailable")).mockResolvedValueOnce([]);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({data: {symbol: "600519"}, meta: {}}))));
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" liveQuote={{...audit, symbol: "600519", price: 20}} />);
    await settle();
    fireEvent.click(view.getByRole("button", {name: "＋ 自选"}));
    await settle();
    expect(api.getWatchlist).toHaveBeenCalledTimes(2);
    expect(view.getByText("已在自选")).toBeTruthy();
    act(() => notifyWatchlistChanged());
    await settle();
    expect(api.getWatchlist).toHaveBeenCalledTimes(3);
    expect(view.queryByText("已在自选")).toBeNull();
    expect(view.getByRole("button", {name: "＋ 自选"})).toBeTruthy();
  });
  it("shares a real watchlist read with another owner and deactivation cancels only the detail subscriber", async () => {
    api.getWatchlist.mockImplementation(readActualWatchlist);
    let finish!: (response: Response) => void;
    let sourceSignal!: AbortSignal;
    const fetcher = vi.fn((_url: string, init: RequestInit) => {
      sourceSignal = init.signal as AbortSignal;
      return new Promise<Response>(resolve => {finish = resolve;});
    });
    vi.stubGlobal("fetch", fetcher);
    const otherOwner = readActualWatchlist();
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" liveQuote={{...audit, symbol: "600519", price: 20}} />);
    await settle();
    expect(fetcher).toHaveBeenCalledTimes(1);
    view.rerender(<StockDetailPanel symbol="600519" active={false} chartTab="flow" liveQuote={{...audit, symbol: "600519", price: 20}} />);
    await settle();
    expect(sourceSignal.aborted).toBe(false);
    await act(async () => {
      finish(new Response(JSON.stringify({data: [{symbol: "600519"}], meta: {}})));
      expect(await otherOwner).toEqual([{symbol: "600519"}]);
    });
    expect(view.queryByText("已在自选")).toBeNull();
  });

  it("add success stays selected when a pre-write shared read arrives late; write notification immediately recalibrates", async () => {
    api.getWatchlist.mockImplementation(readActualWatchlist);
    const pending: {init: RequestInit; finish: (data: unknown) => void}[] = [];
    const fetcher = vi.fn((_url: string, init: RequestInit) => new Promise<Response>(resolve => {
      pending.push({init, finish: data => resolve(new Response(JSON.stringify({data, meta: {}})))});
    }));
    vi.stubGlobal("fetch", fetcher);
    const previousOwner = readActualWatchlist();
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" liveQuote={{...audit, symbol: "600519", price: 20}} />);
    await settle();
    expect(fetcher).toHaveBeenCalledTimes(1);
    fireEvent.click(view.getByRole("button", {name: "＋ 自选"}));
    expect(pending[1].init.method).toBe("POST");
    await act(async () => {pending[1].finish({symbol: "600519"});});
    expect(view.getByText("已在自选")).toBeTruthy();
    expect(pending[0].init.signal?.aborted).toBe(false);
    await act(async () => {pending[0].finish([]); await previousOwner;});
    expect(view.getByText("已在自选")).toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(3);
    await act(async () => {pending[2].finish([{symbol: "600519"}]);});
    expect(view.queryByRole("button", {name: "＋ 自选"})).toBeNull();
  });
});

describe("StockDetailPanel · 右列与图表过渡", () => {
  it("collapse pauses book polling immediately, retains the exiting surface and keeps the chart instance/feed", async () => {
    vi.stubGlobal("matchMedia", () => ({matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn()}));
    const feed = testQuoteFeed();
    const first = {...audit, symbol: "600519", price: 20, data_timestamp: audit.received_at};
    feed.publish("600519", first);
    const view = render(<StockDetailPanel symbol="600519" chartTab="kline" quoteFeed={feed} />); await settle();
    const chart = view.getByTestId("kline-price");
    const side = view.container.querySelector(".detail-side-column")!;
    const reads = api.getOrderBook.mock.calls.length;
    fireEvent.click(view.getByRole("button", {name: "收起右列"}));
    expect(side.getAttribute("data-motion-state")).toBe("closed");
    expect(side.hasAttribute("inert")).toBe(true);
    expect(view.getByTestId("kline-price")).toBe(chart);
    await act(async () => {feed.publish("600519", {...first, price: 23, data_timestamp: "2026-10-09T02:00:30Z"});});
    expect(chart.textContent).toBe("23");
    await advance(159);
    expect(view.container.querySelector(".detail-side-column")).toBe(side);
    await advance(1);
    expect(view.container.querySelector(".detail-side-column")).toBeNull();
    await advance(10_000);
    expect(api.getOrderBook).toHaveBeenCalledTimes(reads);
    fireEvent.click(view.getByRole("button", {name: "展开右列"})); await settle();
    expect(api.getOrderBook).toHaveBeenCalledTimes(reads + 1);
    expect(view.getByTestId("kline-price")).toBe(chart);
  });

  it("only a real chart-tab change animates the stable column; quote ticks and right-column collapse do not", async () => {
    vi.stubGlobal("matchMedia", () => ({matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn()}));
    const cancel = vi.fn();
    const animate = vi.fn((_keyframes: Keyframe[], _options: KeyframeAnimationOptions) => ({cancel}));
    const original = HTMLElement.prototype.animate;
    HTMLElement.prototype.animate = animate as unknown as typeof original;
    try {
      const feed = testQuoteFeed();
      const first = {...audit, symbol: "600519", price: 20}; feed.publish("600519", first);
      const view = render(<StockDetailPanel symbol="600519" chartTab="kline" quoteFeed={feed} />); await settle();
      const column = view.container.querySelector(".detail-chart-column");
      expect(animate).not.toHaveBeenCalled();
      fireEvent.click(view.getByRole("button", {name: "分时"})); await settle();
      expect(view.container.querySelector(".detail-chart-column")).toBe(column);
      expect(animate).toHaveBeenCalledTimes(1);
      expect(animate.mock.calls[0][1]).toMatchObject({duration: 160});
      await act(async () => {feed.publish("600519", {...first, price: 22});});
      fireEvent.click(view.getByRole("button", {name: "收起右列"}));
      expect(animate).toHaveBeenCalledTimes(1);
      fireEvent.click(view.getByRole("button", {name: "K线"}));
      expect(animate).toHaveBeenCalledTimes(2);
      expect(cancel).toHaveBeenCalledTimes(1);
      view.unmount(); expect(cancel).toHaveBeenCalledTimes(2);
    } finally { HTMLElement.prototype.animate = original; }
  });

  it.each([[360, 360], [259, 300], [481, 300]])("restores a bounded saved width %s after hydration and preserves collapsed state", async (saved, expected) => {
    localStorage.setItem("ashare-right-w", String(saved));
    localStorage.setItem("ashare-right-collapsed", "1");
    const view = render(<StockDetailPanel symbol="600519" chartTab="kline" />); await settle();
    expect((view.container.querySelector(".detail-content-grid") as HTMLElement).style.getPropertyValue("--right-w")).toBe(`${expected}px`);
    expect(view.getByRole("button", {name: "展开右列"})).toBeTruthy();
  });
});

describe("StockDetailPanel · 资讯首次错误与旧结果保留", () => {
  const digest = () => ({announcements: [{title: "原公告依据", date: "2026-10-09", url: "https://example.invalid/announcement", source: "fixture"}], news: [{title: "原新闻依据", date: "2026-10-09", url: "https://example.invalid/news", source: "fixture"}], announcements_error: null, news_error: null});
  it("first digest failure is retryable and never says there are no announcements or news", async () => {
    api.getNewsDigest.mockRejectedValueOnce(new Error("digest-offline")).mockResolvedValue(digest());
    const view = render(<StockDetailPanel symbol="600519" rightTab="info" chartTab="flow" />);
    await advance(2500);
    expect(view.getByText(/公告源暂时不可用/)).toBeTruthy(); expect(view.getByText(/新闻源暂时不可用/)).toBeTruthy();
    expect(view.queryByText("暂无公告")).toBeNull(); expect(view.queryByText("暂无新闻")).toBeNull();
    fireEvent.click(view.getByRole("button", {name: "重试资讯"})); await settle();
    expect(view.getByText("原公告依据")).toBeTruthy(); expect(view.getByText("原新闻依据")).toBeTruthy();
    expect(view.queryByText(/源暂时不可用/)).toBeNull(); expect(api.getNewsDigest).toHaveBeenCalledTimes(2);
  });
  it("resume failure retains both original rows, while a different symbol never inherits them", async () => {
    api.getNewsDigest.mockResolvedValueOnce(digest()).mockRejectedValue(new Error("digest-offline"));
    const view = render(<StockDetailPanel symbol="600519" rightTab="info" chartTab="flow" />); await advance(2500);
    view.rerender(<StockDetailPanel symbol="600519" rightTab="info" chartTab="flow" active={false} />);
    view.rerender(<StockDetailPanel symbol="600519" rightTab="info" chartTab="flow" active />); await advance(2500);
    expect(view.getByText("原公告依据")).toBeTruthy(); expect(view.getByText("原新闻依据")).toBeTruthy();
    expect(view.getByText(/保留上次读取的新闻/)).toBeTruthy();
    view.rerender(<StockDetailPanel symbol="000001" rightTab="info" chartTab="flow" />);
    expect(view.queryByText("原公告依据")).toBeNull(); expect(view.queryByText("原新闻依据")).toBeNull();
    await advance(2500);
    expect(view.getByText(/新闻源暂时不可用/)).toBeTruthy(); expect(view.queryByText("暂无新闻")).toBeNull();
  });
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
    expect(within(view.getByRole("tabpanel")).getByText(/本次未返回成交记录/)).toBeTruthy();
    fireEvent.click(view.getByRole("tab", {name:"资料"}));
    expect(view.queryByText("买1")).toBeNull();
    expect(view.queryByText(/本次未返回成交记录/)).toBeNull();
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
  it.each([
    { symbol: "sh000001", isIndex: true, price: 3500, expectedAvg: "none" },
    { symbol: "600519", isIndex: false, price: 105.3, expectedAvg: "105.3" },
  ])("$symbol 的分时调用传递指数身份，只给股票计算累计额/股均价", async ({ symbol, isIndex, price, expectedAvg }) => {
    const points: MinutePoint[] = [
      { ts: "2026-10-09T09:59:00+08:00", price, avg: null, volume: 100_000, cum_volume: 900_000, cum_amount: 94_320_000, source: "tencent" },
      { ts: "2026-10-09T10:00:00+08:00", price, avg: null, volume: 100_000, cum_volume: 1_000_000, cum_amount: 104_800_000, source: "tencent" },
    ];
    const liveQuote: Quote = {
      ...audit, symbol, price, source: "tencent", volume: 1_100_000, amount: 115_830_000,
      data_timestamp: "2026-10-09T10:00:30+08:00",
    };
    api.getMinuteLineWithBaseline.mockResolvedValue({ points, vr_baseline_5m: null });
    const view = render(<StockDetailPanel symbol={symbol} chartTab="minute" liveQuote={liveQuote} />);
    await settle();

    expect(mergeQuoteIntoMinutes).toHaveBeenLastCalledWith(points, liveQuote, isIndex);
    expect(view.getByTestId("minute-price").textContent).toBe(String(price));
    expect(view.getByTestId("minute-price").getAttribute("data-avg")).toBe(expectedAvg);
  });

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
    expect(panel.getByRole("alert").textContent).toContain("盘口读取失败");
    expect(panel.queryByText(/本次未返回盘口/)).toBeNull();
    expect(api.getOrderBook).toHaveBeenCalledTimes(1);
    await advance(5000);
    expect(panel.getByText("买1")).toBeTruthy();
    api.getOrderBook.mockRejectedValueOnce(new Error("refresh unavailable"));
    await advance(5000);
    expect(panel.getByText("买1")).toBeTruthy();
    expect(panel.getByRole("alert").textContent).toContain("保留上次结果");
    api.getOrderBook.mockResolvedValueOnce({ ...book(), asks: [], bids: [] });
    await advance(5000);
    expect(panel.getByRole("table").querySelectorAll("tr")).toHaveLength(0);
    expect(panel.getByText("本次盘口返回空档位。")).toBeTruthy();
    expect(panel.queryByRole("alert")).toBeNull();
  });

  it("逐笔首取失败立即结束骨架，成功空数组也保持确认空态", async () => {
    api.getTrades.mockRejectedValueOnce(new Error("unavailable"));
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" rightTab="trades" />);
    await settle();
    const panel = within(view.getByRole("tabpanel"));
    expect(panel.getByRole("alert").textContent).toContain("成交读取失败");
    expect(panel.queryByText(/本次未返回成交记录/)).toBeNull();
    await advance(10_000);
    expect(panel.getByText(/本次未返回成交记录/)).toBeTruthy();
    expect(panel.queryByRole("alert")).toBeNull();
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
    expect(api.getFinancials).not.toHaveBeenCalled();
    expect(api.getNewsDigest).not.toHaveBeenCalled();
    expect(api.getCompanyProfile).not.toHaveBeenCalled();
    expect(api.getStockThemes).not.toHaveBeenCalled();
    expect(api.getKlinePayload).toHaveBeenCalledTimes(1);
    expect(api.getMinuteLineWithBaseline).toHaveBeenCalledTimes(1);
  });
});

 describe("StockDetailPanel · 隐藏详情与共享持仓", () => {
  const coreReads = () => [api.getQuote, api.getKlinePayload, api.getOrderBook, api.getMinuteLineWithBaseline, api.getTrades, api.getStockThemes, api.getWatchlist, api.getFinancials, api.getCompanyProfile, api.getNewsDigest, api.getAuction, api.getCapitalFlow, api.getMarketOverview];

  it("初次隐藏不建立自身WS、不发主读取；恢复立即读取并保留idle预取", async () => {
    const view = render(<StockDetailPanel symbol="600519" active={false} />);
    await advance(120_000);
    for (const read of coreReads()) expect(read).not.toHaveBeenCalled();
    for (const symbols of calls.symbols) expect(symbols).toEqual([]);
    view.rerender(<StockDetailPanel symbol="600519" active />);
    await settle();
    expect(api.getQuote).toHaveBeenCalledTimes(1);
    expect(api.getKlinePayload).toHaveBeenCalledTimes(1);
    expect(api.getOrderBook).toHaveBeenCalledTimes(1);
    expect(api.getStockThemes).toHaveBeenCalledTimes(1);
    await advance(900);
    expect(api.getMinuteLineWithBaseline).toHaveBeenCalledTimes(1);
    expect(api.getTrades).toHaveBeenCalledTimes(1);
  });

  it("已有streamStatus即认定上游所有者，不为尚缺的首帧quote再订阅", () => {
    render(<StockDetailPanel symbol="600519" chartTab="flow" streamStatus="connecting" />);
    for (const symbols of calls.symbols) expect(symbols).toEqual([]);
  });

  it("隐藏期间暂停主读取、忽略报价tick，恢复校准期间保留原图表", async () => {
    const view = render(<StockDetailPanel symbol="600519" />);
    await settle();
    expect(view.getByTestId("kline-price").textContent).toBe("20");
    view.rerender(<StockDetailPanel symbol="600519" active={false} />);
    const counts = coreReads().map(read => read.mock.calls.length);
    const renders = calls.symbols.length;
    await advance(120_000);
    view.rerender(<StockDetailPanel symbol="600519" active={false} liveQuote={{...audit, symbol: "600519", price: 333}} streamStatus="stale" />);
    expect(calls.symbols.length).toBe(renders);
    expect(coreReads().map(read => read.mock.calls.length)).toEqual(counts);
    expect(view.getByTestId("kline-price").textContent).toBe("20");
    const next = deferred<KlinePayload>();
    api.getKlinePayload.mockReturnValueOnce(next.promise);
    view.rerender(<StockDetailPanel symbol="600519" active />);
    await settle();
    expect(view.getByTestId("kline-price").textContent).toBe("20");
    await act(async () => { next.resolve(kline("600519", 44)); });
    expect(view.getByTestId("kline-price").textContent).toBe("44");
  });

  it.each<[ChartTab, RightTab]>([["kline", "book"], ["minute", "trades"]])("隐藏再恢复后旧 %s/%s 回包不能覆盖当前校准", async (chartTab, rightTab) => {
    const old = {kline: deferred<KlinePayload>(), book: deferred<OrderBook>(), minute: deferred<ReturnType<typeof minute>>(), trades: deferred<Trade[]>()};
    api.getKlinePayload.mockReturnValueOnce(old.kline.promise);
    api.getOrderBook.mockReturnValueOnce(old.book.promise);
    api.getMinuteLineWithBaseline.mockReturnValueOnce(old.minute.promise);
    api.getTrades.mockReturnValueOnce(old.trades.promise);
    const view = render(<StockDetailPanel symbol="600519" chartTab={chartTab} rightTab={rightTab} />);
    await advance(900);
    view.rerender(<StockDetailPanel symbol="600519" chartTab={chartTab} rightTab={rightTab} active={false} />);
    await advance(60_000);
    view.rerender(<StockDetailPanel symbol="600519" chartTab={chartTab} rightTab={rightTab} active />);
    await settle();
    const chartId = chartTab === "kline" ? "kline-price" : "minute-price";
    expect(view.getByTestId(chartId).textContent).toBe("20");
    await act(async () => {old.kline.resolve(kline("600519", 111)); old.book.resolve(book("600519", 111)); old.minute.resolve(minute(111)); old.trades.resolve(trades("600519", 111));});
    expect(view.getByTestId(chartId).textContent).toBe("20");
    expect(within(view.getByRole("tabpanel")).queryByText("111.00")).toBeNull();
  });

  it("旧估值/模拟账户晚回不能覆盖恢复后的结果，隐藏停止预检并保留交易草稿", async () => {
    const oldQuote = deferred<Quote>();
    const oldFills = deferred<PaperFill[]>();
    api.getQuote.mockReturnValueOnce(oldQuote.promise).mockResolvedValue({...audit, symbol: "600519", name: "当前报价", price: 22});
    api.getPaperFills.mockReturnValueOnce(oldFills.promise).mockResolvedValue([fill("600519", 22)]);
    api.getPaperPositions.mockResolvedValue([position("600519", 300, 22)]);
    const view = render(<StockDetailPanel symbol="600519" rightTab="trade" />);
    view.rerender(<StockDetailPanel symbol="600519" rightTab="trade" active={false} />);
    const paperCount = api.getPaperAccount.mock.calls.length;
    await advance(60_000);
    expect(api.getPaperAccount).toHaveBeenCalledTimes(paperCount);
    view.rerender(<StockDetailPanel symbol="600519" rightTab="trade" active />);
    await settle();
    expect(view.getByText("当前报价")).toBeTruthy();
    fireEvent.change(view.getByLabelText("价格"), {target: {value: "19.8"}});
    fireEvent.change(view.getByLabelText("数量"), {target: {value: "200"}});
    view.rerender(<StockDetailPanel symbol="600519" rightTab="trade" active={false} />);
    await advance(60_000);
    const checks = api.checkOrderRisk.mock.calls.length;
    await advance(60_000);
    expect(api.checkOrderRisk).toHaveBeenCalledTimes(checks);
    await act(async () => {oldQuote.resolve({...audit, symbol: "600519", name: "旧报价", price: 111}); oldFills.resolve([fill("600519", 111)]);});
    view.rerender(<StockDetailPanel symbol="600519" rightTab="trade" active />);
    await settle();
    expect(view.queryByText("旧报价")).toBeNull();
    expect((view.getByLabelText("价格") as HTMLInputElement).value).toBe("19.8");
    expect((view.getByLabelText("数量") as HTMLInputElement).value).toBe("200");
    expect(view.getByTestId("kline-price").getAttribute("data-cost-price")).toBe("22");
  });

  it("共享持仓的null/error也传到面板，不启动独立轮询，隐藏保留记账草稿", async () => {
    const shared = {data: null, error: "共享持仓读取失败", reload: vi.fn(async () => {})};
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="real" realPositions={shared} />);
    await settle();
    expect(view.getByText("共享持仓读取失败")).toBeTruthy();
    fireEvent.change(view.getByLabelText("实际成交价"), {target: {value: "19.8"}});
    fireEvent.change(view.getByLabelText("数量"), {target: {value: "200"}});
    view.rerender(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="real" realPositions={shared} active={false} />);
    await advance(60_000);
    expect(api.getRealPositions).not.toHaveBeenCalled();
    view.rerender(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="real" realPositions={shared} active />);
    expect((view.getByLabelText("实际成交价") as HTMLInputElement).value).toBe("19.8");
    expect((view.getByLabelText("数量") as HTMLInputElement).value).toBe("200");
  });

  it.each(["speed", "boards"] as const)("隐藏时停止指数%s子轮询，恢复立即重新读取并拒绝旧回包", async rightTab => {
    api.getThemesCatalog.mockResolvedValue([{code: "881156.TI", name: "粮食概念"}]);
    const endpoint = rightTab === "speed" ? api.getSpeedRank : api.getBoards;
    let finish!: (value: unknown) => void;
    endpoint.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const view = render(<StockDetailPanel symbol="sh000001" chartTab="kline" rightTab={rightTab} />);
    await settle();
    expect(endpoint).toHaveBeenCalledTimes(1);
    view.rerender(<StockDetailPanel symbol="sh000001" chartTab="kline" rightTab={rightTab} active={false} />);
    await advance(120_000);
    expect(endpoint).toHaveBeenCalledTimes(1);
    view.rerender(<StockDetailPanel symbol="sh000001" chartTab="kline" rightTab={rightTab} active />);
    await settle();
    expect(endpoint).toHaveBeenCalledTimes(2);
    await act(async () => { finish(rightTab === "speed" ? {theme: "881156.TI", theme_name: "旧题材回包", window: "5m", items: [], note: "旧回包标志"} : [{code: "old", name: "旧板块回包"}]); });
    expect(view.queryByText("旧回包标志")).toBeNull();
    expect(view.queryByText("旧板块回包")).toBeNull();
  });
});

 describe("StockDetailPanel · 恢复失败保留既有结果", () => {
  it.each([
    {endpoint: "getOrderBook" as const, tab: "盘口", result: "66.00"},
    {endpoint: "getTrades" as const, tab: "逐笔", result: "77.00"},
    {endpoint: "getFinancials" as const, tab: "资料", result: "报告期 2026-06-30"},
  ])("恢复时$endpoint预取失败保留同股上次结果", async ({endpoint, tab, result}) => {
    api.getOrderBook.mockResolvedValue(book("600519", 66));
    api.getTrades.mockResolvedValue(trades("600519", 77));
    api.getFinancials.mockResolvedValue([{report_date: "2026-06-30", source: "eastmoney", revenue: 123_000_000}]);
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="profile" />);
    await advance(900);
    expect(api[endpoint]).toHaveBeenCalledTimes(1);
    api[endpoint].mockRejectedValue(new Error("恢复读取失败"));
    view.rerender(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="profile" active={false} />);
    view.rerender(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="profile" active />);
    await advance(900);
    expect(api[endpoint]).toHaveBeenCalledTimes(2);
    fireEvent.click(view.getByRole("tab", {name: tab}));
    await settle();
    expect(within(view.getByRole("tabpanel")).getByText(result)).toBeTruthy();
  });

  it("指数叠加仅在分时可见时读取，隐藏晚包无效，恢复与同日定时校准使用新结果", async () => {
    const old = deferred<MinutePoint[]>();
    api.getMinuteLine.mockReturnValueOnce(old.promise);
    const view = render(<StockDetailPanel symbol="600519" />);
    await advance(900);
    expect(api.getMinuteLine).not.toHaveBeenCalled();
    expect(api.getMarketOverview).not.toHaveBeenCalled();
    api.getMarketOverview.mockResolvedValue({indices: [{...audit, symbol: "sh000001", price: 3000, prev_close: 2990, data_timestamp: "2026-10-09T02:00:00Z"}]});
    fireEvent.click(view.getByRole("button", {name: "分时"}));
    await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(1);
    expect(api.getMarketOverview).toHaveBeenCalledTimes(1);
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" active={false} />);
    await act(async () => {old.resolve(minute(111).points);});
    expect(view.getByTestId("minute-price").getAttribute("data-index-last")).toBe("none");
    api.getMinuteLine.mockResolvedValue(minute(3000).points);
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" active />);
    await settle();
    expect(view.getByTestId("minute-price").getAttribute("data-index-last")).toBe("3000");
    api.getMinuteLine.mockResolvedValue(minute(3030).points);
    await advance(60_000);
    expect(view.getByTestId("minute-price").getAttribute("data-index-last")).toBe("3030");
    api.getMinuteLine.mockRejectedValueOnce(new Error("synthetic_index_failure"));
    await advance(60_000);
    expect(view.getByTestId("minute-price").getAttribute("data-index-last")).toBe("3030");
    expect(view.getByRole("alert").textContent).toContain("指数叠加校准失败");
  });
});
