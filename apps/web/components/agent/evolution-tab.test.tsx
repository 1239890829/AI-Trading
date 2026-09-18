import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { EvolutionTab } from "./evolution-tab";
import { getAgentAgenda, type AgentAgenda, type AgentAgendaItem } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getAgentAgenda: vi.fn(),
  getAgentAgendas: vi.fn(async () => []),
  getAgentExperiments: vi.fn(async () => []),
  runAgentAgenda: vi.fn(),
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

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
