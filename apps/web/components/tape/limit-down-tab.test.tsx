import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { LimitDownTab } from "@/components/tape/limit-down-tab";
import { getLimitDownPool } from "@/lib/api";
import type { LimitDownRecord } from "@/types/market";

// 跌停池 tab（2026-09-04 市场页跌停入口联动新增）：
// 核心是空态与加载态区分（同 limit-up-tab 教训）+ 连续跌停 ≥2 天高风险强调。

afterEach(() => cleanup());

vi.mock("@/lib/api", () => ({ getLimitDownPool: vi.fn() }));

// 组件用 useSearchParams 读 ?date= 初始日期；裸 jsdom 无 Next 路由，mock 成空参数
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));

const rec = (over: Partial<LimitDownRecord>): LimitDownRecord => ({
  symbol: "002909",
  name: "集泰股份",
  trade_date: "2026-09-04",
  price: 7.21,
  change_pct: -9.99,
  consecutive_days: 1,
  open_count: 11,
  seal_amount: 105_587_563,
  turnover_rate: 37.84,
  source: "eastmoney",
  quality: "high",
  quality_reasons: [],
  received_at: "t0",
  ...over,
});

beforeEach(() => {
  vi.mocked(getLimitDownPool).mockReset();
});

async function mount() {
  render(<LimitDownTab />);
  await act(async () => {}); // flush useEffect 里的 load()
}

describe("LimitDownTab", () => {
  it("有数据：渲染行与连续跌停天数强调（≥2 天标高风险）", async () => {
    vi.mocked(getLimitDownPool).mockResolvedValue([
      rec({ symbol: "003032", name: "传智教育", consecutive_days: 2, open_count: 1 }),
      rec({ consecutive_days: 1 }),
    ]);
    await mount();
    expect(screen.getByText("跌停池 · 2026-09-04")).toBeTruthy();
    expect(screen.getByText("传智教育")).toBeTruthy();
    expect(screen.getByText("2 天")).toBeTruthy(); // ≥2 天强调
    expect(screen.getByText("共 2 只（按连续跌停天数排序）")).toBeTruthy();
  });

  it("空池是常态：显示『当日暂无跌停』而非错误", async () => {
    vi.mocked(getLimitDownPool).mockResolvedValue([]);
    await mount();
    expect(screen.getByText(/当日暂无跌停/)).toBeTruthy();
  });

  it("数据源失败：显式琥珀错误（不静默退化成空池）", async () => {
    vi.mocked(getLimitDownPool).mockRejectedValue(new Error("all providers failed"));
    await mount();
    expect(screen.getByText(/跌停池加载失败/)).toBeTruthy();
    expect(screen.getByText(/all providers failed/)).toBeTruthy();
  });
});
