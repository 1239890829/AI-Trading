import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { getAgentTasks, getAgentTaskTypes, type AgentTask } from "@/lib/api";
import { TaskCenter } from "./task-center";

/**
 * P1-36：告警 escalate 落任务中心待办后的**可见性与处置回路**。
 *
 * 关键契约（纯前端，能锁住的只有这些）：
 * 1. 登记类条目（escalation/mutation/agenda）不在 task-types 里，必须靠本地标签兜底
 *    ——否则列表里显示成裸 "escalation"，用户不知道那是什么；
 * 2. 待办的正文在 `params` 里，必须渲染（否则"有了待办但看不到内容"）；
 * 3. `needs_confirm` 才出现处置按钮，且点「已处置」要带 outcome=done 调后端。
 */

const resolveAgentTask = vi.fn(async () => ({}) as AgentTask);
const cancelAgentTask = vi.fn(async () => ({}) as AgentTask);
const createAgentTask = vi.fn(async () => ({}) as AgentTask);

const ESCALATION_TODO: AgentTask = {
  id: "esc-42",
  type: "escalation",
  status: "needs_confirm",
  params: {
    source: "alert_triage",
    event_id: 42,
    summary: "600000 茅台突破｜全市场级风险",
    symbol: "600000",
    rule: "茅台突破",
    reason: "系统性异常，需人工确认",
    trigger_value: 12.5,
  },
  steps: [],
  result_ref: null,
  error: null,
  risk_level: "L1",
  created_by: "alert_triage",
  created_at: "2026-09-10T14:05:00",
  started_at: null,
  finished_at: null,
};

vi.mock("@/lib/api", () => ({
  getAgentTaskTypes: vi.fn(async () => [
    { type: "review", label: "生成复盘报告", risk: "L0", desc: "" },
    { type: "data_check", label: "数据体检", risk: "L0", desc: "" },
  ]),
  getAgentTasks: vi.fn(async () => [ESCALATION_TODO]),
  resolveAgentTask: (...args: unknown[]) => resolveAgentTask(...(args as [])),
  cancelAgentTask: (...args: unknown[]) => cancelAgentTask(...(args as [])),
  createAgentTask: (...args: unknown[]) => createAgentTask(...(args as [])),
}));

beforeEach(() => {
  resolveAgentTask.mockReset().mockResolvedValue({ ...ESCALATION_TODO, status: "succeeded", finished_at: "2026-09-10T14:06:00" });
  vi.mocked(getAgentTasks).mockReset().mockResolvedValue([ESCALATION_TODO]);
  vi.mocked(getAgentTaskTypes).mockReset().mockResolvedValue([
    { type: "review", label: "生成复盘报告", risk: "L0", desc: "" },
    { type: "data_check", label: "数据体检", risk: "L0", desc: "" },
  ]);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

async function renderWithTodo() {
  render(<TaskCenter />);
  // 列表里先出现待办（等首次拉取落定）
  const item = await screen.findByText("告警升级待办");
  fireEvent.click(item);
  return item;
}

it("groups a long execution trace without losing step conditions or changing disposition eligibility",async()=>{
  vi.mocked(getAgentTasks).mockResolvedValue([{...ESCALATION_TODO,steps:Array.from({length:20},(_,index)=>({index,name:`步骤 ${index}`,input_summary:`输入：尚未审议 ${index}.25 亿元`,output_summary:`输出：未执行交易 ${index}`,duration_ms:index,ok:index % 2 === 0}))}]);
  await renderWithTodo();
  const tabs=screen.getAllByRole("tab");
  expect(tabs.length).toBeLessThanOrEqual(6);
  const rendered=tabs.map(tab=>{fireEvent.click(tab);return screen.getByRole("tabpanel").textContent;}).join("");
  expect(rendered).toContain("尚未审议 19.25 亿元");
  expect(rendered).toContain("未执行交易 19");
  expect(screen.getByRole("button",{name:"已处置"})).toBeTruthy();
  expect(resolveAgentTask).not.toHaveBeenCalled();
});

describe("任务中心 · 告警升级待办（P1-36）", () => {
  it("登记类条目用本地标签，不显示裸 type 名", async () => {
    await renderWithTodo();
    expect(screen.queryByText("escalation")).toBeNull();
    expect(screen.getAllByText("告警升级待办").length).toBeGreaterThan(0);
  });

  it("待办正文（params 标量）渲染出来——有内容才叫待办", async () => {
    await renderWithTodo();
    expect(await screen.findByText("600000 茅台突破｜全市场级风险")).toBeTruthy();
    expect(screen.getByText("系统性异常，需人工确认")).toBeTruthy();
    expect(screen.getByText("茅台突破")).toBeTruthy();
  });

  it("needs_confirm 出现处置按钮，点「已处置」以 done 提交", async () => {
    await renderWithTodo();
    const done = await screen.findByRole("button", { name: "已处置" });
    const dismiss = screen.getByRole("button", { name: "忽略" });
    expect(dismiss).toBeTruthy();

    fireEvent.click(done);
    await waitFor(() => expect(resolveAgentTask).toHaveBeenCalledWith("esc-42", "done"));
  });

  it("处置按钮不出现于需要执行的任务（running 走取消，不是处置）", async () => {
    const { getAgentTasks } = await import("@/lib/api");
    // 每次读取都返回运行中任务，避免本用例把完成时间或轮询次数当成处置资格。
    const mocked = vi.mocked(getAgentTasks);
    const original = mocked.getMockImplementation();
    mocked.mockImplementation(async () => [
      { ...ESCALATION_TODO, id: "run-1", type: "review", status: "running" },
    ]);
    try {
      render(<TaskCenter />);
      // 同一文案也出现在「新建任务」按钮上：取 DOM 首个（左侧列表在前）
      const label = (await screen.findAllByText("生成复盘报告"))[0];
      fireEvent.click(label);
      await screen.findByRole("button", { name: "取消任务" });
      expect(screen.queryByRole("button", { name: "已处置" })).toBeNull();
    } finally {
      mocked.mockImplementation(original!);
    }
  });

  it("跨进程取消请求已持久化时显示取消中且禁止重复提交", async () => {
    const { getAgentTasks } = await import("@/lib/api");
    const mocked = vi.mocked(getAgentTasks);
    const original = mocked.getMockImplementation();
    mocked.mockImplementation(async () => [
      { ...ESCALATION_TODO, id: "run-cancel", type: "review", status: "running", cancel_requested_at: "2026-09-22T20:40:00" },
    ]);
    try {
      render(<TaskCenter />);
      const label = (await screen.findAllByText("生成复盘报告"))[0];
      fireEvent.click(label);
      const button = await screen.findByRole("button", { name: "取消中…" });
      expect((button as HTMLButtonElement).disabled).toBe(true);
      fireEvent.click(button);
      expect(cancelAgentTask).not.toHaveBeenCalledWith("run-cancel");
    } finally {
      mocked.mockImplementation(original!);
    }
  });

});

describe("任务中心 · 单一读取与失败恢复", () => {
  const running: AgentTask = { ...ESCALATION_TODO, id: "run-1", type: "review", status: "running" };
  async function flush(ms = 0) {
    await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
  }

  it("初次运行中结果不重复请求；只轮询任务，不反复读取静态入口", async () => {
    vi.useFakeTimers();
    vi.mocked(getAgentTasks).mockResolvedValue([running]);
    render(<TaskCenter />);
    await flush();
    expect(getAgentTasks).toHaveBeenCalledTimes(1);
    expect(getAgentTaskTypes).toHaveBeenCalledTimes(1);
    await flush(3_000);
    expect(getAgentTasks).toHaveBeenCalledTimes(2);
    expect(getAgentTaskTypes).toHaveBeenCalledTimes(1);
  });

  it("入口迟到不挡住独立任务列表", async () => {
    vi.mocked(getAgentTaskTypes).mockReturnValueOnce(new Promise(() => {}));
    render(<TaskCenter />);
    expect(await screen.findByText("告警升级待办")).toBeTruthy();
    expect(screen.getByRole("status", { name: "正在读取任务入口" })).toBeTruthy();
  });

  it("入口失败不清空已成功任务列表", async () => {
    vi.mocked(getAgentTaskTypes).mockRejectedValueOnce(new Error("types offline"));
    render(<TaskCenter />);
    expect(await screen.findByText("告警升级待办")).toBeTruthy();
    expect(screen.getByText(/任务入口读取失败，暂时无法创建/)).toBeTruthy();
  });

  it("首次任务失败也自动重试，成功终态停止轮询", async () => {
    vi.useFakeTimers();
    vi.mocked(getAgentTasks).mockRejectedValueOnce(new Error("initial offline"));
    render(<TaskCenter />);
    await flush();
    expect(screen.getByText(/任务列表加载失败/)).toBeTruthy();
    await flush(3_000);
    expect(getAgentTasks).toHaveBeenCalledTimes(2);
    expect(screen.getByText("告警升级待办")).toBeTruthy();
    await flush(9_000);
    expect(getAgentTasks).toHaveBeenCalledTimes(2);
    expect(getAgentTaskTypes).toHaveBeenCalledTimes(1);
  });

  it("轮询失败保留上次任务及选择，明确未确认；恢复后清错误", async () => {
    vi.useFakeTimers();
    vi.mocked(getAgentTasks).mockResolvedValueOnce([running])
      .mockRejectedValueOnce(new Error("poll offline"))
      .mockResolvedValue([{ ...running, status: "succeeded" }]);
    render(<TaskCenter />);
    await flush();
    fireEvent.click(screen.getAllByText("生成复盘报告")[0]);
    await flush(3_000);
    expect(screen.getByRole("button", { name: "取消任务" })).toBeTruthy();
    expect(screen.getByText(/上次读取结果，当前任务状态未确认/)).toBeTruthy();
    await flush(3_000);
    expect(screen.queryByText(/上次读取结果，当前任务状态未确认/)).toBeNull();
    expect(screen.queryByRole("button", { name: "取消任务" })).toBeNull();
    expect(screen.getAllByText("成功")).toHaveLength(2);
  });

  it("隐藏停止重试，恢复立即校准；卸载后不再轮询", async () => {
    vi.useFakeTimers();
    vi.mocked(getAgentTasks).mockResolvedValue([running]);
    const view = render(<TaskCenter />);
    await flush();
    await act(async () => {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await flush(9_000);
    expect(getAgentTasks).toHaveBeenCalledTimes(1);
    await act(async () => {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await flush();
    expect(getAgentTasks).toHaveBeenCalledTimes(2);
    view.unmount();
    await flush(9_000);
    expect(getAgentTasks).toHaveBeenCalledTimes(2);
  });

  it("恢复可见发起的慢读取与原重试时钟不重复请求", async () => {
    vi.useFakeTimers();
    let complete!: (tasks: AgentTask[]) => void;
    vi.mocked(getAgentTasks).mockResolvedValueOnce([running])
      .mockReturnValueOnce(new Promise(resolve => { complete = resolve; }))
      .mockResolvedValue([running]);
    render(<TaskCenter />);
    await flush();
    await flush(1_000);
    await act(async () => {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
      document.dispatchEvent(new Event("visibilitychange"));
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await flush(3_000);
    expect(getAgentTasks).toHaveBeenCalledTimes(2);
    await act(async () => complete([running]));
    await flush(2_999);
    expect(getAgentTasks).toHaveBeenCalledTimes(2);
    await flush(1);
    expect(getAgentTasks).toHaveBeenCalledTimes(3);
  });

  it.each(["success", "failure"])("取消后新读取先回，旧轮询晚回 %s 不污染终态", async late => {
    vi.useFakeTimers();
    let finishOld!: (tasks: AgentTask[]) => void;
    let failOld!: (error: Error) => void;
    vi.mocked(getAgentTasks).mockResolvedValueOnce([running])
      .mockReturnValueOnce(new Promise((resolve, reject) => { finishOld = resolve; failOld = reject; }))
      .mockResolvedValue([{ ...running, status: "canceled" }]);
    render(<TaskCenter />);
    await flush();
    fireEvent.click(screen.getAllByText("生成复盘报告")[0]);
    await flush(3_000);
    fireEvent.click(screen.getByRole("button", { name: "取消任务" }));
    await flush();
    expect(cancelAgentTask).toHaveBeenCalledWith("run-1");
    expect(screen.getAllByText("已取消")).toHaveLength(2);
    await act(async () => late === "success" ? finishOld([running]) : failOld(new Error("old poll error")));
    expect(screen.queryByText("执行中")).toBeNull();
    expect(screen.queryByText(/当前任务状态未确认/)).toBeNull();
    expect(screen.queryByRole("button", { name: "取消任务" })).toBeNull();
    expect(getAgentTaskTypes).toHaveBeenCalledTimes(1);
  });
});

describe("任务中心 · 处置回执与当前资格", () => {
  async function flush(ms = 0) {
    await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
  }
  function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (error: Error) => void;
    const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
    return { promise, resolve, reject };
  }
  async function selectTodo() {
    render(<TaskCenter />); await flush();
    fireEvent.click(screen.getByText("告警升级待办"));
  }
  const button = (name: "已处置" | "忽略") => screen.getByRole("button", {name}) as HTMLButtonElement;

  it("a local task read and failed calibration disable both dispositions but keep the draft", async () => {
    vi.useFakeTimers();
    const read = deferred<AgentTask[]>();
    vi.mocked(getAgentTasks).mockResolvedValueOnce([ESCALATION_TODO]).mockReturnValueOnce(read.promise).mockResolvedValue([ESCALATION_TODO]);
    await selectTodo();
    fireEvent.change(screen.getByLabelText("可选任务参数（JSON）"), {target: {value: '{"trade_date":"20261012"}'}});
    fireEvent.click(screen.getByRole("button", {name: "刷新"})); await flush();
    fireEvent.click(button("已处置")); fireEvent.click(button("忽略"));
    expect(resolveAgentTask).not.toHaveBeenCalled();
    expect(button("已处置").disabled).toBe(true); expect(button("忽略").disabled).toBe(true);
    await act(async () => read.reject(new Error("calibration offline")));
    expect(screen.getByText(/当前任务状态未确认/)).toBeTruthy();
    expect(button("已处置").disabled).toBe(true); expect(button("忽略").disabled).toBe(true);
    expect((screen.getByLabelText("可选任务参数（JSON）") as HTMLInputElement).value).toBe('{"trade_date":"20261012"}');
    await flush(3_000);
    expect(button("已处置").disabled).toBe(false); expect(button("忽略").disabled).toBe(false);
  });

  it("an in-flight disposition rejects both same and opposite repeated clicks", async () => {
    vi.useFakeTimers();
    const post = deferred<AgentTask>(); resolveAgentTask.mockReturnValue(post.promise);
    await selectTodo();
    fireEvent.click(button("已处置")); fireEvent.click(button("忽略")); fireEvent.click(button("已处置"));
    expect(resolveAgentTask).toHaveBeenCalledTimes(1);
    expect(resolveAgentTask).toHaveBeenCalledWith("esc-42", "done");
    expect(button("已处置").disabled).toBe(true); expect(button("忽略").disabled).toBe(true);
    await act(async () => post.resolve({...ESCALATION_TODO, status: "succeeded"}));
  });

  it.each(["done", "dismissed"] as const)("%s POST receipt stays separate from a slow durable GET and never permits the opposite action", async outcome => {
    vi.useFakeTimers();
    const read = deferred<AgentTask[]>();
    const confirmed: AgentTask = {...ESCALATION_TODO, status: outcome === "done" ? "succeeded" : "canceled", finished_at: "2026-09-10T14:06:00", params: {...ESCALATION_TODO.params, resolve: {outcome, note: "fixture disposition"}}};
    resolveAgentTask.mockResolvedValue(confirmed);
    vi.mocked(getAgentTasks).mockResolvedValueOnce([ESCALATION_TODO]).mockReturnValueOnce(read.promise);
    await selectTodo();
    fireEvent.click(button(outcome === "done" ? "已处置" : "忽略")); await flush();
    const receipt = screen.getByText(/处置请求已提交/);
    expect(receipt.textContent).toContain(outcome === "done" ? "成功" : "已取消");
    expect(receipt.textContent).toContain("持久状态待校准");
    expect(receipt.textContent).toContain("不表示业务动作已执行或消息已送达");
    fireEvent.click(button(outcome === "done" ? "忽略" : "已处置"));
    expect(resolveAgentTask).toHaveBeenCalledTimes(1);
    await act(async () => read.resolve([confirmed]));
    expect(screen.queryByRole("button", {name: "已处置"})).toBeNull();
    expect(screen.queryByRole("button", {name: "忽略"})).toBeNull();
    expect(screen.getByText(/持久状态已校准/)).toBeTruthy();
    expect(getAgentTasks).toHaveBeenCalledTimes(2);
  });

  it("POST success plus GET failure retains its real receipt and blocks repeat disposition until recovery", async () => {
    vi.useFakeTimers();
    const confirmed: AgentTask = {...ESCALATION_TODO, status: "succeeded", finished_at: "2026-09-10T14:06:00"};
    resolveAgentTask.mockResolvedValue(confirmed);
    vi.mocked(getAgentTasks).mockResolvedValueOnce([ESCALATION_TODO]).mockRejectedValueOnce(new Error("post calibration offline")).mockResolvedValue([confirmed]);
    await selectTodo();
    fireEvent.click(button("已处置")); await flush();
    expect(screen.getByText(/处置请求已提交/).textContent).toContain("成功");
    expect(screen.getByText(/当前任务状态未确认/)).toBeTruthy();
    fireEvent.click(button("忽略")); fireEvent.click(button("已处置"));
    expect(resolveAgentTask).toHaveBeenCalledTimes(1);
    await flush(3_000);
    expect(screen.queryByText(/当前任务状态未确认/)).toBeNull();
    expect(screen.queryByRole("button", {name: "已处置"})).toBeNull();
    expect(screen.getByText(/持久状态已校准/)).toBeTruthy();
    expect(getAgentTasks).toHaveBeenCalledTimes(3);
  });
});


it("IMP-025 · 代码产物成功只显示待审提案，正文保留核验边界", async () => {
  vi.mocked(getAgentTasks).mockResolvedValue([{ ...ESCALATION_TODO, id: "proposal", type: "mutation",
    status: "succeeded", params: { kind: "code_proposal", summary: "代码提案" },
    outcome: { label: "待审提案", note: "合并、加载与效果尚未确认。" } }]);
  render(<TaskCenter />);
  fireEvent.click(await screen.findByText("系统变更留痕"));
  expect(screen.getAllByText("待审提案")).toHaveLength(2);
  expect(screen.getByText("合并、加载与效果尚未确认。")).toBeTruthy();
  expect(screen.queryByText("成功")).toBeNull();
});

describe("任务入口读取状态", () => {
  it("读取失败结束骨架并给出重试路径，刷新后恢复入口", async () => {
    const {getAgentTaskTypes} = await import("@/lib/api");
    vi.mocked(getAgentTaskTypes).mockRejectedValueOnce(new Error("入口读取失败"));
    render(<TaskCenter />);
    expect(screen.getByRole("status", {name:"正在读取任务入口"})).toBeTruthy();
    expect(await screen.findByText(/任务入口读取失败，暂时无法创建/)).toBeTruthy();
    expect(screen.queryByRole("status", {name:"正在读取任务入口"})).toBeNull();
    expect(screen.queryByLabelText("可选任务参数（JSON）")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name:"刷新"}));
    expect(await screen.findByRole("button", {name:/生成复盘报告/})).toBeTruthy();
    expect(screen.queryByText(/任务入口读取失败，暂时无法创建/)).toBeNull();
  });

  it("有效空入口有明确空态，不留下无消费者参数输入", async () => {
    const {getAgentTaskTypes} = await import("@/lib/api");
    vi.mocked(getAgentTaskTypes).mockResolvedValueOnce([]);
    render(<TaskCenter />);
    expect(await screen.findByText("暂无可创建的任务。")).toBeTruthy();
    expect(screen.queryByRole("status", {name:"正在读取任务入口"})).toBeNull();
    expect(screen.queryByLabelText("可选任务参数（JSON）")).toBeNull();
  });
});

describe("公司依据任务 · 处理状态不等于判读通过", () => {
  function companyTask(result: unknown, extra: Partial<AgentTask> = {}): AgentTask {
    return {...ESCALATION_TODO, id: "company-batch", type: "company_evidence", status: "succeeded",
      params: {symbols: ["000725", "603197", "000070"]},
      result_ref: result as AgentTask["result_ref"], ...extra};
  }
  async function show(task: AgentTask) {
    vi.mocked(getAgentTasks).mockResolvedValue([task]);
    vi.mocked(getAgentTaskTypes).mockResolvedValue([{type: "company_evidence", label: "采集公司依据与判读", risk: "L0", desc: ""}]);
    render(<TaskCenter />);
    fireEvent.click((await screen.findAllByText("采集公司依据与判读"))[0]);
  }

  it("读取没有产物id的混合逐股结果，保留任务范围与真实调用数", async () => {
    await show(companyTask({kind: "company_evidence", state: "processed", model_calls: 1, symbols: [
      {symbol: "000725", state: "ready", result_key: "saved-key"},
      {symbol: "603197", state: "insufficient"},
      {symbol: "000070", state: "source_failed"},
    ]}));
    expect(screen.getAllByText("处理完成")).toHaveLength(2);
    const result = screen.getByRole("region", {name: "公司资料任务处理结果"});
    expect(result.textContent).toContain("000725 · 经营解释已保存");
    expect(result.textContent).toContain("603197 · 依据不足");
    expect(result.textContent).toContain("000070 · 来源采集失败");
    expect(result.textContent).toContain("模型调用：1 次");
    expect(result.textContent).toContain("不代表所有个股判读通过");
    expect(screen.getByText("000725、603197、000070")).toBeTruthy();
    expect(screen.queryByText(/产物：company_evidence/)).toBeNull();
    expect(screen.queryByText("成功")).toBeNull();
  });

  it.each([undefined, null])("缺少模型调用计数%s时不补为0", async model_calls => {
    await show(companyTask({kind: "company_evidence", state: "processed", symbols: [], model_calls}));
    const result = screen.getByRole("region", {name: "公司资料任务处理结果"});
    expect(result.textContent).toContain("模型调用：未记录");
    expect(result.textContent).not.toContain("模型调用：0 次");
    expect(result.textContent).toContain("未保存逐股处理结果");
  });

  it.each([
    ["no_candidates", "本轮没有当前候选"],
    ["no_due_candidates", "当前候选均未到处理时点"],
    ["disabled", "自动采集已停用"],
  ])("%s保持实际批次状态，不说完成全部判读", async (state, message) => {
    await show(companyTask({kind: "company_evidence", state, symbols: []}));
    expect(screen.getByRole("region", {name: "公司资料任务处理结果"}).textContent).toContain(message);
    expect(screen.queryByText(/全部判读通过/)).toBeNull();
    expect(screen.queryByText(/任务刚创建，等待执行/)).toBeNull();
  });

  it("失败结果与具体错误保留，不把未返回结果说成没有候选", async () => {
    await show(companyTask(null, {status: "failed", error: {code: "LLMError", message: "模型暂不可用", retryable: true}}));
    expect(screen.getByText("失败：模型暂不可用（LLMError）")).toBeTruthy();
    expect(screen.getByRole("region", {name: "公司资料任务处理结果"}).textContent).toContain("尚未保存本批逐股处理结果");
    expect(screen.queryByText("本轮没有当前候选")).toBeNull();
  });

  it("只显示有界合法标的，未知逐股状态不伪装成ready", async () => {
    await show(companyTask({kind: "company_evidence", state: "processed", symbols: [
      {symbol: "000725", state: "interrupted"}, {symbol: "603197", state: "future_state"},
      {symbol: "../private", state: "ready"},
    ]}, {params: {symbols: ["000725", "000725", "603197", "../private", 600000, ...Array.from({length: 12}, (_, i) => String(100000 + i))]}}));
    const result = screen.getByRole("region", {name: "公司资料任务处理结果"});
    expect(result.textContent).toContain("000725 · 处理中断");
    expect(result.textContent).toContain("603197 · 状态未确认");
    expect(result.textContent).not.toContain("../private");
    expect(screen.queryByText(/100006/)).toBeNull();
    expect(screen.queryByText(/\.\.\/private/)).toBeNull();
    expect(screen.getByText(/部分标的参数无效或超出展示上限/)).toBeTruthy();
  });

  it("原有报告任务仍显示原产物身份", async () => {
    vi.mocked(getAgentTasks).mockResolvedValue([{...ESCALATION_TODO, type: "review", status: "succeeded",
      result_ref: {kind: "report", id: "20261020"}}]);
    render(<TaskCenter />);
    fireEvent.click((await screen.findAllByText("生成复盘报告"))[0]);
    expect(screen.getByText("产物：report · 20261020")).toBeTruthy();
    expect(screen.queryByRole("region", {name: "公司资料任务处理结果"})).toBeNull();
  });
});
