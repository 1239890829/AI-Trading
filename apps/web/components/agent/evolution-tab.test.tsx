import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { EvolutionTab } from "./evolution-tab";
import { getAgentExperiments, getAgentAgenda, getAgentAgendas, runAgentAgenda, type AgentAgenda, type AgentAgendaItem } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getAgentAgenda: vi.fn(),
  getAgentAgendas: vi.fn(async () => []),
  getAgentExperiments: vi.fn(async () => []),
  runAgentAgenda: vi.fn(),
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  vi.mocked(getAgentAgenda).mockReset().mockResolvedValue(null);
  vi.mocked(getAgentAgendas).mockReset().mockResolvedValue([]);
  vi.mocked(getAgentExperiments).mockReset().mockResolvedValue([]);
  vi.mocked(runAgentAgenda).mockReset();
});

function fixture(cls: AgentAgendaItem["class"], status: AgentAgendaItem["status"]): AgentAgenda {
  return {
    id: 1, date: "2026-09-18", status: "executed", inputs: {}, budget: {}, error: null,
    created_at: "2026-09-18T15:45:00", finished_at: "2026-09-18T15:45:01",
    items: [{ class: cls, status, finding: "提案状态夹具", evidence: {}, action: "检查",
      expected_effect: "可审阅", verification: "隔离测试", priority: 1,
      result: cls === "C" ? "补丁未应用，未运行宿主测试" : "文档已写入" }],
  };
}

describe("IMP-046 · 代码提案不冒充实际执行", () => {
  it("新提案显示待审，议程完成仅表示已处理", async () => {
    vi.mocked(getAgentAgenda).mockResolvedValue(fixture("C", "proposed"));
    render(<EvolutionTab />);
    expect(await screen.findByText("待审提案")).toBeTruthy();
    expect(screen.getByText("已处理")).toBeTruthy();
    expect(screen.queryByText("已执行")).toBeNull();
    expect(screen.getByText("补丁未应用，未运行宿主测试")).toBeTruthy();
  });
  it("历史C类executed不得显示为已执行或已落地", async () => {
    vi.mocked(getAgentAgenda).mockResolvedValue(fixture("C", "executed"));
    render(<EvolutionTab />);
    expect(await screen.findByText("历史代码记录（待复核）")).toBeTruthy();
    expect(screen.queryByText("已执行")).toBeNull();
  });
  it("保留B类真实文档执行结果", async () => {
    vi.mocked(getAgentAgenda).mockResolvedValue(fixture("B", "executed"));
    render(<EvolutionTab />);
    expect(await screen.findByText("已执行")).toBeTruthy();
    expect(screen.getByText("文档已写入")).toBeTruthy();
    expect(screen.queryByText("待审提案")).toBeNull();
  });
});


describe("IMP-046 · 参数入影子不冒充生效", () => {
  it("当前A类处理显示未生效", async () => {
    const agenda = fixture("A", "executed");
    agenda.items[0] = { ...agenda.items[0], execution_scope: "shadow_only", runtime_applied: false, review_required: true };
    vi.mocked(getAgentAgenda).mockResolvedValue(agenda);
    render(<EvolutionTab />);
    expect(await screen.findByText("已入影子（未生效）")).toBeTruthy();
    expect(screen.queryByText("已执行")).toBeNull();
  });
  it("历史A类executed不推定曾生效或从未生效", async () => {
    vi.mocked(getAgentAgenda).mockResolvedValue(fixture("A", "executed"));
    render(<EvolutionTab />);
    expect(await screen.findByText("历史参数记录（待复核）")).toBeTruthy();
    expect(screen.queryByText("已执行")).toBeNull();
    expect(screen.queryByText("已入影子（未生效）")).toBeNull();
  });
});


it("IMP-025 · 实验只归档未恢复的结果不冒充回滚或验证通过", async () => {
  vi.mocked(getAgentExperiments).mockResolvedValue([{ id: 1, change_id: 1, param_key: "demo",
    hypothesis: "test", baseline: {}, status: "rolled_back", result: { conclusion: "只归档" },
    outcome: { label: "已归档，未恢复参数", note: "因果效果未验证" },
    verification_date: null, extensions: 0, created_at: null, concluded_at: null }]);
  render(<EvolutionTab />);
  expect(await screen.findByText("已归档，未恢复参数")).toBeTruthy();
  expect(screen.queryByText("已自动回滚")).toBeNull();
  expect(screen.queryByText("验证通过")).toBeNull();
});

describe("进化议程 · 独立读取", () => {
  it("慢实验记录不阻塞已成功今日议程与历史", async () => {
    vi.mocked(getAgentAgenda).mockResolvedValueOnce(fixture("B", "executed"));
    vi.mocked(getAgentAgendas).mockResolvedValueOnce([fixture("B", "executed")]);
    vi.mocked(getAgentExperiments).mockReturnValueOnce(new Promise(() => {}));
    render(<EvolutionTab />);
    expect(await screen.findByText("文档已写入")).toBeTruthy();
    expect(screen.getByText("2026-09-18")).toBeTruthy();
  });

  it("历史失败不把成功今日议程改成尚未生成", async () => {
    vi.mocked(getAgentAgenda).mockResolvedValueOnce(fixture("B", "executed"));
    vi.mocked(getAgentAgendas).mockRejectedValueOnce(new Error("history offline"));
    render(<EvolutionTab />);
    expect(await screen.findByText("文档已写入")).toBeTruthy();
    expect(screen.getByText(/历史议程读取失败/)).toBeTruthy();
    expect(screen.queryByText(/今日议程尚未生成/)).toBeNull();
  });

  it("今日议程首次失败是未知，非确认未生成；手动读取恢复且不运行议程", async () => {
    vi.mocked(getAgentAgenda).mockRejectedValueOnce(new Error("agenda offline"));
    render(<EvolutionTab />);
    expect(await screen.findByText(/今日议程读取失败/)).toBeTruthy();
    expect(screen.queryByText(/今日议程尚未生成/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText(/今日议程尚未生成/)).toBeTruthy();
    expect(runAgentAgenda).not.toHaveBeenCalled();
  });

  it("重新读取失败保留旧议程；真实写后校准不会被旧请求覆盖", async () => {
    let old!: (agenda: AgentAgenda | null) => void;
    const fresh = fixture("B", "executed");
    fresh.items[0].finding = "写后新议程";
    vi.mocked(getAgentAgenda).mockResolvedValueOnce(fixture("B", "executed"))
      .mockRejectedValueOnce(new Error("refresh offline"))
      .mockReturnValueOnce(new Promise(resolve => { old = resolve; })).mockResolvedValue(fresh);
    vi.mocked(runAgentAgenda).mockResolvedValue(fresh);
    render(<EvolutionTab />);
    await screen.findByText("文档已写入");
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText(/今日议程读取失败.*上次读取结果/)).toBeTruthy();
    expect(screen.getByText("文档已写入")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    fireEvent.click(screen.getByRole("button", { name: "立即进化（降级兜底）" }));
    expect(await screen.findByText("写后新议程")).toBeTruthy();
    await act(async () => old(fixture("C", "proposed")));
    expect(screen.getByText("写后新议程")).toBeTruthy();
    await waitFor(() => expect(runAgentAgenda).toHaveBeenCalledTimes(1));
  });
});
