import {act, cleanup, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {getThemes, getThemesHot, getThemeStrength, getAuctionBenchmark, getSkyrocket} from "@/lib/api";
import {ThemesTab} from "./themes-tab";
import type {ThemeBoardPayload,CurrentThemeCatalogReference} from "@/types/market";
import type {ThemeStrengthRow, HotTheme} from "@/lib/api";
import {getThemeCatalogCompletion} from "@/lib/api/themes";
vi.mock("@/lib/api/themes",async()=>({...await vi.importActual<typeof import("@/lib/api/themes")>("@/lib/api/themes"),getThemeCatalogCompletion:vi.fn()}));

const scope = vi.hoisted(() => ({params: new URLSearchParams()}));
vi.mock("@/components/inspection/surface-scope", () => ({useSurfaceScope: () => ({searchParams: scope.params, replaceSearch: vi.fn()})}));
vi.mock("@/components/detail/symbol-detail-context", () => ({useSymbolDetail: () => ({open: vi.fn()}), symbolDetailClick: () => () => {}}));
vi.mock("@/components/theme-card", () => ({RankDelta: () => null, ThemeCardView: ({card, strength, hot, currentThemeCatalog}: {card: {theme: string}; strength: ThemeStrengthRow | null; hot?: HotTheme; currentThemeCatalog?: CurrentThemeCatalogReference}) =>
  <div data-testid={card.theme}>{strength ? `${strength.code}:${strength.count}` : "无正式成分样本"}{hot && <span data-testid={`${card.theme}-hot`}>{hot.theme_code}:{hot.heat}</span>}{currentThemeCatalog && <><span data-testid={`${card.theme}-current-reference`}>{currentThemeCatalog.scope}</span><span data-testid={`${card.theme}-current-state`}>{currentThemeCatalog.items.find(item=>item.theme===card.theme)?.catalog_mapping_state}</span></>}</div>}));
vi.mock("@/lib/api", () => ({getThemes: vi.fn(), getThemesHot: vi.fn(), getThemeStrength: vi.fn(), getAuctionBenchmark: vi.fn(), getSkyrocket: vi.fn()}));
vi.mock("@/lib/market-hours", () => ({isTradingSession: () => true}));

const row = (code: string, name: string, count: number) => ({code, name, count} as ThemeStrengthRow);
const payload = (themes: Record<string, unknown>[]) => ({trade_date: "2026-10-27", themes: themes.map(theme=>{const identity=theme.exact_catalog as Record<string,unknown>|null;return {...theme,...(identity?{catalog_mapping_state:"exact",exact_catalog:{name:theme.theme,basis:"exact_name",kind:"concept",catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00",...identity}}:{})};}), broken_ladder: [], caveats: [],
  summary: {limit_up_total: 3, theme_count: themes.length, market_max_boards: 2}} as unknown as ThemeBoardPayload);
const hotPayload = (themes: HotTheme[]) => ({ts: null, themes, stocks: [], ranking_basis: "hot_stock_concentration" as const,
  catalog_revision: 1, catalog_state: "ready" as const, membership_state: "ready" as const, scope: "本次读取同花顺热股与已存正式目录的成分交集"});
const hotRow = (over: Partial<HotTheme> = {}): HotTheme => ({theme: "同名原因", theme_code: "885001.TI", source: "ths_official", heat: 123,
  heat_complete: true, member_count: 10, member_as_of: null, hot_symbols: [], concentration_pct: 10, hot_count: 1, best: null, basis: "正式成员与热股交集", ...over});
beforeEach(() => {
  scope.params = new URLSearchParams();
  vi.mocked(getThemesHot).mockResolvedValue(hotPayload([]));
  vi.mocked(getThemeStrength).mockResolvedValue({});
  vi.mocked(getSkyrocket).mockResolvedValue([]);
  vi.mocked(getAuctionBenchmark).mockResolvedValue([]);
});
afterEach(() => {cleanup(); vi.clearAllMocks();});

describe("题材合力连接正式代码", () => {
  it("按 exact_catalog.code 连接，名称相同或 catalog_code 线索都不能替代", async () => {
    vi.mocked(getThemes).mockResolvedValue(payload([
      {theme: "显示名变化", exact_catalog: {code: "885001.TI", source: "ths_official"}},
      {theme: "同名候选", exact_catalog: {code: "885002.TI", source: "ths_official"}},
      {theme: "只有成员线索", exact_catalog: null, catalog_code: "885001.TI"},
    ]));
    vi.mocked(getThemeStrength).mockResolvedValue({"885001.TI": row("885001.TI", "旧显示名", 120), "885003.TI": row("885003.TI", "同名候选", 180)});
    render(<ThemesTab />);
    await screen.findByTestId("显示名变化");
    await waitFor(() => expect(screen.getByTestId("显示名变化").textContent).toBe("885001.TI:120"));
    expect(screen.getByTestId("同名候选").textContent).toBe("无正式成分样本");
    expect(screen.getByTestId("只有成员线索").textContent).toBe("无正式成分样本");
  });

  it("记录键和行代码冲突时不展示错配样本", async () => {
    vi.mocked(getThemes).mockResolvedValue(payload([{theme: "代码冲突", exact_catalog: {code: "885001.TI", source: "ths_official"}}]));
    vi.mocked(getThemeStrength).mockResolvedValue({"885001.TI": row("885999.TI", "代码冲突", 200)});
    render(<ThemesTab />);
    expect((await screen.findByTestId("代码冲突")).textContent).toBe("无正式成分样本");
  });

  it("历史题材不接入当前成分样本", async () => {
    scope.params = new URLSearchParams({date: "2026-10-26"});
    vi.mocked(getThemes).mockResolvedValue(payload([{theme: "历史观察", exact_catalog: {code: "885001.TI", source: "ths_official"}}]));
    vi.mocked(getThemeStrength).mockResolvedValue({"885001.TI": row("885001.TI", "历史观察", 200)});
    render(<ThemesTab />);
    expect((await screen.findByTestId("历史观察")).textContent).toBe("无正式成分样本");
    expect(getThemeStrength).not.toHaveBeenCalled();
  });
});


describe("题材人气只连接同源正式代码", () => {
  it("同名不同码和无正式身份的原因簇不贴目录人气，同码不同展示名仍合法", async () => {
    vi.mocked(getThemes).mockResolvedValue(payload([
      {theme: "同名原因", exact_catalog: {code: "885002.TI", source: "ths_official"}},
      {theme: "未映射原因", exact_catalog: null, catalog_code: "885003.TI"},
      {theme: "不同展示名", exact_catalog: {code: "885001.TI", source: "ths_official"}},
    ]));
    vi.mocked(getThemesHot).mockResolvedValue(hotPayload([hotRow(), hotRow({theme: "未映射原因", theme_code: "885003.TI"})]));
    render(<ThemesTab />);
    await screen.findByTestId("不同展示名");
    expect(screen.queryByTestId("同名原因-hot")).toBeNull();
    expect(screen.queryByTestId("未映射原因-hot")).toBeNull();
    expect(screen.getByTestId("不同展示名-hot").textContent).toBe("885001.TI:123");
  });

  it("同码但非同花顺目录身份或非官方人气来源不互贴", async () => {
    vi.mocked(getThemes).mockResolvedValue(payload([
      {theme: "外源卡片", exact_catalog: {code: "885001.TI", source: "eastmoney"}},
      {theme: "外源人气", exact_catalog: {code: "885002.TI", source: "ths_official"}},
      {theme: "空代码", exact_catalog: {code: "885003.TI", source: "ths_official"}},
    ]));
    vi.mocked(getThemesHot).mockResolvedValue(hotPayload([
      hotRow({theme: "外源卡片"}), {...hotRow({theme: "外源人气", theme_code: "885002.TI"}), source: "manual"} as unknown as HotTheme,
      hotRow({theme: "空代码", theme_code: null}),
    ]));
    render(<ThemesTab />);
    await screen.findByTestId("外源卡片");
    expect(screen.queryByTestId("外源卡片-hot")).toBeNull();
    expect(screen.queryByTestId("外源人气-hot")).toBeNull();
    expect(screen.queryByTestId("空代码-hot")).toBeNull();
  });
});


describe("合力只请求当前可见正式成员范围", () => {
  it("只请求可见卡片的去重排序代码，隐藏目录不请求", async () => {
    scope.params = new URLSearchParams({focus: "目标"});
    vi.mocked(getThemes).mockResolvedValue(payload([
      {theme: "目标乙", exact_catalog: {code: "885002.TI", source: "ths_official"}},
      {theme: "目标甲", exact_catalog: {code: "885001.TI", source: "ths_official"}},
      {theme: "目标重复甲", exact_catalog: {code: "885001.TI", source: "ths_official"}},
      {theme: "隐藏目录", exact_catalog: {code: "885999.TI", source: "ths_official"}},
      {theme: "目标无正式身份", exact_catalog: null},
      {theme: "目标外源", exact_catalog: {code: "BK1234", source: "eastmoney"}},
    ]));
    vi.mocked(getThemeStrength).mockImplementation(async codes => Object.fromEntries((codes ?? []).map(code => [code, row(code, "显示名称不参与连接", 20)])));
    render(<ThemesTab />);
    await waitFor(() => expect(getThemeStrength).toHaveBeenCalledTimes(1));
    expect(getThemeStrength).toHaveBeenCalledWith(["885001.TI", "885002.TI"], {signal: expect.any(AbortSignal)});
    await waitFor(() => expect(screen.getByTestId("目标甲").textContent).toBe("885001.TI:20"));
    expect(screen.queryByTestId("隐藏目录")).toBeNull();
    expect(screen.getByTestId("目标外源").textContent).toBe("无正式成分样本");
  });

  it.each([
    {themes: []},
    {themes: [{theme: "无正式身份", exact_catalog: null, catalog_code: "885001.TI"}]},
    {themes: [{theme: "外源身份", exact_catalog: {code: "BK1234", source: "eastmoney"}}]},
  ])("空或无同花顺正式代码的范围零请求（%#）", async ({themes}) => {
    vi.mocked(getThemes).mockResolvedValue(payload(themes));
    render(<ThemesTab />);
    await act(async () => {});
    expect(getThemes).toHaveBeenCalledTimes(1);
    expect(getThemeStrength).not.toHaveBeenCalled();
  });

  it("可见行顺序改变但代码组相同时保留同key，不重复读取", async () => {
    scope.params = new URLSearchParams({focus: "甲"});
    vi.mocked(getThemes).mockResolvedValue(payload([
      {theme: "甲先", exact_catalog: {code: "885002.TI", source: "ths_official"}},
      {theme: "甲后", exact_catalog: {code: "885001.TI", source: "ths_official"}},
      {theme: "乙先", exact_catalog: {code: "885001.TI", source: "ths_official"}},
      {theme: "乙后", exact_catalog: {code: "885002.TI", source: "ths_official"}},
    ]));
    const {rerender} = render(<ThemesTab />);
    await waitFor(() => expect(getThemeStrength).toHaveBeenCalledTimes(1));
    scope.params = new URLSearchParams({focus: "乙"});
    rerender(<ThemesTab />);
    await screen.findByTestId("乙先");
    expect(getThemeStrength).toHaveBeenCalledTimes(1);
    expect(getThemeStrength).toHaveBeenCalledWith(["885001.TI", "885002.TI"], {signal: expect.any(AbortSignal)});
  });

  it("切换可见范围取消旧读取，晚包不能覆盖新范围", async () => {
    scope.params = new URLSearchParams({focus: "甲"});
    vi.mocked(getThemes).mockResolvedValue(payload([
      {theme: "甲", exact_catalog: {code: "885001.TI", source: "ths_official"}},
      {theme: "乙", exact_catalog: {code: "885002.TI", source: "ths_official"}},
    ]));
    let finishOld!: (value: Record<string, ThemeStrengthRow>) => void;
    const old = new Promise<Record<string, ThemeStrengthRow>>(resolve => {finishOld = resolve;});
    vi.mocked(getThemeStrength).mockImplementationOnce(() => old).mockResolvedValueOnce({"885002.TI": row("885002.TI", "乙", 22)});
    const {rerender} = render(<ThemesTab />);
    await waitFor(() => expect(getThemeStrength).toHaveBeenCalledTimes(1));
    const signal = vi.mocked(getThemeStrength).mock.calls[0][1]?.signal;
    expect(signal).toBeDefined();
    scope.params = new URLSearchParams({focus: "乙"});
    rerender(<ThemesTab />);
    await waitFor(() => expect(getThemeStrength).toHaveBeenCalledTimes(2));
    expect(signal!.aborted).toBe(true);
    expect(vi.mocked(getThemeStrength).mock.calls[1][0]).toEqual(["885002.TI"]);
    await waitFor(() => expect(screen.getByTestId("乙").textContent).toBe("885002.TI:22"));
    await act(async () => {finishOld({"885001.TI": row("885001.TI", "甲", 999)}); await old;});
    expect(screen.getByTestId("乙").textContent).toBe("885002.TI:22");
  });
});


it("正式行业及缺冻结凭据的概念不误调用概念合力",async()=>{
  vi.mocked(getThemes).mockResolvedValue(payload([
    {theme:"汽车零部件",exact_catalog:{code:"881126.TI",source:"ths_official",kind:"industry"}},
    {theme:"凭据缺失",exact_catalog:{code:"885001.TI",source:"ths_official",catalog_version:null}},
    {theme:"概念原名",exact_catalog:{code:"885002.TI",source:"ths_official"}},
  ]));
  render(<ThemesTab/>);
  await waitFor(()=>expect(getThemeStrength).toHaveBeenCalledTimes(1));
  expect(getThemeStrength).toHaveBeenCalledWith(["885002.TI"],{signal:expect.any(AbortSignal)});
  expect(screen.getByTestId("汽车零部件").textContent).toBe("无正式成分样本");
});


it("历史市场旁列当前目录sidecar但不请求当前概念合力或人气",async()=>{
  scope.params=new URLSearchParams({date:"2026-10-23"});
  const value=payload([{theme:"汽车零部件",exact_catalog:null,catalog_mapping_state:"unavailable",catalog_mapping_context:{reason:"historical_identity_not_frozen"}}]);
  value.trade_date="2026-10-23";value.current_theme_catalog={scope:"current_directory_reference",catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00",items:[{theme:"汽车零部件",catalog_mapping_state:"exact",exact_catalog:{code:"881126.TI",name:"汽车零部件",source:"ths_official",basis:"exact_name",kind:"industry",catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00"}}]};
  vi.mocked(getThemes).mockResolvedValue(value);
  render(<ThemesTab/>);
  expect((await screen.findByTestId("汽车零部件-current-reference")).textContent).toBe("current_directory_reference");
  expect(getThemeStrength).not.toHaveBeenCalled();expect(getThemesHot).not.toHaveBeenCalled();
});

it("慢目录完成只更新同请求旁列，不重读题材或触发新的成分合力",async()=>{
  scope.params=new URLSearchParams({date:"2026-10-23"});
  const key="d".repeat(32),version="a".repeat(64),clock="2026-10-28T00:00:00+00:00";
  const value=payload([{theme:"汽车零部件",exact_catalog:null,catalog_mapping_state:"unavailable"}]);
  value.current_theme_catalog={scope:"current_directory_reference",catalog_version:null,observed_at:null,completion_key:key,items:[{theme:"汽车零部件",catalog_mapping_state:"unavailable",catalog_mapping_context:{reason:"catalog_read_pending",completion_key:key}}]};
  const before=JSON.stringify(value);vi.mocked(getThemes).mockResolvedValue(value);
  let finish!:(result:Awaited<ReturnType<typeof getThemeCatalogCompletion>>)=>void;
  vi.mocked(getThemeCatalogCompletion).mockReturnValueOnce(new Promise(resolve=>{finish=resolve;}));
  render(<ThemesTab/>);
  await waitFor(()=>expect(getThemeCatalogCompletion).toHaveBeenCalledTimes(1));
  expect(screen.getByTestId("汽车零部件-current-state").textContent).toBe("unavailable");
  await act(async()=>finish({scope:"current_directory_resolution",completion_key:key,state:"available",catalog_version:version,observed_at:clock,identities:{汽车零部件:{code:"881126.TI",name:"汽车零部件",source:"ths_official",basis:"exact_name",kind:"industry",catalog_version:version,observed_at:clock}},ambiguous:[],reason:null}));
  expect(screen.getByTestId("汽车零部件-current-state").textContent).toBe("exact");
  expect(getThemes).toHaveBeenCalledTimes(1);expect(getThemeStrength).not.toHaveBeenCalled();expect(getThemesHot).not.toHaveBeenCalled();
  expect(JSON.stringify(value)).toBe(before);
});
