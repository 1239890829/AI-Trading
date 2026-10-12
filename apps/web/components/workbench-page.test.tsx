import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import WorkbenchPage from "@/app/workbench/page";
import type { SymbolBoardFund } from "@/lib/api";

const state = vi.hoisted(() => ({
  params: new URLSearchParams("symbol=600127&ct=minute&rt=info&from=%2Fmarket%3Ftab%3Devents"),
  push: vi.fn(), replace: vi.fn(),
  watch: [{symbol: "600127", group_name: "默认"}, {symbol: "603256", group_name: "材料观察"}],
  quoteStream: {status: "closed", quoteFeed: {subscribe: () => () => {}, getSnapshot: () => undefined}, quotes: {
    "600127": {symbol: "600127", name: "金健米业", price: 8.13, change_pct: 2.52, source: "fixture", quality: "high", quality_reasons: [], received_at: "2026-10-08T09:55:00+08:00", data_timestamp: "2026-10-08T09:55:00+08:00"},
  }},
  realPositions: {items: [{symbol: "600127"}]},
  reloadReal: vi.fn(async () => undefined), mobile: false, mediaListeners: new Set<() => void>(),
}));
vi.mock("next/navigation", () => ({useRouter: () => ({push: state.push, replace: state.replace}), useSearchParams: () => state.params}));
vi.mock("@/hooks/use-quote-stream", () => ({
  STREAM_STATUS_LABEL: {closed: {text: "休市", cls: ""}},
  useQuoteStream: () => state.quoteStream,
}));
// Match the real hooks' stable data identity between network updates.
// A fresh items array on every render repeatedly triggers the REST fallback effect.
vi.mock("@/hooks/use-real-positions", () => ({useRealPositions: () => ({data: state.realPositions, error: null, reload: state.reloadReal})}));
vi.mock("@/components/stock-detail", () => ({StockDetailPanel: ({symbol, chartTab, rightTab, active, realPositions, quoteFeed}: {symbol: string; chartTab?: string; rightTab?: string; active?: boolean; realPositions?: {reload: () => Promise<void>}; quoteFeed?: unknown}) => <div data-testid="stock-detail" data-active={String(active)} data-shared-positions={String(realPositions?.reload === state.reloadReal)} data-shared-quote-feed={String(quoteFeed === state.quoteStream.quoteFeed)}>{symbol}/{chartTab}/{rightTab}</div>}));
vi.mock("@/components/index-cards", () => ({IndexCards: () => <div>指数摘要</div>}));
vi.mock("@/components/detail/account-scope-panel", () => ({AccountScopePanel: ({account}: {account: string}) => <div>账户结果 {account}</div>}));
vi.mock("@/components/picks/pick-detail-modal", () => ({PickDetailModal: ({target}: {target: {kind: string; item: {symbol: string}} | null}) => target ? <div role="dialog">{target.kind}/{target.item.symbol}</div> : null}));
vi.mock("@/lib/api", () => ({
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
afterEach(() => {cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals();});
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

  it("discards old base reads after a manual retry supersedes them", async () => {
    let finish!: (value: Awaited<ReturnType<typeof api.getWatchlist>>) => void;
    vi.mocked(api.getWatchlist).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    vi.mocked(api.getWatchlistGroups).mockRejectedValueOnce(new Error("groups offline"));
    render(<WorkbenchPage />);
    await screen.findByText(/分组读取失败/);
    state.watch = [{symbol: "002297", group_name: "默认"}];
    fireEvent.click(screen.getByRole("button", {name: "重试读取"}));
    await screen.findByRole("button", {name: "查看 002297 002297"});
    await act(async () => {finish([{symbol: "600127", group_name: "默认", source: "manual", created_at: "2026-10-08T09:55:00+08:00", updated_at: "2026-10-08T09:55:00+08:00"}]);});
    expect(screen.queryByRole("button", {name: "查看 金健米业 600127"})).toBeNull();
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
