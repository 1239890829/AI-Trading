import { cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  JudgeChip,
  PickCard,
  StandAsideBanner,
  fromDailyPick,
  fromIntradayStock,
  selectionReading,
} from "@/components/picks/pick-card";
import type { DailyPickItem, IntradayTopStock, OpportunityStock, SelectionMetrics, StandAsideGate } from "@/lib/api";
import * as API from "@/lib/api";
import * as AgentAPI from "@/lib/api/agent";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 event-panel.test.tsx 注释）
afterEach(cleanup);

function readBasis(label: string) {
  expect(within(screen.getByLabelText("入选依据核对")).getByText(label)).toBeTruthy();
}

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
  it("keeps first join evidence beside a later sealed quote and exposes its independent source clock", () => {
    render(<PickCard evidenceExpanded item={fromDailyPick({...base, price: 50, change_pct: 10, selection_entry: {
      state: "recorded", selected_at: "2026-10-09T10:01:02+08:00", reference_price: 40.36,
      reference_change_pct: 1.2, quote_as_of: "2026-10-09T09:59:58+08:00", source: "daily_generation",
      source_version: "a".repeat(64), semantics: "observation_only_not_fill",
    }})} />);
    const entry = screen.getByLabelText("每日首次加入记录");
    expect(entry.textContent).toContain("10:01:02");
    expect(entry.textContent).toContain("¥40.36");
    expect(entry.textContent).toContain("+1.20%");
    expect(entry.textContent).not.toContain("50.00");
    fireEvent.click(within(entry).getByRole("button",{name:"加入记录的完整依据"}));
    expect(entry.textContent).toContain("09:59:58");
    expect(entry.textContent).toContain("非成交价");
  });

  it("does not fill a legacy unknown join from its current price or render invalid numbers", () => {
    const page = render(<PickCard evidenceExpanded item={fromDailyPick(base)} />);
    expect(screen.getByLabelText("每日首次加入记录").textContent).toContain("首次入选依据未记录");
    expect(screen.queryByText("加入参考价 未记录")).toBeNull();
    page.rerender(<PickCard evidenceExpanded item={fromDailyPick({...base, selection_entry: {state: "partial", selected_at: "2026-10-09T09:59:00", reference_price: Number.NaN, reference_change_pct: Number.POSITIVE_INFINITY, quote_as_of: null, source: "daily_generation", source_version: null, semantics: "observation_only_not_fill"}})} />);
    const entry = screen.getByLabelText("每日首次加入记录");
    expect(entry.textContent).toContain("首次入选依据未记录");
    expect(entry.textContent).not.toMatch(/NaN|Infinity/);
  });
  it("distinguishes a missing dimension from a real zero score", () => {
    render(<PickCard evidenceExpanded item={fromDailyPick({...base, sub_scores: {news: 0}, dimension_evidence: {news: {state: "success", reason: "消息维度已读取，原评分为0"}}})} />);
    expect(screen.getByLabelText("情绪评分缺失")).toBeTruthy();
    expect(screen.queryByRole("meter", {name: "情绪评分"})).toBeNull();
    expect(screen.getByRole("meter", {name: "消息评分"}).getAttribute("aria-valuenow")).toBe("0");
  });
  it("keeps a legacy unknown 50 as a raw value without a filled score meter", () => {
    render(<PickCard evidenceExpanded item={fromDailyPick({...base, sub_scores: {news: 50}})} />);
    expect(screen.queryByRole("meter", {name: "消息评分"})).toBeNull();
    expect(screen.getByLabelText("消息原值 50，读取状态待核")).toBeTruthy();
    expect(screen.getByText(/历史记录未保存各维读取状态/)).toBeTruthy();
  });
  it("展示六维评分条（含梯队第六维）", () => {
    render(<PickCard evidenceExpanded item={fromDailyPick(base)} />);
    // 维度名既出现在评分条也出现在依据行里，用 getAllByText
    for (const label of ["情绪", "消息", "技术", "基本", "资金", "梯队"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it("展示联合研判三要素：梯队地位 / 题材阶段 / 风险档位", () => {
    render(<PickCard evidenceExpanded item={fromDailyPick(base)} />);
    expect(screen.getByText("龙头")).toBeTruthy();
    expect(screen.getByText("数据中心交换机 · 启动")).toBeTruthy();
    expect(screen.getByText("龙头博弈")).toBeTruthy();
  });

  it("展示买入参考区间、止损参考位与失效条件", () => {
    render(<PickCard evidenceExpanded item={fromDailyPick(base)} />);
    expect(screen.getByText(/买入参考区间/)).toBeTruthy();
    expect(screen.getByText("止损参考")).toBeTruthy();
    expect(screen.getByText("回落 8%")).toBeTruthy();
    expect(screen.getByText("+10%→减半仓；+20%→再减半")).toBeTruthy();
    expect(within(screen.getByLabelText("失效条件")).getByText(/收盘跌破 5 日线/)).toBeTruthy();
  });

  it("空仓闸门触发时标注仅观察并撤除买入区间", () => {
    const gated: DailyPickItem = { ...base, buy_range: null, observation_only: true };
    render(<PickCard evidenceExpanded item={fromDailyPick(gated)} />);
    expect(screen.getByText("仅观察")).toBeTruthy();
    expect(screen.getByText(/不给出买入参考区间/)).toBeTruthy();
    // 止损与失效条件仍在（观察也要有退出依据）
    expect(screen.getByText("止损参考")).toBeTruthy();
  });

  it("盘前名单不缺维度：不渲染口径注记", () => {
    const { container } = render(<PickCard evidenceExpanded item={fromDailyPick(base)} />);
    expect(container.textContent).not.toContain("不含收盘六维评分");
  });

  it("执行复核与参考价分开显示，明确不等于成交", () => {
    const withExecution: DailyPickItem = {
      ...base,
      tradability: {level: "可参与", basis: "fixture 当前未封板"},
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
    Object.freeze(withExecution.execution!.executable_snapshot);
    Object.freeze(withExecution.execution!);
    const item = fromDailyPick(withExecution);
    const page = render(<PickCard evidenceExpanded item={item} />);
    expect(screen.getByText(/参考价 40.36/)).toBeTruthy();
    expect(screen.getByText(/执行快照 40.88/)).toBeTruthy();
    const chip = screen.getByText("执行复核·通过");
    expect(chip.getAttribute("title")).toContain("不表示已成交");
    page.rerender(<PickCard evidenceExpanded item={item} readError />);
    expect(screen.queryByText("执行复核·通过")).toBeNull();
    expect(screen.queryByText("可参与评估 · 当前未封板")).toBeNull();
    expect(screen.getByText("执行复核·待读取").getAttribute("title")).toContain("ODV-demo");
    expect(screen.getByText(/上次执行快照 40.88/).getAttribute("title")).toContain("sina_market");
    readBasis("梯队");
    expect(within(screen.getByLabelText("入选依据核对")).getByText(/涨停池精确判定/)).toBeTruthy();
    expect(withExecution.execution!.gate_decision).toBe("passed");
    expect(withExecution.execution!.executable_snapshot.state).toBe("ready");
    page.rerender(<PickCard evidenceExpanded item={item} readError={false} />);
    expect(screen.getByText("执行复核·通过")).toBeTruthy();
    expect(screen.getByText("可参与评估 · 当前未封板")).toBeTruthy();
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
    render(<PickCard evidenceExpanded item={normalized} />);
    expect(screen.getByTitle("执行快照非可用状态，主价格回退为组合生成/首见参考价（不是成交价）")).toBeTruthy();
    const chip = screen.getByText("执行快照·陈旧");
    expect(chip.getAttribute("title")).toContain("上游停更");
  });
});

describe("首次加入记录的来源与参考身份", () => {
  const intradayEntry: NonNullable<IntradayTopStock["selection_entry"]> = {
    state: "recorded", selected_at: "2026-10-09T10:12:00+08:00", reference_price: 5.10,
    reference_change_pct: 3.8, quote_as_of: "2026-10-09T10:11:58+08:00", source: "intraday_rank_archive",
    source_version: "intraday-original", semantics: "observation_only_not_fill",
  };
  const dailyEntry: NonNullable<DailyPickItem["selection_entry"]> = {
    ...intradayEntry, selected_at: "2026-10-09T09:26:02+08:00", reference_price: 4.90,
    quote_as_of: "2026-10-09T09:26:00+08:00", source: "daily_generation", source_version: "daily-original",
  };

  it("explains an unarchived reference without implying it was never selected or copying its current price", () => {
    const item = fromIntradayStock({...topPath, reference_only: true});
    expect(item.referenceOnly).toBe(true);
    render(<PickCard evidenceExpanded item={item} />);
    const entry = screen.getByLabelText("盘中首次加入记录");
    expect(entry.textContent).toContain("当前仅参考，未查到已归档入选依据");
    expect(entry.textContent).not.toMatch(/首次加入 未记录|加入参考价 未记录|从未入选|5\.42/);
    fireEvent.click(within(entry).getByRole("button",{name:"加入记录的完整依据"}));
    expect(entry.textContent).toContain("未用现价或读取时间补填");
  });

  it("preserves a recorded join after the stock becomes a sealed reference and labels its snapshot clock", () => {
    render(<PickCard evidenceExpanded item={fromIntradayStock({...topPath, price: 5.42, reference_only: true, selection_entry: intradayEntry})} />);
    const entry = screen.getByLabelText("盘中首次加入记录");
    expect(entry.textContent).toContain("盘中首次加入");
    expect(entry.textContent).toContain("10:12:00");
    expect(entry.textContent).toContain("加入参考价 ¥5.10");
    expect(entry.textContent).not.toContain("未查到");
    fireEvent.click(within(entry).getByRole("button",{name:"加入记录的完整依据"}));
    expect(within(entry).getByText("行情快照时间").nextElementSibling?.textContent).toBe("2026-10-09 10:11:58");
    expect(entry.textContent).not.toContain("报价源时间");
    expect(entry.textContent).toContain("intraday-original");
  });

  it("keeps daily and intraday entry clocks and prices independent on one overlapping card", () => {
    render(<PickCard evidenceExpanded item={fromIntradayStock({...topPath, symbol: base.symbol, selection_entry: intradayEntry})}
      dailyEvidence={{item: {...base, selection_entry: dailyEntry}, date: "2026-10-09", generatedAt: "2026-10-09T09:26:00+08:00", version: "daily-original"}} />);
    const intraday = screen.getByLabelText("盘中首次加入记录");
    const daily = screen.getByLabelText("每日首次加入记录");
    expect(intraday.textContent).toContain("10:12:00");
    expect(intraday.textContent).toContain("¥5.10");
    expect(intraday.textContent).not.toContain("09:26:02");
    expect(daily.textContent).toContain("09:26:02");
    expect(daily.textContent).toContain("¥4.90");
    fireEvent.click(within(daily).getByRole("button",{name:"加入记录的完整依据"}));
    expect(daily.textContent).toContain("报价源时间");
    expect(daily.textContent).not.toContain("行情快照时间");
  });

  it("shows an available daily record while explaining the separate missing intraday archive", () => {
    render(<PickCard evidenceExpanded item={fromIntradayStock({...topPath, symbol: base.symbol})}
      dailyEvidence={{item: {...base, selection_entry: dailyEntry}, date: "2026-10-09", generatedAt: null, version: "daily-original"}} />);
    expect(screen.getByLabelText("盘中首次加入记录").textContent).toContain("首次入选依据未记录");
    expect(screen.getByLabelText("盘中首次加入记录").textContent).not.toContain("¥4.90");
    expect(screen.getByLabelText("每日首次加入记录").textContent).toContain("¥4.90");
  });

  it("preserves the archive-read failure reason rather than describing the stock as never selected", () => {
    render(<PickCard evidenceExpanded item={fromIntradayStock({...topPath, reference_only: true, selection_entry: {
      ...intradayEntry, state: "missing", selected_at: null, reference_price: null, reference_change_pct: null,
      quote_as_of: null, source_version: null, reason: "首次入选证据读取失败；未用当前报价补填",
    }})} />);
    const entry = screen.getByLabelText("盘中首次加入记录");
    fireEvent.click(within(entry).getByRole("button",{name:"加入记录的完整依据"}));
    expect(entry.textContent).toContain("首次入选证据读取失败");
    expect(entry.textContent).not.toContain("从未入选");
  });

  it("does not display an entry from a different source or an unrelated daily stock", () => {
    render(<PickCard evidenceExpanded item={fromIntradayStock({...topPath, selection_entry: dailyEntry})}
      dailyEvidence={{item: {...base, selection_entry: dailyEntry}, date: "2026-10-09", generatedAt: null, version: "daily-original"}} />);
    expect(screen.getByLabelText("盘中首次加入记录").textContent).toContain("首次入选依据未记录");
    expect(screen.queryByLabelText("每日首次加入记录")).toBeNull();
    expect(screen.getByLabelText("盘中首次加入记录").textContent).not.toContain("09:26:02");
  });

  it("retains a partial join clock while identifying its missing original price", () => {
    render(<PickCard evidenceExpanded item={fromIntradayStock({...topPath, selection_entry: {
      ...intradayEntry, state: "partial", reference_price: null, reference_change_pct: Number.NaN,
      reason: "首次加入已记录，原参考价缺项或归档数据降级",
    }})} />);
    const entry = screen.getByLabelText("盘中首次加入记录");
    expect(entry.textContent).toContain("10:12:00");
    expect(entry.textContent).toContain("加入参考价 缺项");
    expect(entry.textContent).toContain("依据不完整");
    expect(entry.textContent).not.toMatch(/NaN|5\.42/);
  });
});

describe("PickCard · 盘中名单（fromIntradayStock）", () => {
  it.each([
    ["当前未封板（距封板 2.50pct）", "当前未封板（旧口径：距封板判定线 2.50 个百分点）"],
    ["距实际涨停价还需上涨 2.33%", "距实际涨停价还需上涨 2.33%"],
  ])("入选、联动依据与提示使用正确距离单位：%s", (basis, displayed) => {
    const linkage = { level: "高" as const, basis };
    const top: IntradayTopStock = { ...topPath, pick_basis: basis, linkage };
    const accordion: OpportunityStock = { ...accordionPath, basis, linkage };
    const { rerender } = render(<PickCard evidenceExpanded item={fromIntradayStock(top)} />);
    readBasis("入选");
    expect(within(screen.getByLabelText("入选依据核对")).getByText(displayed)).toBeTruthy();
    expect(screen.getByText("联动确定性·高").getAttribute("title")).toContain(displayed);

    rerender(<PickCard evidenceExpanded item={fromIntradayStock(accordion)} />);
    fireEvent.click(screen.getByRole("button",{name:"入选解读"}));
    readBasis("联动");
    expect(within(screen.getByLabelText("入选依据核对")).getByText(displayed)).toBeTruthy();
    expect(screen.getByText("联动确定性·高").getAttribute("title")).toContain(displayed);
    expect(top.pick_basis).toBe(basis);
    expect(accordion.basis).toBe(basis);
    expect(linkage.basis).toBe(basis);
  });

  it("分层名单：入选理由与官方涨停原因都渲染（涨停原因来自 ladder 行，不再恒空）", () => {
    render(<PickCard evidenceExpanded item={fromIntradayStock(topPath)} />);
    expect(screen.getByText("入选")).toBeTruthy();
    expect(screen.getByText("涨停原因")).toBeTruthy();
    expect(screen.getByText("T2 跟踪档")).toBeTruthy();
    expect(screen.getByRole("region", {name:"数据源归因原文"}).textContent).toContain("商业航天+军工+机器人");
    expect(screen.getByRole("region", {name:"数据源归因原文"}).textContent).toContain("非已核实的涨停因果");
  });

  it("手风琴路径没有「入选」这一行：整行不出现（不许拿「入选 —」冒充缺失）", () => {
    const { container } = render(<PickCard evidenceExpanded item={fromIntradayStock(accordionPath)} />);
    expect(container.textContent).toContain("涨停原因");
    readBasis("涨停原因");
    expect(container.textContent).toContain("存储芯片+稀土永磁");
    // 标题行「入选原因」两侧统一；但**行标签**「入选」只在分层名单（pick_basis）出现
    expect(screen.getByText("入选原因")).toBeTruthy();
    expect(screen.queryByText("入选")).toBeNull();
    expect(container.textContent).not.toContain("跟踪档");
  });

  it("入选原因标题两侧统一（盘前 2026-09-10 用户反馈「怎么没有入选原因」）", () => {
    render(<PickCard evidenceExpanded item={fromDailyPick(base)} />);
    expect(screen.getByText("入选原因")).toBeTruthy();
    // 盘前的「入选原因」是七维依据行，不是空壳（梯队既是评分条标签也是依据行标签）
    expect(screen.getAllByText("梯队").length).toBeGreaterThan(1);

    cleanup();
    render(<PickCard evidenceExpanded item={fromIntradayStock(topPath)} />);
    expect(screen.getByText("入选原因")).toBeTruthy();
  });

  it("判定徽标三态：unknown 显示「未判定」而非「低」，依据进 title", () => {
    render(<PickCard evidenceExpanded item={fromIntradayStock(accordionPath)} />);
    const chip = screen.getByText("辨识度·未判定");
    expect(chip.getAttribute("title")).toContain("人气榜不可用");
    expect(screen.getByText("确定性·高")).toBeTruthy();
  });

  it("盘中缺每日评分时保留两种来源，不承诺次日生成或实际成交", () => {
    const { container } = render(<PickCard evidenceExpanded item={fromIntradayStock(topPath)} />);
    // 不适用 ⇒ 整节不渲染（盘中路径不出现盘前概念的买入区间）
    expect(container.textContent).not.toContain("买入参考区间");
    // 但必须显式说明口径，用户能看出「为什么这张卡没有评分」
    expect(container.textContent).toContain("评分、估值与参考区间由每日组合原生成时点提供");
    expect(container.textContent).not.toContain("收盘后生成次日名单");
    expect(container.textContent).toContain("参考信息不代表成交");
    expect(container.textContent).toContain("盘中实时口径");
  });

  it("现价/止损/出场纪律与盘前同构渲染（补全单点收口在 attach_risk_fields）", () => {
    render(<PickCard evidenceExpanded item={fromIntradayStock(topPath)} />);
    expect(screen.getByText("5.42")).toBeTruthy();
    expect(screen.getByText("止损参考")).toBeTruthy();
    expect(screen.getByText(/4\.99/)).toBeTruthy();
    expect(screen.getByText("跟踪止盈")).toBeTruthy();
  });

  it("来源徽标与持仓标注：来源恒显示，持仓按传入值显示", () => {
    const { container } = render(
      <PickCard evidenceExpanded item={fromIntradayStock(topPath)} positionLabel="sim" />,
    );
    expect(container.textContent).toContain("盘中跟踪");
    expect(container.textContent).not.toContain("已模拟持仓");

    cleanup();
    const noPos = render(<PickCard evidenceExpanded item={fromIntradayStock(topPath)} />);
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

    const { container } = render(<PickCard evidenceExpanded item={fromIntradayStock(noExit)} />);
    expect(container.textContent).toContain("止损参考"); // 止损仍在 ⇒ 分节仍渲染
    expect(container.textContent).not.toContain("回落 0%");
    expect(container.textContent).not.toContain("跟踪止盈");
  });

  it("exit 存在但缺 trailing_pct 时不渲染跟踪止盈行（0 / 占位都是臆造）", () => {
    const onlyNote: IntradayTopStock = { ...topPath, exit_plan: { note: "错了要快" } };
    const c = fromIntradayStock(onlyNote);
    expect(c.exit?.trailing_pct).toBeNull();

    const { container } = render(<PickCard evidenceExpanded item={c} />);
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

  it("涨停梯队原因缺失时明示，不能静默隐藏证据缺项", () => {
    const bare: OpportunityStock = { ...accordionPath, reason: null };
    const card = fromIntradayStock(bare);
    expect(card.basisRows).toHaveLength(1);

    const { container } = render(<PickCard evidenceExpanded item={card} />);
    expect(container.textContent).not.toContain("入选 —");
    readBasis("涨停原因");
    expect(container.textContent).toContain("数据源未提供涨停原因");
    expect(screen.getByRole("button", {name: "入选解读"})).toBeTruthy();
    expect(card.basisRows.some(row => row.label === "入选")).toBe(false);
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
    const disclosure = screen.getByText("核对依据与时点对照").closest("details")!;
    expect(disclosure.open).toBe(false);
    expect(screen.getByText(gate.advice).closest("details")).toBeNull();
    for (const reason of gate.reasons) expect(within(disclosure).getByText(`· ${reason}`)).toBeTruthy();
    fireEvent.click(within(disclosure).getByText("核对依据与时点对照"));
    expect(disclosure.open).toBe(true);
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
    expect(screen.getByText(/已撤除买入区间/).closest("details")).toBeNull();
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
    expect(screen.getByText(/保留买入区间/).closest("details")).toBeNull();
    expect(screen.getByText(/非空仓信号/)).toBeTruthy();
    expect(screen.queryByText(/已撤除买入区间/)).toBeNull();
  });

  it("复核不可用且未取得触发状态时，警示仍常驻而不是消失", () => {
    render(<StandAsideBanner gate={{ stand_aside: false, level: "none", reasons: [], advice: "当前风险状态待核", gate_source: "unavailable", gate_note: "实时情绪源读取失败" }} />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("实时情绪源读取失败").closest("details")).toBeNull();
    expect(screen.queryByTestId("stand-aside-cleared")).toBeNull();
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
    expect(screen.getByText(liveCleared.advice).closest("details")).toBeNull();
    expect(screen.getByText(/生成时撤除决定/).closest("details")).toBeNull();
    expect(screen.getByTestId("stand-aside-comparison").closest("details")?.open).toBe(false);
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
    expect(screen.getByText(/未触发闸门/).closest("details")).toBeNull();
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
    expect(screen.getByText(degraded.gate_note!).closest("details")).toBeNull();
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
    render(<PickCard evidenceExpanded item={fromIntradayStock(participant)} />);
    expect(screen.getByText("可参与评估 · 当前未封板")).toBeTruthy();
    expect(screen.getByText(/联动确定性·高/)).toBeTruthy();
    // 依据行：分层名单走 pick_basis（"为什么排在这一档"）
    expect(screen.getByText("入选")).toBeTruthy();
    readBasis("入选");
    expect(within(screen.getByLabelText("入选依据核对")).getByText(/题材内涨停 3 家形成集中/)).toBeTruthy();
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
    render(<PickCard evidenceExpanded item={fromIntradayStock(acc)} />);
    expect(screen.getByText("联动")).toBeTruthy();
    readBasis("联动");
    expect(within(screen.getByLabelText("入选依据核对")).getByText(/本股当前未封板/)).toBeTruthy();
  });

  it("参考区梯队：渲染当前不可参与 + 首封时间证据", () => {
    render(<PickCard evidenceExpanded item={fromIntradayStock(ladder)} />);
    expect(screen.getByText("当前不可参与")).toBeTruthy();
    expect(screen.getByText("首封")).toBeTruthy();
    expect(screen.getByText("首封")).toBeTruthy();
    expect(screen.getByLabelText("入选依据核对").textContent).toContain("09:25:00");
    // 参考区不该出现「可参与」——两种口径不能混用同一个徽标
    expect(screen.queryByText("可参与评估 · 当前未封板")).toBeNull();
  });

  it("板块徽标：把「这个板我有没有权限买」放在卡片上", () => {
    // 2026-09-15 用户「只有主板的权限现在」——板块是账户口径的可视证据，
    // 而不是要用户记住代码段规则（名单本身已按权限过滤，这里是可核对性）
    render(<PickCard evidenceExpanded item={fromIntradayStock({ ...participant, board: "深市主板" })} />);
    expect(screen.getByText("深市主板")).toBeTruthy();
    // 字段缺失时整块不渲染（不拿「板块 --」把缺数据伪装成有数据）
    const { container } = render(<PickCard evidenceExpanded item={fromIntradayStock(participant)} />);
    expect(container.textContent).not.toContain("深市主板");
  });

  it("盘前名单：入选原因里带「来源」行（题材联动股可追溯）", () => {
    render(
      <PickCard evidenceExpanded
        item={fromDailyPick({
          ...base,
          source: "theme_linkage",
          source_basis: "题材内涨停 4 家形成集中，本股当前未封板（3.0%）",
          tradability: { level: "可参与", basis: "当前未封板，可进入参与评估；未核盘口深度/排队，不保证成交" },
        })}
      />,
    );
    expect(screen.getByText("来源")).toBeTruthy();
    readBasis("来源");
    expect(within(screen.getByLabelText("入选依据核对")).getByText(/本股当前未封板/)).toBeTruthy();
    expect(screen.getByText("可参与评估 · 当前未封板")).toBeTruthy();
  });
});


describe("维度读取事实", () => {
  it("does not present a missing-data neutral placeholder as measured capital evidence", () => {
    const pick: DailyPickItem = {...base, dimension_evidence: {capital: {state: "missing", reason: "资金源未提供"}, news: {state: "valid_empty", reason: "成功读取，无匹配事件"}}};
    render(<PickCard evidenceExpanded item={fromDailyPick(pick)} />);
    expect(screen.queryByRole("meter", {name: "资金评分"})).toBeNull();
    expect(screen.getByLabelText("资金依据缺失，计算占位 50")).toBeTruthy();
    expect(screen.getByRole("meter", {name: "消息评分"})).toBeTruthy();
    expect(screen.getByText(/资金源未提供/)).toBeTruthy();
  });
});

it("keeps partial first-entry facts and explains why the original quote is untrusted", () => {
  render(<PickCard evidenceExpanded item={fromDailyPick({...base, selection_entry: {
    state: "partial", selected_at: "2026-10-09T10:02:00+08:00", reference_price: 38.8,
    reference_change_pct: 5.2, quote_as_of: "2099-01-01T10:00:00+08:00", source: "daily_generation",
    source_version: "original-v", semantics: "observation_only_not_fill", reason: "首次加入已记录；原报价日期与首次入选日不同",
  }})} />);
  expect(screen.getByText("依据不完整")).toBeTruthy();
  expect(screen.getByText("2026-10-09 10:02:00")).toBeTruthy();
  expect(screen.getByText("加入参考价 ¥38.80")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"加入记录的完整依据"}));
  expect(screen.getByText("首次加入已记录；原报价日期与首次入选日不同")).toBeTruthy();
});


it("retains the original daily evidence behind one intraday card", () => {
  const show = vi.fn();
  render(<PickCard evidenceExpanded item={fromIntradayStock({...topPath, symbol: base.symbol})} dailyEvidence={{item: base, date: "2026-10-09", generatedAt: "2026-10-09T09:26:00+08:00", version: "version-A"}} onShowDailyEvidence={show} />);
  fireEvent.click(screen.getByRole("button", {name: "每日依据"}));
  expect(show).toHaveBeenCalledWith(base);
  expect(show.mock.calls[0][0].buy_range).toEqual(base.buy_range);
  expect(screen.getByText(/每日 2026-10-09/).getAttribute("title")).toBe("version-A");
});

 it("keeps every invalidation visible, including fourth and fifth conditions", () => {
  render(<PickCard evidenceExpanded item={fromDailyPick({...base, invalidations:["风险1","风险2","风险3","第4条：条件未成立","第5条：原判断失效"]})}/>);
  expect(screen.getByLabelText("失效条件").textContent).toContain("第4条：条件未成立");
  expect(screen.getByLabelText("失效条件").textContent).toContain("第5条：原判断失效");
 });


it("keeps the compact card short, exposes late limitations before the full record, and preserves every original condition in evidence", () => {
  const basis = "原消息记录了材料报价和需求变化。".repeat(8) + "公司尚未确认新增订单；仅为题材关联，不代表实际受益。";
  const item = fromIntradayStock({...topPath, pick_basis:basis});
  render(<PickCard item={item}/>);
  const brief = screen.getByLabelText("入选简述");
  expect(brief.textContent!.length).toBeLessThan(160);
  expect(brief.textContent).toContain("仍有待确认事项");
  expect(brief.textContent).toContain("注意适用范围与限制");
  expect(screen.queryByLabelText("入选依据核对")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"入选解读"}));
  expect(screen.queryByLabelText("入选依据核对")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"原筛选完整依据"}));
  expect(screen.getByLabelText("入选依据核对").textContent).toContain(basis);
  expect(item.basisRows[0].value).toBe(basis);
});

it("keeps decision references outside the collapsed evidence: quote, boards, distinctiveness and stop price", () => {
 const item=fromIntradayStock({...topPath,boards:3});
 render(<PickCard item={item}/>);
 expect(screen.getByText("5.42")).toBeTruthy();expect(screen.getByText("+10.09%")).toBeTruthy();
 expect(screen.getByText("中军 · 3板")).toBeTruthy();expect(screen.getByText("辨识度·中")).toBeTruthy();
 expect(screen.getByLabelText("个股关键参考").textContent).toContain("4.99（-8%）");
 expect(screen.queryByLabelText("入选依据核对")).toBeNull();
 fireEvent.click(screen.getByRole("button",{name:"入选解读"}));
 fireEvent.click(screen.getByRole("button",{name:"原筛选完整依据"}));
 expect(screen.getByText("档位基准 8% → 取 8.0%（clamp 3%~12%）")).toBeTruthy();
});

it("explains saved system evidence instead of substituting the vendor limit-up note", () => {
 const item=fromIntradayStock({...topPath, pick_basis:"", reason:"供应商短语：存储芯片"});
 item.linkage=null;item.distinctiveness=null;item.certainty=null;
 const reading=selectionReading(item);
 expect(reading.why).toContain("没有保存系统筛选理由");
 expect(reading.why).not.toContain("存储芯片");
 const daily=selectionReading(fromDailyPick({...base, source_basis:undefined, sub_scores:{fundamental:90}, bases:{fundamental:"营收和净利润增长较好"}}));
 expect(daily.why).toContain("营收和净利润增长较好");
 expect(daily.supports.length).toBeGreaterThan(0);
});

it("uses the recorded selection rationale before a high-scoring dimension as the reason", () => {
 const item=fromDailyPick({...base,source_basis:"因已公布的收购计划纳入观察，仍待审议。",sub_scores:{fundamental:90},bases:{fundamental:"估值中性，盈利同比增长"}});
 const reading=selectionReading(item);
 expect(reading.why).toBe("因已公布的收购计划纳入观察，仍待审议。");
 expect(reading.supports[0].value).toContain("估值中性");
});

it("has one selection reader and loads related messages only after opening it", () => {
 render(<PickCard item={fromDailyPick(base)}/>);
 expect(screen.queryByRole("button",{name:"关联消息"})).toBeNull();
 expect(screen.queryByLabelText("相关消息与AI解读")).toBeNull();
 fireEvent.click(screen.getByRole("button",{name:"入选解读"}));
 expect(screen.getByLabelText("本次筛选考虑")).toBeTruthy();
 expect(screen.getByLabelText("相关消息与AI解读")).toBeTruthy();
 expect(screen.queryByLabelText("入选依据核对")).toBeNull();
 expect(screen.getByLabelText("TradingAgents完整研究").textContent).toContain("未读取");
 fireEvent.click(screen.getByRole("button",{name:"原筛选完整依据"}));
 expect(screen.getByLabelText("入选依据核对")).toBeTruthy();
});

it("keeps exact archived event references in the combined reader and performs one read only on open", async () => {
 const spy=vi.spyOn(API,"getStockAttribution").mockResolvedValue({schema_version:1,symbol:"600995",count:0,items:[]} as API.StockAttributionPayload);
 try {
  render(<PickCard item={fromDailyPick({...base,symbol:"600995",related_event_refs:[{event_id:18,version_id:20,observation_id:19,state:"active",available_at:"2026-10-12T09:31:00+08:00"}]})}/>);
  expect(spy).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button",{name:"入选解读"}));
  await waitFor(()=>expect(spy).toHaveBeenCalledTimes(1));
  expect(spy).toHaveBeenCalledWith("600995");
  fireEvent.click(screen.getByRole("button",{name:"公司依据、完整传导与相关消息"}));
  fireEvent.click(screen.getByText("选股记录事件引用"));
  expect(screen.getByText(/原解释 #20/).textContent).toContain("事件 #18");
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button",{name:"关闭"}));
  expect(screen.getByRole("button",{name:"入选解读"}).getAttribute("aria-expanded")).toBe("false");
  await waitFor(()=>expect(screen.queryByLabelText("相关消息与AI解读")).toBeNull());
  expect(spy).toHaveBeenCalledTimes(1);
 } finally {spy.mockRestore();}
});

it.each([false, true])("keeps every card short without prefetching company or research and reads only the deliberately opened drawer (compact=%s)", async compact => {
 const attribution=vi.spyOn(API,"getStockAttribution").mockResolvedValue({schema_version:1,symbol:"600222",count:0,items:[]} as API.StockAttributionPayload);
 const research=vi.spyOn(AgentAPI,"getTradingAgentsResearch").mockRejectedValue(new Error("isolated research read"));
 const start=vi.spyOn(AgentAPI,"createAgentTask").mockRejectedValue(new Error("must not start"));
 try {
  render(<>{["600111","600222","600333"].map(symbol=><PickCard key={symbol} compact={compact} item={fromDailyPick({...base,symbol})}/>)}</>);
  const entries=screen.getAllByRole("button",{name:"入选解读"});
  expect(entries).toHaveLength(3);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(attribution).not.toHaveBeenCalled();
  expect(research).not.toHaveBeenCalled();
  fireEvent.click(entries[1]);
  await waitFor(()=>expect(attribution).toHaveBeenCalledExactlyOnceWith("600222"));
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(research).not.toHaveBeenCalled();
  expect(start).not.toHaveBeenCalled();
  expect(screen.getAllByRole("button",{name:"入选解读",hidden:true})).toHaveLength(3);
 } finally {attribution.mockRestore();research.mockRestore();start.mockRestore();}
});

it("opens a bottom-entry drawer without navigation or moving the card, and retires it on identity change", () => {
 const item=fromDailyPick(base);
 const view=render(<PickCard item={item}/>);
 const trigger=screen.getByRole("button",{name:"入选解读"});
 expect(within(screen.getByLabelText("关联研究入口")).getByRole("button",{name:"入选解读"})).toBe(trigger);
 fireEvent.click(trigger);
 const dialog=screen.getByRole("dialog",{name:`${base.name} · 入选解读`});
 expect(dialog.classList.contains("motion-drawer")).toBe(true);
 expect(dialog.parentElement?.parentElement).toBe(document.body);
 expect(trigger.getAttribute("aria-expanded")).toBe("true");
 fireEvent.click(within(dialog).getByLabelText("本次筛选考虑"));
 expect(screen.getByRole("dialog")).toBe(dialog);
 view.rerender(<PickCard item={{...item,price:41}}/>);
 expect(screen.getByRole("dialog")).toBe(dialog);
 view.rerender(<PickCard item={{...item,selectionEntry:{state:"missing",selected_at:null,reference_price:null,reference_change_pct:null,quote_as_of:null,source:"daily_generation",semantics:"observation_only_not_fill",source_version:"another-saved-version"}}}/>);
 expect(screen.queryByRole("dialog")).toBeNull();
 fireEvent.click(screen.getByRole("button",{name:"入选解读"}));
 view.rerender(<PickCard item={{...item,symbol:"600127",name:"另一记录"}}/>);
 expect(screen.queryByRole("dialog")).toBeNull();
 expect(screen.getByRole("button",{name:"入选解读"}).getAttribute("aria-expanded")).toBe("false");
});

describe("PickCard · compact local inspection", () => {
 const metrics: SelectionMetrics = {schema_version:1,producer:"linkage_candidates",snapshot_version:"original-snapshot-v1",items:[
  {key:"change_pct",label:"当日涨幅",value:3.12,unit:"%",state:"ready",source:"fixture_snapshot",as_of:"2026-10-23T09:59:58+08:00",received_at:"2026-10-23T10:00:01+08:00",thresholds:[{key:"minimum",label:"候选门槛",value:2,operator:">="}],basis:"当日涨幅跨过候选门槛。此项只是所记录时点的单项量价事实，不能代表同一股票的全部筛选条件，也不能替代账户权限、可执行价格或流动性复核。尚未满足真实可执行价格与流动性要求。"},
  {key:"amount",label:"成交额",value:123000000,unit:"元",state:"ready",source:null,as_of:null,received_at:null,thresholds:[],basis:"原快照成交额，不能推断可成交量。"},
  {key:"theme_limit_ups",label:"题材涨停",value:5,unit:"家",state:"ready",source:null,as_of:null,received_at:null,thresholds:[],basis:"原题材成员中5家涨停，不代表这只股票涨停。"},
  {key:"theme_share",label:"题材占比",value:0,unit:"%",state:"missing",source:null,as_of:null,received_at:null,thresholds:[],basis:"分母来源缺失，零为不可用占位。"},
  {key:"limit_up_gap_pct",label:"距涨停",value:50,unit:"%",state:"unknown",source:null,as_of:null,received_at:null,thresholds:[],basis:"原快照尚未核实涨停价。"},
 ]};
 const entry: NonNullable<IntradayTopStock["selection_entry"]> = {state:"recorded",selected_at:"2026-10-23T09:58:00+08:00",reference_price:5.2,reference_change_pct:1.8,quote_as_of:"2026-10-23T09:57:58+08:00",source:"intraday_rank_archive",source_version:"entry-v1",semantics:"observation_only_not_fill"};
 const compactItem = () => fromIntradayStock({...topPath,selection_metrics:metrics,selection_entry:entry,tradability:{level:"可参与",basis:"原记录当前未封板"}});

 it("inspects fields in an explicit linear keyboard order without hover, focus or modified-key side effects or remote reads", () => {
  const attribution=vi.spyOn(API,"getStockAttribution").mockRejectedValue(new Error("local inspection must not read company evidence"));
  const research=vi.spyOn(AgentAPI,"getTradingAgentsResearch").mockRejectedValue(new Error("local inspection must not read research"));
  const start=vi.spyOn(AgentAPI,"createAgentTask").mockRejectedValue(new Error("local inspection must not start research"));
  try {
   render(<><input aria-label="本卡备注"/><PickCard compact item={compactItem()}/></>);
   const group=screen.getByRole("group",{name:"本卡原测量"});
   const buttons=within(group).getAllByRole("button");
   expect(buttons.map(button=>button.tabIndex)).toEqual([0,-1,-1,-1,-1]);
   expect(document.getElementById(group.getAttribute("aria-describedby")!)?.textContent).toContain("按显示顺序");
   buttons[0].focus();
   expect(buttons[0].getAttribute("aria-pressed")).toBe("false");
   fireEvent.click(buttons[0]);
   fireEvent.mouseEnter(buttons[1]);buttons[1].focus();
   expect(buttons[0].getAttribute("aria-pressed")).toBe("true");
   buttons[0].focus();
   for(const [key,index] of [["ArrowRight",1],["ArrowDown",2],["ArrowLeft",1],["ArrowUp",0],["End",4],["ArrowRight",0],["ArrowLeft",4],["Home",0]] as const){
    expect(fireEvent.keyDown(document.activeElement!,{key})).toBe(false);
    expect(document.activeElement).toBe(buttons[index]);
    expect(buttons[index].getAttribute("aria-pressed")).toBe("true");
    expect(buttons.map(button=>button.tabIndex)).toEqual(buttons.map((_,i)=>i===index?0:-1));
   }
   fireEvent.click(buttons[0]);
   expect(buttons[0].getAttribute("aria-pressed")).toBe("true");
   for(const modifiers of [{isComposing:true},{keyCode:229},{shiftKey:true},{ctrlKey:true},{altKey:true},{metaKey:true}]){
    expect(fireEvent.keyDown(buttons[0],{key:"End",...modifiers})).toBe(true);
    expect(buttons[0].getAttribute("aria-pressed")).toBe("true");
   }
   const note=screen.getByRole("textbox",{name:"本卡备注"});note.focus();
   expect(fireEvent.keyDown(note,{key:"ArrowDown"})).toBe(true);
   expect(document.activeElement).toBe(note);
   expect(buttons[0].getAttribute("aria-pressed")).toBe("true");
   const basis=screen.getByLabelText("本卡所选短依据");
   expect(buttons[0].getAttribute("aria-controls")).toBe(basis.id);
   fireEvent.click(within(basis).getByRole("button",{name:"回看入选理由"}));
   expect(buttons.every(button=>button.getAttribute("aria-pressed")==="false")).toBe(true);
   expect(buttons[0].tabIndex).toBe(0);
   expect(attribution).not.toHaveBeenCalled();expect(research).not.toHaveBeenCalled();expect(start).not.toHaveBeenCalled();
  } finally {attribution.mockRestore();research.mockRestore();start.mockRestore();}
 });

 it("draws a small scale only for finite known daily rule scores, including genuine zero, never intraday measurements or unknowns", () => {
  const card=fromDailyPick({...base,sub_scores:{sentiment:0,news:80,tech:100,fundamental:Number.NaN,capital:101,echelon:55},dimension_evidence:{
   sentiment:{state:"success",reason:"已核0分"},news:{state:"partial",reason:"部分取得"},tech:{state:"valid_empty",reason:"有效空集"},
   fundamental:{state:"success",reason:"坏数字"},capital:{state:"success",reason:"越界数字"},
  }});
  const view=render(<PickCard compact item={card}/>);
  const group=screen.getByRole("group",{name:"本卡原规则分项"});
  expect(within(group).getAllByRole("meter")).toHaveLength(3);
  for(const [label,value] of [["情绪",0],["消息",80],["技术",100]] as const){
   const meter=within(group).getByRole("meter",{name:`${label}原规则分值`});
   expect(meter.getAttribute("aria-valuenow")).toBe(String(value));
   expect((meter.firstElementChild as HTMLElement).style.width).toBe(`${value}%`);
  }
  expect(within(group).queryByRole("meter",{name:"基本面原规则分值"})).toBeNull();
  expect(within(group).queryByRole("meter",{name:"资金原规则分值"})).toBeNull();
  expect(within(group).queryByRole("meter",{name:"梯队原规则分值"})).toBeNull();
  expect(document.getElementById(group.getAttribute("aria-describedby")!)?.textContent).toContain("不是上涨概率");
  view.rerender(<PickCard compact item={{...card,origin:"intraday"}}/>);
  expect(screen.queryByRole("meter")).toBeNull();
 });

 it("shows actual units and keeps the selected field's exact qualifier, thresholds and clocks locally reachable", () => {
  const attribution=vi.spyOn(API,"getStockAttribution").mockRejectedValue(new Error("local inspection must not read company evidence"));
  const research=vi.spyOn(AgentAPI,"getTradingAgentsResearch").mockRejectedValue(new Error("local inspection must not read research"));
  try {
  render(<PickCard compact item={compactItem()}/>);
  const group=screen.getByRole("group",{name:"本卡原测量"});
  expect(within(group).getAllByRole("button")).toHaveLength(5);
  expect(within(group).getByRole("button",{name:"核对成交额，1.23亿"})).toBeTruthy();
  expect(within(group).getByRole("button",{name:"核对题材占比，缺项"})).toBeTruthy();
  expect(within(group).getByRole("button",{name:"核对距涨停，待核"})).toBeTruthy();
  expect(screen.queryByRole("meter")).toBeNull();
  const field=within(group).getByRole("button",{name:"核对当日涨幅，3.12%"});
  field.focus();fireEvent.click(field);
  expect(document.activeElement).toBe(field);
  expect(field.getAttribute("aria-pressed")).toBe("true");
  const basis=screen.getByLabelText("本卡所选短依据");
  expect(basis.querySelector("p")?.textContent).toBe(`${metrics.items[0].basis.slice(0,68)}…`);
  const original=within(basis).getByText("核对本项完整依据").closest("details")!;
  expect(original.open).toBe(false);
  fireEvent.click(within(basis).getByText("核对本项完整依据"));
  expect(original.open).toBe(true);
  expect(within(original).getByText(metrics.items[0].basis)).toBeTruthy();
  expect(original.textContent).toContain("候选门槛 ≥2%");
  expect(original.textContent).toContain("09:59:58");
  expect(original.textContent).toContain("10:00:01");
  expect(original.textContent).toContain("original-snapshot-v1");
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(within(basis).getByRole("button",{name:"回看入选理由"}));
  expect(within(basis).getByText("为什么入选")).toBeTruthy();
  expect(attribution).not.toHaveBeenCalled();expect(research).not.toHaveBeenCalled();
  } finally {attribution.mockRestore();research.mockRestore();}
 });

 it("separates real zero, partial evidence and missing score placeholders without manufacturing six measurements", () => {
  render(<PickCard compact readError item={fromDailyPick({...base,sub_scores:{sentiment:0,news:50,tech:65},dimension_evidence:{
   sentiment:{state:"success",reason:"原情绪项实测为0分"},news:{state:"missing",reason:"消息来源缺失"},tech:{state:"partial",reason:"仅部分走势依据"}},
   vetoes:["权限不满足，不能参与"],observation_only:true,follow_state:"blocked",chip_signal:{available:true,label:"派发警示",signal:"distribution_warning",reasons:["原筹码分布提示派发"]},
  })}/>);
  const group=screen.getByRole("group",{name:"本卡原规则分项"});
  expect(within(group).getAllByRole("button")).toHaveLength(6);
  expect(within(group).getByRole("button",{name:"核对情绪，0分"})).toBeTruthy();
  expect(within(group).getByRole("button",{name:"核对消息，缺项"})).toBeTruthy();
  expect(within(group).getByRole("button",{name:"核对技术，65分"})).toBeTruthy();
  expect(within(group).getByRole("button",{name:"核对基本面，待核"})).toBeTruthy();
  fireEvent.click(within(group).getByRole("button",{name:"核对消息，缺项"}));
  expect(screen.getByLabelText("本卡所选短依据").querySelector("p")?.textContent).toBe("消息来源缺失");
  expect(screen.getByText(/刷新失败，当前行情与参与条件待重新核实/)).toBeTruthy();
  expect(screen.getByText("参与条件待重新核实")).toBeTruthy();
  expect(screen.getByText("禁买")).toBeTruthy();
  expect(screen.getByText("存在 1 项不满足条件")).toBeTruthy();
  expect(screen.getByText("部分依据缺失")).toBeTruthy();
  expect(screen.getByText("派发警示")).toBeTruthy();
  expect(within(group).getAllByRole("meter")).toHaveLength(2);
  expect(within(group).getByRole("meter",{name:"情绪原规则分值"}).getAttribute("aria-valuenow")).toBe("0");
  expect(within(group).queryByRole("meter",{name:"消息原规则分值"})).toBeNull();
 });

 it("does not give a reference-only stock candidate measurements, and resets local inspection only on original identity change", () => {
  const item=compactItem();
  const view=render(<PickCard compact item={item}/>);
  const amount=screen.getByRole("button",{name:"核对成交额，1.23亿"});fireEvent.click(amount);
  view.rerender(<PickCard compact item={{...item,price:5.5}}/>);
  expect(screen.getByRole("button",{name:"核对成交额，1.23亿"}).getAttribute("aria-pressed")).toBe("true");
  view.rerender(<PickCard compact item={{...item,selectionEntry:{...entry,source_version:"entry-v2"}}}/>);
  expect(screen.getByRole("button",{name:"核对成交额，1.23亿"}).getAttribute("aria-pressed")).toBe("false");
  expect(screen.getByText("为什么入选")).toBeTruthy();
  view.rerender(<PickCard compact item={{...item,referenceOnly:true}}/>);
  expect(screen.queryByRole("group",{name:"本卡原测量"})).toBeNull();
  expect(screen.getByText("当前仅参考")).toBeTruthy();
 });

 it("retires an active group's real drawer immediately and does not resume reads or reopen it when the group returns", async () => {
  const attribution=vi.spyOn(API,"getStockAttribution").mockResolvedValue({schema_version:1,symbol:topPath.symbol,count:0,items:[]} as API.StockAttributionPayload);
  const research=vi.spyOn(AgentAPI,"getTradingAgentsResearch").mockRejectedValue(new Error("must remain unopened"));
  const start=vi.spyOn(AgentAPI,"createAgentTask").mockRejectedValue(new Error("must never start"));
  try {
   const item=compactItem();const view=render(<PickCard compact item={item}/>);
   expect(attribution).not.toHaveBeenCalled();
   fireEvent.click(screen.getByRole("button",{name:"入选解读"}));
   await waitFor(()=>expect(attribution).toHaveBeenCalledExactlyOnceWith(topPath.symbol));
   expect(screen.getByRole("dialog")).toBeTruthy();
   view.rerender(<PickCard compact active={false} item={item}/>);
   expect(screen.queryByRole("dialog")).toBeNull();
   expect(screen.queryAllByLabelText("相关消息与AI解读").every(node => !!node.closest("[data-exit-visual]"))).toBe(true);
   fireEvent.click(screen.getByRole("button",{name:"入选解读"}));
   expect(screen.queryByRole("dialog")).toBeNull();
   view.rerender(<PickCard compact active item={item}/>);
   expect(screen.queryByRole("dialog")).toBeNull();
   expect(attribution).toHaveBeenCalledTimes(1);
   expect(research).not.toHaveBeenCalled();expect(start).not.toHaveBeenCalled();
   fireEvent.click(screen.getByRole("button",{name:"入选解读"}));
   expect(screen.getByRole("dialog")).toBeTruthy();
   fireEvent.click(screen.getByRole("button",{name:"原筛选完整依据"}));
   expect(screen.getByLabelText("原画像与判定").textContent).toContain("题材启动；封单 1.2 亿");
  } finally {attribution.mockRestore();research.mockRestore();start.mockRestore();}
 });
});

it("keeps drawer pointer gestures outside the underlying spatial card", () => {
 const bounds=vi.spyOn(HTMLElement.prototype,"getBoundingClientRect").mockReturnValue(new DOMRect(0,0,400,800));
 try {
  const view=render(<PickCard item={fromDailyPick(base)}/>);
  fireEvent.click(screen.getByRole("button",{name:"入选解读"}));
  const content=within(screen.getByRole("dialog")).getByLabelText("本次筛选考虑");
  const move=new MouseEvent("pointermove",{bubbles:true,clientX:360,clientY:100});
  Object.defineProperties(move,{isPrimary:{value:true},pointerType:{value:"mouse"}});
  fireEvent(content,move);
  expect(view.container.querySelector("[data-spatial-active]")).toBeNull();
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button",{name:"关闭"}));
  expect(screen.getByRole("button",{name:"入选解读"}).getAttribute("aria-expanded")).toBe("false");
 } finally {bounds.mockRestore();}
});

it("接力预警同时展示全市场宽度，不把局部承压描述为市场普跌", () => {
  render(<StandAsideBanner gate={{stand_aside: true, level: "strong", reasons: ["昨日涨停中位 -1.81%"],
    advice: "市场情绪明显转弱", gate_source: "live", strip_buy_range: true,
    presentation: {scope: "涨停接力", headline: "涨停接力存在风险，需核对昨日强势股表现",
      facts: [{label: "全市场涨/跌", value: "4962/560家"}, {label: "今日跌停", value: "0家"}],
      note: "相位与昨日涨停收益不是两项独立证据。", source_state: "ready"}}} />);
  expect(screen.getByText("4962/560家").closest("details")).toBeNull();
  expect(screen.getByText("0家")).toBeTruthy();
  expect(screen.queryByText("市场情绪明显转弱")).toBeNull();
  expect(screen.getByText(/已撤除买入区间/)).toBeTruthy();
});

it("复核失败不会把保存的强风险标题冒充当前判断", () => {
  render(<StandAsideBanner gate={{stand_aside: true, level: "strong", reasons: [],
    advice: "市场情绪明显转弱", gate_source: "unavailable"}} />);
  expect(screen.queryByText("市场情绪明显转弱")).toBeNull();
  expect(screen.getByText("实时判断未更新，以下为生成时记录")).toBeTruthy();
});

it("紧凑风险栏保留当前风险与撤区间提示，市场数字在原依据中可核", () => {
  render(<StandAsideBanner compact gate={{stand_aside:true, level:"strong", reasons:["昨日涨停中位 -1.81%"],
    advice:"市场情绪明显转弱", gate_source:"live", strip_buy_range:true,
    presentation:{scope:"涨停接力",headline:"涨停接力存在风险，需核对昨日强势股表现",
      facts:[{label:"全市场涨/跌",value:"4962/560家"}],note:"局部风险不代表全市场普跌",source_state:"ready"}}}/>);
  expect(screen.getByText("涨停接力存在风险，需核对昨日强势股表现").closest("details")).toBeNull();
  expect(screen.getByText(/已撤除买入区间/).closest("details")).toBeNull();
  const disclosure=screen.getByText("4962/560家").closest("details")!;
  expect(disclosure.open).toBe(false);
  fireEvent.click(within(disclosure).getByText("核对依据与时点对照"));
  expect(disclosure.open).toBe(true);
  expect(within(disclosure).getByText("昨日涨停中位 -1.81%",{exact:false})).toBeTruthy();
});
