import { useState, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { BoardFundFlowPayload, BoardFlowRow, DiscoveryMemberships, OfficialCatalogKind, ThemesHotPayload } from "@/lib/api";

const {jsonMock} = vi.hoisted(() => ({jsonMock: vi.fn()}));
vi.mock("@/lib/api/internal", async () => ({
  ...await vi.importActual<typeof import("@/lib/api/internal")>("@/lib/api/internal"),
  getJson: (...args: unknown[]) => jsonMock(...args),
}));
vi.mock("@/components/stock-link", () => ({
  StockLink: ({symbol, children}: {symbol: string; children: ReactNode}) => <a href={`/workbench?symbol=${symbol}`}>{children}</a>,
}));

import { DiscoveryBrowser, DiscoveryNavigation, discoveryLocation, type DiscoverySelection } from "./discovery-browser";

const targetMock = vi.fn();
const navigateMock = vi.fn();
const emptySelection: DiscoverySelection = {date:"2026-10-23",loaded:true,entries:[]};
const selected: DiscoverySelection = {date:"2026-10-23",loaded:true,entries:[{symbol:"600519",name:"原入选公司",open:vi.fn()}]};
const farRect = {top:5000,bottom:5001,left:0,right:0,width:0,height:1,x:0,y:5000,toJSON:() => ({})} as DOMRect;

function hotPayload(): ThemesHotPayload {
  // Formal names/codes are source-verified anchors; all memberships/values below are isolated fixtures.
  return {ts:"2026-10-25T09:50:00+08:00",themes:[
    {theme:"机器人概念",theme_code:"885517.TI",heat:700,heat_complete:true,member_count:20,member_as_of:"2026-10-25T08:00:00+08:00",hot_symbols:["600519"],concentration_pct:5,source:"ths_official",hot_count:1,best:{symbol:"600519",name:"原入选公司",rank:7,rank_change:1},basis:"官方成分反查"},
    {theme:"人形机器人",theme_code:"886069.TI",heat:100,heat_complete:true,member_count:50,member_as_of:"2026-10-25T08:00:00+08:00",hot_symbols:["000001"],concentration_pct:2,source:"ths_official",hot_count:1,best:null,basis:"官方成分反查"},
  ],stocks:[
    {symbol:"600519",name:"原入选公司",rank:7,heat:700,rank_change:1,themes:["机器人概念"]},
    {symbol:"000001",name:"另一热股",rank:10,heat:100,rank_change:null,themes:["人形机器人"]},
    {symbol:"600000",name:"未归属热股",rank:12,heat:450,rank_change:null,themes:[]},
  ],ranking_basis:"hot_stock_concentration",catalog_revision:1,catalog_state:"ready",membership_state:"ready",scope:"hot_stock_sample"};
}

function officialCatalog(kind:OfficialCatalogKind) {
  const anchors=kind==="industry"?[["881101.TI","种植业与林业"],["884001.TI","种子生产"],["884002.TI","粮食种植"]]:[["885517.TI","机器人概念"],["886069.TI","人形机器人"]];
  return {kind,source:"ths_official",synced_at:null,total:anchors.length,items:anchors.map(([code,name])=>({code,name,kind,source:"ths_official",synced_at:null}))};
}

function board(overrides: Partial<BoardFlowRow> = {}): BoardFlowRow {
  return {board_code:"BK0001",name:"默认板块",kind:"concept",change_pct:1,main_net_yi:1,main_net_ratio:null,
    super_net_yi:null,main_net_5d_yi:null,main_net_10d_yi:null,main_net_20d_yi:null,
    leader_name:null,leader_symbol:null,streak:null,rank:1,rank_delta:null,...overrides};
}
function flow(rows: BoardFlowRow[], kind: BoardFundFlowPayload["kind"] = "concept", range: BoardFundFlowPayload["range"] = "intraday"): BoardFundFlowPayload {
  return {available:true,kind,range,rows,degraded:[],received_at:"2026-10-25T09:50:01+08:00",source_as_of:null,
    historical:range==="20d" || range==="latest_saved",as_of:range==="20d" || range==="latest_saved"?"2026-10-23":null,
    ...(range==="latest_saved"?{received_at:null,stored_at:"2026-10-23T15:10:00+08:00",coverage_state:"local_sample" as const,quality:"stale" as const}:{}),
  };
}
function memberships(symbols: string[], date: string | null, state: "matched" | "unknown" = "matched"): DiscoveryMemberships {
  return {scope:"current_not_historical",requested_trade_date:date,content_version:"a".repeat(64),state:state==="matched"?"ready":"partial",
    source:"ths_official",source_as_of:null,observed_at:"2026-10-25T09:50:01+08:00",count:symbols.length,
    matched_count:state==="matched"?symbols.length:0,unknown_count:state==="unknown"?symbols.length:0,
    items:symbols.map(symbol => ({symbol,state,themes:state==="matched"?[{theme_code:"885517.TI",theme_name:"机器人概念",source:"ths_official"}]:[],reason:state==="unknown"?"成分尚未同步":null}))};
}
function route(url: string) {
  const query = new URL(url,"http://localhost");
  if (query.pathname === "/api/themes/hot") return {data:hotPayload()};
  if (query.pathname === "/api/themes/catalog") return {data:officialCatalog(query.searchParams.get("kind")==="industry"?"industry":"concept")};
  if (query.pathname === "/api/themes/catalog/quotes") {
    const codes=(query.searchParams.get("codes")??"").split(",");
    return {data:{kind:query.searchParams.get("kind"),source:"ths_official",source_basis:"index_snapshot",source_as_of:null,source_ready_at:null,received_at:"2026-10-25T09:50:01+08:00",available:true,rows:codes.map(code=>({code,change_pct:null,price:null})),missing_codes:[]}};
  }
  if (query.pathname === "/api/market/board-fund-flow") {
    const kind = query.searchParams.get("kind") as BoardFundFlowPayload["kind"];
    const range = query.searchParams.get("range") as BoardFundFlowPayload["range"];
    return {data:flow([board({kind})],kind,range)};
  }
  if (query.pathname === "/api/picks/discovery-memberships") return {data:memberships((query.searchParams.get("symbols") || "").split(",").filter(Boolean),query.searchParams.get("trade_date"))};
  if (query.pathname === "/api/picks/strategy-registry") return {data:{strategies:[]}};
  throw new Error(`Unexpected read: ${query.pathname}`);
}
function Browser({search="browse=hot",selection=emptySelection}: {search?: string; selection?: DiscoverySelection}) {
  return <DiscoveryBrowser search={search} selection={selection} onNavigate={navigateMock} onTarget={targetMock}/>;
}
function Harness({initial, selection=emptySelection}: {initial: string; selection?: DiscoverySelection}) {
  const [search,setSearch] = useState(initial);
  return <DiscoveryBrowser search={search} selection={selection} onTarget={targetMock} onNavigate={(href,push) => {
    navigateMock(href,push);setSearch(new URL(href,"http://localhost").search);
  }}/>;
}
function resultRows() {return within(screen.getByRole("list",{name:"分类结果"})).getAllByRole("listitem");}
function resultNames() {return resultRows().map(row => row.querySelector("strong")!.textContent);}

beforeEach(() => {
  jsonMock.mockReset();navigateMock.mockReset();targetMock.mockReset();
  jsonMock.mockImplementation(async (url: string) => route(url));
  vi.spyOn(HTMLElement.prototype,"getBoundingClientRect").mockReturnValue(farRect);
});
afterEach(() => {cleanup();vi.restoreAllMocks();});

describe("真实分类发现的来源与范围", () => {
  it("an explicit historical date wins over the cached latest selection when opening a method", async () => {
    jsonMock.mockResolvedValue({data:{strategies:[{key:"daily_picks",name:"每日精选",status:"active"}]}});
    render(<Browser search="browse=methods&date=2026-10-22" selection={selected}/>);
    const link=await screen.findByRole("link",{name:/查看核验与反例/});
    expect(new URL(link.getAttribute("href")!,"http://localhost").searchParams.get("date")).toBe("2026-10-22");
    expect(jsonMock.mock.calls.map(call=>call[0])).toEqual(["/api/picks/strategy-registry?include_candidates=true"]);
  });
  it("方法目录没有显式日期时不被原入选缓存日期固定",async()=>{
    jsonMock.mockResolvedValue({data:{strategies:[{key:"daily_picks",name:"每日精选",status:"active"}]}});
    render(<Browser search="browse=methods" selection={selected}/>);
    const link=await screen.findByRole("link",{name:/查看核验与反例/});
    expect(new URL(link.getAttribute("href")!,"http://localhost").searchParams.has("date")).toBe(false);
    expect(jsonMock).toHaveBeenCalledTimes(1);
  });
  it("方法选择回报精确日期与disc_method，仍只读取一次可选目录",async()=>{
    const row={key:"daily_picks",name:"每日精选",status:"active"},methodTarget=vi.fn();
    jsonMock.mockResolvedValue({data:{strategies:[row]}});
    render(<DiscoveryBrowser search="browse=methods&disc_method=daily_picks&method=intraday_watch&run_id=old" selection={selected}
      methodDate="2026-10-22" onNavigate={navigateMock} onTarget={targetMock} onMethodTarget={methodTarget}/>);
    await screen.findByRole("link",{name:"查看方法规则：每日精选"});
    expect(methodTarget.mock.lastCall).toEqual([JSON.stringify(["methods","2026-10-22","daily_picks"]),row]);
    expect(jsonMock.mock.calls.map(([url])=>url)).toEqual(["/api/picks/strategy-registry?include_candidates=true"]);
  });
  it("巨大人气不能替代集中度或成为全市场主线结论", async () => {
    const data=hotPayload();data.themes[0].heat=51040600;data.stocks[0].heat=51040600;data.themes[0].concentration_pct=1;data.themes[0].member_count=100;
    jsonMock.mockResolvedValue({data});
    render(<Browser/>);
    await screen.findByRole("button",{name:"查看机器人概念成分"});
    expect(resultNames()).toEqual(["人形机器人","机器人概念"]);
    expect(within(resultRows()[1]).getByText("1%")).toBeTruthy();
    expect(screen.getByText(/本系统排序，不是来源官方热榜或全市场最强主线/)).toBeTruthy();
    expect(screen.getByText(/榜单时刻/).textContent).toContain("09:50（北京）");
  });
  it("没有涨停梯队也可发现热门题材，归属未核热股仍能读取", async () => {
    render(<Browser/>);
    expect(await screen.findByRole("button",{name:"查看机器人概念成分"})).toBeTruthy();
    expect(screen.getByText("同花顺正式概念 · 按热股集中度观察")).toBeTruthy();
    expect(screen.getByText(/榜单时刻/).textContent).toContain("09:50（北京）");
    const unmapped = screen.getByText("热榜中归属未核的股票").closest("details")!;
    fireEvent.click(within(unmapped).getByText("热榜中归属未核的股票"));
    expect(within(unmapped).getByRole("link",{name:"未归属热股"}).getAttribute("href")).toContain("symbol=600000");
    expect(jsonMock.mock.calls.map(call => call[0])).toEqual(["/api/themes/hot?limit=30"]);
    expect(selected.entries[0].open).not.toHaveBeenCalled();
  });

  it("人气缺失和成分总数未知不补集中度0，未知条目沉底但保持可读", async () => {
    const data=hotPayload();
    data.themes[0].heat=null;data.themes[0].heat_complete=false;data.stocks[0].heat=null;
    data.themes[0].member_count=null;data.themes[0].concentration_pct=null;
    jsonMock.mockResolvedValue({data});
    render(<Browser/>);
    const target=await screen.findByRole("button",{name:"查看机器人概念成分"});
    expect(within(target).getByText("未核")).toBeTruthy();
    expect(resultNames()).toEqual(["人形机器人","机器人概念"]);
    expect(within(target.closest("li")!).getByText(/成分总数未核/)).toBeTruthy();
    expect(within(target).queryByText("0%")).toBeNull();
    expect(within(target).queryByText("榜内人气合计")).toBeNull();
  });

  it("每种激活视角只读对应来源，切走取消上游请求，不启动策略", async () => {
    let finishHot!: (value: unknown) => void, finishCatalog!: (value: unknown) => void;
    jsonMock.mockImplementation((url:string) => {
      if(url.includes("themes/hot"))return new Promise(resolve => {finishHot=resolve;});
      if(url.startsWith("/api/themes/catalog?"))return new Promise(resolve => {finishCatalog=resolve;});
      return Promise.resolve(route(url));
    });
    const page = render(<Browser search="browse=hot"/>);
    await waitFor(() => expect(jsonMock).toHaveBeenCalledTimes(1));
    const hotSignal = jsonMock.mock.calls[0][2].signal as AbortSignal;
    page.rerender(<Browser search="browse=catalog&disc_kind=industry&disc_range=20d"/>);
    await waitFor(() => expect(jsonMock).toHaveBeenCalledTimes(2));
    expect(hotSignal.aborted).toBe(true);
    expect(jsonMock.mock.calls[1][0]).toBe("/api/themes/catalog?kind=industry");
    const catalogSignal = jsonMock.mock.calls[1][2].signal as AbortSignal;
    page.rerender(<Browser search="browse=methods"/>);
    await screen.findByText("暂无已登记方法。");
    expect(catalogSignal.aborted).toBe(true);
    expect(jsonMock.mock.calls.map(call => call[0])).toEqual([
      "/api/themes/hot?limit=30","/api/themes/catalog?kind=industry","/api/picks/strategy-registry?include_candidates=true",
    ]);
    await act(async () => {
      finishHot({data:hotPayload()});
      finishCatalog({data:officialCatalog("industry")});
    });
    expect(screen.queryByRole("button",{name:"查看机器人概念成分"})).toBeNull();
  });

  it("行业概念基础目录不等可选快照，也不依赖失败的东财资金榜",async()=>{
    let finish!:(value:unknown)=>void;
    jsonMock.mockImplementation((url:string)=>{
      if(url.includes("board-fund-flow"))return Promise.reject(new Error("eastmoney unavailable"));
      if(url.includes("/catalog/quotes?"))return new Promise(resolve=>{finish=resolve;});
      return Promise.resolve(route(url));
    });
    render(<Harness initial="browse=catalog&disc_kind=concept&date=2026-10-22&sec=daily&selection=600519"/>);
    const button=await screen.findByRole("button",{name:"查看机器人概念成分"});
    expect(screen.getByRole("button",{name:"查看人形机器人成分"})).toBeTruthy();
    await waitFor(()=>expect(jsonMock).toHaveBeenCalledTimes(2));
    expect(button).toHaveProperty("disabled",false);fireEvent.click(button);
    expect(targetMock.mock.lastCall).toEqual([JSON.stringify(["catalog","concept","intraday","885517.TI"]),{kind:"theme",code:"885517.TI",name:"机器人概念",catalogKind:"concept"}]);
    expect(jsonMock.mock.calls.some(([url])=>String(url).includes("board-fund-flow"))).toBe(false);
    expect(screen.getByText("报价正在补充，可先浏览目录。")).toBeTruthy();
    const call=jsonMock.mock.calls.find(([url])=>String(url).includes("/catalog/quotes?"))!;
    await act(async()=>finish(route(String(call[0]))));
    expect(screen.getByRole("list",{name:"官方目录结果"}).textContent).not.toContain("0.00%");
  });

  it("只收拢热股样本重合，保留三个正式名称代码及独立下钻，可恢复完整列表",async()=>{
    const data=hotPayload(),symbols=["600519","000001","600000","300024"];
    data.stocks=symbols.map((symbol,index)=>({symbol,name:`隔离热股${index}`,rank:index+1,heat:100,rank_change:null,themes:index<3?["机器人概念","人形机器人","机器视觉"]:["机器视觉"]}));
    data.themes=[
      {...data.themes[0],hot_count:3,hot_symbols:symbols.slice(0,3),member_count:60,concentration_pct:5,heat:300,best:null},
      {...data.themes[1],hot_count:3,hot_symbols:symbols.slice(0,3),member_count:100,concentration_pct:3,heat:300},
      {...data.themes[1],theme:"机器视觉",theme_code:"886002.TI",hot_count:4,hot_symbols:symbols,member_count:200,concentration_pct:2,heat:400},
    ];
    jsonMock.mockResolvedValue({data});render(<Harness initial="browse=hot"/>);
    await screen.findByRole("button",{name:"查看机器人概念成分"});
    expect(resultRows()).toHaveLength(1);
    expect(screen.getByRole("button",{name:"查看人形机器人成分"}).textContent).toContain("886069.TI");
    expect(screen.getByRole("button",{name:"查看机器视觉成分"}).textContent).toContain("886002.TI");
    expect(screen.getByText(/各名称、代码和成分独立保留，不合并为新题材/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"查看机器视觉成分"}));
    expect(targetMock.mock.lastCall?.[1]).toEqual({kind:"theme",code:"886002.TI",name:"机器视觉"});
    fireEvent.click(screen.getByRole("button",{name:"重合热股已收拢"}));
    expect(resultNames()).toEqual(["机器人概念","人形机器人","机器视觉"]);
    expect(new URL(navigateMock.mock.lastCall![0],"http://localhost").searchParams.get("disc_group")).toBe("all");
    expect(jsonMock).toHaveBeenCalledTimes(1);
  });

  it.each(["source","code","hot_symbols"])("正式热榜%s身份不完整时不展示或替换成别的题材",async issue=>{
    const data=hotPayload();
    if(issue==="source")(data.themes[0] as unknown as {source:string}).source="manual";
    if(issue==="code")data.themes[0].theme_code="机器人概念";
    if(issue==="hot_symbols")data.themes[0].hot_symbols=["600519","000001"];
    jsonMock.mockResolvedValue({data});render(<Browser search="browse=hot&disc_code=885517.TI"/>);
    expect((await screen.findByRole("alert")).textContent).toContain("分类返回身份或结构不一致");
    expect(screen.queryByRole("button",{name:"查看机器人概念成分"})).toBeNull();
    expect(targetMock.mock.calls.every(([,value])=>value===null)).toBe(true);
  });

  it.each([
    ["industry","5d"], ["concept","10d"],
  ] as const)("请求concept/5d却收到%s/%s时禁止显示和下钻", async (kind,range) => {
    jsonMock.mockResolvedValue({data:flow([board({name:"错误身份板块",kind})],kind,range)});
    render(<Browser search="browse=funds&disc_kind=concept&disc_range=5d&disc_code=BK0001"/>);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent",expect.stringContaining("身份或结构不一致"));
    expect(screen.queryByRole("button",{name:"查看错误身份板块成分"})).toBeNull();
    expect(targetMock.mock.calls.every(([,target]) => target===null)).toBe(true);
  });

  it("整包种类正确但行属于另一种类时也不串入当前概念", async () => {
    jsonMock.mockResolvedValue({data:flow([board({name:"夹入的行业行",kind:"industry"})],"concept","5d")});
    render(<Browser search="browse=funds&disc_kind=concept&disc_range=5d&disc_code=BK0001"/>);
    await screen.findByRole("alert");
    expect(screen.queryByRole("button",{name:"查看夹入的行业行成分"})).toBeNull();
    expect(targetMock.mock.calls.every(([,target]) => target===null)).toBe(true);
  });

  it("换种类/区间后旧回包不能覆盖新scope的板块", async () => {
    let finishOld!: (value: unknown) => void;
    jsonMock.mockImplementationOnce(() => new Promise(resolve => {finishOld=resolve;}))
      .mockResolvedValueOnce({data:flow([board({name:"新行业存档",kind:"industry"})],"industry","20d")});
    const page = render(<Browser search="browse=funds&disc_kind=concept&disc_range=5d&disc_code=BK0001"/>);
    await waitFor(() => expect(jsonMock).toHaveBeenCalledTimes(1));
    const signal = jsonMock.mock.calls[0][2].signal as AbortSignal;
    page.rerender(<Browser search="browse=funds&disc_kind=industry&disc_range=20d&disc_code=BK0001"/>);
    await screen.findByRole("button",{name:"查看新行业存档成分"});
    expect(signal.aborted).toBe(true);
    await act(async () => finishOld({data:flow([board({name:"旧概念"})],"concept","5d")}));
    expect(screen.queryByText("旧概念")).toBeNull();
    expect(targetMock.mock.lastCall).toEqual([JSON.stringify(["funds","industry","20d","BK0001"]),{kind:"board",code:"BK0001",name:"新行业存档"}]);
  });

  it("真实净流入/流出分向排序，缺值沉底且不冒充0；排序不再发来源请求", async () => {
    const rows = [board({board_code:"BK1",name:"未知净额",main_net_yi:null,change_pct:null}),
      board({board_code:"BK2",name:"大额流出",main_net_yi:-10}),board({board_code:"BK3",name:"真实零额",main_net_yi:0}),
      board({board_code:"BK4",name:"正向流入",main_net_yi:5})];
    jsonMock.mockResolvedValue({data:flow(rows)});
    render(<Harness initial="browse=funds&disc_kind=concept"/>);
    await screen.findByRole("button",{name:"查看正向流入成分"});
    expect(resultNames()).toEqual(["正向流入","真实零额","大额流出","未知净额"]);
    const unknown = resultRows()[3];
    expect(within(unknown).getByText("未提供")).toBeTruthy();
    expect(unknown.textContent).not.toContain("0亿");
    expect(resultRows()[1].textContent).toContain("0亿");
    fireEvent.change(screen.getByRole("combobox",{name:"分类排序"}),{target:{value:"asc"}});
    expect(resultNames()).toEqual(["大额流出","真实零额","正向流入","未知净额"]);
    expect(jsonMock).toHaveBeenCalledTimes(1);
    expect(rows.map(row => row.name)).toEqual(["未知净额","大额流出","真实零额","正向流入"]);
  });

  it("中文组合输入未完成不提交URL或提前过滤；完成后仅在本地筛选", async () => {
    render(<Harness initial="browse=hot&disc_code=886069.TI&selection=daily&date=2026-10-23&account=paper"/>);
    await screen.findByRole("button",{name:"查看机器人概念成分"});
    const input = screen.getByRole("textbox",{name:"搜索分类"});
    fireEvent.compositionStart(input);fireEvent.change(input,{target:{value:"机器人概念"}});
    expect(navigateMock).not.toHaveBeenCalled();
    expect(resultRows()).toHaveLength(2);
    fireEvent.compositionEnd(input,{data:"机器人概念"});
    expect(resultRows()).toHaveLength(1);
    const query = new URL(navigateMock.mock.lastCall![0],"http://localhost").searchParams;
    expect(query.get("disc_q")).toBe("机器人概念");
    expect(query.get("date")).toBe("2026-10-23");expect(query.get("account")).toBe("paper");
    expect(query.has("disc_code")).toBe(false);expect(query.has("selection")).toBe(false);
    expect(jsonMock).toHaveBeenCalledTimes(1);
  });

  it("选择行进入相应来源scope，清原名单选中但保留日期/账户/返回位置", async () => {
    const original="browse=hot&selection=daily&date=2026-10-23&account=paper&symbol=600519&from=%2Fmarket";
    render(<Harness initial={original}/>);
    await screen.findByRole("button",{name:"查看机器人概念成分"});
    fireEvent.click(screen.getByRole("button",{name:"查看机器人概念成分"}));
    const query = new URL(navigateMock.mock.lastCall![0],"http://localhost").searchParams;
    expect(navigateMock.mock.lastCall![1]).toBe(true);
    expect(query.get("disc_code")).toBe("885517.TI");expect(query.get("selection")).toBeNull();
    expect(query.get("account")).toBe("paper");expect(query.get("symbol")).toBe("600519");
    expect(query.get("from")).toBe("/market");expect(query.get("date")).toBe("2026-10-23");
    expect(targetMock.mock.lastCall).toEqual([JSON.stringify(["hot","concept","intraday","885517.TI"]),{kind:"theme",code:"885517.TI",name:"机器人概念"}]);
    expect(selected.entries[0].open).not.toHaveBeenCalled();
  });

  it("超过24条可连续下拉到全部，换筛选回到首批且不重取来源", async () => {
    const rows=Array.from({length:61},(_,i) => board({board_code:`BK${i}`,name:`方向${i}`,main_net_yi:61-i}));
    jsonMock.mockResolvedValue({data:flow(rows)});
    const page=render(<Harness initial="browse=funds"/>);
    await screen.findByRole("button",{name:"查看方向0成分"});
    expect(resultRows()).toHaveLength(24);
    const sentinel=page.container.querySelector<HTMLElement>(".h-px")!;
    vi.spyOn(sentinel,"getBoundingClientRect").mockReturnValue({...farRect,top:100,bottom:101} as DOMRect);
    fireEvent.scroll(window);expect(resultRows()).toHaveLength(48);
    fireEvent.scroll(window);expect(resultRows()).toHaveLength(61);
    expect(screen.getByText("已全部加载（61 个分类）")).toBeTruthy();
    fireEvent.change(screen.getByRole("combobox",{name:"分类排序"}),{target:{value:"asc"}});
    // The new list may fill one viewport immediately; all entries remain reachable.
    expect(resultRows().length).toBeLessThan(61);
    expect(resultNames()[0]).toBe("方向60");
    expect(jsonMock).toHaveBeenCalledTimes(1);
  });
});

describe("行业概念的显式最近存档", () => {
  it("基础目录失败不偷偷改资金源，旧存档URL仍独立可读，返回后恢复正式目录", async () => {
    let currentAvailable=false;
    jsonMock.mockImplementation(async(url:string)=>{
      const parsed=new URL(url,"http://localhost");
      if(parsed.pathname==="/api/themes/catalog" && !currentAvailable)throw new Error("catalog offline");
      if(parsed.pathname==="/api/market/board-fund-flow")return {data:flow([board({name:"原行业样本",kind:"industry"})],"industry","latest_saved")};
      return route(url);
    });
    const base="browse=catalog&date=2026-10-22&sec=daily&selection=600519";
    const page=render(<Browser search={base}/>);
    expect((await screen.findByRole("alert")).textContent).toContain("官方目录读取失败");
    expect(jsonMock.mock.calls.map(([url])=>url)).toEqual(["/api/themes/catalog?kind=industry"]);
    expect(screen.queryByText("原行业样本")).toBeNull();
    expect(screen.queryByRole("button",{name:"查看最近存档"})).toBeNull();
    // The old explicit archive link remains historical; a failed directory never selects it automatically.
    page.rerender(<Browser search={`${base}&disc_range=latest_saved`}/>);
    await screen.findByRole("button",{name:"查看原行业样本成分"});
    expect(navigateMock).not.toHaveBeenCalled();
    expect(screen.getByText("原榜单日期 2026-10-23")).toBeTruthy();
    expect(screen.getByText("Top50净流入存档样本，非全目录")).toBeTruthy();
    expect(screen.getByText(/历史参考，不表示即时源已恢复/)).toBeTruthy();
    expect(screen.getByText(/成分下钻另读当前样本，不是原日归属/)).toBeTruthy();
    expect(screen.getByText(/存档保存于/).textContent).toContain("15:10（北京） · 来源时刻未核");
    expect(screen.queryByText(/源读取完成/)).toBeNull();
    expect(screen.getByText("原日涨跌幅")).toBeTruthy();expect(screen.getByText("原当日净额")).toBeTruthy();
    currentAvailable=true;
    fireEvent.change(screen.getByRole("combobox",{name:"目录范围"}),{target:{value:"intraday"}});
    const next=new URL(navigateMock.mock.lastCall![0],"http://localhost");
    expect(next.searchParams.get("date")).toBe("2026-10-22");expect(next.searchParams.get("sec")).toBe("daily");expect(next.searchParams.get("selection")).toBe("600519");
    expect(next.searchParams.has("disc_range")).toBe(false);
    page.rerender(<Browser search={next.search}/>);
    await screen.findByRole("button",{name:"查看种植业与林业成分"});
    expect(screen.queryByText("Top50净流入存档样本，非全目录")).toBeNull();
    expect(jsonMock.mock.calls.filter(([url])=>String(url).includes("board-fund-flow")).map(([url])=>url)).toEqual(["/api/market/board-fund-flow?kind=industry&range=latest_saved"]);
    expect(jsonMock.mock.calls.filter(([url])=>String(url).startsWith("/api/themes/catalog?")).map(([url])=>url)).toEqual(Array(2).fill("/api/themes/catalog?kind=industry"));
    expect(selected.entries[0].open).not.toHaveBeenCalled();
  });

  it("存档只使用原当日净额和涨跌幅，未知不补0，排序只改变有限样本且BK下钻不冒充历史",async()=>{
    jsonMock.mockResolvedValue({data:flow([
      board({board_code:"BK3",name:"原未知",change_pct:null,main_net_yi:null,main_net_20d_yi:999}),
      board({board_code:"BK2",name:"原零",change_pct:0,main_net_yi:0}),
      board({board_code:"BK1",name:"原涨幅",change_pct:2,main_net_yi:3,main_net_20d_yi:999}),
    ],"concept","latest_saved")});
    render(<Harness initial="browse=catalog&disc_kind=concept&disc_range=latest_saved"/>);
    await screen.findByRole("button",{name:"查看原涨幅成分"});
    expect(resultNames()).toEqual(["原涨幅","原零","原未知"]);
    expect(resultRows()[0].textContent).toContain("+3亿");expect(resultRows()[0].textContent).not.toContain("999");
    expect(resultRows()[1].textContent).toContain("0亿");expect(within(resultRows()[2]).getByText("未提供")).toBeTruthy();
    expect(resultRows()[2].textContent).not.toContain("0亿");
    fireEvent.change(screen.getByRole("combobox",{name:"分类排序"}),{target:{value:"asc"}});
    expect(resultNames()).toEqual(["原零","原涨幅","原未知"]);
    fireEvent.click(screen.getByRole("button",{name:"查看原涨幅成分"}));
    expect(targetMock.mock.lastCall).toEqual([JSON.stringify(["catalog","concept","latest_saved","BK1"]),{kind:"board",code:"BK1",name:"原涨幅"}]);
    expect(jsonMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button",{name:/研究|买入|模拟/})).toBeNull();
  });

  it.each([
    {kind:"industry"}, {range:"20d"}, {historical:false}, {as_of:"2026-02-30"}, {as_of:null}, {coverage_state:"unknown"}, {quality:"medium"},
  ])("拒绝存档身份、日期或范围语义漂移 %j",async patch=>{
    jsonMock.mockResolvedValue({data:{...flow([board({name:"不可信归档"})],"concept","latest_saved"),...patch}});
    render(<Browser search="browse=catalog&disc_kind=concept&disc_range=latest_saved&disc_code=BK0001"/>);
    expect((await screen.findByRole("alert")).textContent).toContain("身份或结构不一致");
    expect(screen.queryByRole("button",{name:"查看不可信归档成分"})).toBeNull();
    expect(targetMock.mock.calls.every(([,target])=>target===null)).toBe(true);
  });

  it("空存档和不可用存档保留真实状态，不另拉本次榜单或20日数据",async()=>{
    jsonMock.mockResolvedValueOnce({data:flow([],"industry","latest_saved")})
      .mockResolvedValueOnce({data:{...flow([board({name:"不可用附带行",kind:"industry"})],"industry","latest_saved"),available:false,reason:"没有可用日归档"}});
    render(<Browser search="browse=catalog&disc_range=latest_saved"/>);
    expect(await screen.findByText("本次范围没有可展示板块；缺数不按零处理。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"刷新分类"}));
    expect((await screen.findByRole("alert")).textContent).toContain("没有可用日归档");
    expect(screen.queryByRole("button",{name:"查看不可用附带行成分"})).toBeNull();
    expect(jsonMock.mock.calls.map(([url])=>url)).toEqual(Array(2).fill("/api/market/board-fund-flow?kind=industry&range=latest_saved"));
  });

  it("新URL只在行业概念生效，旧目录20日链接仍回本次、资金范围不增项",async()=>{
    expect(discoveryLocation("browse=catalog&disc_range=latest_saved").range).toBe("latest_saved");
    expect(discoveryLocation("browse=catalog&disc_range=20d").range).toBe("intraday");
    expect(discoveryLocation("browse=funds&disc_range=latest_saved").range).toBe("intraday");
    expect(discoveryLocation("browse=hot&disc_range=latest_saved").range).toBe("intraday");
    render(<Browser search="browse=funds&disc_range=latest_saved"/>);
    await screen.findByRole("button",{name:"查看默认板块成分"});
    expect(screen.queryByRole("combobox",{name:"目录范围"})).toBeNull();
    expect(within(screen.getByRole("combobox",{name:"资金区间"})).getAllByRole("option").map(option=>option.getAttribute("value"))).toEqual(["intraday","5d","10d","20d"]);
    expect(jsonMock.mock.calls.map(([url])=>url)).toEqual(["/api/market/board-fund-flow?kind=concept&range=intraday"]);
  });
});

describe("原入选名单的目录对照边界", () => {
  it("原名单失败可单独重试，等待时禁用，恢复后才对照且不重取分类源", async () => {
    const retry=vi.fn();
    const failed={...selected,loaded:false,readError:true,pending:false,retry};
    const page=render(<Browser selection={failed}/>);
    await screen.findByRole("button",{name:"查看机器人概念成分"});
    expect(screen.getByText("原名单读取失败，归属对照未完成。")).toBeTruthy();
    expect(resultRows()[0].textContent).toContain("原名单读取失败");
    expect(screen.queryByText("本组原名单已确认空")).toBeNull();
    expect(jsonMock.mock.calls.map(call=>call[0])).toEqual(["/api/themes/hot?limit=30"]);

    fireEvent.click(screen.getByRole("button",{name:"重试原名单"}));
    expect(retry).toHaveBeenCalledTimes(1);
    page.rerender(<Browser selection={{...failed,pending:true}}/>);
    const pending=screen.getByRole("button",{name:"正在重试原名单…"});
    expect(pending).toHaveProperty("disabled",true);
    fireEvent.click(pending);expect(retry).toHaveBeenCalledTimes(1);
    expect(jsonMock).toHaveBeenCalledTimes(1);

    page.rerender(<Browser selection={{...selected,readError:false,pending:false,retry}}/>);
    await screen.findByText("已核匹配 1 只");
    expect(screen.queryByText("原名单读取失败，归属对照未完成。")).toBeNull();
    expect(jsonMock.mock.calls.filter(([url])=>String(url).includes("themes/hot"))).toHaveLength(1);
    expect(jsonMock.mock.calls.filter(([url])=>String(url).includes("discovery-memberships"))).toHaveLength(1);
    expect(jsonMock.mock.calls.some(([url])=>String(url).includes("board-fund-flow"))).toBe(false);
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("只作当前目录对照，不冒充历史归属或原入选依据", async () => {
    render(<Browser selection={selected}/>);
    await screen.findByText("已核匹配 1 只");
    expect(screen.getByText(/当前目录对照不是当时归属或入选依据/)).toBeTruthy();
    const call=jsonMock.mock.calls.find(([url]) => String(url).includes("discovery-memberships"))!;
    expect(String(call[0])).toContain("trade_date=2026-10-23");
    expect(String(call[0])).toContain("symbols=600519");
    expect(selected.entries[0].open).not.toHaveBeenCalled();
  });

  it("归属未知不显示精确零关联，来源失败也不称无匹配", async () => {
    jsonMock.mockImplementation(async (url:string) => url.includes("discovery-memberships")
      ? {data:memberships(["600519"],"2026-10-23","unknown")} : route(url));
    const page=render(<Browser selection={selected}/>);
    await waitFor(() => expect(resultRows()[0].textContent).toContain("1只归属未核"));
    expect(resultRows()[0].textContent).not.toContain("原名单 0 只");
    expect(resultRows()[0].textContent).toMatch(/未核/);
    jsonMock.mockImplementation(async (url:string) => {
      if(url.includes("discovery-memberships"))throw new Error("catalog offline");
      return route(url);
    });
    page.rerender(<Browser selection={{...selected,date:"2026-10-22"}}/>);
    await waitFor(() => expect(resultRows()[0].textContent).toContain("原名单归属未核"));
    expect(resultRows()[0].textContent).not.toContain("原名单 0 只");
  });

  it("换名单日期立刻撤下旧对照，返回错日期被真实API身份守卫拒绝", async () => {
    const page=render(<Browser selection={selected}/>);
    await screen.findByText("已核匹配 1 只");
    let finish!: (value: unknown) => void;
    jsonMock.mockImplementation((url:string) => url.includes("discovery-memberships")
      ? new Promise(resolve => {finish=resolve;}) : Promise.resolve(route(url)));
    page.rerender(<Browser selection={{...selected,date:"2026-10-22"}}/>);
    expect(screen.queryByText("已核匹配 1 只")).toBeNull();
    await waitFor(() => expect(typeof finish).toBe("function"));
    await act(async () => finish({data:memberships(["600519"],"2026-10-23")}));
    expect(screen.queryByText("已核匹配 1 只")).toBeNull();
    expect(resultRows()[0].textContent).toContain("原名单归属未核");
    expect(jsonMock.mock.calls.filter(([url]) => String(url).includes("themes/hot"))).toHaveLength(1);
  });
});

describe("分类导航的键盘与深链", () => {
  it("方向键只移动焦点；切视角链接清旧分类选择但保留对象和日期", () => {
    const search="browse=hot&disc_kind=concept&disc_range=5d&disc_code=old&disc_method=daily_picks&disc_group=all&disc_q=old&disc_sort=asc&selection=daily&date=2026-10-23&account=paper&symbol=600519&panel=evidence&review=1";
    render(<DiscoveryNavigation search={search}/>);
    const nav=screen.getByRole("navigation",{name:"选股分类发现"});
    const hot=within(nav).getByRole("link",{name:"热门题材"});
    const catalog=within(nav).getByRole("link",{name:"行业概念"});
    hot.focus();fireEvent.keyDown(hot,{key:"ArrowRight"});expect(document.activeElement).toBe(catalog);
    expect(hot.getAttribute("aria-current")).toBe("page");expect(catalog.hasAttribute("aria-current")).toBe(false);
    fireEvent.keyDown(catalog,{key:"End"});expect(document.activeElement).toBe(within(nav).getByRole("link",{name:"战法分类"}));
    fireEvent.keyDown(document.activeElement!,{key:"Home",isComposing:true,keyCode:229});expect(document.activeElement).toBe(within(nav).getByRole("link",{name:"战法分类"}));
    const query=new URL(catalog.getAttribute("href")!,"http://localhost").searchParams;
    expect(query.get("browse")).toBe("catalog");expect(query.get("view")).toBe("discover");
    for(const key of ["disc_kind","disc_range","disc_code","disc_method","disc_group","disc_q","disc_sort","selection","panel","review"])expect(query.has(key)).toBe(false);
    expect(query.get("date")).toBe("2026-10-23");expect(query.get("account")).toBe("paper");expect(query.get("symbol")).toBe("600519");
    expect(jsonMock).not.toHaveBeenCalled();
  });
});
