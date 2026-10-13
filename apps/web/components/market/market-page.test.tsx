import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import MarketPage from "@/app/market/page";
import * as api from "@/lib/api";
import type { Quote } from "@/types/market";

const state = vi.hoisted(() => ({ search: "", openSymbol: vi.fn() }));
vi.mock("next/navigation", () => ({useSearchParams: () => new URLSearchParams(state.search)}));
vi.mock("@/lib/market-hours", () => ({isTradingSession: () => true}));
vi.mock("@/components/ui/workspace-deck", () => ({ MarketLensPicker: () => null }));
vi.mock("@/components/detail/symbol-detail-context", () => ({ useSymbolDetail: () => ({open: state.openSymbol}) }));
vi.mock("@/components/event-panel", () => ({ EventPanel: () => null }));
vi.mock("@/components/market/heatmap-tab", () => ({ HeatmapTab: () => null }));
vi.mock("@/components/market/events-tab", () => ({ EventsTab: () => null }));
vi.mock("@/components/market/fund-tab", () => ({ FundTab: () => null }));
vi.mock("@/lib/api", () => ({
  getMarketOverview: vi.fn(), getLimitUpPool: vi.fn(), getBreadth: vi.fn(),
  getSentiment: vi.fn(), getSentimentHistory: vi.fn(),
  indexDetailSymbol: (symbol: string, market: string) => `${market.toLowerCase()}${symbol}`,
}));

const sentiment: api.Sentiment = {
  phase: "修复", temperature: 55, confidence: "中", reasons: ["涨跌宽度修复"],
  misjudge_caveats: ["量能不足"], switch_conditions: "重新转弱时失效", indicators: [],
  ladder: {}, judged_at: "2026-10-09T10:30:00+08:00",
};

beforeEach(() => {
  state.search = "";
  vi.useFakeTimers();
  vi.mocked(api.getMarketOverview).mockResolvedValue({indices: [], total_amount: null, total_amount_freshness: null});
  vi.mocked(api.getLimitUpPool).mockResolvedValue([]);
  vi.mocked(api.getBreadth).mockResolvedValue({up: 3000, down: 1000} as api.Breadth);
  vi.mocked(api.getSentiment).mockResolvedValue(sentiment);
  vi.mocked(api.getSentimentHistory).mockResolvedValue({items: []} as unknown as api.SentimentHistoryPayload);
  vi.stubGlobal("matchMedia", () => ({matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn()}));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); vi.unstubAllGlobals(); });

it("closes failed sentiment evidence and requires a fresh user click after recovery", async () => {
  render(<MarketPage />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", {name: "依据与失效条件"}));
  expect(within(screen.getByRole("dialog", {name: "市场情绪依据与失效条件"})).getByText(/涨跌宽度修复/)).toBeTruthy();

  vi.mocked(api.getSentiment).mockRejectedValueOnce(new Error("offline"));
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByText(/宽度或情绪来源未就绪/)).toBeTruthy();

  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", {name: "依据与失效条件"}).getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(screen.getByRole("button", {name: "依据与失效条件"}));
  expect(within(screen.getByRole("dialog", {name: "市场情绪依据与失效条件"})).getByText(/涨跌宽度修复/)).toBeTruthy();
  expect(api.getSentiment).toHaveBeenCalledTimes(3);
});

const overviewReaders = () => [api.getMarketOverview, api.getLimitUpPool, api.getBreadth, api.getSentiment, api.getSentimentHistory];

it("shows every index immediately and preserves market identity when opening details", async () => {
  const indices = [
    {symbol: "399001", market: "SZ", name: "深证成指"},
    {symbol: "399006", market: "SZ", name: "创业板指"},
    {symbol: "000001", market: "SH", name: "上证指数"},
    {symbol: "000688", market: "SH", name: "科创50"},
    {symbol: "000300", market: "SH", name: "沪深300"},
    {symbol: "000852", market: "SH", name: "中证1000"},
    {symbol: "000001", market: "SZ", name: "同代码不同市场指数"},
  ].map(q => ({...q, price: 100, amount: 1e8, change_pct: 1} as Quote));
  vi.mocked(api.getMarketOverview).mockResolvedValue({indices, total_amount: null, total_amount_freshness: null});
  render(<MarketPage />);
  await act(async () => {});
  const region = within(screen.getByRole("region", {name: "主要指数"}));
  expect(region.getAllByRole("button")).toHaveLength(indices.length);
  for (const quote of indices) {
    const card = within(region.getByRole("button", {name: new RegExp(quote.name!)}));
    expect(card.getByText("100.00")).toBeTruthy();
    expect(card.getByText("+1.00%")).toBeTruthy();
    expect(card.getByText("1.00 亿")).toBeTruthy();
  }
  expect(region.queryByRole("button", {name: /展开|收起/})).toBeNull();
  fireEvent.click(region.getByRole("button", {name: /上证指数/}));
  expect(state.openSymbol).toHaveBeenLastCalledWith({symbol: "sh000001"});
  fireEvent.click(region.getByRole("button", {name: /同代码不同市场指数/}));
  expect(state.openSymbol).toHaveBeenLastCalledWith({symbol: "sz000001"});
  expect(api.getMarketOverview).toHaveBeenCalledTimes(1);
});

it("keeps missing configured indices visible as unavailable without invented quotes", async () => {
  vi.mocked(api.getMarketOverview).mockResolvedValue({
    indices: [{symbol: "000001", market: "SH", name: "上证指数", price: 3000, quality: "stale", quality_reasons: ["refresh_failed"]} as Quote],
    total_amount: null, total_amount_freshness: null,
  });
  render(<MarketPage />);
  await act(async () => {});
  const region = within(screen.getByRole("region", {name: "主要指数"}));
  expect(region.getAllByRole("button")).toHaveLength(6);
  expect(within(region.getByRole("button", {name: /上证指数/})).getByText("过期")).toBeTruthy();
  for (const name of ["科创50", "创业板指", "深证成指", "沪深300", "中证1000"]) {
    const card = region.getByRole("button", {name: new RegExp(name)});
    expect(card.hasAttribute("disabled")).toBe(true);
    expect(within(card).getByText("未返回")).toBeTruthy();
    expect(within(card).getByText("--")).toBeTruthy();
    fireEvent.click(card);
  }
  expect(state.openSymbol).not.toHaveBeenCalled();
});

it("shows attribution from the same pool row in the market leaders preview", async () => {
  vi.mocked(api.getLimitUpPool).mockResolvedValue([{symbol:"600825",name:"新华传媒",trade_date:"2026-10-09",reason:"拟收购财联社+重大资产重组",source:"ths",quality:"high",quality_reasons:[],received_at:"2026-10-09T08:00:00Z"}]);
  render(<MarketPage />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", {name: /涨停原因 · 查看原文/}));
  const dialog = screen.getByRole("dialog", {name: "涨停原因原文"});
  expect(within(dialog).getByText("拟收购财联社+重大资产重组")).toBeTruthy();
  expect(within(dialog).getByText(/2026-10-09 · 同花顺/)).toBeTruthy();
});

it.each(["fund", "heatmap", "events"])("%s lens never starts overview polling, switching back fetches every overview source immediately", async lens => {
  state.search = `tab=${lens}`;
  const view = render(<MarketPage />);
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  for (const read of overviewReaders()) expect(read).not.toHaveBeenCalled();
  state.search = "";
  view.rerender(<MarketPage />);
  await act(async () => {});
  for (const read of overviewReaders()) expect(read).toHaveBeenCalledTimes(1);
  state.search = `tab=${lens}`;
  view.rerender(<MarketPage />);
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  for (const read of overviewReaders()) expect(read).toHaveBeenCalledTimes(1);
});

it("a flight from the departed overview cannot overwrite the newly restored view", async () => {
  let oldOverview!: (value: Awaited<ReturnType<typeof api.getMarketOverview>>) => void;
  let oldSentiment!: (value: api.Sentiment) => void;
  vi.mocked(api.getMarketOverview).mockImplementationOnce(() => new Promise(resolve => {oldOverview = resolve;}));
  vi.mocked(api.getSentiment).mockImplementationOnce(() => new Promise(resolve => {oldSentiment = resolve;}));
  const view = render(<MarketPage />);
  await act(async () => {});
  state.search = "tab=fund";
  view.rerender(<MarketPage />);
  vi.mocked(api.getSentiment).mockResolvedValue({...sentiment, phase: "发酵"});
  state.search = "";
  view.rerender(<MarketPage />);
  await act(async () => {});
  expect(screen.getByText("发酵")).toBeTruthy();
  await act(async () => {
    oldOverview({indices: [], total_amount: null, total_amount_freshness: null});
    oldSentiment({...sentiment, phase: "退潮"});
  });
  expect(screen.queryByText("退潮")).toBeNull();
  expect(screen.getByText("发酵")).toBeTruthy();
});

it("a failed overview refresh retains its last result with a visible failure warning", async () => {
  vi.mocked(api.getMarketOverview).mockResolvedValue({indices: [{symbol: "000001", market: "SH", name: "已知上证指数", price: 3000} as Quote], total_amount: null, total_amount_freshness: null});
  render(<MarketPage />);
  await act(async () => {});
  expect(screen.getByText("已知上证指数")).toBeTruthy();
  vi.mocked(api.getMarketOverview).mockRejectedValueOnce(new Error("offline"));
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(screen.getByText("已知上证指数")).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain("保留值仅作上次结果参考");
});
