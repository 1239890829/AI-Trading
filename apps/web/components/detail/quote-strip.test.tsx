import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QuoteStrip } from "./quote-strip";
import type { Quote } from "@/types/market";

function quote(limitUp: number | null): Quote {
  return {
    symbol: "sh000001",
    market: "SH",
    name: "上证指数",
    price: 3952.13,
    prev_close: 3949.91,
    change: 2.22,
    change_pct: 0.06,
    limit_up_price: limitUp,
    source: "tencent",
    quality: "high",
    quality_reasons: [],
    received_at: "2026-09-22T08:14:01Z",
    data_timestamp: "2026-09-22T08:14:01Z",
  };
}

afterEach(() => {cleanup(); vi.unstubAllGlobals(); vi.useRealTimers();});

describe("QuoteStrip 限价展示", () => {
  it.each([-1, 0, null])("无效涨停价 %s 显示 --，不把占位值冒充真实价格", (limitUp) => {
    render(<QuoteStrip quote={quote(limitUp)} inWatchlist={false} onAdd={() => {}} hideWatchlist />);
    expect(screen.getByText("涨停").parentElement?.textContent).toContain("--");
    expect(screen.queryByText("-1.00")).toBeNull();
  });

  it("有效涨停价正常显示", () => {
    render(<QuoteStrip quote={quote(4344.9)} inWatchlist={false} onAdd={() => {}} hideWatchlist />);
    expect(screen.getByText("涨停").parentElement?.textContent).toContain("4,344.90");
  });

  it("手机指标收起立即不可聚焦，退出壳保留，快速重开继续显示最新行情", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("matchMedia", (query: string) => ({matches: query.includes("max-width"), addEventListener: () => {}, removeEventListener: () => {}}));
    const view = render(<QuoteStrip quote={quote(4344.9)} inWatchlist={false} onAdd={() => {}} hideWatchlist />);
    expect(screen.queryByRole("region", {name:"行情详细指标"})).toBeNull();
    const toggle = screen.getByRole("button", {name:"行情指标"});
    fireEvent.click(toggle);
    const region = screen.getByRole("region", {name:"行情详细指标"});
    fireEvent.click(toggle);
    expect(screen.queryByRole("region", {name:"行情详细指标"})).toBeNull();
    expect(region.closest("[inert]")).toBeTruthy();
    view.rerender(<QuoteStrip quote={{...quote(4344.9), high:4000}} inWatchlist={false} onAdd={() => {}} hideWatchlist />);
    fireEvent.click(toggle);
    await act(async () => vi.advanceTimersByTime(200));
    expect(screen.getByRole("region", {name:"行情详细指标"})).toHaveProperty("textContent", expect.stringContaining("4,000.00"));
  });
});
