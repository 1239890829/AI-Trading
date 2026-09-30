import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { OpportunityEvidencePanel } from "./opportunity-evidence-panel";
import { getOpportunities, type OpportunityView } from "@/lib/api/picks";
vi.mock("@/lib/api/picks", () => ({ getOpportunities: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const payload = (date = "2026-09-30"): OpportunityView => ({
  trade_date: date, contract_version: "test", state: "not_collected", cards: [], runs: [],
  disclaimer: "不构成买卖建议", coverage: "读取已有决定，未采集不等于没有机会",
});
describe("同版机会依据", () => {
  it("distinguishes uncollected, collected empty and failed reads", async () => {
    vi.mocked(getOpportunities).mockResolvedValueOnce(payload())
      .mockResolvedValueOnce({ ...payload("2026-09-29"), state: "collected_empty" })
      .mockResolvedValueOnce({ ...payload("2026-09-28"), state: "unavailable" })
      .mockRejectedValueOnce(new Error("offline"));
    render(<OpportunityEvidencePanel />);
    expect(await screen.findByText(/尚无已归档决定/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("机会决定日期"), { target: { value: "2026-09-29" } });
    expect(await screen.findByText(/本轮已记录，候选为空/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("机会决定日期"), { target: { value: "2026-09-28" } });
    expect(await screen.findByText(/已有运行记录，但来源未就绪/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("机会决定日期"), { target: { value: "2026-09-27" } });
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText(/本轮已记录，候选为空/)).toBeNull();
  });
  it("cannot replace a new date with an old late response", async () => {
    let finish: (data: OpportunityView) => void = () => {};
    vi.mocked(getOpportunities).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
      .mockResolvedValueOnce(payload("2026-09-28"));
    render(<OpportunityEvidencePanel />);
    await waitFor(() => expect(getOpportunities).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText("机会决定日期"), { target: { value: "2026-09-28" } });
    expect(await screen.findByText(/2026-09-28 · 0 只/)).toBeTruthy();
    finish(payload("2026-09-30"));
    await waitFor(() => expect(screen.queryByText(/2026-09-30 · 0 只/)).toBeNull());
  });
  it("keeps multiple hypotheses and unknowns without execution controls", async () => {
    const data = payload();
    data.cards = [{ symbol: "600127", name: "金健米业", hypotheses: ["农业", "独立事件"].map((theme, i) => ({
      opportunity_id: `op-${i}`, decision_id: "od", decision_version: `v${i}`, scenario: "new_path",
      source_theme: theme, routes: ["company_event", "trend"], state: "unknown", data_state: "unknown", source: "test-fixture",
      first_seen: "2026-09-30T10:00:00", as_of: "2026-09-30T10:01:00",
      reasons: [theme], unknowns: ["公司受益待核"], reference: { price: null, semantics: "reference_only_not_fill" },
      actionable: false, execution_blocker: "封板，参与条件待核", snapshot_refs: ["snapshot"],
    })) }];
    vi.mocked(getOpportunities).mockResolvedValue(data);
    render(<OpportunityEvidencePanel />);
    expect(await screen.findByText(/2 条独立假设/)).toBeTruthy();
    expect(screen.getAllByText(/公司受益待核/)).toHaveLength(2);
    expect(screen.getAllByText(/观察路径：公司独立事件 \/ 趋势强势/)).toHaveLength(2);
    expect(screen.getAllByText(/参考价 缺失，不是成交价/)).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /建仓|买入/ })).toBeNull();
  });
});

it("pin and compare retain the original date/version while following reads the selected current object", async () => {
  const first = payload("2026-09-30");
  const hypothesis = {opportunity_id: "o", decision_id: "d", decision_version: "v1", scenario: "trend", source_theme: "趋势", state: "waiting", data_state: "unknown", source: "test", first_seen: "10:00", as_of: "10:01", reasons: ["依据1"], unknowns: ["等待1"], reference: {price: 10, semantics: "reference_only"}, actionable: false, execution_blocker: "不能执行", snapshot_refs: []};
  first.cards = [{symbol: "600127", name: "对象A", hypotheses: [hypothesis]}];
  const second = {...payload("2026-09-29"), cards: [{symbol: "600825", name: "对象B", hypotheses: [{...hypothesis, decision_version: "v2", reasons: ["依据2"]}]}]};
  vi.mocked(getOpportunities).mockResolvedValueOnce(first).mockResolvedValueOnce(second);
  render(<OpportunityEvidencePanel />);
  const origin = await screen.findByRole("button", {name: "查看 600127 依据"});
  origin.closest("details")!.open = true;
  fireEvent.click(origin);
  fireEvent.click(screen.getByRole("button", {name: "固定版本"}));
  fireEvent.change(screen.getByLabelText("机会决定日期"), {target: {value: "2026-09-29"}});
  await screen.findByRole("button", {name: "查看 600825 依据"});
  const side = screen.getByLabelText("当前证据侧栏");
  expect(side.textContent).toContain("2026-09-30（固定快照）");
  expect(side.textContent).toContain("v1");
  fireEvent.click(screen.getByRole("button", {name: "对比"}));
  fireEvent.click(screen.getByRole("button", {name: "查看 600825 依据"}));
  expect(side.textContent).toContain("v1"); expect(side.textContent).toContain("v2");
  fireEvent.click(screen.getByRole("button", {name: "跟随"}));
  expect(screen.getByLabelText("当前证据侧栏").textContent).toContain("对象B");
  expect(screen.getByLabelText("当前证据侧栏").textContent).not.toContain("v1");
  fireEvent.click(screen.getByRole("button", {name: "关闭侧栏"}));
  expect(document.activeElement).toBe(screen.getByLabelText("机会决定日期"));
});
