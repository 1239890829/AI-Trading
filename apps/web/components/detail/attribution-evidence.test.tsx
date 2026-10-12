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
beforeEach(() => {vi.mocked(getStockAttribution).mockResolvedValue(payload());});
afterEach(() => {cleanup(); vi.clearAllMocks(); vi.useRealTimers();});

describe("shared attribution evidence", () => {
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
