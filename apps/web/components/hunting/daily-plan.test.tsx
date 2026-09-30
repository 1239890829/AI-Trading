import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { DailyPlanBlock } from "@/components/hunting/intraday-sections";
import type { MorningBrief } from "@/lib/api";

afterEach(cleanup);

const plan: NonNullable<MorningBrief["daily_plan"]> = {
  based_on: "2026-09-02",
  review: { trade_date: "2026-09-02", review_id: "RV-test", summary: "昨日选股偏弱", findings: ["承接不足"] },
  open_items: [{ id: 1, trade_date: "20260901", title: "复核缺口", category: "process", status: "pending" }],
  agenda: { date: "2026-09-02", status: "executed", items: [{ finding: "验证晨窗", class: "B", status: "executed" }] },
  sources: { review: "available", open_items: "available", agenda: "available" },
  note: "规则拼装；不构成买卖建议",
};

describe("今日条件计划", () => {
  it("显示真实内容、依据日期和免责说明", () => {
    render(<DailyPlanBlock plan={plan} />);
    expect(screen.getByText(/今日条件计划 · 依据 2026-09-02/)).toBeTruthy();
    expect(screen.getByText(/昨日选股偏弱/)).toBeTruthy();
    expect(screen.getByText(/复核缺口/)).toBeTruthy();
    expect(screen.getByText(/验证晨窗/)).toBeTruthy();
    expect(screen.getByText(/不构成买卖建议/)).toBeTruthy();
  });

  it("分别显示合法空集和读取失败", () => {
    render(<DailyPlanBlock plan={{ ...plan, review: null, open_items: [], agenda: null, sources: { review: "empty", open_items: "error", agenda: "empty" } }} />);
    expect(screen.getByText(/该日无复盘报告/)).toBeTruthy();
    expect(screen.getByText("读取失败")).toBeTruthy();
    expect(screen.getByText(/该日无议程/)).toBeTruthy();
  });

  it("旧版简报缺字段时显式提示不可用", () => {
    render(<DailyPlanBlock plan={undefined} />);
    expect(screen.getByText(/今日条件计划不可用/)).toBeTruthy();
    cleanup();
    render(<DailyPlanBlock plan={{ ...plan, sources: undefined }} />);
    expect(screen.getByText(/旧版本生成/)).toBeTruthy();
  });
});
