import {act, cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {useDeviceAccess} from "@/components/access/device-access";
import {TradingAgentsResearchPanel} from "./tradingagents-research-panel";
import {cancelAgentTask, createAgentTask, getTradingAgentsResearch, recoverTradingAgentsResearch, type AgentTask, type TradingAgentsResearchView} from "@/lib/api/agent";

vi.mock("@/components/access/device-access", () => ({useDeviceAccess:vi.fn()}));
vi.mock("@/lib/api/agent", () => ({cancelAgentTask: vi.fn(), createAgentTask: vi.fn(), getTradingAgentsResearch: vi.fn(), recoverTradingAgentsResearch: vi.fn()}));
const view = (symbol = "002121"): TradingAgentsResearchView => ({
  symbol, enabled: false, executable: false, required_model_slots: 22,
  blockers: [{code: "disabled", message: "完整研究未启用"}, {code: "quality_not_admitted", message: "净增益尚未准入"}],
  budget: {date: "2026-10-20", limit: 8, used: 0, remaining: 8, scope: "autonomy_llm"},
  admission: {state: "not_admitted", method_sha256: "current-method"},
  verification: "unverified_full_graph_research", production_eligible: false, latest: null, task: null,
});

const sourceTaskId = "1".repeat(32), sourceRunId = "2".repeat(32), recoveryTaskId = "3".repeat(32);
function recoveryView(state: "not_checked" | "available" | "blocked" | "none" | "unknown" = "not_checked", symbol = "002121"): TradingAgentsResearchView {
  return {...saved(symbol), task: {id: sourceTaskId, status: "canceled", cancel_requested: true, failed: false,
    created_at: "2026-10-26T10:00:00+08:00", finished_at: "2026-10-26T10:01:00+08:00", error: null, result_run_id: null},
    recovery: {state, source_task_id: state === "none" ? null : sourceTaskId, run_id: state === "none" ? null : sourceRunId,
      blockers: state === "blocked" ? [{code: "checkpoint_changed", message: "原检查点身份已变"}] : []}};
}
function recoveryReceipt(): AgentTask {
  return {id: recoveryTaskId, type: "tradingagents_research", params: {symbol: "002121", native_recovery: {source_task_id: sourceTaskId, run_id: sourceRunId}},
    status: "queued", steps: [], result_ref: null, error: null, risk_level: "L0", created_by: "maintenance", created_at: null, started_at: null, finished_at: null};
}
function serveRecovery() {
  vi.mocked(getTradingAgentsResearch).mockImplementation(async (symbol, options) => recoveryView(options?.checkRecovery ? "available" : "not_checked", symbol));
}
async function checkRecovery() {
  fireEvent.click(await screen.findByRole("button", {name: "检查原任务恢复"}));
  return await screen.findByRole("button", {name: "恢复未完成步骤"});
}

describe("完整研究的维护恢复与任务关联", () => {
  it("ordinary saved-only readers never offer recovery or cancellation, even if a mocked response claims eligibility", async () => {
    vi.mocked(useDeviceAccess).mockReturnValue({device: {state: "active", maintenance: false}, open: vi.fn(), refresh: vi.fn(async () => {})});
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(recoveryView("available"));
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen allowStart />);
    await screen.findByText(/已保存完整七报告/);
    expect(screen.queryByRole("button", {name: /恢复|取消本次研究|检查启动/})).toBeNull();
    expect(getTradingAgentsResearch).toHaveBeenCalledExactlyOnceWith("002121", {savedOnly: true});
    expect(recoverTradingAgentsResearch).not.toHaveBeenCalled();
    expect(cancelAgentTask).not.toHaveBeenCalled();
  });

  it("checks the original checkpoint only on request and submits one exact recovery without recreating research", async () => {
    serveRecovery();
    let finish!: (task: AgentTask) => void;
    vi.mocked(recoverTradingAgentsResearch).mockImplementation(() => new Promise(resolve => {finish = resolve;}));
    render(<TradingAgentsResearchPanel symbol="002121" compact allowStart />);
    expect(screen.queryByRole("button", {name: "恢复未完成步骤"})).toBeNull();
    const resume = await checkRecovery();
    expect(getTradingAgentsResearch).toHaveBeenLastCalledWith("002121", {savedOnly: true, checkRecovery: true});
    expect(recoverTradingAgentsResearch).not.toHaveBeenCalled();
    expect(screen.getByText(/已保存原答不重新生成/)).toBeTruthy();
    fireEvent.click(resume); fireEvent.click(resume);
    expect(recoverTradingAgentsResearch).toHaveBeenCalledExactlyOnceWith(sourceTaskId, sourceRunId);
    await act(async () => finish(recoveryReceipt()));
    await screen.findByText(/持久状态待校准/);
    expect(screen.getByRole("button", {name: "恢复未完成步骤"})).toHaveProperty("disabled", true);
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("a maintenance recovery check does not strand the separate normal launch preflight", async () => {
    vi.mocked(getTradingAgentsResearch).mockImplementation(async (_symbol, options) => options?.checkRecovery
      ? {...recoveryView("available"), startup_conditions_not_checked:true}
      : {...view(), enabled:true, executable:true, blockers:[], startup_conditions_not_checked:options?.savedOnly === true, recovery:recoveryView().recovery});
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen />);
    fireEvent.click(await screen.findByRole("button", {name:"检查启动条件"}));
    await waitFor(()=>expect(screen.getByRole("button", {name:"开始完整研究"})).toHaveProperty("disabled",false));
    await checkRecovery();
    expect(screen.getByRole("button", {name:"开始完整研究"})).toHaveProperty("disabled",true);
    fireEvent.click(screen.getByRole("button", {name:"检查启动条件"}));
    await waitFor(()=>expect(getTradingAgentsResearch).toHaveBeenLastCalledWith("002121", {savedOnly:false}));
    expect(screen.queryByRole("button", {name:"恢复未完成步骤"})).toBeNull();
    expect(createAgentTask).not.toHaveBeenCalled();expect(recoverTradingAgentsResearch).not.toHaveBeenCalled();
  });

  it.each(["blocked", "unknown", "none"] as const)("server recovery state %s never becomes an actionable recovery", async state => {
    vi.mocked(getTradingAgentsResearch).mockImplementation(async (_symbol, options) => recoveryView(options?.checkRecovery ? state : "not_checked"));
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen />);
    fireEvent.click(await screen.findByRole("button", {name: "检查原任务恢复"}));
    await screen.findByLabelText("原任务恢复条件");
    expect(screen.queryByRole("button", {name: "恢复未完成步骤"})).toBeNull();
    expect(recoverTradingAgentsResearch).not.toHaveBeenCalled();
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("a failed eligibility refresh preserves originals while disabling stale recovery permission", async () => {
    serveRecovery();
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen />);
    await checkRecovery();
    vi.mocked(getTradingAgentsResearch).mockRejectedValue(new Error("recovery read unavailable"));
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    await screen.findByText(/保留本股上次读取报告/);
    expect(screen.getByText("恢复条件重读失败，当前资格未确认。")).toBeTruthy();
    expect(screen.getByRole("button", {name: "恢复未完成步骤"})).toHaveProperty("disabled", true);
    expect(screen.getByRole("tab", {name: /最终判断/})).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "恢复未完成步骤"}));
    expect(recoverTradingAgentsResearch).not.toHaveBeenCalled();
  });

  it("an unknown recovery submission stays locked after fresh reads and never regenerates preserved answers", async () => {
    let finishAutomaticRead!: (value: TradingAgentsResearchView) => void;
    let reads = 0;
    vi.mocked(getTradingAgentsResearch).mockImplementation(async (_symbol, options) => {
      if (++reads === 3) return new Promise(resolve => {finishAutomaticRead = resolve;});
      return recoveryView(options?.checkRecovery ? "available" : "not_checked");
    });
    vi.mocked(recoverTradingAgentsResearch).mockRejectedValue(new Error("response lost"));
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen />);
    fireEvent.click(await checkRecovery());
    await screen.findByText(/提交结果未确认，已停止重复操作/);
    await waitFor(() => expect(getTradingAgentsResearch).toHaveBeenCalledTimes(3));
    const reread = screen.getByRole("button", {name: "重新读取研究"});
    expect(reread).toHaveProperty("disabled", true);
    fireEvent.click(reread);
    expect(getTradingAgentsResearch).toHaveBeenCalledTimes(3);
    await act(async () => finishAutomaticRead(recoveryView("available")));
    await waitFor(() => expect(reread).toHaveProperty("disabled", false));
    expect(screen.getByRole("button", {name: "恢复未完成步骤"})).toHaveProperty("disabled", true);
    fireEvent.click(reread);
    await waitFor(() => expect(getTradingAgentsResearch).toHaveBeenCalledTimes(4));
    expect(screen.getByRole("button", {name: "恢复未完成步骤"})).toHaveProperty("disabled", true);
    expect(screen.getByRole("tab", {name: /最终判断/})).toBeTruthy();
    expect(recoverTradingAgentsResearch).toHaveBeenCalledTimes(1);
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("maintenance expiry retires checked recovery permission and reauthorization requires a new explicit check", async () => {
    serveRecovery();
    const panel = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen />);
    await checkRecovery();
    vi.mocked(useDeviceAccess).mockReturnValue({device: {state: "active", maintenance: false}, open: vi.fn(), refresh: vi.fn(async () => {})});
    panel.rerender(<TradingAgentsResearchPanel symbol="002121" defaultOpen />);
    await waitFor(() => expect(getTradingAgentsResearch).toHaveBeenLastCalledWith("002121", {savedOnly: true}));
    expect(screen.queryByRole("button", {name: "恢复未完成步骤"})).toBeNull();
    vi.mocked(useDeviceAccess).mockReturnValue({device: {state: "active", maintenance: true}, open: vi.fn(), refresh: vi.fn(async () => {})});
    panel.rerender(<TradingAgentsResearchPanel symbol="002121" defaultOpen />);
    expect(await screen.findByRole("button", {name: "检查原任务恢复"})).toBeTruthy();
    expect(screen.queryByRole("button", {name: "恢复未完成步骤"})).toBeNull();
    expect(recoverTradingAgentsResearch).not.toHaveBeenCalled();
  });

  it("late recovery permission from another stock never enables the current stock", async () => {
    let finish!: (value: TradingAgentsResearchView) => void;
    vi.mocked(getTradingAgentsResearch).mockImplementation(async (symbol, options) =>
      symbol === "002121" && options?.checkRecovery ? new Promise(resolve => {finish = resolve;}) : recoveryView("not_checked", symbol));
    const panel = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen />);
    fireEvent.click(await screen.findByRole("button", {name: "检查原任务恢复"}));
    panel.rerender(<TradingAgentsResearchPanel symbol="600276" defaultOpen />);
    await screen.findByText(/600276 · 已保存完整七报告/);
    await act(async () => finish(recoveryView("available")));
    expect(screen.queryByRole("button", {name: "恢复未完成步骤"})).toBeNull();
    expect(recoverTradingAgentsResearch).not.toHaveBeenCalled();
  });

  it("a late recovery submission receipt cannot calibrate or poll another stock", async () => {
    serveRecovery();
    let finish!: (task: AgentTask) => void;
    vi.mocked(recoverTradingAgentsResearch).mockImplementation(() => new Promise(resolve => {finish = resolve;}));
    const panel = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen />);
    fireEvent.click(await checkRecovery());
    panel.rerender(<TradingAgentsResearchPanel symbol="600276" defaultOpen />);
    await screen.findByText(/600276 · 已保存完整七报告/);
    const reads = vi.mocked(getTradingAgentsResearch).mock.calls.length;
    await act(async () => finish(recoveryReceipt()));
    expect(getTradingAgentsResearch).toHaveBeenCalledTimes(reads);
    expect(screen.queryByText(/持久状态待校准/)).toBeNull();
    expect(screen.queryByRole("button", {name: "恢复未完成步骤"})).toBeNull();
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("cooperative cancellation remains pending until the server reports a terminal state", async () => {
    const running = {...recoveryView(), task: {...recoveryView().task!, status: "running" as const, cancel_requested: false, finished_at: null}};
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(running);
    let finish!: (task: AgentTask) => void;
    vi.mocked(cancelAgentTask).mockImplementation(() => new Promise(resolve => {finish = resolve;}));
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen />);
    const cancel = await screen.findByRole("button", {name: "取消本次研究"});
    fireEvent.click(cancel); fireEvent.click(cancel);
    expect(cancelAgentTask).toHaveBeenCalledExactlyOnceWith(sourceTaskId);
    await act(async () => finish({...recoveryReceipt(), id: sourceTaskId, params: {symbol: "002121"}, status: "running"}));
    await screen.findByRole("button", {name: "取消处理中"});
    expect(screen.getByText(/取消请求已记录，等待当前步骤结束/)).toBeTruthy();
    expect(screen.queryByText(/本股最近任务：已取消/)).toBeNull();
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(recoveryView());
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    await screen.findByText(/本股最近任务：已取消/);
    expect(screen.queryByRole("button", {name: /取消本次研究|取消处理中/})).toBeNull();
    expect(cancelAgentTask).toHaveBeenCalledTimes(1);
  });

  it.each([false, true])("saved report association uses the task's exact run identity, matching=%s", async matches => {
    const result = recoveryView();
    result.latest!.run_id = sourceRunId;
    result.task = {...result.task!, status: "succeeded", result_run_id: matches ? sourceRunId : "4".repeat(32)};
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(result);
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen />);
    expect((await screen.findByLabelText("报告与任务关联")).textContent).toBe(matches ? "本次任务的保存报告。" : "上次已保存报告，不是本次任务结果。");
    expect(createAgentTask).not.toHaveBeenCalled();
    expect(recoverTradingAgentsResearch).not.toHaveBeenCalled();
  });

  it("a foreign recovery receipt remains unknown and cannot unlock a repeat command", async () => {
    serveRecovery();
    vi.mocked(recoverTradingAgentsResearch).mockResolvedValue({...recoveryReceipt(), params: {symbol: "002121", native_recovery: {source_task_id: "4".repeat(32), run_id: sourceRunId}}});
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen />);
    fireEvent.click(await checkRecovery());
    await screen.findByText(/恢复回执原任务或运行不匹配/);
    expect(screen.getByRole("button", {name: "恢复未完成步骤"})).toHaveProperty("disabled", true);
    expect(recoverTradingAgentsResearch).toHaveBeenCalledTimes(1);
    expect(createAgentTask).not.toHaveBeenCalled();
  });
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
const recovered = (symbol = "002121"): TradingAgentsResearchView => {
  const value = saved(symbol);
  value.latest!.method_sha256 = "b".repeat(64);
  value.latest!.recovery_lineage = {version: 1, source_task_id: "1".repeat(32), source_run_id: "2".repeat(32), recovery_task_id: "3".repeat(32),
    source_method_sha256: "a".repeat(64), recovery_method_sha256: "b".repeat(64), reused_original_outputs: 9,
    new_call_from: 11, new_call_to: 24, inherited_unknown_usage_count: 1};
  return value;
};
beforeEach(() => {vi.clearAllMocks(); vi.mocked(useDeviceAccess).mockReturnValue({device:{state:"active",maintenance:true},open:vi.fn(),refresh:vi.fn(async()=>{})}); vi.mocked(getTradingAgentsResearch).mockResolvedValue(view());});
afterEach(() => {cleanup(); vi.useRealTimers();});

describe("完整TradingAgents研究读者", () => {
  it("makes an exact server-confirmed completed-selection reuse visible without implying a new report", async () => {
    const result = saved();
    result.task = {id: "task", status: "succeeded", cancel_requested: false, failed: false,
      created_at: "2026-10-26T20:00:00+08:00", finished_at: "2026-10-26T20:00:01+08:00", error: null,
      result_run_id: result.latest!.run_id, reused_identical_selection: true};
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(result);
    render(<TradingAgentsResearchPanel symbol="002121" compact allowStart={false}/>);
    expect((await screen.findByLabelText("研究复用结果")).textContent).toContain("没有重复生成");
    expect(createAgentTask).not.toHaveBeenCalled();
  });
  it("confirms a same-version reread without posting a generation or losing the saved report", async () => {
    vi.mocked(getTradingAgentsResearch).mockImplementation(async () => saved());
    render(<TradingAgentsResearchPanel symbol="002121" compact allowStart={false}/>);
    await screen.findByText("002121 · 已有保存报告 · 历史报告 · 待核");
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    expect((await screen.findByLabelText("研究重读结果")).textContent).toContain("仍是同一保存版本");
    expect(getTradingAgentsResearch).toHaveBeenLastCalledWith("002121", {savedOnly: true});
    expect(createAgentTask).not.toHaveBeenCalled();
    expect(screen.getByRole("tab", {name: /最终判断/})).toBeTruthy();
  });

  it("shows concrete generation blockers before the retained report without hiding them in disclosure", async () => {
    vi.mocked(getTradingAgentsResearch).mockImplementation(async (_symbol, options) => ({...saved(),
      startup_conditions_not_checked: options?.savedOnly === true,
      blockers: [{code: "usage_unknown", message: "有一次模型用量尚未核实"},
        {code: "research_not_authorized", message: "原授权尚未承接当前方法"}]}));
    render(<TradingAgentsResearchPanel symbol="002121" compact allowStart/>);
    fireEvent.click(await screen.findByRole("button", {name: "检查启动条件"}));
    const blockers = await screen.findByLabelText("新研究启动阻断");
    expect(blockers.textContent).toContain("有一次模型用量尚未核实");
    expect(blockers.textContent).toContain("原授权尚未承接当前方法");
    expect(blockers.closest("details")).toBeNull();
    expect(screen.getByRole("button", {name: "开始完整研究"})).toHaveProperty("disabled", true);
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("discards late manual-read feedback after maintenance permission changes the read owner", async () => {
    const panel = render(<TradingAgentsResearchPanel symbol="002121" compact allowStart/>);
    await screen.findByRole("button", {name: "检查启动条件"});
    let finish!: (value: TradingAgentsResearchView) => void;
    vi.mocked(getTradingAgentsResearch).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    fireEvent.click(screen.getByRole("button", {name: "检查启动条件"}));
    await waitFor(() => expect(getTradingAgentsResearch).toHaveBeenLastCalledWith("002121", {savedOnly: false}));
    await act(async () => {finish({...saved(), executable: true, blockers: []});});
    vi.mocked(getTradingAgentsResearch).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    vi.mocked(useDeviceAccess).mockReturnValue({device: {state: "active", maintenance: false}, open: vi.fn(), refresh: vi.fn(async () => {})});
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(saved());
    panel.rerender(<TradingAgentsResearchPanel symbol="002121" compact allowStart/>);
    await waitFor(() => expect(getTradingAgentsResearch).toHaveBeenLastCalledWith("002121", {savedOnly: true}));
    await act(async () => {finish(saved());});
    expect(screen.queryByLabelText("研究重读结果")).toBeNull();
    expect(createAgentTask).not.toHaveBeenCalled();
  });
  it("ordinary paired readers can reuse full saved reports but cannot inspect launch gates or submit tasks", async () => {
    const open=vi.fn();
    vi.mocked(useDeviceAccess).mockReturnValue({device:{state:"active",maintenance:false},open,refresh:vi.fn(async()=>{})});
    vi.mocked(getTradingAgentsResearch).mockResolvedValue({...saved(),enabled:true,executable:true,blockers:[]});
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen allowStart/>);
    await screen.findByRole("tab",{name:/最终判断/});
    expect(screen.queryByRole("button",{name:"检查启动条件"})).toBeNull();
    expect(screen.queryByRole("button",{name:"开始完整研究"})).toBeNull();
    expect(screen.getByText(/生成新研究需要维护授权/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"设备与权限"}));
    expect(open).toHaveBeenCalledTimes(1);
    expect(getTradingAgentsResearch).toHaveBeenCalledTimes(1);
    expect(getTradingAgentsResearch).toHaveBeenCalledWith("002121",{savedOnly:true});
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("an explicitly enabled compact entry starts only after maintenance preflight and a user command", async () => {
    vi.mocked(getTradingAgentsResearch).mockResolvedValue({...view(),enabled:true,executable:true,blockers:[]});
    vi.mocked(createAgentTask).mockResolvedValue({id:"started",type:"tradingagents_research",params:{symbol:"002121"},status:"queued",steps:[],result_ref:null,error:null,risk_level:"L0",created_by:"user",created_at:null,started_at:null,finished_at:null});
    render(<TradingAgentsResearchPanel symbol="002121" compact allowStart/>);
    await screen.findByRole("button",{name:"检查启动条件"});
    expect(createAgentTask).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button",{name:"检查启动条件"}));
    const start=screen.getByRole("button",{name:"开始完整研究"});
    await waitFor(()=>expect((start as HTMLButtonElement).disabled).toBe(false));
    expect(createAgentTask).not.toHaveBeenCalled();
    fireEvent.click(start);
    await waitFor(()=>expect(createAgentTask).toHaveBeenCalledTimes(1));
    expect(createAgentTask).toHaveBeenCalledWith("tradingagents_research",{symbol:"002121"});
  });
  it("shows explicit selected-stock research authorization without a call quota or an effect-admission claim", async () => {
    vi.mocked(getTradingAgentsResearch).mockResolvedValue({...view(), enabled:true, executable:true, blockers:[],
      admission:{state:"research_only_authorized",method_sha256:"current-method"},
      budget:{date:"2026-10-21",limit:null,used:31,remaining:null,scope:"selected_stock_research"}});
    const panel=render(<TradingAgentsResearchPanel symbol="002121" compact defaultOpen/>);
    await screen.findByText("002121 · 暂无可读完整报告");
    fireEvent.click(screen.getByText("运行条件与承接状态"));
    expect(panel.container.textContent).toContain("已授权入选后完整研究");
    expect(panel.container.textContent).toContain("不代表选股或投资效果已通过验证");
    expect(panel.container.textContent).not.toMatch(/预算不足|剩余|所需模型余量|null次/);
    expect(createAgentTask).not.toHaveBeenCalled();
  });
  it("opens an ordinary read only on request and exposes actual blockers without running models", async () => {
    render(<TradingAgentsResearchPanel symbol="002121"/>);
    expect(getTradingAgentsResearch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name: /TradingAgents 完整研究/}));
    await screen.findByText(/当前未启用完整研究/);
    expect(getTradingAgentsResearch).toHaveBeenCalledWith("002121", {savedOnly:true});
    fireEvent.click(screen.getByText(/查看启动阻断原因/));
    expect(screen.getByText("净增益尚未准入")).toBeTruthy();
    expect((screen.getByRole("button", {name: "开始完整研究"}) as HTMLButtonElement).disabled).toBe(true);
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("retains all seven full originals, source cutoff, method and stale identity", async () => {
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(saved());
    const {container} = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen/>);
    await screen.findByText(/已保存完整七报告/);
    for (const [key, label] of [["market_report", "行情"], ["sentiment_report", "情绪"], ["news_report", "新闻"], ["fundamentals_report", "公司"], ["investment_plan", "多空研究"], ["trader_investment_plan", "交易研究"], ["final_trade_decision", "最终判断"]] as const) {
      fireEvent.click(screen.getByRole("tab", {name: new RegExp(label)}));
      fireEvent.click(screen.getByRole("button", {name: `核对${label}完整原文`}));
      expect(screen.getByLabelText(`${label}完整原文`).querySelector("pre")?.textContent).toBe(saved().latest!.reports[key]);
    }
    expect(container.textContent).toContain("材料截点：2026-10-19 15:00");
    expect(container.textContent).toContain("历史生成报告，不代表今天已重新判读");
    expect(container.textContent).toContain("报告方法与当前执行器不同");
    expect(container.textContent).toContain("old-method");
    expect(container.textContent).toContain("不构成买卖建议");
    expect(screen.queryByLabelText("原答复用与接续")).toBeNull();
    expect(screen.queryByLabelText("研究用量缺口")).toBeNull();
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it.each([false, true])("keeps recovered originals, frozen cutoff and inherited unknown visible in compact=%s without starting recovery", async compact => {
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(recovered());
    const panel = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen compact={compact} allowStart={false}/>);
    expect((await screen.findByLabelText("原答复用与接续")).textContent).toContain("已复用9份原答，仅接续未完成步骤；材料截点保持不变");
    expect(screen.getByLabelText("研究用量缺口").textContent).toBe("仍有1次历史用量未知，总用量与费用不完整。");
    expect(panel.container.textContent).toContain("材料截点：2026-10-19 15:00");
    fireEvent.click(screen.getByText("报告版本与核验身份"));
    const source = screen.getByLabelText("恢复任务与方法来源");
    for (const value of ["1".repeat(32), "2".repeat(32), "3".repeat(32), "a".repeat(64), "b".repeat(64), "新增调用编号：11–24"]) expect(source.textContent).toContain(value);
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    await waitFor(() => expect(getTradingAgentsResearch).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText("研究用量缺口").textContent).toContain("用量未知");
    expect(screen.queryByRole("button", {name: /启动|恢复|接续/})).toBeNull();
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("does not invent reused answers or a complete bill from zero inherited unknown", async () => {
    const result = recovered();
    result.latest!.recovery_lineage = {...result.latest!.recovery_lineage!, reused_original_outputs: 0, inherited_unknown_usage_count: 0};
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(result);
    const panel = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen compact/>);
    expect((await screen.findByLabelText("原答复用与接续")).textContent).toContain("从未完成步骤接续，未复用已保存原答");
    expect(screen.queryByLabelText("研究用量缺口")).toBeNull();
    expect(panel.container.textContent).not.toMatch(/费用已完整|总用量已完整|重新判读全部|第11步/);
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("retires a delayed old stock response and closes old report disclosures on stock change", async () => {
    let finish!: (value: TradingAgentsResearchView) => void;
    vi.mocked(getTradingAgentsResearch).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;})).mockResolvedValueOnce(saved("600276"));
    const panel = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen/>);
    await waitFor(() => expect(getTradingAgentsResearch).toHaveBeenCalledWith("002121", {savedOnly:true}));
    panel.rerender(<TradingAgentsResearchPanel symbol="600276" defaultOpen/>);
    await screen.findByText(/600276 · 已保存/);
    await act(async () => {finish(recovered());});
    expect(panel.container.textContent).not.toContain("002121 行情原报告");
    fireEvent.click(screen.getByRole("tab", {name: /^行情/}));
    expect(panel.container.textContent).toContain("600276 行情原报告");
    expect(screen.queryByLabelText("原答复用与接续")).toBeNull();
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
    fireEvent.click(screen.getByRole("tab", {name: /^行情/}));
    expect(panel.container.textContent).toContain("002121 行情原报告");
    expect((screen.getByRole("button", {name: "开始完整研究"}) as HTMLButtonElement).disabled).toBe(true);
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it.each(["queued", "running", "canceled", "failed"] as const)("shows %s separately from a retained report and never equates cancellation request with completion", async status => {
    const result = saved(); result.task = {id: "task", status, cancel_requested: status === "running", failed: status === "failed", created_at: "2026-10-20T10:00:00+08:00", finished_at: null, error: status === "failed" ? {code: "SourceError", message: "本轮失败，旧报告保留"} : null};
    vi.mocked(getTradingAgentsResearch).mockResolvedValue(result);
    const panel = render(<TradingAgentsResearchPanel symbol="002121" defaultOpen/>);
    await screen.findByText(/已保存完整七报告/);
    const labels = {queued: "排队中", running: "取消请求已记录，等待当前步骤结束", canceled: "已取消", failed: "研究失败"};
    expect(panel.container.textContent).toContain(labels[status]);
    fireEvent.click(screen.getByRole("tab", {name: /^行情/}));
    expect(panel.container.textContent).toContain("002121 行情原报告");
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("submits exactly the current symbol once, keeps unknown post calibration, and accepts no model/path fields", async () => {
    vi.mocked(getTradingAgentsResearch).mockResolvedValue({...view(), enabled: true, executable: true, blockers: []});
    let finish!: (task: AgentTask) => void;
    vi.mocked(createAgentTask).mockImplementation(() => new Promise(resolve => {finish = resolve;}));
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen/>);
    await screen.findByRole("button", {name: "检查启动条件"});
    fireEvent.click(screen.getByRole("button", {name: "检查启动条件"}));
    const button = await screen.findByRole("button", {name: "开始完整研究"});
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button); fireEvent.click(button);
    expect(createAgentTask).toHaveBeenCalledTimes(1);
    expect(createAgentTask).toHaveBeenCalledWith("tradingagents_research", {symbol: "002121"});
    await act(async () => {finish({id: "new-task", type: "tradingagents_research", params: {symbol: "002121"}, status: "queued", steps: [], result_ref: null, error: null, risk_level: "L0", created_by: "operator", created_at: "2026-10-20T21:00:00+08:00", started_at: null, finished_at: null});});
    await screen.findByText(/持久状态待校准/);
    expect((screen.getByRole("button", {name: "开始完整研究"}) as HTMLButtonElement).disabled).toBe(true);
  });

  it("does not request equity research for an index or hidden stock panel", () => {
    const panel = render(<TradingAgentsResearchPanel symbol="sh000001" defaultOpen/>);
    expect(panel.container.textContent).toBe("");
    panel.rerender(<TradingAgentsResearchPanel symbol="002121" defaultOpen active={false}/>);
    expect(screen.queryByRole("region", {name: "TradingAgents完整研究"})).toBeNull();
    expect(getTradingAgentsResearch).not.toHaveBeenCalled();
  });

  it("reads saved roles immediately inside an opened compact reader without offering a model start", async () => {
    vi.mocked(getTradingAgentsResearch).mockResolvedValue({...view(), enabled: true, executable: true, blockers: []});
    render(<TradingAgentsResearchPanel symbol="002121" compact/>);
    expect(screen.getByRole("button", {name: "TradingAgents 研究记录 · 收起"})).toBeTruthy();
    await screen.findByText("002121 · 暂无可读完整报告");
    expect(screen.queryByRole("button", {name: "开始完整研究"})).toBeNull();
    expect(screen.queryByRole("link", {name: "任务中心查看或取消"})).toBeNull();
    expect(screen.getByText("运行条件与承接状态").closest("details")?.open).toBe(false);
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
    expect(screen.getByRole("tab", {name: /最终判断/}).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByLabelText("最终判断完整原文")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "核对最终判断完整原文"}));
    expect(screen.getByLabelText("最终判断完整原文").textContent).toContain("最终原文不证明未来收益");
    for (const [key, label] of [["market_report", "行情"], ["sentiment_report", "情绪"], ["news_report", "新闻"], ["fundamentals_report", "公司"], ["investment_plan", "多空研究"], ["trader_investment_plan", "交易研究"]] as const) {
      fireEvent.click(screen.getByRole("tab", {name: new RegExp(label)}));
      expect(screen.queryByLabelText("最终判断完整原文")).toBeNull();
      fireEvent.click(screen.getByRole("button", {name: `核对${label}完整原文`}));
      expect(screen.getByLabelText(`${label}完整原文`).querySelector("pre")?.textContent).toBe(result.latest!.reports[key]);
    }
    expect(panel.container.textContent).toContain("材料截点：2026-10-19 15:00");
    expect(panel.container.textContent).toContain("old-method");
    expect(screen.getByText(/自动来源或引用检查有2项未通过/)).toBeTruthy();
    expect(screen.getByText(/报告方法与当前执行器不同/)).toBeTruthy();
    expect(createAgentTask).not.toHaveBeenCalled();
  });

  it("distinguishes compact initial loading, failed reads and runtime blockers from an absent report", async () => {
    let finish!: (value: TradingAgentsResearchView) => void;
    vi.mocked(getTradingAgentsResearch).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    render(<TradingAgentsResearchPanel symbol="002121" defaultOpen compact/>);
    expect(screen.getByText("002121 · 读取中")).toBeTruthy();
    expect(screen.queryByText("002121 · 暂无可读完整报告")).toBeNull();
    await act(async () => {finish({...view(), blockers: [{code: "driver_unavailable", message: "完整执行器尚未装配"}]});});
    await screen.findByText("002121 · 暂无可读完整报告");
    fireEvent.click(screen.getByText("运行条件与承接状态"));
    fireEvent.click(screen.getByText(/查看启动阻断原因/));
    expect(screen.getByText("完整执行器尚未装配")).toBeTruthy();
    vi.mocked(getTradingAgentsResearch).mockRejectedValue(new Error("read failed"));
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    await screen.findByText("002121 · 读取失败");
    expect(screen.queryByText("002121 · 暂无可读完整报告")).toBeNull();
    expect(screen.queryByText(/预算不足|所需模型余量/)).toBeNull();
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
    fireEvent.click(screen.getByRole("tab", {name: /^行情/}));
    expect(panel.container.textContent).toContain("002121 行情原报告");
    let finish!: (value: TradingAgentsResearchView) => void;
    vi.mocked(getTradingAgentsResearch).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;})).mockResolvedValueOnce(saved("600276"));
    fireEvent.click(screen.getByRole("button", {name: "重新读取研究"}));
    await waitFor(() => expect(getTradingAgentsResearch).toHaveBeenCalledTimes(3));
    panel.rerender(<TradingAgentsResearchPanel symbol="600276" defaultOpen compact/>);
    await screen.findByText("600276 · 已有保存报告 · 历史报告 · 待核");
    await act(async () => {finish(saved());});
    expect(panel.container.textContent).not.toContain("002121 行情原报告");
    fireEvent.click(screen.getByRole("tab", {name: /^行情/}));
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
    expect(screen.getByRole("tab", {name: /最终判断/})).toBeTruthy();
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
