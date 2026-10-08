import { useEffect, useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import HuntingPage from "@/app/hunting/page";
import * as api from "@/lib/api";

let search = new URLSearchParams();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }), useSearchParams: () => search }));
vi.mock("@/hooks/use-polling-fetch", () => ({ usePollingFetch: (fn: () => Promise<unknown>, _ms: number, _key: unknown, options?: {enabled?: boolean}) => {
  const latest = useRef(fn);
  useEffect(() => { latest.current = fn; });
  useEffect(() => { if (options?.enabled !== false) void latest.current(); }, [options?.enabled]);
} }));
vi.mock("@/hooks/use-exit-presence", () => ({ useExitPresence: () => ({ value: null, active: false }) }));
vi.mock("@/components/ui/modal-shell", () => ({ ModalShell: () => null }));
vi.mock("@/components/ui/candidate-collection", () => ({ CandidateCollection: ({children}: {children: React.ReactNode}) => <div>{children}</div> }));
vi.mock("@/components/ui/loading", () => ({ FadeIn: ({children}: {children: React.ReactNode}) => <div>{children}</div>, CardListSkeleton: () => <div>读取候选中</div>, PageSkeletonFallback: () => null, StatGridSkeleton: () => null, StatsSkeleton: () => null, TableSkeleton: () => null }));
vi.mock("@/components/picks/pick-card", () => ({ PickCard: ({item}: {item: {symbol: string}}) => <article>候选 {item.symbol}</article>, fromDailyPick: (item: unknown) => item, fromIntradayStock: (item: unknown) => item, StandAsideBanner: () => <div data-testid="risk-gate">原风险闸门</div> }));
vi.mock("@/components/hunting/intraday-sections", () => ({ AlertItem: () => null, DirectionCard: () => null, DailyPlanBlock: () => null, EnvStrip: () => null, MacroCalendar: () => null, MarketOverviewStrip: () => null, OpportunitySection: ({showLedger}: {showLedger: boolean}) => <div>题材原消费者：台账嵌入 {String(showLedger)}</div>, ClimateBlock: () => null, OvernightBiasBlock: () => null, ReviewOutcomeTable: () => null, StatsPanel: () => null, WatcherPanel: () => null }));
vi.mock("@/components/hunting/pick-sections", () => ({ DailyReviews: () => null, HistoryList: () => null, ReasonDistribution: () => null, RolePerformanceTable: () => null }));
vi.mock("@/components/hunting/stats-bar", () => ({ HuntingStatsBar: () => null }));
vi.mock("@/components/hunting/post-market-enhance", () => ({ PostMarketEnhance: ({initiallyOpen}: {initiallyOpen: boolean}) => <div>原接力潜伏：{String(initiallyOpen)}</div> }));
vi.mock("@/components/hunting/opportunity-evidence-panel", () => ({ OpportunityEvidencePanel: () => <div>真实依据组件</div> }));
vi.mock("@/components/hunting/watch-ledger-panel", () => ({ WatchLedgerPanel: () => <div>真实跟踪组件</div> }));
vi.mock("@/components/hunting/leader-research-panel", () => ({ LeaderResearchPanel: () => <div>真实研究组件</div> }));
vi.mock("@/lib/api", () => ({ ApiError: class extends Error { status = 500; }, getTodayPicks: vi.fn(), getPicksHistory: vi.fn(), getPickReviews: vi.fn(), getPicksMeta: vi.fn(), getSignalHealth: vi.fn(), getMorningBriefToday: vi.fn(), getWatcherState: vi.fn(), getIntradayReview: vi.fn(), getIntradayOpportunities: vi.fn(), getIntradayTop: vi.fn(), getPositionLabels: vi.fn() }));

beforeEach(() => {
  search = new URLSearchParams();
  vi.mocked(api.getTodayPicks).mockResolvedValue({ date: "2026-10-08", items: [{symbol: "600127"}, {symbol: "002636"}], meta: {gate: {}, min_pick_score: 53} } as unknown as api.DailyPicksPayload);
  vi.mocked(api.getPicksHistory).mockResolvedValue([]);
  vi.mocked(api.getPickReviews).mockResolvedValue([]);
  vi.mocked(api.getPicksMeta).mockResolvedValue({reason_distribution: {}, note: "test-fixture"});
  vi.mocked(api.getSignalHealth).mockResolvedValue(null as unknown as api.SignalHealthPayload);
  vi.mocked(api.getMorningBriefToday).mockResolvedValue(null as unknown as api.MorningBrief);
  vi.mocked(api.getWatcherState).mockResolvedValue(null as unknown as api.WatcherState);
  vi.mocked(api.getIntradayReview).mockResolvedValue(null as unknown as api.IntradayReviewStats);
  vi.mocked(api.getIntradayOpportunities).mockResolvedValue({ themes: [], caveats: [] } as unknown as api.IntradayOpportunities);
  vi.mocked(api.getIntradayTop).mockResolvedValue({items: [{symbol: "600127"}], reference_items: []} as unknown as api.IntradayTopPayload);
  vi.mocked(api.getPositionLabels).mockResolvedValue({});
  HTMLElement.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("matchMedia", () => ({matches: true}));
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

describe("真实选股消费者迁移", () => {
  it("retains persisted and dynamic lists, deduplicates symbols and places risk before candidates", async () => {
    render(<HuntingPage />);
    expect(await screen.findByText("候选 600127")).toBeTruthy();
    expect(screen.getAllByText("候选 600127")).toHaveLength(1);
    expect(screen.getByText("候选 002636")).toBeTruthy();
    expect(screen.getByRole("heading", {name: "每日精选"})).toBeTruthy();
    expect(screen.getByText(/2026-10-08 · 收盘定次日/)).toBeTruthy();
    expect(screen.getByText("题材原消费者：台账嵌入 false")).toBeTruthy();
    const risk = screen.getByTestId("risk-gate");
    expect(risk.compareDocumentPosition(screen.getByText("候选 600127")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it.each([["evidence", "真实依据组件"], ["tracking", "真实跟踪组件"], ["research", "真实研究组件"]])("does not mount discovery polling behind %s", async (view, name) => {
    search = new URLSearchParams({view});
    render(<HuntingPage />);
    expect(screen.getByText(name)).toBeTruthy();
    await waitFor(() => expect(api.getIntradayTop).not.toHaveBeenCalled());
    expect(api.getTodayPicks).not.toHaveBeenCalled();
    expect(api.getPositionLabels).not.toHaveBeenCalled();
  });

  it("distinguishes failed intraday reads from a valid empty candidate list", async () => {
    vi.mocked(api.getIntradayTop).mockRejectedValue(new Error("offline"));
    render(<HuntingPage />);
    expect(await screen.findByText(/盘中候选读取失败/)).toBeTruthy();
    expect(screen.queryByText(/当前没有可参与候选/)).toBeNull();
    expect(screen.getByText("候选 002636")).toBeTruthy();
  });

  it("opens the original relay consumer from its task deep link", async () => {
    search = new URLSearchParams({view: "discover", sec: "postmarket"});
    render(<HuntingPage />);
    expect(screen.getByText("原接力潜伏：true")).toBeTruthy();
    await screen.findByText("候选 600127");
  });
});
