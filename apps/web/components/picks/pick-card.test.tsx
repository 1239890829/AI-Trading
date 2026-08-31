import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PickCard, StandAsideBanner } from "@/components/picks/pick-card";
import type { DailyPickItem, StandAsideGate } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 event-panel.test.tsx 注释）
afterEach(cleanup);

const base: DailyPickItem = {
  symbol: "002396",
  name: "星网锐捷",
  price: 40.36,
  change_pct: 10,
  score: 59.7,
  sub_scores: {
    sentiment: 30,
    news: 50,
    tech: 65,
    fundamental: 55,
    capital: 50,
    echelon: 86,
  },
  bases: { echelon: "涨停池精确判定（3 板）；角色「龙头」基础 88 分" },
  vetoes: [],
  buy_range: { low: 39.15, high: 41.57, basis: "现价 ±3%" },
  themes: ["数据中心交换机"],
  related_events: [],
  echelon_role: "龙头",
  theme: "数据中心交换机",
  theme_stage: "启动",
  risk_tier: "龙头博弈",
  stop_loss: { pct: 9.96, price: 36.34, basis: "1.5×ATR" },
  exit_discipline: {
    trailing_pct: 8,
    roi_ladder: [
      { gain_pct: 10, action: "减半仓" },
      { gain_pct: 20, action: "再减半" },
    ],
    note: "高位接力，错了要快",
    disclaimer: "不构成买卖建议",
  },
  invalidations: ["收盘跌破 5 日线（当前 34.31）", "龙头炸板且尾盘未能回封"],
};

describe("PickCard", () => {
  it("展示六维评分条（含梯队第六维）", () => {
    render(<PickCard item={base} />);
    // 维度名既出现在评分条也出现在 basis 摘要里，用 getAllByText
    for (const label of ["情绪", "消息", "技术", "基本", "资金", "梯队"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it("展示联合研判三要素：梯队地位 / 题材阶段 / 风险档位", () => {
    render(<PickCard item={base} />);
    expect(screen.getByText("龙头")).toBeTruthy();
    expect(screen.getByText("数据中心交换机 · 启动")).toBeTruthy();
    expect(screen.getByText("龙头博弈")).toBeTruthy();
  });

  it("展示买入参考区间与止损参考位、失效条件", () => {
    render(<PickCard item={base} />);
    expect(screen.getByText(/买入参考区间/)).toBeTruthy();
    expect(screen.getByText(/止损参考/)).toBeTruthy();
    expect(screen.getByText(/跟踪回撤 8%/)).toBeTruthy();
    expect(screen.getByText(/收盘跌破 5 日线/)).toBeTruthy();
  });

  it("空仓闸门触发时标注仅观察并撤除买入区间", () => {
    const gated: DailyPickItem = {
      ...base,
      buy_range: null,
      observation_only: true,
    };
    render(<PickCard item={gated} />);
    expect(screen.getByText("仅观察")).toBeTruthy();
    expect(screen.getByText(/不给出买入参考区间/)).toBeTruthy();
    // 止损与失效条件仍在（观察也要有退出依据）
    expect(screen.getByText(/止损参考/)).toBeTruthy();
  });
});

describe("StandAsideBanner", () => {
  it("强预警：展示建议与逐条触发原因", () => {
    const gate: StandAsideGate = {
      stand_aside: true,
      level: "strong",
      reasons: ["情绪相位「退潮」", "首板晋级率 16% < 30%"],
      advice: "市场情绪明显转弱，建议空仓观望，切忌盲目出手",
      disclaimer: "风险提示为规则化判读结果，不构成买卖建议",
    };
    render(<StandAsideBanner gate={gate} />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText(/建议空仓观望/)).toBeTruthy();
    for (const r of gate.reasons) {
      expect(screen.getByText(new RegExp(r.replace(/[（）<>%]/g, ".")))).toBeTruthy();
    }
    expect(screen.getByText(/不构成买卖建议/)).toBeTruthy();
  });

  it("未触发时不渲染（不打扰）", () => {
    const { container } = render(
      <StandAsideBanner gate={{ stand_aside: false, level: "none", reasons: [], advice: "正常" }} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
