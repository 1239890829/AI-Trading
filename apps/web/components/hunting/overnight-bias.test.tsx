import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { OvernightBiasBlock } from "@/components/hunting/intraday-sections";
import type { OvernightBias, OvernightBiasEvidence } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（同 macro-calendar.test.tsx）
afterEach(cleanup);

function ev(over: Partial<OvernightBiasEvidence>): OvernightBiasEvidence {
  return {
    key: "nasdaq",
    label: "纳斯达克指数",
    unit: "%",
    weighted: true,
    note: "隔夜科技风险偏好",
    date: "2026-09-09",
    value: 26253.34,
    prev_value: 26421.41,
    change: -0.64,
    zone: "降",
    bullish: false,
    ...over,
  };
}

function bias(over: Partial<OvernightBias> = {}): OvernightBias {
  return {
    stance: "承压",
    score: -2,
    score_range: "±3",
    available_weight: 3,
    unjudged_reason: null,
    evidence: [
      ev({}),
      ev({ key: "sox", label: "费城半导体指数", change: -1.8 }),
      ev({
        key: "usdcnh",
        label: "离岸人民币（USDCNH）",
        change: 0.42,
        zone: "升",
        bullish: false,
      }),
      ev({
        key: "us10y",
        label: "美国10年期国债收益率",
        unit: "bp",
        change: 15,
        zone: "升",
        bullish: false,
        weighted: false,
      }),
    ],
    missing: [],
    invalidation: ["本偏向主要解释开盘跳空。", "国内事件冲击时让位。"],
    horizon_note: "隔夜外围给出的是『开盘情绪偏向』。",
    disclaimer: "偏向 + 依据 + 失效条件，不构成买卖建议。",
    as_of: { nasdaq: "2026-09-09" },
    ...over,
  };
}

describe("隔夜海外偏向（P1-34）", () => {
  it("渲染档位 + 规则分 + 四路输入 + 免责页脚", () => {
    render(<OvernightBiasBlock bias={bias()} />);
    expect(screen.getByText("隔夜海外 · 今日偏向")).toBeTruthy();
    expect(screen.getByText("承压")).toBeTruthy();
    expect(screen.getByText("-2 / ±3")).toBeTruthy();
    expect(screen.getByText("纳斯达克指数")).toBeTruthy();
    expect(screen.getByText("美国10年期国债收益率")).toBeTruthy();
    // 变化按 unit 格式化：bp 走一位小数、% 走两位小数
    expect(screen.getByText("09-09 +15.0bp")).toBeTruthy();
    expect(screen.getByText("09-09 +0.42%")).toBeTruthy();
    expect(screen.getByText(/不构成买卖建议/)).toBeTruthy();
    expect(screen.getByText(/主要解释开盘跳空/)).toBeTruthy();
  });

  it("美债 10Y 标「仅记录」而非偏多/偏空（实测否决项不得被误读为方向输入）", () => {
    render(<OvernightBiasBlock bias={bias()} />);
    expect(screen.getByText("仅记录")).toBeTruthy();
    // 其余三路是真实方向输入
    expect(screen.getAllByText("偏空").length).toBe(3);
  });

  it("三态：stance=null 显示「未判定」并给出原因，绝不渲染成中性", () => {
    render(
      <OvernightBiasBlock
        bias={bias({
          stance: null,
          score: null,
          available_weight: 1,
          unjudged_reason: "有信息输入不足（有效权重 1 < 2）→ 未判定",
        })}
      />,
    );
    expect(screen.getByText("未判定")).toBeTruthy();
    expect(screen.queryByText("中性")).toBeNull();
    expect(screen.getByText(/有效权重 1 < 2/)).toBeTruthy();
  });

  it("三态：整块不可用（null）不渲染", () => {
    const { container } = render(<OvernightBiasBlock bias={null} />);
    expect(container.textContent).toBe("");
  });

  it("单路缺失显式列出 skip_reason，不当作「平」", () => {
    render(
      <OvernightBiasBlock
        bias={bias({
          evidence: [ev({ change: null, zone: null, bullish: null, skip_reason: "源不可得" })],
          missing: ["纳斯达克指数：源不可得"],
        })}
      />,
    );
    expect(screen.getByText("源不可得")).toBeTruthy();
    expect(screen.getByText("09-09 —")).toBeTruthy();
    expect(screen.getByText("未参与")).toBeTruthy();
    expect(screen.getByText(/输入缺失：纳斯达克指数/)).toBeTruthy();
  });

  it("中性档也照常渲染（中性是判定结果，不是缺失）", () => {
    render(<OvernightBiasBlock bias={bias({ stance: "中性", score: 0 })} />);
    expect(screen.getByText("中性")).toBeTruthy();
    expect(screen.getByText("0 / ±3")).toBeTruthy();
  });
});
