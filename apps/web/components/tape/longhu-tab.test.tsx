import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { LonghuTab } from "@/components/tape/longhu-tab";
import { getLonghu } from "@/lib/api";
import type { LongHuRecord } from "@/types/market";

// 龙虎榜披露语义（2026-09-02 盘中实测驱动）：
// 当日榜由交易所收盘后披露、数据商约 17:00 同步——盘中查当天四源皆空，
// 后端 502"数据源失败"。披露前的当日查询必须呈现"尚未披露"而非加载失败。

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

vi.mock("@/lib/api", () => ({
  getLonghu: vi.fn(),
  // B3 题材迁徙：best-effort 增强，测试里默认拒绝（静默降级不渲染）
  getLonghuThemeTrail: vi.fn().mockRejectedValue(new Error("skipped")),
}));

const record = (trade_date: string): LongHuRecord => ({
  symbol: "600519",
  name: "贵州茅台",
  trade_date,
  range_days: 1,
  change_pct: 1.2,
  net_buy: 1_000_000,
  buy_amount: 3_000_000,
  sell_amount: 2_000_000,
  source: "ths",
  quality: "high",
  quality_reasons: [],
  received_at: "t0",
});

/** 把客户端时间钉在 2026-09-02（周二，交易日）的指定时刻。组件的 now/todayISO 都取自它。 */
function at(hour: number, minute: number) {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(2026, 8, 2, hour, minute));
}

beforeEach(() => {
  vi.mocked(getLonghu).mockReset();
});

async function mount() {
  render(<LonghuTab />);
  await act(async () => {}); // flush useEffect 里的 load()
}

describe("LonghuTab 披露语义（P1-C）", () => {
  it("盘中查当天四源空（后端 502）→ 显示'尚未披露'提示，不显示加载失败", async () => {
    at(14, 20); // 盘中
    vi.mocked(getLonghu).mockRejectedValue(
      new Error("龙虎榜数据源失败：all providers failed for get_longhu_records"),
    );
    await mount();
    expect(screen.getByText(/今日榜单尚未披露/)).toBeTruthy();
    expect(screen.queryByText(/加载失败/)).toBeNull();
    expect(getLonghu).toHaveBeenCalledWith(undefined);
  });

  it("查历史日期失败 → 仍显示加载失败（披露语义只适用于当天）", async () => {
    at(14, 20);
    vi.mocked(getLonghu).mockRejectedValue(new Error("boom"));
    render(<LonghuTab />);
    await act(async () => {});
    // 默认当天查询显示"尚未披露"→ 切到历史日期后，失败就回归普通红错
    fireEvent.change(screen.getByLabelText(/按日期查询/), {
      target: { value: "2026-09-01" },
    });
    await act(async () => {});
    expect(screen.getByText(/加载失败：boom/)).toBeTruthy();
    expect(screen.queryByText(/尚未披露/)).toBeNull();
  });

  it("17:00 后查当天仍失败 → 是真故障，显示加载失败", async () => {
    at(17, 5); // 披露时刻之后
    vi.mocked(getLonghu).mockRejectedValue(new Error("all providers failed"));
    await mount();
    expect(screen.getByText(/加载失败/)).toBeTruthy();
    expect(screen.queryByText(/尚未披露/)).toBeNull();
  });

  it("盘中源提前吐出当日快照 → 显示'未定稿快照'提示（防御分支仍在）", async () => {
    at(14, 20);
    vi.mocked(getLonghu).mockResolvedValue([record("2026-09-02")]);
    await mount();
    expect(screen.getByText(/盘中未定稿快照/)).toBeTruthy();
  });

  it("盘后拿到当日定稿 → 不显示未定稿提示", async () => {
    at(17, 5);
    vi.mocked(getLonghu).mockResolvedValue([record("2026-09-02")]);
    await mount();
    expect(screen.queryByText(/未定稿快照/)).toBeNull();
  });
});
