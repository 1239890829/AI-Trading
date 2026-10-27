import { useEffect, type ReactNode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BoardFlowKind, BoardFlowMember, BoardFlowMembersPayload, BoardFlowRow, BoardFundFlowPayload } from "@/lib/api";
import { InspectionScope, useSurfaceScope } from "@/components/inspection/surface-scope";
import { BoardFlowPanel } from "./board-flow";

const {read}=vi.hoisted(()=>({read:vi.fn()}));
vi.mock("@/lib/api/internal",async()=>({...await vi.importActual<typeof import("@/lib/api/internal")>("@/lib/api/internal"),getJson:(...args:unknown[])=>read(...args)}));
vi.mock("@/components/stock-link",()=>({StockLink:({symbol,children,className}:{symbol:string;children:ReactNode;className?:string})=><a href={`/workbench?symbol=${symbol}`} className={className}>{children}</a>,useStockRowNav:()=>()=>()=>{}}));
const received="2026-10-27T08:35:00+08:00",quote="2026-10-26T15:39:00+08:00";
const far={top:5000,bottom:5001,left:0,right:0,width:0,height:1,x:0,y:5000,toJSON:()=>({})} as DOMRect;
function board(index=0,kind:BoardFlowKind="concept"):BoardFlowRow {
  return {board_code:index===0?"BK1184":index===1?"BK1629":`BK${2000+index}`,name:index===0?"人形机器人":index===1?"AI应用":`板块${index}`,kind,
    change_pct:2,main_net_yi:index===0?10:-index,main_net_ratio:null,super_net_yi:null,main_net_5d_yi:null,main_net_10d_yi:null,
    leader_name:null,leader_symbol:null,streak:null,rank:index+1,rank_delta:null};
}
function funds(rows:BoardFlowRow[]=[board()],kind:BoardFlowKind="concept"):BoardFundFlowPayload {
  return {available:true,kind,range:"website",source:"eastmoney_official_funds",source_as_of:null,as_of:null,received_at:received,quote_source_as_of:quote,
    quality:"stale",observation_state:"clock_unknown",coverage_state:"source_complete",source_total:rows.length,source_returned:rows.length,directory_total:rows.length,
    coverage:{total:rows.length,returned:rows.length,complete:true},total_boards:rows.length,rows,degraded:[]};
}
function member(index=0):BoardFlowMember {
  return {symbol:String(600000+index),name:`成员${index}`,price:10,change_pct:100-index,main_net_yi:index===0?null:index===1?0:-1,
    main_net_ratio:null,super_net_yi:null,big_net_yi:null,quote_source_as_of:quote};
}
function members(rows:BoardFlowMember[]=[member()],kind:BoardFlowKind="concept",code="BK1184"):BoardFlowMembersPayload {
  return {available:true,board_code:code,name:code==="BK1184"?"人形机器人":"AI应用",kind,source:"eastmoney_official_funds",source_as_of:null,as_of:null,received_at:received,
    quality:"stale",observation_state:"clock_unknown",coverage_state:"source_complete",source_total:rows.length,source_returned:rows.length,directory_total:kind==="concept"?516:128,
    coverage:{total:rows.length,returned:rows.length,complete:true},quote_source_as_of:rows.flatMap(row=>row.quote_source_as_of?[row.quote_source_as_of]:[]).sort((a,b)=>Date.parse(b)-Date.parse(a))[0]??null,rows,degraded:[]};
}
let replaceScope:(params:URLSearchParams)=>void;
function ScopeControl(){const scope=useSurfaceScope();useEffect(()=>{replaceScope=scope.replaceSearch;});return null;}
function mount(search="") {return render(<InspectionScope initialSearch={search}><ScopeControl/><BoardFlowPanel/></InspectionScope>);}
function names(){return within(screen.getByRole("table",{name:"完整成员资金"})).getAllByRole("row").slice(1).map(row=>row.querySelector("td:nth-child(2) a")?.textContent);}
function route(url:string) {
  const u=new URL(url,"http://localhost");
  const kind=u.searchParams.get("kind")==="industry"?"industry":"concept";
  if(u.pathname==="/api/market/board-fund-flow") {
    const range=u.searchParams.get("range");
    return {data:range==="website"?funds([board(0,kind),board(1,kind)],kind):{...funds([board(0,kind)],kind),range,rows:[{...board(0,kind),main_net_ratio:8,streak:3,rank_delta:1}]}};
  }
  if(u.pathname.endsWith("/members"))return {data:members([member()],kind,u.pathname.split("/").at(-2))};
  if(u.pathname.endsWith("/minute"))return {data:{available:false,board_code:u.pathname.split("/").at(-2),items:[],daily_bars:[],degraded:[]}};
  throw new Error(`unexpected ${url}`);
}
beforeEach(()=>{read.mockReset();read.mockImplementation(async(url:string)=>route(url));vi.spyOn(HTMLElement.prototype,"getBoundingClientRect").mockReturnValue(far);});
afterEach(()=>{cleanup();vi.restoreAllMocks();});

describe("唯一市场板块官网全集",()=>{
  it("默认概念516和行业128准确标完整范围、未知资金源钟及独立报价日期",async()=>{
    read.mockImplementation(async(url:string)=>{const kind=new URL(url,"http://localhost").searchParams.get("kind")==="industry"?"industry":"concept";
      return {data:funds(Array.from({length:kind==="concept"?516:128},(_,index)=>board(index,kind)),kind)};});
    mount();await screen.findByText("概念 · 覆盖 516/516 · 本次来源范围已读全");
    expect(read.mock.calls[0][0]).toBe("/api/market/board-fund-flow?kind=concept&range=website");
    expect(screen.getByText("资金源钟未提供 · 即时状态未知")).toBeTruthy();
    expect(screen.getByText(/^最新板块报价钟 /).textContent).toContain("2026-10-26");
    expect(screen.getByText(/^接收 /).textContent).toContain("2026-10-27");
    await act(async()=>replaceScope(new URLSearchParams("board_kind=industry&board_range=website")));
    await screen.findByText("行业 · 覆盖 128/128 · 本次来源范围已读全");
    expect(read.mock.calls[1][0]).toBe("/api/market/board-fund-flow?kind=industry&range=website");
    expect(screen.queryByText(/Top20/)).toBeNull();
  });

  it("完整BK深链只开对应成员，314成员按涨幅排序并沿原drawer滚动加载至尾部",async()=>{
    read.mockImplementation(async(url:string)=>url.includes("/members")?{data:members(Array.from({length:314},(_,index)=>member(index)))}:route(url));
    const isDrawerBody=(node:HTMLElement)=>node.classList.contains("overflow-y-auto") && !!node.closest('[data-testid="board-flow-drawer"]');
    vi.spyOn(HTMLElement.prototype,"scrollHeight","get").mockImplementation(function(this:HTMLElement){return isDrawerBody(this)?2000:0;});
    vi.spyOn(HTMLElement.prototype,"clientHeight","get").mockImplementation(function(this:HTMLElement){return isDrawerBody(this)?300:0;});
    mount("board_kind=concept&board_range=website&board_code=BK1184");
    await screen.findByText("覆盖 314/314 · 已读全");
    const requested=["/api/market/board-fund-flow?kind=concept&range=website","/api/market/board-fund-flow/BK1184/members?view=website&kind=concept"];
    expect(read.mock.calls.map(([url])=>url)).toEqual(requested);
    const drawer=screen.getByTestId("board-flow-drawer");
    const table=within(drawer).getByRole<HTMLTableElement>("table",{name:"完整成员资金"});
    // Resolve accessible controls once while only 30 rows exist. Re-scanning the
    // growing whole document by role obscures the behavior under test in jsdom.
    const more=within(drawer).getByRole("button",{name:"继续加载"});
    const sort=within(drawer).getByRole("button",{name:"涨跌幅降序，点击切换"});
    const renderedNames=()=>Array.from(table.tBodies[0].rows,row=>row.cells[1].querySelector("a")?.textContent);
    expect(renderedNames()).toHaveLength(30);expect(renderedNames()[0]).toBe("成员0");
    fireEvent.click(more);expect(renderedNames()).toHaveLength(60);
    for(let index=0;index<7;index++)fireEvent.click(more);
    expect(renderedNames()).toHaveLength(270);
    const scrollOwner=table.closest<HTMLElement>(".overflow-y-auto")!;
    expect(scrollOwner).toBeTruthy();
    const sentinel=screen.getByTestId("board-members-sentinel");
    const position=vi.spyOn(sentinel,"getBoundingClientRect").mockReturnValue({...far,top:200,bottom:201,y:200});
    fireEvent.scroll(scrollOwner);expect(renderedNames()).toHaveLength(300);
    fireEvent.scroll(scrollOwner);
    expect(renderedNames()).toHaveLength(314);
    expect(renderedNames()).toEqual(Array.from({length:314},(_,index)=>`成员${index}`));
    expect(within(drawer).getByText("已全部加载（314 只成员）")).toBeTruthy();
    expect(drawer.querySelectorAll('[class*="overflow-y-auto"]')).toHaveLength(1);
    expect(screen.getByTestId("website-member-facts").parentElement!.querySelector('[class*="overflow"]')).toBeNull();
    expect(read.mock.calls.some(([url])=>String(url).includes("/minute"))).toBe(false);
    expect(read.mock.calls.map(([url])=>url)).toEqual(requested);
    position.mockReturnValue(far);
    fireEvent.click(sort);expect(renderedNames()[0]).toBe("成员313");expect(renderedNames().length).toBeLessThan(314);
    expect(read.mock.calls.map(([url])=>url)).toEqual(requested);
  });

  it("名称代码搜索与排序纯本地，缺值排后且资金0不丢，报价旧日常显",async()=>{
    const rows=[{...member(0),price:null,change_pct:null,quote_source_as_of:null},member(1),{...member(2),change_pct:-2,quote_source_as_of:"2026-10-23T15:00:00+08:00"}];
    read.mockImplementation(async(url:string)=>url.includes("/members")?{data:members(rows)}:route(url));
    mount("board_code=BK1184");await screen.findByText("覆盖 3/3 · 已读全");
    expect(names()).toEqual(["成员1","成员2","成员0"]);expect(screen.getByText("缺报价 1/3只 · 缺数保留成员")).toBeTruthy();
    expect(within(screen.getByTestId("website-member-facts")).getByText("资金源钟未提供")).toBeTruthy();
    expect(within(screen.getByRole("table",{name:"完整成员资金"})).getByText(/2026-10-23/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"涨跌幅降序，点击切换"}));expect(names()).toEqual(["成员2","成员1","成员0"]);
    fireEvent.change(screen.getByRole("searchbox",{name:"查找成员"}),{target:{value:"600001"}});expect(names()).toEqual(["成员1"]);
    expect(screen.getByRole("table",{name:"完整成员资金"}).textContent).toContain("0.0亿");
    expect(screen.getByRole("table",{name:"完整成员资金"}).textContent).not.toContain("亿亿");
    expect(read).toHaveBeenCalledTimes(2);
  });

  it.each(["source","kind","coverage","duplicate","funds_clock"])("拒收官网榜单%s错误，不按名字下钻",async issue=>{
    const data=funds();if(issue==="source")data.source="sina_board_funds";if(issue==="kind")data.kind="industry";
    if(issue==="coverage")data.source_total=516;if(issue==="duplicate"){data.rows=[board(),board()];data.source_total=data.source_returned=data.directory_total=2;data.coverage={total:2,returned:2,complete:true};}
    if(issue==="funds_clock")data.source_as_of=quote;
    read.mockResolvedValue({data});mount("board_code=BK1184");
    expect((await screen.findByRole("alert")).textContent).toContain("身份或覆盖不符");
    expect(screen.queryByTestId("board-flow-table")).toBeNull();expect(read).toHaveBeenCalledTimes(1);
  });

  it.each(["source","code","kind","coverage","quote"])("拒收成员%s错误，不用旧来源行补齐",async issue=>{
    const data=members();if(issue==="source")data.source="sina_board_funds";if(issue==="code")data.board_code="BK1629";
    if(issue==="kind")data.kind="industry";if(issue==="coverage")data.coverage={total:314,returned:1,complete:true};if(issue==="quote")data.quote_source_as_of=received;
    read.mockImplementation(async(url:string)=>url.includes("/members")?{data}:route(url));mount("board_code=BK1184");
    expect((await screen.findByRole("alert")).textContent).toContain("成员返回身份或覆盖不符");expect(screen.queryByRole("table",{name:"完整成员资金"})).toBeNull();
  });

  it("同BK官方改名保留成员并提示；失败后fresh重读仍保留未知资金源钟",async()=>{
    let attempts=0;
    read.mockImplementation(async(url:string)=>{
      if(!url.includes("/members"))return route(url);
      attempts++;if(attempts===1)throw new Error("offline");return {data:{...members(),name:"来源新正式名称"}};
    });
    mount("board_code=BK1184");await screen.findByText(/成员读取失败/);
    fireEvent.click(screen.getByRole("button",{name:"重试成员读取"}));
    await screen.findByText("成员来源名称：来源新正式名称；代码保持 BK1184。");
    expect(read.mock.calls.filter(([url])=>String(url).includes("/members"))[1][0]).toBe("/api/market/board-fund-flow/BK1184/members?view=website&kind=concept&fresh=true");
    expect(screen.getByTestId("website-member-facts").textContent).toContain("资金源钟未提供");
  });

  it("新BK源scope取消旧成员读取，晚包不能落入另一板块",async()=>{
    let finish!:(value:unknown)=>void;
    read.mockImplementation((url:string)=>url.includes("BK1184/members")?new Promise(resolve=>{finish=resolve;}):Promise.resolve(route(url)));
    mount("board_code=BK1184");await waitFor(()=>expect(typeof finish).toBe("function"));
    const signal=read.mock.calls.find(([url])=>String(url).includes("BK1184/members"))![2].signal as AbortSignal;
    await act(async()=>replaceScope(new URLSearchParams("board_code=BK1629&board_range=website&board_kind=concept")));
    await screen.findByRole("table",{name:"完整成员资金"});expect(signal.aborted).toBe(true);
    await act(async()=>finish({data:members([{...member(4),name:"晚到旧板成员"}])}));
    expect(screen.queryByText("晚到旧板成员")).toBeNull();expect(screen.getByRole("dialog").textContent).toContain("BK1629");
  });

  it("历史URL进入官网不保留无效ratio/streak/rank筛选或排序，合法净流入条件保留",async()=>{
    mount("board_range=intraday");await screen.findByTestId("board-row-BK1184");
    fireEvent.click(screen.getByRole("button",{name:"主力占比>5%"}));
    fireEvent.click(screen.getByRole("button",{name:"连续流入≥3"}));fireEvent.click(screen.getByRole("button",{name:"排名上升"}));
    fireEvent.click(screen.getByRole("button",{name:"净流入>0"}));
    await act(async()=>replaceScope(new URLSearchParams("board_range=website")));
    await screen.findByTestId("board-row-BK1184");
    expect(screen.getByRole("button",{name:"主力占比>5%"})).toHaveProperty("disabled",true);
    for(const name of ["主力占比>5%","连续流入≥3","排名上升"])expect(screen.getByRole("button",{name}).getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByRole("button",{name:"净流入>0"}).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByTestId("board-row-BK1629")).toBeNull();
  });

  it("Sina/THS/未知类别旧链接暂停资金读取，用户明确选择东财后才读官网",async()=>{
    mount("board_notice=unmapped_legacy_funds&legacy_source=sina&legacy_code=gn_ldc&legacy_kind=region");
    expect(screen.getByRole("alert").textContent).toContain("未请求板块资金");expect(read).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("原类别 region");
    fireEvent.click(screen.getByRole("button",{name:"查看东财官网板块"}));
    await screen.findByTestId("board-row-BK1184");expect(read.mock.calls[0][0]).toBe("/api/market/board-fund-flow?kind=concept&range=website");
  });
});
