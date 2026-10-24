import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {getNotifications, getStockAttribution, type NotificationItem, type NotificationsPayload} from "@/lib/api";
import {NotificationBell} from "./notification-drawer";
import {__resetPrefsCache} from "@/lib/notification-read";
import {SymbolDetailCtx} from "@/components/detail/symbol-detail-context";
import type {TradingAgentsResearchView} from "@/lib/api/agent";
import {InspectionContext} from "@/components/inspection/inspection-context";

const savedResearch=(symbol:string):TradingAgentsResearchView=>({symbol,startup_conditions_not_checked:true,enabled:false,executable:false,blockers:[],required_model_slots:null,budget:null,admission:{state:"not_admitted",method_sha256:null},verification:"unverified_full_graph_research",production_eligible:false,latest:null,task:null});
let compact = false;
let payload: NotificationsPayload;
const research = vi.hoisted(() => ({read:vi.fn(), start:vi.fn()}));
const destinations = vi.hoisted(() => ({quote:vi.fn(), inspection:vi.fn()}));
vi.mock("@/components/news-modal", () => ({NewsModal:({item}: {item:{title:string} | null}) => item ? <section aria-label="原文阅读">{item.title}</section> : null}));
vi.mock("@/lib/api/agent", async () => ({...await vi.importActual<typeof import("@/lib/api/agent")>("@/lib/api/agent"), getTradingAgentsResearch:research.read, createAgentTask:research.start}));
vi.mock("@/lib/api", async () => ({
  ...await vi.importActual<typeof import("@/lib/api")>("@/lib/api"),
  getNotifications:vi.fn(async () => payload),
  getStockAttribution:vi.fn(async (symbol:string) => ({schema_version:1,symbol,count:0,items:[]})),
  getNotificationReadState:vi.fn(async () => {throw new Error("isolated");}),
  saveNotificationReadState:vi.fn(async () => {throw new Error("isolated");}),
}));
function item(id:string, symbol:string): NotificationItem {
  return {id,symbol,category:"opportunity",label:"入选观察",session:"intraday",ts:"2026-10-23T02:00:00Z",title:`原入选${id}`,body:`${id}原依据，订单尚未确认。`,validity:"原消息撤回即失效。",url:null,score:null,
    references:[{event_id:id === "A" ? 11 : 12,kind:"selection",selection_source:"daily",trade_date:"2026-10-23",source_id:`daily:${id}`,source_version:`version:${id}`,source_as_of:"2026-10-23T01:59:00Z",recorded_at:"2026-10-23T02:00:00Z",
      evidence:{pick_basis:`${id}原选择依据，订单尚未确认。`,sub_scores:{news:82,tech:0},dimension_evidence:{news:{state:"success"},tech:{state:"missing"}},bases:{news:"保存的事件依据",tech:"价格资料缺失"}}}]};
}
async function open() {
  render(<SymbolDetailCtx.Provider value={{open:destinations.quote,close:vi.fn()}}><InspectionContext.Provider value={{open:destinations.inspection,close:vi.fn()}}><NotificationBell/></InspectionContext.Provider></SymbolDetailCtx.Provider>);
  const trigger=screen.getByRole("button",{name:/打开通知中心/});trigger.focus();fireEvent.click(trigger);
  await screen.findAllByTestId("notification-row");
  return trigger;
}
beforeEach(() => {
  compact=false;localStorage.clear();__resetPrefsCache();
  research.read.mockImplementation(async (symbol:string)=>savedResearch(symbol));
  payload={items:[item("A","600127"),item("B","600519")],count:2,generated_at:"2026-10-23T02:01:00Z",news_min_score:60,policy:"stock_opportunities_only",errors:null};
  vi.stubGlobal("matchMedia",(query:string)=>({matches:query.includes("max-width") && compact,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
});
afterEach(()=>{cleanup();vi.clearAllMocks();vi.unstubAllGlobals();});

it("restores the original narrow list cards and opens the original detail drawer only for a selected stock",async()=>{
  await open();
  const drawer=screen.getByTestId("notification-drawer");
  expect(drawer.firstElementChild?.className).toContain("max-w-sm");
  expect(screen.queryByTestId("notification-workspace")).toBeNull();
  const rows=screen.getAllByTestId("notification-row");
  expect(rows[0].className).toContain("rounded-lg");
  fireEvent.mouseEnter(rows[0]);fireEvent.focus(rows[0]);
  expect(getStockAttribution).not.toHaveBeenCalled();expect(research.read).not.toHaveBeenCalled();
  fireEvent.click(within(rows[0]).getByTestId("notification-judgment"));
  const detail=await screen.findByRole("dialog",{name:"原入选A"});
  expect(drawer.contains(detail)).toBe(false);
  await waitFor(()=>expect(getStockAttribution).toHaveBeenCalledTimes(1));
  expect(getStockAttribution).toHaveBeenLastCalledWith("600127",{poolDate:undefined,responseAsOf:"2026-10-23T01:59:00Z"});
  fireEvent.click(within(rows[0]).getByTestId("notification-judgment"));
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
  expect(research.read).toHaveBeenCalledExactlyOnceWith("600127",{savedOnly:true});expect(research.start).not.toHaveBeenCalled();
});

it.each([false,true])("preserves keyboard selection, IME and row focus after detail close at compact=%s",async(narrow)=>{
  compact=narrow;await open();
  const rows=screen.getAllByTestId("notification-row");
  const first=within(rows[0]).getByRole("button",{name:/原入选A/});
  first.focus();fireEvent.keyDown(first,{key:"ArrowDown",isComposing:true});
  expect(getStockAttribution).not.toHaveBeenCalled();expect(research.read).not.toHaveBeenCalled();
  fireEvent.keyDown(first,{key:"ArrowDown"});
  const detail=await screen.findByRole("dialog",{name:"原入选B"});
  await waitFor(()=>expect(detail.contains(document.activeElement)).toBe(true));
  fireEvent.click(within(detail).getByRole("button",{name:"关闭"}));
  await waitFor(()=>expect(document.activeElement).toBe(within(rows[1]).getByRole("button",{name:/原入选B/})));
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
  expect(research.read).toHaveBeenCalledExactlyOnceWith("600519",{savedOnly:true});expect(research.start).not.toHaveBeenCalled();
});

it("retains saved evidence and all original conditions in the original report disclosure",async()=>{
  await open();
  const row=screen.getAllByTestId("notification-row")[0];
  expect(row.textContent).toContain("订单尚未确认");
  fireEvent.click(within(row).getByTestId("notification-judgment"));
  const detail=await screen.findByRole("dialog",{name:"原入选A"});
  fireEvent.click(within(detail).getByRole("button",{name:"完整依据与研究记录"}));
  expect(detail.textContent).toContain("价格资料缺失");
  expect(detail.textContent).toContain("原消息撤回即失效");
  expect(detail.textContent).toContain("version:A");
  expect(research.read).toHaveBeenCalledExactlyOnceWith("600127",{savedOnly:true});expect(research.start).not.toHaveBeenCalled();
});

it("keeps a failed refresh visible in the original detail without rereading the company",async()=>{
  await open();
  fireEvent.click(within(screen.getAllByTestId("notification-row")[0]).getByTestId("notification-judgment"));
  const detail=await screen.findByRole("dialog",{name:"原入选A"});
  await waitFor(()=>expect(getStockAttribution).toHaveBeenCalledTimes(1));
  vi.mocked(getNotifications).mockRejectedValueOnce(new Error("offline"));
  fireEvent.click(screen.getByRole("button",{name:"刷新通知",hidden:true}));
  await waitFor(()=>expect(within(detail).getByRole("alert").textContent).toContain("当前通知状态未确认"));
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
});

it.each(["查看行情", "查看原文", "查看此版本复盘"])("detail action %s preserves the original list position and focus",async(action)=>{
  payload.items[0].url="https://example.com/original";
  await open();
  const list=screen.getByTestId("notification-list");
  const first=within(screen.getAllByTestId("notification-row")[0]).getByRole("button",{name:/原入选A/});
  list.scrollTop=420;fireEvent.click(first);
  const detail=await screen.findByRole("dialog",{name:"原入选A"});
  if(action==="查看此版本复盘") fireEvent.click(within(detail).getByRole("button",{name:"完整依据与研究记录"}));
  screen.getByTestId("notification-detail-body").scrollTop=860;
  fireEvent.click(within(detail).getByRole(action==="查看原文"?"button":"link",{name:action}));
  expect(list.scrollTop).toBe(420);
  await waitFor(()=>expect(document.activeElement).toBe(first));
  if(action==="查看行情") expect(destinations.quote).toHaveBeenCalledWith({symbol:"600127",preferModal:true});
  if(action==="查看此版本复盘") expect(destinations.inspection).toHaveBeenCalledWith({kind:"selection-review",date:"2026-10-23",version:"version:A",motionOrigin:null});
  if(action==="查看原文") expect(screen.getByRole("region",{name:"原文阅读"}).textContent).toBe("原入选A");
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
  expect(research.read).toHaveBeenCalledExactlyOnceWith("600127",{savedOnly:true});expect(research.start).not.toHaveBeenCalled();
});

it("pauses the covered list during reading and rebinds its original scroll owner without another request",async()=>{
  payload={...payload,items:Array.from({length:48},(_,index)=>item(`N${index}`,"600127")),count:48};
  let near=false;
  const heights=vi.spyOn(HTMLElement.prototype,"scrollHeight","get").mockImplementation(function(this:HTMLElement){return this.dataset.testid==="notification-list"?2000:0;});
  const clients=vi.spyOn(HTMLElement.prototype,"clientHeight","get").mockImplementation(function(this:HTMLElement){return this.dataset.testid==="notification-list"?300:0;});
  const rectangles=vi.spyOn(HTMLElement.prototype,"getBoundingClientRect").mockImplementation(()=>{
    const top=near?200:5000;return {top,bottom:top+1,left:0,right:100,width:100,height:1,x:0,y:top,toJSON:()=>({})};
  });
  try{
    await open();
    expect(screen.getAllByTestId("notification-row")).toHaveLength(30);
    const list=screen.getByTestId("notification-list");list.scrollTop=420;
    fireEvent.click(within(screen.getAllByTestId("notification-row")[0]).getByTestId("notification-judgment"));
    const detail=await screen.findByRole("dialog",{name:"原入选N0"});
    await waitFor(()=>expect(getStockAttribution).toHaveBeenCalledTimes(1));
    payload={...payload,items:[...payload.items,item("N48","600127")],count:49};
    fireEvent.click(screen.getByRole("button",{name:"刷新通知",hidden:true}));
    await waitFor(()=>expect(list.textContent).toContain("49 条通知"));
    near=true;fireEvent.scroll(list);
    await act(async()=>{});
    expect(screen.getAllByTestId("notification-row")).toHaveLength(30);
    const requests=vi.mocked(getNotifications).mock.calls.length;
    fireEvent.click(within(detail).getByRole("button",{name:"关闭"}));
    fireEvent.scroll(list);
    expect(screen.getAllByTestId("notification-row")).toHaveLength(49);
    expect(list.scrollTop).toBe(420);
    expect(vi.mocked(getNotifications).mock.calls.length).toBe(requests);
    expect(getStockAttribution).toHaveBeenCalledTimes(1);
  }finally{rectangles.mockRestore();clients.mockRestore();heights.mockRestore();}
});

it("reads a selected notification from the current payload while preserving its source identity",async()=>{
  await open();
  fireEvent.click(within(screen.getAllByTestId("notification-row")[0]).getByTestId("notification-judgment"));
  const detail=await screen.findByRole("dialog",{name:"原入选A"});
  payload={...payload,items:[{...payload.items[0],validity:"原来源已撤回，停止沿用。"},payload.items[1]]};
  fireEvent.click(screen.getByRole("button",{name:"刷新通知",hidden:true}));
  await waitFor(()=>expect(detail.textContent).toContain("原来源已撤回，停止沿用。"));
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
});
