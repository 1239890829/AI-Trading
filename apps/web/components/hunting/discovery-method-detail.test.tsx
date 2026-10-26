import {useCallback, useEffect, useState} from "react";
import {act, cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import type {SavedMethodRunDetail, SavedMethodRunIdentity, SavedMethodRunIndex, StrategyRegistryItem} from "@/lib/api/picks";
import {REFERENCE_RESEARCH_METHODS} from "@/lib/api/picks";
import type {AgentTask} from "@/lib/api/agent";
import {DiscoveryMethods, discoveryMethodScope} from "./discovery-methods";
import {DiscoveryMethodDetail} from "./discovery-method-detail";

const mocks = vi.hoisted(() => ({registry:vi.fn(), index:vi.fn(), detail:vi.fn(), create:vi.fn(), task:vi.fn(), access:vi.fn()}));
vi.mock("@/lib/api/picks", async () => ({...await vi.importActual<typeof import("@/lib/api/picks")>("@/lib/api/picks"),
  getStrategyRegistry:mocks.registry, getStrategyMethodRuns:mocks.index, getStrategyMethodRun:mocks.detail}));
vi.mock("@/lib/api/agent", () => ({createAgentTask:mocks.create, getAgentTask:mocks.task}));
vi.mock("@/components/access/device-access", () => ({useDeviceAccess:mocks.access}));

const method = "reference:macd_golden_cross";
const version = "ashare-macd-12-26-9-v1";
const row: StrategyRegistryItem = {key:method,catalog_kind:"reference",name:"MACD金叉",status:"research_ready",
  candidate:{source_label:"TSP来源目录",source_code:"macd_golden_cross",source_version:"20261025:v4",asset:"stock",
    criteria:["原MACD金叉"],existing_coverage:["原日线读取"],limitations:["非生产入选"],runnable:true,
    execution:{mode:"manual_research",version,definition_digest:"a".repeat(64),production_enabled:false,
      definition:"前日DIF≤DEA且当日DIF>DEA",definition_supplement:"至少60根日线，本系统v1"}},
  discovery:{category:"trend",category_label:"趋势量价",session:"after_hours",session_label:"盘后参考",summary:"手动价格研究",supports_saved_runs:true}};
const identity:SavedMethodRunIdentity={method,run_id:"saved-research-23",trade_date:"2026-10-23",source_version:"source-23",
  strategy_version:version,content_version:"b".repeat(64),state:"saved"};
const index:SavedMethodRunIndex={method,items:[identity],coverage:{offset:0,limit:30,total:1,returned:1,next_offset:null,
  possibly_truncated:false,history_semantics:"测试保存索引"}};
const detail:SavedMethodRunDetail={...identity,row_state:"all",items:[],counts:{universe:0,eligible:0,matched:0,not_matched:0,unknown:0},
  coverage:{offset:0,limit:200,total:0,batch_total:0,returned:0,next_offset:null,possibly_truncated:false,history_semantics:"测试原研究批次"}};

/** Same scope/date hand-off as HuntingPage; history updates stand in for Next's patched History API. */
function RoutedMethods() {
  const [search,setSearch]=useState(window.location.search);
  const [resolved,setResolved]=useState<{scope:string;row:StrategyRegistryItem|null}|null>(null);
  useEffect(()=>{const sync=()=>setSearch(window.location.search);window.addEventListener("test-route-update",sync);return()=>window.removeEventListener("test-route-update",sync);},[]);
  const params=new URLSearchParams(search),date=params.get("date")??undefined;
  const scope=discoveryMethodScope(date,params.get("disc_method")??"");
  const target=resolved?.scope===scope?resolved.row:null;
  const resolve=useCallback((scope:string,row:StrategyRegistryItem|null)=>setResolved(previous=>previous?.scope===scope&&previous.row===row?previous:{scope,row}),[]);
  return <><DiscoveryMethods search={search} date={date} onMethodTarget={resolve}/>
    {target&&<DiscoveryMethodDetail key={target.key} row={target} date={date} search={search} onClose={()=>{}}/>}</>;
}

beforeEach(()=>{
  window.localStorage.clear();
  window.history.replaceState({__NA:true,__PRIVATE_NEXTJS_INTERNALS_TREE:{tree:"test-next-owner"}},"",`/hunting?view=discover&browse=methods&disc_method=${encodeURIComponent(method)}`);
  const replace=window.history.replaceState.bind(window.history);
  vi.spyOn(window.history,"replaceState").mockImplementation((state,unused,url)=>{
    // Match installed Next's private-marker bypass and external-history path.
    if(state?.__NA || state?._N){replace(state,unused,url);return;}
    replace({...state,__NA:true,__PRIVATE_NEXTJS_INTERNALS_TREE:window.history.state?.__PRIVATE_NEXTJS_INTERNALS_TREE},unused,url);
    window.dispatchEvent(new Event("test-route-update"));
  });
  mocks.registry.mockReset().mockResolvedValue([row]);mocks.index.mockReset().mockResolvedValue(index);mocks.detail.mockReset().mockResolvedValue(detail);
  mocks.create.mockReset();mocks.task.mockReset();
  mocks.access.mockReset().mockReturnValue({device:{state:"active",maintenance:false},open:vi.fn(),refresh:vi.fn()});
});
afterEach(()=>{cleanup();vi.restoreAllMocks();});

it("reused capabilities have a real internal reading entry without starting research",()=>{
  const reused: StrategyRegistryItem = {...row,key:"reference:hot_theme",name:"热点题材",status:"candidate",
    candidate:{...row.candidate!,source_code:"hot_theme",runnable:false,execution:undefined,
      adoption:{state:"reuse_existing",consumer:"themes",reason:"复用既有正式题材",href:"/hunting?browse=hot"}}};
  render(<DiscoveryMethodDetail row={reused} onClose={()=>{}}/>);
  expect(screen.getByRole("link",{name:"打开已有热点题材能力"}).getAttribute("href")).toBe("/hunting?browse=hot");
  expect(mocks.create).not.toHaveBeenCalled();expect(mocks.index).not.toHaveBeenCalled();
});

it("never turns an unrecognized capability URL into a navigation action",()=>{
  const unsafe: StrategyRegistryItem = {...row,key:"reference:hot_theme",name:"热点题材",status:"candidate",
    candidate:{...row.candidate!,source_code:"hot_theme",runnable:false,execution:undefined,
      adoption:{state:"reuse_existing",reason:"bad reference",href:"javascript:alert(1)"}}};
  render(<DiscoveryMethodDetail row={unsafe} onClose={()=>{}}/>);
  expect(screen.queryByRole("link",{name:"打开已有热点题材能力"})).toBeNull();
});

it("keeps the user's first saved-result disclosure open after the real reader pins a date and exact run through a scope remount",async()=>{
  let finish!:(value:SavedMethodRunIndex)=>void;
  mocks.index.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  render(<RoutedMethods/>);
  const summary=await screen.findByText("保存研究结果");
  expect(summary.closest("details")?.open).toBe(false);
  expect(mocks.index).not.toHaveBeenCalled();expect(mocks.detail).not.toHaveBeenCalled();
  fireEvent.click(summary);
  await screen.findByText("读取保存运行索引…");
  await act(async()=>finish(index));
  await waitFor(()=>expect(new URLSearchParams(window.location.search).get("run_id")).toBe(identity.run_id));
  expect(new URLSearchParams(window.location.search).get("date")).toBe(identity.trade_date);
  expect(new URLSearchParams(window.location.search).get("content_version")).toBe(identity.content_version);
  expect(new URLSearchParams(window.location.search).get("disc_saved")).toBe(JSON.stringify([method,version]));
  expect(screen.getByText("保存研究结果").closest("details")?.open).toBe(true);
  expect(await screen.findByLabelText("原保存运行")).toBeTruthy();
  expect(mocks.detail.mock.calls.at(-1)?.[0]).toEqual(identity);
  expect(mocks.create).not.toHaveBeenCalled();expect(mocks.task).not.toHaveBeenCalled();
  expect(window.history.state.__PRIVATE_NEXTJS_INTERNALS_TREE).toEqual({tree:"test-next-owner"});
  expect(vi.mocked(window.history.replaceState).mock.calls.every(([state])=>!state?.__NA&&!state?._N)).toBe(true);
});

it.each(["absent", "incomplete", "foreign_method", "foreign_version", "unbound_value"])("%s display intent cannot turn a complete run URL into an automatic saved-result read",async kind=>{
  const params=new URLSearchParams(window.location.search);
  params.set("date",identity.trade_date);params.set("method",method);params.set("run_id",identity.run_id);
  params.set("source_version",identity.source_version!);params.set("content_version",identity.content_version);
  if(kind!=="absent")params.set("disc_saved",kind==="incomplete"?JSON.stringify([method]):kind==="foreign_method"?JSON.stringify(["reference:ma_bullish",version]):kind==="foreign_version"?JSON.stringify([method,"old-version"]):"1");
  window.history.replaceState({},"",`/hunting?${params}`);
  render(<RoutedMethods/>);
  expect((await screen.findByText("保存研究结果")).closest("details")?.open).toBe(false);
  expect(screen.queryByLabelText("选股历史运行")).toBeNull();
  expect(mocks.index).not.toHaveBeenCalled();expect(mocks.detail).not.toHaveBeenCalled();
  expect(mocks.create).not.toHaveBeenCalled();expect(mocks.task).not.toHaveBeenCalled();
});

it("manual collapse removes the owner marker and a registry failure/recovery remount stays closed without new saved reads",async()=>{
  const view=render(<RoutedMethods/>);
  fireEvent.click(await screen.findByText("保存研究结果"));
  await screen.findByLabelText("原保存运行");
  await waitFor(()=>expect(screen.getByText("保存研究结果").closest("details")?.open).toBe(true));
  const reads={index:mocks.index.mock.calls.length,detail:mocks.detail.mock.calls.length};
  fireEvent.click(screen.getByText("保存研究结果"));
  await waitFor(()=>expect(new URLSearchParams(window.location.search).has("disc_saved")).toBe(false));
  expect(screen.getByText("保存研究结果").closest("details")?.open).toBe(false);
  mocks.registry.mockRejectedValueOnce(new Error("fixture registry unavailable"));
  fireEvent.click(screen.getByRole("button",{name:"刷新方法"}));
  await screen.findByRole("alert");
  await waitFor(()=>expect(screen.queryByTestId("discovery-method-detail")).toBeNull());
  fireEvent.click(screen.getByRole("button",{name:"刷新方法"}));
  expect((await screen.findByText("保存研究结果")).closest("details")?.open).toBe(false);
  expect(mocks.index).toHaveBeenCalledTimes(reads.index);expect(mocks.detail).toHaveBeenCalledTimes(reads.detail);
  const replacements=vi.mocked(window.history.replaceState).mock.calls.length;
  view.rerender(<RoutedMethods/>);await act(async()=>{});
  expect(window.history.replaceState).toHaveBeenCalledTimes(replacements);
  expect(mocks.create).not.toHaveBeenCalled();expect(mocks.task).not.toHaveBeenCalled();
});

it("a registry execution-version change cannot carry the previous version's open marker",async()=>{
  render(<RoutedMethods/>);
  fireEvent.click(await screen.findByText("保存研究结果"));await screen.findByLabelText("原保存运行");
  const reads={index:mocks.index.mock.calls.length,detail:mocks.detail.mock.calls.length};
  const version2="ashare-macd-12-26-9-v2";
  mocks.registry.mockResolvedValueOnce([{...row,candidate:{...row.candidate!,execution:{...row.candidate!.execution!,version:version2}}}]);
  fireEvent.click(screen.getByRole("button",{name:"刷新方法"}));
  await screen.findByText(new RegExp(`本系统明确版本 ${version2}`));
  expect(screen.getByText("保存研究结果").closest("details")?.open).toBe(false);
  expect(screen.queryByLabelText("选股历史运行")).toBeNull();
  expect(mocks.index).toHaveBeenCalledTimes(reads.index);expect(mocks.detail).toHaveBeenCalledTimes(reads.detail);
});

it("completion of one explicitly submitted mocked task carries its saved reader through the same date pin, without resubmitting",async()=>{
  mocks.access.mockReturnValue({device:{state:"active",maintenance:true},open:vi.fn(),refresh:vi.fn()});
  const task:AgentTask={id:"1".repeat(32),type:"reference_strategies",status:"queued",params:{},steps:[],error:null,risk_level:"L0",created_by:"maintenance",created_at:null,started_at:null,finished_at:null,result_ref:null};
  mocks.create.mockResolvedValue(task);
  mocks.task.mockResolvedValue({...task,status:"succeeded",result_ref:{kind:"reference_strategies",reused:true,
    runs:REFERENCE_RESEARCH_METHODS.map(key=>({...identity,method:key,strategy_version:key===method?version:"other-bound-method-version"}))}});
  render(<RoutedMethods/>);
  await screen.findByText("保存研究结果");
  expect(mocks.create).not.toHaveBeenCalled();expect(mocks.task).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button",{name:"运行研究筛选"}));
  await screen.findByLabelText("原保存运行");
  expect(new URLSearchParams(window.location.search).get("disc_saved")).toBe(JSON.stringify([method,version]));
  expect(new URLSearchParams(window.location.search).get("run_id")).toBe(identity.run_id);
  expect(screen.getByText("保存研究结果").closest("details")?.open).toBe(true);
  expect(mocks.create).toHaveBeenCalledExactlyOnceWith("reference_strategies",{});
  expect(mocks.task).toHaveBeenCalledExactlyOnceWith(task.id);
});
