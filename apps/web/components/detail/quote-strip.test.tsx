import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
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

afterEach(cleanup);

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
});
