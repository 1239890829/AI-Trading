import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { getAgentParamChanges, getAgentRollbackReasons, applyAgentParamChange, rollbackAgentParamChange, type AgentParamChange } from "@/lib/api";
import { ParamsTab } from "./params-tab";

// 页面挂载即取数：把 api 层整体桩掉——本组测试只关心**容器与滚动的契约**，
// 不关心数据（数据展示另有断言价值时再单独测）。
vi.mock("@/lib/api", () => ({
  getAgentParams: vi.fn(async () => []),
  getAgentParamChanges: vi.fn(async () => []),
  getAgentParamSurvival: vi.fn(async () => ({
    decided: 0,
    applied: 0,
    rolled_back: 0,
    superseded: 0,
    still_effective: 0,
    survival_rate: null,
    insufficient: true,
    note: "",
    by_key: {},
    rollback_reasons: {},
    reason_labels: {},
  })),
  getAgentRollbackReasons: vi.fn(async () => []),
  createAgentParamChange: vi.fn(),
  applyAgentParamChange: vi.fn(),
  rollbackAgentParamChange: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.mocked(getAgentParamChanges).mockResolvedValue([]);
  vi.mocked(getAgentRollbackReasons).mockResolvedValue([]);
  vi.mocked(rollbackAgentParamChange).mockReset();
});

describe("参数配置页 · 滚动契约（2026-09-10 用户报「向下滚动不了」）", () => {
  /** The old whole-form scroll contract is replaced by two independently bounded lists.
   * jsdom verifies ownership; rendered dimensions and scrolling require browser evidence.
   */
  it("白名单与变更历史各自拥有阅读区，表单根不承担列表滚动", () => {
    const { container } = render(<ParamsTab />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain("h-full");
    expect(root.className).toContain("min-h-0");
    expect(root.className).not.toContain("overflow-y-auto");
    const columns = root.querySelector(".settings-columns");
    expect(columns).toBeTruthy();
    const whitelist = screen.getByRole("heading", {name: /参数白名单/}).closest("section")!;
    const history = screen.getByRole("heading", {name: /变更单历史/}).closest("section")!;
    expect(whitelist.parentElement).toBe(columns);
    expect(history.parentElement).toBe(columns);
    expect(whitelist.querySelector(".settings-list-scroll")).toBeTruthy();
    expect(history.querySelector(".settings-list-scroll")).toBeTruthy();
    expect(whitelist.querySelector(".settings-list-scroll")).not.toBe(history.querySelector(".settings-list-scroll"));
  });
});


function change(status: AgentParamChange["status"], allowed = false): AgentParamChange {
  return { id: 12, key: "picks_min_pick_score", before: 50, after: 58,
    source_type: allowed ? "manual" : "ai_suggestion", source_id: "fixture",
    evidence: { approved: true, manual_apply_allowed: true }, status,
    created_at: "2026-09-18T15:45:00", applied_at: null, rolled_back_at: null,
    rollback_reason: null, task_id: null, manual_apply_allowed: allowed,
    apply_block_reason: allowed ? null : "需要独立审阅，模型声明不构成批准" };
}

describe("IMP-046 · 参数候选与批准身份", () => {
  it.each(["draft", "shadow", "shadow_rejected"] as const)("%s不能凭证据中的approved显示生效按钮", async (status) => {
    vi.mocked(getAgentParamChanges).mockResolvedValue([change(status)]);
    render(<ParamsTab />);
    expect(await screen.findByText("需要独立审阅，模型声明不构成批准")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认生效" })).toBeNull();
    expect(applyAgentParamChange).not.toHaveBeenCalled();
  });
  it("影子与拒绝状态不退化成普通待确认", async () => {
    vi.mocked(getAgentParamChanges).mockResolvedValue([change("shadow"), { ...change("shadow_rejected"), id: 13 }]);
    render(<ParamsTab />);
    expect(await screen.findByText("影子候选（未生效）")).toBeTruthy();
    expect(screen.getByText("影子已拒绝")).toBeTruthy();
    expect(screen.queryByText("待确认")).toBeNull();
  });
  it("保留服务端允许的人工草稿确认动作", async () => {
    vi.mocked(getAgentParamChanges).mockResolvedValue([change("draft", true)]);
    vi.mocked(applyAgentParamChange).mockResolvedValue(change("applied"));
    render(<ParamsTab />);
    fireEvent.click(await screen.findByRole("button", { name: "确认生效" }));
    await waitFor(() => expect(applyAgentParamChange).toHaveBeenCalledWith(12));
  });
  it("旧接口未给许可字段时不自行批准", async () => {
    const row = change("draft", true); delete row.manual_apply_allowed;
    vi.mocked(getAgentParamChanges).mockResolvedValue([row]);
    render(<ParamsTab />);
    expect(await screen.findByText("待确认")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认生效" })).toBeNull();
  });
});


it("IMP-025 · 历史回滚缺回执时保留未知，真实归档使用后端结果", async () => {
  const base: AgentParamChange = { id: 1, key: "demo", before: 1, after: 2, source_type: "manual",
    source_id: "", evidence: null, status: "rolled_back", created_at: null, applied_at: null,
    rolled_back_at: null, rollback_reason: null, task_id: null };
  vi.mocked(getAgentParamChanges).mockResolvedValue([base, { ...base, id: 2,
    outcome: { label: "已归档，未恢复参数", note: "当前值由新变更拥有。" } }]);
  render(<ParamsTab />);
  expect(await screen.findByText("历史回滚结果待核实")).toBeTruthy();
  expect(screen.getByText("已归档，未恢复参数")).toBeTruthy();
  expect(screen.queryByText("已回滚")).toBeNull();
});

it("回滚原因菜单保留服务端代码，失败时保留归因草稿", async () => {
  vi.mocked(getAgentParamChanges).mockResolvedValue([change("applied")]);
  vi.mocked(getAgentRollbackReasons).mockResolvedValue([
    { code: "manual", label: "人工判断" },
    { code: "performance", label: "效果劣化" },
  ]);
  vi.mocked(rollbackAgentParamChange).mockRejectedValue(new Error("回滚失败：版本冲突"));
  render(<ParamsTab />);
  fireEvent.click(await screen.findByRole("button", { name: "回滚" }));
  const trigger = screen.getByRole("button", { name: "回滚原因：人工判断" });
  trigger.focus();
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("menuitemradio", { name: "效果劣化" }));
  fireEvent.change(screen.getByPlaceholderText("备注（可选）"), { target: { value: "复核实验结果" } });
  fireEvent.click(screen.getByRole("button", { name: "确认回滚" }));
  expect(await screen.findByText("回滚失败：版本冲突")).toBeTruthy();
  expect(rollbackAgentParamChange).toHaveBeenCalledExactlyOnceWith(12, {
    reason_code: "performance", note: "复核实验结果",
  });
  expect(screen.getByRole("button", { name: "回滚原因：效果劣化" })).toBeTruthy();
  expect((screen.getByPlaceholderText("备注（可选）") as HTMLInputElement).value).toBe("复核实验结果");
});
