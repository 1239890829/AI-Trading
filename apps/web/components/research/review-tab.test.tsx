import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ReviewTab } from "./review-tab";
import type { ReviewReportDetail, ReviewReportSummary } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 alerts-tab.test.tsx 注释）
afterEach(cleanup);

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getReviewReports: vi.fn(),
    getReviewEffectiveness: vi.fn(),
    getReviewReport: vi.fn(),
    updateActionItemStatus: vi.fn(),
  };
});

const mocked = await import("@/lib/api");

const SUMMARY: ReviewReportSummary = {
  review_id: "RV-20260901-214950",
  trade_date: "20260901",
  methodology_version: "v1",
  model_actual: "rules",
  model_degraded: false,
  summary: "委托 0 笔；数据缺失 2 处；P0 改进项 1 条",
  gap_count: 2,
  action_item_count: 1,
  generated_at: "2026-09-01T13:49:50",
};

const EFFECT = {
  by_category: {
    data: { total: 30, confirmed: 0, reverted: 0, rejected: 0, pending: 30, adoption_rate: 0, revert_rate: null },
  },
  suggestions: [],
};

function detailWith(itemId: string, status = "pending"): ReviewReportDetail {
  return {
    review_id: SUMMARY.review_id,
    trade_date: SUMMARY.trade_date,
    generated_at: SUMMARY.generated_at,
    methodology_version: "v1",
    model: { requested: "rules", actual: "rules", degraded: false },
    dimensions: [],
    action_items: [
      {
        id: itemId,
        title: "补齐阻断级数据缺失：breadth, sentiment",
        category: "data",
        priority: "P0",
        expected_impact: "恢复被阻断维度的复盘结论",
        evidence: "breadth(snapshot_service): 全市场快照尚未就绪",
        target: "snapshot_service.breadth_payload",
        proposed_change: "检查 provider 健康状态与快照服务是否就绪",
        status,
        resolution_note: "",
      },
    ],
    meta_insights: [],
    summary: SUMMARY.summary,
  };
}

function setup(itemId = "42", status = "pending") {
  vi.mocked(mocked.getReviewReports).mockResolvedValue([SUMMARY]);
  vi.mocked(mocked.getReviewEffectiveness).mockResolvedValue(EFFECT);
  vi.mocked(mocked.getReviewReport).mockResolvedValue(detailWith(itemId, status));
  vi.mocked(mocked.updateActionItemStatus).mockResolvedValue({
    id: Number(itemId) || 0,
    review_id: SUMMARY.review_id,
    trade_date: SUMMARY.trade_date,
    title: "补齐阻断级数据缺失：breadth, sentiment",
    category: "data",
    priority: "P0",
    target: "snapshot_service.breadth_payload",
    proposed_change: "检查 provider 健康状态与快照服务是否就绪",
    status: "confirmed",
    resolution_note: "",
    resolved_at: "2026-09-01T14:00:00",
  });
}

describe("ReviewTab 改进项处置闭环", () => {
  it("改进项带处置入口，点击「确认」即提交并刷新", async () => {
    setup();
    render(<ReviewTab />);

    const btn = await screen.findByRole("button", { name: "确认" });
    fireEvent.click(btn);

    // 守卫三元组必须随请求携带：报告重跑后 id 会漂移（rowid 复用），
    // 裸 id 处置会静默挂到不相干的改进项上，后端靠三元组拒绝陈旧寻址
    await waitFor(() => {
      expect(mocked.updateActionItemStatus).toHaveBeenCalledWith(
        expect.objectContaining({
          id: "42",
          trade_date: "20260901",
          category: "data",
          title: "补齐阻断级数据缺失：breadth, sentiment",
        }),
        "confirmed",
        "",
      );
    });
    // 处置后要刷新有效性统计，否则右侧采纳率停留在旧值
    await waitFor(() => {
      expect(mocked.getReviewEffectiveness).toHaveBeenCalledTimes(2);
    });
  });

  it("驳回必须填理由——未填时提交按钮不可用", async () => {
    setup();
    render(<ReviewTab />);

    fireEvent.click(await screen.findByRole("button", { name: "驳回" }));

    const submit = await screen.findByRole("button", { name: "提交" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByPlaceholderText("驳回理由（必填）"), {
      target: { value: "该缺口是偶发，不值得改" },
    });
    expect((submit as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(submit);
    await waitFor(() => {
      expect(mocked.updateActionItemStatus).toHaveBeenCalledWith(
        expect.objectContaining({
          id: "42",
          trade_date: "20260901",
          category: "data",
          title: "补齐阻断级数据缺失：breadth, sentiment",
        }),
        "rejected",
        "该缺口是偶发，不值得改",
      );
    });
  });

  it("旧版报告的临时编号不可寻址，不暴露处置按钮", async () => {
    setup("AI-3f2a9c11");
    render(<ReviewTab />);

    expect(
      await screen.findByText(/来自旧版报告，无数据库主键/),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认" })).toBeNull();
  });

  it("已处置的改进项显示状态与撤销入口", async () => {
    setup("42", "applied");
    render(<ReviewTab />);

    expect(await screen.findByText("已实施")).toBeTruthy();
    // 已实施就不再重复提供「已实施」按钮，但可撤销
    expect(screen.queryByRole("button", { name: "已实施" })).toBeNull();
    expect(screen.getByRole("button", { name: "撤销处置" })).toBeTruthy();
  });
});
