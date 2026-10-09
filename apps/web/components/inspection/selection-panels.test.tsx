import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SelectionReviewPanel, SelectionPreviewPanel } from "./selection-panels";
import * as api from "@/lib/api";

vi.mock("@/lib/api", () => ({getPickReviews: vi.fn(), getTodayPicks: vi.fn(), getIntradayTop: vi.fn(), getMorningBriefToday: vi.fn(), getWatcherState: vi.fn(), getIntradayOpportunities: vi.fn(), ApiError: class extends Error {}}));
vi.mock("@/components/hunting/pick-sections", () => ({DailyReviews: ({reviews}: {reviews: api.PickReviewRow[]}) => <div>{reviews.map(row => <span key={row.symbol}>{row.symbol}:{row.selection_version}</span>)}</div>}));
vi.mock("@/components/picks/pick-card", () => ({fromDailyPick: (it: object) => ({...it, origin: "picks"}), fromIntradayStock: (it: object) => ({...it, origin: "intraday"}), PickCard: ({item}: {item: {symbol: string}}) => <div>候选 {item.symbol}</div>}));
vi.mock("@/components/picks/pick-detail-modal", () => ({PickDetailModal: () => null}));
vi.mock("@/components/hunting/opportunity-evidence-panel", () => ({OpportunityEvidencePanel: ({date, onDateChange}: {date?: string; onDateChange?: (date: string) => void}) => <div>依据:{date}<button onClick={() => onDateChange?.("2026-10-02")}>切依据日期</button></div>}));
vi.mock("@/components/hunting/leader-research-panel", () => ({LeaderResearchPanel: () => null}));
vi.mock("@/components/hunting/watch-ledger-panel", () => ({WatchLedgerPanel: ({date}: {date?: string}) => <div>跟踪:{date}</div>}));
vi.mock("@/components/research/review-tab", () => ({ReviewTab: () => null}));
vi.mock("@/components/hunting/intraday-sections", () => ({AlertItem: () => <div>原提醒</div>, ClimateBlock: () => null, DailyPlanBlock: () => null, DirectionCard: () => null, EnvStrip: () => null, MacroCalendar: () => null, MarketOverviewStrip: () => null, OpportunitySection: () => null, OvernightBiasBlock: () => null, WatcherPanel: () => null}));
vi.mock("@/components/hunting/post-market-enhance", () => ({PostMarketEnhance: () => <div>接力与潜伏</div>}));
vi.mock("@/lib/market-hours", () => ({bjToday: () => "2026-10-09", isTradingSession: () => true}));
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it("holds the original date/version, rejects foreign rows, and discards late replies", async () => {
  let resolveOld!: (value: api.PickReviewRow[]) => void;
  vi.mocked(api.getPickReviews).mockReturnValueOnce(new Promise(resolve => {resolveOld = resolve;}));
  const page = render(<SelectionReviewPanel request={{kind: "selection-review", date: "20261009", version: "old"}} />);
  await waitFor(() => expect(api.getPickReviews).toHaveBeenCalledWith("20261009", "old"));
  vi.mocked(api.getPickReviews).mockResolvedValue([{date: "2026-10-09", symbol: "foreign", selection_version: "old"}] as api.PickReviewRow[]);
  page.rerender(<SelectionReviewPanel request={{kind: "selection-review", date: "2026-10-09", version: "new"}} />);
  await screen.findByText(/响应含其他日期或版本/);
  await act(async () => resolveOld([{date: "2026-10-09", symbol: "late", selection_version: "old"}] as api.PickReviewRow[]));
  expect(screen.queryByText("late:old")).toBeNull();
  expect(screen.queryByText("foreign:old")).toBeNull();
  expect(screen.getByText(/未替换成当前版本/)).toBeTruthy();
});

it("shows intraday candidates without waiting for a slow daily read and deduplicates later overlap", async () => {
  let resolveDaily!: (value: api.DailyPicksPayload) => void;
  vi.mocked(api.getTodayPicks).mockReturnValue(new Promise(resolve => {resolveDaily = resolve;}));
  vi.mocked(api.getIntradayTop).mockResolvedValue({trade_date: "2026-10-09", items: [{symbol: "600127"}]} as api.IntradayTopPayload);
  render(<SelectionPreviewPanel request={{kind: "selection-preview"}} />);
  await screen.findByText("候选 600127");
  await act(async () => resolveDaily({date: "2026-10-09", items: [{symbol: "600127"}]} as api.DailyPicksPayload));
  expect(screen.getAllByText("候选 600127")).toHaveLength(1);
  expect(screen.getByRole("button", {name: "每日原生成依据"})).toBeTruthy();
});

it("passes an explicit past date into evidence instead of reading today's scope", () => {
  render(<SelectionPreviewPanel request={{kind: "selection-preview", view: "evidence", date: "2026-10-08"}} />);
  expect(screen.getByText("依据:2026-10-08")).toBeTruthy();
  expect(api.getTodayPicks).not.toHaveBeenCalled();
});

it("opens the requested reminders and daily scope without unrelated candidate reads", async () => {
  vi.mocked(api.getMorningBriefToday).mockResolvedValue({brief_date: "2026-10-09", alerts: [{key: "one"}]} as api.MorningBrief);
  const page = render(<SelectionPreviewPanel request={{kind: "selection-preview", section: "reminders"}} />);
  await screen.findByText("原提醒");
  expect(api.getTodayPicks).not.toHaveBeenCalled();
  expect(api.getIntradayTop).not.toHaveBeenCalled();
  page.unmount();
  vi.mocked(api.getTodayPicks).mockResolvedValue({date: "2026-10-09", items: [{symbol: "600127"}]} as api.DailyPicksPayload);
  render(<SelectionPreviewPanel request={{kind: "selection-preview", section: "daily"}} />);
  await screen.findByText("候选 600127");
  expect(api.getIntradayTop).not.toHaveBeenCalled();
});

it("does not replace a past current-only section with today's candidates", () => {
  render(<SelectionPreviewPanel request={{kind: "selection-preview", section: "brief", date: "2026-10-08"}} />);
  expect(screen.getByRole("status").textContent).toContain("未以今天的数据代替");
  expect(api.getMorningBriefToday).not.toHaveBeenCalled();
  expect(api.getTodayPicks).not.toHaveBeenCalled();
});

it("allows a historical evidence inspection to change its local date", () => {
  render(<SelectionPreviewPanel request={{kind: "selection-preview", view: "evidence", date: "20261008"}} />);
  expect(screen.getByText("依据:2026-10-08")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "切依据日期"}));
  expect(screen.getByText("依据:2026-10-02")).toBeTruthy();
});

it("keeps the intraday candidate section separate and retains the sealed reference list", async () => {
  vi.mocked(api.getIntradayTop).mockResolvedValue({trade_date: "2026-10-09", items: [{symbol: "600127"}], reference_items: [{symbol: "002636"}]} as api.IntradayTopPayload);
  render(<SelectionPreviewPanel request={{kind: "selection-preview", section: "candidates"}} />);
  await screen.findByText("候选 600127");
  expect(api.getTodayPicks).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText(/已封板 \/ 仅参考/));
  expect(screen.getByText("候选 002636")).toBeTruthy();
});
