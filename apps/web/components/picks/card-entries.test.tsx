import {act, cleanup, render, screen, waitFor} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {CardEntryRow} from "./card-entries";
import {getEventsForSymbol, type EventSummary} from "@/lib/api";

vi.mock("@/lib/api", () => ({getEventsForSymbol: vi.fn()}));
vi.mock("@/components/detail/detail-modal", () => ({useDetailModal: () => ({open: vi.fn()})}));
vi.mock("@/components/inspection/inspection-context", () => ({useInspection: () => ({open: vi.fn()})}));
vi.mock("@/lib/market-hours", () => ({isTradingSession: () => true}));
afterEach(() => {cleanup(); vi.restoreAllMocks();});

it("shares concurrent reads and expires an empty result so later news becomes reachable", async () => {
  let clock = 100;
  vi.spyOn(Date, "now").mockImplementation(() => clock);
  let resolve!: (value: Awaited<ReturnType<typeof getEventsForSymbol>>) => void;
  vi.mocked(getEventsForSymbol).mockReturnValueOnce(new Promise(done => {resolve = done;}));
  const pages = render(<><CardEntryRow symbol="600991" /><CardEntryRow symbol="600991" /></>);
  await waitFor(() => expect(getEventsForSymbol).toHaveBeenCalledTimes(1));
  await act(async () => resolve({symbol: "600991", themes: [], count: 0, items: []}));
  expect(screen.queryByRole("button", {name: "消息"})).toBeNull();
  pages.unmount();
  vi.mocked(getEventsForSymbol).mockResolvedValue({symbol: "600991", themes: [], count: 1, items: [{} as EventSummary]});
  clock += 30_001;
  render(<CardEntryRow symbol="600991" />);
  await screen.findByRole("button", {name: "消息"});
  expect(getEventsForSymbol).toHaveBeenCalledTimes(2);
});

it("does not cache a read failure as an empty result", async () => {
  vi.mocked(getEventsForSymbol).mockReset().mockRejectedValueOnce(new Error("offline"));
  const page = render(<CardEntryRow symbol="600992" />);
  await waitFor(() => expect(getEventsForSymbol).toHaveBeenCalledTimes(1));
  await act(async () => {});
  page.unmount();
  vi.mocked(getEventsForSymbol).mockResolvedValue({symbol: "600992", themes: [], count: 1, items: [{} as EventSummary]});
  render(<CardEntryRow symbol="600992" />);
  await screen.findByRole("button", {name: "消息"});
  expect(getEventsForSymbol).toHaveBeenCalledTimes(2);
});
