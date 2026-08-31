import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { StockEventsRow } from "./stock-events";
import type { EventSummary } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getEventsForSymbol: vi.fn() };
});

const mockedGet = vi.mocked((await import("@/lib/api")).getEventsForSymbol);

const event: EventSummary = {
  id: 7,
  title: "沃什鹰派发言致美股与黄金大跌",
  url: "https://example.com/news/7",
  source: "财联社",
  source_tier: 4,
  published_at: "2026-08-31 09:00:00",
  fact_kind: "fact",
  certainty: "done",
  category: "statement",
  half_life_hours: 48,
  source_symbol: null,
  is_active: true,
  directions: [
    { target_type: "theme", target: "黄金概念", direction: -1, strength: 2, chain: "", basis: "" },
  ],
};

describe("StockEventsRow", () => {
  it("渲染相关事件标题、方向与原文链接", async () => {
    mockedGet.mockResolvedValue({ symbol: "600519", themes: [], count: 1, items: [event] });

    render(<StockEventsRow symbol="600519" />);
    await screen.findByText(/沃什鹰派/);

    expect(screen.getByText("利空")).toBeTruthy();
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("https://example.com/news/7");
  });

  it("无相关事件时零占用", async () => {
    mockedGet.mockResolvedValue({ symbol: "600519", themes: [], count: 0, items: [] });
    const { container } = render(<StockEventsRow symbol="600519" />);
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("加载失败静默（不拖垮详情页）", async () => {
    mockedGet.mockRejectedValue(new Error("boom"));
    const { container } = render(<StockEventsRow symbol="600519" />);
    await new Promise((r) => setTimeout(r, 5));
    expect(container.textContent).toBe("");
  });
});
