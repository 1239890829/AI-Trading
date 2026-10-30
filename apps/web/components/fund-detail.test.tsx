import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {FundDetail} from "@/components/fund-detail";
import type {AssetRecord, FundPublicReference, FundSnapshot} from "@/lib/api/assets";

function snapshot(): FundSnapshot {
  const section = {state: "ready" as const, reason: null, missing: [], source: "ths_official" as const, evidence: []};
  const point = {nav_date: "2026-10-28", unit_nav: 8.1932, adj_nav: 8.1932};
  return {
    instrument: {instrument_id: "fund:OF:040046:CNY", asset_type: "fund", fund_type: "fund-otc", market: "OF",
      symbol: "040046", source_symbol: "040046.OF", currency: "CNY", name: "原标的目录名称", source: "ths_official"},
    state: "partial", received_at: "2026-10-30T06:00:00Z",
    nav: {...section, currency: "CNY", points: [point], latest: point, published_at: null, publication_time_known: false, basis: "official_nav"},
    holdings: {...section, state: "unavailable", items: [], disclosed_weight_pct: null, report_date: null, current_positions: false},
    lookthrough: {...section, state: "unavailable", source: "system_calculation", items: [], covered_weight_pct: 0,
      uncovered_weight_pct: 100, scope: "disclosed_equity_one_fund_layer", current_positions: false},
    terms: {...section, source: "mixed_public_sources", manager_name: "华安基金", fees: [{label: "管理费", value: "0.5%", condition: "年费率"}],
      dealing_rules: [{label: "赎回", value: "按基金公告", condition: null}], investment_scope: "目标ETF份额与指数成份股。",
      benchmark: "纳斯达克100指数收益率（经汇率调整）95%", subscription_status: "暂停申购"},
    research: {...section, state: "partial", source: "system_calculation", eligible: false,
      reason: "公开产品说明不等于监管文件核验，不构成推荐或策略准入。",
      checks: [{label: "投资范围", value: "公开资料具备", condition: null}, {label: "申购状态", value: "暂停申购", condition: null}],
      disclaimer: "仅供研究，不构成买卖建议"},
    estimate: {...section, state: "unavailable", source: "system_calculation", value: null, currency: "CNY", basis: "estimate"},
    public_profile: {source_symbol: "040046.OF", state: "partial", document_scope: "public_product_description_not_verified_regulatory_document",
      recommendation: false, missing: [], conflicts: ["established_date"], evidence: [], fields: [
        {key: "name", label: "基金简称", value: "华安纳斯达克100ETF联接(QDII)A", source: "huaan_fund", source_url: "https://www.huaan.com.cn/funds/040046/index.shtml", source_as_of: null},
        {key: "full_name", label: "法定名称", value: "华安纳斯达克100交易型开放式指数证券投资基金联接基金(QDII)", source: "huaan_fund", source_url: "https://www.huaan.com.cn/funds/040046/index.shtml", source_as_of: null},
        {key: "currency", label: "份额币种", value: "CNY", source: "huaan_fund", source_url: "https://www.huaan.com.cn/funds/040046/index.shtml", source_as_of: null},
        {key: "established_date", label: "成立日期", value: "2022-12-09", source: "huaan_fund", source_url: "https://www.huaan.com.cn/funds/040046/index.shtml", source_as_of: null},
        {key: "fund_type", label: "基金类型", value: "指数型-海外股票", source: "eastmoney_fund", source_url: "https://fundf10.eastmoney.com/jbgk_040046.html", source_as_of: null},
        {key: "redemption_status", label: "赎回状态", value: "开放赎回", source: "huaan_fund", source_url: "https://www.huaan.com.cn/funds/040046/index.shtml", source_as_of: null},
      ]},
    reference: reference(),
  };
}

function reference(): FundPublicReference {
  return {source_symbol: "040046.OF", state: "ready", source: "sina_fund", scope: "reported_top_holdings_reference",
    warning: "上一季度重仓，不能作为基金单位净值走势参考。", scope_note: "上一季度前十大重仓参考，不是基金估值。",
    source_as_of: "2026-10-30T05:50:00Z", trading_date: "2026-10-30", is_current_day: true, age_seconds: 60,
    disclosed_nav: 8.1932, disclosed_nav_date: "2026-10-28", heavy_stock_us: true,
    curves: [{key: "nav_pct", label: "来源口径一", latest_pct: -0.3031, points: []},
      {key: "nav2_pct", label: "来源口径二", latest_pct: -2.1183, points: []}],
    evidence: [], missing: [], estimated_fund_nav: null, estimated_fund_percent: null};
}

function record(): AssetRecord {
  return {instrument_id: "fund:OF:040046:CNY", asset_type: "fund", source_symbol: "040046.OF", name: "本人原始记录名",
    currency: "CNY", is_watched: true, units: 100, cost_total: 800, note: null, version: 2,
    updated_at: "2026-10-29T10:00:00+08:00", basis: "manual_not_broker_verified"};
}

function detail(data = snapshot()) {
  const callbacks = {setRange: vi.fn(), onRefreshRecords: vi.fn(), onRefresh: vi.fn()};
  const view = render(<FundDetail snapshot={data} range="year" record={record()} recordPending={false} refreshing={false} {...callbacks}/>);
  return {...view, ...callbacks};
}

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

describe("基金详情实际消费", () => {
  it.each(["missing", "wrong_share", "unavailable", "stale"] as const)("参考%s不能阻断公布净值，持有市值仍只按官方单位净值", state => {
    const data = snapshot();
    if (state === "missing") data.reference = null;
    if (state === "wrong_share") data.reference!.source_symbol = "270042.OF";
    if (state === "unavailable") {data.reference!.state = "unavailable"; data.reference!.curves = [];}
    if (state === "stale") {data.reference!.state = "stale"; data.reference!.is_current_day = false; data.reference!.source_as_of = "2026-10-29T05:50:00Z";}
    detail(data);
    expect(screen.getByRole("img", {name: "单位净值曲线，2026-10-28至2026-10-28"})).toBeTruthy();
    expect(screen.getByRole("slider", {name: "选择净值日期"}).getAttribute("aria-valuetext")).toBe("2026-10-28，单位净值8.1932 CNY");
    expect(screen.getByText(/净值日 2026-10-28 · 发布时刻 未提供/)).toBeTruthy();
    expect(screen.getByText("按净值记录市值").nextElementSibling?.textContent).toContain("819.32");
    expect(screen.getByText(/不是实时估值/)).toBeTruthy();
    expect(screen.getByText(/整只基金当日估值尚未获得/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("来源名称、产品资料与冲突逐项显示，研究资料不能冒充监管核验或推荐", () => {
    const data = snapshot(), original = structuredClone(data); detail(data);
    expect(screen.getByRole("heading", {name: "华安纳斯达克100ETF联接(QDII)A"})).toBeTruthy();
    expect(screen.getByText(/标的目录原名：原标的目录名称/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "费用与申赎"}));
    expect(screen.getByText("华安纳斯达克100交易型开放式指数证券投资基金联接基金(QDII)")).toBeTruthy();
    expect(screen.getByText("开放赎回")).toBeTruthy();
    expect(screen.getByText("指数型-海外股票")).toBeTruthy();
    expect(screen.getByText("2022-12-09")).toBeTruthy();
    expect(screen.getAllByText("基金公司官网 · 源更新时点未提供").length).toBeGreaterThan(0);
    expect(screen.getByText("天天基金公开资料 · 源更新时点未提供")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("established_date");
    expect(screen.getByText(/同花顺与公开基金资料 · 逐字段核源/)).toBeTruthy();
    expect(screen.getByText("暂停申购")).toBeTruthy();
    expect(screen.getByText("纳斯达克100指数收益率（经汇率调整）95%")).toBeTruthy();
    fireEvent.click(screen.getByText("投资范围原文"));
    expect(screen.getByText("目标ETF份额与指数成份股。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "研究核对"}));
    expect(screen.getByText("公开资料具备")).toBeTruthy();
    expect(screen.getByText(/公开产品说明不等于监管文件核验，不构成推荐或策略准入/)).toBeTruthy();
    expect(screen.getByText("这是资料检查，不是买入资格或收益预测。")).toBeTruthy();
    expect(screen.queryByRole("button", {name: /买入|交易/})).toBeNull();
    expect(data).toEqual(original); expect(fetch).not.toHaveBeenCalled();
  });

  it("另一份额的产品资料不能改当前标题或混入费用页", () => {
    const data = snapshot(); data.public_profile!.source_symbol = "270042.OF";
    detail(data);
    expect(screen.getByRole("heading", {name: "原标的目录名称"})).toBeTruthy();
    expect(screen.queryByRole("heading", {name: "华安纳斯达克100ETF联接(QDII)A"})).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "费用与申赎"}));
    expect(screen.queryByText("2022-12-09")).toBeNull();
    expect(screen.queryByText("指数型-海外股票")).toBeNull();
    expect(screen.getByText("0.5%")).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("公开资料被隔离时说明原因，已读净值和原费用仍保留", () => {
    const data = snapshot();
    data.public_profile = {...data.public_profile!, state: "unavailable", fields: [],
      missing: ["fund_public_currency_mismatch"], conflicts: []};
    data.terms = {...data.terms, source: "ths_official", investment_scope: null, benchmark: null,
      subscription_status: null, missing: ["investment_scope", "benchmark", "subscription_status"]};
    detail(data);
    expect(screen.getByRole("heading", {name: "原标的目录名称"})).toBeTruthy();
    expect(screen.getByRole("slider").getAttribute("aria-valuetext")).toContain("单位净值8.1932 CNY");
    fireEvent.click(screen.getByRole("button", {name: "费用与申赎"}));
    expect(screen.getByText("公开资料未通过核对，未覆盖已读取的净值和费用。")).toBeTruthy();
    fireEvent.click(screen.getByText("资料缺项与隔离原因 · 1"));
    expect(screen.getByText("fund_public_currency_mismatch")).toBeTruthy();
    expect(screen.getByText("0.5%")).toBeTruthy();
    expect(screen.queryByText("开放赎回")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("净值缺失仍可独立查看公开产品资料和参考，不能拿参考造净值图或持有市值", () => {
    const data = snapshot();
    data.nav = {...data.nav, state: "unavailable", points: [], latest: null, missing: ["fund_source_timeout"]};
    detail(data);
    expect(screen.getByText("-0.30%")).toBeTruthy();
    expect(screen.queryByRole("img", {name: /净值曲线/})).toBeNull();
    expect(screen.getByText("按净值记录市值").nextElementSibling?.textContent).toContain("--");
    expect(screen.getByText(/缺少官方单位净值/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "费用与申赎"}));
    expect(screen.getByText("开放赎回")).toBeTruthy();
    expect(screen.getByText("暂停申购")).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("口径和资料页切换不调用刷新；只有显式刷新和区间选择交回上层", () => {
    const callbacks = detail();
    fireEvent.click(screen.getByRole("button", {name: "来源口径二"}));
    fireEvent.click(screen.getByRole("button", {name: "费用与申赎"}));
    fireEvent.click(screen.getByRole("button", {name: "研究核对"}));
    fireEvent.click(screen.getByRole("button", {name: "净值与记录"}));
    expect(callbacks.onRefresh).not.toHaveBeenCalled();
    expect(callbacks.setRange).not.toHaveBeenCalled();
    expect(callbacks.onRefreshRecords).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name: "近一月"}));
    expect(callbacks.setRange).toHaveBeenCalledExactlyOnceWith("month");
    fireEvent.click(screen.getByRole("button", {name: "刷新资料"}));
    expect(callbacks.onRefresh).toHaveBeenCalledTimes(1);
    expect(fetch).not.toHaveBeenCalled();
  });
});
