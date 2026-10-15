import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import WorkbenchPage from "@/app/workbench/page";
import type { SymbolBoardFund } from "@/lib/api";
import {notifyWatchlistChanged} from "@/lib/watchlist-sync";
import {getWatchlist as readActualWatchlist} from "@/lib/api/watchlist";
import {SymbolDetailStateCtx} from "@/components/detail/symbol-detail-context";
import type {Quote} from "@/types/market";

const state = vi.hoisted(() => ({
  params: new URLSearchParams("symbol=600127&ct=minute&rt=info&from=%2Fmarket%3Ftab%3Devents"),
  push: vi.fn(), replace: vi.fn(),
  watch: [{symbol: "600127", group_name: "默认"}, {symbol: "603256", group_name: "材料观察"}],
  quoteStream: {status: "live", quoteFeed: {subscribe: () => () => {}, getSnapshot: () => undefined}, quotes: {
    "600127": {symbol: "600127", name: "金健米业", price: 8.13, change_pct: 2.52, source: "fixture", quality: "high", quality_reasons: [], received_at: "2026-10-08T09:55:00+08:00", data_timestamp: "2026-10-08T09:55:00+08:00"},
  }},
  realPositions: {items: [{symbol: "600127"}]},
  reloadReal: vi.fn(async () => undefined), mobile: false, mediaListeners: new Set<() => void>(),
}));
vi.mock("next/navigation", () => ({useRouter: () => ({push: state.push, replace: state.replace}), useSearchParams: () => state.params}));
vi.mock("@/lib/market-hours", () => ({isTradingSession: () => true}));
vi.mock("@/hooks/use-quote-stream", () => ({
  STREAM_STATUS_LABEL: {live: {text: "已连接", cls: "", title: "通信夹具，不证明源报价已更新"}},
  useQuoteStream: () => state.quoteStream,
}));
// Match the real hooks' stable data identity between network updates.
// A fresh items array on every render repeatedly triggers the REST fallback effect.
vi.mock("@/hooks/use-real-positions", () => ({useRealPositions: () => ({data: state.realPositions, error: null, reload: state.reloadReal})}));
vi.mock("@/components/stock-detail", () => ({StockDetailPanel: ({symbol, chartTab, rightTab, active, realPositions, quoteFeed, watchlistMembership, onWatchlistAdded, indexQuote}: {symbol: string; chartTab?: string; rightTab?: string; active?: boolean; realPositions?: {reload: () => Promise<void>}; quoteFeed?: unknown; watchlistMembership?: boolean | null; onWatchlistAdded?: (symbol: string) => void; indexQuote?: Quote | null}) => <><div data-testid="stock-detail" data-active={String(active)} data-shared-positions={String(realPositions?.reload === state.reloadReal)} data-shared-quote-feed={String(quoteFeed === state.quoteStream.quoteFeed)} data-membership={String(watchlistMembership)} data-index-source={indexQuote === null ? "missing" : indexQuote?.source ?? "no-owner"}>{symbol}/{chartTab}/{rightTab}</div><button onClick={() => onWatchlistAdded?.(symbol)}>确认加入回执</button></>}));
vi.mock("@/components/index-cards", () => ({IndexCards: ({onSelect}: {onSelect: (symbol: string) => void}) => <div>指数摘要<button onClick={() => onSelect("sh000688")}>查看科创50</button></div>}));
vi.mock("@/components/detail/account-scope-panel", () => ({AccountScopePanel: ({account}: {account: string}) => <div>账户结果 {account}</div>}));
vi.mock("@/components/picks/pick-detail-modal", () => ({PickDetailModal: ({target}: {target: {kind: string; item: {symbol: string}} | null}) => target ? <div role="dialog">{target.kind}/{target.item.symbol}</div> : null}));
vi.mock("@/lib/api", () => ({
  isIndexSymbol: (symbol: string) => /^(?:sh|sz)\d{6}$/.test(symbol),
  getWatchlist: vi.fn(async () => state.watch), getWatchlistGroups: vi.fn(async () => ["材料观察"]),
  getMarketOverview: vi.fn(async () => ({indices: [], total_amount: null, total_amount_freshness: null})),
  getPaperPositions: vi.fn(async () => [{symbol: "600127", quantity: 100, pnl_pct: null}]),
  getRiskState: vi.fn(async () => null), getTurnoverToday: vi.fn(async () => null),
  getQuotes: vi.fn(async () => []), getBoardFundBySymbols: vi.fn(async () => ({})), getPositionLabels: vi.fn(async () => ({})),
  getTodayPicks: vi.fn(async () => ({date: "2026-10-08", items: [{symbol: "600127", name: "金健米业", score: 72, themes: ["粮食安全"], echelon_role: "观察"}]})),
  getIntradayTop: vi.fn(async () => ({items: [{symbol: "600127", name: "金健米业", tier: 2, theme: "粮食安全", stage: "观察", pick_basis: "规则依据", certainty: null, distinctiveness: null}, {symbol: "002297", name: "博云新材", tier: 3, theme: "材料", stage: "观察", pick_basis: "规则依据", certainty: null, distinctiveness: null}]})),
  addToWatchlist: vi.fn(async () => undefined), removeFromWatchlist: vi.fn(async () => undefined), updateWatchlistGroup: vi.fn(async () => undefined),
  createWatchlistGroup: vi.fn(async () => undefined), renameWatchlistGroup: vi.fn(async () => undefined), deleteWatchlistGroup: vi.fn(async () => undefined),
}));
const api = await import("@/lib/api");
afterEach(() => {cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();});
beforeEach(() => {
  state.mobile = false;
  state.mediaListeners.clear();
  vi.stubGlobal("matchMedia", () => ({get matches() {return state.mobile;}, addEventListener: (_: string, listener: () => void) => state.mediaListeners.add(listener), removeEventListener: (_: string, listener: () => void) => state.mediaListeners.delete(listener)}));
  state.params = new URLSearchParams("symbol=600127&ct=minute&rt=info&from=%2Fmarket%3Ftab%3Devents");
  state.watch = [{symbol: "600127", group_name: "默认"}, {symbol: "603256", group_name: "材料观察"}];
  vi.mocked(api.getBoardFundBySymbols).mockReset().mockResolvedValue({});
});

const industry = (board_name: string, main_net_yi = 1.25): SymbolBoardFund => ({
  board_name, board_code: "BKfixture", level: "industry", kind: "industry",
  change_pct: 1, main_net_yi, main_net_ratio: null, streak: null,
});

async function selectList(name: string) {
  fireEvent.keyDown(screen.getByRole("button", {name: /^列表：/}), {key: "ArrowDown"});
  fireEvent.click(await screen.findByRole("menuitemradio", {name: new RegExp(`^${name}`)}));
}

describe("B+C workbench production consumers", () => {
  it("详情复用已有概览的上证观察，空成功明确缺项而非新建指数读取", async () => {
    const sh: Quote = {symbol: "000001", market: "SH", source: "tencent", quality: "high", quality_reasons: [], received_at: "2026-10-14T15:00:01+08:00", data_timestamp: "2026-10-14T15:00:00+08:00", price: 3000, prev_close: 2990};
    vi.mocked(api.getMarketOverview).mockResolvedValueOnce({indices: [sh], total_amount: null, total_amount_freshness: null});
    render(<WorkbenchPage/>);
    await waitFor(() => expect(screen.getByTestId("stock-detail").dataset.indexSource).toBe("tencent"));
    expect(api.getMarketOverview).toHaveBeenCalledTimes(1);
  });
  it("父概览未成功时指数观察为unknown，成功空批才明确缺项", async () => {
    vi.mocked(api.getMarketOverview).mockRejectedValueOnce(new Error("overview not read"));
    render(<WorkbenchPage/>);
    await screen.findByText(/行情概览读取失败/);
    expect(screen.getByTestId("stock-detail").dataset.indexSource).toBe("no-owner");
    fireEvent.click(screen.getByRole("button", {name: "重试读取"}));
    await waitFor(() => expect(screen.getByTestId("stock-detail").dataset.indexSource).toBe("missing"));
    expect(api.getMarketOverview).toHaveBeenCalledTimes(2);
  });
  it("证券详情弹窗打开时暂停背景私有读取，关闭立即恢复且保留父报价owner", async () => {
    const view = render(<SymbolDetailStateCtx.Provider value={null}><WorkbenchPage/></SymbolDetailStateCtx.Provider>);
    await screen.findByRole("button", {name: "查看 金健米业 600127"});
    expect(screen.getByTestId("stock-detail").dataset.active).toBe("true");
    view.rerender(<SymbolDetailStateCtx.Provider value={{symbol: "600127", preferModal: true}}><WorkbenchPage/></SymbolDetailStateCtx.Provider>);
    expect(screen.getByTestId("stock-detail").dataset.active).toBe("false");
    expect(screen.getByTestId("stock-detail").dataset.sharedQuoteFeed).toBe("true");
    view.rerender(<SymbolDetailStateCtx.Provider value={null}><WorkbenchPage/></SymbolDetailStateCtx.Provider>);
    expect(screen.getByTestId("stock-detail").dataset.active).toBe("true");
    expect(api.getWatchlist).toHaveBeenCalledTimes(1);
  });

  it("首帧补报价在同一证券集合的新数组到达时只保留一次读取", async () => {
    const previousRead = vi.mocked(api.getQuotes).getMockImplementation();
    const previousPositions = state.realPositions;
    vi.mocked(api.getQuotes).mockImplementation(() => new Promise(() => {}));
    try {
      const view = render(<WorkbenchPage/>);
      await screen.findByRole("button", {name: "查看 金健米业 600127"});
      const reads = vi.mocked(api.getQuotes).mock.calls.length;
      expect(reads).toBeGreaterThan(0);
      state.realPositions = {items: previousPositions.items.map(item => ({...item}))};
      view.rerender(<WorkbenchPage/>);
      await act(async () => {});
      expect(api.getQuotes).toHaveBeenCalledTimes(reads);
    } finally {
      state.realPositions = previousPositions;
      vi.mocked(api.getQuotes).mockImplementation(previousRead!);
    }
  });

  it("父列表未知时加入成功只确认该股，后续成功全表空可校准为不在自选", async () => {
    vi.mocked(api.getWatchlist).mockRejectedValueOnce(new Error("first read unavailable"));
    const view = render(<WorkbenchPage/>);
    await screen.findByText(/自选读取失败/);
    expect(screen.getByTestId("stock-detail").dataset.membership).toBe("null");
    fireEvent.click(screen.getByRole("button", {name: "确认加入回执"}));
    expect(screen.getByTestId("stock-detail").dataset.membership).toBe("true");
    expect(api.getWatchlist).toHaveBeenCalledTimes(1);
    state.params.set("symbol", "603256");
    view.rerender(<WorkbenchPage/>);
    expect(screen.getByTestId("stock-detail").dataset.membership).toBe("null");
    state.params.set("symbol", "600127");
    view.rerender(<WorkbenchPage/>);
    expect(screen.getByTestId("stock-detail").dataset.membership).toBe("true");
    state.watch = [];
    fireEvent.click(screen.getByRole("button", {name: "重试读取"}));
    await waitFor(() => expect(screen.getByTestId("stock-detail").dataset.membership).toBe("false"));
    expect(screen.queryByText(/自选读取失败/)).toBeNull();
  });
  it("父自选首读失败保持unknown，成功后保留确认值；单次写后只有父owner一读", async () => {
    vi.mocked(api.getWatchlist).mockRejectedValueOnce(new Error("first read unavailable"));
    render(<WorkbenchPage/>);
    await screen.findByText(/自选读取失败/);
    expect(screen.getByTestId("stock-detail").dataset.membership).toBe("null");
    fireEvent.click(screen.getByRole("button", {name: "重试读取"}));
    await waitFor(() => expect(screen.getByTestId("stock-detail").dataset.membership).toBe("true"));
    const reads = vi.mocked(api.getWatchlist).mock.calls.length;
    vi.mocked(api.getWatchlist).mockRejectedValueOnce(new Error("later unavailable"));
    act(() => notifyWatchlistChanged());
    await screen.findByText(/自选读取失败/);
    expect(api.getWatchlist).toHaveBeenCalledTimes(reads + 1);
    expect(screen.getByTestId("stock-detail").dataset.membership).toBe("true");
  });

  it("分组单失败的重试只读分组，未成功前保留原失败，不重读成功域", async () => {
    vi.mocked(api.getWatchlistGroups).mockRejectedValueOnce(new Error("group unavailable"));
    render(<WorkbenchPage/>);
    await screen.findByText(/分组读取失败/);
    expect(api.getWatchlist).toHaveBeenCalledTimes(1);
    expect(api.getMarketOverview).toHaveBeenCalledTimes(1);
    expect(api.getPaperPositions).toHaveBeenCalledTimes(1);
    vi.mocked(api.getWatchlistGroups).mockRejectedValueOnce(new Error("still unavailable"));
    fireEvent.click(screen.getByRole("button", {name: "重试读取"}));
    await waitFor(() => expect(api.getWatchlistGroups).toHaveBeenCalledTimes(2));
    expect(screen.getByText(/分组读取失败/)).toBeTruthy();
    expect(api.getWatchlist).toHaveBeenCalledTimes(1);
    expect(api.getMarketOverview).toHaveBeenCalledTimes(1);
    expect(api.getPaperPositions).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", {name: "重试读取"}));
    await waitFor(() => expect(screen.queryByText(/分组读取失败/)).toBeNull());
    expect(api.getWatchlistGroups).toHaveBeenCalledTimes(3);
    expect(api.getWatchlist).toHaveBeenCalledTimes(1);
  });

  it("shares the real initial GET but manual calibration bypasses the old owner without flashing or accepting its late result", async () => {
    const previous = vi.mocked(api.getWatchlist).getMockImplementation()!;
    vi.mocked(api.getWatchlist).mockImplementation(readActualWatchlist);
    const pending: {signal: AbortSignal; finish: (data: unknown) => void}[] = [];
    const fetcher = vi.fn((_url: string, init: RequestInit) => new Promise<Response>(resolve => {
      pending.push({signal: init.signal as AbortSignal, finish: data => resolve(new Response(JSON.stringify({data, meta: {}})))});
    }));
    vi.stubGlobal("fetch", fetcher);
    const sibling = readActualWatchlist();
    const view = render(<WorkbenchPage />);
    try {
      expect(fetcher).toHaveBeenCalledTimes(1);
      await act(async () => {pending[0].finish([{symbol: "600127", group_name: "默认"}]); await sibling;});
      expect(screen.getByRole("button", {name: "查看 金健米业 600127"})).toBeTruthy();
      act(() => notifyWatchlistChanged());
      expect(fetcher).toHaveBeenCalledTimes(2);
      const oldSibling = readActualWatchlist();
      expect(fetcher).toHaveBeenCalledTimes(2);
      act(() => notifyWatchlistChanged());
      expect(fetcher).toHaveBeenCalledTimes(3);
      expect(pending[1].signal.aborted).toBe(false);
      expect(screen.getByRole("button", {name: "查看 金健米业 600127"})).toBeTruthy();
      expect(screen.queryByLabelText("正在读取证券列表")).toBeNull();
      expect(screen.queryByText(/自选读取失败/)).toBeNull();
      await act(async () => {pending[2].finish([{symbol: "002297", group_name: "默认"}]);});
      expect(screen.getByRole("button", {name: "查看 002297 002297"})).toBeTruthy();
      await act(async () => {pending[1].finish([{symbol: "600127", group_name: "默认"}]); await oldSibling;});
      expect(screen.getByRole("button", {name: "查看 002297 002297"})).toBeTruthy();
      expect(screen.queryByRole("button", {name: "查看 金健米业 600127"})).toBeNull();
      expect(screen.queryByText(/自选读取失败/)).toBeNull();
    } finally {
      view.unmount();
      vi.mocked(api.getWatchlist).mockImplementation(previous);
    }
  });
  it("passes the existing quote owner to selected detail while preserving list quotes", async () => {
    render(<WorkbenchPage />);
    expect(await screen.findByRole("button", {name: "查看 金健米业 600127"})).toBeTruthy();
    expect(screen.getByTestId("stock-detail").dataset.sharedQuoteFeed).toBe("true");
    expect(screen.getByRole("button", {name: "查看 金健米业 600127"}).textContent).toContain("8.13");
    fireEvent.click(screen.getByRole("button", {name: "查看 603256 603256"}));
    expect(screen.getByTestId("stock-detail").dataset.sharedQuoteFeed).toBe("true");
  });
  it("shows the watchlist before the slow overview settles and isolates auxiliary errors", async () => {
    let finish!: (value: Awaited<ReturnType<typeof api.getMarketOverview>>) => void;
    vi.mocked(api.getMarketOverview).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    vi.mocked(api.getWatchlistGroups).mockRejectedValueOnce(new Error("groups offline"));
    render(<WorkbenchPage />);
    expect(await screen.findByRole("button", {name: "查看 金健米业 600127"})).toBeTruthy();
    expect(screen.queryByLabelText("正在读取证券列表")).toBeNull();
    expect(screen.getByText(/分组读取失败/)).toBeTruthy();
    await act(async () => {finish({indices: [], total_amount: null, total_amount_freshness: null} as Awaited<ReturnType<typeof api.getMarketOverview>>);});
    expect(screen.getByRole("button", {name: "查看 金健米业 600127"})).toBeTruthy();
  });

  it("reports a failed watchlist immediately while auxiliary data is still pending", async () => {
    let finish!: (value: Awaited<ReturnType<typeof api.getMarketOverview>>) => void;
    vi.mocked(api.getMarketOverview).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    vi.mocked(api.getWatchlist).mockRejectedValueOnce(new Error("watch offline"));
    render(<WorkbenchPage />);
    expect(await screen.findByText(/自选读取失败/)).toBeTruthy();
    expect(screen.getByText(/列表读取有缺项/)).toBeTruthy();
    expect(screen.queryByLabelText("正在读取证券列表")).toBeNull();
    await act(async () => {finish({indices: [], total_amount: null, total_amount_freshness: null} as Awaited<ReturnType<typeof api.getMarketOverview>>);});
  });

  it("leaves an unrelated pending watchlist read intact when retrying failed groups", async () => {
    let finish!: (value: Awaited<ReturnType<typeof api.getWatchlist>>) => void;
    vi.mocked(api.getWatchlist).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    vi.mocked(api.getWatchlistGroups).mockRejectedValueOnce(new Error("groups offline"));
    render(<WorkbenchPage />);
    await screen.findByText(/分组读取失败/);
    fireEvent.click(screen.getByRole("button", {name: "重试读取"}));
    await waitFor(() => expect(api.getWatchlistGroups).toHaveBeenCalledTimes(2));
    expect(api.getWatchlist).toHaveBeenCalledTimes(1);
    await act(async () => {finish([{symbol: "600127", group_name: "默认", source: "manual", created_at: "2026-10-08T09:55:00+08:00", updated_at: "2026-10-08T09:55:00+08:00"}]);});
    expect(api.getWatchlist).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", {name: "查看 金健米业 600127"})).toBeTruthy();
  });

  it("keeps quotes/paper positions at 10s, static lists at 60s and mutation sync immediate", async () => {
    vi.useFakeTimers();
    render(<WorkbenchPage />);
    await act(async () => {await vi.advanceTimersByTimeAsync(0);});
    expect(api.getMarketOverview).toHaveBeenCalledTimes(1);
    expect(api.getWatchlist).toHaveBeenCalledTimes(1);
    expect(api.getWatchlistGroups).toHaveBeenCalledTimes(1);
    expect(api.getPaperPositions).toHaveBeenCalledTimes(1);
    await act(async () => {await vi.advanceTimersByTimeAsync(10_000);});
    expect(api.getMarketOverview).toHaveBeenCalledTimes(2);
    expect(api.getWatchlist).toHaveBeenCalledTimes(1);
    expect(api.getWatchlistGroups).toHaveBeenCalledTimes(1);
    expect(api.getPaperPositions).toHaveBeenCalledTimes(2);
    await act(async () => {await vi.advanceTimersByTimeAsync(20_000);});
    expect(api.getMarketOverview).toHaveBeenCalledTimes(4);
    expect(api.getPaperPositions).toHaveBeenCalledTimes(4);
    await act(async () => {await vi.advanceTimersByTimeAsync(30_000);});
    expect(api.getMarketOverview).toHaveBeenCalledTimes(7);
    expect(api.getWatchlist).toHaveBeenCalledTimes(2);
    expect(api.getWatchlistGroups).toHaveBeenCalledTimes(2);
    expect(api.getPaperPositions).toHaveBeenCalledTimes(7);
    state.watch = [{symbol: "002297", group_name: "默认"}];
    await act(async () => {notifyWatchlistChanged();});
    expect(api.getWatchlist).toHaveBeenCalledTimes(3);
    expect(api.getWatchlistGroups).toHaveBeenCalledTimes(3);
    expect(api.getMarketOverview).toHaveBeenCalledTimes(7);
    expect(api.getPaperPositions).toHaveBeenCalledTimes(7);
    expect(screen.getByRole("button", {name: "查看 002297 002297"})).toBeTruthy();
  });

  it("pauses hidden mobile detail, resumes on reveal/desktop and shares the position owner", async () => {
    state.mobile = true;
    render(<WorkbenchPage />);
    const detail = screen.getByTestId("stock-detail");
    expect(detail.dataset.active).toBe("true");
    expect(detail.dataset.sharedPositions).toBe("true");
    fireEvent.click(screen.getByRole("button", {name: "指数与自选"}));
    expect(detail.dataset.active).toBe("false");
    fireEvent.click(screen.getByRole("button", {name: "图表"}));
    expect(detail.dataset.active).toBe("true");
    fireEvent.click(screen.getByRole("button", {name: "指数与自选"}));
    act(() => {state.mobile = false; state.mediaListeners.forEach(listener => listener());});
    expect(detail.dataset.active).toBe("true");
  });
  it("keeps the known industry after partial/empty refresh, never keeps the old cash value, and restores it on recovery", async () => {
    vi.mocked(api.getBoardFundBySymbols).mockResolvedValue({"600127": industry("农产品加工")});
    render(<WorkbenchPage />);
    const select = await screen.findByRole("button", {name: "查看 金健米业 600127"});
    await screen.findByText("行业 · 农产品加工");
    const slot = select.querySelector(".bc-attention-fund")!;
    expect(slot.textContent).toContain("+1.3亿");
    expect(api.getBoardFundBySymbols).toHaveBeenCalledTimes(1);

    vi.mocked(api.getBoardFundBySymbols).mockResolvedValue({});
    fireEvent(document, new Event("visibilitychange"));
    await screen.findByText("行业 · 农产品加工（上次）");
    expect(select.querySelector(".bc-attention-fund")).toBe(slot);
    expect(slot.textContent).toContain("未更新");
    expect(slot.textContent).not.toContain("1.3亿");
    expect(slot.getAttribute("title")).toContain("未沿用旧金额");

    vi.mocked(api.getBoardFundBySymbols).mockResolvedValue({"600127": industry("农产品加工", 2.5)});
    fireEvent(document, new Event("visibilitychange"));
    await screen.findByText("行业 · 农产品加工");
    expect(select.querySelector(".bc-attention-fund")).toBe(slot);
    expect(slot.textContent).toContain("+2.5亿");
    expect(slot.textContent).not.toContain("上次");
  });

  it("always provides an honest board slot and identifies concept fallback instead of calling it industry", async () => {
    vi.mocked(api.getBoardFundBySymbols).mockResolvedValue({"600127": {...industry("粮食安全"), level: "concept"}});
    render(<WorkbenchPage />);
    await screen.findByText("概念 · 粮食安全");
    const missing = screen.getByRole("button", {name: "查看 603256 603256"});
    expect(missing.querySelector(".bc-attention-fund")?.textContent).toBe("所属板块待确认--");
  });

  it("does not let a late old collection replace the new collection's board identity", async () => {
    let finish!: (rows: Record<string, SymbolBoardFund>) => void;
    vi.mocked(api.getBoardFundBySymbols).mockImplementation(symbols => symbols.includes("002297")
      ? Promise.resolve({"002297": industry("航空材料")})
      : new Promise(resolve => {finish = resolve;}));
    render(<WorkbenchPage />);
    await screen.findByRole("button", {name: "查看 金健米业 600127"});
    await waitFor(() => expect(api.getBoardFundBySymbols).toHaveBeenCalledTimes(1));
    expect(api.getBoardFundBySymbols).toHaveBeenLastCalledWith(["600127", "603256"]);
    state.watch = [{symbol: "002297", group_name: "默认"}];
    fireEvent(document, new Event("visibilitychange"));
    await screen.findByText("行业 · 航空材料");
    await act(async () => {finish({"600127": industry("农产品加工")});});
    expect(api.getBoardFundBySymbols).toHaveBeenCalledTimes(2);
    expect(api.getBoardFundBySymbols).toHaveBeenLastCalledWith(["002297"]);
    expect(screen.getByText("行业 · 航空材料")).toBeTruthy();
    expect(screen.queryByText(/行业 · 农产品加工/)).toBeNull();
  });

  it("covers the 51st stock in a second API batch without duplicate initial calls", async () => {
    state.watch = Array.from({length: 51}, (_, i) => ({symbol: String(600000 + i), group_name: "默认"}));
    vi.mocked(api.getBoardFundBySymbols).mockImplementation(async symbols => Object.fromEntries(symbols.map(symbol => [symbol, industry(`板块${symbol}`)])));
    render(<WorkbenchPage />);
    await screen.findByText("行业 · 板块600050");
    expect(api.getBoardFundBySymbols).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.getBoardFundBySymbols).mock.calls.map(([symbols]) => symbols.length)).toEqual([50, 1]);
  });

  it("keeps missing quotes visible and preserves object, chart, detail and return identity when selecting", async () => {
    render(<WorkbenchPage />);
    expect(await screen.findByRole("button", {name: "查看 603256 603256"})).toBeTruthy();
    expect(screen.getByText("等待行情")).toBeTruthy();
    expect(screen.getByTestId("stock-detail").textContent).toBe("600127/minute/info");
    expect(screen.getByRole("button", {name: "核对"}).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", {name: "指数与自选"}));
    expect(screen.getByRole("button", {name: "指数与自选"}).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", {name: "查看 603256 603256"}));
    expect(screen.getByRole("button", {name: "图表"}).getAttribute("aria-pressed")).toBe("true");
    const next = new URL(state.replace.mock.calls.at(-1)?.[0], "http://localhost");
    expect(next.searchParams.get("symbol")).toBe("603256");
    expect(next.searchParams.get("ct")).toBe("minute");
    expect(next.searchParams.get("rt")).toBe("info");
    expect(next.searchParams.get("from")).toBe("/market?tab=events");
    fireEvent.click(screen.getByRole("button", {name: "核对"}));
    expect(screen.getByTestId("stock-detail").textContent).toBe("603256/minute/info");
    expect(api.addToWatchlist).not.toHaveBeenCalled();
  });

  it("reveals an incoming account/detail deep link after a local mobile view switch", async () => {
    const page = render(<WorkbenchPage />);
    await screen.findByRole("button", {name: "查看 金健米业 600127"});
    fireEvent.click(screen.getByRole("button", {name: "指数与自选"}));
    state.params = new URLSearchParams("symbol=600127&mode=positions&account=paper&rt=trade&ct=minute");
    page.rerender(<WorkbenchPage />);
    expect(screen.getByRole("button", {name: "核对"}).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("stock-detail").textContent).toBe("600127/minute/trade");
    expect(await screen.findByText("账户结果 paper")).toBeTruthy();
    expect(api.addToWatchlist).not.toHaveBeenCalled();
    expect(api.removeFromWatchlist).not.toHaveBeenCalled();
  });

  it("keeps both system sources, deduplicates securities, and exposes each source's real detail", async () => {
    render(<WorkbenchPage />);
    await screen.findByRole("button", {name: "查看 金健米业 600127"});
    await selectList("系统候选");
    expect(screen.getAllByRole("button", {name: "查看 金健米业 600127"})).toHaveLength(1);
    expect(screen.getByRole("button", {name: "查看 002297 002297"})).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "查看 600127 盘中依据"}));
    expect(screen.getByRole("dialog").textContent).toBe("top/600127");
    fireEvent.click(screen.getByRole("button", {name: "查看 600127 精选依据"}));
    expect(screen.getByRole("dialog").textContent).toBe("pick/600127");
    expect(screen.queryByRole("button", {name: /移出自选/})).toBeNull();
  });

  it("reports a failed system source rather than confirming an empty candidate list", async () => {
    vi.mocked(api.getIntradayTop).mockRejectedValueOnce(new Error("offline"));
    render(<WorkbenchPage />);
    await screen.findByRole("button", {name: "查看 金健米业 600127"});
    await selectList("系统候选");
    expect(screen.getByText(/盘中候选读取失败/)).toBeTruthy();
    expect(screen.queryByText(/^暂无系统候选/)).toBeNull();
  });

  it("uses inline group confirmation and keeps input after the server rejects a new name", async () => {
    vi.mocked(api.createWatchlistGroup).mockRejectedValueOnce(new Error("分组名称已存在"));
    render(<WorkbenchPage />);
    await screen.findByRole("button", {name: "查看 金健米业 600127"});
    fireEvent.click(screen.getByRole("button", {name: "管理自选"}));
    fireEvent.click(screen.getByRole("button", {name: "新建分组"}));
    fireEvent.change(screen.getByLabelText("新分组名称"), {target: {value: "材料观察"}});
    fireEvent.click(screen.getByRole("button", {name: "保存分组"}));
    expect(await screen.findByText(/分组保存失败：分组名称已存在/)).toBeTruthy();
    expect((screen.getByLabelText("新分组名称") as HTMLInputElement).value).toBe("材料观察");
    expect(api.createWatchlistGroup).toHaveBeenCalledWith("材料观察");
  });

  it("keeps the same add control pending and preserves the code after rejection", async () => {
    let rejectAdd: (reason?: unknown) => void = () => {};
    vi.mocked(api.addToWatchlist).mockImplementationOnce(() => new Promise((_resolve, reject) => {rejectAdd = reject;}));
    render(<WorkbenchPage />);
    await screen.findByRole("button", {name: "查看 金健米业 600127"});
    fireEvent.click(screen.getByRole("button", {name: "管理自选"}));
    fireEvent.change(screen.getByLabelText("加入自选"), {target: {value: "002297"}});
    fireEvent.click(screen.getByRole("button", {name: "添加"}));
    expect((screen.getByRole("button", {name: "添加中…"}) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("加入自选") as HTMLInputElement).disabled).toBe(true);
    rejectAdd(new Error("offline"));
    expect(await screen.findByText(/添加失败.*输入代码已保留/)).toBeTruthy();
    expect((screen.getByLabelText("加入自选") as HTMLInputElement).value).toBe("002297");
    expect((screen.getByRole("button", {name: "添加"}) as HTMLButtonElement).disabled).toBe(false);
    expect(api.addToWatchlist).toHaveBeenCalledTimes(1);
  });

  it("keeps account scopes separate and never offers watchlist deletion for a position", async () => {
    state.params = new URLSearchParams("symbol=600127&mode=positions&account=paper&ct=minute&from=%2Fhunting");
    render(<WorkbenchPage />);
    expect(await screen.findByText("账户结果 paper")).toBeTruthy();
    expect(await screen.findByRole("button", {name: "查看 金健米业 600127"})).toBeTruthy();
    expect(screen.queryByRole("button", {name: /移出自选/})).toBeNull();
    fireEvent.keyDown(screen.getByRole("button", {name: "账户：手工模拟"}), {key: "ArrowDown"});
    fireEvent.click(await screen.findByRole("menuitemradio", {name: /机会影子/}));
    await waitFor(() => expect(state.push).toHaveBeenCalled());
    const next = new URL(state.push.mock.calls.at(-1)?.[0], "http://localhost");
    expect(next.searchParams.get("account")).toBe("hunting");
    expect(next.searchParams.get("ct")).toBe("minute");
    expect(next.searchParams.get("from")).toBe("/hunting");
    expect(api.removeFromWatchlist).not.toHaveBeenCalled();
  });
});

it("指数选择写入成分视角，指数回个股清除板块深链并打开盘口", async () => {
  state.params = new URLSearchParams("symbol=600127&ct=minute&rt=info");
  const view = render(<WorkbenchPage/>);
  await screen.findByRole("button", {name: "查看科创50"});
  fireEvent.click(screen.getByRole("button", {name: "查看科创50"}));
  let target = new URL(state.replace.mock.calls.at(-1)![0], "http://local.invalid");
  expect(target.searchParams.get("symbol")).toBe("sh000688");
  expect(target.searchParams.get("rt")).toBe("members");
  state.params = new URLSearchParams("symbol=sh000688&ct=minute&rt=boards");
  view.rerender(<WorkbenchPage/>);
  await screen.findByRole("button", {name: /查看 金健米业/});
  fireEvent.click(screen.getByRole("button", {name: /查看 金健米业/}));
  target = new URL(state.replace.mock.calls.at(-1)![0], "http://local.invalid");
  expect(target.searchParams.get("symbol")).toBe("600127");
  expect(target.searchParams.get("rt")).toBe("book");
});

it("从另一指数的板块视角选择新指数也回到该指数成分股", async () => {
  state.params = new URLSearchParams("symbol=sh000001&rt=boards");
  render(<WorkbenchPage/>);
  fireEvent.click(await screen.findByRole("button", {name: "查看科创50"}));
  const target = new URL(state.replace.mock.calls.at(-1)![0], "http://local.invalid");
  expect(target.searchParams.get("symbol")).toBe("sh000688");
  expect(target.searchParams.get("rt")).toBe("members");
});
