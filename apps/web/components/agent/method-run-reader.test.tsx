import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import {act,cleanup,fireEvent,render,screen,waitFor,within} from "@testing-library/react";
import type {ReactNode} from "react";
import type {ReferenceResearchRowState,SavedMethodRunDetail,SavedMethodRunIdentity,SavedStrategyRunItem} from "@/lib/api/picks";
import {MethodRunReader} from "./method-run-reader";
import {ApiError} from "@/lib/api/client";
import {identityLocation,methodRunKey,methodRunUrl,readMethodRunLocation} from "./method-run-location";

const mocks=vi.hoisted(()=>({index:vi.fn(),detail:vi.fn()}));
vi.mock("@/lib/api/picks",async()=>({...await vi.importActual<typeof import("@/lib/api/picks")>("@/lib/api/picks"),getStrategyMethodRuns:mocks.index,getStrategyMethodRun:mocks.detail}));
vi.mock("@/components/stock-link",()=>({StockLink:({symbol,children}:{symbol:string;children:ReactNode})=><a href={`/workbench?symbol=${symbol}`}>{children}</a>}));
const original:SavedMethodRunIdentity={method:"daily_picks",run_id:"original-22",trade_date:"2026-10-22",source_version:"source-22",content_version:"a".repeat(64),strategy_version:"method-22",state:"saved"};
const newer:SavedMethodRunIdentity={...original,run_id:"newer-23",trade_date:"2026-10-23",source_version:"source-23",content_version:"b".repeat(64),strategy_version:"method-23"};
const row:SavedStrategyRunItem={item_id:"rejected:0",symbol:"600000",name:"原落选股",state:"rejected",score:0,reason:"原否决不变",basis:{tech:"原技术依据"},diagnostics:{state:"partial",sub_scores:{tech:0,news:40},dimension_evidence:{tech:{state:"missing",reason:"原技术源未提供"},news:{state:"success",reason:"原新闻已保存"}},scoring_trace:{algorithm_version:"original-score",dimensions:{tech:{score:0,weight:0,contribution:0}},weighted_total:0,post_veto_score:0,risk_penalty:0,final_score:0},vetoes:["原财务否决"],selection_reason:"below_min_score",score_shortfall:0,min_pick_score:0,missing:["source_clock"]}};
function detail(identity=original,items:SavedStrategyRunItem[]=[row],offset=0,total=items.length):SavedMethodRunDetail {
  return {...identity,items,counts:{candidates:40,scored:24,selected:0,rejected_saved:total},criteria:{min_pick_score:0,max_picks:3},coverage:{offset,limit:200,total,returned:items.length,next_offset:offset+items.length<total?offset+items.length:null,possibly_truncated:offset+items.length<total,history_semantics:"每日仅保留该日当前保存版本，不代表全部生成版本。"}};
}
function locate(identity=original,filter="rejected",selectedRow="") {
  const location={...identityLocation(identity,readMethodRunLocation("","daily_picks")),filter:filter as "rejected",row:selectedRow};
  window.history.replaceState({},"",methodRunUrl("/agent","?area=research&tab=strategies",identity.method,location));
}
beforeEach(()=>{
  window.history.replaceState({},"","/agent?area=research&tab=strategies");
  mocks.index.mockImplementation(async({method}:{method:string})=>({method,items:[newer,original],coverage:{limit:30,possibly_truncated:false,history_semantics:"原索引范围"}}));
  mocks.detail.mockImplementation(async(identity:SavedMethodRunIdentity)=>detail(identity));
});

describe("daily original input classification",()=>{
  it("filters saved categories locally without another request and never calls a partial page complete",async()=>{
    locate(original,"selected");
    const selected:SavedStrategyRunItem={item_id:"selected:0",symbol:"600001",name:"原入选甲",state:"selected",score:55,evaluation:{state:"selected",label:"原入选"}};
    const capacity:SavedStrategyRunItem={item_id:"candidate:1",symbol:"600002",name:"容量未评估乙",state:"rejected",score:null,reason_code:"candidate_capacity",evaluation:{state:"not_evaluated",label:"容量未评估"}};
    const value=detail(original,[selected,capacity],0,3);
    value.counts={selected:1};
    value.selection_summary={state:"saved",scope:"recorded_inputs_not_all_market",input_total:3,saved_total:3,omitted_total:0,
      counts:{selected:1,score_rejected:1,not_evaluated:1,input_unavailable:0,excluded:0,unknown:0},
      saved_counts:{selected:1,score_rejected:1,not_evaluated:1,input_unavailable:0,excluded:0,unknown:0},candidate_capacity:40,deep_capacity:24,reason:"原分布"};
    mocks.detail.mockResolvedValue(value);
    render(<MethodRunReader method="daily_picks"/>);await screen.findByText("原入选甲");
    expect(screen.queryByText(/已全部加载/)).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"容量未评估 1"}));
    await screen.findByText("容量未评估乙");
    expect(screen.queryByText("原入选甲")).toBeNull();
    expect(mocks.detail).toHaveBeenCalledTimes(1);
    expect(window.location.search).toContain("run_state=not_evaluated");
    expect(screen.getByText(/本批已读 2 \/ 3/)).toBeTruthy();
    expect(readMethodRunLocation(window.location.search,"daily_picks").filter).toBe("not_evaluated");
    expect(readMethodRunLocation("?run_state=not_evaluated","intraday_watch").filter).toBe("selected");
  });
});

describe("reference research state paging",()=>{
  const identity:SavedMethodRunIdentity={...original,method:"reference:macd_golden_cross",run_id:"research-state-23",trade_date:"2026-10-23",strategy_version:"v1"};
  const counts={universe:5580,eligible:5434,matched:353,not_matched:5081,unknown:146};
  const rows=(state:"matched"|"not_matched"|"unknown",length:number,offset=0)=>Array.from({length},(_,index):SavedStrategyRunItem=>({
    item_id:`${state}:${offset+index}`,symbol:"600000",name:`${state}原行${offset+index}`,state,stage:"research",score:null,
    conditions:[{key:"input_valid",met:state==="unknown"?null:true,value:null,reference:null}]}));
  function page(state:ReferenceResearchRowState,items:SavedStrategyRunItem[],offset=0,total=state==="all"?5580:counts[state],batchTotal=5580):SavedMethodRunDetail {
    return {...detail(identity,items,offset,total),row_state:state,counts,
      coverage:{offset,limit:200,total,batch_total:batchTotal,returned:items.length,next_offset:offset+items.length<total?offset+items.length:null,
        possibly_truncated:offset+items.length<total,history_semantics:"按原保存状态分页，全批计数不变"}};
  }
  function locateState(state:ReferenceResearchRowState="all") {
    const location={...identityLocation(identity,readMethodRunLocation("",identity.method)),filter:state};
    window.history.replaceState({},"",methodRunUrl("/agent","area=research&tab=strategies",identity.method,location));
  }
  beforeEach(()=>{locateState();mocks.index.mockResolvedValue({method:identity.method,items:[identity],coverage:{limit:30,possibly_truncated:false,history_semantics:"原研究索引"}});});

  it("reads the selected state from offset zero instead of blindly traversing 5580 rows or filtering the first 200 matches",async()=>{
    mocks.detail.mockImplementation(async(_identity:SavedMethodRunIdentity,{rowState="all",offset=0}:{rowState?:ReferenceResearchRowState;offset?:number})=>
      page(rowState,rows(rowState==="all"?"matched":rowState,rowState==="unknown"?146:200,offset),offset));
    render(<MethodRunReader method={identity.method}/>);
    await screen.findByText("matched原行0");
    expect(mocks.detail).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button",{name:"待核"}));
    expect(await screen.findByText("unknown原行0")).toBeTruthy();
    expect(mocks.detail).toHaveBeenCalledTimes(2);
    expect(mocks.detail.mock.calls[1][1]).toMatchObject({rowState:"unknown"});
    expect(mocks.detail.mock.calls[1][1].offset??0).toBe(0);
    const saved=screen.getByLabelText("原保存运行");
    expect(within(saved).getByText("原样本").nextElementSibling?.textContent).toBe("5,580");
    expect(within(saved).getByText("未知",{selector:"dt"}).nextElementSibling?.textContent).toBe("146");
    expect(screen.getByText(/本状态已读 146 \/ 146 条；全批 5,580 条原行/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"未符合"}));
    expect(await screen.findByText("not_matched原行0")).toBeTruthy();
    expect(screen.queryByText("unknown原行0")).toBeNull();
    expect(mocks.detail).toHaveBeenCalledTimes(3);
    expect(mocks.detail.mock.calls.map(([,options])=>options.rowState)).toEqual(["all","unknown","not_matched"]);
    expect(mocks.detail.mock.calls.every(([value])=>methodRunKey(value)===methodRunKey(identity))).toBe(true);
  });

  it("keeps state controls during a pending read and ignores a late response after a rapid switch",async()=>{
    let finish!:(value:SavedMethodRunDetail)=>void;
    mocks.detail.mockImplementation((_identity:SavedMethodRunIdentity,{rowState="all"}:{rowState?:ReferenceResearchRowState})=>rowState==="unknown"
      ?new Promise< SavedMethodRunDetail >(resolve=>{finish=resolve;})
      :Promise.resolve(page(rowState,rows(rowState==="all"?"matched":rowState,1))));
    render(<MethodRunReader method={identity.method}/>);await screen.findByText("matched原行0");
    fireEvent.click(screen.getByRole("button",{name:"待核"}));
    await waitFor(()=>expect(mocks.detail).toHaveBeenCalledTimes(2));
    expect(screen.getByText("读取指定原批次…")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"未符合"}));
    await screen.findByText("not_matched原行0");
    expect(mocks.detail.mock.calls[1][1].signal.aborted).toBe(true);
    await act(async()=>finish(page("unknown",rows("unknown",1))));
    expect(screen.queryByText("unknown原行0")).toBeNull();
    expect(screen.getByText("not_matched原行0")).toBeTruthy();
    expect(mocks.detail).toHaveBeenCalledTimes(3);
  });

  it.each(["state_echo","missing_batch_total","foreign_row"])("rejects an initial %s response instead of displaying or silently filtering foreign rows",async issue=>{
    locateState("unknown");
    const value=page("unknown",rows("unknown",1));
    if(issue==="state_echo")value.row_state="all";
    if(issue==="missing_batch_total")delete value.coverage.batch_total;
    if(issue==="foreign_row")value.items=rows("matched",1);
    mocks.detail.mockResolvedValue(value);
    render(<MethodRunReader method={identity.method}/>);
    expect((await screen.findByRole("alert")).textContent).toContain("状态或分页范围不符");
    expect(screen.queryByLabelText("原保存运行")).toBeNull();
    expect(screen.queryByText("unknown原行0")).toBeNull();expect(screen.queryByText("matched原行0")).toBeNull();
  });

  it.each(["state_echo","batch_total","counts"])("keeps existing unknown rows when a continuation has changed %s and retries the same exact offset",async issue=>{
    locateState("unknown");
    const bad=page("unknown",rows("unknown",1,1),1);
    if(issue==="state_echo")bad.row_state="all";
    else if(issue==="batch_total")bad.coverage.batch_total=5581;
    else bad.counts={...counts,unknown:145};
    mocks.detail.mockResolvedValueOnce(page("unknown",rows("unknown",1))).mockResolvedValueOnce(bad)
      .mockResolvedValueOnce(page("unknown",rows("unknown",1,1),1));
    render(<MethodRunReader method={identity.method}/>);await screen.findByText("unknown原行0");
    await screen.findByRole("button",{name:"继续读取原批次"});
    fireEvent.scroll(window,{bubbles:false});
    expect((await screen.findByRole("alert")).textContent).toContain("状态或分页范围不符");
    expect(screen.getByText("unknown原行0")).toBeTruthy();expect(screen.queryByText("unknown原行1")).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"重试原批次后续行"}));
    await screen.findByText("unknown原行1");
    expect(mocks.detail.mock.calls[1][1]).toMatchObject({rowState:"unknown",offset:1,limit:200});
    expect(mocks.detail.mock.calls[2][1]).toMatchObject({rowState:"unknown",offset:1,limit:200});
  });

  it("does not append an old state's pending continuation after another state becomes current",async()=>{
    locateState("unknown");let finish!:(value:SavedMethodRunDetail)=>void;
    mocks.detail.mockImplementation((_identity:SavedMethodRunIdentity,{rowState="all",offset=0}:{rowState?:ReferenceResearchRowState;offset?:number})=>
      rowState==="unknown"&&offset===1?new Promise<SavedMethodRunDetail>(resolve=>{finish=resolve;})
        :Promise.resolve(page(rowState,rows(rowState==="all"?"matched":rowState,1),offset)));
    render(<MethodRunReader method={identity.method}/>);await screen.findByText("unknown原行0");
    fireEvent.click(screen.getByRole("button",{name:"继续读取原批次"}));await waitFor(()=>expect(mocks.detail).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button",{name:"未符合"}));await screen.findByText("not_matched原行0");
    expect(mocks.detail.mock.calls[1][1].signal.aborted).toBe(true);
    await act(async()=>finish(page("unknown",rows("unknown",1,1),1)));
    expect(screen.queryByText("unknown原行1")).toBeNull();expect(screen.queryByText("unknown原行0")).toBeNull();
    expect(screen.getByText("not_matched原行0")).toBeTruthy();
  });

  it("shows a source-confirmed state zero without erasing the batch's unknown denominator",async()=>{
    locateState("not_matched");
    mocks.detail.mockResolvedValue({...page("not_matched",[],0,0,1),counts:{universe:1,eligible:0,matched:0,not_matched:0,unknown:1}});
    render(<MethodRunReader method={identity.method}/>);
    await screen.findByText("原研究批次已保存：未符合 0 条原行。");
    const saved=screen.getByLabelText("原保存运行");
    expect(within(saved).getByText("未知",{selector:"dt"}).nextElementSibling?.textContent).toBe("1");
    expect(screen.getByText(/本状态已读 0 \/ 0 条；全批 1 条原行/)).toBeTruthy();
    expect(screen.queryByText(/所查扫描范围没有可读取/)).toBeNull();
    expect(mocks.detail).toHaveBeenCalledTimes(1);
  });

  it("keeps inconsistent old summary counts separate from actual saved-row coverage instead of replacing either",async()=>{
    locateState("unknown");
    mocks.detail.mockResolvedValue({...page("unknown",rows("unknown",1),0,1,1),counts:{universe:3,eligible:1,matched:0,not_matched:1,unknown:2}});
    render(<MethodRunReader method={identity.method}/>);await screen.findByText("unknown原行0");
    expect(screen.getByText("原分母与实际保存行覆盖不一致，分别保留；未用当前可见行改写原计数。")).toBeTruthy();
    const saved=screen.getByLabelText("原保存运行");
    expect(within(saved).getByText("原样本").nextElementSibling?.textContent).toBe("3");
    expect(within(saved).getByText("未知",{selector:"dt"}).nextElementSibling?.textContent).toBe("2");
    expect(screen.getByText(/本状态已读 1 \/ 1 条；全批 1 条原行/)).toBeTruthy();
    expect(screen.queryByText(/0 条原行/)).toBeNull();
  });
});

describe("saved candidate source traces",()=>{
  const fund:SavedStrategyRunItem={item_id:"candidate:1",symbol:"510300",name:"场内基金原行",state:"rejected",score:null,
    stage:"identity",reason_code:"outside_a_share_universe",reason:"不在当时已核实的A股证券范围；未占用股票评估名额",
    evidence:{source:"hot",candidate_sources:[{from:"hot"}],input_position:1}};
  it("renders a saved fund identity rejection as unscored while retaining the scored-rejection denominator",async()=>{
    locate(original,"rejected");mocks.detail.mockResolvedValue({...detail(original,[row,fund]),
      counts:{candidates:1,scored:1,selected:0,rejected_saved:1},
      candidate_audit:{state:"saved",input_total:2,saved_total:2,omitted_total:0,complete:true,source_states:{events:"available",hot:"available"},absent_symbol_state:"not_recalled_in_recorded_scope"}});
    render(<MethodRunReader method="daily_picks"/>);
    const fundRow=(await screen.findByText("场内基金原行")).closest("li")!;
    expect(within(fundRow).getByText("未评分")).toBeTruthy();
    expect(within(fundRow).getByText("证券身份")).toBeTruthy();
    expect(within(fundRow).getByText("原来源：人气榜")).toBeTruthy();
    expect(within(fundRow).queryByText("原分 0")).toBeNull();
    expect(screen.getByText("原分 0")).toBeTruthy();
    const saved=screen.getByLabelText("原保存运行");
    expect(within(saved).getByText("评分拒选",{selector:"dt"}).nextElementSibling?.textContent).toBe("1");
    fireEvent.click(within(fundRow).getByRole("button",{name:"核对场内基金原行原候选留痕"}));
    const trace=screen.getByRole("region",{name:"场内基金原行的原候选留痕"});
    expect(within(trace).getByText("原输入位置 1")).toBeTruthy();
    expect(within(trace).queryByLabelText("原运行六维评分")).toBeNull();
    expect(mocks.detail).toHaveBeenCalledTimes(1);
  });

  it("shows bounded partial trace coverage and source capacity without deriving counts from visible rows",async()=>{
    const capacity:SavedStrategyRunItem={...fund,item_id:"candidate:81",symbol:"600081",name:"容量截断原行",
      stage:"candidate",reason_code:"candidate_capacity",reason:"原候选来源容量截断；没有评分",
      evidence:{source:"event",candidate_sources:[{from:"event",event_id:"saved-event"},{from:"hot"}],input_position:81}};
    locate(original,"rejected");mocks.detail.mockResolvedValue({...detail(original,[row,capacity]),
      counts:{candidates:80,scored:12,selected:0,rejected_saved:1},
      candidate_audit:{state:"partial",input_total:530,saved_total:500,omitted_total:30,complete:false,
        source_states:{events:"available",hot:"unavailable"},absent_symbol_state:"unknown"}});
    render(<MethodRunReader method="daily_picks"/>);
    const summary=await screen.findByRole("region",{name:"原候选来源留痕"});
    expect(summary.textContent).toContain("部分保存");
    expect(summary.textContent).toContain("记录 500 / 原输入 530 · 未保存 30");
    expect(summary.textContent).toContain("未出现不等于低分");
    expect(summary.textContent).toContain("事件来源 可用");
    expect(summary.textContent).toContain("热榜来源 不可用");
    const candidate=screen.getByText("容量截断原行").closest("li")!;
    expect(within(candidate).getByText("候选容量")).toBeTruthy();
    expect(within(candidate).getByText("未评分")).toBeTruthy();
    expect(within(candidate).getByText("原来源：事件点名 · 人气榜")).toBeTruthy();
    expect(within(screen.getByLabelText("原保存运行")).getByText("评分拒选",{selector:"dt"}).nextElementSibling?.textContent).toBe("1");
    expect(mocks.detail).toHaveBeenCalledTimes(1);
  });

  it("keeps old missing candidate provenance unknown rather than inventing zero records or low scores",async()=>{
    locate(original,"rejected");mocks.detail.mockResolvedValue({...detail(),candidate_audit:{state:"missing",reason:"原版本未保存逐候选来源与截断依据，未按当前规则重算"}});
    render(<MethodRunReader method="daily_picks"/>);
    const summary=await screen.findByRole("region",{name:"原候选来源留痕"});
    expect(summary.textContent).toContain("未保存");
    expect(summary.textContent).toContain("未出现不等于低分");
    expect(summary.textContent).not.toContain("记录 0");
    expect(screen.getByText("原分 0")).toBeTruthy();
  });

  it("keeps an unsuccessful saved scoring attempt distinct from an unrecorded later stage",async()=>{
    const attempted:SavedStrategyRunItem={...fund,item_id:"candidate:2",symbol:"600002",name:"评分尝试原行",state:"unknown",stage:"score",
      reason_code:"scoring_unavailable",reason:"原批次尝试评分但没有保存成功评分；不能补成0分"};
    const unknown:SavedStrategyRunItem={...fund,item_id:"candidate:3",symbol:"600003",name:"后续缺项原行",state:"unknown",stage:"candidate",
      reason_code:"candidate_admitted",reason:"原候选已接纳；后续处理未记录"};
    locate(original,"unknown");mocks.detail.mockResolvedValue({...detail(original,[attempted,unknown]),
      candidate_audit:{state:"saved",input_total:2,saved_total:2,omitted_total:0,complete:true}});
    render(<MethodRunReader method="daily_picks"/>);
    const scored=(await screen.findByText("评分尝试原行")).closest("li")!;
    expect(within(scored).getByText("未保存成功评分")).toBeTruthy();
    expect(within(scored).getByText("评分")).toBeTruthy();
    expect(within(scored).queryByText("未评分")).toBeNull();
    const missing=screen.getByText("后续缺项原行").closest("li")!;
    expect(within(missing).getByText("评分状态未记录")).toBeTruthy();
    expect(screen.queryByText("原分 0")).toBeNull();
    expect(mocks.detail).toHaveBeenCalledTimes(1);
  });
});
afterEach(()=>{cleanup();vi.clearAllMocks();mocks.index.mockReset();mocks.detail.mockReset();});

describe("saved method run reader",()=>{
  it("a 409 damaged research index renders an error rather than zero records, and explicit retry keeps research semantics",async()=>{
    const method="reference:macd_golden_cross";
    mocks.index.mockRejectedValue(new ApiError(409,"研究目录存在损坏记录；未静默丢弃","http_409"));
    render(<MethodRunReader method={method}/>);
    expect((await screen.findByRole("alert")).textContent).toContain("运行索引读取失败：研究目录存在损坏记录");
    expect(screen.queryByText(/所查扫描范围没有可读取/)).toBeNull();
    expect(screen.queryByText(/原批次已保存：0/)).toBeNull();
    expect(mocks.detail).not.toHaveBeenCalled();
    mocks.index.mockResolvedValue({method,items:[],coverage:{offset:0,limit:30,total:0,returned:0,next_offset:null,possibly_truncated:false,history_semantics:"手动价格研究"}});
    fireEvent.click(screen.getByRole("button",{name:"重试索引"}));
    await screen.findByText("所查扫描范围没有可读取的研究批次；不证明源日未运行或零匹配。");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/零入选/)).toBeNull();
    expect(mocks.detail).not.toHaveBeenCalled();
  });

  it("reads an exact manual research batch with all denominators, unknown conditions and no selection or score claims",async()=>{
    const identity:SavedMethodRunIdentity={...original,method:"reference:macd_golden_cross",run_id:"research-23",trade_date:"2026-10-23",strategy_version:"v1"};
    const rows:SavedStrategyRunItem[]=[{item_id:"600000.SH",symbol:"600000",name:"600000",state:"matched",stage:"research",decision:"matched",score:null,conditions:[{key:"dif_above_dea",met:true,value:0,reference:-1}],metrics:{dif:0}},
      {item_id:"600001.SH",symbol:"600001",name:"600001",state:"not_matched",stage:"research",decision:"not_matched",score:null,conditions:[{key:"dif_above_dea",met:false,value:-1,reference:0}]},
      {item_id:"600002.SH",symbol:"600002",name:"600002",state:"unknown",stage:"research",decision:"unknown",score:null,reason:"缺少原交易日",conditions:[{key:"input_valid",met:null,value:null,reference:null}]}];
    mocks.index.mockResolvedValue({method:identity.method,items:[identity],coverage:{limit:30,possibly_truncated:false,history_semantics:"手动研究，不是选股"}});
    mocks.detail.mockImplementation(async(_identity:SavedMethodRunIdentity,{rowState="all"}:{rowState?:ReferenceResearchRowState})=>{
      const filtered=rowState==="all"?rows:rows.filter(row=>row.state===rowState);
      return {...detail(identity,filtered),row_state:rowState,coverage:{...detail(identity,filtered).coverage,batch_total:rows.length},counts:{universe:3,eligible:2,matched:1,not_matched:1,unknown:1},source_completed_date:"2026-10-23",source_as_of:null,source_cutoff_at:"2026-10-23T16:00:00+08:00",definition_digest:"c".repeat(64),definition_supplement:"EMA从首个输入初始化；这是本系统v1。",data_limitations:["历史全集未认证"]};
    });
    render(<MethodRunReader method={identity.method} initialIdentity={identity} compact/>);
    const result=await screen.findByLabelText("原保存运行");
    expect(within(result).getByText("原样本").nextElementSibling?.textContent).toBe("3");
    expect(within(result).getByText("指标可评价").nextElementSibling?.textContent).toBe("2");
    expect(within(result).getByText(/源首次发布时间 未知；筛选界限 2026-10-23 16:00/)).toBeTruthy();
    expect(within(result).getByLabelText("600000研究条件").textContent).toContain("原值 0.0000 · 对照 -1.0000");
    // Unknown stays explicit; two absent numeric values no longer add repeated boilerplate.
    expect(within(result).getByLabelText("600002研究条件").textContent).toBe("输入有效性未知");
    expect(within(result).queryByText("未保存分数")).toBeNull();
    expect(within(result).queryByRole("button",{name:"原入选"})).toBeNull();
    expect(screen.getByText("批次与版本筛选").closest("details")?.open).toBe(false);
    fireEvent.click(screen.getByRole("button",{name:"未符合"}));
    await screen.findByLabelText("600001研究条件");
    const filtered=screen.getByLabelText("原保存运行");
    expect(within(filtered).queryByLabelText("600000研究条件")).toBeNull();
    expect(within(filtered).getByLabelText("600001研究条件")).toBeTruthy();
    expect(mocks.detail.mock.calls[0][0]).toEqual(identity);
  });

  it("restores the original row and shows saved zero, missing evidence, veto and contribution without recomputing",async()=>{
    locate(original,"rejected","rejected:0");render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText("原落选股")).toBeTruthy();
    const score=screen.getByRole("region",{name:"本行原分线与差额"});
    expect(within(score).getAllByText("0分")).toHaveLength(3);
    fireEvent.click(screen.getByRole("button",{name:"核对技术评分依据"}));
    expect(screen.getByText("原技术源未提供")).toBeTruthy();
    expect(screen.queryByRole("meter",{name:"技术原规则分值"})).toBeNull();
    expect(screen.getByRole("meter",{name:"消息原规则分值"}).getAttribute("aria-valuenow")).toBe("40");
    expect(screen.getByText("原财务否决")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"原合成贡献与依据"}));
    expect(screen.getByText("权重 0.00% · 原贡献 0.00分")).toBeTruthy();
    expect(screen.getByText(/原算法版本：original-score/)).toBeTruthy();
    expect(screen.getByRole("link",{name:"另看当前名单（非本次历史）"}).getAttribute("href")).toContain("sec=daily");
    expect(screen.queryByRole("link",{name:"执行与成交核对"})).toBeNull();
    expect(mocks.detail.mock.calls[0][0]).toMatchObject(original);
  });
  it("ignores a late old-batch answer after the user chooses another exact run",async()=>{
    let finish!: (value:SavedMethodRunDetail)=>void;
    mocks.detail.mockImplementation((identity:SavedMethodRunIdentity)=>identity.run_id===newer.run_id?new Promise(resolve=>{finish=resolve;}):Promise.resolve(detail(identity,[{...row,name:"较早原批次"}])));
    render(<MethodRunReader method="daily_picks"/>);
    await waitFor(()=>expect(mocks.detail).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByRole("combobox",{name:"选择原运行批次"}),{target:{value:methodRunKey(original)}});
    fireEvent.click(await screen.findByRole("button",{name:"拒绝存档"}));
    expect(await screen.findByText("较早原批次")).toBeTruthy();
    await act(async()=>finish(detail(newer,[{...row,name:"迟到新批次"}])));
    expect(screen.queryByText("迟到新批次")).toBeNull();
    expect(new URLSearchParams(window.location.search).get("run_id")).toBe(original.run_id);
  });
  it("never substitutes the latest run when the requested historical content returns 404",async()=>{
    locate();mocks.detail.mockRejectedValue(new Error("404 原内容已变化"));
    render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText(/指定原批次读取失败.*404 原内容已变化/)).toBeTruthy();
    expect(screen.queryByText("原落选股")).toBeNull();
    expect(mocks.detail).toHaveBeenCalledTimes(1);expect(mocks.detail.mock.calls[0][0].run_id).toBe(original.run_id);
    expect(new URLSearchParams(window.location.search).get("run_id")).toBe(original.run_id);
  });
  it.each([{trade_date:"2026-10-23"},{content_version:"c".repeat(64)},{source_version:"different-source"},{method:"intraday_watch"}])("rejects an identity-mismatched detail $trade_date $source_version $method",async patch=>{
    locate();mocks.detail.mockResolvedValue({...detail(),...patch});render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText(/返回的日期、来源版本或内容指纹不符/)).toBeTruthy();
    expect(screen.queryByText("原落选股")).toBeNull();
  });
  it("keeps an incomplete exact URL unresolved instead of automatically choosing the newest run",async()=>{
    window.history.replaceState({},"","/agent?area=research&tab=strategies&method=daily_picks&run_id=original-22&date=2026-10-22");
    render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText(/原批次链接缺少有效日期/)).toBeTruthy();
    await waitFor(()=>expect(mocks.index).toHaveBeenCalledTimes(1));expect(mocks.detail).not.toHaveBeenCalled();
  });
  it("reads unknown original source versions by explicit missing-state identity",async()=>{
    const legacy={...original,source_version:null,version_state:"legacy" as const};locate(legacy);mocks.detail.mockResolvedValue(detail(legacy));
    render(<MethodRunReader method="daily_picks"/>);await screen.findByText("原落选股");
    expect(mocks.index.mock.calls[0][0]).toMatchObject({source_version_missing:true,source_version:undefined});
    expect(mocks.detail.mock.calls[0][0].source_version).toBeNull();
    expect(screen.getByText(/旧档没有完整方法或来源版本/)).toBeTruthy();
    expect(new URLSearchParams(window.location.search).get("source_version_missing")).toBe("true");
  });
  it("scrolls beyond the first 200 stored rows with the same digest and retains original diagnostics",async()=>{
    locate();
    const first=Array.from({length:200},(_,i)=>({...row,item_id:`selected:${i}`,name:`已读入选${i}`,state:"selected" as const}));
    mocks.detail.mockImplementation(async(identity:SavedMethodRunIdentity,{offset=0}:{offset?:number})=>offset===0?detail(identity,first,0,201):detail(identity,[{...row,item_id:"rejected:200",name:"第201原落选"}],200,201));
    render(<MethodRunReader method="daily_picks"/>);
    await screen.findByRole("button",{name:"继续读取原批次"});
    fireEvent.scroll(window);
    expect(await screen.findByText("第201原落选")).toBeTruthy();
    expect(mocks.detail.mock.calls[1][0]).toMatchObject(original);expect(mocks.detail.mock.calls[1][1]).toMatchObject({offset:200,limit:200});
    expect(screen.getByText(/本批已读 201 \/ 201 条原行/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"核对第201原落选原评分与缺项"}));
    expect(screen.getByText("原财务否决")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"原入选"}));
    expect(screen.getByText("已读入选0")).toBeTruthy();expect(mocks.detail).toHaveBeenCalledTimes(2);
  });
  it.each([201,401])("does not lose the first scroll after each continuation control mounts for %i original rows",async total=>{
    locate();
    const first=Array.from({length:200},(_,i)=>({...row,item_id:`selected:${i}`,name:`已读入选${i}`,state:"selected" as const}));
    mocks.detail.mockImplementation(async(identity:SavedMethodRunIdentity,{offset=0}:{offset?:number})=>
      offset===total-1?detail(identity,[{...row,item_id:`rejected:${offset}`,name:"挂载后原落选"}],offset,total)
        :detail(identity,first.map((item,index)=>({...item,item_id:`selected:${offset+index}`})),offset,total));
    const scrolledOffsets=new Set<number>();
    const observer=new MutationObserver(()=>{
      if(!Array.from(document.querySelectorAll("button")).some(button=>button.textContent==="继续读取原批次" && !button.disabled))return;
      const read=Array.from(document.querySelectorAll("p")).map(paragraph=>paragraph.textContent?.match(/^本批已读 (\d+) \/ /)).find(Boolean);
      const offset=read?Number(read[1]):null;
      if(offset===null || scrolledOffsets.has(offset))return;
      scrolledOffsets.add(offset);
      fireEvent.scroll(window);
    });
    observer.observe(document.body,{childList:true,characterData:true,subtree:true});
    try {
      render(<MethodRunReader method="daily_picks"/>);
      expect(await screen.findByText("挂载后原落选")).toBeTruthy();
      const offsets=total===201?[200]:[200,400];
      expect([...scrolledOffsets]).toEqual(offsets);
      expect(mocks.detail).toHaveBeenCalledTimes(offsets.length+1);
      for(const [index,offset] of offsets.entries()) {
        expect(mocks.detail.mock.calls[index+1][0]).toMatchObject(original);
        expect(mocks.detail.mock.calls[index+1][1]).toMatchObject({offset,limit:200});
      }
    } finally {observer.disconnect();}
  });
  it("rebinds remote continuation to a newly scrollable parent after filtering without relying on event bubbling",async()=>{
    locate();
    const first=[row,...Array.from({length:4},(_,index)=>({...row,item_id:`candidate:${index}`,state:"candidate" as const,name:`原候选${index}`}))];
    mocks.detail.mockImplementation(async(identity:SavedMethodRunIdentity,{offset=0}:{offset?:number})=>offset===0?detail(identity,first,0,6)
      :detail(identity,[{...row,item_id:"candidate:5",state:"candidate",name:"父层滚动续读原行"}],5,6));
    vi.spyOn(HTMLElement.prototype,"scrollHeight","get").mockImplementation(function(this:HTMLElement) {
      return this.dataset.testid==="remote-scroll-owner"?this.querySelectorAll("li").length*40+20:0;
    });
    vi.spyOn(HTMLElement.prototype,"clientHeight","get").mockImplementation(function(this:HTMLElement) {
      return this.dataset.testid==="remote-scroll-owner"?100:0;
    });
    try {
      render(<div data-testid="remote-scroll-owner"><MethodRunReader method="daily_picks"/></div>);
      await screen.findByText("原落选股");
      const owner=screen.getByTestId("remote-scroll-owner");
      expect(owner.scrollHeight).toBeLessThan(owner.clientHeight);
      fireEvent.click(screen.getByRole("button",{name:"候选"}));
      expect(screen.getByText("原候选3")).toBeTruthy();
      expect(owner.scrollHeight).toBeGreaterThan(owner.clientHeight);
      fireEvent.scroll(owner,{bubbles:false});
      expect(await screen.findByText("父层滚动续读原行")).toBeTruthy();
      expect(mocks.detail).toHaveBeenCalledTimes(2);
      expect(mocks.detail.mock.calls[1][0]).toMatchObject(original);
      expect(mocks.detail.mock.calls[1][1]).toMatchObject({offset:5,limit:200});
    } finally {vi.restoreAllMocks();}
  });
  it("retains read rows when another page fails and refuses a changed-content retry",async()=>{
    locate();mocks.detail.mockResolvedValueOnce(detail(original,[row],0,2)).mockRejectedValueOnce(new Error("page offline"))
      .mockResolvedValueOnce({...detail(original,[{...row,item_id:"rejected:1",name:"漂移原行"}],1,2),content_version:"d".repeat(64)});
    render(<MethodRunReader method="daily_picks"/>);await screen.findByText("原落选股");
    fireEvent.click(screen.getByRole("button",{name:"继续读取原批次"}));
    expect(await screen.findByText(/后续页读取失败：page offline/)).toBeTruthy();expect(screen.getByText("原落选股")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"重试原批次后续行"}));
    expect(await screen.findByText(/后续页的原批次身份不符/)).toBeTruthy();expect(screen.queryByText("漂移原行")).toBeNull();
    expect(screen.getByText("原落选股")).toBeTruthy();expect(mocks.detail).toHaveBeenCalledTimes(3);
  });
  it("restores date, batch, filter and original row on browser history without rereading current lists",async()=>{
    locate(newer);render(<MethodRunReader method="daily_picks"/>);await screen.findByText("原落选股");
    locate(original,"rejected","rejected:0");fireEvent.popState(window);
    expect(await screen.findByRole("region",{name:"原落选股的原评分诊断"})).toBeTruthy();
    expect(new URLSearchParams(window.location.search).get("run_row")).toBe("rejected:0");
    expect(screen.getByRole("button",{name:"拒绝存档"}).getAttribute("aria-pressed")).toBe("true");
    expect(mocks.detail.mock.calls.at(-1)?.[0]).toMatchObject(original);
    const close=screen.getByRole("button",{name:"收起原落选股原评分与缺项"});
    fireEvent.keyDown(screen.getByRole("region",{name:"原落选股的原评分诊断"}),{key:"Escape"});
    expect(document.activeElement).toBe(close);expect(new URLSearchParams(window.location.search).has("run_row")).toBe(false);
  });
  it("keeps an empty archive distinct from proof that a date had no selection",async()=>{
    mocks.index.mockResolvedValue({method:"daily_picks",items:[],coverage:{limit:30,possibly_truncated:false,history_semantics:"原保存范围"}});
    render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText(/所查扫描范围没有可读取的保存批次；不证明当日未运行或零入选/)).toBeTruthy();
    expect(mocks.detail).not.toHaveBeenCalled();
  });
  it("locks an automatically selected original run when a later index refresh discovers a newer batch",async()=>{
    mocks.index.mockResolvedValueOnce({method:"daily_picks",items:[original],coverage:{limit:30,possibly_truncated:false,history_semantics:"原范围"}})
      .mockResolvedValue({method:"daily_picks",items:[newer,original],coverage:{limit:30,possibly_truncated:false,history_semantics:"原范围"}});
    mocks.detail.mockImplementation(async(identity:SavedMethodRunIdentity)=>detail(identity,[{...row,state:"selected",name:identity.run_id===original.run_id?"首次锁定原行":"自动漂到新行"}]));
    render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText("首次锁定原行")).toBeTruthy();
    fireEvent(document,new Event("visibilitychange"));
    await waitFor(()=>expect(mocks.index).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("自动漂到新行")).toBeNull();expect(screen.getByText("首次锁定原行")).toBeTruthy();
    expect(new URLSearchParams(window.location.search).get("run_id")).toBe(original.run_id);
    expect(mocks.detail.mock.calls.every(([identity])=>identity.run_id===original.run_id)).toBe(true);
  });
  it("continues an empty index scan without calling the unread range empty",async()=>{
    mocks.index.mockResolvedValueOnce({method:"daily_picks",items:[],coverage:{offset:0,limit:30,scanned:30,next_offset:30,possibly_truncated:true,history_semantics:"原范围"}})
      .mockResolvedValueOnce({method:"daily_picks",items:[original],coverage:{offset:30,limit:30,scanned:1,next_offset:null,possibly_truncated:false,history_semantics:"原范围"}});
    mocks.detail.mockResolvedValue(detail(original,[{...row,state:"selected",name:"较早已存原行"}]));
    render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText(/本次扫描没有匹配批次，仍有未读范围/)).toBeTruthy();
    expect(screen.queryByText(/所查扫描范围没有可读取的保存批次/)).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"继续读取更早批次"}));
    expect(await screen.findByText("较早已存原行")).toBeTruthy();
    expect(mocks.index.mock.calls[1][0]).toMatchObject({offset:30,method:"daily_picks"});
    expect(mocks.detail.mock.calls[0][0]).toMatchObject(original);
  });
  it("does not lose the first scroll after each earlier-index control mounts or reuse the previous cursor",async()=>{
    mocks.index.mockResolvedValueOnce({method:"daily_picks",items:[],coverage:{offset:0,limit:30,scanned:30,next_offset:30,possibly_truncated:true,history_semantics:"原范围"}})
      .mockResolvedValueOnce({method:"daily_picks",items:[original],coverage:{offset:30,limit:30,scanned:30,next_offset:60,possibly_truncated:true,history_semantics:"原范围"}})
      .mockResolvedValueOnce({method:"daily_picks",items:[newer],coverage:{offset:60,limit:30,scanned:1,next_offset:null,possibly_truncated:false,history_semantics:"原范围"}});
    mocks.detail.mockResolvedValue(detail(original,[{...row,state:"selected",name:"首批锁定原行"}]));
    const scrolledCounts=new Set<number>();
    const observer=new MutationObserver(()=>{
      if(!Array.from(document.querySelectorAll("button")).some(button=>button.textContent==="继续读取更早批次" && !button.disabled))return;
      const read=Array.from(document.querySelectorAll("p")).map(paragraph=>paragraph.textContent?.match(/^已读索引 (\d+) 批/)).find(Boolean);
      const count=read?Number(read[1]):null;
      if(count===null || scrolledCounts.has(count))return;
      scrolledCounts.add(count);
      fireEvent.scroll(window);
    });
    observer.observe(document.body,{childList:true,characterData:true,subtree:true});
    try {
      render(<MethodRunReader method="daily_picks"/>);
      expect(await screen.findByText(/已读索引 2 批。原范围/)).toBeTruthy();
      expect([...scrolledCounts]).toEqual([0,1]);
      expect(mocks.index.mock.calls.map(([query])=>query.offset)).toEqual([undefined,30,60]);
      expect(mocks.index.mock.calls.every(([query])=>query.method==="daily_picks")).toBe(true);
      expect(await screen.findByText("首批锁定原行")).toBeTruthy();
      expect(mocks.detail).toHaveBeenCalledTimes(1);
      expect(mocks.detail.mock.calls[0][0]).toMatchObject(original);
      expect(new URLSearchParams(window.location.search).get("run_id")).toBe(original.run_id);
    } finally {observer.disconnect();}
  });
  it("labels a daily rejected row's saved rank without calling it an intraday stage",async()=>{
    locate(original,"rejected","rejected:0");mocks.detail.mockResolvedValue(detail(original,[{...row,rank:1}]));
    render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByRole("region",{name:"原排名与阶段"})).toBeTruthy();
    expect(screen.queryByText("原盘中阶段与容量")).toBeNull();
  });
  it("states confirmed zero selections from a complete original source summary",async()=>{
    locate(original,"selected");mocks.detail.mockResolvedValue(detail(original,Array.from({length:20},(_,index)=>({...row,item_id:`rejected:${index}`}))));
    render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText("原批次已保存：0只入选。")).toBeTruthy();
    expect(screen.queryByText(/不据此推断当时零入选/)).toBeNull();
    expect(screen.getByText(/本批已读 20 \/ 20 条原行/)).toBeTruthy();
  });
  it.each([
    {label:"unknown original selected count",patch:{counts:{selected:null}}},
    {label:"partially readable archive",patch:{state:"partial" as const,reason:"原名单缺失"}},
  ])("does not claim zero selections for $label",async({patch})=>{
    locate(original,"selected");mocks.detail.mockResolvedValue({...detail(),...patch});render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText(/不据此推断当时零入选/)).toBeTruthy();
    expect(screen.queryByText("原批次已保存：0只入选。")).toBeNull();
  });
  it("retains the source-confirmed zero while reading later original pages",async()=>{
    locate(original,"selected");mocks.detail.mockResolvedValueOnce(detail(original,[row],0,2))
      .mockResolvedValueOnce(detail(original,[{...row,item_id:"rejected:1"}],1,2));
    render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText("原批次已保存：0只入选。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"继续读取原批次"}));
    expect(await screen.findByText("原批次已保存：0只入选。")).toBeTruthy();
    expect(screen.queryByText(/不据此推断当时零入选/)).toBeNull();
  });
  it.each([
    {method:"daily_picks" as const,state:"saved" as const},{method:"intraday_watch" as const,state:"saved" as const},
    {method:"daily_picks" as const,state:"partial" as const},{method:"intraday_watch" as const,state:"partial" as const},
  ])("uses the $method full source summary independently of 200/960 client rows when $state",async({method,state})=>{
    const identity={...original,method};locate(identity,"selected");
    const first=Array.from({length:200},(_,index)=>({...row,item_id:`rejected:${index}`}));
    mocks.detail.mockResolvedValue({...detail(identity,first,0,960),state,possibly_truncated:true,reason:state==="partial"?"原排序源记录缺失":""});
    render(<MethodRunReader method={method}/>);
    expect(await screen.findByText(/本批已读 200 \/ 960 条原行/)).toBeTruthy();
    expect(screen.getByText("原存档投影可能截断，不按当前行数替换原计数。")).toBeTruthy();
    if(state==="saved") {
      expect(screen.getByText("原批次已保存：0只入选。")).toBeTruthy();
      expect(screen.queryByText(/不据此推断当时零入选/)).toBeNull();
    } else {
      expect(screen.queryByText("原批次已保存：0只入选。")).toBeNull();
      expect(screen.getByText(/不据此推断当时零入选/)).toBeTruthy();
    }
  });
  it("withdraws zero confirmation if a later page contradicts the original source summary",async()=>{
    locate(original,"selected");mocks.detail.mockResolvedValueOnce(detail(original,[row],0,2))
      .mockResolvedValueOnce({...detail(original,[{...row,item_id:"rejected:1"}],1,2),counts:{selected:1}});
    render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText("原批次已保存：0只入选。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"继续读取原批次"}));
    expect(await screen.findByText(/不据此推断当时零入选/)).toBeTruthy();
    expect(screen.queryByText("原批次已保存：0只入选。")).toBeNull();
  });
  it.each(["saved","partial"] as const)("retires the first-page truncation warning after over 200 %s rows without hiding source gaps",async state=>{
    locate(original,"selected");
    const first=Array.from({length:200},(_,index)=>({...row,item_id:`rejected:${index}`,name:index===0?"首屏已读原行":`原落选${index}`}));
    const reason=state==="partial"?"原来源记录缺失":"";
    mocks.detail.mockResolvedValueOnce({...detail(original,first,0,201),state,reason,possibly_truncated:true})
      .mockResolvedValueOnce({...detail(original,[{...row,item_id:"rejected:200"}],200,201),state,reason,possibly_truncated:false});
    render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText("原存档投影可能截断，不按当前行数替换原计数。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"继续读取原批次"}));
    expect(await screen.findByText(/本批已读 201 \/ 201 条原行/)).toBeTruthy();
    expect(screen.queryByText("原存档投影可能截断，不按当前行数替换原计数。")).toBeNull();
    if(state==="partial")expect(screen.getByText(/部分存档不可读取：原来源记录缺失/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"拒绝存档"}));
    expect(screen.getByText("首屏已读原行")).toBeTruthy();expect(mocks.detail).toHaveBeenCalledTimes(2);
  });
  it("uses the external Next history path instead of replaying its private bypass markers",async()=>{
    window.history.replaceState({__NA:true,__PRIVATE_NEXTJS_INTERNALS_TREE:{tree:"retained-by-next"}},"",window.location.href);
    const replace=vi.spyOn(window.history,"replaceState"),push=vi.spyOn(window.history,"pushState");
    try {
      render(<MethodRunReader method="daily_picks"/>);
      await screen.findByRole("button",{name:"拒绝存档"});
      expect(replace).toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button",{name:"拒绝存档"}));
      expect(push).toHaveBeenCalled();
      for(const [state] of [...replace.mock.calls,...push.mock.calls]) {
        expect(state).not.toHaveProperty("__NA");expect(state).not.toHaveProperty("_N");
      }
      expect(new URLSearchParams(window.location.search).get("run_state")).toBe("rejected");
    } finally {replace.mockRestore();push.mockRestore();}
  });
  it("does not overwrite a traversal target during an unrelated parent render before popstate sync",async()=>{
    locate(original,"rejected","rejected:0");const previousUrl=window.location.pathname+window.location.search;
    const view=render(<MethodRunReader method="daily_picks"/>);
    await screen.findByRole("region",{name:"原落选股的原评分诊断"});
    fireEvent.click(screen.getByRole("button",{name:"原入选"}));
    await screen.findByText("原批次已保存：0只入选。");
    window.history.replaceState({},"",previousUrl);
    view.rerender(<MethodRunReader method="daily_picks"/>);
    expect(window.location.pathname+window.location.search).toBe(previousUrl);
    fireEvent.popState(window);
    expect(await screen.findByRole("region",{name:"原落选股的原评分诊断"})).toBeTruthy();
  });
  it("restores the exact rejected row with native browser Back and returns to selected with Forward",async()=>{
    locate(original,"rejected","rejected:0");render(<MethodRunReader method="daily_picks"/>);
    await screen.findByRole("region",{name:"原落选股的原评分诊断"});
    fireEvent.click(screen.getByRole("button",{name:"原入选"}));
    await screen.findByText("原批次已保存：0只入选。");
    await act(async()=>{await new Promise<void>(resolve=>{window.addEventListener("popstate",()=>resolve(),{once:true});window.history.back();});});
    expect(await screen.findByRole("region",{name:"原落选股的原评分诊断"})).toBeTruthy();
    expect(new URLSearchParams(window.location.search).get("run_state")).toBe("rejected");
    expect(new URLSearchParams(window.location.search).get("run_row")).toBe("rejected:0");
    await act(async()=>{await new Promise<void>(resolve=>{window.addEventListener("popstate",()=>resolve(),{once:true});window.history.forward();});});
    expect(await screen.findByText("原批次已保存：0只入选。")).toBeTruthy();
    expect(new URLSearchParams(window.location.search).has("run_state")).toBe(false);
    expect(screen.queryByRole("region",{name:"原落选股的原评分诊断"})).toBeNull();
  });
});
