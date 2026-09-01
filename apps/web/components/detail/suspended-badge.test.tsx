import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import { SuspendedBadge, SuspendedNotice, isSuspended } from "./suspended-badge";
import type { TradingStatusInfo } from "@/types/market";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 theme-chips.test.tsx）
afterEach(cleanup);

const SUSPENDED: TradingStatusInfo = {
  status: "suspended",
  suspended_days: 5,
  suspended_since: "2026-08-26",
  last_bar_date: "2026-08-25",
  anchor_date: "2026-09-02",
  reason: "自 2026-08-26 起无成交，截至 2026-09-02 已停牌 5 个交易日",
};
const TRADING: TradingStatusInfo = { status: "trading", reason: "最近交易日 2026-09-02 有成交" };
const UNKNOWN: TradingStatusInfo = { status: "unknown", reason: "无日K数据（数据源不可用，或该标的尚未上市/已退市）" };

describe("isSuspended", () => {
  it("只有 suspended 为真——trading / unknown / null 都不得当停牌处理", () => {
    expect(isSuspended(SUSPENDED)).toBe(true);
    expect(isSuspended(TRADING)).toBe(false);
    // 关键：unknown 绝不能被当成停牌，否则"数据源挂了"会显示成"停牌"
    expect(isSuspended(UNKNOWN)).toBe(false);
    expect(isSuspended(null)).toBe(false);
    expect(isSuspended(undefined)).toBe(false);
  });
});

describe("SuspendedBadge", () => {
  it("停牌显示天数，判据进 title", () => {
    render(<SuspendedBadge status={SUSPENDED} />);
    expect(screen.getByText("停牌 5 个交易日")).toBeTruthy();
    expect(screen.getByTitle(/停牌判定依据/)).toBeTruthy();
  });

  it("正常交易不占位（null 渲染）", () => {
    const { container } = render(<SuspendedBadge status={TRADING} />);
    expect(container.firstChild).toBeNull();
  });

  it("unknown 显式显示「交易状态未知」，不静默隐掉", () => {
    render(<SuspendedBadge status={UNKNOWN} />);
    expect(screen.getByText("交易状态未知")).toBeTruthy();
  });

  it("null（未判定）不渲染，但 unknown 渲染——两者不可混为一谈", () => {
    const { container } = render(<SuspendedBadge status={null} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("SuspendedNotice", () => {
  it("停牌时把判据原文展示出来，用户可自行判断可信度", () => {
    render(<SuspendedNotice status={SUSPENDED} />);
    expect(screen.getByText("该股当前停牌")).toBeTruthy();
    expect(screen.getByText(/已停牌 5 个交易日/)).toBeTruthy();
    expect(screen.getByText(/日K 缺失交易日数/)).toBeTruthy();
  });

  it("unknown 走「无法判定」文案，措辞区别于停牌", () => {
    render(<SuspendedNotice status={UNKNOWN} />);
    expect(screen.getByText("交易状态无法判定")).toBeTruthy();
    expect(screen.queryByText("该股当前停牌")).toBeNull();
  });

  it("正常交易不渲染", () => {
    const { container } = render(<SuspendedNotice status={TRADING} />);
    expect(container.firstChild).toBeNull();
  });
});
