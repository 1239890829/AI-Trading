import {act, cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {getEventAttribution, getStockAttribution, type AttributionEvidenceItem, type StockAttributionPayload} from "@/lib/api";
import {AttributionEvidence, StockAttributionPanel} from "./attribution-evidence";

vi.mock("@/lib/api", () => ({getStockAttribution: vi.fn(), getEventAttribution: vi.fn()}));
const evidence: AttributionEvidenceItem = {
  event_id: 18, title: "拟收购；标的尚未完成审计", summary: "拟购买股权，需审批", url: "https://example.com/18",
  state: "active", state_label: "解释生效", is_active: true, certainty: "proposed",
  dimensions: [{key: "corporate", label: "公司事件", matched_terms: ["收购"]}],
  source: {collector: "eastmoney", original_publisher: null, source_tier: 2, source_tier_label: "采集平台", source_item_id: "18", content_hash: "original"},
  timing: {source_published_at: null, received_at: "2026-10-12T09:31:00+08:00", available_at: "2026-10-12T09:31:00+08:00", interpretation_effective_at: "2026-10-12T09:31:00+08:00", card_published_at: null, source_time_known: false},
  interpretation_ref: {event_id: 18, version_id: 22, observation_id: 19, available_at: "2026-10-12T09:31:00+08:00", state: "active"},
  fact_support: {status: "unverified", label: "来源内容待核", note: "来源记录不代表真实性已核实"},
  stock_relation: {status: "theme_only", label: "仅题材关联", direction: null, chain: "需核查实际业务", basis: "仅命中题材成员", matched_by: "theme", matched_themes: ["传媒"]},
  market_response: {status: "unknown", status_label: "市场响应待核", verification_scope: "market_response", fact_verified: false, causality_verified: false, basis: "未独立计算市场响应"},
  qualifiers: [{kind: "proposed", label: "尚属预案", matched_terms: ["拟"]}],
  source_context: {title: "原始标题：拟收购", summary: "尚未完成审计；不代表完成收购。", url: "https://example.com/18", observation_id: 19, change_kind: "initial"},
  pending_context: null, alternatives: ["成交变化还可能来自其他催化"], limitations: ["未核实业务受益"],
};
const payload = (symbol = "600825", item = evidence): StockAttributionPayload => ({schema_version: 1, symbol, count: 1, items: [item]});
const secondEvidence: AttributionEvidenceItem = {...evidence, event_id: 21, title: "第二条：公司澄清业务范围", source_context: {...evidence.source_context!, summary: "澄清仅供核查，不构成受益证明。"}, interpretation_ref: {...evidence.interpretation_ref, event_id: 21}};
const listPayload = (symbol = "600825"): StockAttributionPayload => ({schema_version: 1, symbol, count: 2, items: [evidence, secondEvidence]});
beforeEach(() => {vi.mocked(getStockAttribution).mockResolvedValue(payload());});
afterEach(() => {cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); vi.useRealTimers();});

describe("shared attribution evidence", () => {
  it("starts with compact message buttons and opens only the selected detail without another read", async () => {
    vi.mocked(getStockAttribution).mockResolvedValue(listPayload());
    render(<StockAttributionPanel symbol="600825" defaultExpanded selectedAt="2026-10-12T09:30:00+08:00" />);
    const first = await screen.findByRole("button", {name: `查看归因消息：${evidence.title}`});
    expect(screen.getByRole("button", {name: `查看归因消息：${secondEvidence.title}`})).toBeTruthy();
    expect(screen.queryByRole("article")).toBeNull();
    expect(screen.queryByText(evidence.stock_relation.basis)).toBeNull();
    fireEvent.click(first);
    expect(screen.getAllByRole("article")).toHaveLength(1);
    expect(screen.queryByText(secondEvidence.title)).toBeNull();
    expect(screen.getByText(/尚属预案（拟）/)).toBeTruthy();
    expect(screen.getByText(/消息可用时间晚于首次加入/)).toBeTruthy();
    fireEvent.click(screen.getByText("核对原文与时点"));
    expect(screen.getByText("尚未完成审计；不代表完成收购。")).toBeTruthy();
    expect(getStockAttribution).toHaveBeenCalledTimes(1);
    expect(getEventAttribution).not.toHaveBeenCalled();
  });

  it("returns focus to the original message and can then read a different message", async () => {
    vi.mocked(getStockAttribution).mockResolvedValue(listPayload());
    render(<StockAttributionPanel symbol="600825" defaultExpanded />);
    fireEvent.click(await screen.findByRole("button", {name: `查看归因消息：${secondEvidence.title}`}));
    expect(document.activeElement).toBe(screen.getByRole("button", {name: "返回消息列表"}));
    fireEvent.click(screen.getByRole("button", {name: "返回消息列表"}));
    expect(document.activeElement).toBe(screen.getByRole("button", {name: `查看归因消息：${secondEvidence.title}`}));
    expect(screen.queryByRole("article")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: `查看归因消息：${evidence.title}`}));
    expect(screen.getByRole("article", {name: "事件归因 18"})).toBeTruthy();
    expect(screen.queryByRole("article", {name: "事件归因 21"})).toBeNull();
    expect(getStockAttribution).toHaveBeenCalledTimes(1);
  });

  it("returns to the compact list after closing and reopening", async () => {
    render(<StockAttributionPanel symbol="600825" defaultExpanded />);
    fireEvent.click(await screen.findByRole("button", {name: `查看归因消息：${evidence.title}`}));
    fireEvent.click(screen.getByRole("button", {name: "驱动线索"}));
    fireEvent.click(screen.getByRole("button", {name: "驱动线索"}));
    await screen.findByRole("button", {name: `查看归因消息：${evidence.title}`});
    expect(screen.queryByRole("article")).toBeNull();
    expect(screen.queryByRole("button", {name: "返回消息列表"})).toBeNull();
  });

  it("never transfers the selected detail to another stock, even when the event ID is shared", async () => {
    vi.mocked(getStockAttribution).mockResolvedValueOnce(payload()).mockResolvedValueOnce(payload("600127", {...evidence, title: "新股票线索"}));
    const view = render(<StockAttributionPanel symbol="600825" defaultExpanded />);
    fireEvent.click(await screen.findByRole("button", {name: `查看归因消息：${evidence.title}`}));
    view.rerender(<StockAttributionPanel symbol="600127" defaultExpanded />);
    await screen.findByRole("button", {name: "查看归因消息：新股票线索"});
    expect(screen.queryByRole("article")).toBeNull();
    expect(screen.queryByText(evidence.title)).toBeNull();
    expect(getStockAttribution).toHaveBeenCalledTimes(2);
  });

  it("preserves the selected detail through a refresh failure", async () => {
    render(<StockAttributionPanel symbol="600825" defaultExpanded />);
    fireEvent.click(await screen.findByRole("button", {name: `查看归因消息：${evidence.title}`}));
    vi.mocked(getStockAttribution).mockRejectedValueOnce(new Error("offline"));
    fireEvent.click(screen.getByRole("button", {name: "刷新依据"}));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("保留上次读取内容"));
    expect(screen.getByRole("article", {name: "事件归因 18"})).toBeTruthy();
    expect(screen.getByRole("button", {name: "返回消息列表"})).toBeTruthy();
  });

  it("returns to the list when a successful refresh removes the selected message", async () => {
    render(<StockAttributionPanel symbol="600825" defaultExpanded />);
    fireEvent.click(await screen.findByRole("button", {name: `查看归因消息：${evidence.title}`}));
    vi.mocked(getStockAttribution).mockResolvedValueOnce(payload("600825", secondEvidence));
    fireEvent.click(screen.getByRole("button", {name: "刷新依据"}));
    const remaining = await screen.findByRole("button", {name: `查看归因消息：${secondEvidence.title}`});
    expect(screen.getByRole("status")).toHaveProperty("textContent", expect.stringContaining("所选消息已不在本次资料中，已返回列表"));
    expect(screen.queryByRole("article")).toBeNull();
    expect(document.activeElement).toBe(remaining);
    vi.mocked(getStockAttribution).mockResolvedValueOnce(listPayload());
    fireEvent.click(screen.getByRole("button", {name: "刷新依据"}));
    await screen.findByRole("button", {name: `查看归因消息：${evidence.title}`});
    expect(screen.queryByRole("article")).toBeNull();
  });

  it("keeps the selected message visible during an update and uses the refreshed interpretation", async () => {
    render(<StockAttributionPanel symbol="600825" defaultExpanded />);
    fireEvent.click(await screen.findByRole("button", {name: `查看归因消息：${evidence.title}`}));
    let finish!: (value: StockAttributionPayload) => void;
    vi.mocked(getStockAttribution).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    fireEvent.click(screen.getByRole("button", {name: "刷新依据"}));
    expect(screen.getByRole("article", {name: "事件归因 18"})).toBeTruthy();
    expect(screen.queryByRole("status", {name: "归因依据加载中"})).toBeNull();
    await act(async () => {finish(payload("600825", {...evidence, title: "同条消息的新解释", qualifiers: [{kind: "pending", label: "审批尚未完成", matched_terms: []}]}));});
    expect(screen.getByRole("article", {name: "事件归因 18"})).toBeTruthy();
    expect(screen.getByText("同条消息的新解释")).toBeTruthy();
    expect(screen.getByText(/审批尚未完成/)).toBeTruthy();
    expect(screen.queryByRole("button", {name: /查看归因消息/})).toBeNull();
  });

  it("handles a refresh that removes every message without leaving an empty detail", async () => {
    render(<StockAttributionPanel symbol="600825" defaultExpanded />);
    fireEvent.click(await screen.findByRole("button", {name: `查看归因消息：${evidence.title}`}));
    vi.mocked(getStockAttribution).mockResolvedValueOnce({schema_version: 1, symbol: "600825", count: 0, items: []});
    fireEvent.click(screen.getByRole("button", {name: "刷新依据"}));
    await screen.findByText(/所选消息已不在本次资料中/);
    expect(screen.queryByRole("article")).toBeNull();
    expect(screen.getByText(/不能据此认定没有驱动消息/)).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole("list", {name: "归因消息列表"}));
  });

  it("restores the bounded reader position on return without scrolling the page", async () => {
    render(<StockAttributionPanel symbol="600825" defaultExpanded />);
    const first = await screen.findByRole("button", {name: `查看归因消息：${evidence.title}`});
    const reader = first.closest<HTMLElement>("[data-attribution-reader]")!;
    reader.scrollTop = 120;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
      return new DOMRect(0, this.textContent === "返回消息列表" ? -120 : 0, 320, 44);
    });
    fireEvent.click(first);
    expect(reader.scrollTop).toBe(0);
    fireEvent.click(screen.getByRole("button", {name: "返回消息列表"}));
    expect(reader.scrollTop).toBe(120);
    expect(document.activeElement).toBe(screen.getByRole("button", {name: `查看归因消息：${evidence.title}`}));
  });

  it("positions the single detail within the actual drawer body and restores its original scroll", async () => {
    const view = render(<div role="dialog"><div data-testid="drawer-reader" className="overflow-y-auto" style={{overflowY: "auto"}}><StockAttributionPanel symbol="600825" defaultExpanded bounded={false} /></div></div>);
    const first = await screen.findByRole("button", {name: `查看归因消息：${evidence.title}`});
    const drawer = screen.getByTestId("drawer-reader");
    drawer.scrollTop = 240;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
      return new DOMRect(0, this.textContent === "返回消息列表" ? -120 : 0, 320, 44);
    });
    fireEvent.click(first);
    expect(drawer.scrollTop).toBe(112);
    fireEvent.click(screen.getByRole("button", {name: "返回消息列表"}));
    expect(drawer.scrollTop).toBe(240);
    expect(document.activeElement).toBe(screen.getByRole("button", {name: `查看归因消息：${evidence.title}`}));
    view.unmount();
  });

  it("uses the inner stock context reader and leaves its outer scroll container untouched", async () => {
    render(<div data-testid="outer-reader" className="overflow-auto" style={{overflow: "auto"}}><div data-testid="stock-context-reader" className="stock-context-brief" style={{overflowY: "auto"}}><StockAttributionPanel symbol="600825" defaultExpanded bounded={false} /></div></div>);
    const first = await screen.findByRole("button", {name: `查看归因消息：${evidence.title}`});
    const outer = screen.getByTestId("outer-reader");
    const context = screen.getByTestId("stock-context-reader");
    outer.scrollTop = 80;
    context.scrollTop = 240;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
      return new DOMRect(0, this.textContent === "返回消息列表" ? -120 : 0, 320, 44);
    });
    fireEvent.click(first);
    expect(context.scrollTop).toBe(112);
    expect(outer.scrollTop).toBe(80);
    fireEvent.click(screen.getByRole("button", {name: "返回消息列表"}));
    expect(context.scrollTop).toBe(240);
    expect(outer.scrollTop).toBe(80);
    expect(document.activeElement).toBe(screen.getByRole("button", {name: `查看归因消息：${evidence.title}`}));
  });

  it("qualifies strong original interpretation wording without losing its source trace", () => {
    render(<AttributionEvidence item={{...evidence, stock_relation: {...evidence.stock_relation, basis: "权威关联：收购标的", chain: "直接利好相关业务"}}} />);
    expect(screen.getByText("来源收录不等于消息属实；关联与方向仍须核对。")).toBeTruthy();
    expect(screen.getAllByText("原解释（待核）：")).toHaveLength(2);
    expect(screen.getByText(/权威关联：收购标的/)).toBeTruthy();
    expect(screen.getByText(/直接利好相关业务/)).toBeTruthy();
  });

  it("waits for explicit expansion and adds no candidate timer", async () => {
    vi.useFakeTimers();
    render(<StockAttributionPanel symbol="600825" />);
    expect(getStockAttribution).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name: "驱动线索"}));
    await act(async () => {});
    expect(screen.getByText(evidence.title)).toBeTruthy();
    await act(async () => {await vi.advanceTimersByTimeAsync(300_000);});
    expect(getStockAttribution).toHaveBeenCalledTimes(1);
  });

  it("separates loading from valid empty and incomplete coverage", async () => {
    let finish!: (value: StockAttributionPayload) => void;
    vi.mocked(getStockAttribution).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    render(<StockAttributionPanel symbol="600825" defaultExpanded />);
    expect(screen.getByRole("status", {name: "归因依据加载中"})).toBeTruthy();
    expect(screen.queryByText(/暂未找到关联事件/)).toBeNull();
    await act(async () => {finish({schema_version: 1, symbol: "600825", count: 0, items: []});});
    expect(screen.getByText(/不能据此认定没有驱动消息/)).toBeTruthy();
  });

  it("retains the previous same-stock evidence during failed refresh", async () => {
    render(<StockAttributionPanel symbol="600825" defaultExpanded />);
    await screen.findByText(evidence.title);
    vi.mocked(getStockAttribution).mockRejectedValueOnce(new Error("offline"));
    fireEvent.click(screen.getByRole("button", {name: "刷新依据"}));
    expect(screen.getByText(evidence.title)).toBeTruthy();
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("保留上次读取内容"));
  });

  it("discards late replies from the previous stock", async () => {
    let finish!: (value: StockAttributionPayload) => void;
    vi.mocked(getStockAttribution).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;})).mockResolvedValueOnce(payload("600127", {...evidence, title: "当前股票关联"}));
    const page = render(<StockAttributionPanel symbol="600825" defaultExpanded />);
    page.rerender(<StockAttributionPanel symbol="600127" defaultExpanded />);
    await screen.findByText("当前股票关联");
    await act(async () => {finish(payload());});
    expect(screen.queryByText(evidence.title)).toBeNull();
  });

  it("reads an event without inventing a stock identity", async () => {
    vi.mocked(getEventAttribution).mockResolvedValue({...payload(), symbol: null});
    render(<StockAttributionPanel eventId={18} title="事件依据核对" defaultExpanded />);
    await screen.findByText(evidence.title);
    expect(getEventAttribution).toHaveBeenCalledWith(18, undefined);
    expect(getStockAttribution).not.toHaveBeenCalled();
  });

  it("preserves proposals, source excerpts and unknowns without fake probabilities", () => {
    render(<AttributionEvidence item={evidence} />);
    expect(screen.getByText("仅题材关联")).toBeTruthy();
    expect(screen.getByText("来源内容待核")).toBeTruthy();
    expect(screen.getByText("市场响应待核")).toBeTruthy();
    expect(screen.getByText(/尚属预案（拟）/)).toBeTruthy();
    fireEvent.click(screen.getByText("核对原文与时点"));
    expect(screen.getByText("尚未完成审计；不代表完成收购。")).toBeTruthy();
    expect(screen.getByText(/未知，未用收录时间补填/)).toBeTruthy();
    expect(screen.queryByText(/置信度|上涨概率|已确认涨停原因/)).toBeNull();
    expect(screen.getByRole("link", {name: "解释版本绑定的来源内容原文"}).getAttribute("href")).toBe("https://example.com/18");
  });

  it("marks legacy card excerpts without inventing a source observation binding", () => {
    render(<AttributionEvidence item={{...evidence, source: {...evidence.source, source_tier: null, source_tier_label: "来源等级未知"}, source_context: {...evidence.source_context!, observation_id: null}, interpretation_ref: {...evidence.interpretation_ref, observation_id: null}}} />);
    fireEvent.click(screen.getByText("核对原文与时点"));
    expect(screen.getByText("旧卡片摘录 · 来源观察身份未记录")).toBeTruthy();
    expect(screen.queryByText("解释版本绑定的来源内容")).toBeNull();
    expect(screen.getByText("尚未完成审计；不代表完成收购。")).toBeTruthy();
    expect(screen.getByText(/来源等级未知/)).toBeTruthy();
  });

  it("keeps a mismatching source observation distinct from the interpretation binding", () => {
    render(<AttributionEvidence item={{...evidence, source_context: {...evidence.source_context!, observation_id: 23}}} />);
    fireEvent.click(screen.getByText("核对原文与时点"));
    expect(screen.getByText("来源观察内容 · 解释绑定待核")).toBeTruthy();
    expect(screen.queryByText("解释版本绑定的来源内容")).toBeNull();
  });

  it("shows that an unknown event stage cannot be read as a completed event", () => {
    const unknownStage = {...evidence, certainty: "unknown"};
    render(<AttributionEvidence item={unknownStage} />);
    expect(screen.getByText("事件阶段尚未核实，不能按已落地事件解读。")).toBeTruthy();
  });

  it("does not confuse a recorded completed stage with fact or causality verification", () => {
    const completedStage = {...evidence, certainty: "done", qualifiers: []};
    render(<AttributionEvidence item={completedStage} />);
    expect(screen.queryByText("事件阶段尚未核实，不能按已落地事件解读。")).toBeNull();
    expect(screen.getByText("来源内容待核")).toBeTruthy();
    expect(screen.getByText("市场响应待核")).toBeTruthy();
  });

  it("does not backfill later information into original admission", () => {
    render(<AttributionEvidence item={evidence} selectedAt="2026-10-12T09:30:00+08:00" recordedRef={{...evidence.interpretation_ref, version_id: 20}} />);
    expect(screen.getByText(/消息可用时间晚于首次加入/)).toBeTruthy();
    expect(screen.getByText(/不回填当时入选依据/)).toBeTruthy();
  });

  it("cannot infer historical availability from a timezone-free clock", () => {
    render(<AttributionEvidence item={evidence} selectedAt="2026-10-12T09:30:00" />);
    expect(screen.getByText(/时点证据不完整/)).toBeTruthy();
    expect(screen.queryByText(/晚于首次加入/)).toBeNull();
  });

  it("separates a later explanation from an earlier source observation", () => {
    render(<AttributionEvidence item={{...evidence, timing: {...evidence.timing, available_at: "2026-10-12T09:20:00+08:00", interpretation_effective_at: "2026-10-12T09:35:00+08:00"}}} selectedAt="2026-10-12T09:30:00+08:00" />);
    expect(screen.getByText(/当前解释形成晚于首次加入/)).toBeTruthy();
    expect(screen.queryByText(/消息可用时间晚于首次加入/)).toBeNull();
  });

  it("never describes a list entry's version as a selection record", () => {
    render(<AttributionEvidence item={evidence} recordedRef={{...evidence.interpretation_ref, version_id: 20}} />);
    expect(screen.getByText(/与列表入口引用的版本不同/)).toBeTruthy();
    expect(screen.queryByText(/首次加入记录|选股记录/)).toBeNull();
  });

  it("keeps source revisions distinct and rejects unsafe source links", () => {
    render(<AttributionEvidence item={{...evidence, pending_context: {title: "修订标题", summary: "公司澄清不涉及相关业务", url: "javascript:alert(1)", observation_id: 23, change_kind: "revision"}}} />);
    fireEvent.click(screen.getByText("核对原文与时点"));
    expect(screen.getByText("来源已修订，待复核内容")).toBeTruthy();
    expect(screen.getByText("公司澄清不涉及相关业务")).toBeTruthy();
    expect(screen.queryByRole("link", {name: "来源已修订，待复核内容原文"})).toBeNull();
  });
});
