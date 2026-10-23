import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {getNotifications, getStockAttribution, type NotificationItem, type NotificationsPayload} from "@/lib/api";
import {NotificationBell} from "./notification-drawer";
import {__resetPrefsCache} from "@/lib/notification-read";

let compact = false;
let payload: NotificationsPayload;
const research = vi.hoisted(() => ({read:vi.fn(), start:vi.fn()}));
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
  render(<NotificationBell/>);
  const trigger=screen.getByRole("button",{name:/打开通知中心/});trigger.focus();fireEvent.click(trigger);
  await screen.findAllByTestId("notification-row");
  return trigger;
}
beforeEach(() => {
  compact=false;localStorage.clear();__resetPrefsCache();
  payload={items:[item("A","600127"),item("B","600519")],count:2,generated_at:"2026-10-23T02:01:00Z",news_min_score:60,policy:"stock_opportunities_only",errors:null};
  vi.stubGlobal("matchMedia",(query:string)=>({matches:query.includes("max-width") && compact,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
});
afterEach(()=>{cleanup();vi.clearAllMocks();vi.unstubAllGlobals();});

it("reads only the selected stock in the same dialog, while hover and repeat selection do not issue another read",async()=>{
  await open();
  expect(getStockAttribution).not.toHaveBeenCalled();
  expect(screen.getAllByTestId("notification-unread-dot")).toHaveLength(2);
  const rows=screen.getAllByTestId("notification-row");
  fireEvent.mouseEnter(rows[0]);fireEvent.focus(rows[0]);
  expect(getStockAttribution).not.toHaveBeenCalled();
  fireEvent.click(within(rows[0]).getByTestId("notification-judgment"));
  const region=await screen.findByRole("region",{name:"原入选A"});
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(screen.getByRole("dialog",{name:"通知中心"}).contains(region)).toBe(true);
  await waitFor(()=>expect(getStockAttribution).toHaveBeenCalledTimes(1));
  expect(getStockAttribution).toHaveBeenLastCalledWith("600127",{poolDate:undefined,responseAsOf:"2026-10-23T01:59:00Z"});
  fireEvent.mouseEnter(rows[1]);fireEvent.click(within(rows[0]).getByTestId("notification-judgment"));
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
  expect(screen.getAllByTestId("notification-unread-dot")).toHaveLength(1);
  expect(research.read).not.toHaveBeenCalled();expect(research.start).not.toHaveBeenCalled();
});

it("moves deliberately through the list with arrows and returns focus from the mobile inline explanation",async()=>{
  compact=true;await open();
  const rows=screen.getAllByTestId("notification-row");
  const first=within(rows[0]).getByRole("button",{name:/原入选A/});
  first.focus();fireEvent.keyDown(first,{key:"ArrowDown",isComposing:true});
  expect(getStockAttribution).not.toHaveBeenCalled();
  fireEvent.keyDown(first,{key:"ArrowDown"});
  const region=await screen.findByRole("region",{name:"原入选B"});
  await waitFor(()=>expect(region.contains(document.activeElement)).toBe(true));
  expect(screen.getByTestId("notification-workspace").getAttribute("data-view")).toBe("detail");
  fireEvent.click(screen.getByRole("button",{name:"返回通知列表"}));
  expect(screen.getByTestId("notification-workspace").getAttribute("data-view")).toBe("list");
  await waitFor(()=>expect(document.activeElement).toBe(within(rows[1]).getByRole("button",{name:/原入选B/})));
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
});

it("shows archived evidence states without turning a missing dimension into a measured zero",async()=>{
  await open();
  const fingerprint=within(screen.getAllByTestId("notification-row")[0]).getByLabelText("原入选证据指纹");
  expect(fingerprint.textContent).toContain("消息82");
  expect(fingerprint.textContent).toContain("技术缺项");
  expect(fingerprint.textContent).not.toContain("技术0");
  expect(within(fingerprint).getAllByRole("meter")).toHaveLength(1);
  expect(getStockAttribution).not.toHaveBeenCalled();
});

it("clears an open explanation with its list and Escape still closes the sole drawer",async()=>{
  const trigger=await open();
  fireEvent.click(within(screen.getAllByTestId("notification-row")[0]).getByTestId("notification-judgment"));
  await screen.findByRole("region",{name:"原入选A"});
  fireEvent.click(screen.getByRole("button",{name:"一键清除"}));
  expect(screen.queryByRole("region",{name:"原入选A"})).toBeNull();
  expect(screen.queryAllByTestId("notification-row")).toHaveLength(0);
  fireEvent.keyDown(window,{key:"Escape"});
  expect(screen.queryByRole("dialog",{name:"通知中心"})).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

it("does not draw measured bars for out-of-range or unknown archived values",async()=>{
  const ref=payload.items[0].references![0];
  ref.evidence={sub_scores:{news:200,tech:0,sentiment:55},dimension_evidence:{news:{state:"success"},tech:{state:"missing"},sentiment:{state:"unknown"}}};
  await open();
  const fingerprint=within(screen.getAllByTestId("notification-row")[0]).getByLabelText("原入选证据指纹");
  expect(within(fingerprint).queryByRole("meter")).toBeNull();
  expect(fingerprint.textContent).toContain("技术缺项");
  expect(fingerprint.textContent).not.toContain("200");
  expect(fingerprint.textContent).not.toContain("55");
});

it("keeps a failed refresh visible in the selected mobile explanation without rereading the company",async()=>{
  compact=true;await open();
  fireEvent.click(within(screen.getAllByTestId("notification-row")[0]).getByTestId("notification-judgment"));
  const region=await screen.findByRole("region",{name:"原入选A"});
  await waitFor(()=>expect(getStockAttribution).toHaveBeenCalledTimes(1));
  vi.mocked(getNotifications).mockRejectedValueOnce(new Error("offline"));
  fireEvent.click(screen.getByRole("button",{name:"刷新通知"}));
  await waitFor(()=>expect(within(region).getByRole("alert").textContent).toContain("当前通知状态未确认"));
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
});

it("pauses a mobile hidden list through a payload update and rebinds its own scroll owner on return",async()=>{
  compact=true;
  payload={...payload,items:Array.from({length:48},(_,index)=>item(`N${index}`,"600127")),count:48};
  let near=false;
  const hidden=()=>screen.queryByTestId("notification-workspace")?.getAttribute("data-view")==="detail";
  const heights=vi.spyOn(HTMLElement.prototype,"scrollHeight","get").mockImplementation(function(this:HTMLElement){
    return this.dataset.testid==="notification-list" && !hidden() ? 2000 : 0;
  });
  const clients=vi.spyOn(HTMLElement.prototype,"clientHeight","get").mockImplementation(function(this:HTMLElement){
    return this.dataset.testid==="notification-list" && !hidden() ? 300 : 0;
  });
  const rectangles=vi.spyOn(HTMLElement.prototype,"getBoundingClientRect").mockImplementation(()=>{
    const top=hidden()?0:near?200:5000;
    return {top,bottom:top+1,left:0,right:100,width:100,height:1,x:0,y:top,toJSON:()=>({})};
  });
  try{
    await open();
    expect(screen.getAllByTestId("notification-row")).toHaveLength(30);
    fireEvent.click(within(screen.getAllByTestId("notification-row")[0]).getByTestId("notification-judgment"));
    await screen.findByRole("region",{name:"原入选N0"});
    await waitFor(()=>expect(getStockAttribution).toHaveBeenCalledTimes(1));
    payload={...payload,items:[...payload.items,item("N48","600127")],count:49};
    fireEvent.click(screen.getByRole("button",{name:"刷新通知"}));
    await waitFor(()=>expect(screen.getByTestId("notification-list").textContent).toContain("49 条通知"));
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});
    expect(screen.getAllByTestId("notification-row")).toHaveLength(30);
    const requests=vi.mocked(getNotifications).mock.calls.length;
    fireEvent.click(screen.getByRole("button",{name:"返回通知列表"}));
    near=true;fireEvent.scroll(screen.getByTestId("notification-list"));
    expect(screen.getAllByTestId("notification-row")).toHaveLength(49);
    expect(vi.mocked(getNotifications).mock.calls.length).toBe(requests);
    expect(getStockAttribution).toHaveBeenCalledTimes(1);
  }finally{rectangles.mockRestore();clients.mockRestore();heights.mockRestore();}
});

it("keeps the desktop list scroll owner active while its inline explanation is visible",async()=>{
  payload={...payload,items:Array.from({length:48},(_,index)=>item(`D${index}`,"600127")),count:48};
  let near=false;
  const heights=vi.spyOn(HTMLElement.prototype,"scrollHeight","get").mockImplementation(function(this:HTMLElement){return this.dataset.testid==="notification-list"?2000:0;});
  const clients=vi.spyOn(HTMLElement.prototype,"clientHeight","get").mockImplementation(function(this:HTMLElement){return this.dataset.testid==="notification-list"?300:0;});
  const rectangles=vi.spyOn(HTMLElement.prototype,"getBoundingClientRect").mockImplementation(()=>{
    const top=near?200:5000;return {top,bottom:top+1,left:0,right:100,width:100,height:1,x:0,y:top,toJSON:()=>({})};
  });
  try{
    await open();
    expect(screen.getAllByTestId("notification-row")).toHaveLength(30);
    fireEvent.click(within(screen.getAllByTestId("notification-row")[0]).getByTestId("notification-judgment"));
    await screen.findByRole("region",{name:"原入选D0"});
    const requests=vi.mocked(getNotifications).mock.calls.length;
    near=true;fireEvent.scroll(screen.getByTestId("notification-list"));
    expect(screen.getAllByTestId("notification-row")).toHaveLength(48);
    expect(screen.getByRole("region",{name:"原入选D0"})).toBeTruthy();
    expect(vi.mocked(getNotifications).mock.calls.length).toBe(requests);
  }finally{rectangles.mockRestore();clients.mockRestore();heights.mockRestore();}
});
