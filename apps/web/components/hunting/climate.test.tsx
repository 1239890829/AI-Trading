import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ClimateBlock } from "@/components/hunting/intraday-sections";
import type { Climate } from "@/lib/api";

afterEach(cleanup);

function climate(over: Partial<Climate> = {}): Climate {
  return {
    state: "neutral",
    alert: "el_nino",
    strength: "strong",
    consecutive: 3,
    peak_abs: 1.8,
    threshold: 0.5,
    persist_seasons: 5,
    latest: { season: "JJA", year: 2026, anom: 1.8, end_date: "2026-08-31" },
    series: [
      { season: "AMJ", year: 2026, anom: 0.95 },
      { season: "MJJ", year: 2026, anom: 1.39 },
      { season: "JJA", year: 2026, anom: 1.8 },
    ],
    candidate_links: [
      { target: "磷化工", strength: 2, direction: 1, chain: "厄尔尼诺农业链" },
      { target: "化肥", strength: 2, direction: 1, chain: "厄尔尼诺农业链" },
    ],
    unjudged_reason: null,
    as_of: "2026-09-11",
    timing_note: "ONI 是三月滑动平均且季末后发布，天然滞后，不构成领先指标。",
    chain_caveat: "人工映射的候选假设，本模块已用月频数据检验，未获支持。",
    empirical_verdict: "**人工传导链未获数据支持**：基础化工方向与表相反。",
    disclaimer: "以上为气候相位与候选传导链陈述，不构成买卖建议。",
    ...over,
  };
}

describe("气候相位（P1-32）", () => {
  it("渲染相位 + 强度 + 最新季 + 候选链 + 免责页脚", () => {
    render(<ClimateBlock climate={climate()} />);
    expect(screen.getByText("气候相位 · ENSO/ONI")).toBeTruthy();
    expect(screen.getByText("中性 · 强")).toBeTruthy();
    expect(screen.getByText("JJA 2026 +1.80")).toBeTruthy();
    expect(screen.getByText(/磷化工、化肥/)).toBeTruthy();
    expect(screen.getByText(/不构成买卖建议/)).toBeTruthy();
  });

  it("「无领先性」与「实证未获支持」两段声明必须同时渲染", () => {
    // 少了任一段，界面就把人工假设呈现成了结论（红线 3）
    render(<ClimateBlock climate={climate()} />);
    expect(screen.getByText(/不构成领先指标/)).toBeTruthy();
    // <details> 的 textContent 含其子节点文本，会与外层同时命中 → 用 getAllByText
    expect(screen.getAllByText(/未获数据支持/).length).toBeGreaterThan(0);
  });

  it("预警态单列：越线但未满 5 季，不得显示成「厄尔尼诺确立」", () => {
    render(<ClimateBlock climate={climate()} />);
    expect(screen.getByText("预警态")).toBeTruthy();
    expect(screen.getByText(/已连续越线 3 个季，未满 5 季/)).toBeTruthy();
    expect(screen.queryByText("厄尔尼诺 · 强")).toBeNull();
  });

  it("三态：state=null 显示「未判定」并给出原因，绝不渲染成中性", () => {
    render(
      <ClimateBlock
        climate={climate({
          state: null,
          alert: null,
          strength: null,
          consecutive: 0,
          unjudged_reason: "ONI 数据滞后（最新季末距今 200 天 > 120）",
          candidate_links: [],
        })}
      />,
    );
    expect(screen.getByText("未判定")).toBeTruthy();
    expect(screen.queryByText("中性")).toBeNull();
    expect(screen.getByText(/数据滞后/)).toBeTruthy();
  });

  it("三态：整块不可用（null）不渲染", () => {
    const { container } = render(<ClimateBlock climate={null} />);
    expect(container.textContent).toBe("");
  });

  it("厄尔尼诺确立态显示相位名与强度档", () => {
    render(
      <ClimateBlock
        climate={climate({ state: "el_nino", alert: null, strength: "very_strong", consecutive: 6 })}
      />,
    );
    expect(screen.getByText("厄尔尼诺 · 超强")).toBeTruthy();
    expect(screen.queryByText("预警态")).toBeNull();
  });

  it("拉尼娜态不给任何候选方向（取反属直觉链）", () => {
    render(
      <ClimateBlock
        climate={climate({ state: "la_nina", alert: null, strength: "moderate", candidate_links: [] })}
      />,
    );
    expect(screen.getByText("拉尼娜 · 中等")).toBeTruthy();
    expect(screen.queryByText(/候选题材/)).toBeNull();
  });
});
