import { act, cleanup, fireEvent, render, screen,waitFor,within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AlertItem, DirectionCard, IntradayThemeRow, OpportunitySection, ReviewOutcomeTable, StatsPanel, WatcherPanel } from "./intraday-sections";
import type { BriefDirection, IntradayOpportunities, IntradayReviewStats, OpportunityTheme, WatcherState } from "@/lib/api";
import {getThemeCatalogCompletion} from "@/lib/api/themes";

vi.mock("@/lib/api/themes",async()=>({...await vi.importActual<typeof import("@/lib/api/themes")>("@/lib/api/themes"),getThemeCatalogCompletion:vi.fn()}));

vi.mock("@/components/concept-detail-modal",()=>({ConceptDetailModal:({code,catalogKind,referenceDate,active}: {code:string;catalogKind:string;referenceDate?:string;active:boolean})=>active?<div data-testid="official-detail">{catalogKind}:{code}:{referenceDate}</div>:null}));
vi.mock("@/components/picks/pick-card", () => ({
  fromIntradayStock: (item: unknown) => item,
  PickCard: ({ item, readError }: { item: { symbol: string }; readError?: boolean }) =>
    <article data-testid={item.symbol} data-read-error={String(!!readError)}>{readError ? "参与条件待核" : "当前可参与"}</article>,
}));
afterEach(() => {cleanup(); vi.useRealTimers();});
const theme: OpportunityTheme = {
  theme: "固定题材", stage: "主升", stage_basis: [], strength_score: 70, strength_tier: "A",
  tier_basis: null, formation: null, health_note: null, risks: [], max_boards: 2,
  limit_up_count: 1, has_succession: true,
  participants: [{ symbol: "600001" }] as OpportunityTheme["participants"],
  stocks: [{ symbol: "600002" }] as OpportunityTheme["stocks"],
};
const opps: IntradayOpportunities = { trade_date: "2026-10-09", themes: [theme], caveats: [], hot_available: false,
  summary: { limit_up_total: 1, market_max_boards: 2, top_theme: "固定题材" } };

it("retains direction evidence and each condition interaction with missing review facts left unknown", () => {
  const direction: BriefDirection = {
    direction: "原方向", score: 84, basis: "原规则依据", logic: "原方向逻辑",
    defensive: true, stage: null, entry_mode: "潜伏", entry_basis: "原参与依据", pool: [],
    trigger_conditions: ["原触发条件"], falsify_conditions: ["原证伪条件"],
    review: {outcome: "证伪", failure_class: "数据缺失误导", confirmed: false, falsified: false,
      falsify_keys: [], closing_met: 1, closing_total: 3, closing_unknown: 2,
      actual_pct: null, actual_limit_up: null, actual_max_boards: null, actual_leader: null,
      missing_ratio: null, note: "复盘事实待核", source: "原来源"},
  };
  const view = render(<DirectionCard d={direction}/>);
  expect(screen.getByTitle("原规则依据").textContent).toBe("84 分");
  expect(screen.getByTitle("原参与依据").textContent).toBe("潜伏");
  expect(screen.getByText(/实际：板块 unknown/).textContent).toContain("unknown 2");
  expect(screen.getByText(/实际：板块 unknown/).textContent).toContain("复盘事实待核");
  fireEvent.click(screen.getByRole("tab", {name: "方向逻辑"}));
  expect(screen.getByRole("tabpanel").textContent).toContain("原方向逻辑");
  expect(screen.getByRole("tabpanel").textContent).toContain("规则依据：原规则依据");
  expect(screen.getByRole("tabpanel").textContent).toContain("参与方式依据：原参与依据");
  fireEvent.click(screen.getByRole("tab", {name: "触发条件"}));
  expect(screen.getByRole("tabpanel").textContent).toContain("原触发条件");
  fireEvent.click(screen.getByRole("tab", {name: "证伪条件"}));
  expect(screen.getByRole("tabpanel").textContent).toContain("原证伪条件");
  view.rerender(<ReviewOutcomeTable reviewed={[direction]}/>);
  const table = screen.getByRole("table", {name: "盘前盘后方向对照"});
  const cells = within(within(table).getAllByRole("row")[1]).getAllByRole("cell");
  expect(cells.map(cell => [cell.getAttribute("data-label"), cell.textContent])).toEqual([
    ["方向", "原方向"], ["盘前模式", "潜伏"], ["盘后分类", "证伪"], ["实际板块", "unknown"],
    ["误判归因 / 备注", "缺数据不可信 · 复盘事实待核"],
  ]);
});

it("keeps all daily comparison fields and unavailable return values without manufacturing zeroes", () => {
  const stats: IntradayReviewStats = {
    daily: [{date: "20261028", directions: 9, reviewed: 4, fermented: 1, half: 2, falsified: 1, flat: 0, alerts: 3}],
    directions: {total: 4, outcomes: {发酵: 1, 半发酵: 2, 证伪: 1, 无波动: 0}, failures: {}},
    alert_t1: {n: 0, win_rate: null, avg_win: null, avg_loss: null, profit_loss_ratio: null, avg_return: null},
    alert_t3: {n: 0, win_rate: null, avg_win: null, avg_loss: null, profit_loss_ratio: null, avg_return: null},
    alerts: [], sample_note: "原样本边界：缺失收益保持未知",
  };
  render(<StatsPanel stats={stats}/>);
  const table = screen.getByRole("table", {name: "逐日对照"});
  const cells = within(within(table).getAllByRole("row")[1]).getAllByRole("cell");
  expect(cells.map(cell => [cell.getAttribute("data-label"), cell.textContent])).toEqual([
    ["日期", "20261028"], ["方向", "4/9"], ["发酵", "1"], ["半发酵", "2"],
    ["证伪", "1"], ["无波动", "0"], ["提醒", "3"],
  ]);
  expect(screen.queryByText("0%")).toBeNull();
  expect(screen.getByText(stats.sample_note)).toBeTruthy();
});

it("retains tracker evidence, missing peak and complete alerted symbols in labeled fields", () => {
  const watcher: WatcherState = {active: true, beat_count: 12, trackers: [{
    direction: "原方向", beats: 12, missing_beats: 3, peak_pct: null, below_zero_beats: 2,
    confirmed: false, falsified: true, falsify_triggers: [{key: "original", detail: "原证伪依据"}],
    alerted_symbols: ["000333", "601229", "001318", "600127"], last_confirm: null,
  }]};
  render(<WatcherPanel watcher={watcher} pending={false}/>);
  const table = screen.getByRole("table", {name: "盘中跟踪状态"});
  const cells = within(within(table).getAllByRole("row")[1]).getAllByRole("cell");
  expect(cells.map(cell => [cell.getAttribute("data-label"), cell.textContent])).toEqual([
    ["方向", "原方向"], ["拍数", "12"], ["缺数据拍", "3"], ["峰值涨幅", "—"],
    ["确认", "否"], ["证伪", "是"], ["已提醒", "000333、601229、001318、600127"],
  ]);
  expect(screen.getByTitle("原证伪依据")).toBeTruthy();
});

it("keeps all collapsed theme risks visible and internal inspection does not toggle the row", () => {
  const toggle = vi.fn(), ancestor = vi.fn();
  render(<div onClick={ancestor}><IntradayThemeRow t={{...theme, risks: ["第一风险", "第二风险", "第三风险", "第四风险不能省略"]}} expanded={false} onToggle={toggle} /></div>);
  expect(screen.getByText("第四风险不能省略")).toBeTruthy();
  fireEvent.click(screen.getByText("市场梯队"));
  expect(toggle).not.toHaveBeenCalled();
  expect(ancestor).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("第一风险"));
  expect(toggle).toHaveBeenCalledTimes(1);
  expect(ancestor).not.toHaveBeenCalled();
});

it("题材来源失效同时降级参与组与梯队组，恢复后再显示当前资格", () => {
  const view = render(<OpportunitySection opps={opps} expanded="固定题材" onToggle={() => {}} showLedger={false} readError />);
  expect(screen.getByTestId("600001").dataset.readError).toBe("true");
  expect(screen.getByTestId("600002").dataset.readError).toBe("true");
  expect(screen.queryByText(/只 · 当前未封板、可进入参与评估/)).toBeNull();
  expect(screen.getByRole("heading", { name: /上次题材机会/ })).toBeTruthy();
  view.rerender(<OpportunitySection opps={opps} expanded="固定题材" onToggle={() => {}} showLedger={false} />);
  expect(screen.getByTestId("600001").dataset.readError).toBe("false");
  expect(screen.getByTestId("600002").dataset.readError).toBe("false");
});

it("theme collapse removes candidates from the accessible scope immediately and rapid reversal preserves the reopened cards", () => {
  vi.useFakeTimers();
  const view = render(<OpportunitySection opps={opps} expanded="固定题材" onToggle={() => {}} showLedger={false} />);
  view.rerender(<OpportunitySection opps={opps} expanded={null} onToggle={() => {}} showLedger={false} />);
  expect(screen.queryAllByRole("article")).toHaveLength(0);
  expect(screen.getByTestId("600001").closest("[inert]")).toBeTruthy();
  view.rerender(<OpportunitySection opps={opps} expanded="固定题材" onToggle={() => {}} showLedger={false} />);
  act(() => vi.advanceTimersByTime(200));
  expect(screen.getAllByRole("article")).toHaveLength(2);
});


it("行业官方成分保留类别与原日期，旧catalog_code不形成入口",()=>{
  const exact={code:"881126.TI",name:"汽车零部件",source:"ths_official",basis:"exact_name",kind:"industry" as const,catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00"};
  const view=render(<IntradayThemeRow t={{...theme,theme:"汽车零部件",exact_catalog:exact,catalog_mapping_state:"exact",catalog_code:"885999.TI"}} expanded={false} tradeDate="2026-10-23" onToggle={()=>{}}/>);
  const href=screen.getByRole("link",{name:"市场梯队"}).getAttribute("href")!;expect(new URL(href,"https://example.invalid").searchParams.get("date")).toBe("2026-10-23");
  fireEvent.click(screen.getByRole("button",{name:"官方成分"}));
  expect(screen.getByTestId("official-detail").textContent).toBe("industry:881126.TI:2026-10-23");
  view.rerender(<IntradayThemeRow t={{...theme,catalog_code:"885999.TI",catalog_mapping_state:"unmapped"}} expanded={false} onToggle={()=>{}}/>);
  expect(screen.queryByRole("button",{name:"官方成分"})).toBeNull();
});

it("机会组头完成目录核对后同步展示当前参考和行业成分入口，不改原候选资格",async()=>{
  const key="b".repeat(32),version="a".repeat(64),clock="2026-10-28T00:00:00+00:00";
  const source={...opps,themes:[{...theme,theme:"汽车零部件",catalog_mapping_state:"unavailable" as const}],current_theme_catalog:{scope:"current_directory_reference" as const,catalog_version:null,observed_at:null,completion_key:key,items:[{theme:"汽车零部件",catalog_mapping_state:"unavailable" as const,exact_catalog:null,catalog_mapping_context:{reason:"catalog_read_pending",completion_key:key}}]}};
  const before=JSON.stringify(source);
  let finish!:(value:Awaited<ReturnType<typeof getThemeCatalogCompletion>>)=>void;
  vi.mocked(getThemeCatalogCompletion).mockReturnValueOnce(new Promise(resolve=>{finish=resolve;}));
  render(<OpportunitySection opps={source} expanded="汽车零部件" onToggle={()=>{}} showLedger={false}/>);
  await waitFor(()=>expect(getThemeCatalogCompletion).toHaveBeenCalledTimes(1));
  expect(screen.getByText("目录核对中")).toBeTruthy();
  await act(async()=>finish({scope:"current_directory_resolution",completion_key:key,state:"available",catalog_version:version,observed_at:clock,identities:{汽车零部件:{code:"881126.TI",name:"汽车零部件",source:"ths_official",basis:"exact_name",kind:"industry",catalog_version:version,observed_at:clock}},ambiguous:[],reason:null}));
  expect(screen.getByText("现目录同名参考 · 881126.TI")).toBeTruthy();
  expect(screen.getByText("当时目录未就绪 · 保留原标签")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"当前成分"}));
  expect(screen.getByTestId("official-detail").textContent).toBe("industry:881126.TI:2026-10-09");
  expect(screen.getByTestId("600001").dataset.readError).toBe("false");
  expect(screen.getByTestId("600002").dataset.readError).toBe("false");
  expect(JSON.stringify(source)).toBe(before);
});


it.each([['pre_limit', '临板预警'], ['falsify', '证伪'], ['future_kind', '未分类提醒']])('keeps reminder type %s distinct from a failed thesis', (kind, label) => {
  render(<AlertItem a={{key:'original',kind,direction:'原方向',symbol:'',name:'',text:'原提醒依据',at:'2026-10-27T09:57:58+08:00',meta:{}}}/>);
  expect(screen.getByText(label)).toBeTruthy();
  expect(screen.getByText('2026-10-27 09:57:58')).toBeTruthy();
  if(kind !== 'falsify')expect(screen.queryByText('证伪')).toBeNull();
});

it('does not invent a timestamp or treat a missing matured return as not yet due', () => {
  render(<AlertItem a={{key:'original',kind:'confirm',direction:'原方向',symbol:'',name:'',text:'原提醒依据',at:'',meta:{returns:{ref_date:'2026-10-01',ref_price:10,t1_date:'2026-10-09',t1_return:2,t3_date:'2026-10-13',t3_return:null,complete:false}}}}/>);
  expect(screen.getByText('原时间未记录')).toBeTruthy();
  expect(screen.getByText('待核')).toBeTruthy();
  expect(screen.queryByText('未到期')).toBeNull();
});
