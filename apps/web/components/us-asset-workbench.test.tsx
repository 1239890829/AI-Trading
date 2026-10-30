import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {act, cleanup, createEvent, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {UsAssetWorkbench} from "@/components/us-asset-workbench";
import {UsDailyChart} from "@/components/us-daily-chart";
import {getAssetCapabilities} from "@/lib/api/assets";
import {ApiError} from "@/lib/api/client";
import * as api from "@/lib/api/us-assets";
import type {UsAssetRecord, UsDailyPoint, UsSnapshot} from "@/lib/api/us-assets";

const state = vi.hoisted(() => ({params: new URLSearchParams("asset=us_equity&symbol=AAPL.OQ"), replace: vi.fn()}));
vi.mock("next/navigation", () => ({useSearchParams: () => state.params, useRouter: () => ({replace: state.replace})}));
vi.mock("@/lib/api/assets", () => ({getAssetCapabilities: vi.fn()}));
vi.mock("@/lib/api/us-assets", async importOriginal => ({...await importOriginal<typeof import("@/lib/api/us-assets")>(), getUsSnapshot: vi.fn(), getUsAssetRecords: vi.fn(), saveUsWatchRecord: vi.fn(), deleteUsWatchRecord: vi.fn()}));
const capabilities = {items: [{asset_type: "us_equity" as const, state: "available" as const, reason: "腾讯已核常规与日K，扩展时段未核", capabilities: ["regular_quote", "daily"], unavailable: ["overnight"], source: "tencent", trading: false as const}]};
function fixture(code = "AAPL.OQ"): UsSnapshot {
  const symbol = code.split(".").slice(0, -1).join(".");
  const missing = {state: "unavailable" as const, reason: "当前来源尚无独立时段事实"};
  return {state: "partial", instrument: {instrument_id: `us_equity:US:${code}:USD`, asset_type: "us_equity", market: "US", symbol, source_symbol: code, currency: "USD", name: symbol === "AAPL" ? "苹果" : "英伟达", english_name: symbol === "AAPL" ? "Apple Inc." : "NVIDIA", security_type: "stock", exchange_source_code: code.split(".").at(-1)!, source: "tencent"},
    regular: {state: "ready", price: 270, previous_close: 268, change: 2, change_pct: .75, change_basis: "source_previous_regular_close", open: 267, high: 272, low: 266, volume_shares: 12_345_678, turnover: 123_456_789, source_time: "2026-10-29T16:00:00-04:00", source_timezone: "America/New_York", received_at: "2026-10-30T05:00:00Z", observation: "regular_close", feed_delay_seconds: null, realtime_verified: false, source: "tencent", evidence: []},
    daily: {state: "ready", reason: null, points: [{date: "2026-10-27", open: 260, high: 264, low: 259, close: 263, volume_shares: 400_000}, {date: "2026-10-28", open: 264, high: 269, low: 263, close: 268, volume_shares: 500_000}, {date: "2026-10-29", open: 267, high: 272, low: 266, close: 270, volume_shares: 600_000}], price_basis: "none", source: "tencent", source_symbol: code, evidence: []},
    pre_market: missing, post_market: missing, overnight: missing, minute: {...missing, reason: "分钟源不完整"}, clock_phase: "overnight", calendar_status: "unverified", calendar_note: "节假日与半日市未核，不表示正在开市", received_at: "2026-10-30T05:00:00Z", missing: ["overnight", "minute"]};
}
const record = (): UsAssetRecord => ({instrument_id: "us_equity:US:AAPL.OQ:USD", asset_type: "us_equity", source_symbol: "AAPL.OQ", currency: "USD", name: "保存苹果", is_watched: true, units: null, cost_total: null, note: null, version: 3, updated_at: "2026-10-30T05:00:00Z", basis: "manual_not_broker_verified"});
beforeEach(() => {vi.resetAllMocks(); state.params = new URLSearchParams("asset=us_equity&symbol=AAPL.OQ"); vi.mocked(getAssetCapabilities).mockResolvedValue(capabilities); vi.mocked(api.getUsAssetRecords).mockResolvedValue({items: [record()]}); vi.mocked(api.getUsSnapshot).mockImplementation(async code => fixture(code)); vi.mocked(api.saveUsWatchRecord).mockResolvedValue(record()); vi.mocked(api.deleteUsWatchRecord).mockResolvedValue();});
afterEach(() => cleanup());

describe("美股独立只读工作台", () => {
  it("常规收盘与日K可用，不因夜盘缺口整块关闭；保留美元、股数与双时区", async () => {
    const view = render(<UsAssetWorkbench/>); await screen.findByRole("heading", {name: "苹果"});
    expect(view.container.querySelector('main[data-asset="us_equity"]')).toBeTruthy();
    expect(screen.getByRole("img", {name: /AAPL.OQ原始非复权日K/})).toBeTruthy();
    expect(screen.getByText("常规时段收盘价")).toBeTruthy(); expect(screen.getByText(/延迟未核/)).toBeTruthy();
    expect(screen.getByText(/2026-10-29 16:00:00（纽约） \/ 2026-10-30 04:00:00（北京）/)).toBeTruthy();
    expect(screen.getByText("成交量（股）")).toBeTruthy(); expect(screen.getByText("12,345,678")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "夜盘"}));
    expect(screen.getByText(/夜盘暂无可核数据/)).toBeTruthy(); expect(screen.getByText(/不以常规收盘价代替/)).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "日K"}));
    expect(screen.getByRole("img")).toBeTruthy(); expect(api.getUsSnapshot).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", {name: /买入|卖出|记录持有/})).toBeNull();
  });
  it.each(["available", "stale", "wrong-identity"])("夜盘只呈现具名源的指示性买卖价，保持时段独立：%s", async state => {
    const value = fixture();
    value.overnight_data = {state: state === "stale" ? "stale" : "available", reason: state === "stale" ? "source_quote_too_old" : "source_indicative_bid_ask", instrument_id: state === "wrong-identity" ? "us_equity:US:NVDA.OQ:USD" : value.instrument.instrument_id, symbol: "AAPL", source: "alpaca", feed: "overnight", session: "overnight", authorization_verified: true, source_age_seconds: state === "stale" ? 90 : 2, freshness_limit_seconds: 60, received_at: "2026-10-30T05:00:02Z", checked_at: "2026-10-30T05:00:02Z", calendar_status: "unverified", calendar_note: "节假日未核", missing: [], evidence: [], quote: {bid_price: 271.11, ask_price: 271.22, bid_size_shares: 10, ask_size_shares: 20, source_time: "2026-10-30T05:00:00Z", source_timestamp: "2026-10-30T05:00:00Z", received_at: "2026-10-30T05:00:02Z", currency: "USD", price_kind: "indicative_bid_ask", last_trade_available: false, source: "alpaca", feed: "overnight"}};
    vi.mocked(api.getUsSnapshot).mockResolvedValue(value); render(<UsAssetWorkbench/>); await screen.findByRole("heading", {name: "苹果"});
    fireEvent.click(screen.getByRole("button", {name: "夜盘"}));
    if (state === "wrong-identity") {expect(screen.getByRole("alert").textContent).toContain("夜盘身份"); expect(screen.queryByText("271.11")).toBeNull();}
    else {expect(screen.getByText("271.11")).toBeTruthy(); expect(screen.getByText("271.22")).toBeTruthy(); if (state === "stale") expect(screen.getByRole("heading", {name: /已陈旧/})).toBeTruthy();}
    expect(screen.getByText(/仅指示性买卖报价/)).toBeTruthy(); expect(screen.getByText("270.00")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "日K"})); expect(screen.getByRole("img")).toBeTruthy(); expect(api.getUsSnapshot).toHaveBeenCalledTimes(1);
  });
  it.each(["AAPL", "600519.SH", "040046.OF", "NVDA.OQ?other=1"])("非canonical美股链接不请求错域：%s", async symbol => {
    state.params = new URLSearchParams({asset: "us_equity", symbol}); render(<UsAssetWorkbench/>);
    expect(await screen.findByRole("heading", {name: "美股身份无法确认"})).toBeTruthy(); expect(api.getUsSnapshot).not.toHaveBeenCalled();
  });
  it("切股时晚到回包不可覆盖当前证券，旧传输取消", async () => {
    let finish!: (value: UsSnapshot) => void; vi.mocked(api.getUsSnapshot).mockImplementation(code => code === "AAPL.OQ" ? new Promise(resolve => {finish = resolve;}) : Promise.resolve(fixture(code)));
    const view = render(<UsAssetWorkbench/>); await waitFor(() => expect(api.getUsSnapshot).toHaveBeenCalledTimes(1)); const signal = vi.mocked(api.getUsSnapshot).mock.calls[0][1]!.signal!;
    state.params = new URLSearchParams("asset=us_equity&symbol=NVDA.OQ"); view.rerender(<UsAssetWorkbench/>);
    expect(await screen.findByRole("heading", {name: "英伟达"})).toBeTruthy(); expect(signal.aborted).toBe(true);
    await act(async () => finish(fixture())); expect(screen.queryByRole("heading", {name: "苹果"})).toBeNull();
  });
  it.each(["symbol", "currency"])("返回%s不符时不显示价格、图表或可写自选", async field => {
    const value = fixture(field === "symbol" ? "NVDA.OQ" : "AAPL.OQ"); if (field === "currency") (value.instrument as unknown as {currency: string}).currency = "CNY";
    vi.mocked(api.getUsSnapshot).mockResolvedValue(value); render(<UsAssetWorkbench/>);
    expect(await screen.findByRole("heading", {name: "返回身份或币种不一致"})).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull(); expect(screen.queryByRole("button", {name: /加入美股自选|移出美股自选/})).toBeNull();
  });
  it("日K局部失败不隐藏已核报价；不以单个收盘点伪造日K", async () => {
    const value = fixture(); value.daily = {...value.daily, state: "unavailable", reason: "来源未返回历史", points: []}; vi.mocked(api.getUsSnapshot).mockResolvedValue(value);
    render(<UsAssetWorkbench/>); await screen.findByRole("heading", {name: "苹果"}); expect(screen.getByText(/日K暂不可用/)).toBeTruthy();
    expect(screen.getByText("270.00")).toBeTruthy(); expect(screen.queryByRole("img")).toBeNull();
  });
  it("日K返回另一只股票时只拒绝图表，不改用错股序列", async () => {
    const value = fixture(); value.daily.source_symbol = "NVDA.OQ"; vi.mocked(api.getUsSnapshot).mockResolvedValue(value);
    render(<UsAssetWorkbench/>); await screen.findByRole("heading", {name: "苹果"});
    expect(screen.getByRole("alert").textContent).toContain("日K身份或价格口径不一致"); expect(screen.queryByRole("img")).toBeNull(); expect(screen.getByText("270.00")).toBeTruthy();
  });
  it("来源未核时段的报价不冒充盘后；ETF来源身份可展示但没有交易入口", async () => {
    state.params = new URLSearchParams("asset=us_equity&symbol=SPY.AM"); const value = fixture("SPY.AM"); value.instrument.name = "标普ETF"; value.instrument.security_type = "etf"; value.regular.observation = "session_unverified";
    vi.mocked(api.getUsSnapshot).mockResolvedValue(value); render(<UsAssetWorkbench/>);
    expect(await screen.findByText("来源报价 · 时段待核")).toBeTruthy(); expect(screen.getByText(/SPY.AM · USD · ETF/)).toBeTruthy();
    expect(screen.queryByText("盘后报价")).toBeNull(); expect(screen.queryByRole("button", {name: /买入|卖出|记录持有/})).toBeNull();
  });
  it("自选读取失败不报零只或伪造空记录，已核行情仍可查看", async () => {
    vi.mocked(api.getUsAssetRecords).mockRejectedValue(new Error("读取失败")); render(<UsAssetWorkbench/>);
    await screen.findByRole("heading", {name: "苹果"}); const list = screen.getByRole("region", {name: "美股自选列表"});
    expect(within(list).getByText("读取失败")).toBeTruthy(); expect(within(list).queryByText("0 只")).toBeNull(); expect(within(list).queryByText(/还没有美股自选/)).toBeNull();
    expect(screen.getByRole("img")).toBeTruthy(); expect(screen.getByRole("button", {name: "加入美股自选"}).hasAttribute("disabled")).toBe(true);
  });
  it("能力关闭只保留原自选，不发起新行情或隐藏记录", async () => {
    vi.mocked(getAssetCapabilities).mockResolvedValue({items: [{...capabilities.items[0], state: "blocked", reason: "来源停用"}]}); render(<UsAssetWorkbench/>);
    expect(await screen.findByRole("heading", {name: "美股来源暂不可用"})).toBeTruthy(); expect(screen.getByText("保存苹果")).toBeTruthy(); expect(api.getUsSnapshot).not.toHaveBeenCalled();
  });
  it("美股自选点击携带完整code，不展示矛盾记录为已确认来源名称", async () => {
    const bad = {...record(), instrument_id: "fund:OF:040046:CNY", source_symbol: "040046.OF", currency: "CNY", name: "旧错域标签"};
    vi.mocked(api.getUsAssetRecords).mockResolvedValue({items: [record(), bad]}); state.params = new URLSearchParams("asset=us_equity"); render(<UsAssetWorkbench/>);
    const list = await screen.findByRole("region", {name: "美股自选列表"}); await within(list).findByText("保存苹果");
    fireEvent.click(within(list).getByRole("button", {name: /保存苹果.*AAPL.OQ/}));
    expect(state.replace).toHaveBeenCalledWith("/workbench?mode=watch&asset=us_equity&symbol=AAPL.OQ", {scroll: false});
    expect(within(list).getByRole("button", {name: /旧错域标签/}).hasAttribute("disabled")).toBe(true); expect(api.getUsSnapshot).not.toHaveBeenCalled();
  });
  it("保存与移除只操作自选CAS，不构造持有，冲突保留原状态", async () => {
    vi.mocked(api.getUsAssetRecords).mockResolvedValue({items: []}); const view = render(<UsAssetWorkbench/>);
    const add = await screen.findByRole("button", {name: "加入美股自选"}); await waitFor(() => expect(add.hasAttribute("disabled")).toBe(false)); fireEvent.click(add);
    await waitFor(() => expect(api.saveUsWatchRecord).toHaveBeenCalledWith(fixture().instrument, 0, undefined));
    view.unmount(); vi.mocked(api.getUsAssetRecords).mockResolvedValue({items: [record()]}); vi.mocked(api.deleteUsWatchRecord).mockRejectedValue(new ApiError(409, "旧版本", "conflict"));
    render(<UsAssetWorkbench/>); const remove = await screen.findByRole("button", {name: "移出美股自选"}); await waitFor(() => expect(remove.hasAttribute("disabled")).toBe(false)); fireEvent.click(remove);
    expect(await screen.findByText(/自选已有新版本/)).toBeTruthy(); expect(api.deleteUsWatchRecord).toHaveBeenCalledWith(record().instrument_id, 3);
    expect(screen.getByRole("button", {name: "移出美股自选"})).toBeTruthy();
  });
  it("保存和移除后从同scope重新读取，列表、按钮和版本随持久事实更新", async () => {
    let rows: UsAssetRecord[] = [];
    vi.mocked(api.getUsAssetRecords).mockImplementation(async () => ({items: rows.map(row => ({...row}))}));
    vi.mocked(api.saveUsWatchRecord).mockImplementation(async () => {const value = {...record(), name: "苹果", version: 1}; rows = [value]; return value;});
    vi.mocked(api.deleteUsWatchRecord).mockImplementation(async () => {rows = [];});
    render(<UsAssetWorkbench/>); const add = await screen.findByRole("button", {name: "加入美股自选"}); await waitFor(() => expect(add.hasAttribute("disabled")).toBe(false)); fireEvent.click(add);
    const remove = await screen.findByRole("button", {name: "移出美股自选"});
    expect(within(screen.getByRole("region", {name: "美股自选列表"})).getByText("苹果")).toBeTruthy(); fireEvent.click(remove);
    expect(await screen.findByRole("button", {name: "加入美股自选"})).toBeTruthy();
    expect(api.deleteUsWatchRecord).toHaveBeenCalledWith(record().instrument_id, 1);
    expect(within(screen.getByRole("region", {name: "美股自选列表"})).getByText(/还没有美股自选/)).toBeTruthy();
    expect(api.getUsSnapshot).toHaveBeenCalledTimes(1);
  });
  it("旧美股记录出现持有字段时不由仅自选功能清除或覆盖", async () => {
    vi.mocked(api.getUsAssetRecords).mockResolvedValue({items: [{...record(), units: 2, cost_total: 20}]}); render(<UsAssetWorkbench/>);
    expect(await screen.findByText(/旧记录含未支持的持有字段/)).toBeTruthy(); const button = screen.getByRole("button", {name: "移出美股自选"});
    expect(button.hasAttribute("disabled")).toBe(true); fireEvent.click(button); expect(api.deleteUsWatchRecord).not.toHaveBeenCalled(); expect(api.saveUsWatchRecord).not.toHaveBeenCalled();
  });
  it("报价陈旧明确标注；刷新失败仍保留原源钟并可重试", async () => {
    const value = fixture(); value.regular.observation = "stale"; vi.mocked(api.getUsSnapshot).mockResolvedValueOnce(value).mockRejectedValue(new Error("源超时"));
    render(<UsAssetWorkbench/>); expect(await screen.findByText("常规时段历史报价 · 已过期")).toBeTruthy(); fireEvent.click(screen.getByRole("button", {name: "刷新资料"}));
    expect(await screen.findByText(/刷新失败，以下保留上次读取/)).toBeTruthy(); expect(screen.getByText(/2026-10-29 16:00:00（纽约）/)).toBeTruthy(); expect(screen.getByRole("img")).toBeTruthy();
  });
});

describe("美股原始日K的本地交互", () => {
  const points: UsDailyPoint[] = Array.from({length: 120}, (_, index) => ({date: new Date(Date.UTC(2026, 3, index + 1)).toISOString().slice(0, 10), open: 100 + index, high: 103 + index, low: 99 + index, close: 102 + index, volume_shares: 1000 + index}));
  it("20/60/120窗口与日期游标只使用已读历史，不发额外请求", () => {
    render(<UsDailyChart points={points} symbol="AAPL.OQ"/>); const slider = screen.getByRole("slider", {name: "选择美股日K日期"});
    expect(slider.getAttribute("max")).toBe("59"); fireEvent.click(screen.getByRole("button", {name: "近20根"})); expect(slider.getAttribute("max")).toBe("19");
    fireEvent.change(slider, {target: {value: "0"}}); expect(slider.getAttribute("aria-valuetext")).toContain(points[100].date);
    fireEvent.click(screen.getByRole("button", {name: "近120根"})); fireEvent.change(slider, {target: {value: "0"}}); expect(slider.getAttribute("aria-valuetext")).toContain(points[0].date);
    expect(api.getUsSnapshot).not.toHaveBeenCalled(); expect(screen.queryByText(/\d+ 手/)).toBeNull();
  });
  it.each([{width: 1000, height: 220}, {width: 356, height: 105.265625}])("宽窄实际SVG比例下指针日期无偏移：%j", rect => {
    render(<UsDailyChart points={points.slice(0, 3)} symbol="AAPL.OQ"/>); const svg = screen.getByRole("img") as unknown as SVGSVGElement;
    vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({...rect, x: 20, y: 30, left: 20, top: 30, right: 20 + rect.width, bottom: 30 + rect.height, toJSON: () => ({})});
    const wickX = [...svg.querySelectorAll("g")].filter(node => node.querySelector("rect")).map(node => Number(node.querySelector("line")!.getAttribute("x1")));
    const scale = Math.min(rect.width / 744, rect.height / 220), inset = (rect.width - 744 * scale) / 2;
    for (const index of [0, 1, 2]) {const event = createEvent.pointerMove(svg); Object.defineProperty(event, "clientX", {value: 20 + inset + wickX[index] * scale}); fireEvent(svg, event); expect(screen.getByRole("slider").getAttribute("aria-valuetext")).toContain(points[index].date);}
  });
  it.each(["duplicate", "wrong-price", "nan"])("无效历史拒绝绘制而非静默补点：%s", kind => {
    const invalid = points.slice(0, 3).map(row => ({...row})); if (kind === "duplicate") invalid[1].date = invalid[0].date; else if (kind === "wrong-price") invalid[1].high = 1; else invalid[1].volume_shares = NaN;
    render(<UsDailyChart points={invalid} symbol="AAPL.OQ"/>); expect(screen.getByRole("alert").textContent).toContain("停止绘制"); expect(screen.queryByRole("img")).toBeNull();
  });
});
