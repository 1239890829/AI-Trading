import { useEffect, useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import HuntingPage from "@/app/hunting/page";
import * as api from "@/lib/api";
import {CardEntryRow} from "@/components/picks/card-entries";

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
vi.mock("@/components/picks/pick-card", () => ({ PickCard: ({item, readError, dailyEvidence, onShowDailyEvidence}: {item: {symbol: string}; readError?: boolean; dailyEvidence?: {item: api.DailyPickItem}; onShowDailyEvidence?: (item: api.DailyPickItem) => void}) => <article data-read-error={String(!!readError)}>候选 {item.symbol}<CardEntryRow symbol={item.symbol} />{dailyEvidence && <button onClick={() => onShowDailyEvidence?.(dailyEvidence.item)}>每日依据 {item.symbol}</button>}</article>, fromDailyPick: (item: unknown) => item, fromIntradayStock: (item: unknown) => item, StandAsideBanner: () => <div data-testid="risk-gate">原风险闸门</div> }));
vi.mock("@/components/picks/pick-detail-modal", () => ({PickDetailModal: ({target, readError}: {target: {item: api.DailyPickItem} | null; readError?: boolean}) => target ? <div data-testid="daily-detail" data-read-error={String(!!readError)}>原依据 {target.item.symbol} · 执行价 {target.item.execution?.executable_snapshot.price}</div> : null}));
vi.mock("@/components/detail/detail-modal", () => ({useDetailModal: () => ({open: vi.fn()})}));
vi.mock("@/lib/market-hours", () => ({isTradingSession: () => true}));
vi.mock("@/components/hunting/intraday-sections", () => ({ AlertItem: () => null, DirectionCard: () => null, DailyPlanBlock: () => null, EnvStrip: () => null, MacroCalendar: () => null, MarketOverviewStrip: () => null, OpportunitySection: ({showLedger}: {showLedger: boolean}) => <div>题材原消费者：台账嵌入 {String(showLedger)}</div>, ClimateBlock: () => null, OvernightBiasBlock: () => null, ReviewOutcomeTable: ({reviewed}: {reviewed: {direction: string}[]}) => <div>{reviewed.map(row => <p key={row.direction}>原对照 {row.direction}</p>)}</div>, StatsPanel: ({stats}: {stats: {sample_note: string}}) => <div>{stats.sample_note}</div>, WatcherPanel: () => null }));
vi.mock("@/components/hunting/pick-sections", () => ({ DailyReviews: ({reviews}: {reviews: {selection_version?: string | null}[]}) => <div>{reviews.map((row, i) => <span key={i}>复盘版本 {row.selection_version}</span>)}</div>, HistoryList: ({history}: {history: {date: string}[]}) => <div>{history.map(row => <p key={row.date}>原历史 {row.date}</p>)}</div>, ReasonDistribution: () => null, RolePerformanceTable: () => null }));
vi.mock("@/components/hunting/stats-bar", () => ({ HuntingStatsBar: () => null }));
vi.mock("@/components/hunting/post-market-enhance", () => ({ PostMarketEnhance: ({initiallyOpen}: {initiallyOpen: boolean}) => <div>原接力潜伏：{String(initiallyOpen)}</div> }));
vi.mock("@/components/hunting/opportunity-evidence-panel", () => ({ OpportunityEvidencePanel: () => <div>真实依据组件</div> }));
vi.mock("@/components/hunting/watch-ledger-panel", () => ({ WatchLedgerPanel: () => <div>真实跟踪组件</div> }));
vi.mock("@/components/hunting/leader-research-panel", () => ({ LeaderResearchPanel: () => <div>真实研究组件</div> }));
vi.mock("@/lib/api", () => ({ ApiError: class extends Error { status = 500; }, getTodayPicks: vi.fn(), getPicksHistory: vi.fn(), getPickReviews: vi.fn(), getPicksMeta: vi.fn(), getSignalHealth: vi.fn(), getMorningBriefToday: vi.fn(), getWatcherState: vi.fn(), getIntradayReview: vi.fn(), getIntradayOpportunities: vi.fn(), getIntradayTop: vi.fn(), getPositionLabels: vi.fn(), getEventsForSymbol: vi.fn() }));

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
  vi.mocked(api.getIntradayTop).mockResolvedValue({trade_date: "2026-10-08", items: [{symbol: "600127"}], reference_items: []} as unknown as api.IntradayTopPayload);
  vi.mocked(api.getPositionLabels).mockResolvedValue({});
  vi.mocked(api.getEventsForSymbol).mockImplementation(async symbol => ({symbol, themes: [], count: 0, items: []}));
  HTMLElement.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("matchMedia", () => ({matches: true}));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

describe("真实选股消费者迁移", () => {
  it("retains persisted and dynamic lists, deduplicates symbols and places risk before candidates", async () => {
    render(<HuntingPage />);
    expect(await screen.findByText("候选 600127")).toBeTruthy();
    expect(screen.getAllByText("候选 600127")).toHaveLength(1);
    expect(screen.getByText("候选 002636")).toBeTruthy();
    expect(screen.getByRole("heading", {name: "每日精选"})).toBeTruthy();
    expect(screen.getByText(/2026-10-08 · 当日持久候选/)).toBeTruthy();
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


it("starts intraday reads while the daily group is still waiting", async () => {
  let resolveDaily!: (value: api.DailyPicksPayload) => void;
  vi.mocked(api.getTodayPicks).mockReturnValue(new Promise(resolve => {resolveDaily = resolve;}));
  render(<HuntingPage />);
  await waitFor(() => expect(api.getIntradayTop).toHaveBeenCalledTimes(1));
  await act(async () => resolveDaily({date: "2026-10-09", items: []}));
});

it("labels retained theme opportunities when only that source fails", async () => {
  render(<HuntingPage />);
  await screen.findByText("题材原消费者：台账嵌入 false");
  vi.mocked(api.getIntradayOpportunities).mockRejectedValue(new Error("themes offline"));
  fireEvent(document, new Event("visibilitychange"));
  expect(await screen.findByText(/上次题材结果/)).toBeTruthy();
  expect(screen.getByText("题材原消费者：台账嵌入 false")).toBeTruthy();
});

it("renders candidates before slow auxiliary review and brief reads finish", async () => {
  vi.mocked(api.getPicksMeta).mockReturnValue(new Promise(() => {}));
  vi.mocked(api.getMorningBriefToday).mockReturnValue(new Promise(() => {}));
  render(<HuntingPage />);
  expect(await screen.findByText("候选 002636")).toBeTruthy();
  expect(screen.getByText("候选 600127")).toBeTruthy();
});

it("does not let a late daily read overwrite the results after leaving and returning", async () => {
  let resolveOld!: (value: api.DailyPicksPayload) => void;
  vi.mocked(api.getTodayPicks).mockReturnValueOnce(new Promise(resolve => {resolveOld = resolve;}));
  const page = render(<HuntingPage />);
  await waitFor(() => expect(api.getTodayPicks).toHaveBeenCalledTimes(1));
  search = new URLSearchParams({view: "research"}); page.rerender(<HuntingPage />);
  search = new URLSearchParams({view: "discover"}); page.rerender(<HuntingPage />);
  await screen.findByText("候选 002636");
  await act(async () => resolveOld({date: "2026-10-09", items: [{symbol: "OLD"}]} as api.DailyPicksPayload));
  expect(screen.queryByText("候选 OLD")).toBeNull();
  expect(screen.getByText("候选 002636")).toBeTruthy();
});

it("binds original review links to their date and version and discards an older version reply", async () => {
  search = new URLSearchParams({view: "review", date: "2026-10-09", version: "version-A"});
  let resolveOld!: (value: api.PickReviewRow[]) => void;
  vi.mocked(api.getPickReviews).mockReturnValueOnce(new Promise(resolve => {resolveOld = resolve;}));
  const page = render(<HuntingPage />);
  await waitFor(() => expect(api.getPickReviews).toHaveBeenCalledWith("2026-10-09", "version-A"));
  search = new URLSearchParams({view: "review", date: "2026-10-09", version: "version-B"});
  page.rerender(<HuntingPage />);
  await screen.findByText(/该日期或版本尚无复盘记录/);
  expect(api.getPickReviews).toHaveBeenLastCalledWith("2026-10-09", "version-B");
  await act(async () => resolveOld([{selection_version: "version-A"}] as api.PickReviewRow[]));
  expect(screen.queryByText("复盘版本 version-A")).toBeNull();
  expect(screen.getByText(/该日期或版本尚无复盘记录/)).toBeTruthy();
});

it("review reads only its six real sources on entry and refresh", async () => {
  vi.useFakeTimers();
  search = new URLSearchParams({view: "review", date: "2026-10-09", version: "v1"});
  vi.mocked(api.getTodayPicks).mockResolvedValue({date: "2026-10-08", items: [], stale: true, note: "原daily来源说明"});
  vi.mocked(api.getPicksHistory).mockResolvedValue([{date: "2026-10-07"}] as Awaited<ReturnType<typeof api.getPicksHistory>>);
  vi.mocked(api.getPickReviews).mockResolvedValue([{selection_version: "v1"}] as api.PickReviewRow[]);
  vi.mocked(api.getMorningBriefToday).mockResolvedValueOnce({brief_date: "2026-10-08", directions: [{direction: "农业", review: {}}], review: {reviewed_at: "2026-10-08T15:35:00+08:00"}} as api.MorningBrief).mockRejectedValue(new Error("brief-offline"));
  vi.mocked(api.getIntradayReview).mockResolvedValueOnce({sample_note: "原跟踪统计来源"} as api.IntradayReviewStats).mockRejectedValue(new Error("stats-offline"));
  render(<HuntingPage />);
  await act(async () => {});
  const unused = [api.getWatcherState, api.getSignalHealth, api.getIntradayOpportunities, api.getIntradayTop];
  for (const fn of unused) expect(fn).not.toHaveBeenCalled();
  const retained = [api.getTodayPicks, api.getPicksHistory, api.getPickReviews, api.getPicksMeta, api.getMorningBriefToday, api.getIntradayReview];
  for (const fn of retained) expect(fn).toHaveBeenCalledTimes(1);
  for (const text of ["原对照 农业", "原历史 2026-10-07", "复盘版本 v1", "test-fixture", "原跟踪统计来源", "原daily来源说明"]) expect(screen.getByText(text)).toBeTruthy();
  await act(async () => {await vi.advanceTimersByTimeAsync(60_000);});
  for (const fn of unused) expect(fn).not.toHaveBeenCalled();
  for (const fn of retained) expect(fn).toHaveBeenCalledTimes(2);
  expect(screen.getByText(/原记录复盘：2026-10-09 · 版本 v1/)).toBeTruthy();
  for (const text of ["原对照 农业", "原历史 2026-10-07", "复盘版本 v1", "test-fixture", "原跟踪统计来源", "原daily来源说明"]) expect(screen.getByText(text)).toBeTruthy();
  expect(screen.getByText(/保留上次读取的对照/)).toBeTruthy();
  expect(screen.getByText(/统计读取失败/)).toBeTruthy();
});

it("closed reference details keeps the full count but makes no per-stock presence reads", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-12T10:00:00+08:00"));
  vi.mocked(api.getIntradayTop).mockResolvedValue({trade_date: "2026-10-08", items: [{symbol: "600127"}], reference_items: [{symbol: "600987"}, {symbol: "600988"}]} as api.IntradayTopPayload);
  render(<HuntingPage />);
  await act(async () => {});
  const summary = screen.getByText(/涨停梯队 2 只/);
  const fold = summary.closest("details")!;
  const refCalls = () => vi.mocked(api.getEventsForSymbol).mock.calls.filter(([symbol]) => ["600987", "600988"].includes(symbol));
  expect(refCalls()).toHaveLength(0);
  expect(screen.queryByText("候选 600987")).toBeNull();
  fold.open = true; fireEvent(fold, new Event("toggle")); await act(async () => {});
  expect(screen.getByText("候选 600987")).toBeTruthy();
  expect(screen.getByText("候选 600988")).toBeTruthy();
  expect(refCalls()).toHaveLength(0);
  await act(async () => {await vi.advanceTimersByTimeAsync(30_000);});
  expect(refCalls()).toHaveLength(0);
  fold.open = false; fireEvent(fold, new Event("toggle")); await act(async () => {});
  await act(async () => {await vi.advanceTimersByTimeAsync(60_000);});
  expect(refCalls()).toHaveLength(0);
  expect(screen.getByText(/涨停梯队 2 只/)).toBeTruthy();
  expect(screen.getByText("候选 002636")).toBeTruthy();
  expect(screen.getByText("候选 600127")).toBeTruthy();
});

it("same-scope refresh failures preserve all candidates but remove current gate eligibility", async () => {
  render(<HuntingPage />);
  await screen.findByText("候选 002636");
  vi.mocked(api.getTodayPicks).mockRejectedValue(new Error("daily-offline"));
  vi.mocked(api.getIntradayTop).mockRejectedValue(new Error("top-offline"));
  fireEvent.click(screen.getByRole("button", {name: "刷新数据"}));
  await screen.findByText(/保留上次读取的盘中候选/);
  expect(screen.getByText("候选 600127").closest("article")?.dataset.readError).toBe("true");
  expect(screen.getByText("候选 002636").closest("article")?.dataset.readError).toBe("true");
  expect(screen.getAllByText(/候选 (600127|002636)/)).toHaveLength(2);
  expect(screen.queryByTestId("risk-gate")).toBeNull();
  expect(screen.queryByText(/当前没有可参与候选/)).toBeNull();
  vi.mocked(api.getTodayPicks).mockResolvedValue({date: "2026-10-08", items: [{symbol: "600128"}, {symbol: "002826"}], meta: {gate: {}}} as api.DailyPicksPayload);
  vi.mocked(api.getIntradayTop).mockResolvedValue({trade_date: "2026-10-08", items: [{
    symbol: "600128", name: null, role: null, boards: null, change_pct: null, theme: null,
    stage: null, strength_tier: null, distinctiveness: null, certainty: null, reason: null,
    tier: 0, pick_basis: "fixture",
  }], reference_items: [], total_candidates: 1, criteria: "fixture", hot_available: false, caveats: []});
  fireEvent.click(screen.getByRole("button", {name: "刷新数据"}));
  await screen.findByText("候选 002826");
  expect(screen.getByText("候选 600128").closest("article")?.dataset.readError).toBe("false");
  expect(screen.getByText("候选 002826").closest("article")?.dataset.readError).toBe("false");
  expect(screen.queryByText("候选 600127")).toBeNull();
  expect(screen.queryByText("候选 002636")).toBeNull();
  expect(screen.getByTestId("risk-gate")).toBeTruthy();
  expect(screen.queryByText(/保留上次读取的盘中候选/)).toBeNull();
});

it("a successful same-version daily reply updates the open evidence, while removal or version changes close it permanently", async () => {
  const dailyReply = (price: number, symbol = "600127", version = "v1") => ({date: "2026-10-08", items: [{symbol, execution: {executable_snapshot: {price}}}], meta: {gate: {}, selection_version: version}} as api.DailyPicksPayload);
  vi.mocked(api.getTodayPicks).mockResolvedValue(dailyReply(11));
  render(<HuntingPage />);
  fireEvent.click(await screen.findByRole("button", {name: "每日依据 600127"}));
  expect(screen.getByTestId("daily-detail").textContent).toContain("执行价 11");
  vi.mocked(api.getTodayPicks).mockRejectedValue(new Error("offline"));
  fireEvent.click(screen.getByRole("button", {name: "刷新数据"}));
  await screen.findByText(/保留上次读取的组合/);
  expect(screen.getByTestId("daily-detail").dataset.readError).toBe("true");
  vi.mocked(api.getTodayPicks).mockResolvedValue(dailyReply(12));
  fireEvent.click(screen.getByRole("button", {name: "刷新数据"}));
  await waitFor(() => expect(screen.getByTestId("daily-detail").textContent).toContain("执行价 12"));
  expect(screen.getByTestId("daily-detail").dataset.readError).toBe("false");
  vi.mocked(api.getTodayPicks).mockResolvedValue(dailyReply(13, "002636"));
  fireEvent.click(screen.getByRole("button", {name: "刷新数据"}));
  await waitFor(() => expect(screen.queryByTestId("daily-detail")).toBeNull());
  vi.mocked(api.getTodayPicks).mockResolvedValue(dailyReply(14));
  fireEvent.click(screen.getByRole("button", {name: "刷新数据"}));
  await screen.findByRole("button", {name: "每日依据 600127"});
  expect(screen.queryByTestId("daily-detail")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "每日依据 600127"}));
  vi.mocked(api.getTodayPicks).mockResolvedValue(dailyReply(15, "600127", "v2"));
  fireEvent.click(screen.getByRole("button", {name: "刷新数据"}));
  await waitFor(() => expect(screen.queryByTestId("daily-detail")).toBeNull());
});

it("labels the real role summary as bound price observations and retains its existing sample gate", async () => {
  const {HuntingStatsBar} = await vi.importActual<typeof import("@/components/hunting/stats-bar")>("@/components/hunting/stats-bar");
  const page = render(<HuntingStatsBar health={null} stats={null} rolePerformance={[
    {role: "龙头", count: 2, win_rate: 100, avg_excess: 3},
    {role: "中军", count: 6, win_rate: 70, avg_excess: 1.5},
  ]} />);
  const role = screen.getByText("精选 · 最优角色").closest(".hunting-stat")!;
  expect(role.textContent).toContain("中军");
  expect(role.textContent).not.toContain("龙头");
  expect(role.textContent).toContain("走好观察占比 70%");
  expect(role.getAttribute("title")).toContain("同版同窗可信价格观察");
  expect(role.getAttribute("title")).toContain("有效记录 ≥3 条");
  expect(role.getAttribute("title")).toContain("不是成交胜率");
  expect(screen.getByText("精选 · 信号健康").closest(".hunting-stat")?.getAttribute("title")).toContain("同版同窗可信价格观察");
  page.rerender(<HuntingStatsBar health={null} stats={null} rolePerformance={[]} />);
  expect(screen.getByText("角色价格观察表见复盘区")).toBeTruthy();
});
