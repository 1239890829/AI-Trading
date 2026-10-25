import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import {act,cleanup,fireEvent,render,screen,waitFor,within} from "@testing-library/react";
import type {ReactNode} from "react";
import type {SavedMethodRunDetail,SavedMethodRunIdentity,SavedStrategyRunItem} from "@/lib/api/picks";
import {MethodRunReader} from "./method-run-reader";
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
afterEach(()=>{cleanup();vi.clearAllMocks();mocks.index.mockReset();mocks.detail.mockReset();});

describe("saved method run reader",()=>{
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
  it("labels a daily rejected row's saved rank without calling it an intraday stage",async()=>{
    locate(original,"rejected","rejected:0");mocks.detail.mockResolvedValue(detail(original,[{...row,rank:1}]));
    render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByRole("region",{name:"原排名与阶段"})).toBeTruthy();
    expect(screen.queryByText("原盘中阶段与容量")).toBeNull();
  });
  it("states confirmed zero selections only after reading the entire complete original batch",async()=>{
    locate(original,"selected");mocks.detail.mockResolvedValue(detail(original,Array.from({length:20},(_,index)=>({...row,item_id:`rejected:${index}`}))));
    render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText("原批次已保存：0只入选。")).toBeTruthy();
    expect(screen.queryByText(/不据此推断当时零入选/)).toBeNull();
    expect(screen.getByText(/本批已读 20 \/ 20 条原行/)).toBeTruthy();
  });
  it.each([
    {label:"unknown original selected count",patch:{counts:{selected:null}}},
    {label:"partially readable archive",patch:{state:"partial" as const,reason:"原名单缺失"}},
    {label:"unread later rows",patch:{coverage:{offset:0,limit:200,total:2,returned:1,next_offset:1,possibly_truncated:true,history_semantics:"原范围"}}},
    {label:"missing original total",patch:{coverage:{offset:0,limit:200,total:null,returned:1,next_offset:null,possibly_truncated:false,history_semantics:"原范围"}}},
  ])("does not claim zero selections for $label",async({patch})=>{
    locate(original,"selected");mocks.detail.mockResolvedValue({...detail(),...patch});render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText(/不据此推断当时零入选/)).toBeTruthy();
    expect(screen.queryByText("原批次已保存：0只入选。")).toBeNull();
  });
  it("confirms saved zero only when the last original page completes the bound batch",async()=>{
    locate(original,"selected");mocks.detail.mockResolvedValueOnce(detail(original,[row],0,2))
      .mockResolvedValueOnce(detail(original,[{...row,item_id:"rejected:1"}],1,2));
    render(<MethodRunReader method="daily_picks"/>);
    expect(await screen.findByText(/不据此推断当时零入选/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"继续读取原批次"}));
    expect(await screen.findByText("原批次已保存：0只入选。")).toBeTruthy();
    expect(screen.queryByText(/不据此推断当时零入选/)).toBeNull();
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
