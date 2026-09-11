import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  JudgeChip,
  PickCard,
  StandAsideBanner,
  fromDailyPick,
  fromIntradayStock,
} from "@/components/picks/pick-card";
import type { DailyPickItem, IntradayTopStock, OpportunityStock, StandAsideGate } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 event-panel.test.tsx 注释）
afterEach(cleanup);

/** 盘前名单：收盘产出，六维评分 / 估值 / 买入区间 / 失效条件齐备。 */
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

/** 盘中名单 · 分层（intraday-top）：带 tier / pick_basis，另有辨识度·确定性判定。 */
const topPath: IntradayTopStock = {
  symbol: "002204",
  name: "大连重工",
  role: "中军",
  boards: 1,
  change_pct: 10.09,
  theme: "商业航天",
  stage: "启动",
  strength_tier: "观察",
  distinctiveness: { level: "中", basis: "未上人气榜前 30；角色 中军" },
  certainty: { level: "高", basis: "题材启动；封单 1.2 亿；首封 10:18:18" },
  reason: "商业航天+军工+机器人",
  tier: 2,
  pick_basis: "确定性高：题材阶段与封板质量支持延续（辨识度未到高）",
  price: 5.42,
  stop_ref: { pct: 8, price: 4.99, basis: "档位基准 8% → 取 8.0%（clamp 3%~12%）" },
  exit_plan: { trailing_pct: 8, roi_ladder: [{ gain_pct: 10, action: "减半仓" }], note: "错了要快" },
};

/** 盘中名单 · 题材手风琴（OpportunityStock）：无 tier / 无 pick_basis，只有官方涨停原因。 */
const accordionPath: OpportunityStock = {
  symbol: "600111",
  name: "北方稀土",
  role: "龙头",
  boards: 5,
  change_pct: 10.01,
  reason: "存储芯片+稀土永磁",
  hot_rank: 3,
  distinctiveness: { level: "unknown", basis: "人气榜不可用，辨识度无法判定" },
  certainty: { level: "高", basis: "题材发酵；封单 2.0 亿" },
};

describe("PickCard · 盘前名单（fromDailyPick）", () => {
  it("展示六维评分条（含梯队第六维）", () => {
    render(<PickCard item={fromDailyPick(base)} />);
    // 维度名既出现在评分条也出现在依据行里，用 getAllByText
    for (const label of ["情绪", "消息", "技术", "基本", "资金", "梯队"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it("展示联合研判三要素：梯队地位 / 题材阶段 / 风险档位", () => {
    render(<PickCard item={fromDailyPick(base)} />);
    expect(screen.getByText("龙头")).toBeTruthy();
    expect(screen.getByText("数据中心交换机 · 启动")).toBeTruthy();
    expect(screen.getByText("龙头博弈")).toBeTruthy();
  });

  it("展示买入参考区间、止损参考位与失效条件", () => {
    render(<PickCard item={fromDailyPick(base)} />);
    expect(screen.getByText(/买入参考区间/)).toBeTruthy();
    expect(screen.getByText(/止损参考/)).toBeTruthy();
    expect(screen.getByText("回落 8%")).toBeTruthy();
    expect(screen.getByText("+10%→减半仓；+20%→再减半")).toBeTruthy();
    expect(screen.getByText(/收盘跌破 5 日线/)).toBeTruthy();
  });

  it("空仓闸门触发时标注仅观察并撤除买入区间", () => {
    const gated: DailyPickItem = { ...base, buy_range: null, observation_only: true };
    render(<PickCard item={fromDailyPick(gated)} />);
    expect(screen.getByText("仅观察")).toBeTruthy();
    expect(screen.getByText(/不给出买入参考区间/)).toBeTruthy();
    // 止损与失效条件仍在（观察也要有退出依据）
    expect(screen.getByText(/止损参考/)).toBeTruthy();
  });

  it("盘前名单不缺维度：不渲染口径注记", () => {
    const { container } = render(<PickCard item={fromDailyPick(base)} />);
    expect(container.textContent).not.toContain("不含收盘六维评分");
  });
});

describe("PickCard · 盘中名单（fromIntradayStock）", () => {
  it("分层名单：入选理由与官方涨停原因都渲染（涨停原因来自 ladder 行，不再恒空）", () => {
    render(<PickCard item={fromIntradayStock(topPath)} />);
    expect(screen.getByText("入选")).toBeTruthy();
    expect(screen.getByText("涨停原因")).toBeTruthy();
    expect(screen.getByText("商业航天+军工+机器人")).toBeTruthy();
    expect(screen.getByText("T2 跟踪档")).toBeTruthy();
  });

  it("手风琴路径没有「入选」这一行：整行不出现（不许拿「入选 —」冒充缺失）", () => {
    const { container } = render(<PickCard item={fromIntradayStock(accordionPath)} />);
    expect(container.textContent).toContain("涨停原因");
    expect(container.textContent).toContain("存储芯片+稀土永磁");
    // 标题行「入选原因」两侧统一；但**行标签**「入选」只在分层名单（pick_basis）出现
    expect(screen.getByText("入选原因")).toBeTruthy();
    expect(screen.queryByText("入选")).toBeNull();
    expect(container.textContent).not.toContain("跟踪档");
  });

  it("入选原因标题两侧统一（盘前 2026-09-10 用户反馈「怎么没有入选原因」）", () => {
    render(<PickCard item={fromDailyPick(base)} />);
    expect(screen.getByText("入选原因")).toBeTruthy();
    // 盘前的「入选原因」是七维依据行，不是空壳（梯队既是评分条标签也是依据行标签）
    expect(screen.getAllByText("梯队").length).toBeGreaterThan(1);

    cleanup();
    render(<PickCard item={fromIntradayStock(topPath)} />);
    expect(screen.getByText("入选原因")).toBeTruthy();
  });

  it("判定徽标三态：unknown 显示「未判定」而非「低」，依据进 title", () => {
    render(<PickCard item={fromIntradayStock(accordionPath)} />);
    const chip = screen.getByText("辨识度·未判定");
    expect(chip.getAttribute("title")).toContain("人气榜不可用");
    expect(screen.getByText("确定性·高")).toBeTruthy();
  });

  it("缺收盘口径维度时：不补值也不留白，用口径注记说明为什么没有", () => {
    const { container } = render(<PickCard item={fromIntradayStock(topPath)} />);
    // 不适用 ⇒ 整节不渲染（盘中路径不出现盘前概念的买入区间）
    expect(container.textContent).not.toContain("买入参考区间");
    // 但必须显式说明口径，用户能看出「为什么这张卡没有评分」
    expect(container.textContent).toContain("不含收盘六维评分");
    expect(container.textContent).toContain("盘中实时口径");
  });

  it("现价/止损/出场纪律与盘前同构渲染（补全单点收口在 attach_risk_fields）", () => {
    render(<PickCard item={fromIntradayStock(topPath)} />);
    expect(screen.getByText("5.42")).toBeTruthy();
    expect(screen.getByText(/止损参考/)).toBeTruthy();
    expect(screen.getByText(/4\.99/)).toBeTruthy();
    expect(screen.getByText("跟踪止盈")).toBeTruthy();
  });

  it("来源徽标与持仓标注：来源恒显示，持仓按传入值显示", () => {
    const { container } = render(
      <PickCard item={fromIntradayStock(topPath)} positionLabel="sim" />,
    );
    expect(container.textContent).toContain("盘中跟踪");
    expect(container.textContent).toContain("已模拟持仓");

    cleanup();
    const noPos = render(<PickCard item={fromIntradayStock(topPath)} />);
    expect(noPos.container.textContent).not.toContain("已模拟持仓");
    expect(noPos.container.textContent).toContain("盘中跟踪");
  });
});

describe("适配器归一：4 组异名同义字段", () => {
  it("role ↔ echelon_role · stage ↔ theme_stage", () => {
    const p = fromDailyPick(base);
    expect(p.role).toBe("龙头"); // echelon_role
    expect(p.stage).toBe("启动"); // theme_stage

    const i = fromIntradayStock(topPath);
    expect(i.role).toBe("中军"); // role
    expect(i.stage).toBe("启动"); // stage
  });

  it("stop_ref ↔ stop_loss · exit_plan ↔ exit_discipline 落到同一对字段", () => {
    const p = fromDailyPick(base);
    expect(p.stopLoss?.price).toBe(36.34); // stop_loss
    expect(p.exit?.trailing_pct).toBe(8); // exit_discipline

    const i = fromIntradayStock(topPath);
    expect(i.stopLoss?.price).toBe(4.99); // stop_ref
    expect(i.exit?.trailing_pct).toBe(8); // exit_plan（未定型 dict）
    expect(i.stopLoss?.pct).toBe(8);
  });

  it("盘中 exit_plan 里一项都没有 → exit 为 null（不渲染「回落 0%」这种臆造数字）", () => {
    const noExit: IntradayTopStock = { ...topPath, exit_plan: {} };
    expect(fromIntradayStock(noExit).exit).toBeNull();

    const { container } = render(<PickCard item={fromIntradayStock(noExit)} />);
    expect(container.textContent).toContain("止损参考"); // 止损仍在 ⇒ 分节仍渲染
    expect(container.textContent).not.toContain("回落 0%");
    expect(container.textContent).not.toContain("跟踪止盈");
  });

  it("exit 存在但缺 trailing_pct 时不渲染跟踪止盈行（0 / 占位都是臆造）", () => {
    const onlyNote: IntradayTopStock = { ...topPath, exit_plan: { note: "错了要快" } };
    const c = fromIntradayStock(onlyNote);
    expect(c.exit?.trailing_pct).toBeNull();

    const { container } = render(<PickCard item={c} />);
    expect(container.textContent).toContain("纪律");
    expect(container.textContent).not.toContain("跟踪止盈");
    expect(container.textContent).not.toContain("回落");
  });

  it("盘中算不出/不该给的字段一律 null：不臆造 0 或默认分", () => {
    const c = fromIntradayStock(topPath);
    expect(c.pe_ttm).toBeNull();
    expect(c.score).toBeNull();
    expect(c.sub_scores).toBeNull();
    expect(c.buyRange).toBeNull();
    expect(c.riskTier).toBeNull();
  });

  it("两源都无依据行时，整节不渲染（回归：曾渲染「入选 —」的假缺失占位）", () => {
    const bare: OpportunityStock = { ...accordionPath, reason: null };
    const card = fromIntradayStock(bare);
    expect(card.basisRows).toHaveLength(0);

    const { container } = render(<PickCard item={card} />);
    expect(container.textContent).not.toContain("入选");
    expect(container.textContent).not.toContain("涨停原因");
    expect(container.textContent).not.toContain("依据");
  });
});

describe("JudgeChip", () => {
  it("unknown 与 low 走不同配色（判不出 ≠ 低）", () => {
    const { container: unknown } = render(<JudgeChip label="辨识度" level="unknown" basis="" />);
    const { container: low } = render(<JudgeChip label="辨识度" level="低" basis="" />);
    expect(unknown.innerHTML).toContain("amber");
    expect(low.innerHTML).toContain("zinc");
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

  it("撤除档：横幅明示「已撤除买入区间」", () => {
    const gate: StandAsideGate = {
      stand_aside: true,
      level: "strong",
      reasons: ["情绪相位「退潮」"],
      advice: "建议空仓观望",
      phase: "退潮",
      strip_buy_range: true,
    };
    render(<StandAsideBanner gate={gate} />);
    expect(screen.getByText(/已撤除买入区间/)).toBeTruthy();
    expect(screen.queryByText(/保留买入区间/)).toBeNull();
  });

  it("提示档：横幅明示「保留买入区间」（与撤除档必须能一眼区分）", () => {
    const gate: StandAsideGate = {
      stand_aside: true,
      level: "mild",
      reasons: ["首板晋级率 29% < 30%（接力无人接）"],
      advice: "市场情绪偏弱，建议控制仓位、减少出手频率",
      phase: "高潮",
      strip_buy_range: false,
    };
    render(<StandAsideBanner gate={gate} />);
    expect(screen.getByText(/保留买入区间/)).toBeTruthy();
    expect(screen.getByText(/非空仓信号/)).toBeTruthy();
    expect(screen.queryByText(/已撤除买入区间/)).toBeNull();
  });

  it("旧数据无 strip_buy_range 字段时不臆造分档结论（整行不渲染）", () => {
    const gate: StandAsideGate = {
      stand_aside: true,
      level: "mild",
      reasons: ["炸板率 40% ≥ 35%"],
      advice: "建议控制仓位",
    };
    render(<StandAsideBanner gate={gate} />);
    expect(screen.queryByText(/已撤除买入区间/)).toBeNull();
    expect(screen.queryByText(/保留买入区间/)).toBeNull();
    expect(screen.getByRole("alert")).toBeTruthy(); // 横幅本身照常
  });
});
