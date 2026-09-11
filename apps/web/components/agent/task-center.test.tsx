import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { AgentTask } from "@/lib/api";
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

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function renderWithTodo() {
  render(<TaskCenter />);
  // 列表里先出现待办（等首次拉取落定）
  const item = await screen.findByText("告警升级待办");
  fireEvent.click(item);
  return item;
}

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
    vi.mocked(getAgentTasks).mockResolvedValueOnce([
      { ...ESCALATION_TODO, id: "run-1", type: "review", status: "running" },
    ]);
    render(<TaskCenter />);
    // 同一文案也出现在「新建任务」按钮上：取 DOM 首个（左侧列表在前）
    const label = (await screen.findAllByText("生成复盘报告"))[0];
    fireEvent.click(label);
    await screen.findByRole("button", { name: "取消任务" });
    expect(screen.queryByRole("button", { name: "已处置" })).toBeNull();
  });
});
