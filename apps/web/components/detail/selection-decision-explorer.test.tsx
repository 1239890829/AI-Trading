import {act, cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {PickCard, fromDailyPick, fromIntradayStock} from "@/components/picks/pick-card";
import {NotificationDetail} from "@/components/notifications/notification-detail";
import {getStockAttribution, type AttributionEvidenceItem, type DailyPickItem, type IntradayTopStock, type NotificationItem, type SelectionMetrics} from "@/lib/api";
import {readSelectionMetrics} from "./selection-decision-explorer";

vi.mock("@/lib/api", async () => ({...await vi.importActual<typeof import("@/lib/api")>("@/lib/api"), getStockAttribution: vi.fn()}));
const reason = "原选择记录：产品线有观察线索，尚不能确认新增订单。";
const condition = "客户或订单不能确认时，原判断不能升级。";
const source: AttributionEvidenceItem = {
  event_id: 18, title: "合成事件：拟扩大产品应用", summary: "拟扩大应用，仍需核查项目。", url: "https://example.com/18",
  state: "active", state_label: "解释生效", is_active: true, certainty: "proposed", dimensions: [],
  source: {collector: "fixture", original_publisher: null, source_tier: 1, source_tier_label: "合成来源", source_item_id: "18", content_hash: "fixture"},
  timing: {source_published_at: "2026-10-20T08:30:00+08:00", received_at: "2026-10-20T08:31:00+08:00", available_at: "2026-10-20T08:31:00+08:00", interpretation_effective_at: "2026-10-20T08:32:00+08:00", card_published_at: null, source_time_known: true},
  interpretation_ref: {event_id: 18, version_id: 22, observation_id: 19, available_at: "2026-10-20T08:31:00+08:00", state: "active"},
  fact_support: {status: "unverified", label: "来源待核", note: "未独立核验"},
  stock_relation: {status: "theme_only", label: "仅题材关联", direction: null, chain: "", basis: "原题材关系", matched_by: "theme", matched_themes: []},
  market_response: {status: "unknown", status_label: "市场响应待核", verification_scope: "market_response", fact_verified: false, causality_verified: false, basis: "未验证价格因果"},
  qualifiers: [], source_context: {title: "合成事件：拟扩大产品应用", summary: "拟扩大应用，仍需核查项目。", url: "https://example.com/18", observation_id: 19, change_kind: "initial"},
  pending_context: null, alternatives: [], limitations: ["不证明经营效果"],
  company_impact: {symbol: "600825", company_name: "合成样例公司", event_id: 18, version_id: 22, observation_id: 19,
    input_sha256: "a".repeat(64), generated_at: "2026-10-20T09:05:00+08:00", valid_until: "2026-10-27T09:05:00+08:00", facts_captured_at: "2026-10-20T09:00:00+08:00",
    facts: [{id: "company:product", kind: "product", quote: "合成公司主营传动产品，但尚未取得本次项目订单。", document_title: "合成公司半年度报告", url: "https://static.cninfo.com.cn/finalpage/2026-08-30/fixture.PDF", published_at: "2026-08-30T00:00:00+08:00", period: "2026上半年", page: 18, sha256: "b".repeat(64)}],
    analysis: {status: "conditional", summary: "若项目采用公司相应产品，才可能形成需求，现有材料不能证明获单。", chain: [{text: "应用计划与原产品存在待核交集，客户和订单尚不能确认。", evidence_ids: ["event:title", "company:product"]}], counterarguments: ["原产品不等于本次项目已经采用。"], missing: ["缺少本次客户或订单归属。"]},
    model: {provider: "fixture", model: "fixture-model"}, verification: "automated_evidence_bound_not_gold", generation_method: "model_selection_and_deterministic_compilation", eligibility_changed: false, current_context_only: true, business_effect_verified: false},
};
const originalRef = {event_id: 18, version_id: 22, observation_id: 19, available_at: "2026-10-20T08:31:00+08:00", state: "active" as const};
const secondRef = {...originalRef, event_id: 21, version_id: 33, observation_id: 29};
const measurements: SelectionMetrics = {schema_version: 1, producer: "linkage_candidates", snapshot_version: "snapshot-original", items: [
  {key: "change_pct", label: "当日涨幅", value: 7.26, unit: "%", state: "ready", source: "fixture-quote", as_of: "2026-10-20T09:10:00+08:00", received_at: "2026-10-20T09:10:02+08:00",
    thresholds: [{key: "candidate_min", label: "候选下沿", value: 1, operator: ">="}, {key: "follow", label: "跟进线", value: 3.5, operator: ">="}, {key: "pre_limit", label: "临板线", value: 6.75, operator: ">="}, {key: "seal", label: "封板线", value: 9.4, operator: "<"}], basis: "原计算使用这次快照涨幅与现役门槛；进入评估不保证成交。"},
  {key: "amount", label: "当日成交额", value: 125000000, unit: "元", state: "ready", source: "fixture-quote", as_of: null, received_at: null,
    thresholds: [{key: "min", label: "流动性下沿", value: 30000000, operator: ">="}], basis: "原流动性依据，不代表增量资金流入。"},
  {key: "theme_limit_ups", label: "题材涨停家数", value: 6, unit: "家", state: "ready", source: "theme_board", as_of: null, received_at: null,
    thresholds: [{key: "min", label: "集中家数下沿", value: 3, operator: ">="}], basis: "题材原统计，仅为规则基座。"},
  {key: "theme_share", label: "题材涨停占比", value: null, unit: "%", state: "unknown", source: "theme_board", as_of: null, received_at: null,
    thresholds: [], basis: "分母未提供，原规则未把缺失当作0%。"},
  {key: "limit_up_gap_pct", label: "距实际涨停价", value: null, unit: "%", state: "unknown", source: null, as_of: null, received_at: null,
    thresholds: [], basis: "原源未提供实际涨停价，不能以判定线推算。"},
]};
function secondSource(): AttributionEvidenceItem {
  return {...source, event_id: 21, title: "合成事件：应用项目暂缓", interpretation_ref: secondRef,
    source_context: {...source.source_context!, title: "合成事件：应用项目暂缓", observation_id: 29, summary: "项目暂缓，尚无恢复时间。"},
    company_impact: {...source.company_impact!, event_id: 21, version_id: 33, observation_id: 29,
      facts: [{...source.company_impact!.facts[0], quote: "合成公司披露相关产品，尚无项目恢复订单。", document_title: "合成公司项目说明", page: 24}],
      analysis: {...source.company_impact!.analysis, summary: "项目暂缓可能推迟需求，但公司敞口尚不能量化。", counterarguments: ["未确认该项目采用公司产品。"], missing: ["缺少项目恢复时间和敞口。"]}}};
}
function mount(kind: "pick" | "notice", asOf = "2026-10-20T09:20:00+08:00", refs = [originalRef], archived: Record<string, unknown> = {}, sourceAsOf: string | null = asOf) {
  if (kind === "pick") {
    const pick = {symbol: "600825", name: "合成样例公司", price: 12, change_pct: 1, score: 60, sub_scores: {news: 77, fundamental: 21, tech: 50},
      bases: {news: "原消息分项依据", fundamental: "原基本面分项依据", tech: "技术原读取缺失"},
      dimension_evidence: {news: {state: "success", reason: "原消息分项依据"}, fundamental: {state: "success", reason: "原基本面分项依据"}, tech: {state: "missing", reason: "技术原读取缺失，50为计算占位"}}, source_basis: reason,
      related_events: [], related_event_refs: refs, vetoes: [], invalidations: [condition]} as unknown as DailyPickItem;
    const view = render(<PickCard item={fromDailyPick(pick)}/>);
    fireEvent.click(screen.getByRole("button", {name: "入选解读"}));
    return view;
  }
  const item: NotificationItem = {id: "selection-1", title: "原入选观察", category: "opportunity", label: "入选观察", session: "intraday", symbol: "600825", body: reason, validity: condition, ts: asOf, score: null, url: null,
    references: [{event_id: 99, kind: "selection", selection_source: "intraday", source_id: "run:A", source_version: "vA", source_as_of: sourceAsOf, recorded_at: asOf, trade_date: "2026-10-20", evidence: {pick_basis: reason, invalidations: [condition], related_event_refs: refs, ...archived}}]};
  return render(<NotificationDetail item={item} open onClose={vi.fn()} onOpenNews={vi.fn()}/>);
}
beforeEach(() => {vi.useFakeTimers({toFake: ["Date"]}); vi.setSystemTime(new Date("2026-10-20T10:00:00+08:00")); vi.mocked(getStockAttribution).mockResolvedValue({schema_version: 1, symbol: "600825", count: 1, items: [source]});});
afterEach(() => {cleanup(); vi.clearAllMocks(); vi.useRealTimers();});

it.each(["pick", "notice"] as const)("%s shows company evidence, conditional impact and risks without requiring an expansion", async kind => {
  mount(kind);
  const explorer = await screen.findByRole("region", {name: "入选决策核对台"});
  const facts = await within(explorer).findByLabelText("当前事件公司原句");
  expect(facts.textContent).toContain(source.company_impact!.facts[0].quote);
  expect(facts.textContent).toContain("第 18 页");
  expect(within(facts).getByRole("link", {name: "查看披露原文"}).getAttribute("href")).toBe(source.company_impact!.facts[0].url);
  expect(within(explorer).getByLabelText("当前事件影响解释").textContent).toContain(source.company_impact!.analysis.summary);
  expect(within(explorer).getByLabelText("当前事件反证与缺项").textContent).toContain(source.company_impact!.analysis.counterarguments[0]);
  expect(within(explorer).getByLabelText("当前事件反证与缺项").textContent).toContain(source.company_impact!.analysis.missing[0]);
  expect(within(explorer).getByRole("tabpanel")).toBeTruthy();
  if (kind === "notice") expect(within(explorer).getByText(/原记录未存分项评分/)).toBeTruthy();
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
});

it("real saved score values fill bars, missing placeholders do not; hover/focus/click link the exact basis", async () => {
  mount("pick");
  const explorer = await screen.findByRole("region", {name: "入选决策核对台"});
  const news = within(explorer).getByRole("meter", {name: "消息原规则分值"});
  expect(news.getAttribute("aria-valuenow")).toBe("77");
  expect(news.getAttribute("aria-valuemax")).toBe("100");
  expect((news.firstElementChild as HTMLElement).style.width).toBe("77%");
  expect(within(explorer).queryByRole("meter", {name: "技术原规则分值"})).toBeNull();
  const fundamental = within(explorer).getByRole("button", {name: "核对基本面评分依据"});
  fireEvent.mouseEnter(fundamental);
  expect(within(explorer).getByLabelText("当前评分依据").textContent).toContain("原基本面分项依据");
  expect(within(explorer).getByLabelText("公司联系").getAttribute("data-highlight")).toBe("true");
  const tech = within(explorer).getByRole("button", {name: "核对技术评分依据"});
  fireEvent.focus(tech);
  expect(within(explorer).getByLabelText("当前评分依据").textContent).toContain("50 分为计算占位");
  fireEvent.click(within(explorer).getByRole("button", {name: "核对消息评分依据"}));
  expect(within(explorer).getByLabelText("当前评分依据").textContent).toContain("原消息分项依据");
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
});

it("a saved numeric score with unknown read status stays visible without a measured fill", async () => {
  mount("notice", undefined, undefined, {sub_scores: {news: 50}, bases: {news: "旧记录有50分，未存读取状态"}});
  const explorer = await screen.findByRole("region", {name: "入选决策核对台"});
  const news = within(explorer).getByRole("button", {name: "核对消息评分依据"});
  expect(news.textContent).toContain("50 / 100");
  expect(within(explorer).queryByRole("meter", {name: "消息原规则分值"})).toBeNull();
  fireEvent.focus(news);
  expect(within(explorer).getByLabelText("当前评分依据").textContent).toContain("未保存读取状态");
});

it("notifications use only their archived finite score values and never fill missing placeholders", async () => {
  mount("notice", undefined, undefined, {sub_scores: {news: 64, tech: 50, fundamental: 104, capital: "92", sentiment: Infinity, undeclared: 100},
    bases: {news: "通知原存档消息依据"}, dimension_evidence: {news: {state: "success"}, tech: {state: "error", reason: "原技术来源失败"}}});
  const explorer = await screen.findByRole("region", {name: "入选决策核对台"});
  expect(within(explorer).getByRole("meter", {name: "消息原规则分值"}).getAttribute("aria-valuenow")).toBe("64");
  expect(within(explorer).getAllByRole("meter")).toHaveLength(1);
  expect(within(explorer).queryByText("undeclared")).toBeNull();
  fireEvent.click(within(explorer).getByRole("button", {name: "核对技术评分依据"}));
  expect(within(explorer).getByLabelText("当前评分依据").textContent).toContain("50 分为计算占位");
  fireEvent.click(within(explorer).getByRole("button", {name: "核对消息评分依据"}));
  expect(within(explorer).getByLabelText("当前评分依据").textContent).toContain("通知原存档消息依据");
});

it("intraday cards show original measurements and variable producer thresholds, not invented six-dimension scores", async () => {
  const intraday = {symbol: "600825", name: "合成样例公司", change_pct: 7.26, price: 13.6, tier: 1, pick_basis: reason,
    linkage: {level: "高", basis: "原联动定性依据"}, selection_metrics: measurements, related_event_refs: [originalRef]} as unknown as IntradayTopStock;
  const adapted = fromIntradayStock(intraday);
  expect(adapted.sub_scores).toBeNull();
  render(<PickCard item={adapted}/>);
  fireEvent.click(screen.getByRole("button", {name: "入选解读"}));
  const region = await screen.findByRole("region", {name: "盘中关键选择项"});
  const meter = within(region).getByRole("meter", {name: "当日涨幅原测量刻度"});
  expect(meter.getAttribute("aria-valuenow")).toBe("7.26");
  expect(meter.getAttribute("aria-valuemax")).toBe("9.4");
  expect(region.textContent).toContain("跟进线 ≥3.5%");
  expect(region.textContent).toContain("临板线 ≥6.75%");
  expect(region.textContent).not.toContain("/ 100");
  expect(within(region).queryByRole("meter", {name: "距实际涨停价原测量刻度"})).toBeNull();
  fireEvent.mouseEnter(within(region).getByRole("button", {name: "核对当日成交额原测量"}));
  const basis = within(region).getByLabelText("当前原测量依据");
  expect(basis.textContent).toContain("不代表增量资金流入");
  expect(basis.textContent).toContain("源时刻：未提供");
  expect(basis.textContent).toContain("接收时刻：未提供");
  fireEvent.focus(within(region).getByRole("button", {name: "核对当日涨幅原测量"}));
  expect(basis.textContent).toContain("09:10:00");
  expect(basis.textContent).toContain("09:10:02");
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
});

it("the selected main card exposes and links real measurements without opening a drawer or reading attribution", () => {
  const item = fromIntradayStock({symbol: "600825", name: "合成样例公司", change_pct: 7.26, price: 13.6, tier: 1, pick_basis: reason,
    linkage: {level: "高", basis: "原联动定性依据"}, selection_metrics: measurements} as unknown as IntradayTopStock);
  const view = render(<PickCard item={item}/>);
  const overview = screen.getByRole("region", {name: "盘中关键选择项概览"});
  expect(within(overview).getByRole("meter", {name: "当日涨幅原测量刻度"}).getAttribute("aria-valuenow")).toBe("7.26");
  expect(within(overview).getByLabelText("当前原测量依据").textContent).toContain("进入评估不保证成交");
  fireEvent.mouseEnter(within(overview).getByRole("button", {name: "核对当日成交额原测量"}));
  expect(within(overview).getByLabelText("当前原测量依据").textContent).toContain("不代表增量资金流入");
  fireEvent.focus(within(overview).getByRole("button", {name: "核对题材涨停家数原测量"}));
  expect(within(overview).getByLabelText("当前原测量依据").textContent).toContain("题材原统计");
  expect(screen.queryByRole("region", {name: "入选决策核对台"})).toBeNull();
  expect(getStockAttribution).not.toHaveBeenCalled();
  view.rerender(<PickCard item={{...item, referenceOnly: true}}/>);
  expect(screen.queryByRole("region", {name: "盘中关键选择项概览"})).toBeNull();
  expect(getStockAttribution).not.toHaveBeenCalled();
});

it("the daily main card shares exact scores, leaves unknown and missing values unfilled, and links the original basis without a read", () => {
  const item = fromDailyPick({symbol: "600825", name: "合成样例公司", price: 12, change_pct: 1, score: 60,
    sub_scores: {news: 77, fundamental: 50, tech: 50}, bases: {news: "原消息评分依据"},
    dimension_evidence: {news: {state: "success", reason: "原消息评分依据"}, fundamental: {state: "unknown", reason: "原状态未知"}, tech: {state: "missing", reason: "技术读取缺失"}}} as unknown as DailyPickItem);
  const view = render(<PickCard item={item}/>);
  const overview = screen.getByRole("region", {name: "入选关键评分概览"});
  expect(within(overview).getByRole("meter", {name: "消息原规则分值"}).getAttribute("aria-valuenow")).toBe("77");
  expect(within(overview).getAllByRole("meter")).toHaveLength(1);
  fireEvent.focus(within(overview).getByRole("button", {name: "核对基本面评分依据"}));
  expect(within(overview).getByLabelText("当前评分依据").textContent).toContain("原状态未知");
  expect(within(overview).getByLabelText("当前评分依据").textContent).toContain("数字不证明证据完整");
  fireEvent.click(within(overview).getByRole("button", {name: "核对消息评分依据"}));
  expect(within(overview).getByLabelText("当前评分依据").textContent).toContain("原消息评分依据");
  view.rerender(<PickCard item={{...item, sub_scores: {news: 0}, dimensionEvidence: {news: {state: "valid_empty", reason: "来源成功读取，没有匹配消息"}}}}/>);
  expect(within(overview).getByRole("meter", {name: "消息原规则分值"}).getAttribute("aria-valuenow")).toBe("0");
  fireEvent.focus(within(overview).getByRole("button", {name: "核对消息评分依据"}));
  expect(within(overview).getByLabelText("当前评分依据").textContent).toContain("有效空集");
  expect(within(overview).getByLabelText("当前评分依据").textContent).toContain("不是读取失败或缺失");
  expect(getStockAttribution).not.toHaveBeenCalled();
});

it("notifications render only archived original measurements and retain a missing denominator and missing actual limit price", async () => {
  mount("notice", undefined, undefined, {selection_metrics: measurements});
  const region = await screen.findByRole("region", {name: "盘中关键选择项"});
  expect(within(region).getByRole("meter", {name: "题材涨停家数原测量刻度"}).getAttribute("aria-valuenow")).toBe("6");
  expect(within(region).queryByRole("meter", {name: "题材涨停占比原测量刻度"})).toBeNull();
  fireEvent.click(within(region).getByRole("button", {name: "核对题材涨停占比原测量"}));
  expect(within(region).getByLabelText("当前原测量依据").textContent).toContain("未把缺失当作0%");
  fireEvent.click(within(region).getByRole("button", {name: "核对距实际涨停价原测量"}));
  expect(within(region).getByLabelText("当前原测量依据").textContent).toContain("不能以判定线推算");
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
});

it.each([
  {...measurements, producer: "arbitrary"},
  {...measurements, items: [{...measurements.items[0], unit: "分"}]},
  {...measurements, items: [measurements.items[0], measurements.items[0]]},
  {...measurements, items: [{...measurements.items[0], thresholds: [{key: "fake", label: "假满分", value: Infinity, operator: ">="}]}]},
])("rejects a foreign, conflicting or malformed measurement contract", value => {
  expect(readSelectionMetrics(value)).toBeNull();
});

it.each(["pick", "notice"] as const)("%s event controls update the visible evidence together and support keyboard navigation without refetching", async kind => {
  const second = secondSource();
  vi.mocked(getStockAttribution).mockResolvedValue({schema_version: 1, symbol: "600825", count: 2, items: [source, second]});
  mount(kind, "2026-10-20T09:20:00+08:00", [originalRef, secondRef]);
  const explorer = await screen.findByRole("region", {name: "入选决策核对台"});
  const firstTab = await within(explorer).findByRole("tab", {name: /合成事件：拟扩大产品应用/});
  const secondTab = within(explorer).getByRole("tab", {name: /合成事件：应用项目暂缓/});
  fireEvent.click(secondTab);
  expect(secondTab.getAttribute("aria-selected")).toBe("true");
  expect(within(explorer).getByLabelText("当前事件公司原句").textContent).toContain(second.company_impact!.facts[0].quote);
  expect(within(explorer).getByLabelText("当前事件影响解释").textContent).toContain(second.company_impact!.analysis.summary);
  expect(within(explorer).getByLabelText("当前事件反证与缺项").textContent).toContain(second.company_impact!.analysis.missing[0]);
  expect(within(explorer).getByLabelText("当前事件原句").textContent).toContain("项目暂缓，尚无恢复时间。");
  expect(within(explorer).getByLabelText("当前事件公司原句").textContent).not.toContain(source.company_impact!.facts[0].quote);
  fireEvent.keyDown(secondTab, {key: "ArrowLeft"});
  expect(document.activeElement).toBe(firstTab);
  expect(firstTab.getAttribute("aria-selected")).toBe("true");
  expect(within(explorer).getByLabelText("当前事件公司原句").textContent).toContain(source.company_impact!.facts[0].quote);
  fireEvent.keyDown(firstTab, {key: "ArrowRight", isComposing: true});
  expect(firstTab.getAttribute("aria-selected")).toBe("true");
  fireEvent.keyDown(firstTab, {key: "End"});
  expect(document.activeElement).toBe(secondTab);
  fireEvent.keyDown(secondTab, {key: "Home"});
  expect(document.activeElement).toBe(firstTab);
  expect(within(explorer).getByRole("tabpanel").getAttribute("aria-labelledby")).toBe(firstTab.id);
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
});

it("keyboard selection reveals an offscreen event within the horizontal rail without moving its enclosing reader", async () => {
  vi.mocked(getStockAttribution).mockResolvedValue({schema_version: 1, symbol: "600825", count: 2, items: [source, secondSource()]});
  mount("notice", "2026-10-20T09:20:00+08:00", [originalRef, secondRef]);
  const explorer = await screen.findByRole("region", {name: "入选决策核对台"});
  const rail = await within(explorer).findByRole("tablist", {name: "关联事件选择"});
  const tabs = within(rail).getAllByRole("tab");
  Object.defineProperties(rail, {clientWidth: {value: 260}, scrollWidth: {value: 368}});
  vi.spyOn(rail, "getBoundingClientRect").mockImplementation(() => ({left: 0, right: 260}) as DOMRect);
  tabs.forEach((tab, index) => vi.spyOn(tab, "getBoundingClientRect").mockImplementation(() => ({left: index * 188 - rail.scrollLeft, right: index * 188 + 180 - rail.scrollLeft}) as DOMRect));
  explorer.scrollTop = 75;
  fireEvent.keyDown(tabs[0], {key: "End"});
  expect(document.activeElement).toBe(tabs[1]);
  expect(rail.scrollLeft).toBe(108);
  expect(explorer.scrollTop).toBe(75);
  fireEvent.keyDown(tabs[1], {key: "Home"});
  expect(document.activeElement).toBe(tabs[0]);
  expect(rail.scrollLeft).toBe(0);
  expect(explorer.scrollTop).toBe(75);
});

it.each(["pick", "notice"] as const)("%s does not put another stock's company claim into the visible explorer", async kind => {
  vi.mocked(getStockAttribution).mockResolvedValue({schema_version: 1, symbol: "600825", count: 1, items: [{...source, company_impact: {...source.company_impact!, symbol: "600826"}}]});
  mount(kind);
  const explorer = await screen.findByRole("region", {name: "入选决策核对台"});
  await act(async () => {});
  expect(explorer.textContent).not.toContain(source.company_impact!.facts[0].quote);
  expect(explorer.textContent).not.toContain(source.company_impact!.analysis.summary);
  expect(explorer.textContent).toContain("待核");
});

it("old selection notices show historical gaps instead of inserting later company research", async () => {
  mount("notice", "2026-10-19T09:20:00+08:00");
  const explorer = await screen.findByRole("region", {name: "入选决策核对台"});
  await act(async () => {});
  expect(explorer.textContent).toContain("原提醒时点");
  expect(explorer.textContent).not.toContain(source.company_impact!.facts[0].quote);
  expect(explorer.textContent).not.toContain(source.company_impact!.analysis.summary);
  expect(getStockAttribution).toHaveBeenCalledWith("600825", {poolDate: undefined, responseAsOf: "2026-10-19T09:20:00+08:00"});
});

it.each([null, "invalid"])("a historical notice with unknown cutoff %s cannot substitute the current response clock or company status", async sourceAsOf => {
  vi.mocked(getStockAttribution).mockResolvedValue({schema_version: 1, symbol: "600825", count: 1, items: [source],
    response_context: {pool_date: null, response_as_of: "2026-10-20T09:20:00+08:00", view: "current_with_response_context", historical_replay: false},
    company_status: {symbol: "600825", state: "ready", reason: "当前处理完成记录不属于历史", updated_at: "2026-10-20T09:10:00+08:00", next_retry_at: null,
      task_id: null, documents_count: 1, missing: [], facts: source.company_impact!.facts}});
  mount("notice", undefined, undefined, {}, sourceAsOf);
  const explorer = await screen.findByRole("region", {name: "入选决策核对台"});
  await act(async () => {});
  expect(explorer.textContent).toContain("原提醒未保存有效资料时点");
  expect(within(explorer).queryByRole("tab", {name: /合成事件/})).toBeNull();
  expect(explorer.textContent).not.toContain(source.company_impact!.analysis.summary);
  fireEvent.click(screen.getByRole("button", {name: "公司依据、完整传导与相关消息"}));
  expect(screen.getByText(/不以当前响应时刻回填/)).toBeTruthy();
  expect(screen.queryByText("当前处理完成记录不属于历史", {exact: false})).toBeNull();
  expect(screen.queryByText(source.company_impact!.facts[0].quote)).toBeNull();
});
