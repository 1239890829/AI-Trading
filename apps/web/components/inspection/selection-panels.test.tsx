import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SelectionReviewPanel, SelectionPreviewPanel } from "./selection-panels";
import * as api from "@/lib/api";
import {fromDailyPick,fromIntradayStock} from "@/components/picks/pick-card";
import {getThemeCatalogCompletion} from "@/lib/api/themes";
import type {CurrentThemeCatalogReference} from "@/types/market";
vi.mock("@/lib/api/themes",async()=>({...await vi.importActual<typeof import("@/lib/api/themes")>("@/lib/api/themes"),getThemeCatalogCompletion:vi.fn()}));

vi.mock("@/lib/api", () => ({getPickReviews: vi.fn(), getTodayPicks: vi.fn(), getIntradayTop: vi.fn(), getMorningBriefToday: vi.fn(), getWatcherState: vi.fn(), getIntradayOpportunities: vi.fn(), ApiError: class extends Error {}}));
vi.mock("@/components/hunting/pick-sections", () => ({DailyReviews: ({reviews}: {reviews: api.PickReviewRow[]}) => <div>{reviews.map(row => <span key={row.symbol}>{row.symbol}:{row.selection_version}</span>)}</div>}));
vi.mock("@/components/picks/pick-card", () => ({fromDailyPick: vi.fn((it: object) => ({...it, origin: "picks"})), fromIntradayStock: vi.fn((it: object) => ({...it, origin: "intraday"})), PickCard: ({item}: {item: {symbol: string}}) => <div>候选 {item.symbol}</div>}));
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


it("每日与盘中旁览只传各自响应的目录参考，不跨包拼接或新取源",async()=>{
  const dailyItem={symbol:"600001",theme:"汽车零部件"} as api.DailyPickItem;
  const intradayItem={symbol:"600002",theme:"汽车零部件"} as api.IntradayTopStock;
  const referenceItem={symbol:"600003",theme:"汽车零部件",reference_only:true} as api.IntradayTopStock;
  const dailyReference={scope:"current_directory_reference" as const,catalog_version:"a".repeat(64),observed_at:"2026-10-09T10:00:00+08:00",items:[]};
  const intradayReference={...dailyReference,catalog_version:"b".repeat(64),observed_at:"2026-10-09T10:00:01+08:00"};
  const daily:api.DailyPicksPayload={date:"2026-10-09",items:[dailyItem],meta:{weights:{},selection_version:"daily-original"},current_theme_catalog:dailyReference};
  const intraday:api.IntradayTopPayload={trade_date:"2026-10-09",items:[intradayItem],reference_items:[referenceItem],total_candidates:1,criteria:"原条件",hot_available:false,caveats:[],current_theme_catalog:intradayReference};
  const before=JSON.stringify({daily,intraday});
  vi.mocked(api.getTodayPicks).mockResolvedValue(daily);vi.mocked(api.getIntradayTop).mockResolvedValue(intraday);
  render(<SelectionPreviewPanel request={{kind:"selection-preview"}}/>);
  await screen.findByText("候选 600001");await screen.findByText("候选 600002");
  fireEvent.click(screen.getByText(/已封板 \/ 仅参考/));
  expect(screen.getByText("候选 600003")).toBeTruthy();
  expect(fromDailyPick).toHaveBeenCalledWith(dailyItem,{date:"2026-10-09",version:"daily-original",currentThemeCatalog:dailyReference});
  expect(fromIntradayStock).toHaveBeenCalledWith(intradayItem,intradayReference);
  expect(fromIntradayStock).toHaveBeenCalledWith(referenceItem,intradayReference);
  expect(api.getTodayPicks).toHaveBeenCalledTimes(1);expect(api.getIntradayTop).toHaveBeenCalledTimes(1);
  expect(api.getIntradayOpportunities).not.toHaveBeenCalled();expect(JSON.stringify({daily,intraday})).toBe(before);
});

it("旁览每日和盘中共享完成等待，原业务只读一次且不改日期资格",async()=>{
  const key="f".repeat(32),version="a".repeat(64),clock="2026-10-28T00:00:00+00:00";
  const reference:CurrentThemeCatalogReference={scope:"current_directory_reference",catalog_version:null,observed_at:null,completion_key:key,items:[{theme:"汽车零部件",catalog_mapping_state:"unavailable",catalog_mapping_context:{reason:"catalog_read_pending",completion_key:key}}]};
  const dailyItem={symbol:"600001",theme:"汽车零部件"} as api.DailyPickItem,intradayItem={symbol:"600002",theme:"汽车零部件"} as api.IntradayTopStock;
  const daily={date:"2026-10-09",items:[dailyItem],current_theme_catalog:reference} as api.DailyPicksPayload;
  const intraday={trade_date:"2026-10-09",items:[intradayItem],current_theme_catalog:reference} as api.IntradayTopPayload;
  const before=JSON.stringify({daily,intraday});
  vi.mocked(api.getTodayPicks).mockResolvedValue(daily);vi.mocked(api.getIntradayTop).mockResolvedValue(intraday);
  let finish!:(result:Awaited<ReturnType<typeof getThemeCatalogCompletion>>)=>void;
  vi.mocked(getThemeCatalogCompletion).mockReturnValueOnce(new Promise(resolve=>{finish=resolve;}));
  render(<SelectionPreviewPanel request={{kind:"selection-preview"}}/>);
  await waitFor(()=>expect(getThemeCatalogCompletion).toHaveBeenCalledTimes(1));
  await act(async()=>finish({scope:"current_directory_resolution",completion_key:key,state:"available",catalog_version:version,observed_at:clock,identities:{汽车零部件:{code:"881126.TI",name:"汽车零部件",source:"ths_official",basis:"exact_name",kind:"industry",catalog_version:version,observed_at:clock}},ambiguous:[],reason:null}));
  const overlay=expect.objectContaining({completion_key:undefined,items:[expect.objectContaining({theme:"汽车零部件",catalog_mapping_state:"exact",exact_catalog:expect.objectContaining({kind:"industry",code:"881126.TI"})})]});
  expect(fromDailyPick).toHaveBeenLastCalledWith(dailyItem,{date:"2026-10-09",version:null,currentThemeCatalog:overlay});
  expect(fromIntradayStock).toHaveBeenLastCalledWith(intradayItem,overlay);
  expect(api.getTodayPicks).toHaveBeenCalledTimes(1);expect(api.getIntradayTop).toHaveBeenCalledTimes(1);
  expect(JSON.stringify({daily,intraday})).toBe(before);
});
