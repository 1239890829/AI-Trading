import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import {FundReferencePanel} from "@/components/fund-reference-panel";
import type {FundPublicReference} from "@/lib/api/assets";

function reference(): FundPublicReference {
  return {
    source_symbol: "040046.OF", state: "ready", scope: "reported_top_holdings_reference", source: "sina_fund",
    warning: "源方风险提示：上一季度前十大重仓存在滞后，不能作为基金单位净值走势参考。",
    scope_note: "两种口径分别来自源方，不互算、不择优。",
    source_as_of: "2026-10-30T05:50:00Z", trading_date: "2026-10-30", is_current_day: true, age_seconds: 60,
    disclosed_nav: 8.1932, disclosed_nav_date: "2026-10-28", heavy_stock_us: true,
    curves: [
      {key: "nav_pct", label: "来源口径一", latest_pct: -.3031, points: []},
      {key: "nav2_pct", label: "来源口径二", latest_pct: -2.1183, points: []},
    ],
    missing: [], evidence: [{endpoint: "https://stock.finance.sina.com.cn/fixed-reference", params: {symbol: "040046"},
      received_at: "2026-10-30T05:51:00Z", payload_sha256: "a".repeat(64), receipt_id: "source-receipt", raw_available: true}],
    estimated_fund_nav: null, estimated_fund_percent: null,
  };
}

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

describe("基金来源重仓参考", () => {
  it("分别展示两种来源口径，交互只消费已有快照，不发新请求或改原值", () => {
    const data = reference(), original = structuredClone(data);
    render(<FundReferencePanel reference={data} sourceSymbol="040046.OF"/>);
    expect(screen.getByRole("heading", {name: "今日重仓参考"})).toBeTruthy();
    expect(screen.getByText("-0.30%")).toBeTruthy();
    expect(screen.getByRole("button", {name: "来源口径一"}).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", {name: "来源口径二"}));
    expect(screen.getByText("-2.12%")).toBeTruthy();
    expect(screen.queryByText("-0.30%")).toBeNull();
    expect(screen.getByRole("button", {name: "来源口径二"}).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", {name: "来源口径一"}));
    expect(screen.getByText("-0.30%")).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled(); expect(data).toEqual(original);
  });

  it("明显区分参考、整只基金估值和较早公布净值，保留风险提示与回执", () => {
    render(<FundReferencePanel reference={reference()} sourceSymbol="040046.OF"/>);
    const panel = screen.getByRole("region", {name: "基金重仓参考"});
    expect(within(panel).getByText(/不能代表整只基金当日涨跌或净值/)).toBeTruthy();
    expect(within(panel).getByText(/整只基金当日估值尚未获得；不以参考值冒充/)).toBeTruthy();
    expect(within(panel).getByText(/新浪基金 · 10\/30 13:50（北京时间）/)).toBeTruthy();
    fireEvent.click(within(panel).getByText("参考范围与来源"));
    expect(within(panel).getByText(/来源公布净值 8.1932，净值日 2026-10-28；不升级为今日净值/)).toBeTruthy();
    expect(within(panel).getByText(/上一季度前十大重仓存在滞后/)).toBeTruthy();
    expect(within(panel).getByText(/source-receipt/).textContent).toContain("10/30 13:51");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("陈旧参考保留原日期与真实数值，不能换成今日或接收时点", () => {
    const data = reference();
    data.state = "stale"; data.is_current_day = false;
    data.source_as_of = "2026-10-29T05:50:00Z"; data.trading_date = "2026-10-29";
    render(<FundReferencePanel reference={data} sourceSymbol="040046.OF"/>);
    expect(screen.getByRole("heading", {name: "重仓参考"})).toBeTruthy();
    expect(screen.queryByRole("heading", {name: "今日重仓参考"})).toBeNull();
    expect(screen.getByText(/10\/29 13:50（北京时间） · 陈旧参考/)).toBeTruthy();
    expect(screen.getByText("-0.30%")).toBeTruthy();
  });

  it("源钟无时区不能由宿主时区或接收钟补成看似已知", () => {
    const data = reference(); data.source_as_of = "2026-10-30T13:50:00";
    render(<FundReferencePanel reference={data} sourceSymbol="040046.OF"/>);
    expect(screen.getByText(/新浪基金 · 时点未提供（北京时间）/)).toBeTruthy();
    expect(screen.queryByText(/新浪基金 · 10\/30/)).toBeNull();
  });

  it("另一份额的参考和回执完全隔离，不显示成当前基金的有效资料", () => {
    const data = reference(); data.source_symbol = "270042.OF";
    render(<FundReferencePanel reference={data} sourceSymbol="040046.OF"/>);
    expect(screen.getByText("暂无有效参考")).toBeTruthy();
    expect(screen.queryByText("-0.30%")).toBeNull();
    expect(screen.queryByRole("button", {name: "来源口径二"})).toBeNull();
    expect(screen.queryByText(/source-receipt/)).toBeNull();
    expect(screen.getByText(/公布净值仍可独立查看/)).toBeTruthy();
  });

  it.each([null, undefined])("缺参考%s保留明确空态，不能用0%%替代", data => {
    render(<FundReferencePanel reference={data} sourceSymbol="040046.OF"/>);
    expect(screen.getByText("暂无有效参考")).toBeTruthy();
    expect(screen.queryByText("0.00%")).toBeNull();
    expect(screen.getByText(/整只基金当日估值尚未获得/)).toBeTruthy();
  });

  it("局部缺百分比不阻断另一条有效曲线，非有限数也不能画成数值", () => {
    const data = reference(); data.state = "partial";
    data.curves[0].latest_pct = Number.NaN; data.missing = ["nav_pct_ratio_conflict"];
    render(<FundReferencePanel reference={data} sourceSymbol="040046.OF"/>);
    expect(screen.getByText("暂无有效参考")).toBeTruthy();
    expect(screen.queryByText("NaN%")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "来源口径二"}));
    expect(screen.getByText("-2.12%")).toBeTruthy();
    expect(screen.getByText("nav_pct_ratio_conflict")).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });
});
