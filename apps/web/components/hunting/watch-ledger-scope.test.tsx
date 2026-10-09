import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WatchLedgerPanel } from "./watch-ledger-panel";
import { getWatchLedger, placePaperOrder, type WatchLedgerPayload } from "@/lib/api";

vi.mock("@/lib/api", () => ({getWatchLedger: vi.fn(), placePaperOrder: vi.fn()}));
vi.mock("./opportunity-evidence-panel", () => ({OpportunityEvidencePanel: ({date}: {date?: string}) => <div>证据范围 {date ?? "today"}</div>}));
vi.mock("./leader-research-panel", () => ({LeaderResearchPanel: ({date}: {date?: string}) => <div>研究范围 {date ?? "today"}</div>}));
vi.mock("@/lib/market-hours", () => ({bjToday: () => "2026-10-09", isTradingSession: () => true}));

function ledger(date: string, symbol = "600127"): WatchLedgerPayload {
  return {
    trade_date: date,
    rows: [{id: 1, trade_date: date, symbol, name: `记录 ${symbol}`, layer: "today_strongest",
      source_theme: "粮食安全", reason: {text: "首见归档依据"}, is_leader: false, boards: 0,
      entry_price: 7.15, entry_time: "10:01:02", status: "tracking", close_price: null,
      pnl_pct: null, verdict: null, verdict_reason: null, merged_into_picks: false}],
    stats: {trade_date: date, total: 1, settled: 0, tracking: 1, success: 0, fail: 0,
      flat: 0, win_rate: null, avg_pnl_pct: null},
    history: [],
  };
}

beforeEach(() => {
  vi.mocked(getWatchLedger).mockReset().mockResolvedValue(ledger("2026-10-09"));
  vi.mocked(placePaperOrder).mockReset().mockResolvedValue({id: 3, status: "pending"});
});
afterEach(cleanup);

it("normalizes a compact original date and passes it to evidence and research readers", async () => {
  vi.mocked(getWatchLedger).mockResolvedValue(ledger("2026-10-08"));
  render(<WatchLedgerPanel date="20261008" readOnly />);
  await waitFor(() => expect(getWatchLedger).toHaveBeenCalledWith("2026-10-08", 5));
  await screen.findByText("记录 600127");
  expect(screen.getByText("研究范围 2026-10-08")).toBeTruthy();
  const details = screen.getByText("查看同版机会依据").closest("details")!;
  await act(async () => {
    details.open = true;
    details.dispatchEvent(new Event("toggle", {bubbles: true}));
  });
  expect(screen.getByText("证据范围 2026-10-08")).toBeTruthy();
  expect(screen.queryByText(/范围 today/)).toBeNull();
  expect(placePaperOrder).not.toHaveBeenCalled();
});

it("binds results to the requested date and discards an older in-flight reply", async () => {
  let resolveOld!: (value: WatchLedgerPayload) => void;
  vi.mocked(getWatchLedger).mockReturnValueOnce(new Promise(resolve => {resolveOld = resolve;}));
  const page = render(<WatchLedgerPanel date="2026-10-08" readOnly />);
  await waitFor(() => expect(getWatchLedger).toHaveBeenCalledWith("2026-10-08", 5));
  vi.mocked(getWatchLedger).mockResolvedValue(ledger("2026-10-09", "002636"));
  page.rerender(<WatchLedgerPanel date="20261009" readOnly />);
  await screen.findByText("记录 002636");
  expect(getWatchLedger).toHaveBeenLastCalledWith("2026-10-09", 5);
  await act(async () => resolveOld(ledger("2026-10-08", "600127")));
  expect(screen.queryByText("记录 600127")).toBeNull();
  expect(screen.getByText("记录 002636")).toBeTruthy();
  expect(screen.getByText("研究范围 2026-10-09")).toBeTruthy();
});

it("keeps an undated inspection read-only even when today's row has an entry price", async () => {
  render(<WatchLedgerPanel readOnly />);
  await screen.findByText("记录 600127");
  expect(getWatchLedger).toHaveBeenCalledWith(undefined, 5);
  expect(screen.queryByRole("button", {name: "模拟建仓"})).toBeNull();
  expect(screen.getByText(/此旁览只读核对，不提交模拟委托/)).toBeTruthy();
  expect(placePaperOrder).not.toHaveBeenCalled();
});

it("retains the default current-day explicit simulation action and submits only after a click", async () => {
  render(<WatchLedgerPanel />);
  const button = await screen.findByRole("button", {name: "模拟建仓"});
  expect(placePaperOrder).not.toHaveBeenCalled();
  fireEvent.click(button);
  await waitFor(() => expect(placePaperOrder).toHaveBeenCalledWith("600127", "buy", 7.15, 100));
  expect((await screen.findByRole("status")).textContent).toContain("模拟买单已提交");
});
