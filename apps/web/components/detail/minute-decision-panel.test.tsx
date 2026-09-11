import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MinuteDecisionPanel } from "@/components/detail/minute-decision-panel";
import {
  getMinuteDecisions,
  getMinuteSignals,
  type MinuteDecisionsPayload,
  type MinuteSignalsPayload,
} from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getMinuteSignals: vi.fn(),
    getMinuteDecisions: vi.fn(),
  };
});

const mockedSignals = vi.mocked(getMinuteSignals);
const mockedDecisions = vi.mocked(getMinuteDecisions);

const signals = (
  list: MinuteSignalsPayload["signals"],
  extra: Partial<MinuteSignalsPayload> = {},
): MinuteSignalsPayload => ({
  symbol: "600519",
  signals: list,
  observed: 3,
  degraded: [],
  recorded: 0,
  basis: {},
  ...extra,
});

const decisions = (items: MinuteDecisionsPayload["items"]): MinuteDecisionsPayload => ({
  items,
  settled: 0,
  outcomes: {},
  open_count: 0,
  note: "结算门槛 8bp（≈2×交易成本）",
});

const SIGNAL = {
  ts: "2026-09-11T01:35:00+00:00", // 北京 09:35
  signal_price: 10.02,
  bias: "低吸偏向",
  score: -0.61,
  confidence: "medium",
  triggered: [
    {
      key: "avg_dev",
      name: "均价线偏离",
      weight: 0.3,
      direction: -1,
      trigger_value: -2.2,
      threshold: -1.5,
      evidence: "偏离均价 -2.20% 后 3 分钟未创新低",
    },
  ],
  invalidate_condition: "跌破均价 3%",
};

describe("MinuteDecisionPanel 做 T 决策", () => {
  it("信号渲染偏向 + 依据 + 失效条件，且带不构成买卖建议声明", async () => {
    mockedSignals.mockResolvedValue(signals([SIGNAL]));
    mockedDecisions.mockResolvedValue(decisions([]));
    render(<MinuteDecisionPanel symbol="600519" />);
    await waitFor(() => expect(screen.getByText("低吸偏向")).toBeTruthy());
    const t = document.body.textContent ?? "";
    expect(t).toContain("偏离均价 -2.20%");
    expect(t).toContain("跌破均价 3%");
    expect(t).toContain("09:35"); // UTC → 北京时间
    expect(t).toContain("不构成买卖建议");
  });

  it("signals 为空列表 → 明示「确无信号」而不是空白（三态：空 ≠ 加载中）", async () => {
    mockedSignals.mockResolvedValue(signals([]));
    mockedDecisions.mockResolvedValue(decisions([]));
    render(<MinuteDecisionPanel symbol="600519" />);
    await waitFor(() =>
      expect(screen.getByText("当前无越过阈值的做 T 信号（引擎已在运行）")).toBeTruthy(),
    );
  });

  it("degraded 非空 → 如实标注降级项（不假装满配）", async () => {
    mockedSignals.mockResolvedValue(
      signals([SIGNAL], { degraded: ["avg_dev: 缺近 5 日波动率，偏离阈值未做自适应"] }),
    );
    mockedDecisions.mockResolvedValue(decisions([]));
    render(<MinuteDecisionPanel symbol="600519" />);
    await waitFor(() => expect(screen.getByText(/1 项输入缺失导致降级/)).toBeTruthy());
  });

  it("outcome=null 显示「待结算」而非错误结论；expired 明确「未判定」", async () => {
    mockedSignals.mockResolvedValue(signals([SIGNAL]));
    mockedDecisions.mockResolvedValue(
      decisions([
        {
          decision_id: "MD-20260911-600519-09350935",
          symbol: "600519",
          trade_date: "20260911",
          trigger_ts: "2026-09-11T01:35:00+00:00",
          signal_price: 10.02,
          bias: "低吸偏向",
          score: -0.61,
          confidence: "medium",
          triggered: [],
          invalidate_condition: "",
          executed: false,
          executed_price: null,
          realized_spread_pct: null,
          best_price: null,
          worst_price: null,
          optimal_spread_pct: null,
          outcome: null,
          error_attribution: null,
        },
        {
          decision_id: "MD-20260911-600519-10001000",
          symbol: "600519",
          trade_date: "20260911",
          trigger_ts: "2026-09-11T02:00:00+00:00",
          signal_price: 10.1,
          bias: "高抛偏向",
          score: 0.55,
          confidence: "medium",
          triggered: [],
          invalidate_condition: "",
          executed: false,
          executed_price: null,
          realized_spread_pct: null,
          best_price: null,
          worst_price: null,
          optimal_spread_pct: null,
          outcome: "expired",
          error_attribution: null,
        },
      ]),
    );
    render(<MinuteDecisionPanel symbol="600519" />);
    await waitFor(() => expect(screen.getByText("待结算")).toBeTruthy());
    expect(screen.getByText("数据不足·未判定")).toBeTruthy();
  });

  it("错误归因（leave-one-out）透传显示", async () => {
    mockedSignals.mockResolvedValue(signals([SIGNAL]));
    mockedDecisions.mockResolvedValue(
      decisions([
        {
          decision_id: "MD-20260911-600519-09350935",
          symbol: "600519",
          trade_date: "20260911",
          trigger_ts: "2026-09-11T01:35:00+00:00",
          signal_price: 10.02,
          bias: "低吸偏向",
          score: -0.61,
          confidence: "medium",
          triggered: [],
          invalidate_condition: "",
          executed: true,
          executed_price: 10.0,
          realized_spread_pct: -0.2,
          best_price: 10.1,
          worst_price: 9.9,
          optimal_spread_pct: 0.8,
          outcome: "wrong",
          error_attribution: {
            primary_cause: "avg_dev",
            primary_name: "均价线偏离",
            score_without: -0.278,
            pivotal: true,
            counter_evidence: "剔除「均价线偏离」后 score=-0.28，信号跌出信号区",
          },
        },
      ]),
    );
    render(<MinuteDecisionPanel symbol="600519" />);
    await waitFor(() => expect(screen.getByText("方向错误")).toBeTruthy());
    const t = document.body.textContent ?? "";
    expect(t).toContain("主因「均价线偏离」");
    expect(t).toContain("跌出信号区");
    expect(t).toContain("已执行@10.00");
  });

  it("信号源失败但决策库成功 → 仍显示决策记录（独立容错，不整块空白）", async () => {
    mockedSignals.mockRejectedValue(new Error("分时数据源失败"));
    mockedDecisions.mockResolvedValue(decisions([]));
    render(<MinuteDecisionPanel symbol="600519" />);
    await waitFor(() => expect(screen.getByText("分时数据源失败")).toBeTruthy());
    expect(screen.getByText(/暂无记录/)).toBeTruthy();
  });
});
