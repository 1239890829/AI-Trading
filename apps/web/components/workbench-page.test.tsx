import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import WorkbenchPage from "@/app/workbench/page";

const state = vi.hoisted(() => ({
  params: new URLSearchParams("symbol=600127&ct=minute&rt=info&from=%2Fmarket%3Ftab%3Devents"),
  push: vi.fn(), replace: vi.fn(),
  watch: [{symbol: "600127", group_name: "默认"}, {symbol: "603256", group_name: "材料观察"}],
}));
vi.mock("next/navigation", () => ({useRouter: () => ({push: state.push, replace: state.replace}), useSearchParams: () => state.params}));
vi.mock("@/hooks/use-quote-stream", () => ({
  STREAM_STATUS_LABEL: {closed: {text: "休市", cls: ""}},
  useQuoteStream: () => ({status: "closed", quotes: {
    "600127": {symbol: "600127", name: "金健米业", price: 8.13, change_pct: 2.52, source: "fixture", quality: "high", quality_reasons: [], received_at: "2026-10-08T09:55:00+08:00", data_timestamp: "2026-10-08T09:55:00+08:00"},
  }}),
}));
vi.mock("@/hooks/use-real-positions", () => ({useRealPositions: () => ({data: {items: [{symbol: "600127"}]}, error: null})}));
vi.mock("@/components/stock-detail", () => ({StockDetailPanel: ({symbol, chartTab, rightTab}: {symbol: string; chartTab?: string; rightTab?: string}) => <div data-testid="stock-detail">{symbol}/{chartTab}/{rightTab}</div>}));
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
afterEach(() => {cleanup(); vi.clearAllMocks();});
beforeEach(() => {
  state.params = new URLSearchParams("symbol=600127&ct=minute&rt=info&from=%2Fmarket%3Ftab%3Devents");
  state.watch = [{symbol: "600127", group_name: "默认"}, {symbol: "603256", group_name: "材料观察"}];
});

async function selectList(name: string) {
  fireEvent.keyDown(screen.getByRole("button", {name: /^列表：/}), {key: "ArrowDown"});
  fireEvent.click(await screen.findByRole("menuitemradio", {name: new RegExp(`^${name}`)}));
}

describe("B+C workbench production consumers", () => {
  it("keeps missing quotes visible and preserves object, chart, detail and return identity when selecting", async () => {
    render(<WorkbenchPage />);
    expect(await screen.findByRole("button", {name: "查看 603256 603256"})).toBeTruthy();
    expect(screen.getByText("等待行情")).toBeTruthy();
    expect(screen.getByTestId("stock-detail").textContent).toBe("600127/minute/info");
    fireEvent.click(screen.getByRole("button", {name: "查看 603256 603256"}));
    const next = new URL(state.replace.mock.calls.at(-1)?.[0], "http://localhost");
    expect(next.searchParams.get("symbol")).toBe("603256");
    expect(next.searchParams.get("ct")).toBe("minute");
    expect(next.searchParams.get("rt")).toBe("info");
    expect(next.searchParams.get("from")).toBe("/market?tab=events");
    expect(api.addToWatchlist).not.toHaveBeenCalled();
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
