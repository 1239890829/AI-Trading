import {act, cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {TradingAgentsResearchPanel} from "./tradingagents-research-panel";
import {createAgentTask, getTradingAgentsResearch, type AgentTask, type TradingAgentsResearchView} from "@/lib/api/agent";

vi.mock("@/lib/api/agent", () => ({createAgentTask: vi.fn(), getTradingAgentsResearch: vi.fn()}));
const view = (symbol = "002121"): TradingAgentsResearchView => ({
  symbol, enabled: false, executable: false, required_model_slots: 22,
  blockers: [{code: "disabled", message: "完整研究未启用"}, {code: "quality_not_admitted", message: "净增益尚未准入"}],
  budget: {date: "2026-10-20", limit: 8, used: 0, remaining: 8, scope: "autonomy_llm"},
  admission: {state: "not_admitted", method_sha256: "current-method"},
  verification: "unverified_full_graph_research", production_eligible: false, latest: null, task: null,
});
const saved = (symbol = "002121"): TradingAgentsResearchView => ({...view(symbol), latest: {
  symbol, version: 1, state: "completed", run_id: `${symbol}-run`, generated_at: "2026-10-19T21:00:00+08:00",
  as_of: "2026-10-19T15:00:00+08:00", generated_today: false, upstream_sha: "upstream", input_sha256: "input", method_sha256: "old-method",
  verification: "unverified_full_graph_research", production_eligible: false,
  review_state: "unreviewed", review_summary: {finite_failures: 0, peer_review_required: 7, source_verified: false, independent_human_gold: false},
  reports: {market_report: `${symbol} 行情原报告`, sentiment_report: "合法新闻语气，待核", news_report: "诉讼一审待生效，可上诉",
    fundamentals_report: "冻结比例基期未经审计，当前现金流未知", investment_plan: "研究辩论原文", trader_investment_plan: "无账户，不给仓位",
    final_trade_decision: "最终原文不证明未来收益"},
}});
beforeEach(() => {vi.clearAllMocks(); vi.mocked(getTradingAgentsResearch).mockResolvedValue(view());});
afterEach(() => {cleanup(); vi.useRealTimers();});

describe("完整TradingAgents研究读者", () => {
  it("opens an ordinary read only on request and exposes actual blockers without running models", async () => {
    render(<TradingAgentsResearchPanel symbol="002121"/>);
    expect(getTradingAgentsResearch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name: /TradingAgents 完整研究/}));
    await screen.findByText(/当前未启用完整研究/);
    expect(getTradingAgentsResearch).toHaveBeenCalledWith("002121");
    fireEvent.click(screen.getByText(/查看启动阻断原因/));
    expect(screen.getByText("净增益尚未准入")).toBeTruthy();
    expect((screen.getByRole("button", {name: "显式启动完整研究"}) as HTMLButtonElement).disabled).toBe(true);
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("retains all seven full originals, source cutoff, method and stale identity", async () => {
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(saved());
    const {container} = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen/>);
    await screen.findByText(/已保存完整七报告/);
    for (const label of ["行情分析", "新闻与情绪分析", "新闻分析", "基本面分析", "研究辩论结论", "交易员研究方案", "风险辩论与最终研究结论"]) {
      const summary = screen.getByText(label); fireEvent.click(summary);
      expect(summary.closest("details")?.open).toBe(true);
    }
    expect(container.textContent).toContain("冻结比例基期未经审计，当前现金流未知");
    expect(container.textContent).toContain("材料截点：2026-10-19 15:00");
    expect(container.textContent).toContain("历史生成报告，不代表今天已重新判读");
    expect(container.textContent).toContain("报告方法与当前执行器不同");
    expect(container.textContent).toContain("old-method");
    expect(container.textContent).toContain("不构成买卖建议");
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("retires a delayed old stock response and closes old report disclosures on stock change", async () => {
    let finish!: (value: TradingAgentsResearchView) => void;
    vi.mocked(getTradingAgentsResearch).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;})).mockResolvedValueOnce(saved("600276"));
    const panel = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen/>);
    await waitFor(() => expect(getTradingAgentsResearch).toHaveBeenCalledWith("002121"));
    panel.rerender(<TradingAgentsResearchPanel symbol="600276" defaultOpen/>);
    await screen.findByText(/600276 · 已保存/);
    await act(async () => {finish(saved());});
    expect(panel.container.textContent).not.toContain("002121 行情原报告");
    expect(panel.container.textContent).toContain("600276 行情原报告");
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("shows failed automatic checks beside complete originals without calling them validated", async () => {
    const value = saved();
    value.latest!.review_state = "finite_failed_requires_review";
    value.latest!.review_summary.finite_failures = 2;
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(value);
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen/>);
    await screen.findByText(/自动来源或引用检查有2项未通过/);
    expect(screen.getByText(/尚无独立人工验收/)).toBeTruthy();
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("preserves same-stock saved reports on refresh failure and blocks execution from stale eligibility", async () => {
    vi.mocked(getTradingAgentsResearch).mockResolvedValue({...saved(), enabled: true, executable: true, blockers: []});
    const panel = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen/>);
    await screen.findByText(/已保存完整七报告/);
    vi.mocked(getTradingAgentsResearch).mockRejectedValue(new Error("ordinary read failed"));
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    await screen.findByText(/保留本股上次读取报告/);
    expect(panel.container.textContent).toContain("启动条件读取失败，当前资格未确认");
    expect(panel.container.textContent).toContain("002121 行情原报告");
    expect((screen.getByRole("button", {name: "显式启动完整研究"}) as HTMLButtonElement).disabled).toBe(true);
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it.each(["queued", "running", "canceled", "failed"] as const)("shows %s separately from a retained report and never equates cancellation request with completion", async status => {
    const result = saved(); result.task = {id: "task", status, cancel_requested: status === "running", failed: status === "failed", created_at: "2026-10-20T10:00:00+08:00", finished_at: null, error: status === "failed" ? {code: "SourceError", message: "本轮失败，旧报告保留"} : null};
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(result);
    const panel = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen/>);
    await screen.findByText(/已保存完整七报告/);
    const labels = {queued: "排队中", running: "取消请求已记录，等待当前步骤结束", canceled: "已取消", failed: "研究失败"};
    expect(panel.container.textContent).toContain(labels[status]);
    expect(panel.container.textContent).toContain("002121 行情原报告");
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("submits exactly the current symbol once, keeps unknown post calibration, and accepts no model/path fields", async () => {
    vi.mocked(getTradingAgentsResearch).mockResolvedValue({...view(), enabled: true, executable: true, blockers: []});
    let finish!: (task: AgentTask) => void;
    vi.mocked(createAgentTask).mockImplementation(() => new Promise(resolve => {finish = resolve;}));
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen/>);
    const button = await screen.findByRole("button", {name: "显式启动完整研究"});
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button); fireEvent.click(button);
    expect(createAgentTask).toHaveBeenCalledTimes(1);
    expect(createAgentTask).toHaveBeenCalledWith("tradingagents_research", {symbol: "002121"});
    await act(async () => {finish({id: "new-task", type: "tradingagents_research", params: {symbol: "002121"}, status: "queued", steps: [], result_ref: null, error: null, risk_level: "L0", created_by: "operator", created_at: "2026-10-20T21:00:00+08:00", started_at: null, finished_at: null});});
    await screen.findByText(/持久状态待校准/);
    expect((screen.getByRole("button", {name: "显式启动完整研究"}) as HTMLButtonElement).disabled).toBe(true);
  });

  it("does not request equity research for an index or hidden stock panel", () => {
    const panel = render(<TradingAgentsResearchPanel symbol="sh000001" defaultOpen/>);
    expect(panel.container.textContent).toBe("");
    panel.rerender(<TradingAgentsResearchPanel symbol="002121" defaultOpen active={false}/>);
    expect(screen.queryByRole("region", {name: "TradingAgents完整研究"})).toBeNull();
    expect(getTradingAgentsResearch).not.toHaveBeenCalled();
  });

  it("keeps the compact entry unread until opened and never offers a model start even when executable", async () => {
    vi.mocked(getTradingAgentsResearch).mockResolvedValue({...view(), enabled: true, executable: true, blockers: []});
    render(<TradingAgentsResearchPanel symbol="002121" compact/>);
    expect(screen.getByText(/未读取 · 展开只查已有结果/)).toBeTruthy();
    expect(getTradingAgentsResearch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name: "TradingAgents 研究记录 · 查看"}));
    await screen.findByText("002121 · 未查到研究记录");
    expect(screen.queryByRole("button", {name: "显式启动完整研究"})).toBeNull();
    expect(screen.queryByRole("link", {name: "任务中心查看或取消"})).toBeNull();
    expect(screen.getByText("为什么尚未运行或更新").closest("details")?.open).toBe(false);
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    await waitFor(() => expect(getTradingAgentsResearch).toHaveBeenCalledTimes(2));
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("shows the compact final report before six full originals without opening long prose or losing evidence", async () => {
    const result = saved();
    result.latest!.review_state = "finite_failed_requires_review";
    result.latest!.review_summary.finite_failures = 2;
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(result);
    const panel = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen compact allowStart={false}/>);
    await screen.findByText("002121 · 已有保存报告 · 历史报告 · 待核");
    const final = screen.getByText("查看最终研究结论").closest("details")!;
    const others = screen.getByText("查看其余6份完整报告").closest("details")!;
    expect(final.open).toBe(false);
    expect(others.open).toBe(false);
    expect(final.compareDocumentPosition(others) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(screen.getByText("查看最终研究结论"));
    expect(final.open).toBe(true);
    expect(final.textContent).toContain("最终原文不证明未来收益");
    expect(final.textContent).toContain("尚未独立验收，不构成买卖建议");
    fireEvent.click(screen.getByText("查看其余6份完整报告"));
    for (const label of ["行情分析", "新闻与情绪分析", "新闻分析", "基本面分析", "研究辩论结论", "交易员研究方案"]) {
      fireEvent.click(screen.getByText(label));
      expect(screen.getByText(label).closest("details")?.open).toBe(true);
    }
    expect(panel.container.textContent).toContain("材料截点：2026-10-19 15:00");
    expect(panel.container.textContent).toContain("old-method");
    expect(screen.getByText(/自动来源或引用检查有2项未通过/)).toBeTruthy();
    expect(screen.getByText(/报告方法与当前执行器不同/)).toBeTruthy();
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("distinguishes compact initial loading, failed reads and budget blocks from an unrun stock", async () => {
    let finish!: (value: TradingAgentsResearchView) => void;
    vi.mocked(getTradingAgentsResearch).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen compact/>);
    expect(screen.getByText("002121 · 读取中")).toBeTruthy();
    expect(screen.queryByText("002121 · 未查到研究记录")).toBeNull();
    await act(async () => {finish({...view(), blockers: [{code: "budget_insufficient", message: "完整图需22次，剩余8次"}]});});
    await screen.findByText("002121 · 未查到研究记录");
    expect(screen.getByText("预算不足，当前不能新增完整研究。")).toBeTruthy();
    vi.mocked(getTradingAgentsResearch).mockRejectedValue(new Error("read failed"));
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    await screen.findByText("002121 · 读取失败");
    expect(screen.queryByText("002121 · 未查到研究记录")).toBeNull();
    expect(screen.queryByText("预算不足，当前不能新增完整研究。")).toBeNull();
    expect(screen.getByText(/尚不能确认是否有已保存报告/)).toBeTruthy();
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("retains a compact same-stock report on failed refresh and rejects a delayed previous-stock response", async () => {
    vi.mocked(getTradingAgentsResearch).mockResolvedValueOnce(saved());
    const panel = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen compact/>);
    await screen.findByText("002121 · 已有保存报告 · 历史报告 · 待核");
    vi.mocked(getTradingAgentsResearch).mockRejectedValueOnce(new Error("read failed"));
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    await screen.findByText(/保留本股上次读取报告/);
    expect(panel.container.textContent).toContain("002121 行情原报告");
    let finish!: (value: TradingAgentsResearchView) => void;
    vi.mocked(getTradingAgentsResearch).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;})).mockResolvedValueOnce(saved("600276"));
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    await waitFor(() => expect(getTradingAgentsResearch).toHaveBeenCalledTimes(3));
    panel.rerender(<TradingAgentsResearchPanel symbol="600276" defaultOpen compact/>);
    await screen.findByText("600276 · 已有保存报告 · 历史报告 · 待核");
    await act(async () => {finish(saved());});
    expect(panel.container.textContent).not.toContain("002121 行情原报告");
    expect(panel.container.textContent).toContain("600276 行情原报告");
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("shows a completed cancellation as canceled while keeping a failed new run separate from a historical report", async () => {
    const result = saved();
    result.task = {id: "task", status: "canceled", cancel_requested: true, failed: false, created_at: "2026-10-20T10:00:00+08:00", finished_at: "2026-10-20T10:01:00+08:00", error: null};
    vi.mocked(getTradingAgentsResearch).mockResolvedValueOnce(result);
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen compact/>);
    await screen.findByText("002121 · 已取消 · 历史报告 · 待核");
    expect(screen.queryByText(/等待当前步骤结束/)).toBeNull();
    vi.mocked(getTradingAgentsResearch).mockResolvedValueOnce({...result, task: {...result.task!, status: "failed", failed: true, error: {code: "SourceError", message: "本轮来源失败"}}});
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    await screen.findByText("002121 · 研究失败 · 历史报告 · 待核");
    expect(screen.getByText(/本轮来源失败/)).toBeTruthy();
    expect(screen.getByText("查看最终研究结论")).toBeTruthy();
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("does not poll compact static reports but still supports an explicit reread", async () => {
    vi.useFakeTimers();
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(saved());
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen compact/>);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("002121 · 已有保存报告 · 历史报告 · 待核")).toBeTruthy();
    expect(getTradingAgentsResearch).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(180_000); });
    expect(getTradingAgentsResearch).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    await act(async () => { await Promise.resolve(); });
    expect(getTradingAgentsResearch).toHaveBeenCalledTimes(2);
    expect(createAgentTask).not.toHaveBeenCalled();
  });
});
