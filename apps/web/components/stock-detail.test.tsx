import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StockDetailPanel, type ChartTab, type RightTab } from "@/components/stock-detail";
import type { KlinePayload, MinutePoint, PaperAccountInfo, PaperFill, PaperPositionInfo } from "@/lib/api";
import { mergeQuoteIntoMinutes } from "@/lib/kline-live";
import { getWatchlist as readActualWatchlist } from "@/lib/api/watchlist";
import { notifyWatchlistChanged } from "@/lib/watchlist-sync";
import type { Kline, OrderBook, Quote, Trade } from "@/types/market";
import {DetailModalProvider} from "@/components/detail/detail-modal";

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
  getIndexMembers: vi.fn(),
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
  getStockAttribution: vi.fn(),
  getEventAttribution: vi.fn(),
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
  MinuteChart: ({ points, index, prevClose, referenceDate, upperPrice, lowerPrice }: { points: MinutePoint[]; index?: {points: MinutePoint[]; prevClose?: number} | null; prevClose?: number | null; referenceDate?: string | null; upperPrice?: number | null; lowerPrice?: number | null }) => <div data-testid="minute-price" data-index-last={index?.points.at(-1)?.price ?? "none"} data-index-clock={index?.points.at(-1)?.ts ?? "none"} data-index-prev-close={index?.prevClose ?? "none"} data-avg={points.at(-1)?.avg ?? "none"} data-prev-close={prevClose ?? "none"} data-reference-date={referenceDate ?? "none"} data-upper-price={upperPrice ?? "none"} data-lower-price={lowerPrice ?? "none"}>{points.at(-1)?.price}</div>,
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
  it.each(["trade", "real", "book"] as const)("%s不再挂载前台模拟账户或触发模拟读取", async rightTab => {
    render(<StockDetailPanel symbol="600519" chartTab="flow" rightTab={rightTab}/>); await settle();
    expect(api.getPaperAccount).not.toHaveBeenCalled();
    expect(api.getPaperPositions).not.toHaveBeenCalled();
    expect(api.getPaperFills).not.toHaveBeenCalled();
    expect(api.getPaperOrders).not.toHaveBeenCalled();
    expect(screen.queryByRole("tab", {name:"模拟"})).toBeNull();
  });

  it("历史分钟尚未就绪时，同一可信feed先显示已观察当前点，并同步后续价格", async () => {
    api.getMinuteLineWithBaseline.mockResolvedValue({points:[], vr_baseline_5m:null});
    const feed = testQuoteFeed();
    const first: Quote = {...audit, symbol:"600519", price:22, data_timestamp:"2026-10-09T02:00:30Z"};
    feed.publish("600519", first);
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" quoteFeed={feed} streamStatus="live"/>);
    await settle();
    expect(view.getByTestId("minute-price").textContent).toBe("22");
    await act(async () => {feed.publish("600519", {...first,price:23,data_timestamp:"2026-10-09T02:00:31Z"});});
    expect(view.getByTestId("minute-price").textContent).toBe("23");
    expect(api.getNewsDigest).not.toHaveBeenCalled();
    expect(calls.symbols.every(s => s.length === 0)).toBe(true);
  });

  it.each(["kline", "minute"] as const)("%s拒旧帧保留已显示的可信尾点，不能退回更旧REST基座再弹回", async chartTab => {
    const feed = testQuoteFeed();
    const first: Quote = {...audit, symbol: "600519", price: 22, data_timestamp: "2026-10-09T02:00:30Z"};
    feed.publish("600519", first);
    const view = render(<StockDetailPanel symbol="600519" chartTab={chartTab} quoteFeed={feed} streamStatus="live"/>); await settle();
    const chart = view.getByTestId(chartTab === "kline" ? "kline-price" : "minute-price");
    expect(chart.textContent).toBe("22");
    await act(async () => {feed.publish("600519", {...first, quality: "stale", quality_reasons: ["source_time_regress_ignored"]});});
    expect(chart.textContent).toBe("22");
    expect(view.container.querySelector("[data-quality=stale]")).toBeTruthy();
    await act(async () => {feed.publish("600519", {...first, price: 23, data_timestamp: "2026-10-09T02:00:31Z"});});
    expect(chart.textContent).toBe("23");
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });

  it.each(["kline", "minute"] as const)("%s正式新REST基座和换日不会借用旧的可信尾点", async chartTab => {
    const feed = testQuoteFeed();
    const first: Quote = {...audit, symbol: "600519", price: 22, data_timestamp: "2026-10-09T02:00:30Z"};
    feed.publish("600519", first);
    const view = render(<StockDetailPanel symbol="600519" chartTab={chartTab} quoteFeed={feed} streamStatus="live"/>); await settle();
    const chart = view.getByTestId(chartTab === "kline" ? "kline-price" : "minute-price");
    expect(chart.textContent).toBe("22");
    await act(async () => {feed.publish("600519", {...first, quality: "stale", quality_reasons: ["source_time_regress_ignored"]});});
    if (chartTab === "kline") api.getKlinePayload.mockResolvedValueOnce(kline("600519", 25));
    else api.getMinuteLineWithBaseline.mockResolvedValueOnce(minute(25));
    await advance(60_000);
    expect(chart.textContent).toBe("25");
    await act(async () => {feed.publish("600519", {...first, price: 26, data_timestamp: "2026-10-09T02:00:31Z"});});
    expect(chart.textContent).toBe("26");
    await act(async () => {feed.publish("600519", {...first, price: 40, data_timestamp: "2026-10-10T02:00:31Z", quality: "stale", quality_reasons: ["refresh_failed"]});});
    expect(chart.textContent).toBe("25");
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });

  it.each(["kline", "minute"] as const)("%s无源钟的拒绝帧不臆造新日期，也不抹掉已显示可信尾点", async chartTab => {
    const feed = testQuoteFeed();
    const first: Quote = {...audit, symbol: "600519", price: 22, data_timestamp: "2026-10-09T02:00:30Z"};
    feed.publish("600519", first);
    const view = render(<StockDetailPanel symbol="600519" chartTab={chartTab} quoteFeed={feed} streamStatus="live"/>); await settle();
    const chart = view.getByTestId(chartTab === "kline" ? "kline-price" : "minute-price");
    expect(chart.textContent).toBe("22");
    await act(async () => {feed.publish("600519", {...first, data_timestamp: null, quality: "stale", quality_reasons: ["source_time_unknown_ignored"]});});
    expect(chart.textContent).toBe("22");
    expect(view.getByRole("button", {name: "查看行情问题"}).textContent).toBe("时间待核");
    expect(view.container.querySelector("[data-quality=stale]")).toBeTruthy();
  });

  it("质量和缺项只更新头部固定槽，图内不重复挂载报价提示或挤压图表", async () => {
    const feed = testQuoteFeed();
    const first: Quote = {...audit, symbol: "600519", price: 22, data_timestamp: "2026-10-09T02:00:30Z"};
    feed.publish("600519", first);
    const props = {symbol: "600519", quoteFeed: feed, streamStatus: "live" as const};
    const view = render(<StockDetailPanel {...props}/>); await settle();
    const chart = view.container.querySelector(".detail-chart-column")!;
    expect(within(chart as HTMLElement).queryByRole("status")).toBeNull();
    await act(async () => {feed.publish("600519", {...first, quality: "stale", quality_reasons: ["refresh_failed"]});});
    const header = view.container.querySelector(".quote-sheet") as HTMLElement;
    expect(within(header).getByRole("button", {name: "查看行情问题"}).textContent).toContain("刷新异常");
    expect(within(chart as HTMLElement).queryByText("刷新异常")).toBeNull();
    fireEvent.click(within(header).getByRole("button", {name: "查看行情问题"}));
    expect(within(view.getByRole("dialog", {name: "行情诊断"})).getByText(/源数据时间/)).toHaveProperty("textContent", expect.stringContaining("2026-10-09 10:00:30"));
    fireEvent.click(view.getByRole("button", {name: "关闭"}));
    await advance(200);
    await act(async () => {feed.publish("600519", null);});
    expect(within(header).getByRole("button", {name: "查看行情问题"}).textContent).toContain("报价待恢复");
    expect(within(chart as HTMLElement).queryByText("报价待恢复")).toBeNull();
    expect(view.getByTestId("kline-price").textContent).toBe("22");
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });

  it.each(["kline", "minute", "flow"] as const)("%s正常休市状态只在行情头部显示，连接和源读取异常仍可见", async chartTab => {
    vi.setSystemTime(new Date("2026-10-14T08:00:00+08:00"));
    const quote: Quote = {...audit, symbol: "600519", price: 88, quality: "stale", quality_reasons: ["market_closed"],
      data_timestamp: "2026-10-13T15:00:00+08:00",
      display_session: {phase: "closed", trade_date: "2026-10-14", as_of: "2026-10-14T08:00:00+08:00", trade_day_result: true, expected_source_date: "2026-10-13", expected_source_after: "2026-10-13T14:59:50+08:00"}};
    const props = {symbol: "600519", chartTab, liveQuote: quote};
    const view = render(<StockDetailPanel {...props} streamStatus="live" />); await settle();
    expect(view.getAllByText("未开盘")).toHaveLength(1);
    expect(within(view.container.querySelector(".quote-sheet") as HTMLElement).getByText("未开盘")).toBeTruthy();
    view.rerender(<StockDetailPanel {...props} streamStatus="polling" />); await settle();
    expect(view.getByText("备用读取")).toBeTruthy();
    view.rerender(<StockDetailPanel {...props} streamStatus="error" />); await settle();
    expect(view.getByText("备用读取失败")).toBeTruthy();
    view.rerender(<StockDetailPanel {...props} streamStatus="live" liveQuote={{...quote, quality_reasons: ["market_closed", "refresh_failed"]}} />); await settle();
    expect(view.getByRole("button", {name: "查看行情问题"}).textContent).toBe("刷新异常");
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });

  it("REST基座尾记录按北京时间显示，保留原始bar身份", async () => {
    const original = kline(); original.bars[0].ts = "2026-10-09T10:00:12+08:00";
    api.getKlinePayload.mockResolvedValue(original);
    const quote = {...audit, quality: "stale" as const, symbol: "600519", price: 88, data_timestamp: "2026-10-09T02:01:30Z"};
    const view = render(<StockDetailPanel symbol="600519" liveQuote={quote} />); await settle();
    fireEvent.click(view.getByRole("button", {name: "查看行情问题"}));
    const drawer = within(view.getByRole("dialog"));
    expect(drawer.getByText(/REST基座/).textContent).toContain("尾记录 2026-10-09 10:00:12");
    expect(drawer.getByText(/REST基座/).textContent).not.toContain("T10:00:12+08:00");
    expect(original.bars[0].ts).toBe("2026-10-09T10:00:12+08:00");
  });
  it.each(["low", "stale", "invalid"] as const)("%s报价可作为保留读数但不能推进K线或分钟", async (quality) => {
    const quote = {...audit, quality, symbol: "600519", price: 88, data_timestamp: "2026-10-09T02:01:30Z"};
    const view = render(<StockDetailPanel symbol="600519" chartTab="kline" liveQuote={quote} />);
    await settle();
    expect(view.getByTestId("kline-price").textContent).toBe("20");
    expect(view.queryByText(/未推进图表尾点/)).toBeNull();
    fireEvent.click(view.getByRole("button", {name: "查看行情问题"}));
    expect(within(view.getByRole("dialog")).getByText(/未推进图表尾点/)).toBeTruthy();
    fireEvent.click(within(view.getByRole("dialog")).getByRole("button", {name: "关闭"}));
    await advance(200);
    fireEvent.click(view.getByRole("button", {name: "分时"}));
    await settle();
    expect(view.getByTestId("minute-price").textContent).toBe("20");
    expect(view.queryByText(/未推进图表尾点/)).toBeNull();
    fireEvent.click(view.getByRole("button", {name: "查看行情问题"}));
    expect(within(view.getByRole("dialog")).getByText(/未推进图表尾点/)).toBeTruthy();
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
    expect(view.getByRole("alert").textContent).toContain("保留结果");
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
    expect(view.getByRole("button", {name: "查看行情问题"}).textContent).toBe("报价待恢复");
    expect(view.queryByText(/最近行情帧缺少该证券/)).toBeNull();
    fireEvent.click(view.getByRole("button", {name: "查看行情问题"}));
    expect(within(view.getByRole("dialog")).getByText(/最近行情帧缺少该证券/)).toBeTruthy();
    fireEvent.click(within(view.getByRole("dialog")).getByRole("button", {name: "关闭"}));
    await advance(200);
    await act(async () => {feed.publish("600519", {...latest, price: 23});});
    expect(view.getByTestId("kline-price").textContent).toBe("23");
    expect(view.queryByText(/最近行情帧缺少该证券/)).toBeNull();
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
  api.getIndexMembers.mockImplementation(async (symbol: string) => ({symbol, name: "合成指数", source: "fixture", snapshot_at: "2026-10-15T07:29:07+00:00", items: []}));
  api.getAuction.mockResolvedValue(null);
  api.getFinancials.mockResolvedValue(null);
  api.getCompanyProfile.mockResolvedValue(null);
  api.getNewsDigest.mockResolvedValue({ announcements: [], news: [], announcements_error: null, news_error: null });
  api.getThemesCatalog.mockResolvedValue([]);
  api.getRealPositions.mockResolvedValue({items: [], cleared: [], total: {market_value: 0, cost_total: 0, unrealized_pnl: 0, realized_pnl: 0}, count: 0});
  api.getLimitUpPoolSnapshot.mockResolvedValue({trade_date: "2026-10-09", pool: []});
  api.getSpeedRank.mockResolvedValue({theme: "881156.TI", theme_name: "粮食概念", window: "5m", basis: "最近5分钟", items: []});
  api.getBoards.mockResolvedValue([]);
  api.getStockAttribution.mockImplementation(async (symbol: string) => ({schema_version: 1, symbol, count: 0, items: []}));
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
  it("财务首次进入读取一次，同一页签重复点选不重复GET", async () => {
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="book"/>); await settle();
    await advance(900);
    expect(api.getFinancials).not.toHaveBeenCalled();
    fireEvent.click(view.getByRole("tab", {name: "资料"})); await settle();
    expect(api.getFinancials).toHaveBeenCalledTimes(1);
    fireEvent.click(view.getByRole("tab", {name: "资料"})); await settle();
    expect(api.getFinancials).toHaveBeenCalledTimes(1);
    view.rerender(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="profile" active={false}/>); await settle();
    view.rerender(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="profile" active/>); await settle();
    expect(api.getFinancials).toHaveBeenCalledTimes(2);
  });

  it("财务只在资料首入读取，失败后恢复仍重读，不把失败当作已完成缓存", async () => {
    api.getFinancials.mockRejectedValueOnce(new Error("financial prefetch failed"));
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="book"/>); await settle();
    await advance(900);
    expect(api.getFinancials).not.toHaveBeenCalled();
    fireEvent.click(view.getByRole("tab", {name: "资料"})); await settle();
    expect(api.getFinancials).toHaveBeenCalledTimes(1);
    view.rerender(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="profile" active={false}/>); await settle();
    view.rerender(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="profile" active/>); await settle();
    expect(api.getFinancials).toHaveBeenCalledTimes(2);
  });

  it.each([null, false, true])("父owner的membership=%s替代子详情GET和写后订阅，切股/手机恢复仍不重复读", async membership => {
    const props = {symbol: "600519", chartTab: "flow" as const, liveQuote: {...audit, symbol: "600519", price: 20}, watchlistMembership: membership};
    const view = render(<StockDetailPanel {...props}/>); await settle();
    expect(api.getWatchlist).not.toHaveBeenCalled();
    if (membership === null) {
      expect(view.getByText("自选待确认")).toBeTruthy();
      expect(view.getByRole("button", {name: "＋ 自选"})).toBeTruthy();
    } else expect(!!view.queryByText("已在自选")).toBe(membership);
    act(() => notifyWatchlistChanged()); await settle();
    view.rerender(<StockDetailPanel {...props} symbol="000001" liveQuote={{...audit, symbol: "000001", price: 10}}/>); await settle();
    view.rerender(<StockDetailPanel {...props} active={false}/>); await settle();
    view.rerender(<StockDetailPanel {...props} active/>); await settle();
    expect(api.getWatchlist).not.toHaveBeenCalled();
  });

  it.each([null, false])("父owner的membership=%s时，加自选成功回执先交给同一owner，再由原写后事件校准", async membership => {
    const confirmed = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({data: {symbol: "600519"}, meta: {}}))));
    const props = {symbol: "600519", chartTab: "flow" as const, liveQuote: {...audit, symbol: "600519", price: 20}, watchlistMembership: membership, onWatchlistAdded: confirmed};
    const view = render(<StockDetailPanel {...props}/>); await settle();
    fireEvent.click(view.getByRole("button", {name: "＋ 自选"})); await settle();
    expect(confirmed).toHaveBeenCalledExactlyOnceWith("600519");
    expect(api.getWatchlist).not.toHaveBeenCalled();
    view.rerender(<StockDetailPanel {...props} watchlistMembership/>); await settle();
    expect(view.getByText("已在自选")).toBeTruthy();
  });

  it("父owner未知时仍可尝试添加，POST失败保持未知且不增加子详情GET", async () => {
    const confirmed = vi.fn();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({detail: "write unavailable"}), {status: 503}));
    vi.stubGlobal("fetch", fetcher);
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" liveQuote={{...audit, symbol: "600519", price: 20}} watchlistMembership={null} onWatchlistAdded={confirmed}/>); await settle();
    fireEvent.click(view.getByRole("button", {name: "＋ 自选"})); await settle();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]?.[1]).toMatchObject({method: "POST"});
    expect(view.getByText(/加入自选失败/)).toBeTruthy();
    expect(view.getByText("自选待确认")).toBeTruthy();
    expect(view.queryByText("已在自选")).toBeNull();
    expect(confirmed).not.toHaveBeenCalled();
    expect(api.getWatchlist).not.toHaveBeenCalled();
  });

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
  const currentQuote = {...audit, symbol: "600519", price: 20, data_timestamp: "2026-10-09T10:00:00+08:00"};
  const qualifiedPool = () => ({trade_date: "2026-10-09", pool: [{...audit, symbol: "600519", trade_date: "2026-10-09", reason: "拟收购+需审批"}], break_pool: [],
    coverage: {limit_up: {state: "success", error: null}, limit_break: {state: "success", error: null}}, qualification_observations: {}});

  it("只给真实同日涨停或炸板成员显示原因，详情与原因共用一次资格读取", async () => {
    api.getLimitUpPoolSnapshot.mockResolvedValue(qualifiedPool());
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" liveQuote={currentQuote}/>); await settle();
    expect(api.getLimitUpPoolSnapshot).toHaveBeenCalledTimes(1);
    expect(api.getLimitUpPoolSnapshot).toHaveBeenCalledWith("2026-10-09", {includeBreaks: true});
    expect(api.getStockAttribution).not.toHaveBeenCalled();
    fireEvent.click(view.getByRole("tab", {name: "涨停原因"})); await settle();
    const panel = within(view.getByRole("tabpanel"));
    expect(panel.getByText("拟收购+需审批")).toBeTruthy();
    expect(panel.queryByRole("button", {name: "查看全文"})).toBeNull();
    expect(view.queryByRole("dialog")).toBeNull();
    expect(api.getStockAttribution).toHaveBeenCalledTimes(1);
    expect(api.getLimitUpPoolSnapshot).toHaveBeenCalledTimes(1);
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });

  it("仅high触及价格或资格源失败均不创造原因入口，普通股也不另留原因Tab", async () => {
    api.getLimitUpPoolSnapshot.mockRejectedValue(new Error("synthetic coverage failure"));
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="reason" liveQuote={{...currentQuote, high: 22, limit_up_price: 22}}/>); await settle();
    expect(view.queryByRole("tab", {name: "涨停原因"})).toBeNull();
    expect(view.getByRole("alert").textContent).toContain("资格待恢复");
    expect(view.getByRole("button", {name: "重试资格"})).toBeTruthy();
    expect(api.getStockAttribution).not.toHaveBeenCalled();
  });

  it.each(["stale", "low"])("首次%s池行带原因原句也不能绕过资格门", async quality => {
    const snapshot = qualifiedPool();
    api.getLimitUpPoolSnapshot.mockResolvedValue({...snapshot, pool: [{...snapshot.pool[0], quality}], coverage: {...snapshot.coverage, limit_up: {state: "unknown", error: null}},
      reason_observations: {"600519": [{symbol: "600519", trade_date: "2026-10-09", reason: "拟收购+需审批", source: "ths", content_hash: "fixture", observed_at: "2026-10-09T10:00:00+08:00", received_at: null}]}});
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" liveQuote={currentQuote}/>); await settle();
    expect(view.queryByRole("tab", {name: "涨停原因"})).toBeNull();
    expect(api.getStockAttribution).not.toHaveBeenCalled();
  });

  it("旧events深链落在资讯父项的事件子项，切父项不误触新闻摘要", async () => {
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="events"/>); await settle();
    const outer = within(view.getByRole("tablist", {name: "核对视角"}));
    expect(outer.getByRole("tab", {name: "资讯", selected: true})).toBeTruthy();
    expect(outer.queryByRole("tab", {name: "相关事件"})).toBeNull();
    expect(view.getByRole("tab", {name: "相关事件", selected: true})).toBeTruthy();
    expect(api.getEventsForSymbol).toHaveBeenCalledTimes(1);
    expect(api.getNewsDigest).not.toHaveBeenCalled();
    fireEvent.click(outer.getByRole("tab", {name: "资讯"})); await settle();
    expect(api.getNewsDigest).not.toHaveBeenCalled();
    fireEvent.click(view.getByRole("tab", {name: "新闻公告"})); await settle();
    expect(api.getNewsDigest).toHaveBeenCalledTimes(1);
  });

  it("旧themes深链复用资料概念区域，只出现一个同名入口且不请求新闻", async () => {
    api.getStockThemes.mockResolvedValue({symbol: "600519", official: [{theme_code: "fixture", theme_name: "同名概念", source: "ths_official", theme_chg_1d: 2}], attribution: [{theme_name: "同名概念", date: "2026-10-09"}]});
    api.getCompanyProfile.mockResolvedValue({source: "synthetic_fixture", board_groups: {industry: [], region: [], concept: ["同名概念"], style_index: []}});
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="themes"/>); await advance(900);
    const panel = within(view.getByRole("tabpanel"));
    expect(panel.getAllByRole("link", {name: "同名概念"})).toHaveLength(1);
    expect(panel.getByText("官方 / 归因 / 资料")).toBeTruthy();
    expect(panel.getByText("+2.00%")).toBeTruthy();
    expect(view.getByRole("tab", {name: "资料", selected: true})).toBeTruthy();
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });

  it("题材读取错误可恢复，错股资料不冒充确认空集或当前归属", async () => {
    api.getStockThemes.mockResolvedValueOnce({symbol: "000001", official: [], attribution: []});
    const view = render(<StockDetailPanel symbol="600519" />); await settle();
    fireEvent.click(view.getByRole("tab", {name: "资料"})); await settle();
    const panel = within(view.getByRole("tabpanel"));
    expect(panel.getByRole("alert").textContent).toContain("题材读取失败");
    expect(panel.queryByText("本次资料未返回题材归属。")).toBeNull();
    api.getStockThemes.mockResolvedValueOnce({symbol: "600519", official: [{theme_code: "fixture", theme_name: "恢复题材", source: "ths_official"}], attribution: []});
    fireEvent.click(panel.getByRole("button", {name: "重新读取题材"})); await settle();
    expect(panel.getByRole("link", {name: "恢复题材"})).toBeTruthy();
    expect(panel.queryByRole("alert")).toBeNull();
    expect(api.getStockThemes).toHaveBeenCalledTimes(2);
  });
  it("驱动线索进入既有抽屉，图表上方不展开长解释，也不伪造首次加入记录", async () => {
    const view = render(<DetailModalProvider><StockDetailPanel symbol="600519" /></DetailModalProvider>);
    await settle();
    const chart = view.getByTestId("kline-price");
    expect(view.container.querySelector(".detail-context-disclosure")).toBeNull();
    expect(api.getStockAttribution).not.toHaveBeenCalled();
    const opener = view.getByRole("button", {name: "驱动线索"}); opener.focus();
    fireEvent.click(opener);
    await settle();
    const drawer = view.getByRole("dialog", {name: "驱动线索"});
    expect(drawer.className).toContain("motion-drawer");
    expect(drawer.contains(chart)).toBe(false);
    expect(view.getByTestId("kline-price")).toBe(chart);
    expect(within(drawer).queryByText("原首次加入记录")).toBeNull();
    expect(api.getStockAttribution).toHaveBeenCalledTimes(1);
    fireEvent.click(within(drawer).getByText(/不改写首次入选记录/));
    expect(view.getByRole("dialog", {name: "驱动线索"})).toBe(drawer);
    fireEvent.click(within(drawer).getByRole("button", {name: "关闭"}));
    expect(view.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("题材、事件与原因只在右栏可读，资料概念复用已有题材链接，做T已移除", async () => {
    api.getStockThemes.mockResolvedValue({symbol: "600519", official: [{theme_code: "fixture", theme_name: "合成题材", source: "ths_official"}], attribution: []});
    api.getCompanyProfile.mockResolvedValue({source: "synthetic_fixture", board_groups: {industry: ["合成行业"], region: [], concept: ["资料概念"], style_index: []}});
    const view = render(<StockDetailPanel symbol="600519" />);
    await advance(900);
    expect(view.queryByRole("tab", {name: "做T"})).toBeNull();
    expect(view.queryByText("合成题材")).toBeNull();
    const tabs = view.getByRole("tablist", {name: "核对视角"});
    expect(tabs.getAttribute("style")).toContain("repeat(3");
    fireEvent.click(view.getByRole("tab", {name: "资料"})); await settle();
    const panel = within(view.getByRole("tabpanel"));
    expect(panel.getByRole("link", {name: "合成题材"})).toBeTruthy();
    expect(panel.getByRole("link", {name: "资料概念"})).toBeTruthy();
    fireEvent.click(view.getByRole("tab", {name: "资料"}));
    expect(within(view.getByRole("tabpanel")).getByRole("link", {name: "资料概念"}).getAttribute("href")).toContain("tab=themes");
    expect(view.queryByRole("tab", {name: "题材归属"})).toBeNull();
    expect(view.queryByRole("tab", {name: "涨停原因"})).toBeNull();
    fireEvent.click(view.getByRole("tab", {name: "资讯"}));
    expect(view.getByRole("tab", {name: "相关事件"})).toBeTruthy();
    expect(api.getStockThemes).toHaveBeenCalledTimes(1);
  });
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
    expect(onRightTabChange).toHaveBeenLastCalledWith("real");
    expect(document.activeElement).toBe(controls.getByRole("tab", { name: "手工记账" }));
    expect(controls.getAllByRole("tab")).toHaveLength(5);
  });

  it("指数只提供适用视角，不能从入口进入股票交易", () => {
    const view = render(<StockDetailPanel symbol="sh000001" chartTab="flow" />);
    const controls = within(view.getByRole("tablist", { name: "核对视角" }));
    expect(controls.getAllByRole("tab").map(button => button.textContent)).toEqual(["成分股", "板块"]);
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

  it("默认K线/盘口立即读取，分时与逐笔仅在页签可见时首读及轮询", async () => {
    const view = render(<StockDetailPanel symbol="600519" />);
    await settle();
    expect(api.getKlinePayload).toHaveBeenCalledTimes(1);
    expect(api.getOrderBook).toHaveBeenCalledTimes(1);
    expect(view.getByTestId("kline-price").textContent).toBe("20");
    expect(within(view.getByRole("tabpanel")).getByText("买1")).toBeTruthy();
    expect(api.getMinuteLineWithBaseline).not.toHaveBeenCalled();
    expect(api.getTrades).not.toHaveBeenCalled();
    await advance(900);
    expect(api.getMinuteLineWithBaseline).not.toHaveBeenCalled();
    expect(api.getTrades).not.toHaveBeenCalled();
    await advance(4100);
    expect(api.getOrderBook).toHaveBeenCalledTimes(2);
    expect(api.getKlinePayload).toHaveBeenCalledTimes(1);
    expect(api.getTrades).not.toHaveBeenCalled();
    fireEvent.click(view.getByRole("tab", { name: "逐笔" }));
    fireEvent.click(view.getByRole("button", { name: "分时" }));
    await settle();
    expect(api.getTrades).toHaveBeenCalledTimes(1);
    expect(api.getMinuteLineWithBaseline).toHaveBeenCalledTimes(1);
    await advance(10_000);
    expect(api.getTrades).toHaveBeenCalledTimes(2);
    expect(api.getOrderBook).toHaveBeenCalledTimes(2);
  });

  it("初始分时/逐笔由激活轮询立即读取，idle 不重发；离开页签暂停轮询", async () => {
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" rightTab="trades" />);
    await settle();
    expect(api.getMinuteLineWithBaseline).toHaveBeenCalledTimes(1);
    expect(api.getTrades).toHaveBeenCalledTimes(1);
    expect(api.getKlinePayload).toHaveBeenCalledTimes(1);
    expect(api.getOrderBook).not.toHaveBeenCalled();
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
  ])("$endpoint 首次读取在途时，切入复用首取，完成后恢复轮询", async ({ endpoint, chartTab, rightTab, result, interval }) => {
    const pending = deferred<typeof result>();
    api[endpoint].mockReturnValueOnce(pending.promise);
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" rightTab="profile" />);
    await advance(900);
    expect(api[endpoint]).toHaveBeenCalledTimes(endpoint === "getKlinePayload" ? 1 : 0);
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

  it("逐笔可见首读在途时切走再切入只复用一次，晚回不替换盘口，后续进入即时校准", async () => {
    const pending = deferred<Trade[]>();
    api.getTrades.mockReturnValueOnce(pending.promise).mockResolvedValue(trades("600519", 20));
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" rightTab="book" />);
    await advance(900);
    expect(api.getTrades).not.toHaveBeenCalled();
    fireEvent.click(view.getByRole("tab", {name: "逐笔"}));
    await settle();
    expect(api.getTrades).toHaveBeenCalledTimes(1);
    fireEvent.click(view.getByRole("tab", {name: "盘口"}));
    await advance(20_000);
    expect(api.getTrades).toHaveBeenCalledTimes(1);
    fireEvent.click(view.getByRole("tab", {name: "逐笔"}));
    await settle();
    expect(api.getTrades).toHaveBeenCalledTimes(1);
    fireEvent.click(view.getByRole("tab", {name: "盘口"}));
    await act(async () => {pending.resolve(trades("600519", 77));});
    expect(within(view.getByRole("tabpanel")).getByText("买1")).toBeTruthy();
    expect(within(view.getByRole("tabpanel")).queryByText("77.00")).toBeNull();
    fireEvent.click(view.getByRole("tab", {name: "逐笔"}));
    await settle();
    expect(api.getTrades).toHaveBeenCalledTimes(2);
    expect(within(view.getByRole("tabpanel")).getByText("20.00")).toBeTruthy();
    await advance(10_000);
    expect(api.getTrades).toHaveBeenCalledTimes(3);
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
    expect(panel.getByRole("alert").textContent).toContain("保留结果");
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
    expect(api.getMinuteLineWithBaseline).not.toHaveBeenCalled();
  });
});

 describe("StockDetailPanel · 隐藏详情与共享持仓", () => {
  const coreReads = () => [api.getQuote, api.getKlinePayload, api.getOrderBook, api.getMinuteLineWithBaseline, api.getTrades, api.getStockThemes, api.getWatchlist, api.getFinancials, api.getCompanyProfile, api.getNewsDigest, api.getAuction, api.getCapitalFlow, api.getMarketOverview];

  it("初次隐藏不建立自身WS、不发主读取；恢复只读取当前可见数据", async () => {
    const view = render(<StockDetailPanel symbol="600519" active={false} />);
    await advance(120_000);
    for (const read of coreReads()) expect(read).not.toHaveBeenCalled();
    for (const symbols of calls.symbols) expect(symbols).toEqual([]);
    view.rerender(<StockDetailPanel symbol="600519" active />);
    await settle();
    expect(api.getQuote).toHaveBeenCalledTimes(1);
    expect(api.getKlinePayload).toHaveBeenCalledTimes(1);
    expect(api.getOrderBook).toHaveBeenCalledTimes(1);
    expect(api.getStockThemes).not.toHaveBeenCalled();
    await advance(900);
    expect(api.getMinuteLineWithBaseline).not.toHaveBeenCalled();
    expect(api.getTrades).not.toHaveBeenCalled();
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
    view.rerender(<StockDetailPanel symbol="600519" active={false} liveQuote={{...audit, symbol: "600519", price: 333, quality: "stale", quality_reasons: ["quote_age_exceeded"]}} streamStatus="live" />);
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

  it.each(["members", "boards"] as const)("隐藏时停止指数%s子轮询，恢复立即重新读取并拒绝旧回包", async rightTab => {
    api.getThemesCatalog.mockResolvedValue([{code: "881156.TI", name: "粮食概念"}]);
    const endpoint = rightTab === "members" ? api.getIndexMembers : api.getBoards;
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
    await act(async () => { finish(rightTab === "members" ? {symbol: "sh000001", name: "旧回包标志", source: "fixture", snapshot_at: "2026-10-15T07:29:07+00:00", items: []} : [{code: "old", name: "旧板块回包"}]); });
    expect(view.queryByText("旧回包标志")).toBeNull();
    expect(view.queryByText("旧板块回包")).toBeNull();
  });
});

 describe("StockDetailPanel · 恢复失败保留既有结果", () => {
  it.each([
    {endpoint: "getOrderBook" as const, tab: "盘口", result: "66.00"},
    {endpoint: "getFinancials" as const, tab: "资料", result: "报告期 2026-06-30"},
  ])("恢复时$endpoint可见读取失败保留同股上次结果", async ({endpoint, tab, result}) => {
    api.getOrderBook.mockResolvedValue(book("600519", 66));
    api.getTrades.mockResolvedValue(trades("600519", 77));
    api.getFinancials.mockResolvedValue([{report_date: "2026-06-30", source: "eastmoney", revenue: 123_000_000}]);
    const rightTab = endpoint === "getOrderBook" ? "book" : "profile";
    const view = render(<StockDetailPanel symbol="600519" chartTab="flow" rightTab={rightTab} />);
    await advance(900);
    expect(api[endpoint]).toHaveBeenCalledTimes(1);
    api[endpoint].mockRejectedValue(new Error("恢复读取失败"));
    view.rerender(<StockDetailPanel symbol="600519" chartTab="flow" rightTab={rightTab} active={false} />);
    view.rerender(<StockDetailPanel symbol="600519" chartTab="flow" rightTab={rightTab} active />);
    await advance(900);
    expect(api[endpoint]).toHaveBeenCalledTimes(2);
    fireEvent.click(view.getByRole("tab", {name: tab}));
    await settle();
    expect(within(view.getByRole("tabpanel")).getByText(result)).toBeTruthy();
  });

  it("逐笔成功首读后隐藏暂停，恢复失败保留同股结果并按原间隔重试", async () => {
    api.getTrades.mockResolvedValue(trades("600519", 77));
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" rightTab="trades" />);
    await settle();
    expect(api.getTrades).toHaveBeenCalledTimes(1);
    expect(within(view.getByRole("tabpanel")).getByText("77.00")).toBeTruthy();
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" rightTab="trades" active={false} />);
    await advance(60_000);
    expect(api.getTrades).toHaveBeenCalledTimes(1);
    api.getTrades.mockRejectedValue(new Error("恢复读取失败"));
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" rightTab="trades" active />);
    await settle();
    expect(api.getTrades).toHaveBeenCalledTimes(2);
    const panel = within(view.getByRole("tabpanel"));
    expect(panel.getByText("77.00")).toBeTruthy();
    expect(panel.getByRole("alert").textContent).toContain("成交读取失败");
    await advance(10_000);
    expect(api.getTrades).toHaveBeenCalledTimes(3);
    expect(panel.getByText("77.00")).toBeTruthy();
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
    expect(view.getByRole("alert").textContent).toContain("指数叠加读取失败");
  });

  it("指数叠加可展示已确认休市同日历史，保留非实时标签和实际 stale 质量，后续失败可重试", async () => {
    const fetcher = vi.fn(() => {throw new Error("offline test: unexpected fetch");});
    vi.stubGlobal("fetch", fetcher);
    vi.setSystemTime(new Date("2026-10-14T08:08:35+08:00"));
    const points = [{ts: "2026-10-13T15:00:00+08:00", price: 3030, source: "tencent"}];
    const quote = {...audit, symbol: "000001", market: "SH", source: "tencent", quality: "stale" as const, quality_reasons: ["market_closed"],
      price: 3030, prev_close: 3000, data_timestamp: "2026-10-13T16:15:00+08:00",
      display_session: {phase: "closed" as const, trade_date: "2026-10-14", as_of: "2026-10-14T08:08:35+08:00", trade_day_result: true}};
    const before = JSON.stringify(quote);
    api.getMinuteLineWithBaseline.mockResolvedValue({points, vr_baseline_5m: null});
    api.getMinuteLine.mockResolvedValue(points);
    api.getMarketOverview.mockResolvedValue({indices: [quote]});
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" />);
    await settle();
    expect(view.getByTestId("minute-price").getAttribute("data-index-last")).toBe("3030");
    expect(view.getByText("上证叠加 · 2026-10-13 · 非实时")).toBeTruthy();
    expect(view.queryByText(/指数叠加读取失败/)).toBeNull();
    expect(JSON.stringify(quote)).toBe(before);
    api.getMinuteLine.mockRejectedValueOnce(new Error("synthetic overlay network failure"));
    await advance(60_000);
    expect(view.getByRole("alert").textContent).toContain("指数叠加读取失败 · 保留结果");
    expect(view.getByTestId("minute-price").getAttribute("data-index-last")).toBe("3030");
    expect(view.getByText("上证叠加 · 2026-10-13 · 非实时")).toBeTruthy();
    fireEvent.click(view.getByRole("button", {name: "重新读取指数叠加"}));
    await settle();
    expect(view.queryByText(/指数叠加读取失败/)).toBeNull();
    expect(api.getNewsDigest).not.toHaveBeenCalled();
    expect(api.getPaperAccount).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each(["refresh_failed", "quote_age_exceeded"])("指数叠加不能把真实%s混入休市历史", async reason => {
    vi.setSystemTime(new Date("2026-10-14T08:08:35+08:00"));
    const points = [{ts: "2026-10-13T15:00:00+08:00", price: 3030, source: "tencent"}];
    api.getMinuteLineWithBaseline.mockResolvedValue({points, vr_baseline_5m: null});
    api.getMinuteLine.mockResolvedValue(points);
    api.getMarketOverview.mockResolvedValue({indices: [{...audit, symbol: "sh000001", source: "tencent", quality: "stale", quality_reasons: ["market_closed", reason],
      price: 3030, prev_close: 3000, data_timestamp: "2026-10-13T16:15:00+08:00",
      display_session: {phase: "closed", trade_date: "2026-10-14", as_of: "2026-10-14T08:08:35+08:00", trade_day_result: true}}]});
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" />);
    await settle();
    expect(view.getByRole("alert").textContent).toContain("指数叠加读取失败");
    expect(view.getByTestId("minute-price").getAttribute("data-index-last")).toBe("none");
    expect(view.queryByText(/上证叠加 · .*非实时/)).toBeNull();
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });
});

describe("StockDetailPanel · 已有指数观察驱动一次恢复校准", () => {
  function indexQuote(clock = "2026-10-09T10:00:02+08:00"): Quote {
    return {...audit, symbol: "000001", market: "SH", price: 3000, prev_close: 2990,
      data_timestamp: "2026-10-09T10:00:00+08:00",
      display_session: {phase: "continuous", trade_date: "2026-10-09", as_of: clock, trade_day_result: true}};
  }
  const missing = (): Quote => ({...indexQuote("2026-10-09T10:00:01+08:00"), quality: "stale", quality_reasons: ["index_batch_missing"]});
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => {throw new Error("offline recovery test: unexpected fetch");}));
    api.getMinuteLine.mockResolvedValue(minute(3000).points);
  });

  it("新成功父观察只校准一次；同引用和后续同状态新对象不循环", async () => {
    api.getMarketOverview.mockResolvedValueOnce({indices: [missing()]}).mockResolvedValue({indices: [{...indexQuote(), prev_close: 3020}]});
    api.getMinuteLine.mockResolvedValueOnce(minute(3000).points).mockResolvedValue([{ts: "2026-10-09T02:00:01Z", price: 3030, source: "tencent"}]);
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={null}/>);
    await settle();
    expect(view.getByRole("alert").textContent).toContain("指数叠加读取失败");
    const recovered = indexQuote();
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={recovered}/>);
    await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(2);
    expect(api.getMarketOverview).toHaveBeenCalledTimes(2);
    expect(view.queryByText(/指数叠加读取失败/)).toBeNull();
    expect(view.getByTestId("minute-price").getAttribute("data-index-last")).toBe("3030");
    expect(view.getByTestId("minute-price").getAttribute("data-index-clock")).toBe("2026-10-09T02:00:01Z");
    expect(view.getByTestId("minute-price").getAttribute("data-index-prev-close")).toBe("3020");
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={recovered}/>);
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={indexQuote("2026-10-09T10:00:03+08:00")}/>);
    await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(2);
    expect(api.getNewsDigest).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("收盘后缺批恢复为严格closed历史观察才校准，仍显示非实时且不升级stale", async () => {
    vi.setSystemTime(new Date("2026-10-09T17:15:00+08:00"));
    const recovered: Quote = {...indexQuote("2026-10-09T17:14:54+08:00"), quality: "stale", quality_reasons: ["market_closed"],
      display_session: {phase: "closed", trade_date: "2026-10-09", as_of: "2026-10-09T17:14:54+08:00", trade_day_result: true}};
    const before = JSON.stringify(recovered);
    api.getMarketOverview.mockResolvedValueOnce({indices: [{...recovered, quality_reasons: ["market_closed", "index_batch_missing"], display_session: {...recovered.display_session!, as_of: "2026-10-09T17:14:45+08:00"}}]}).mockResolvedValue({indices: [recovered]});
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={null}/>); await settle();
    expect(view.getByRole("alert")).toBeTruthy();
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={recovered}/>); await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(2);
    expect(view.queryByText(/指数叠加读取失败/)).toBeNull();
    expect(view.getByText("上证叠加 · 2026-10-09 · 非实时")).toBeTruthy();
    expect(JSON.stringify(recovered)).toBe(before);
  });

  it.each(["failure", "success"])("慢请求跨恢复，原请求%s后只在真正结束时决定是否补读", async outcome => {
    const old = deferred<MinutePoint[]>();
    api.getMinuteLine.mockReturnValueOnce(old.promise);
    api.getMarketOverview.mockResolvedValue({indices: [indexQuote()]});
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={null}/>);
    await settle();
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={indexQuote("2026-10-09T10:00:03+08:00")}/>);
    await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(1);
    await act(async () => {if (outcome === "failure") old.reject(new Error("old request failed")); else old.resolve(minute(3000).points);});
    await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(outcome === "failure" ? 2 : 1);
    expect(view.queryByText(/指数叠加读取失败/)).toBeNull();
  });

  it("后台flight的pending未置位也不并发补读，补读失败后同状态新对象不循环", async () => {
    api.getMarketOverview.mockResolvedValue({indices: [indexQuote()]});
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={null}/>);
    await settle();
    const old = deferred<MinutePoint[]>(); api.getMinuteLine.mockReturnValueOnce(old.promise);
    await advance(60_000);
    const recovered = indexQuote("2026-10-09T10:00:03+08:00");
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={recovered}/>); await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(2);
    api.getMinuteLine.mockRejectedValue(new Error("recovery still failed"));
    await act(async () => {old.reject(new Error("background failure"));}); await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(3);
    expect(view.getByRole("alert").textContent).toContain("指数叠加读取失败 · 保留结果");
    for (let second = 3; second < 6; second++) {
      view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={indexQuote(`2026-10-09T10:00:0${second}+08:00`)}/>); await settle();
    }
    expect(api.getMinuteLine).toHaveBeenCalledTimes(3);
  });

  it("手动换代不会冒充旧flight已排空，旧成功不能清新失败或并发第三次恢复读", async () => {
    api.getMarketOverview.mockResolvedValue({indices: [indexQuote()]});
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={null}/>); await settle();
    api.getMinuteLine.mockRejectedValueOnce(new Error("earlier failure"));
    await advance(60_000);
    const old = deferred<MinutePoint[]>(), manual = deferred<MinutePoint[]>(), recovery = deferred<MinutePoint[]>();
    api.getMinuteLine.mockReturnValueOnce(old.promise).mockReturnValueOnce(manual.promise).mockReturnValueOnce(recovery.promise);
    await advance(60_000);
    fireEvent.click(view.getByRole("button", {name: "重新读取指数叠加"})); await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(4);
    await act(async () => {manual.reject(new Error("new manual failure"));}); await settle();
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={indexQuote("2026-10-09T10:00:03+08:00")}/>); await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(4); // old is still physically unresolved.
    await act(async () => {old.resolve(minute(2999).points);}); await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(5);
    expect(view.getByRole("alert").textContent).toContain("指数叠加读取失败");
    expect(view.getByTestId("minute-price").getAttribute("data-index-last")).toBe("3000");
    await act(async () => {recovery.resolve(minute(3030).points);}); await settle();
    expect(view.queryByText(/指数叠加读取失败/)).toBeNull();
    expect(view.getByTestId("minute-price").getAttribute("data-index-last")).toBe("3030");
  });

  it("一个输入先失败也须等另一个真实读取结束，仍按原时机显示失败", async () => {
    const overview = deferred<{indices: Quote[]}>();
    api.getMinuteLine.mockRejectedValueOnce(new Error("minute failed early"));
    api.getMarketOverview.mockReturnValueOnce(overview.promise).mockResolvedValue({indices: [indexQuote()]});
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={null}/>); await settle();
    expect(view.getByRole("alert").textContent).toContain("指数叠加读取失败");
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={indexQuote("2026-10-09T10:00:03+08:00")}/>); await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(1);
    await act(async () => {overview.resolve({indices: [indexQuote()]});}); await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(2);
    expect(view.queryByText(/指数叠加读取失败/)).toBeNull();
  });

  it.each(["index_batch_missing", "source_time_regress_ignored", "quote_age_exceeded"])("父观察的%s不能触发恢复，冻结旧as_of也不触发", async reason => {
    api.getMarketOverview.mockResolvedValue({indices: [missing()]});
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={null}/>); await settle();
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={{...indexQuote(), quality: "stale", quality_reasons: [reason]}}/>); await settle();
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={indexQuote("2026-10-09T10:00:00+08:00")}/>); await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(1);
    expect(view.getByRole("alert")).toBeTruthy();
  });

  it.each(["inactive", "other_tab", "document_hidden", "standalone"])("%s不因父恢复观察额外读取", async mode => {
    api.getMarketOverview.mockResolvedValue({indices: [missing()]});
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={mode === "standalone" ? undefined : null}/>); await settle();
    const before = api.getMinuteLine.mock.calls.length;
    if (mode === "document_hidden") vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    view.rerender(<StockDetailPanel symbol="600519" chartTab={mode === "other_tab" ? "kline" : "minute"} active={mode !== "inactive"} indexQuote={mode === "standalone" ? undefined : indexQuote()}/>); await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(before);
  });

  it("切股/跨日让旧慢包和其恢复候选失效；新owner只正常首读", async () => {
    const old = deferred<MinutePoint[]>(); api.getMinuteLine.mockReturnValueOnce(old.promise);
    api.getMarketOverview.mockResolvedValue({indices: [indexQuote()]});
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={null}/>); await settle();
    view.rerender(<StockDetailPanel symbol="600519" chartTab="minute" indexQuote={indexQuote()}/>); await settle();
    view.rerender(<StockDetailPanel symbol="600127" chartTab="minute" indexQuote={indexQuote()}/>); await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(2);
    await act(async () => {old.reject(new Error("old symbol failed late"));}); await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(2);
    expect(view.queryByText(/指数叠加读取失败/)).toBeNull();
    api.getMinuteLineWithBaseline.mockResolvedValue({points: [{ts: "2026-10-12T10:00:00+08:00", price: 22, source: "tdx"}], vr_baseline_5m: null});
    await advance(60_000);
    const reads = api.getMinuteLine.mock.calls.length;
    view.rerender(<StockDetailPanel symbol="600127" chartTab="minute" indexQuote={indexQuote("2026-10-12T10:00:02+08:00")}/>); await settle();
    expect(api.getMinuteLine).toHaveBeenCalledTimes(reads);
    expect(view.getByRole("alert")).toBeTruthy();
  });
});


describe("StockDetailPanel · 首次报价与真实恢复", () => {
  const firstQuote = (symbol = "600519"): Quote => ({...audit, symbol, source: "tencent", name: "首报证券", price: 22, data_timestamp: "2026-10-09T02:00:30Z"});

  it("空首帧后REST成功不假称已有报价待恢复，收到真实live后缺帧仍提醒", async () => {
    const feed = testQuoteFeed(); feed.publish("600519", null);
    api.getQuote.mockResolvedValue(firstQuote());
    const view = render(<StockDetailPanel symbol="600519" quoteFeed={feed} streamStatus="live"/>);
    await settle();
    expect(view.container.querySelector(".quote-sheet")?.textContent).toContain("22.00");
    expect(view.queryByRole("button", {name: "查看行情问题"})).toBeNull();
    expect(api.getQuote).toHaveBeenCalledTimes(1);
    await act(async () => {feed.publish("600519", {...firstQuote(), price: 23});});
    await act(async () => {feed.publish("600519", null);});
    expect(view.getByRole("button", {name: "查看行情问题"}).textContent).toBe("报价待恢复");
    expect(view.container.querySelector(".quote-sheet")?.textContent).toContain("23.00");
    expect(api.getQuote).toHaveBeenCalledTimes(1);
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });

  it("首报REST仍在途时保留加载态，失败后保留真实不可用", async () => {
    const request = deferred<Quote>(); api.getQuote.mockReturnValueOnce(request.promise);
    const feed = testQuoteFeed(); feed.publish("600519", null);
    const view = render(<StockDetailPanel symbol="600519" quoteFeed={feed} streamStatus="live"/>);
    await settle();
    expect(view.queryByText(/行情数据暂不可用/)).toBeNull();
    expect(view.queryByText("报价待恢复")).toBeNull();
    await act(async () => {request.reject(new Error("isolated initial quote failure"));});
    expect(view.getByText(/行情数据暂不可用/)).toBeTruthy();
    expect(api.getQuote).toHaveBeenCalledTimes(1);
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });

  it("同一证券第一次空快照和第二次暖快照不会虚构不同故障状态", async () => {
    const current = firstQuote(); api.getQuote.mockResolvedValue(current);
    const cold = testQuoteFeed(); cold.publish("600519", null);
    const first = render(<StockDetailPanel symbol="600519" quoteFeed={cold} streamStatus="live"/>);
    await settle();
    expect(first.queryByRole("button", {name: "查看行情问题"})).toBeNull();
    first.unmount();
    const warm = testQuoteFeed(); warm.publish("600519", current);
    const second = render(<StockDetailPanel symbol="600519" quoteFeed={warm} streamStatus="live"/>);
    await settle();
    expect(second.queryByRole("button", {name: "查看行情问题"})).toBeNull();
    expect(second.container.querySelector(".quote-sheet")?.textContent).toContain("22.00");
    expect(api.getQuote).toHaveBeenCalledTimes(2);
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });

  it("另一证券首次空帧不借用上一证券的恢复状态", async () => {
    const feed = testQuoteFeed(); feed.publish("600519", firstQuote());
    api.getQuote.mockImplementation(async (symbol: string) => firstQuote(symbol));
    const view = render(<StockDetailPanel symbol="600519" quoteFeed={feed} streamStatus="live"/>);
    await settle();
    feed.publish("000001", null);
    view.rerender(<StockDetailPanel symbol="000001" quoteFeed={feed} streamStatus="live"/>);
    await settle();
    expect(view.queryByRole("button", {name: "查看行情问题"})).toBeNull();
    expect(view.container.querySelector(".quote-sheet")?.textContent).toContain("000001");
    expect(api.getQuote).toHaveBeenCalledTimes(2);
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });

  it.each(["missing", "unobserved"])("切股后%s首帧与REST在途/失败均不保留旧证券报价和限价", async frame => {
    const request = deferred<Quote>();
    const first = {...firstQuote(), prev_close: 20, limit_up_price: 22, limit_down_price: 18};
    api.getQuote.mockImplementation((symbol: string) => symbol === "600519" ? Promise.resolve(first) : request.promise);
    const feed = testQuoteFeed(); feed.publish("600519", first);
    const view = render(<StockDetailPanel symbol="600519" chartTab="minute" quoteFeed={feed} streamStatus="live"/>);
    await settle();
    expect(view.container.querySelector(".quote-sheet")?.textContent).toContain("600519");
    if (frame === "missing") feed.publish("000001", null);
    view.rerender(<StockDetailPanel symbol="000001" chartTab="minute" quoteFeed={feed} streamStatus="live"/>);
    await settle();
    expect(view.container.querySelector(".quote-sheet")).toBeNull();
    expect(view.queryByText(/行情数据暂不可用/)).toBeNull();
    expect(view.queryByText("报价待恢复")).toBeNull();
    const chart = view.getByTestId("minute-price");
    for (const name of ["prev-close", "reference-date", "upper-price", "lower-price"]) expect(chart.getAttribute(`data-${name}`)).toBe("none");
    await act(async () => {request.reject(new Error("isolated next symbol quote failure"));});
    expect(view.getByText(/行情数据暂不可用/)).toBeTruthy();
    expect(api.getQuote).toHaveBeenCalledTimes(2);
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });

  it("A→B→A复用同一缓存报价对象仍重新接纳A，不继承B的在途或失败状态", async () => {
    const pendingB = deferred<Quote>();
    const currentA = firstQuote();
    api.getQuote.mockImplementation((symbol: string) => symbol === "600519" ? Promise.resolve(currentA) : pendingB.promise);
    const feed = testQuoteFeed(); feed.publish("600519", currentA); feed.publish("000001", null);
    const view = render(<StockDetailPanel symbol="600519" quoteFeed={feed} streamStatus="live"/>);
    await settle();
    view.rerender(<StockDetailPanel symbol="000001" quoteFeed={feed} streamStatus="live"/>);
    await settle();
    view.rerender(<StockDetailPanel symbol="600519" quoteFeed={feed} streamStatus="live"/>);
    await settle();
    expect(view.container.querySelector(".quote-sheet")?.textContent).toContain("600519");
    expect(view.container.querySelector(".quote-sheet")?.textContent).toContain("22.00");
    await act(async () => {pendingB.reject(new Error("old B failure"));});
    expect(view.queryByText(/行情数据暂不可用/)).toBeNull();
    expect(view.queryByText("报价待恢复")).toBeNull();
    expect(api.getQuote).toHaveBeenCalledTimes(3);
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });

  it.each(["stale", "invalid"] as const)("首次真实%s行情保留质量问题，不能当成正常首报", async quality => {
    const feed = testQuoteFeed();
    feed.publish("600519", {...firstQuote(), quality, quality_reasons: [quality === "stale" ? "refresh_failed" : "price_nonpositive"]});
    const view = render(<StockDetailPanel symbol="600519" quoteFeed={feed} streamStatus="live"/>);
    await settle();
    expect(view.container.querySelector(`[data-quality=${quality}]`)).toBeTruthy();
    expect(view.getByRole("button", {name: "查看行情问题"}).textContent).not.toBe("报价待恢复");
    expect(api.getNewsDigest).not.toHaveBeenCalled();
  });
});

describe("证券类型切换与首屏请求预算", () => {
  it("从指数切回个股恢复盘口，不能保留板块内容或涨速入口", async () => {
    const view = render(<StockDetailPanel symbol="sh000001" chartTab="kline" rightTab="boards" />);
    await advance(20);
    view.rerender(<StockDetailPanel symbol="600519" chartTab="kline" rightTab="boards" />);
    await advance(20);
    expect(view.getByRole("tab", {name: "盘口"}).getAttribute("aria-selected")).toBe("true");
    expect(view.queryByRole("tab", {name: "涨速"})).toBeNull();
    expect(api.getOrderBook).toHaveBeenCalledWith("600519");
  });

  it("盘口首屏和空闲后均不读取不可见的资金、财报、资料、分时、竞价或题材", async () => {
    const view = render(<StockDetailPanel symbol="600519" chartTab="kline" rightTab="book" />);
    await advance(3000);
    for (const endpoint of [api.getCapitalFlow, api.getFinancials, api.getCompanyProfile, api.getStockThemes, api.getMinuteLineWithBaseline, api.getAuction]) expect(endpoint).not.toHaveBeenCalled();
    expect(api.getKlinePayload).toHaveBeenCalledTimes(1);
    expect(api.getOrderBook).toHaveBeenCalledTimes(1);
    fireEvent.click(view.getByRole("tab", {name: "资料"}));
    await advance(20);
    expect(api.getFinancials).toHaveBeenCalledTimes(1);
    expect(api.getCompanyProfile).toHaveBeenCalledTimes(1);
    expect(api.getStockThemes).toHaveBeenCalledTimes(1);
  });
});

it("复用指数面板打开明确的股票资料深链时保留该意图，不能被归位覆盖", async () => {
  const view = render(<StockDetailPanel symbol="sh000001" chartTab="kline" rightTab="boards" />);
  await advance(20);
  view.rerender(<StockDetailPanel symbol="600519" chartTab="kline" rightTab="profile" />);
  await advance(20);
  expect(view.getByRole("tab", {name: "资料", selected: true})).toBeTruthy();
  expect(api.getFinancials).toHaveBeenCalledTimes(1);
});
