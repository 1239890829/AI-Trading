import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {act, cleanup, createEvent, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import WorkbenchPage from "@/app/workbench/page";
import {FundDetail} from "@/components/fund-detail";
import {FundRecordEditor} from "@/components/fund-record-editor";
import {FundNavChart} from "@/components/fund-nav-chart";
import {ApiError} from "@/lib/api/client";
import * as api from "@/lib/api/assets";
import type {AssetRecord, FundSnapshot} from "@/lib/api/assets";

const state = vi.hoisted(() => ({params: new URLSearchParams("asset=fund&symbol=040046.OF"), push: vi.fn(), replace: vi.fn(), quotes: vi.fn(), positions: vi.fn()}));
vi.mock("next/navigation", () => ({useSearchParams: () => state.params, useRouter: () => ({push: state.push, replace: state.replace}), usePathname: () => "/workbench"}));
vi.mock("@/hooks/use-quote-stream", () => ({useQuoteStream: state.quotes, STREAM_STATUS_LABEL: {}}));
vi.mock("@/hooks/use-real-positions", () => ({useRealPositions: state.positions}));
vi.mock("@/lib/api/assets", () => ({getAssetCapabilities: vi.fn(), getAssetRecords: vi.fn(), getFundSnapshot: vi.fn(), saveAssetRecord: vi.fn(), deleteAssetRecord: vi.fn()}));

function fixture(symbol = "040046.OF"): FundSnapshot {
  const section = {state: "ready" as const, reason: null, missing: [], source: "ths_official" as const, evidence: []};
  const point = {nav_date: "2026-10-28", unit_nav: 1.2, adj_nav: 1.4};
  return {instrument: {instrument_id: `fund:OF:${symbol.slice(0, 6)}:CNY`, asset_type: "fund", fund_type: "fund-otc", market: "OF", symbol: symbol.slice(0, 6), source_symbol: symbol, currency: "CNY", name: symbol === "040046.OF" ? "测试美股联接" : "另一只基金", source: "ths_official"},
    state: "partial", received_at: "2026-10-30T10:00:00+08:00",
    nav: {...section, currency: "CNY", points: [{nav_date: "2026-10-27", unit_nav: 1.1, adj_nav: 1.3}, point], latest: point, published_at: null, publication_time_known: false, basis: "official_nav"},
    holdings: {...section, items: [{source_symbol: "AAPL.O", name: "披露公司", asset_type: "us_equity", weight_pct: 12, report_date: "2026-09-30", published_at: "2026-10-28T09:00:00+08:00", modified_at: null}], disclosed_weight_pct: 12, report_date: "2026-09-30", current_positions: false},
    lookthrough: {...section, source: "system_calculation", items: [{source_symbol: "AAPL.O", name: "披露公司", weight_pct: 12, via: ["040046.OF"], report_date: "2026-09-30"}], covered_weight_pct: 12, uncovered_weight_pct: 88, scope: "disclosed_equity_one_fund_layer", current_positions: false},
    terms: {...section, manager_name: "测试管理人", fees: [{label: "管理费", value: "0.5%", condition: "年费率"}], dealing_rules: [{label: "赎回", value: "按公告", condition: null}], investment_scope: "境外市场，测试文件", benchmark: null, subscription_status: null},
    research: {...section, state: "partial", source: "system_calculation", eligible: false, checks: [{label: "正式基准", value: "缺少", condition: null}], missing: ["正式基准"], disclaimer: "仅供研究，不构成买卖建议"},
    estimate: {...section, state: "unavailable", source: "system_calculation", value: null, currency: "CNY", basis: "estimate"},
  };
}
const record = (): AssetRecord => ({instrument_id: "fund:OF:040046:CNY", asset_type: "fund", source_symbol: "040046.OF", name: "我的标签", currency: "CNY", is_watched: true, units: 100, cost_total: 110, note: "原备注", version: 3, updated_at: "2026-10-30T09:00:00+08:00", basis: "manual_not_broker_verified"});
const capabilities = {items: [
  {asset_type: "fund" as const, state: "available" as const, reason: "正式源", capabilities: ["nav"], unavailable: ["estimate"], source: "ths_official", trading: false as const},
  {asset_type: "us_equity" as const, state: "blocked" as const, reason: "缺少合规、可核时效的美股行情源", capabilities: [], unavailable: ["quote"], source: null, trading: false as const},
]};

beforeEach(() => {
  vi.resetAllMocks(); state.params = new URLSearchParams("asset=fund&symbol=040046.OF");
  vi.mocked(api.getAssetCapabilities).mockResolvedValue(capabilities);
  vi.mocked(api.getAssetRecords).mockResolvedValue({items: [record()]});
  vi.mocked(api.getFundSnapshot).mockImplementation(async symbol => fixture(symbol));
  vi.mocked(api.saveAssetRecord).mockResolvedValue(record());
  vi.mocked(api.deleteAssetRecord).mockResolvedValue();
});
afterEach(() => {cleanup(); vi.useRealTimers();});

describe("统一工作台资产边界", () => {
  it("基金挂原工作台外壳，按完整身份读取，完全不挂 A 股行情和持仓消费者", async () => {
    const {container} = render(<WorkbenchPage/>);
    expect(await screen.findByText("测试美股联接")).toBeTruthy();
    expect(container.querySelectorAll('main[data-workspace="workbench"]')).toHaveLength(1);
    expect(screen.getByRole("navigation", {name: "资产类型"}).querySelector('[aria-current="page"]')?.textContent).toBe("基金");
    expect(api.getFundSnapshot).toHaveBeenCalledWith("040046.OF", "year", {signal: expect.any(AbortSignal)});
    expect(state.quotes).not.toHaveBeenCalled(); expect(state.positions).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", {name: "模拟买入"})).toBeNull();
  });
  it("美股只显示能力阻断，不请求基金、记录或 A 股行情", async () => {
    state.params = new URLSearchParams("asset=us_equity&symbol=AAPL"); render(<WorkbenchPage/>);
    expect(await screen.findByText("美股来源待核验")).toBeTruthy();
    expect(screen.getByText("缺少合规、可核时效的美股行情源")).toBeTruthy();
    expect(api.getFundSnapshot).not.toHaveBeenCalled(); expect(api.getAssetRecords).not.toHaveBeenCalled(); expect(state.quotes).not.toHaveBeenCalled();
  });
  it.each(["asset=fund&symbol=040046", "asset=unknown&symbol=040046.OF", "symbol=040046.OF"])("无效链接不向A股或基金取错对象：%s", async query => {
    state.params = new URLSearchParams(query); render(<WorkbenchPage/>); await act(async () => {});
    expect(api.getFundSnapshot).not.toHaveBeenCalled(); expect(state.quotes).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", {name: /身份无法确认/})).toBeTruthy();
  });
  it("换基金时旧慢回包不能挂到新基金，旧请求被取消", async () => {
    let resolveOld!: (data: FundSnapshot) => void;
    vi.mocked(api.getFundSnapshot).mockImplementation(symbol => symbol === "040046.OF" ? new Promise(resolve => {resolveOld = resolve;}) : Promise.resolve(fixture(symbol)));
    const view = render(<WorkbenchPage/>); await waitFor(() => expect(api.getFundSnapshot).toHaveBeenCalledTimes(1));
    const oldSignal = vi.mocked(api.getFundSnapshot).mock.calls[0][2]!.signal!;
    state.params = new URLSearchParams("asset=fund&symbol=040047.OF"); view.rerender(<WorkbenchPage/>);
    expect(await screen.findByText("另一只基金")).toBeTruthy(); expect(oldSignal.aborted).toBe(true);
    await act(async () => resolveOld(fixture()));
    expect(screen.queryByText("测试美股联接")).toBeNull();
  });
  it("返回另一基金身份时拒绝展示与写入", async () => {
    vi.mocked(api.getFundSnapshot).mockResolvedValue(fixture("040047.OF")); render(<WorkbenchPage/>);
    expect(await screen.findByText("返回身份不一致")).toBeTruthy(); expect(screen.queryByText("另一只基金")).toBeNull();
    expect(screen.queryByRole("button", {name: "记录持有"})).toBeNull();
  });
  it("空自选是真实空态，搜索提示不创造本人持仓", async () => {
    state.params = new URLSearchParams("asset=fund"); vi.mocked(api.getAssetRecords).mockResolvedValue({items: []}); render(<WorkbenchPage/>);
    expect(await screen.findByText("选择一只基金")).toBeTruthy(); expect(screen.getByText(/还没有基金自选/)).toBeTruthy();
    expect(api.saveAssetRecord).not.toHaveBeenCalled(); expect(api.getFundSnapshot).not.toHaveBeenCalled();
  });
});

describe("基金真实阅读交互", () => {
  function detail(snapshot = fixture()) {return render(<FundDetail snapshot={snapshot} range="year" setRange={vi.fn()} record={record()} recordPending={false} onRefreshRecords={vi.fn()} refreshing={false} onRefresh={vi.fn()}/>);}
  it("净值日期游标与口径可切换，不把复权值当单位净值", () => {
    detail(); const slider = screen.getByRole("slider", {name: "选择净值日期"});
    fireEvent.change(slider, {target: {value: "0"}}); expect(screen.getByText("1.1000")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "复权净值"})); expect(screen.getByText("1.3000")).toBeTruthy();
    expect(slider.getAttribute("aria-valuetext")).toContain("2026-10-27，复权净值1.3 CNY");
  });
  it("穿透保留 88% 未知，披露/费用/研究切换只读现有snapshot，零重复请求", () => {
    detail(); fireEvent.click(screen.getByRole("button", {name: "披露持仓"}));
    const row = screen.getByText("披露公司").closest("details")!; fireEvent.click(row.querySelector("summary")!);
    expect(within(row).getByText("2026-10-28 09:00:00（北京时间）")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "一层穿透"}));
    expect(screen.getByText("未覆盖 88.00%")).toBeTruthy(); expect(screen.getByRole("meter").getAttribute("value")).toBe("12");
    fireEvent.click(screen.getByRole("button", {name: "费用与申赎"})); expect(screen.getByText("0.5%")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "研究核对"})); expect(screen.getByText("研究候选 · 待补证据")).toBeTruthy();
    expect(api.getFundSnapshot).not.toHaveBeenCalled(); expect(api.saveAssetRecord).not.toHaveBeenCalled();
  });
  it("缺净值时不画曲线，也不回退成本计算市值", () => {
    const data = fixture(); data.nav = {...data.nav, state: "unavailable", points: [], latest: null}; detail(data);
    expect(screen.queryByRole("img", {name: /净值曲线/})).toBeNull(); expect(screen.getByText(/缺少官方单位净值/)).toBeTruthy();
    expect(screen.getByText("按净值记录市值").nextElementSibling?.textContent).toContain("--");
  });
  it("只有复权净值时须明确切换口径，不能替代单位净值算持有市值", () => {
    const data = fixture(); data.nav.points = data.nav.points.map(row => ({...row, unit_nav: null})); data.nav.latest = data.nav.points.at(-1)!;
    detail(data); expect(screen.queryByRole("img", {name: /净值曲线/})).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "查看复权净值"}));
    expect(screen.getByRole("img", {name: /复权净值曲线/})).toBeTruthy();
    expect(screen.getByText("按净值记录市值").nextElementSibling?.textContent).toContain("--");
  });
  it("UTC披露时刻明确换算北京时间，净值所属日保持原日期", () => {
    const data = fixture(); data.holdings.items[0].published_at = "2026-10-27T23:30:00+00:00";
    data.nav.published_at = "2026-10-29T00:30:00Z"; data.nav.publication_time_known = true;
    detail(data); expect(screen.getByText(/净值日 2026-10-28 · 发布时刻 2026-10-29 08:30:00（北京时间）/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "披露持仓"})); expect(screen.getByText("2026-10-28 07:30:00（北京时间）")).toBeTruthy();
  });
});

describe("净值图指针与SVG实际比例", () => {
  const points = Array.from({length: 100}, (_, index) => ({nav_date: new Date(Date.UTC(2026, 5, 1 + index)).toISOString().slice(0, 10), unit_nav: 1 + index / 100, adj_nav: null}));
  function pointAt(svg: SVGSVGElement, clientX: number) {
    const event = createEvent.pointerMove(svg);
    Object.defineProperty(event, "clientX", {value: clientX});
    fireEvent(svg, event);
  }
  it.each([{width: 1200, height: 250}, {width: 360, height: 250}])("按$width×$height实际meet比例读取首中末点，留白和越界夹到最近端点", ({width, height}) => {
    render(<FundNavChart points={points} currency="CNY"/>);
    const svg = screen.getByRole("img") as unknown as SVGSVGElement;
    const left = 100, scale = Math.min(width / 744, height / 220), inset = (width - 744 * scale) / 2;
    vi.spyOn(svg, "getBoundingClientRect").mockReturnValue(new DOMRect(left, 30, width, height));
    for (const index of [0, 50, 99]) {
      pointAt(svg, left + inset + (56 + index / 99 * 664) * scale);
      expect(screen.getByRole("slider").getAttribute("aria-valuetext")).toContain(points[index].nav_date);
      expect((screen.getByRole("slider") as HTMLInputElement).value).toBe(String(index));
    }
    pointAt(svg, left - 20); expect((screen.getByRole("slider") as HTMLInputElement).value).toBe("0");
    pointAt(svg, left + width + 20); expect((screen.getByRole("slider") as HTMLInputElement).value).toBe("99");
  });
  it("隐藏或尚未布局的零尺寸图表不产生无效游标", () => {
    render(<FundNavChart points={points} currency="CNY"/>);
    const svg = screen.getByRole("img") as unknown as SVGSVGElement;
    vi.spyOn(svg, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 0, 0));
    pointAt(svg, 100);
    expect((screen.getByRole("slider") as HTMLInputElement).value).toBe("99");
    expect(screen.getByRole("slider").getAttribute("aria-valuetext")).toContain(points[99].nav_date);
  });
});

describe("持有记录版本与输入保护", () => {
  it("仅自选且无持有的记录移出时带版本删除，不提交不合法的空记录", async () => {
    const data = fixture(), refresh = vi.fn();
    render(<FundRecordEditor instrument={data.instrument} record={{...record(), units: null, cost_total: null}} latest={null} loading={false} onRefresh={refresh}/>);
    fireEvent.click(screen.getByRole("button", {name: "移出自选"}));
    expect(await screen.findByText("已移出自选")).toBeTruthy();
    expect(api.deleteAssetRecord).toHaveBeenCalledWith(record().instrument_id, 3);
    expect(api.saveAssetRecord).not.toHaveBeenCalled(); expect(refresh).toHaveBeenCalledTimes(1);
  });
  it("自选移除遇到版本冲突保留原记录，不重试或声称已删除", async () => {
    vi.mocked(api.deleteAssetRecord).mockRejectedValue(new ApiError(409, "记录已变化", "version_conflict"));
    const data = fixture(), refresh = vi.fn();
    render(<FundRecordEditor instrument={data.instrument} record={{...record(), units: null, cost_total: null}} latest={null} loading={false} onRefresh={refresh}/>);
    fireEvent.click(screen.getByRole("button", {name: "移出自选"}));
    expect(await screen.findByText(/记录已有新版本/)).toBeTruthy();
    expect(api.deleteAssetRecord).toHaveBeenCalledTimes(1); expect(refresh).not.toHaveBeenCalled();
    expect(screen.queryByText("已移出自选")).toBeNull(); expect(screen.getByText("备注：原备注")).toBeTruthy();
  });
  it("币种来自份额；新建 expected_version=0，保存原值且不请求模拟端点", async () => {
    const data = fixture(); data.instrument = {...data.instrument, currency: "USD", instrument_id: "fund:OF:040046:USD"};
    const refresh = vi.fn(); render(<FundRecordEditor instrument={data.instrument} latest={null} loading={false} onRefresh={refresh}/>);
    fireEvent.click(screen.getByRole("button", {name: "记录持有"}));
    fireEvent.change(screen.getByLabelText("持有份额"), {target: {value: "12.5"}});
    fireEvent.change(screen.getByLabelText("总成本（USD）"), {target: {value: "200"}});
    fireEvent.click(screen.getByRole("button", {name: "保存持有记录"}));
    await waitFor(() => expect(api.saveAssetRecord).toHaveBeenCalledWith("fund:OF:040046:USD", expect.objectContaining({units: 12.5, cost_total: 200, expected_version: 0})));
    expect(await screen.findByText("已保存手工持有记录")).toBeTruthy(); expect(refresh).toHaveBeenCalled();
  });
  it("409 保留输入和原持有，不借新版本无声重试；移出自选保留份额成本", async () => {
    vi.mocked(api.saveAssetRecord).mockRejectedValue(new ApiError(409, "版本冲突", "version_conflict"));
    const data = fixture(); render(<FundRecordEditor instrument={data.instrument} record={record()} latest={data.nav.latest} loading={false} onRefresh={vi.fn()}/>);
    fireEvent.click(screen.getByRole("button", {name: "编辑持有"}));
    const input = screen.getByLabelText("持有份额"); fireEvent.change(input, {target: {value: "180"}}); input.focus();
    fireEvent.click(screen.getByRole("button", {name: "保存持有记录"}));
    expect(await screen.findByText(/记录已被其他操作更新/)).toBeTruthy(); expect((input as HTMLInputElement).value).toBe("180");
    expect(api.saveAssetRecord).toHaveBeenCalledTimes(1); expect(api.saveAssetRecord).toHaveBeenLastCalledWith(record().instrument_id, expect.objectContaining({expected_version: 3}));
    fireEvent.click(screen.getByRole("button", {name: "取消编辑"}));
    vi.mocked(api.saveAssetRecord).mockResolvedValue({...record(), is_watched: false});
    fireEvent.click(screen.getByRole("button", {name: "移出自选"}));
    await waitFor(() => expect(api.saveAssetRecord).toHaveBeenLastCalledWith(record().instrument_id, expect.objectContaining({is_watched: false, units: 100, cost_total: 110, expected_version: 3})));
  });
  it("删除需确认并携带当前版本，失败不伪造已删除", async () => {
    vi.mocked(api.deleteAssetRecord).mockRejectedValue(new ApiError(409, "记录已变化", "version_conflict"));
    const data = fixture(); render(<FundRecordEditor instrument={data.instrument} record={record()} latest={null} loading={false} onRefresh={vi.fn()}/>);
    fireEvent.click(screen.getByRole("button", {name: "删除记录"})); expect(api.deleteAssetRecord).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name: "确认删除记录"})); expect(await screen.findByText(/删除失败/)).toBeTruthy();
    expect(api.deleteAssetRecord).toHaveBeenCalledWith(record().instrument_id, 3); expect(screen.queryByText("手工记录已删除")).toBeNull();
  });
});
