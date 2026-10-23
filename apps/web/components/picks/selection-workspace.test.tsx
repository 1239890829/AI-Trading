import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {getStockAttribution, type DailyPickItem, type IntradayTopStock, type StockAttributionPayload} from "@/lib/api";
import {createAgentTask, getTradingAgentsResearch, type TradingAgentsResearchView} from "@/lib/api/agent";
import {fromDailyPick, fromIntradayStock} from "./pick-card";
import {SelectionWorkspace, type SelectionWorkspaceGroup} from "./selection-workspace";

vi.mock("@/lib/api", async () => ({...await vi.importActual<typeof import("@/lib/api")>("@/lib/api"), getStockAttribution: vi.fn()}));
vi.mock("@/lib/api/agent", () => ({getTradingAgentsResearch: vi.fn(), createAgentTask: vi.fn()}));
const open = vi.hoisted(() => vi.fn());
vi.mock("@/components/detail/detail-modal", () => ({useDetailModal: () => ({open})}));
vi.mock("@/components/inspection/inspection-context", () => ({useInspection: () => ({open})}));
function daily(symbol: string, name: string, news = 82) {
  return fromDailyPick({symbol, name, price: 10, change_pct: 2, score: 70, sub_scores: {news, tech: 50, fundamental: 31},
    bases: {news: `${name}原消息依据。订单尚未确认，不得升级为确定获益。`, tech: "缺少技术来源，50为计算占位。", fundamental: "原基本面仅部分取得。"},
    dimension_evidence: {news: {state: "success", reason: `${name}原消息依据。订单尚未确认，不得升级为确定获益。`}, tech: {state: "missing", reason: "缺少技术来源，50为计算占位。"}, fundamental: {state: "partial", reason: "原基本面仅部分取得。"}},
    vetoes: [], themes: [], related_events: [], buy_range: null, source_basis: `${name}原入选依据，后续仍需核对。`, invalidations: ["客户订单缺失则不能升级。"],
    stop_loss: {price: 9.4, pct: 6, basis: "原止损参考，非成交"},
    selection_entry: {state: "recorded", selected_at: "2026-10-23T09:35:00+08:00", quote_as_of: "2026-10-23T09:35:00+08:00", reference_price: 10, reference_change_pct: 2, source: "daily_generation", source_version: "daily-v1", semantics: "observation_only_not_fill"}} satisfies DailyPickItem);
}
function intraday(symbol = "600111", name = "盘中甲") {
  return fromIntradayStock({symbol, name, price: 12, change_pct: 4, tier: 1, theme: "合成题材", pick_basis: "本轮联动满足原门槛，仍需参与复核。",
    role: null, boards: null, stage: null, strength_tier: null, reason: null, distinctiveness: null, certainty: null,
    selection_metrics: {schema_version: 1, producer: "linkage_candidates", snapshot_version: "live-v1", items: [{key: "change_pct", label: "当日涨幅", value: 4, unit: "%", state: "ready", source: "fixture", as_of: "2026-10-23T10:00:00+08:00", received_at: null,
      thresholds: [{key: "min", label: "候选下沿", operator: ">=", value: 1}], basis: "原涨幅4%，仅用于候选门槛，不能保证可成交。"}]}} satisfies IntradayTopStock);
}
function groups(): SelectionWorkspaceGroup[] {
  return [{key: "intraday", title: "盘中候选", description: "当日动态名单", items: [intraday()]}, {key: "picks", title: "每日精选", description: "原持久记录", items: [daily("600222", "每日乙"), daily("600333", "每日丙", 65)]}];
}
const emptySource = (symbol: string): StockAttributionPayload => ({schema_version: 1, symbol, count: 0, items: []});
const emptyResearch = (symbol: string): TradingAgentsResearchView => ({symbol, enabled: false, executable: false, blockers: [], budget: null, required_model_slots: null, admission: {state: "not_admitted", method_sha256: null}, verification: "unverified_full_graph_research", production_eligible: false, latest: null, task: null});
beforeEach(() => {
  vi.clearAllMocks();
  HTMLElement.prototype.scrollIntoView = vi.fn();
  vi.mocked(getStockAttribution).mockImplementation(async symbol => emptySource(symbol));
  vi.mocked(getTradingAgentsResearch).mockImplementation(async symbol => emptyResearch(symbol));
});
afterEach(cleanup);

it("shows separate full-group matrices in original order and reads only one selected stock's saved evidence", async () => {
  const input = groups();
  input[0].items.push({...intraday("600999", "仅作参考"), referenceOnly: true});
  render(<SelectionWorkspace groups={input}/>);
  const intradayMatrix = screen.getByRole("table", {name: "盘中候选原值对照"});
  const dailyMatrix = screen.getByRole("table", {name: "每日精选原值对照"});
  expect(within(intradayMatrix).getByText("4%")).toBeTruthy();
  expect(within(intradayMatrix).queryByText("原规则分 / 100")).toBeNull();
  expect(within(dailyMatrix).getAllByText("原规则分 / 100")).toHaveLength(6);
  expect(within(dailyMatrix).getAllByRole("rowheader").map(row => row.textContent)).toEqual([expect.stringContaining("每日乙"), expect.stringContaining("每日丙")]);
  expect(screen.queryByRole("button", {name: "核对仅作参考入选依据"})).toBeNull();
  await waitFor(() => expect(getStockAttribution).toHaveBeenCalledTimes(1));
  expect(getStockAttribution).toHaveBeenLastCalledWith("600111");
  expect(getTradingAgentsResearch).toHaveBeenCalledTimes(1);
  expect(getTradingAgentsResearch).toHaveBeenLastCalledWith("600111");
  expect(screen.queryByRole("button", {name: "显式启动完整研究"})).toBeNull();
  expect(createAgentTask).not.toHaveBeenCalled();
});

it("unavailable or pending empty lists do not imply no candidates and never read a reference-only stock", () => {
  const input: SelectionWorkspaceGroup[] = [{key: "intraday", title: "盘中候选", description: "未读取", readState: "pending", items: [{...intraday(), referenceOnly: true}]}, {key: "picks", title: "每日精选", description: "失败", readState: "unavailable", items: []}];
  render(<SelectionWorkspace groups={input}/>);
  expect(screen.getByText("正在读取这组原名单…")).toBeTruthy();
  expect(screen.getByText("原名单读取失败，不能确认是否有入选项。")).toBeTruthy();
  expect(screen.queryByRole("region", {name: "所选股原地证据舞台"})).toBeNull();
  expect(getStockAttribution).not.toHaveBeenCalled();
  expect(getTradingAgentsResearch).not.toHaveBeenCalled();
});

it("deduplicating daily rows does not turn the true persisted selection count into an empty selection", () => {
  const input = groups();
  input[1] = {...input[1], items: [], total: 2};
  render(<SelectionWorkspace groups={input}/>);
  const dailyGroup = screen.getByRole("heading", {name: "每日精选"}).closest("section")!;
  expect(dailyGroup.textContent).toContain("2 只");
  expect(dailyGroup.textContent).toContain("0 行对照 · 同股不重复");
  expect(dailyGroup.textContent).toContain("本组入选已在盘中候选展示");
});

it.each([["可参与", "可参与评估 · 当前未封板"], ["不可参与", "当前不可参与"], ["unknown", "可参与性未判定"], ["tradable", "可参与性未判定"]])("keeps the original eligibility wording for %s instead of exposing raw enum values", async (level, expected) => {
  const input = groups();
  input[0].items[0] = {...input[0].items[0], tradability: {level, basis: "原参与资格依据，不保证成交"}};
  render(<SelectionWorkspace groups={input}/>);
  const stage = screen.getByRole("region", {name: "所选股原地证据舞台"});
  expect(within(stage).getByText(expected).getAttribute("title")).toBe("原参与资格依据，不保证成交");
  if (["unknown", "tradable"].includes(level)) expect(within(stage).queryByText(level)).toBeNull();
  await act(async () => {});
  expect(createAgentTask).not.toHaveBeenCalled();
});

it("a value cell selects the stock and exact basis; same-stock field changes do not refetch; dot comparison changes the owner", async () => {
  render(<SelectionWorkspace groups={groups()}/>);
  await waitFor(() => expect(getStockAttribution).toHaveBeenCalledTimes(1));
  const cell = screen.getByRole("button", {name: "每日乙，消息，82分"});
  cell.focus(); fireEvent.click(cell);
  const stage = screen.getByRole("region", {name: "所选股原地证据舞台"});
  expect(within(stage).getByRole("heading", {name: /每日乙/})).toBeTruthy();
  expect(within(stage).getByRole("region", {name: "所选分项原依据"}).textContent).toContain("订单尚未确认，不得升级为确定获益。");
  expect(within(stage).getByRole("region", {name: "原选择简述"}).textContent).toContain("客户订单缺失则不能升级。");
  expect(within(stage).getAllByText("每日乙原入选依据，后续仍需核对。")).toHaveLength(1);
  expect(screen.getByLabelText("当前核对反馈").textContent).toContain("每日乙 · 消息 · 82分");
  expect(document.activeElement).toBe(cell);
  await waitFor(() => expect(getStockAttribution).toHaveBeenCalledTimes(2));
  expect(getTradingAgentsResearch).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", {name: "每日乙，技术，缺项"}));
  const field = within(stage).getByRole("region", {name: "所选分项原依据"});
  expect(field.textContent).toContain("50为计算占位");
  expect(within(field).getByLabelText("技术同组原值对照").querySelectorAll("i")).toHaveLength(0);
  expect(getStockAttribution).toHaveBeenCalledTimes(2);
  expect(getTradingAgentsResearch).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", {name: "每日乙，消息，82分"}));
  fireEvent.click(within(stage).getByRole("button", {name: "改看每日丙的消息"}));
  await waitFor(() => expect(getStockAttribution).toHaveBeenLastCalledWith("600333"));
  expect(within(stage).getByRole("region", {name: "所选分项原依据"}).textContent).toContain("每日丙原消息依据");
  expect(createAgentTask).not.toHaveBeenCalled();
});

it("gives local feedback and an explicit in-place locator without taking keyboard focus; all metric clocks and original conditions remain reachable", async () => {
  render(<SelectionWorkspace groups={groups()}/>);
  const target = screen.getByRole("button", {name: "盘中甲，当日涨幅，4%"});
  target.focus(); fireEvent.click(target);
  expect(screen.getByLabelText("当前核对反馈").textContent).toContain("盘中甲 · 当日涨幅 · 4%");
  expect(document.activeElement).toBe(target);
  fireEvent.click(screen.getByRole("button", {name: "定位盘中甲的所选依据"}));
  expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledWith(expect.objectContaining({block: "start"}));
  expect(document.activeElement).toBe(target);
  fireEvent.click(screen.getByText("核对本项来源与时刻"));
  const field = screen.getByRole("region", {name: "所选分项原依据"});
  expect(field.textContent).toContain("2026-10-23 10:00:00");
  expect(field.textContent).toContain("live-v1");
  expect(field.textContent).toContain("候选下沿≥1%");
  fireEvent.click(await screen.findByRole("button", {name: "核对完整理由与条件"}));
  expect(screen.getByRole("region", {name: "原选择核对"}).textContent).toContain("本轮联动满足原门槛，仍需参与复核。");
  expect(createAgentTask).not.toHaveBeenCalled();
});

it("a delayed prior owner cannot put its evidence under the new selection; a removed owner and new version retire old context", async () => {
  let resolve!: (value: StockAttributionPayload) => void;
  vi.mocked(getStockAttribution).mockImplementationOnce(() => new Promise(done => {resolve = done;}));
  const input = groups();
  const view = render(<SelectionWorkspace groups={input}/>);
  await waitFor(() => expect(getStockAttribution).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", {name: "每日乙，消息，82分"}));
  await waitFor(() => expect(getStockAttribution).toHaveBeenLastCalledWith("600222"));
  await act(async () => resolve(emptySource("600111")));
  expect(screen.getByRole("region", {name: "所选股原地证据舞台"}).querySelector("[data-evidence-owner]")?.getAttribute("data-evidence-owner")).toBe("600222");
  view.rerender(<SelectionWorkspace groups={[input[0], {...input[1], items: [input[1].items[1]]}]}/>);
  expect(screen.getByRole("region", {name: "所选股原地证据舞台"}).textContent).not.toContain("每日乙原消息依据");
  expect(createAgentTask).not.toHaveBeenCalled();
});

it("retires a changed list date/version even when a legacy stock lacks its own archived entry identity", async () => {
  const legacy = {...daily("600222", "每日乙"), selectionEntry: undefined};
  const original: SelectionWorkspaceGroup[] = [{key: "picks", title: "每日精选", description: "原日期", contextKey: "2026-10-22/v1", items: [legacy]}];
  const view = render(<SelectionWorkspace groups={original}/>);
  await waitFor(() => expect(getStockAttribution).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", {name: "每日乙，消息，82分"}));
  expect(screen.getByRole("region", {name: "所选分项原依据"})).toBeTruthy();
  view.rerender(<SelectionWorkspace groups={[{...original[0], contextKey: "2026-10-23/v2", description: "新日期"}]}/>);
  expect(screen.queryByRole("region", {name: "所选分项原依据"})).toBeNull();
  await waitFor(() => expect(getStockAttribution).toHaveBeenCalledTimes(2));
  expect(getTradingAgentsResearch).toHaveBeenCalledTimes(2);
  expect(createAgentTask).not.toHaveBeenCalled();
});

it("retains financial/theme actions, original daily evidence and stop reference while refreshing a selected matrix", async () => {
  const input = groups();
  input[1].items[0] = {...input[1].items[0], tradability: {level: "可参与", basis: "原记录可参与评估，仍非成交"}};
  const dailyEntry = {item: {symbol: "600111"} as DailyPickItem, date: "2026-10-23", generatedAt: null, version: "daily-v1"};
  const onDaily = vi.fn();
  const view = render(<SelectionWorkspace groups={input} dailyEvidence={new Map([["600111", dailyEntry]])} onShowDailyEvidence={onDaily}/>);
  fireEvent.click(screen.getByRole("button", {name: "每日原依据"}));
  expect(onDaily).toHaveBeenCalledWith(dailyEntry.item);
  fireEvent.click(screen.getByRole("button", {name: "资金"}));
  expect(open).toHaveBeenCalledWith(expect.objectContaining({kind: "capital", symbol: "600111"}));
  fireEvent.click(screen.getByRole("button", {name: "核对每日乙入选依据"}));
  expect(screen.getByText(/止损参考 ¥9.40/)).toBeTruthy();
  await waitFor(() => expect(getStockAttribution).toHaveBeenLastCalledWith("600222"));
  const requests = vi.mocked(getStockAttribution).mock.calls.length;
  const reportReads = vi.mocked(getTradingAgentsResearch).mock.calls.length;
  view.rerender(<SelectionWorkspace groups={[input[0], {...input[1], readError: true}]}/>);
  expect(screen.getByRole("region", {name: "所选股原地证据舞台"}).textContent).toContain("上次名单 · 当前待核");
  expect(within(screen.getByRole("region", {name: "所选股原地证据舞台"})).queryByText("可参与评估 · 当前未封板")).toBeNull();
  expect(getStockAttribution).toHaveBeenCalledTimes(requests);
  expect(getTradingAgentsResearch).toHaveBeenCalledTimes(reportReads);
  expect(createAgentTask).not.toHaveBeenCalled();
});
