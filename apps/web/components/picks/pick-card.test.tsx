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

  it("执行复核与参考价分开显示，明确不等于成交", () => {
    const withExecution: DailyPickItem = {
      ...base,
      execution: {
        contract_version: "execution-facts-v1",
        decision_id: "OD-demo",
        decision_version: "ODV-demo",
        strategy_version: "s1",
        feature_version: "f1",
        reference_entry: {
          price: 40.36, as_of: "2026-09-21T09:26:00+08:00",
          source: "daily_pick_set", semantics: "reference_only_not_fill",
        },
        executable_snapshot: {
          state: "ready", price: 40.88, change_pct: 3.1,
          source: "sina_market", semantics: "action_time_quote_not_fill",
        },
        gate_decision: "passed",
      },
    };
    render(<PickCard item={fromDailyPick(withExecution)} />);
    expect(screen.getByText(/参考价 40.36/)).toBeTruthy();
    expect(screen.getByText(/执行快照 40.88/)).toBeTruthy();
    const chip = screen.getByText("执行复核·通过");
    expect(chip.getAttribute("title")).toContain("不表示已成交");
  });

  it("执行行情陈旧时直接显示陈旧原因，不把旧价伪装成可执行", () => {
    const stale: DailyPickItem = {
      ...base,
      execution: {
        contract_version: "execution-facts-v1",
        decision_id: "OD-stale",
        decision_version: "ODV-stale",
        strategy_version: "s1",
        feature_version: "f1",
        reference_entry: { price: 40.36, as_of: null, source: "daily_pick_set", semantics: "reference_only_not_fill" },
        executable_snapshot: {
          state: "stale", price: 40.50, change_pct: 2.0,
          freshness_reason: "上游停更", semantics: "action_time_quote_not_fill",
        },
        gate_decision: "rejected",
        gate_reason: "执行快照 stale（上游停更），只保留参考、不执行",
      },
    };
    const normalized = fromDailyPick(stale);
    expect(normalized.price).toBe(40.36);
    render(<PickCard item={normalized} />);
    const chip = screen.getByText("执行快照·陈旧");
    expect(chip.getAttribute("title")).toContain("上游停更");
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

/* ------------------------------------------- 闸门动态对照（2026-09-16 用户问题 7） */

/**
 * 用户原话：「猎场头部的情绪判断不应在当天提前定死，而应依据盘面变化动态调整」。
 *
 * 实测缺陷（2026-09-16）：组合 09:26 生成时在场仅 3 只涨停、最高 2 板 ⇒ 判「退潮」
 * 并撤除买入区间，该结论落库后**定格全天**；而同一页面紧邻的风格 chip 是读取时重算的，
 * 显示「高潮 · 题材进攻」。两个相反结论同屏，且定格的是更悲观的那个 ——
 * 当天实际有多只标的给出介入机会。
 *
 * 后端因此新增读取时复核（`meta.gate_live`）。本组用例守的是**对照面必须诚实**：
 * ① 已解除要说得出来；② 已解除**不得**暗示当日名单恢复可买（buy_range 已落库撤除）；
 * ③ 往严方向的变化（生成时未触发→现在触发）同样要提示；
 * ④ 「复核不可用」既不能说成"已触发"也不能说成"已解除"。
 */
describe("StandAsideBanner · 闸门动态对照", () => {
  /** 生成时刻落库值（gate_source=stored）。 */
  const storedGate: StandAsideGate = {
    stand_aside: true,
    level: "strong",
    reasons: ["情绪相位「退潮」——赚钱效应处于周期低位", "首板晋级率 8% 处于历史 9 分位"],
    advice: "市场情绪明显转弱，建议空仓观望，切忌盲目出手",
    phase: "退潮",
    strip_buy_range: true,
    gate_source: "stored",
    signals: { promotion_1to2: 0.08, promotion_1to2_pctl: 9, limit_down: 0 },
  };

  /** 读取时刻复核结果（gate_source=live）——收盘口径「高潮」，闸门不该触发。 */
  const liveCleared: StandAsideGate = {
    stand_aside: false,
    level: "none",
    reasons: [],
    advice: "市场情绪未见系统性风险，按组合纪律执行即可",
    phase: "高潮",
    strip_buy_range: false,
    gate_source: "live",
    signals: { promotion_1to2: 0.36, promotion_1to2_pctl: 62, break_rate: 0.11, break_caliber: "pool", limit_down: 0 },
    recheck: {
      phase: "高潮", trade_date: "2026-09-16", judged_at: "2026-09-16T10:48:06+00:00",
      stored_phase: "退潮", phase_changed: true, inputs_source: "sentiment.gate_inputs",
    },
  };

  it("已解除：说明生成时判了什么、当前换成什么，并讲清「不回溯」", () => {
    render(<StandAsideBanner gate={liveCleared} stored={storedGate} generatedAt="2026-09-16T09:26:35+08:00" />);
    const box = screen.getByTestId("stand-aside-cleared");
    expect(box.textContent).toContain("09:26");
    expect(box.textContent).toContain("退潮");
    expect(box.textContent).toContain("已解除");
    // 撤区间是生成时已落库的动作 → 必须明说不可回溯，否则等于暗示"可以买了"
    expect(box.textContent).toContain("不回溯");
    expect(box.textContent).toContain("不构成买入建议");
    // 已解除不是警报：用 role=status 而非 role=alert（视觉与语义都要往下降）
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("已解除：逐项对照两侧相位与输入（解释「结论为何变化」）", () => {
    render(<StandAsideBanner gate={liveCleared} stored={storedGate} />);
    const cmp = screen.getByTestId("stand-aside-comparison");
    expect(cmp.textContent).toContain("生成时相位「退潮」");
    expect(cmp.textContent).toContain("当前相位「高潮」");
    expect(cmp.textContent).toContain("首板晋级率 8%（历史 9 分位）");
    expect(cmp.textContent).toContain("首板晋级率 36%（历史 62 分位）");
  });

  it("往严方向：生成时未触发、现在触发 → 必须提示（漏报比误报贵）", () => {
    const liveOn: StandAsideGate = {
      ...storedGate,
      gate_source: "live",
      reasons: ["炸板率 41% 处于历史 95 分位——封板异常不牢"],
      advice: "市场情绪偏弱，建议控制仓位、减少出手频率",
      level: "mild",
    };
    const storedOff: StandAsideGate = { ...storedGate, stand_aside: false, reasons: [], phase: "高潮" };
    render(<StandAsideBanner gate={liveOn} stored={storedOff} generatedAt="2026-09-16T09:26:35+08:00" />);
    const box = screen.getByTestId("stand-aside-banner");
    expect(box.textContent).toContain("未触发闸门");
    expect(box.textContent).toContain("已触发");
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("复核不可用：照落库值显示 + 原因说明，既不谎称已解除也不谎称已触发", () => {
    const degraded: StandAsideGate = {
      ...storedGate,
      gate_source: "unavailable",
      gate_note: "实时情绪不可用（全市场快照尚未就绪），显示生成时刻结论",
    };
    render(<StandAsideBanner gate={degraded} stored={storedGate} />);
    const box = screen.getByTestId("stand-aside-banner");
    expect(box.textContent).toContain("全市场快照尚未就绪");
    expect(screen.queryByTestId("stand-aside-cleared")).toBeNull();
    expect(box.textContent).not.toContain("未触发闸门");
    // 落库结论本身照常展示（降级不等于把风险提示也撤掉）
    expect(box.textContent).toContain("建议空仓观望");
  });

  it("两次都触发但理由变了：展示对照行，主结论仍用实时理由", () => {
    const liveOn: StandAsideGate = {
      ...storedGate,
      gate_source: "live",
      reasons: ["炸板率 41% 处于历史 95 分位——封板异常不牢"],
    };
    render(<StandAsideBanner gate={liveOn} stored={storedGate} />);
    expect(screen.getByTestId("stand-aside-banner").textContent).toContain("炸板率 41%");
    expect(screen.getByTestId("stand-aside-comparison").textContent).toContain("生成时相位「退潮」");
  });

  it("旧后端形态（只传落库值、无复核）：不做差异判断，也不渲染「已解除」", () => {
    // 前端 gateView = gate_live ?? gate ⇒ 复核缺失时传进来的就是 stored 对象本身。
    // 若此处按触发态比较会误判为「生成时未触发、现在触发」——那是事实的反面。
    render(<StandAsideBanner gate={storedGate} />);
    expect(screen.queryByTestId("stand-aside-cleared")).toBeNull();
    expect(screen.queryByTestId("stand-aside-comparison")).toBeNull();
    const box = screen.getByTestId("stand-aside-banner");
    expect(box.textContent).not.toContain("未触发闸门");
    expect(box.textContent).toContain("建议空仓观望");
  });
});

/* ---------------------------------------------------------------- 可参与性口径（2026-09-15） */

/**
 * 猎场口径变更（用户指令：只收「投资者实际可以参与的」个股）在前端的落点：
 * ① 可参与性徽标必须渲染，且「不可参与」不能与「可参与」共用同一种视觉语言；
 * ② 联动确定性是**候选卡片的主判定**（候选当前未封板，封板历史不能替代 current 判定）；
 * ③ 参考区（涨停梯队）卡片的可参与性判定与首封时间证据都要可见。
 */
describe("PickCard · 可参与性三态（2026-09-15 猎场口径）", () => {
  /** 可参与评估候选：当前未封板、通过联动门槛（不保证成交）。 */
  const participant: IntradayTopStock = {
    symbol: "301662",
    name: "宏工科技",
    role: null,
    boards: null,
    change_pct: 14.35,
    theme: "固态电池",
    stage: "启动",
    strength_tier: "观察",
    distinctiveness: null,
    certainty: null,
    linkage: { level: "高", basis: "题材启动 · 涨停 3 家 · 已进临板区（距封板 5.35pct）" },
    tradability: { level: "可参与", basis: "当前未封板，可进入参与评估；未核盘口深度/排队，不保证成交" },
    reason: null,
    tier: 1,
    pick_basis:
      "联动确定性高：题材成建制且已进临板区（当前未封板、进入参与评估）；题材内涨停 3 家形成集中，本股当前未封板（14.3%，距封板 5.35pct）——已进临板区",
    price: 67.1,
  };

  /** 参考区的曾封板梯队成员：当前仍封板；后续若开板必须用新快照重评。 */
  const ladder: IntradayTopStock = {
    ...participant,
    symbol: "002912",
    name: "中新赛克",
    role: "龙头",
    boards: 4,
    change_pct: 10.02,
    first_seal_time: "09:25:00",
    linkage: null,
    tradability: {
      level: "不可参与",
      basis: "当前仍封在涨停板（竞价即封，首封 09:25）——当前不可参与；后续若开板须按新快照重评",
    },
    tier: 1,
    reference_only: true,
  };

  it("可参与候选：渲染「可参与」徽标 + 联动确定性判定 + 依据行", () => {
    render(<PickCard item={fromIntradayStock(participant)} />);
    expect(screen.getByText("可参与评估 · 当前未封板")).toBeTruthy();
    expect(screen.getByText(/联动确定性·高/)).toBeTruthy();
    // 依据行：分层名单走 pick_basis（"为什么排在这一档"）
    expect(screen.getByText("入选")).toBeTruthy();
    expect(screen.getByText(/题材内涨停 3 家形成集中/)).toBeTruthy();
    // current 未封板候选 ⇒ 不臆造当前梯队语义字段（角色/连板/辨识度/确定性都不渲染）
    expect(screen.queryByText(/辨识度/)).toBeNull();
    expect(screen.queryByText(/中军|龙头|首板/)).toBeNull();
  });

  it("题材手风琴候选：依据行走「联动」行（与「入选」分开两行）", () => {
    // 手风琴路径的 participants 带 `basis`（OpportunityStock），分层名单则只有
    // pick_basis —— 两条路径的依据行标签不同，是刻意的：前者答"为什么进猎场"，
    // 后者答"为什么排这一档"。混成一行会让两个问题只剩一个答案。
    const acc: OpportunityStock = {
      symbol: "301662",
      name: "宏工科技",
      role: null,
      boards: null,
      change_pct: 14.35,
      reason: null,
      hot_rank: null,
      distinctiveness: { level: "低", basis: "首板且无人气数据" },
      certainty: { level: "unknown", basis: "题材阶段缺失" },
      linkage: { level: "高", basis: "题材启动 · 涨停 3 家 · 已进临板区" },
      tradability: { level: "可参与", basis: "当前未封板，可进入参与评估；未核盘口深度/排队，不保证成交" },
      basis: "题材内涨停 3 家形成集中，本股当前未封板（14.3%，距封板 5.35pct）——已进临板区",
    };
    render(<PickCard item={fromIntradayStock(acc)} />);
    expect(screen.getByText("联动")).toBeTruthy();
    expect(screen.getByText(/本股当前未封板/)).toBeTruthy();
  });

  it("参考区梯队：渲染当前不可参与 + 首封时间证据", () => {
    render(<PickCard item={fromIntradayStock(ladder)} />);
    expect(screen.getByText("当前不可参与")).toBeTruthy();
    expect(screen.getByText("首封")).toBeTruthy();
    expect(screen.getByText("09:25:00")).toBeTruthy();
    // 参考区不该出现「可参与」——两种口径不能混用同一个徽标
    expect(screen.queryByText("可参与评估 · 当前未封板")).toBeNull();
  });

  it("板块徽标：把「这个板我有没有权限买」放在卡片上", () => {
    // 2026-09-15 用户「只有主板的权限现在」——板块是账户口径的可视证据，
    // 而不是要用户记住代码段规则（名单本身已按权限过滤，这里是可核对性）
    render(<PickCard item={fromIntradayStock({ ...participant, board: "深市主板" })} />);
    expect(screen.getByText("深市主板")).toBeTruthy();
    // 字段缺失时整块不渲染（不拿「板块 --」把缺数据伪装成有数据）
    const { container } = render(<PickCard item={fromIntradayStock(participant)} />);
    expect(container.textContent).not.toContain("深市主板");
  });

  it("盘前名单：入选原因里带「来源」行（题材联动股可追溯）", () => {
    render(
      <PickCard
        item={fromDailyPick({
          ...base,
          source: "theme_linkage",
          source_basis: "题材内涨停 4 家形成集中，本股当前未封板（3.0%）",
          tradability: { level: "可参与", basis: "当前未封板，可进入参与评估；未核盘口深度/排队，不保证成交" },
        })}
      />,
    );
    expect(screen.getByText("来源")).toBeTruthy();
    expect(screen.getByText(/本股当前未封板/)).toBeTruthy();
    expect(screen.getByText("可参与评估 · 当前未封板")).toBeTruthy();
  });
});
