import { useEffect, useRef, useId, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import HuntingPage from "@/app/hunting/page";
import * as api from "@/lib/api";
import { CandidateDataUnavailableError, getStrategyRegistry } from "@/lib/api/picks";
import { getOfficialBoardRanking, getResearchClusters, type OfficialBoardRanking } from "@/lib/api/themes";
import {clusterFixture} from "@/test/research-clusters-fixture";
import {unrecordedResearchClusters} from "@/lib/research-clusters";
import {CardEntryRow} from "@/components/picks/card-entries";

let search = new URLSearchParams();
const movedReplace = vi.hoisted(()=>vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: movedReplace, push: vi.fn() }), useSearchParams: () => search }));
vi.mock("@/hooks/use-polling-fetch", () => ({ usePollingFetch: (fn: () => Promise<unknown>, _ms: number, _key: unknown, options?: {enabled?: boolean}) => {
  const latest = useRef(fn);
  useEffect(() => { latest.current = fn; });
  useEffect(() => { if (options?.enabled !== false) void latest.current(); }, [options?.enabled]);
} }));
vi.mock("@/components/ui/modal-shell", () => ({ ModalShell: () => null }));
vi.mock("@/components/ui/candidate-collection", () => ({ CandidateCollection: ({children,view,description,toolbar = true}: {children: React.ReactNode;view?:"cards"|"list";description?:React.ReactNode;toolbar?:boolean}) => {
  const [mode, setMode] = useState("cards");
  return <div data-testid="candidate-collection" data-mode={view ?? mode}>{description}{toolbar && <button onClick={() => setMode(mode === "cards" ? "list" : "cards")}>本组呈现方式</button>}{children}</div>;
} }));
vi.mock("@/components/ui/loading", () => ({ FadeIn: ({children}: {children: React.ReactNode}) => <div>{children}</div>, CardListSkeleton: () => <div>读取候选中</div>, PageSkeletonFallback: () => null, StatGridSkeleton: () => null, StatsSkeleton: () => null, TableSkeleton: () => null }));
vi.mock("@/components/picks/pick-card", () => ({ PickCard: ({item, readError, dailyEvidence, onShowDailyEvidence, active = true, compact = false}: {item: {symbol: string}; readError?: boolean; dailyEvidence?: {item: api.DailyPickItem}; onShowDailyEvidence?: (item: api.DailyPickItem) => void; active?: boolean; compact?: boolean}) => {
  const instance = useId(); const [field, setField] = useState("原选择");
  return <article data-card-instance={instance} data-read-error={String(!!readError)} data-active={String(active)} data-compact={String(compact)}>候选 {item.symbol}<a href={`/workbench?symbol=${item.symbol}`}>标的 {item.symbol}</a><button onClick={() => setField("已选分项")} aria-label={`本地分项 ${item.symbol}`}>{field}</button><CardEntryRow symbol={item.symbol} />{dailyEvidence && <button onClick={() => onShowDailyEvidence?.(dailyEvidence.item)}>每日依据 {item.symbol}</button>}</article>;
}, fromDailyPick: (item: unknown) => item, fromIntradayStock: (item: unknown) => item, StandAsideBanner: () => <div data-testid="risk-gate">原风险闸门</div> }));
vi.mock("@/components/picks/pick-detail-modal", () => ({PickDetailModal: ({target, readError}: {target: {item: api.DailyPickItem} | null; readError?: boolean}) => target ? <div data-testid="daily-detail" data-read-error={String(!!readError)}>原依据 {target.item.symbol} · 执行价 {target.item.execution?.executable_snapshot.price}</div> : null}));
vi.mock("@/components/detail/detail-modal", () => ({useDetailModal: () => ({open: vi.fn()})}));
vi.mock("@/lib/market-hours", async () => ({...await vi.importActual<typeof import("@/lib/market-hours")>("@/lib/market-hours"),isTradingSession: () => true}));
vi.mock("@/components/hunting/intraday-sections", () => ({ AlertItem: () => null, DirectionCard: () => null, DailyPlanBlock: () => null, EnvStrip: () => null, MacroCalendar: () => null, MarketOverviewStrip: () => null, OpportunitySection: ({showLedger}: {showLedger: boolean}) => <div>题材原消费者：台账嵌入 {String(showLedger)}</div>, ClimateBlock: () => null, OvernightBiasBlock: () => null, ReviewOutcomeTable: ({reviewed}: {reviewed: {direction: string}[]}) => <div>{reviewed.map(row => <p key={row.direction}>原对照 {row.direction}</p>)}</div>, StatsPanel: ({stats}: {stats: {sample_note: string}}) => <div>{stats.sample_note}</div>, WatcherPanel: () => null }));
vi.mock("@/components/hunting/pick-sections", () => ({ DailyReviews: ({reviews}: {reviews: {selection_version?: string | null}[]}) => <div>{reviews.map((row, i) => <span key={i}>复盘版本 {row.selection_version}</span>)}</div>, HistoryList: ({history}: {history: {date: string}[]}) => <div>{history.map(row => <p key={row.date}>原历史 {row.date}</p>)}</div>, ReasonDistribution: () => null, RolePerformanceTable: () => null }));
vi.mock("@/components/hunting/stats-bar", () => ({ HuntingStatsBar: () => null }));
vi.mock("@/components/hunting/post-market-enhance", () => ({ PostMarketEnhance: ({initiallyOpen}: {initiallyOpen: boolean}) => <div>原接力潜伏：{String(initiallyOpen)}</div> }));
vi.mock("@/components/hunting/opportunity-evidence-panel", () => ({ OpportunityEvidencePanel: () => <div>真实依据组件</div> }));
vi.mock("@/components/hunting/watch-ledger-panel", () => ({ WatchLedgerPanel: () => <div>真实跟踪组件</div> }));
vi.mock("@/components/hunting/leader-research-panel", () => ({ LeaderResearchPanel: () => <div>真实研究组件</div> }));
vi.mock("@/lib/api", () => ({ ApiError: class extends Error { status = 500; }, getTodayPicks: vi.fn(), getPicksHistory: vi.fn(), getPickReviews: vi.fn(), getPicksMeta: vi.fn(), getSignalHealth: vi.fn(), getMorningBriefToday: vi.fn(), getWatcherState: vi.fn(), getIntradayReview: vi.fn(), getIntradayOpportunities: vi.fn(), getIntradayTop: vi.fn(), getPositionLabels: vi.fn(), getEventsForSymbol: vi.fn(), getThemesHot: vi.fn(), getBoardFundFlow: vi.fn(), getDiscoveryMemberships: vi.fn(), getConceptDetail: vi.fn(), getBoardFlowMembers: vi.fn() }));
vi.mock("@/lib/api/picks", async () => ({...await vi.importActual<typeof import("@/lib/api/picks")>("@/lib/api/picks"), getStrategyRegistry:vi.fn()}));
vi.mock("@/lib/api/themes", async () => ({...await vi.importActual<typeof import("@/lib/api/themes")>("@/lib/api/themes"), getOfficialBoardRanking:vi.fn(),getResearchClusters:vi.fn()}));

const officialRankingFixture = (): OfficialBoardRanking => ({
  scope:"directory",kind:"concept",source:"ths_official",source_basis:"index_snapshot",available:true,
  catalog_version:"c".repeat(64),catalog_state:"ready",catalog_synced_at:"2026-10-25T08:00:00+08:00",
  directory_total:1,source_total:1,source_returned:1,value_count:1,coverage_state:"source_complete",missing_codes:[],
  source_as_of:null,trade_date:null,quality:"stale",observation_state:"clock_unknown",
  source_ready_from:"2026-10-25T09:50:00+08:00",source_ready_at:"2026-10-25T09:50:00+08:00",
  received_from:"2026-10-25T09:50:01+08:00",received_at:"2026-10-25T09:50:01+08:00",
  rows:[{code:"885517.TI",name:"机器人概念",source:"ths_official",kind:"concept",synced_at:"2026-10-25T08:00:00+08:00",
    price:100,change_pct:0,quote_state:"value"}],
});

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
  vi.mocked(api.getThemesHot).mockResolvedValue({ranking_basis:"hot_stock_concentration",catalog_revision:1,catalog_state:"ready",membership_state:"ready",scope:"隔离夹具：热股×正式目录",ts:"2026-10-25T02:00:00Z", stocks:[{rank:1,symbol:"600127",name:"原标的",heat:10,rank_change:0,themes:["机器人概念"]}],themes:[{theme:"机器人概念",theme_code:"885517.TI",heat:10,heat_complete:true,hot_count:1,hot_symbols:["600127"],source:"ths_official",member_count:100,member_as_of:"2026-10-25T02:00:00Z",concentration_pct:1,best:null,basis:"隔离夹具：正式目录归属"}]});
  vi.mocked(getOfficialBoardRanking).mockResolvedValue(officialRankingFixture());
  vi.mocked(getResearchClusters).mockResolvedValue(unrecordedResearchClusters("2026-10-08"));
  vi.mocked(api.getDiscoveryMemberships).mockResolvedValue({scope:"current_not_historical", requested_trade_date:"2026-10-08",content_version:"a".repeat(64),state:"unknown",source:"local_theme_catalog",source_as_of:null,observed_at:"2026-10-25T10:00:00+08:00",count:1,matched_count:0,unknown_count:1,items:[{symbol:"600127",state:"unknown",themes:[],reason:"未核"}]});
  vi.mocked(api.getConceptDetail).mockResolvedValue({code:"885517.TI",name:"机器人概念",total:1,limit_up_count:0,members:[{symbol:"600127",name:"原标的",price:10,change_pct:0,tags:[]}],tag_groups:[],meta_note:"当前目录"} as unknown as api.ConceptDetail);
  HTMLElement.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("matchMedia", () => ({matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn()}));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

const group = (name: "candidates" | "daily") => document.getElementById(`sec-${name}`)!;
const card = (symbol: string, name: "candidates" | "daily" = "candidates") => within(group(name)).getByText(`候选 ${symbol}`).closest("article")!;

it("reads research clusters from the dated opportunity scope while retaining the unavailable candidate warning", async () => {
  const date = "2026-10-27";
  vi.mocked(api.getIntradayTop).mockRejectedValue(new CandidateDataUnavailableError({empty_reasons:["涨停快照未就绪"]}));
  vi.mocked(api.getIntradayOpportunities).mockResolvedValue({trade_date:date,themes:[],caveats:[]} as unknown as api.IntradayOpportunities);
  vi.mocked(getResearchClusters).mockResolvedValue(clusterFixture(date));
  render(<HuntingPage/>);
  expect(await screen.findByText(/候选数据待核：涨停快照未就绪/)).toBeTruthy();
  const panel = screen.getByRole("region", {name:"研究主题簇"});
  expect(await within(panel).findByRole("button", {name:/人形机器人产业链/})).toBeTruthy();
  expect(panel.textContent).toContain(date);
  expect(getResearchClusters).toHaveBeenCalledTimes(1);
  expect(vi.mocked(getResearchClusters).mock.calls[0][0]).toEqual(expect.objectContaining({date}));
  expect(screen.queryByText(/当前没有同时满足条件的候选/)).toBeNull();
});

it("shows the pending and unverified research scope without requesting a guessed date or offering an inactive retry", async () => {
  let failTop!: (reason:Error) => void;
  let finishOpportunities!: (value:api.IntradayOpportunities) => void;
  vi.mocked(api.getIntradayTop).mockReturnValue(new Promise((_resolve,reject) => {failTop=reject;}));
  vi.mocked(api.getIntradayOpportunities).mockReturnValue(new Promise(resolve => {finishOpportunities=resolve;}));
  render(<HuntingPage/>);
  const panel = screen.getByRole("region", {name:"研究主题簇"});
  expect(within(panel).getByRole("status").textContent).toContain("正在确认盘中交易日期");
  expect(within(panel).queryByRole("button", {name:"重读研究簇"})).toBeNull();
  expect(getResearchClusters).not.toHaveBeenCalled();
  await act(async () => {
    failTop(new Error("candidate-offline"));
    finishOpportunities({trade_date:null,themes:[],caveats:[]} as unknown as api.IntradayOpportunities);
  });
  expect(within(panel).getByRole("alert").textContent).toContain("盘中交易日期未核实");
  expect(within(panel).queryByRole("button", {name:"重读研究簇"})).toBeNull();
  expect(getResearchClusters).not.toHaveBeenCalled();
  expect(screen.getByText(/盘中候选读取失败/)).toBeTruthy();
});

it("keeps the candidate date ahead of different opportunity and daily dates", async () => {
  const date = "2026-10-08";
  vi.mocked(api.getIntradayOpportunities).mockResolvedValue({trade_date:"2026-10-27",themes:[],caveats:[]} as unknown as api.IntradayOpportunities);
  vi.mocked(api.getTodayPicks).mockResolvedValue({date:"2026-10-26",items:[]} as unknown as api.DailyPicksPayload);
  vi.mocked(getResearchClusters).mockResolvedValue(clusterFixture(date));
  render(<HuntingPage/>);
  expect(await screen.findByRole("button", {name:/人形机器人产业链/})).toBeTruthy();
  const panel = screen.getByRole("region", {name:"研究主题簇"});
  expect(panel.textContent).toContain(date);
  expect(getResearchClusters).toHaveBeenCalledTimes(1);
  expect(vi.mocked(getResearchClusters).mock.calls[0][0]).toEqual(expect.objectContaining({date}));
});

it("does not borrow intraday dates when the selected daily scope has no verified date", async () => {
  search=new URLSearchParams({sec:"daily"});
  vi.mocked(api.getTodayPicks).mockRejectedValue(new Error("daily-offline"));
  vi.mocked(api.getIntradayOpportunities).mockResolvedValue({trade_date:"2026-10-27",themes:[],caveats:[]} as unknown as api.IntradayOpportunities);
  render(<HuntingPage/>);
  await screen.findByText(/每日精选读取失败。请刷新重试/);
  const panel = screen.getByRole("region", {name:"研究主题簇"});
  expect(within(panel).getByRole("alert").textContent).toContain("每日精选交易日期未核实");
  expect(within(panel).queryByRole("button", {name:"重读研究簇"})).toBeNull();
  expect(getResearchClusters).not.toHaveBeenCalled();
});

it("research cluster filters only original visible daily cards, keeps original counts and restores them without more reads",async()=>{
  search=new URLSearchParams({sec:"daily"});
  const source=clusterFixture("2026-10-08");source.reference_mode="saved";
  source.clusters[0].stocks[0].symbol="600127";source.clusters[0].branches.forEach(b=>{b.symbols=["600127"];});
  source.clusters[1].stocks[0].symbol="002636";source.clusters[1].branches[0].symbols=["002636"];
  vi.mocked(getResearchClusters).mockResolvedValue(source);
  const frozen=JSON.stringify(source);render(<HuntingPage/>);
  expect(await within(group("daily")).findByText("候选 600127")).toBeTruthy();
  expect(within(group("daily")).getByText("候选 002636")).toBeTruthy();
  fireEvent.click(await screen.findByRole("button",{name:/单股线索/}));
  const chooser=await screen.findByRole("button",{name:/“羊”字辈.*名单1只/});
  const calls=vi.mocked(getResearchClusters).mock.calls.length;
  fireEvent.click(chooser);
  expect(within(group("daily")).queryByText("候选 600127")).toBeNull();
  expect(within(group("daily")).getByText("候选 002636")).toBeTruthy();
  expect(within(group("candidates")).queryByText("候选 002636")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"全部原名单"}));
  expect(within(group("daily")).getByText("候选 600127")).toBeTruthy();
  expect(getResearchClusters).toHaveBeenCalledTimes(calls);
  expect(JSON.stringify(source)).toBe(frozen);
});

it.each(["browse=funds&disc_kind=industry&disc_code=BK0475&disc_range=5d","browse=funds&disc_source=sina&disc_code=gn_ldc","browse=funds&disc_code=886069.TI"])("旧资金链接先迁移、不挂载原选股或资金读取：%s",async query=>{
  search=new URLSearchParams(query);render(<HuntingPage/>);
  await waitFor(()=>expect(movedReplace).toHaveBeenCalled());
  const url=new URL(movedReplace.mock.lastCall![0],"http://localhost");
  expect(url.pathname).toBe("/market");expect(url.searchParams.get("tab")).toBe("fund");
  if(query.includes("BK0475"))expect(Object.fromEntries(url.searchParams)).toEqual({tab:"fund",board_kind:"industry",board_range:"5d",board_code:"BK0475"});
  else expect(url.searchParams.get("board_notice")).toBe("unmapped_legacy_funds");
  expect(screen.queryByRole("navigation",{name:"选股分类发现"})).toBeNull();
  for(const reader of [api.getTodayPicks,api.getPicksHistory,api.getThemesHot,getOfficialBoardRanking,api.getBoardFundFlow,api.getBoardFlowMembers,api.getIntradayTop,api.getDiscoveryMemberships])expect(reader).not.toHaveBeenCalled();
});

it("keeps one shared switch and presentation preference available through pending, valid empty and failed reads", async () => {
  search = new URLSearchParams({sec:"daily"});
  let finish!: (value:api.DailyPicksPayload) => void;
  vi.mocked(api.getTodayPicks).mockReturnValueOnce(new Promise(resolve => {finish=resolve;}));
  const page=render(<HuntingPage/>);
  const switcher=screen.getByRole("navigation",{name:"候选名单切换"});
  const daily=screen.getByRole("link",{name:"每日精选"});
  expect(screen.getAllByRole("link",{name:"每日精选"})).toHaveLength(1);
  expect(daily.textContent).not.toContain("0只");
  fireEvent.click(screen.getByRole("button",{name:"列表"}));
  await act(async () => finish({date:"2026-10-08",items:[]} as api.DailyPicksPayload));
  await waitFor(() => expect(daily.textContent).toContain("0只"));
  expect(within(group("daily")).getByTestId("candidate-collection").dataset.mode).toBe("list");
  daily.focus();search=new URLSearchParams({sec:"candidates"});page.rerender(<HuntingPage/>);
  expect(screen.getByRole("navigation",{name:"候选名单切换"})).toBe(switcher);
  expect(document.activeElement).toBe(daily);
  expect(screen.getByRole("button",{name:"卡片"}).getAttribute("aria-pressed")).toBe("true");
  search=new URLSearchParams({sec:"daily"});page.rerender(<HuntingPage/>);
  expect(screen.getByRole("button",{name:"列表"}).getAttribute("aria-pressed")).toBe("true");
  vi.mocked(api.getTodayPicks).mockRejectedValueOnce(new Error("source-offline"));
  fireEvent.click(screen.getByRole("button",{name:"刷新数据"}));
  await screen.findByText(/每日精选读取失败。以下保留/);
  expect(screen.getByRole("navigation",{name:"候选名单切换"})).toBe(switcher);
  expect(daily.textContent).not.toContain("0只");
  expect(api.getEventsForSymbol).not.toHaveBeenCalled();
});

it("switches complete card groups through the existing URL section and preserves each group's local state", async () => {
  const page = render(<HuntingPage/>);
  await screen.findByText("候选 002636");
  expect(group("candidates").hidden).toBe(false);
  expect(group("daily").hidden).toBe(true);
  expect(group("daily").hasAttribute("inert")).toBe(true);
  expect(within(group("daily")).getAllByText(/候选 /)).toHaveLength(2);
  expect(card("600127").dataset.active).toBe("true");
  expect(card("600127", "daily").dataset.active).toBe("false");
  expect(card("600127").dataset.compact).toBe("true");
  fireEvent.click(screen.getByRole("button", {name:"列表"}));
  fireEvent.click(within(card("600127")).getByRole("button", {name: "本地分项 600127"}));
  const intradayInstance = card("600127").dataset.cardInstance;
  search = new URLSearchParams({sec: "daily"}); page.rerender(<HuntingPage/>);
  expect(group("candidates").hidden).toBe(true);
  expect(group("candidates").hasAttribute("inert")).toBe(true);
  expect(group("daily").hidden).toBe(false);
  expect(group("daily").hasAttribute("inert")).toBe(false);
  expect(card("600127").dataset.active).toBe("false");
  expect(card("600127", "daily").dataset.active).toBe("true");
  fireEvent.click(within(card("600127", "daily")).getByRole("button", {name: "本地分项 600127"}));
  const reads = vi.mocked(api.getTodayPicks).mock.calls.length;
  const topReads = vi.mocked(api.getIntradayTop).mock.calls.length;
  search = new URLSearchParams({sec: "candidates"}); page.rerender(<HuntingPage/>);
  expect(card("600127").dataset.cardInstance).toBe(intradayInstance);
  expect(within(group("candidates")).getByTestId("candidate-collection").dataset.mode).toBe("list");
  expect(within(card("600127")).getByRole("button", {name: "本地分项 600127"}).textContent).toBe("已选分项");
  search = new URLSearchParams({sec: "daily"}); page.rerender(<HuntingPage/>);
  expect(within(card("600127", "daily")).getByRole("button", {name: "本地分项 600127"}).textContent).toBe("已选分项");
  expect(api.getTodayPicks).toHaveBeenCalledTimes(reads);
  expect(api.getIntradayTop).toHaveBeenCalledTimes(topReads);
  expect(api.getEventsForSymbol).not.toHaveBeenCalled();
});

it.each(["desktop", "mobile"])("restores per-group positions in the actual %s scroll owner without moving the market context or navigation focus", async layout => {
  const page = render(<HuntingPage/>);
  await screen.findByText("候选 002636");
  const flow = screen.getByRole("region", {name: "候选结果与题材"});
  const grid = flow.parentElement!;
  flow.style.overflowY = layout === "desktop" ? "auto" : "visible";
  grid.style.overflowY = layout === "mobile" ? "auto" : "hidden";
  const owner = layout === "desktop" ? flow : grid;
  const context = screen.getByRole("complementary", {name: "市场环境、节拍与复盘"});
  context.scrollTop = 64;
  owner.scrollTop = 280; fireEvent.scroll(owner);
  const nav = screen.getByRole("link", {name: "每日精选"}); nav.focus();
  search = new URLSearchParams({sec: "daily"}); page.rerender(<HuntingPage/>);
  expect(owner.scrollTop).toBe(0);
  expect(document.activeElement).toBe(nav);
  owner.scrollTop = 145; fireEvent.scroll(owner);
  search = new URLSearchParams({sec: "candidates"}); page.rerender(<HuntingPage/>);
  expect(owner.scrollTop).toBe(280);
  expect(context.scrollTop).toBe(64);
  expect(screen.getByRole("complementary", {name: "市场环境、节拍与复盘"})).toBe(context);
  search = new URLSearchParams({sec: "daily"}); page.rerender(<HuntingPage/>);
  expect(owner.scrollTop).toBe(145);
  expect(HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();
});

it("an initial daily section keeps the pending target visible and closes global daily details when the card group changes", async () => {
  search = new URLSearchParams({sec: "daily"});
  let finish!: (value: api.DailyPicksPayload) => void;
  vi.mocked(api.getTodayPicks).mockReturnValueOnce(new Promise(resolve => {finish = resolve;}));
  const page = render(<HuntingPage/>);
  await waitFor(() => expect(api.getIntradayTop).toHaveBeenCalledTimes(1));
  expect(group("daily").hidden).toBe(false);
  expect(group("candidates").hidden).toBe(true);
  expect(within(group("daily")).getByText("读取候选中")).toBeTruthy();
  expect(within(group("daily")).queryByText(/没有组合|均已在盘中/)).toBeNull();
  await act(async () => finish({date: "2026-10-08", items: [{symbol: "600127"}]} as api.DailyPicksPayload));
  search = new URLSearchParams({sec: "candidates"}); page.rerender(<HuntingPage/>);
  fireEvent.click(await screen.findByRole("button", {name: "每日依据 600127"}));
  expect(screen.getByTestId("daily-detail")).toBeTruthy();
  search = new URLSearchParams({sec: "daily"}); page.rerender(<HuntingPage/>);
  expect(screen.queryByTestId("daily-detail")).toBeNull();
});

describe("真实选股消费者迁移", () => {
  it("retains each producer's complete list, hides the inactive group and places risk before candidates", async () => {
    render(<HuntingPage />);
    expect(await within(group("candidates")).findByText("候选 600127")).toBeTruthy();
    expect(screen.getAllByText("候选 600127")).toHaveLength(2);
    expect(screen.getByText("候选 002636")).toBeTruthy();
    expect(screen.queryByRole("heading", {name: "每日精选"})).toBeNull();
    expect(screen.getByRole("heading", {name: "每日精选", hidden: true})).toBeTruthy();
    expect(screen.getByText(/2026-10-08 · 当日持久候选/)).toBeTruthy();
    expect(screen.getByText("题材原消费者：台账嵌入 false")).toBeTruthy();
    const intraday = screen.getByRole("heading", {name: "盘中候选"}).closest("section")!;
    const daily = group("daily");
    expect(intraday.id).toBe("sec-candidates");
    expect(daily.id).toBe("sec-daily");
    expect(intraday.contains(card("600127"))).toBe(true);
    expect(daily.contains(screen.getByText("候选 002636"))).toBe(true);
    expect(screen.getAllByTestId("candidate-collection")).toHaveLength(2);
    expect(screen.queryByRole("region", {name: "入选依据对照工作区"})).toBeNull();
    const risk = screen.getByTestId("risk-gate");
    expect(risk.compareDocumentPosition(card("600127")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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
    await within(group("candidates")).findByText("候选 600127");
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
  expect(card("600127")).toBeTruthy();
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

it("review keeps outcome polling but rereads the history catalogue only on explicit refresh", async () => {
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
  for (const fn of retained.filter(fn => fn !== api.getPicksHistory)) expect(fn).toHaveBeenCalledTimes(2);
  expect(api.getPicksHistory).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/原记录复盘：2026-10-09 · 版本 v1/)).toBeTruthy();
  for (const text of ["原对照 农业", "原历史 2026-10-07", "复盘版本 v1", "test-fixture", "原跟踪统计来源", "原daily来源说明"]) expect(screen.getByText(text)).toBeTruthy();
  expect(screen.getByText(/保留上次读取的对照/)).toBeTruthy();
  expect(screen.getByText(/统计读取失败/)).toBeTruthy();
});

it("closed review does not read archives; opening it preserves current candidate and gate polling", async () => {
  vi.useFakeTimers();
  render(<HuntingPage />);
  await act(async () => {});
  const archives = [api.getPicksHistory, api.getPickReviews, api.getPicksMeta];
  for (const fn of archives) expect(fn).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("当日对照与精选复盘"));
  await act(async () => {});
  // jsdom does not apply the browser's native details toggle.
  const details = screen.getByText("当日对照与精选复盘").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  await act(async () => {});
  for (const fn of archives) expect(fn).toHaveBeenCalledTimes(1);
  await act(async () => {await vi.advanceTimersByTimeAsync(60_000);});
  expect(api.getPicksHistory).toHaveBeenCalledTimes(1);
  expect(api.getPickReviews).toHaveBeenCalledTimes(2);
  expect(api.getTodayPicks).toHaveBeenCalledTimes(2);
  expect(screen.getByText("候选 002636")).toBeTruthy();
});

it("closed reference details keeps the full count but makes no per-stock presence reads", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-12T10:00:00+08:00"));
  vi.mocked(api.getIntradayTop).mockResolvedValue({trade_date: "2026-10-08", items: [{symbol: "600127"}], reference_items: [{symbol: "600987"}, {symbol: "600988"}]} as api.IntradayTopPayload);
  render(<HuntingPage />);
  await act(async () => {});
  const summary = screen.getByText(/涨停梯队 2 只/);
  const refCalls = () => vi.mocked(api.getEventsForSymbol).mock.calls.filter(([symbol]) => ["600987", "600988"].includes(symbol));
  expect(refCalls()).toHaveLength(0);
  expect(screen.queryByText("候选 600987")).toBeNull();
  fireEvent.click(summary); await act(async () => {});
  expect(screen.getByText("候选 600987")).toBeTruthy();
  expect(screen.getByText("候选 600988")).toBeTruthy();
  expect(refCalls()).toHaveLength(0);
  await act(async () => {await vi.advanceTimersByTimeAsync(30_000);});
  expect(refCalls()).toHaveLength(0);
  fireEvent.click(summary); await act(async () => {});
  await act(async () => {await vi.advanceTimersByTimeAsync(60_000);});
  expect(refCalls()).toHaveLength(0);
  expect(screen.getByText(/涨停梯队 2 只/)).toBeTruthy();
  expect(screen.getByText("候选 002636")).toBeTruthy();
  expect(card("600127")).toBeTruthy();
});

it("reference disclosure keeps an inert exit for 160ms and a quick reversal preserves the same cards", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("matchMedia", () => ({matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn()}));
  vi.mocked(api.getIntradayTop).mockResolvedValue({trade_date: "2026-10-08", items: [{symbol: "600127"}], reference_items: [{symbol: "600987"}]} as api.IntradayTopPayload);
  render(<HuntingPage />); await act(async () => {});
  const trigger = screen.getByRole("button", {name: /涨停梯队 1 只/});
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(trigger);
  const card = screen.getByText("候选 600987");
  const disclosure = card.closest(".motion-disclosure")!;
  expect(disclosure.getAttribute("data-open")).toBe("true");
  fireEvent.click(trigger);
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(disclosure.getAttribute("data-open")).toBe("false");
  expect(disclosure.hasAttribute("inert")).toBe(true);
  await act(async () => {await vi.advanceTimersByTimeAsync(100);});
  expect(screen.getByText("候选 600987")).toBe(card);
  fireEvent.click(trigger);
  await act(async () => {await vi.advanceTimersByTimeAsync(160);});
  expect(screen.getByText("候选 600987")).toBe(card);
  expect(disclosure.hasAttribute("inert")).toBe(false);
  fireEvent.click(trigger);
  await act(async () => {await vi.advanceTimersByTimeAsync(159);});
  expect(screen.getByText("候选 600987")).toBe(card);
  await act(async () => {await vi.advanceTimersByTimeAsync(1);});
  expect(screen.queryByText("候选 600987")).toBeNull();
});

it("same-scope refresh failures preserve all candidates but remove current gate eligibility", async () => {
  render(<HuntingPage />);
  await screen.findByText("候选 002636");
  vi.mocked(api.getTodayPicks).mockRejectedValue(new Error("daily-offline"));
  vi.mocked(api.getIntradayTop).mockRejectedValue(new Error("top-offline"));
  fireEvent.click(screen.getByRole("button", {name: "刷新数据"}));
  await screen.findByText(/保留上次读取的盘中候选/);
  expect(card("600127").dataset.readError).toBe("true");
  expect(screen.getByText("候选 002636").closest("article")?.dataset.readError).toBe("true");
  expect(screen.getAllByText(/候选 (600127|002636)/)).toHaveLength(3);
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
  expect(card("600128").dataset.readError).toBe("false");
  expect(screen.getByText("候选 002826").closest("article")?.dataset.readError).toBe("false");
  expect(screen.queryByText("候选 600127")).toBeNull();
  expect(screen.queryByText("候选 002636")).toBeNull();
  expect(screen.getByTestId("risk-gate")).toBeTruthy();
  expect(screen.queryByText(/保留上次读取的盘中候选/)).toBeNull();
});

it("acknowledges manual reads, prevents duplicate primary reads and reports partial failure without clearing cards", async () => {
  render(<HuntingPage/>);
  await screen.findByText("候选 002636");
  let finish!: (value: api.DailyPicksPayload) => void;
  let reject!: (error: Error) => void;
  vi.mocked(api.getTodayPicks).mockReturnValueOnce(new Promise(resolve => {finish = resolve;}));
  vi.mocked(api.getIntradayTop).mockReturnValueOnce(new Promise((_resolve, fail) => {reject = fail;}));
  const instance = card("600127");
  fireEvent.click(screen.getByRole("button", {name: "刷新数据"}));
  const busy = screen.getByRole("button", {name: "读取中…"});
  expect(busy.hasAttribute("disabled")).toBe(true);
  expect(busy.getAttribute("aria-busy")).toBe("true");
  expect(screen.getByRole("status",{name:"选股读取状态"}).textContent).toContain("正在读取名单");
  fireEvent.click(busy);
  await waitFor(() => expect(api.getTodayPicks).toHaveBeenCalledTimes(2));
  expect(api.getIntradayTop).toHaveBeenCalledTimes(2);
  expect(card("600127")).toBe(instance);
  await act(async () => {
    finish({date: "2026-10-08", items: [{symbol: "600127"}, {symbol: "002636"}], meta: {gate: {}}} as api.DailyPicksPayload);
    reject(new Error("source-offline"));
  });
  await screen.findByRole("button", {name: "刷新数据"});
  expect(screen.getByRole("status",{name:"选股读取状态"}).textContent).toContain("部分读取失败");
  expect(screen.getByRole("status",{name:"选股读取状态"}).textContent).not.toContain("本轮读取完成");
  expect(card("600127")).toBe(instance);
  expect(instance.dataset.readError).toBe("true");
  expect(api.getEventsForSymbol).not.toHaveBeenCalled();
});

it("an auxiliary slow read does not disable refreshing the ready candidate list or fetch closed archives", async () => {
  let finishHealth!: (value: api.SignalHealthPayload) => void;
  vi.mocked(api.getSignalHealth).mockReturnValueOnce(new Promise(resolve => {finishHealth = resolve;}));
  render(<HuntingPage/>);
  await screen.findByText("候选 002636");
  const refresh = screen.getByRole("button", {name: "刷新数据"});
  expect(refresh.hasAttribute("disabled")).toBe(false);
  expect(screen.getByRole("status",{name:"选股读取状态"}).textContent).toContain("附加资料");
  fireEvent.click(refresh);
  await waitFor(() => expect(api.getTodayPicks).toHaveBeenCalledTimes(2));
  expect(api.getSignalHealth).toHaveBeenCalledTimes(1);
  expect(api.getPicksHistory).not.toHaveBeenCalled();
  expect(api.getPickReviews).not.toHaveBeenCalled();
  expect(api.getPicksMeta).not.toHaveBeenCalled();
  await act(async () => finishHealth(null as unknown as api.SignalHealthPayload));
  await waitFor(() => expect(screen.getByRole("status",{name:"选股读取状态"}).textContent).toContain("本轮读取完成"));
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

it("retains the legacy card instance on same-version refresh but retires it when its list date or version changes", async () => {
  const reply = (date = "2026-10-08", version = "v1") => ({date, items: [{symbol: "002636"}], meta: {selection_version: version}} as api.DailyPicksPayload);
  vi.mocked(api.getTodayPicks).mockResolvedValue(reply());
  render(<HuntingPage/>);
  const card = () => screen.getByText("候选 002636").closest("article")!;
  await screen.findByText("候选 002636");
  const initial = card().dataset.cardInstance;
  await act(async () => {fireEvent.click(screen.getByRole("button", {name: "刷新数据"}));});
  expect(card().dataset.cardInstance).toBe(initial);
  vi.mocked(api.getTodayPicks).mockResolvedValue(reply("2026-10-08", "v2"));
  await act(async () => {fireEvent.click(screen.getByRole("button", {name: "刷新数据"}));});
  await waitFor(() => expect(card().dataset.cardInstance).not.toBe(initial));
  const updated = card().dataset.cardInstance;
  vi.mocked(api.getTodayPicks).mockResolvedValue(reply("2026-10-09", "v2"));
  await act(async () => {fireEvent.click(screen.getByRole("button", {name: "刷新数据"}));});
  await waitFor(() => expect(card().dataset.cardInstance).not.toBe(updated));
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


it("loads only the chosen discovery source and original selected group, without hidden market or research polling", async () => {
  search=new URLSearchParams({browse:"hot"});render(<HuntingPage/>);
  await screen.findByRole("button",{name:"查看机器人概念成分"});
  expect(getOfficialBoardRanking).toHaveBeenCalledTimes(1);
  expect(getOfficialBoardRanking).toHaveBeenCalledWith("concept",{signal:expect.any(AbortSignal),fresh:false});
  expect(api.getThemesHot).not.toHaveBeenCalled();
  expect(api.getDiscoveryMemberships).not.toHaveBeenCalled();
  expect(screen.getByText(/来源交易日与行情时点未提供/)).toBeTruthy();
  expect(screen.getByText("按需读取")).toBeTruthy();
  expect(screen.queryByRole("button",{name:"刷新数据"})).toBeNull();
  expect(api.getIntradayTop).toHaveBeenCalledTimes(1);
  for(const read of [api.getTodayPicks,api.getSignalHealth,api.getMorningBriefToday,api.getWatcherState,api.getIntradayReview,api.getIntradayOpportunities,api.getPositionLabels,api.getEventsForSymbol,api.getBoardFundFlow]) expect(read).not.toHaveBeenCalled();
  expect(group("candidates").hidden).toBe(true);
  expect(card("600127").dataset.active).toBe("false");
  vi.useFakeTimers();await act(async()=>{await vi.advanceTimersByTimeAsync(120_001);});
  expect(getOfficialBoardRanking).toHaveBeenCalledTimes(1);
  expect(api.getThemesHot).not.toHaveBeenCalled();expect(api.getDiscoveryMemberships).not.toHaveBeenCalled();
  expect(api.getIntradayTop).toHaveBeenCalledTimes(1);
});

it.each(["candidates","daily"] as const)("retries the failed %s original list independently and restores real membership/code comparison",async currentGroup=>{
  const day="2026-10-08";
  let finishRetry!:()=>void;
  const dailyReply={date:day,items:[{symbol:"600127",name:"原标的"}],meta:{selection_version:"retry-daily-v1"}} as unknown as api.DailyPicksPayload;
  const intradayReply={trade_date:day,selection_identity_version:"retry-intraday-v1",items:[{symbol:"600127",name:"原标的"}],reference_items:[]} as unknown as api.IntradayTopPayload;
  if(currentGroup==="daily")vi.mocked(api.getTodayPicks).mockRejectedValueOnce(new Error("original daily unavailable"))
    .mockImplementationOnce(()=>new Promise(resolve=>{finishRetry=()=>resolve(dailyReply);}));
  else vi.mocked(api.getIntradayTop).mockRejectedValueOnce(new Error("original intraday unavailable"))
    .mockImplementationOnce(()=>new Promise(resolve=>{finishRetry=()=>resolve(intradayReply);}));
  vi.mocked(api.getDiscoveryMemberships).mockImplementation(async(symbols,date)=>({
    scope:"current_not_historical",requested_trade_date:date??null,content_version:"b".repeat(64),state:"ready",source:"local_theme_catalog",
    source_as_of:null,observed_at:"2026-10-25T10:00:00+08:00",count:symbols.length,matched_count:symbols.length,unknown_count:0,
    items:symbols.map(symbol=>({symbol,state:"matched" as const,themes:[{theme_code:"885517.TI",theme_name:"机器人概念",source:"ths_official"}],reason:null})),
  }));
  search=new URLSearchParams({browse:"hot",disc_view:"concentration",sec:currentGroup});
  const page=render(<HuntingPage/>);
  await screen.findByRole("button",{name:"查看机器人概念成分"});
  await screen.findByText("原名单读取失败，归属对照未完成。");
  expect(screen.getByRole("list",{name:"分类结果"}).textContent).toContain("原名单读取失败");
  expect(screen.queryByText("本组原名单已确认空")).toBeNull();
  expect(api.getDiscoveryMemberships).not.toHaveBeenCalled();
  expect(api.getThemesHot).toHaveBeenCalledTimes(1);
  expect(getOfficialBoardRanking).not.toHaveBeenCalled();
  expect(screen.queryByRole("button",{name:"刷新数据"})).toBeNull();

  fireEvent.click(screen.getByRole("button",{name:"重试原名单"}));
  const busy=await screen.findByRole("button",{name:"正在重试原名单…"});
  expect(busy).toHaveProperty("disabled",true);
  fireEvent.click(busy);
  const producer=currentGroup==="daily"?api.getTodayPicks:api.getIntradayTop;
  const other=currentGroup==="daily"?api.getIntradayTop:api.getTodayPicks;
  expect(producer).toHaveBeenCalledTimes(2);expect(other).not.toHaveBeenCalled();
  expect(api.getThemesHot).toHaveBeenCalledTimes(1);
  await act(async()=>{finishRetry();});
  await screen.findByText("已核匹配 1 只");
  expect(screen.queryByText("原名单读取失败，归属对照未完成。")).toBeNull();
  expect(api.getDiscoveryMemberships).toHaveBeenCalledTimes(1);
  expect(api.getDiscoveryMemberships).toHaveBeenCalledWith(["600127"],day,{signal:expect.any(AbortSignal)});

  search=new URLSearchParams({browse:"hot",disc_view:"concentration",sec:currentGroup,disc_code:"885517.TI"});page.rerender(<HuntingPage/>);
  expect(await screen.findByRole("button",{name:"返回原名单核对原标的"})).toBeTruthy();
  const detail=screen.getByTestId("discovery-detail");
  expect(detail.textContent).toContain(`原名单日期：${day}`);
  expect(detail.textContent).toContain("代码相同 1 只");
  expect(api.getConceptDetail).toHaveBeenCalledTimes(1);
  vi.useFakeTimers();await act(async()=>{await vi.advanceTimersByTimeAsync(120_001);});
  expect(producer).toHaveBeenCalledTimes(2);expect(other).not.toHaveBeenCalled();
  expect(api.getThemesHot).toHaveBeenCalledTimes(1);expect(api.getDiscoveryMemberships).toHaveBeenCalledTimes(1);
  for(const read of [api.getPicksHistory,api.getPickReviews,api.getPicksMeta,api.getSignalHealth,api.getMorningBriefToday,api.getWatcherState,
    api.getIntradayReview,api.getIntradayOpportunities,api.getPositionLabels,api.getEventsForSymbol,api.getBoardFundFlow])expect(read).not.toHaveBeenCalled();
});

it("retires a previously open original-evidence drawer on classification switch and preserves the original card instance",async()=>{
  const page=render(<HuntingPage/>);await screen.findByText("候选 002636");
  const instance=card("600127").dataset.cardInstance;
  fireEvent.click(screen.getByRole("button",{name:"每日依据 600127"}));
  expect(screen.getByTestId("daily-detail")).toBeTruthy();
  search=new URLSearchParams({browse:"hot"});page.rerender(<HuntingPage/>);
  await screen.findByRole("button",{name:"查看机器人概念成分"});
  expect(screen.queryByTestId("daily-detail")).toBeNull();
  search=new URLSearchParams();page.rerender(<HuntingPage/>);
  expect(card("600127").dataset.cardInstance).toBe(instance);
  expect(card("600127").dataset.active).toBe("true");
  expect(screen.queryByTestId("daily-detail")).toBeNull();
});

it("restores mobile discovery list position after detail/back and does not overwrite it with detail scrolling",async()=>{
  search=new URLSearchParams({browse:"hot"});const page=render(<HuntingPage/>);
  await screen.findByRole("button",{name:"查看机器人概念成分"});
  const flow=screen.getByRole("region",{name:"候选结果与题材"});const grid=flow.parentElement!;
  flow.style.overflowY="visible";grid.style.overflowY="auto";
  grid.scrollTop=345;fireEvent.scroll(grid);
  search=new URLSearchParams({browse:"hot",disc_code:"885517.TI"});page.rerender(<HuntingPage/>);
  await screen.findByTestId("discovery-detail");
  await waitFor(()=>expect(grid.scrollTop).toBe(0));
  grid.scrollTop=90;fireEvent.scroll(grid);
  search=new URLSearchParams({browse:"hot"});page.rerender(<HuntingPage/>);
  await waitFor(()=>expect(screen.queryByTestId("discovery-detail")).toBeNull());
  expect(grid.scrollTop).toBe(345);
  expect(document.activeElement).toBe(screen.getByRole("button",{name:"查看机器人概念成分"}));
});

it("consumes a return-to-selection focus intent once, rather than refocusing on each saved-list refresh",async()=>{
  search=new URLSearchParams({selection:"600127"});render(<HuntingPage/>);
  const stock=await screen.findByRole("link",{name:"标的 600127"});
  await waitFor(()=>expect(document.activeElement).toBe(stock));
  const control=screen.getByRole("button",{name:"列表"});control.focus();
  vi.mocked(api.getIntradayTop).mockResolvedValue({...await api.getIntradayTop(),items:[{symbol:"600127"}]} as api.IntradayTopPayload);
  fireEvent.click(screen.getByRole("button",{name:"刷新数据"}));
  await waitFor(()=>expect(vi.mocked(api.getIntradayTop).mock.calls.length).toBeGreaterThan(1));
  expect(document.activeElement).toBe(control);
});


it("preserves the original market-context position across a classification placeholder",async()=>{
  const page=render(<HuntingPage/>);await screen.findByText("候选 002636");
  const side=screen.getByRole("complementary",{name:"市场环境、节拍与复盘"});
  side.scrollTop=164;fireEvent.scroll(side);
  search=new URLSearchParams({browse:"hot"});page.rerender(<HuntingPage/>);
  await screen.findByRole("button",{name:"查看机器人概念成分"});
  side.scrollTop=0;fireEvent.scroll(side);
  search=new URLSearchParams();page.rerender(<HuntingPage/>);
  expect(side.scrollTop).toBe(164);
});

it("stops previously opened review archives when their context is hidden by classification browsing",async()=>{
  const page=render(<HuntingPage/>);await screen.findByText("候选 002636");
  const journal=screen.getByText("当日对照与精选复盘").closest("details")!;
  journal.open=true;fireEvent(journal,new Event("toggle",{bubbles:true}));
  await waitFor(()=>expect(api.getPickReviews).toHaveBeenCalled());
  search=new URLSearchParams({browse:"hot"});page.rerender(<HuntingPage/>);
  await screen.findByRole("button",{name:"查看机器人概念成分"});
  const reads=vi.mocked(api.getPickReviews).mock.calls.length;
  vi.useFakeTimers();await act(async()=>{await vi.advanceTimersByTimeAsync(120_001);});
  expect(api.getPickReviews).toHaveBeenCalledTimes(reads);
});

const savedMethodFixture = () => ({
  key:"daily_picks",name:"每日精选六维组合",catalog_kind:"registered",status:"active",basis:"market_neutral",source:"daily_pick_review",evaluable:true,
  discovery:{category:"multi_factor",category_label:"多因子组合",session:"daily",session_label:"每日生成",summary:"原六维组合记录",supports_saved_runs:true},
} satisfies api.StrategyRegistryItem);

it("opens the chosen method in the existing right column with one opt-in directory read", async () => {
  vi.mocked(getStrategyRegistry).mockResolvedValue([savedMethodFixture()]);
  search=new URLSearchParams({browse:"methods",disc_method:"daily_picks",date:"2026-10-22"});
  const view=render(<HuntingPage/>);
  const side=await screen.findByLabelText("方法详情 每日精选六维组合");
  expect(within(side).getByRole("heading",{name:"每日精选六维组合"})).toBeTruthy();
  expect(getStrategyRegistry).toHaveBeenCalledTimes(1);
  expect(vi.mocked(getStrategyRegistry).mock.calls[0][0]?.includeCandidates).toBe(true);
  expect(api.getTodayPicks).not.toHaveBeenCalled();
  expect(api.getIntradayTop).not.toHaveBeenCalled();
  const link=within(side).getByRole("link",{name:"查看原运行与版本"});
  expect(new URL(link.getAttribute("href")!,"http://localhost").searchParams.get("date")).toBe("2026-10-22");
  search=new URLSearchParams({browse:"methods",disc_method:"missing-key",date:"2026-10-22"});
  view.rerender(<HuntingPage/>);
  expect(screen.queryByLabelText("方法详情 每日精选六维组合")).toBeNull();
  expect(screen.getByRole("heading",{name:"核对真实方法"})).toBeTruthy();
});

it("refreshing the same method updates its data without stealing mobile reading focus", async () => {
  const original=savedMethodFixture();
  vi.mocked(getStrategyRegistry).mockResolvedValue([original]);
  search=new URLSearchParams({browse:"methods",disc_method:"daily_picks"});
  render(<HuntingPage/>);
  const side=await screen.findByLabelText("方法详情 每日精选六维组合");
  const heading=within(side).getByRole("heading",{name:"每日精选六维组合"});
  await waitFor(()=>expect(document.activeElement).toBe(heading));
  const close=within(side).getByRole("button",{name:"返回方法目录"});
  close.focus();side.scrollTop=93;
  expect(document.activeElement).toBe(close);
  let finish!: (rows: api.StrategyRegistryItem[]) => void;
  vi.mocked(getStrategyRegistry).mockReturnValueOnce(new Promise(resolve=>{finish=resolve;}));
  fireEvent.click(screen.getByRole("button",{name:"刷新方法"}));
  await waitFor(()=>expect(getStrategyRegistry).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText("方法详情 每日精选六维组合")).toBe(side);
  expect(document.activeElement).toBe(close);
  await act(async()=>{finish([{...original,discovery:{...original.discovery,summary:"新读取的原方法说明"}}]);});
  await within(side).findByText("新读取的原方法说明");
  await act(async()=>{});
  expect(screen.getByLabelText("方法详情 每日精选六维组合")).toBe(side);
  expect(within(side).getByRole("button",{name:"返回方法目录"})).toBe(close);
  expect(document.activeElement).toBe(close);
  expect(side.scrollTop).toBe(93);
  expect(getStrategyRegistry).toHaveBeenCalledTimes(2);
});

it.each(["desktop", "mobile"])("returns %s method focus to search when unfollowing removes the original row", async layout => {
  vi.stubGlobal("matchMedia", () => ({matches: layout === "mobile", addEventListener: vi.fn(), removeEventListener: vi.fn()}));
  localStorage.setItem("ashare.discovery.methods.favorites.v1", "[]");
  fireEvent(window, new StorageEvent("storage", {key: "ashare.discovery.methods.favorites.v1", newValue: "[]"}));
  vi.mocked(getStrategyRegistry).mockResolvedValue([savedMethodFixture()]);
  search = new URLSearchParams({browse: "methods"});
  const view = render(<HuntingPage />);
  fireEvent.click(await screen.findByRole("button", {name: "关注每日精选六维组合"}));
  fireEvent.click(screen.getByRole("button", {name: "我关注 1"}));
  expect(screen.getByRole("link", {name: "查看方法规则：每日精选六维组合"})).toBeTruthy();

  search = new URLSearchParams({browse: "methods", disc_method: "daily_picks"});
  view.rerender(<HuntingPage />);
  const side = await screen.findByLabelText("方法详情 每日精选六维组合");
  fireEvent.click(within(side).getByRole("button", {name: "取消关注每日精选六维组合"}));
  expect(screen.queryByRole("link", {name: "查看方法规则：每日精选六维组合"})).toBeNull();
  expect(screen.getByText("当前关注没有匹配方法。")).toBeTruthy();
  const close = within(side).getByRole("button", {name: "返回方法目录"});
  close.focus(); fireEvent.click(close);
  search = new URLSearchParams({browse: "methods"});
  view.rerender(<HuntingPage />);

  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", {name: "搜索战法"})));
  expect(screen.queryByLabelText("方法详情 每日精选六维组合")).toBeNull();
  expect(getStrategyRegistry).toHaveBeenCalledTimes(1);
});

it("shows original business data causes without pretending an unavailable response is a new current list", async () => {
  const {CandidateDataUnavailableError} = await import("@/lib/api/picks");
  vi.mocked(api.getIntradayTop).mockRejectedValue(new CandidateDataUnavailableError({
    empty_reasons: ["全市场数据未就绪（降级），本轮候选资格暂不可核实", "部分题材与官方成分的对应关系尚未核实"],
    read_context: {snapshot_state: "degraded", snapshot_as_of: "2026-10-27T14:53:27+08:00", linkage_failed: false,
      theme_gate_counts: {missing_catalog: 2}, missing_quote: 7},
  }));
  render(<HuntingPage/>);
  const message = await within(group("candidates")).findByRole("alert");
  expect(message.textContent).toContain("候选数据待核：全市场数据未就绪（降级）");
  expect(message.textContent).toContain("官方成分的对应关系尚未核实");
  expect(message.textContent).not.toContain("读取失败");
  expect(within(group("candidates")).queryByText("候选 600127")).toBeNull();
  expect(screen.getByRole("link", {name: "盘中候选"}).textContent).not.toContain("0只");
});

it("retains the prior candidate only as a past observation when the next read has domain-unavailable evidence", async () => {
  const {CandidateDataUnavailableError} = await import("@/lib/api/picks");
  render(<HuntingPage/>);
  await within(group("candidates")).findByText("候选 600127");
  const before = card("600127");
  vi.mocked(api.getIntradayTop).mockRejectedValue(new CandidateDataUnavailableError({empty_reasons: ["全市场数据未就绪（过期），候选资格待核"]}));
  fireEvent.click(screen.getByRole("button", {name: "刷新数据"}));
  await within(group("candidates")).findByText(/候选数据待核：全市场数据未就绪（过期）/);
  expect(card("600127")).toBe(before);
  expect(before.dataset.readError).toBe("true");
  expect(within(group("candidates")).getByRole("alert").textContent).toContain("保留上次读取的盘中候选，当前参与条件待重新核实");
  expect(screen.getByRole("link", {name: "盘中候选"}).textContent).not.toContain("1只");
});
