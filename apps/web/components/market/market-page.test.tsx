import { useEffect, useRef } from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import MarketPage from "@/app/market/page";
import * as api from "@/lib/api";

const state = vi.hoisted(() => ({ polls: new Map<number, () => Promise<unknown>>() }));
vi.mock("@/hooks/use-polling-fetch", () => ({ usePollingFetch: (fn: () => Promise<unknown>, ms: number) => {
  const latest = useRef(fn);
  useEffect(() => { latest.current = fn; });
  useEffect(() => {
    state.polls.set(ms, () => latest.current());
    void latest.current();
    return () => { state.polls.delete(ms); };
  }, [ms]);
} }));
vi.mock("@/components/ui/workspace-deck", () => ({ MarketLensPicker: () => null }));
vi.mock("@/components/detail/symbol-detail-context", () => ({ useSymbolDetail: () => ({open: vi.fn()}) }));
vi.mock("@/components/event-panel", () => ({ EventPanel: () => null }));
vi.mock("@/components/market/heatmap-tab", () => ({ HeatmapTab: () => null }));
vi.mock("@/components/market/events-tab", () => ({ EventsTab: () => null }));
vi.mock("@/components/market/fund-tab", () => ({ FundTab: () => null }));
vi.mock("@/lib/api", () => ({
  getMarketOverview: vi.fn(), getLimitUpPool: vi.fn(), getBreadth: vi.fn(),
  getSentiment: vi.fn(), getSentimentHistory: vi.fn(),
}));

const sentiment: api.Sentiment = {
  phase: "修复", temperature: 55, confidence: "中", reasons: ["涨跌宽度修复"],
  misjudge_caveats: ["量能不足"], switch_conditions: "重新转弱时失效", indicators: [],
  ladder: {}, judged_at: "2026-10-09T10:30:00+08:00",
};

beforeEach(() => {
  state.polls.clear();
  vi.mocked(api.getMarketOverview).mockResolvedValue({indices: [], total_amount: null, total_amount_freshness: null});
  vi.mocked(api.getLimitUpPool).mockResolvedValue([]);
  vi.mocked(api.getBreadth).mockResolvedValue({up: 3000, down: 1000} as api.Breadth);
  vi.mocked(api.getSentiment).mockResolvedValue(sentiment);
  vi.mocked(api.getSentimentHistory).mockResolvedValue({items: []} as unknown as api.SentimentHistoryPayload);
  vi.stubGlobal("matchMedia", () => ({matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn()}));
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

it("closes failed sentiment evidence and requires a fresh user click after recovery", async () => {
  render(<MarketPage />);
  fireEvent.click(await screen.findByRole("button", {name: "依据与失效条件"}));
  expect(within(screen.getByRole("dialog", {name: "市场情绪依据与失效条件"})).getByText(/涨跌宽度修复/)).toBeTruthy();

  vi.mocked(api.getSentiment).mockRejectedValueOnce(new Error("offline"));
  await act(async () => { await state.polls.get(30_000)!(); });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByText(/宽度或情绪来源未就绪/)).toBeTruthy();

  await act(async () => { await state.polls.get(30_000)!(); });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", {name: "依据与失效条件"}).getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(screen.getByRole("button", {name: "依据与失效条件"}));
  expect(within(screen.getByRole("dialog", {name: "市场情绪依据与失效条件"})).getByText(/涨跌宽度修复/)).toBeTruthy();
  expect(api.getSentiment).toHaveBeenCalledTimes(3);
});
