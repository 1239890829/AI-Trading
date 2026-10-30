import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {act, cleanup, fireEvent, render, screen} from "@testing-library/react";
import {SearchBox} from "@/components/search-box";
import {getAssetRecords, saveAssetRecord, searchAssets, type AssetInstrument, type AssetSearchResult} from "@/lib/api/assets";
import {searchSymbols} from "@/lib/api";

const nav = vi.hoisted(() => ({push: vi.fn(), open: vi.fn()}));
vi.mock("next/navigation", () => ({useRouter: () => ({push: nav.push})}));
vi.mock("@/components/detail/symbol-detail-context", () => ({useSymbolDetail: () => ({open: nav.open})}));
vi.mock("@/lib/api", () => ({ApiError: class extends Error {constructor(readonly status: number, message: string) {super(message);}}, addToWatchlist: vi.fn(), searchSymbols: vi.fn()}));
vi.mock("@/lib/api/assets", () => ({getAssetRecords: vi.fn(), saveAssetRecord: vi.fn(), searchAssets: vi.fn()}));
const instrument: AssetInstrument = {instrument_id: "fund:OF:040046:CNY", asset_type: "fund", fund_type: "fund-otc", market: "OF", symbol: "040046", source_symbol: "040046.OF", currency: "CNY", name: "测试纳指联接", source: "ths_official"};
const result = (): AssetSearchResult => ({query: "纳指", asset_type: "fund", state: "ready", items: [instrument], reason: null, issues: [], coverage: "source_search_window", evidence: []});
beforeEach(() => {vi.resetAllMocks(); localStorage.clear(); vi.mocked(searchAssets).mockResolvedValue(result()); vi.mocked(getAssetRecords).mockResolvedValue({items: []}); vi.mocked(saveAssetRecord).mockResolvedValue({} as never);});
afterEach(() => {cleanup(); vi.useRealTimers();});

describe("分资产搜索与历史身份", () => {
  it("基金选择直接打开同一工作台，历史点击不重新取源且不污染 A 股 v1", async () => {
    vi.useFakeTimers(); const aHistory = JSON.stringify([{query: "茅台", symbol: "600519", name: "贵州茅台", market: "SH"}]); localStorage.setItem("ashare-security-search-history-v1", aHistory);
    const view = render(<SearchBox asset="fund"/>); const input = screen.getByRole("textbox");
    fireEvent.change(input, {target: {value: "纳指"}}); await act(async () => vi.advanceTimersByTime(250));
    fireEvent.click(screen.getByRole("button", {name: /测试纳指联接.*040046.OF/}));
    expect(nav.push).toHaveBeenLastCalledWith("/workbench?mode=watch&asset=fund&symbol=040046.OF"); expect(nav.open).not.toHaveBeenCalled();
    expect(localStorage.getItem("ashare-security-search-history-v1")).toBe(aHistory);
    const history = JSON.parse(localStorage.getItem("ashare-asset-search-history-v1:fund")!); expect(history[0].instrument.instrument_id).toBe(instrument.instrument_id);
    view.unmount(); render(<SearchBox asset="fund"/>); await act(async () => {});
    fireEvent.focus(screen.getByRole("textbox")); fireEvent.click(screen.getByRole("button", {name: "查看 测试纳指联接 040046.OF OF"}));
    expect(searchAssets).toHaveBeenCalledTimes(1); expect(searchSymbols).not.toHaveBeenCalled(); expect(nav.push).toHaveBeenCalledTimes(2);
    expect(document.activeElement).toBe(screen.getByRole("textbox"));
  });
  it("切换scope并卸载旧搜索后，迟到基金回包不能在美股显示", async () => {
    vi.useFakeTimers(); let resolve!: (value: AssetSearchResult) => void;
    vi.mocked(searchAssets).mockImplementation((query, asset) => asset === "fund" ? new Promise(done => {resolve = done;}) : Promise.resolve({...result(), query, asset_type: "us_equity", state: "blocked", reason: "美股源未核验", items: []}));
    const view = render(<SearchBox key="fund" asset="fund"/>); fireEvent.change(screen.getByRole("textbox"), {target: {value: "纳指"}}); await act(async () => vi.advanceTimersByTime(250));
    view.rerender(<SearchBox key="us" asset="us_equity"/>); fireEvent.change(screen.getByRole("textbox"), {target: {value: "AAPL"}}); await act(async () => vi.advanceTimersByTime(250));
    await act(async () => resolve(result())); expect(screen.queryByText("测试纳指联接")).toBeNull(); expect(screen.getByText("美股源未核验")).toBeTruthy(); expect(searchSymbols).not.toHaveBeenCalled();
  });
  it("错误币种/代码身份的历史被拒绝，不能按同号 A 股打开", async () => {
    localStorage.setItem("ashare-asset-search-history-v1:fund", JSON.stringify([{query: "纳指", symbol: "040046.OF", instrument: {...instrument, currency: "USD"}}]));
    render(<SearchBox asset="fund"/>); await act(async () => {}); fireEvent.focus(screen.getByRole("textbox"));
    expect(screen.queryByRole("button", {name: /查看.*040046/})).toBeNull(); expect(nav.open).not.toHaveBeenCalled();
  });
  it("基金快捷自选合并现有持有字段并携带版本，不覆盖份额成本", async () => {
    vi.useFakeTimers(); vi.mocked(getAssetRecords).mockResolvedValue({items: [{instrument_id: instrument.instrument_id, asset_type: "fund", source_symbol: "040046.OF", name: "手工标签", currency: "CNY", is_watched: false, units: 88, cost_total: 100, note: "保留", version: 4, updated_at: "2026-10-30T10:00:00+08:00", basis: "manual_not_broker_verified"}]});
    render(<SearchBox asset="fund"/>); fireEvent.change(screen.getByRole("textbox"), {target: {value: "纳指"}}); await act(async () => vi.advanceTimersByTime(250));
    fireEvent.click(screen.getByRole("button", {name: "加入自选 测试纳指联接"})); await act(async () => {});
    expect(saveAssetRecord).toHaveBeenCalledWith(instrument.instrument_id, {source_symbol: "040046.OF", name: "手工标签", is_watched: true, units: 88, cost_total: 100, note: "保留", expected_version: 4});
  });
  it("中文组合输入不请求，选字后才请求当前资产", async () => {
    vi.useFakeTimers(); render(<SearchBox asset="fund"/>); const input = screen.getByRole("textbox");
    fireEvent.compositionStart(input); fireEvent.change(input, {target: {value: "nazhi"}}); await act(async () => vi.advanceTimersByTime(500)); expect(searchAssets).not.toHaveBeenCalled();
    fireEvent.change(input, {target: {value: "纳指"}}); fireEvent.compositionEnd(input); await act(async () => {}); expect(searchAssets).toHaveBeenCalledWith("纳指", "fund");
  });
  it("搜索回包scope不匹配时不呈现结果或可写动作", async () => {
    vi.useFakeTimers(); vi.mocked(searchAssets).mockResolvedValue({...result(), asset_type: "us_equity"});
    render(<SearchBox asset="fund"/>); fireEvent.change(screen.getByRole("textbox"), {target: {value: "纳指"}}); await act(async () => vi.advanceTimersByTime(250));
    expect(screen.getByRole("alert").textContent).toContain("搜索返回的资产或关键词不匹配");
    expect(screen.queryByText("测试纳指联接")).toBeNull(); expect(saveAssetRecord).not.toHaveBeenCalled();
  });
});
