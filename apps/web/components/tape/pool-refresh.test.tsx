import {act, cleanup, render, screen} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {getLimitUpPoolSnapshot, getLimitDownPoolSnapshot} from "@/lib/api";
import {LimitUpTab} from "./limit-up-tab";
import {LimitDownTab} from "./limit-down-tab";

const scope = vi.hoisted(() => ({params: new URLSearchParams()}));
vi.mock("@/components/inspection/surface-scope", () => ({useSurfaceScope: () => ({searchParams: scope.params, replaceSearch: vi.fn()})}));
vi.mock("@/lib/api", () => ({getLimitUpPoolSnapshot: vi.fn(), getLimitDownPoolSnapshot: vi.fn()}));
vi.mock("@/lib/market-hours", () => ({isTradingSession: () => true}));
vi.mock("@/components/stock-link", () => ({StockLink: ({children}: {children: React.ReactNode}) => <span>{children}</span>, useStockRowNav: () => () => () => {}}));
vi.mock("@/components/detail/limit-reason", () => ({LimitReason: () => null}));

const record = {symbol: "600127", name: "测试观察", trade_date: "2026-10-13", source: "ths", quality: "high" as const, quality_reasons: [], received_at: "2026-10-13T10:00:00+08:00"};
const result = {trade_date: "2026-10-13", pool: [record]};
beforeEach(() => {
  scope.params = new URLSearchParams();
  vi.useFakeTimers();
  vi.mocked(getLimitUpPoolSnapshot).mockReset().mockResolvedValue(result);
  vi.mocked(getLimitDownPoolSnapshot).mockReset().mockResolvedValue(result);
});
afterEach(() => {cleanup(); vi.useRealTimers();});
const flush = async (ms = 0) => act(async () => {await vi.advanceTimersByTimeAsync(ms);});

describe.each([
  ["涨停", LimitUpTab, getLimitUpPoolSnapshot],
  ["跌停", LimitDownTab, getLimitDownPoolSnapshot],
] as const)("%s池读取身份", (_name, Component, getter) => {
  it("does not repeatedly fetch an explicit historical date", async () => {
    scope.params = new URLSearchParams({date: "2026-10-13"});
    render(<Component />);
    await flush();
    await flush(180_000);
    expect(getter).toHaveBeenCalledTimes(1);
    expect(screen.getByText("测试观察")).toBeTruthy();
  });
  it("does not claim the current pool is empty after a failed refresh of a previous empty result", async () => {
    vi.mocked(getter).mockResolvedValueOnce({...result, pool: []}).mockRejectedValueOnce(new Error("source failed"));
    render(<Component />);
    await flush();
    await flush(60_000);
    expect(screen.getByRole("heading", {name: "数量待核对"})).toBeTruthy();
    expect(screen.queryByText(/暂无涨停|暂无跌停/)).toBeNull();
  });
  it("keeps an earlier observation during a latest-pool failure, marks current membership unknown, and recovers", async () => {
    vi.mocked(getter).mockResolvedValueOnce(result).mockRejectedValueOnce(new Error("source failed"))
      .mockResolvedValue({...result, pool: [{...record, name: "恢复观察"}]});
    render(<Component />);
    await flush();
    await flush(60_000);
    expect(screen.getByText("测试观察")).toBeTruthy();
    expect(screen.getByText(/当前在池状态待核对/)).toBeTruthy();
    expect(screen.getByRole("heading", {name: "数量待核对"})).toBeTruthy();
    await flush(60_000);
    expect(screen.getByText("恢复观察")).toBeTruthy();
    expect(screen.queryByText(/当前在池状态待核对/)).toBeNull();
    expect(getter).toHaveBeenCalledTimes(3);
  });
});
