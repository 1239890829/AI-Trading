import { useState, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { BoardFundFlowPayload, BoardFlowRow, DiscoveryMemberships, ThemesHotPayload } from "@/lib/api";

const {jsonMock} = vi.hoisted(() => ({jsonMock: vi.fn()}));
vi.mock("@/lib/api/internal", async () => ({
  ...await vi.importActual<typeof import("@/lib/api/internal")>("@/lib/api/internal"),
  getJson: (...args: unknown[]) => jsonMock(...args),
}));
vi.mock("@/components/stock-link", () => ({
  StockLink: ({symbol, children}: {symbol: string; children: ReactNode}) => <a href={`/workbench?symbol=${symbol}`}>{children}</a>,
}));

import { DiscoveryBrowser, DiscoveryNavigation, type DiscoverySelection } from "./discovery-browser";

const targetMock = vi.fn();
const navigateMock = vi.fn();
const emptySelection: DiscoverySelection = {date:"2026-10-23",loaded:true,entries:[]};
const selected: DiscoverySelection = {date:"2026-10-23",loaded:true,entries:[{symbol:"600519",name:"原入选公司",open:vi.fn()}]};
const farRect = {top:5000,bottom:5001,left:0,right:0,width:0,height:1,x:0,y:5000,toJSON:() => ({})} as DOMRect;

function hotPayload(): ThemesHotPayload {
  return {ts:"2026-10-25T09:50:00+08:00",themes:[
    {theme:"未涨停新题材",theme_code:"885001.TI",heat:700,hot_count:1,best:{symbol:"600519",name:"原入选公司",rank:7,rank_change:1},basis:"官方成分反查"},
    {theme:"另一已映射题材",theme_code:"885002.TI",heat:100,hot_count:1,best:null,basis:"官方成分反查"},
  ],stocks:[
    {symbol:"600519",name:"原入选公司",rank:7,heat:700,rank_change:1,themes:["未涨停新题材"]},
    {symbol:"000001",name:"未归属热股",rank:12,heat:450,rank_change:null,themes:[]},
  ]};
}

function board(overrides: Partial<BoardFlowRow> = {}): BoardFlowRow {
  return {board_code:"BK0001",name:"默认板块",kind:"concept",change_pct:1,main_net_yi:1,main_net_ratio:null,
    super_net_yi:null,main_net_5d_yi:null,main_net_10d_yi:null,main_net_20d_yi:null,
    leader_name:null,leader_symbol:null,streak:null,rank:1,rank_delta:null,...overrides};
}
function flow(rows: BoardFlowRow[], kind: BoardFundFlowPayload["kind"] = "concept", range: BoardFundFlowPayload["range"] = "intraday"): BoardFundFlowPayload {
  return {available:true,kind,range,rows,degraded:[],received_at:"2026-10-25T09:50:01+08:00",source_as_of:null,
    historical:range==="20d",as_of:range==="20d"?"2026-10-23":null};
}
function memberships(symbols: string[], date: string | null, state: "matched" | "unknown" = "matched"): DiscoveryMemberships {
  return {scope:"current_not_historical",requested_trade_date:date,content_version:"a".repeat(64),state:state==="matched"?"ready":"partial",
    source:"ths_official",source_as_of:null,observed_at:"2026-10-25T09:50:01+08:00",count:symbols.length,
    matched_count:state==="matched"?symbols.length:0,unknown_count:state==="unknown"?symbols.length:0,
    items:symbols.map(symbol => ({symbol,state,themes:state==="matched"?[{theme_code:"885001.TI",theme_name:"未涨停新题材",source:"ths_official"}]:[],reason:state==="unknown"?"成分尚未同步":null}))};
}
function route(url: string) {
  const query = new URL(url,"http://localhost");
  if (query.pathname === "/api/themes/hot") return {data:hotPayload()};
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
    expect(jsonMock.mock.calls.map(call=>call[0])).toEqual(["/api/picks/strategy-registry"]);
  });
  it("large heat remains readable in narrow rows while preserving the exact source value", async () => {
    const data=hotPayload();data.themes[0].heat=51040600;data.stocks[0].heat=51040600;
    jsonMock.mockResolvedValue({data});
    render(<Browser/>);
    const exact=await screen.findByTitle("51,040,600");
    expect(exact.textContent).toContain("5104.1万");
    expect(screen.getByText(/榜单时刻/).textContent).toContain("09:50（北京）");
  });
  it("没有涨停梯队也可发现热门题材，归属未核热股仍能读取", async () => {
    render(<Browser/>);
    expect(await screen.findByRole("button",{name:"查看未涨停新题材成分"})).toBeTruthy();
    expect(screen.getByText("热股共同归属，包含题材与通用标签")).toBeTruthy();
    expect(screen.getByText(/榜单时刻/).textContent).toContain("09:50（北京）");
    const unmapped = screen.getByText("热榜中归属未核的股票").closest("details")!;
    fireEvent.click(within(unmapped).getByText("热榜中归属未核的股票"));
    expect(within(unmapped).getByRole("link",{name:"未归属热股"}).getAttribute("href")).toContain("symbol=000001");
    expect(jsonMock.mock.calls.map(call => call[0])).toEqual(["/api/themes/hot?limit=30"]);
    expect(selected.entries[0].open).not.toHaveBeenCalled();
  });

  it("源股未提供人气时聚合占位0不是有效零，显示缺失覆盖", async () => {
    const data=hotPayload();
    data.themes[0].heat=0;data.stocks[0].heat=null;
    jsonMock.mockResolvedValue({data});
    render(<Browser/>);
    const target=await screen.findByRole("button",{name:"查看未涨停新题材成分"});
    expect(within(target).getByText("未齐")).toBeTruthy();
    expect(within(target).getByText("0/1股提供人气")).toBeTruthy();
    expect(within(target).queryByText("榜内人气合计")).toBeNull();
  });

  it("每种激活视角只读对应来源，切走取消上游请求，不启动策略", async () => {
    let finishHot!: (value: unknown) => void, finishBoards!: (value: unknown) => void;
    jsonMock.mockImplementation((url:string) => {
      if(url.includes("themes/hot"))return new Promise(resolve => {finishHot=resolve;});
      if(url.includes("board-fund-flow"))return new Promise(resolve => {finishBoards=resolve;});
      return Promise.resolve(route(url));
    });
    const page = render(<Browser search="browse=hot"/>);
    await waitFor(() => expect(jsonMock).toHaveBeenCalledTimes(1));
    const hotSignal = jsonMock.mock.calls[0][2].signal as AbortSignal;
    page.rerender(<Browser search="browse=catalog&disc_kind=industry&disc_range=20d"/>);
    await waitFor(() => expect(jsonMock).toHaveBeenCalledTimes(2));
    expect(hotSignal.aborted).toBe(true);
    expect(jsonMock.mock.calls[1][0]).toBe("/api/market/board-fund-flow?kind=industry&range=intraday");
    const boardSignal = jsonMock.mock.calls[1][2].signal as AbortSignal;
    page.rerender(<Browser search="browse=methods"/>);
    await screen.findByText("暂无已登记方法。");
    expect(boardSignal.aborted).toBe(true);
    expect(jsonMock.mock.calls.map(call => call[0])).toEqual([
      "/api/themes/hot?limit=30","/api/market/board-fund-flow?kind=industry&range=intraday","/api/picks/strategy-registry",
    ]);
    await act(async () => {
      finishHot({data:hotPayload()});
      finishBoards({data:flow([board({kind:"industry"})],"industry")});
    });
    expect(screen.queryByRole("button",{name:"查看未涨停新题材成分"})).toBeNull();
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
    render(<Harness initial="browse=hot&disc_code=885002.TI&selection=daily&date=2026-10-23&account=paper"/>);
    await screen.findByRole("button",{name:"查看未涨停新题材成分"});
    const input = screen.getByRole("textbox",{name:"搜索分类"});
    fireEvent.compositionStart(input);fireEvent.change(input,{target:{value:"未涨停"}});
    expect(navigateMock).not.toHaveBeenCalled();
    expect(resultRows()).toHaveLength(2);
    fireEvent.compositionEnd(input,{data:"未涨停"});
    expect(resultRows()).toHaveLength(1);
    const query = new URL(navigateMock.mock.lastCall![0],"http://localhost").searchParams;
    expect(query.get("disc_q")).toBe("未涨停");
    expect(query.get("date")).toBe("2026-10-23");expect(query.get("account")).toBe("paper");
    expect(query.has("disc_code")).toBe(false);expect(query.has("selection")).toBe(false);
    expect(jsonMock).toHaveBeenCalledTimes(1);
  });

  it("选择行进入相应来源scope，清原名单选中但保留日期/账户/返回位置", async () => {
    const original="browse=hot&selection=daily&date=2026-10-23&account=paper&symbol=600519&from=%2Fmarket";
    render(<Harness initial={original}/>);
    await screen.findByRole("button",{name:"查看未涨停新题材成分"});
    fireEvent.click(screen.getByRole("button",{name:"查看未涨停新题材成分"}));
    const query = new URL(navigateMock.mock.lastCall![0],"http://localhost").searchParams;
    expect(navigateMock.mock.lastCall![1]).toBe(true);
    expect(query.get("disc_code")).toBe("885001.TI");expect(query.get("selection")).toBeNull();
    expect(query.get("account")).toBe("paper");expect(query.get("symbol")).toBe("600519");
    expect(query.get("from")).toBe("/market");expect(query.get("date")).toBe("2026-10-23");
    expect(targetMock.mock.lastCall).toEqual([JSON.stringify(["hot","concept","intraday","885001.TI"]),{kind:"theme",code:"885001.TI",name:"未涨停新题材"}]);
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

describe("原入选名单的目录对照边界", () => {
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
    const search="browse=hot&disc_kind=concept&disc_range=5d&disc_code=old&disc_q=old&disc_sort=asc&selection=daily&date=2026-10-23&account=paper&symbol=600519&panel=evidence&review=1";
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
    for(const key of ["disc_kind","disc_range","disc_code","disc_q","disc_sort","selection","panel","review"])expect(query.has(key)).toBe(false);
    expect(query.get("date")).toBe("2026-10-23");expect(query.get("account")).toBe("paper");expect(query.get("symbol")).toBe("600519");
    expect(jsonMock).not.toHaveBeenCalled();
  });
});
