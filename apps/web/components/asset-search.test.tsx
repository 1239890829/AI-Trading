import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {act, cleanup, fireEvent, render, screen} from "@testing-library/react";
import {SearchBox} from "@/components/search-box";
import {getAssetRecords, getFundCatalog, saveAssetRecord, searchAssets, type AssetInstrument, type AssetSearchResult, type FundCatalog} from "@/lib/api/assets";
import {searchSymbols} from "@/lib/api";
import {searchUsAssets} from "@/lib/api/us-assets";

const nav = vi.hoisted(() => ({push: vi.fn(), open: vi.fn()}));
vi.mock("next/navigation", () => ({useRouter: () => ({push: nav.push})}));
vi.mock("@/components/detail/symbol-detail-context", () => ({useSymbolDetail: () => ({open: nav.open})}));
vi.mock("@/lib/api", () => ({ApiError: class extends Error {constructor(readonly status: number, message: string) {super(message);}}, addToWatchlist: vi.fn(), searchSymbols: vi.fn()}));
vi.mock("@/lib/api/assets", () => ({getAssetRecords: vi.fn(), getFundCatalog: vi.fn(), saveAssetRecord: vi.fn(), searchAssets: vi.fn()}));
vi.mock("@/lib/api/us-assets", async original => ({...await original<typeof import("@/lib/api/us-assets")>(), searchUsAssets: vi.fn()}));
const instrument: AssetInstrument = {instrument_id: "fund:OF:040046:CNY", asset_type: "fund", fund_type: "fund-otc", market: "OF", symbol: "040046", source_symbol: "040046.OF", currency: "CNY", name: "测试纳指联接", source: "ths_official"};
const result = (): AssetSearchResult => ({query: "纳指", asset_type: "fund", state: "ready", items: [instrument], reason: null, issues: [], coverage: "source_search_window", evidence: []});
const sourceCatalog = (): FundCatalog => ({state: "ready", tab: "meiguo", purchase: "all", source: "ths_official", items: [{source_symbol: "040047.OF", source_name: "来源纳指联接C", state: "candidate"}]} as FundCatalog);
beforeEach(() => {vi.resetAllMocks(); localStorage.clear(); vi.mocked(searchAssets).mockResolvedValue(result()); vi.mocked(searchUsAssets).mockImplementation(async query => ({query, asset_type: "us_equity", state: "blocked", reason: "美股源未核验", items: [], issues: [], evidence: []})); vi.mocked(getFundCatalog).mockResolvedValue({state: "unavailable", items: []} as unknown as FundCatalog); vi.mocked(getAssetRecords).mockResolvedValue({items: []}); vi.mocked(saveAssetRecord).mockResolvedValue({} as never);});
afterEach(() => {cleanup(); vi.useRealTimers();});

describe("分资产搜索与历史身份", () => {
  it("美股来源候选和历史一次直达，不造完整身份或触发A股/基金读写", async () => {
    vi.useFakeTimers();
    vi.mocked(searchUsAssets).mockImplementation(async query => ({query, asset_type: "us_equity", state: "ready", reason: null,
      items: [{symbol: "AAPL", source_symbol: "AAPL.OQ", market: "US", name: "苹果", source_kind: "GP", security_type: "stock", source: "tencent"}], issues: [], evidence: []}));
    const view = render(<SearchBox asset="us_equity"/>);
    fireEvent.change(screen.getByRole("textbox"), {target: {value: "AAPL"}}); await act(async () => vi.advanceTimersByTime(250));
    expect(screen.getByText("美股来源 · 点选核对")).toBeTruthy();
    expect(screen.queryByRole("button", {name: /加入自选/})).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "苹果 AAPL.OQ US"}));
    expect(nav.push).toHaveBeenLastCalledWith("/workbench?mode=watch&asset=us_equity&symbol=AAPL.OQ");
    const saved = JSON.parse(localStorage.getItem("ashare-asset-search-history-v1:us_equity")!)[0];
    expect(saved.us_candidate).toBe(true); expect(saved.instrument).toBeUndefined();
    view.unmount(); render(<SearchBox asset="us_equity"/>); await act(async () => {});
    fireEvent.focus(screen.getByRole("textbox")); fireEvent.click(screen.getByRole("button", {name: "查看 苹果 AAPL.OQ US"}));
    expect(nav.push).toHaveBeenCalledTimes(2); expect(searchUsAssets).toHaveBeenCalledTimes(1);
    expect(searchSymbols).not.toHaveBeenCalled(); expect(searchAssets).not.toHaveBeenCalled();
    expect(getFundCatalog).not.toHaveBeenCalled(); expect(saveAssetRecord).not.toHaveBeenCalled();
  });
  it("错误US来源和证券身份不允许展示、迁入A股或保存", async () => {
    vi.useFakeTimers(); vi.mocked(searchUsAssets).mockImplementation(async query => ({query, asset_type: "us_equity", state: "ready", reason: null,
      items: [{symbol: "AAPL", source_symbol: "AAPL.HK", market: "US", name: "错市场", source_kind: "GP", security_type: "stock", source: "tencent"}], issues: [], evidence: []}));
    const view = render(<SearchBox asset="us_equity"/>); fireEvent.change(screen.getByRole("textbox"), {target: {value: "AAPL"}}); await act(async () => vi.advanceTimersByTime(250));
    expect(screen.queryByText("错市场")).toBeNull();
    view.unmount(); localStorage.setItem("ashare-security-search-history-v1", JSON.stringify([{query:"AAPL",symbol:"AAPL.OQ",name:"苹果",market:"US",us_candidate:true}]));
    render(<SearchBox/>); await act(async () => {}); fireEvent.focus(screen.getByRole("textbox"));
    expect(screen.queryByRole("button", {name: /查看.*苹果/})).toBeNull(); expect(nav.open).not.toHaveBeenCalled();
  });
  it.each(["provider", "kind"])("同代码但来源或证券分类不符时不冒充腾讯：%s", async mismatch => {
    vi.useFakeTimers();
    vi.mocked(searchUsAssets).mockImplementation(async query => ({query, asset_type: "us_equity", state: "ready", reason: null,
      items: [{symbol: "AAPL", source_symbol: "AAPL.OQ", market: "US", name: "错误来源名称", source_kind: mismatch === "kind" ? "GP-ETF" : "GP", security_type: "stock", source: mismatch === "provider" ? "other" : "tencent"} as never], issues: [], evidence: []}));
    render(<SearchBox asset="us_equity"/>); fireEvent.change(screen.getByRole("textbox"), {target: {value: "AAPL"}}); await act(async () => vi.advanceTimersByTime(250));
    expect(screen.queryByText("错误来源名称")).toBeNull(); expect(nav.push).not.toHaveBeenCalled(); expect(saveAssetRecord).not.toHaveBeenCalled();
  });
  it("基金选择直接打开同一工作台，历史点击不重新取源且不污染 A 股 v1", async () => {
    vi.useFakeTimers(); const aHistory = JSON.stringify([{query: "茅台", symbol: "600519", name: "贵州茅台", market: "SH"}]); localStorage.setItem("ashare-security-search-history-v1", aHistory);
    const view = render(<SearchBox asset="fund"/>); const input = screen.getByRole("textbox");
    fireEvent.change(input, {target: {value: "纳指"}}); await act(async () => vi.advanceTimersByTime(250));
    fireEvent.click(screen.getByRole("button", {name: /测试纳指联接.*040046.OF/}));
    expect(nav.push).toHaveBeenLastCalledWith("/workbench?mode=research&asset=fund&symbol=040046.OF"); expect(nav.open).not.toHaveBeenCalled();
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
    vi.useFakeTimers(); vi.mocked(searchAssets).mockResolvedValue({...result(), asset_type: "us_equity"}); vi.mocked(getFundCatalog).mockResolvedValue(sourceCatalog());
    render(<SearchBox asset="fund"/>); fireEvent.change(screen.getByRole("textbox"), {target: {value: "纳指"}}); await act(async () => vi.advanceTimersByTime(250));
    expect(screen.getByRole("alert").textContent).toContain("搜索返回的资产或关键词不匹配");
    expect(screen.queryByText("测试纳指联接")).toBeNull(); expect(saveAssetRecord).not.toHaveBeenCalled();
  });
  it("来源中文搜索只复用一份名单，不造币种或直接自选；历史点击直开详情核验", async () => {
    vi.useFakeTimers(); vi.mocked(getFundCatalog).mockResolvedValue(sourceCatalog()); vi.mocked(searchAssets).mockImplementation(async query => ({...result(), query, items: []}));
    const view = render(<SearchBox asset="fund"/>); const input = screen.getByRole("textbox");
    fireEvent.change(input, {target: {value: "纳指"}}); await act(async () => vi.advanceTimersByTime(250));
    expect(screen.queryByRole("button", {name: "加入自选 来源纳指联接C"})).toBeNull();
    expect(screen.getByText("来源候选 · 份额待核")).toBeTruthy();
    fireEvent.change(input, {target: {value: "联接"}}); await act(async () => vi.advanceTimersByTime(250));
    expect(getFundCatalog).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", {name: /来源纳指联接C.*040047.OF/}));
    expect(nav.push).toHaveBeenLastCalledWith("/workbench?mode=research&asset=fund&symbol=040047.OF");
    const saved = JSON.parse(localStorage.getItem("ashare-asset-search-history-v1:fund")!)[0];
    expect(saved.source_candidate).toBe(true); expect(saved.instrument).toBeUndefined(); expect(saved.currency).toBeUndefined();
    view.unmount(); render(<SearchBox asset="fund"/>); await act(async () => {}); fireEvent.focus(screen.getByRole("textbox"));
    fireEvent.click(screen.getByRole("button", {name: "查看 来源纳指联接C 040047.OF OF"}));
    expect(searchAssets).toHaveBeenCalledTimes(2); expect(getFundCatalog).toHaveBeenCalledTimes(1);
    expect(saveAssetRecord).not.toHaveBeenCalled(); expect(getAssetRecords).not.toHaveBeenCalled(); expect(nav.open).not.toHaveBeenCalled();
  });
  it("标的搜索失败仍可打开来源候选；冲突/场内项不伪造份额身份", async () => {
    vi.useFakeTimers(); vi.mocked(searchAssets).mockRejectedValue(new Error("搜索超时"));
    const catalog = sourceCatalog(); catalog.items.push({...catalog.items[0], source_symbol: "501312.SH", source_name: "场内纳指", state: "out_of_scope"}, {...catalog.items[0], source_symbol: "040048.OF", source_name: "冲突纳指", state: "conflict"});
    vi.mocked(getFundCatalog).mockResolvedValue(catalog);
    render(<SearchBox asset="fund"/>); fireEvent.change(screen.getByRole("textbox"), {target: {value: "纳指"}}); await act(async () => vi.advanceTimersByTime(250));
    expect(screen.getByText("来源纳指联接C")).toBeTruthy(); expect(screen.getByText(/标的搜索暂不可用/)).toBeTruthy();
    expect(screen.queryByText("场内纳指")).toBeNull(); expect(screen.queryByText("冲突纳指")).toBeNull(); expect(saveAssetRecord).not.toHaveBeenCalled();
  });
  it("来源候选历史不能迁入A股，缺完整代码的基金候选历史也不接纳", async () => {
    const invalid = [{query: "纳指", symbol: "040047", name: "缺后缀", market: "OF", source_candidate: true}];
    localStorage.setItem("ashare-asset-search-history-v1:fund", JSON.stringify(invalid));
    const view = render(<SearchBox asset="fund"/>); await act(async () => {}); fireEvent.focus(screen.getByRole("textbox"));
    expect(screen.queryByRole("button", {name: /查看/})).toBeNull();
    view.unmount(); localStorage.setItem("ashare-security-search-history-v1", JSON.stringify([{...invalid[0], symbol: "040047.OF"}]));
    render(<SearchBox/>); await act(async () => {}); fireEvent.focus(screen.getByRole("textbox"));
    expect(screen.queryByRole("button", {name: /查看/})).toBeNull(); expect(nav.open).not.toHaveBeenCalled();
  });
  it("候选目录慢读不能拖住已有标的搜索；关闭后迟到结果不重开搜索", async () => {
    vi.useFakeTimers(); let finish!: (value: FundCatalog) => void;
    vi.mocked(getFundCatalog).mockImplementation(() => new Promise(resolve => {finish = resolve;}));
    render(<SearchBox asset="fund"/>); fireEvent.change(screen.getByRole("textbox"), {target: {value: "纳指"}}); await act(async () => vi.advanceTimersByTime(250));
    expect(screen.getByText("测试纳指联接")).toBeTruthy(); expect(screen.getByRole("textbox").getAttribute("aria-busy")).toBe("false");
    fireEvent.click(screen.getByRole("button", {name: "关闭搜索结果"}));
    await act(async () => finish(sourceCatalog()));
    expect(screen.queryByRole("region", {name: "证券搜索结果"})).toBeNull(); expect(nav.push).not.toHaveBeenCalled();
  });
  it("标的检索慢读时先展示源中文候选，仍不开放快捷持有", async () => {
    vi.useFakeTimers(); let finish!: (value: AssetSearchResult) => void;
    vi.mocked(searchAssets).mockImplementation(() => new Promise(resolve => {finish = resolve;})); vi.mocked(getFundCatalog).mockResolvedValue(sourceCatalog());
    render(<SearchBox asset="fund"/>); fireEvent.change(screen.getByRole("textbox"), {target: {value: "纳指"}}); await act(async () => vi.advanceTimersByTime(250));
    expect(screen.getByText("来源纳指联接C")).toBeTruthy(); expect(screen.queryByRole("button", {name: /加入自选/})).toBeNull();
    await act(async () => finish(result())); expect(screen.getByText("测试纳指联接")).toBeTruthy(); expect(saveAssetRecord).not.toHaveBeenCalled();
  });
  it("目录证据过期只在下一次搜索读取，不因时间流逝轮询或永久缓存", async () => {
    vi.useFakeTimers(); const now = new Date("2026-10-30T04:00:00Z"); vi.setSystemTime(now);
    vi.mocked(searchAssets).mockImplementation(async query => ({...result(), query}));
    vi.mocked(getFundCatalog).mockResolvedValue({...sourceCatalog(), evidence: [{endpoint: "quota/list", params: {}, received_at: new Date(now.getTime() - 290_000).toISOString(), payload_sha256: "hash", receipt_id: "receipt", raw_available: true}]});
    render(<SearchBox asset="fund"/>); const input = screen.getByRole("textbox");
    fireEvent.change(input, {target: {value: "纳指"}}); await act(async () => vi.advanceTimersByTime(250));
    expect(getFundCatalog).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTime(10_001)); expect(getFundCatalog).toHaveBeenCalledTimes(1);
    fireEvent.change(input, {target: {value: "联接"}}); await act(async () => vi.advanceTimersByTime(250)); expect(getFundCatalog).toHaveBeenCalledTimes(2);
    expect(saveAssetRecord).not.toHaveBeenCalled();
  });
});
