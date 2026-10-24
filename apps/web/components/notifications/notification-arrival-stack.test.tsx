import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import type {ArrivalNotification} from "@/lib/notification-arrivals";
import {notificationArrivals,ARRIVAL_STORAGE_KEY, parseArrivalLedger, updateArrivalLedger} from "@/lib/notification-arrivals";
import {NotificationArrivalStack,notificationArrivalSummary} from "./notification-arrival-stack";

const NOW=Date.parse("2026-10-24T09:30:00+08:00");
const read=vi.fn(); const center=vi.fn();
function notice(id="alert-1", phase:"selected"|"confirmed"|"research_ready"|"paper_status"="selected"): ArrivalNotification {
  return {id,category:"opportunity",label:"入选观察",session:"intraday",ts:new Date(NOW+1000).toISOString(),recorded_at:new Date(NOW+1000).toISOString(),title:`【入选观察】600001 样本${id}`,body:"合同尚未确认，当前条件需核对。",symbol:"600001",url:null,score:null,source:"盘中选股原结果",
    arrival:{phase,origin_id:`${phase}:${id}`,version:"v1",source_as_of:new Date(NOW+1000).toISOString()}};
}
let narrow=false;
beforeEach(()=>{
  localStorage.clear(); read.mockClear(); center.mockClear(); narrow=false;
  vi.spyOn(Date,"now").mockReturnValue(NOW);
  vi.spyOn(document,"hidden","get").mockReturnValue(false);
  vi.stubGlobal("matchMedia",()=>({matches:narrow,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
  vi.stubGlobal("navigator",{locks:{request:vi.fn(async(_name:string,task:()=>unknown)=>task())}});
});
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();});
async function mount(){
  const view=render(<><button type="button">原操作</button><NotificationArrivalStack items={[]} ready clearBefore={0} onOpen={read} onOpenCenter={center}/></>);
  await waitFor(()=>expect(parseArrivalLedger(localStorage.getItem(ARRIVAL_STORAGE_KEY)).initialized).toBe(true));
  return {...view,receive:(items:ArrivalNotification[],ready=true)=>view.rerender(<><button type="button">原操作</button><NotificationArrivalStack items={items} ready={ready} clearBefore={0} onOpen={read} onOpenCenter={center}/></>)};
}

it("first loaded history stays silent, while a new phase shows its source without stealing focus",async()=>{
  const view=render(<NotificationArrivalStack items={[notice("old")]} ready clearBefore={0} onOpen={read} onOpenCenter={center}/>);
  await waitFor(()=>expect(parseArrivalLedger(localStorage.getItem(ARRIVAL_STORAGE_KEY)).initialized).toBe(true));
  expect(screen.queryByTestId("notification-arrival")).toBeNull(); view.unmount();
  const next=await mount();
  const focused=screen.getByRole("button",{name:"原操作"}); focused.focus(); next.receive([notice("new")]);
  const card=await screen.findByTestId("notification-arrival");
  expect(card.textContent).toContain("新入选"); expect(card.textContent).toContain("盘中选股原结果");
  expect(document.activeElement).toBe(focused); expect(read).not.toHaveBeenCalled();
});

it("same-ID confirmation is a new phase; closing does not mark read and refresh does not replay",async()=>{
  const view=await mount(); view.receive([notice()]);
  const card=await screen.findByTestId("notification-arrival");
  await waitFor(()=>expect(parseArrivalLedger(localStorage.getItem(ARRIVAL_STORAGE_KEY)).entries.some(entry=>entry.state==="displayed")).toBe(true));
  fireEvent.click(within(card).getByRole("button",{name:/收起新入选提醒/}));
  expect(read).not.toHaveBeenCalled(); expect(localStorage.getItem("ashare.notifications.read")).toBeNull();
  view.receive([notice()]); expect(screen.queryByTestId("notification-arrival")).toBeNull();
  view.receive([notice("alert-1","confirmed")]);
  expect((await screen.findByTestId("notification-arrival")).textContent).toContain("条件确认");
  await waitFor(()=>expect(parseArrivalLedger(localStorage.getItem(ARRIVAL_STORAGE_KEY)).entries.filter(entry=>entry.state==="displayed")).toHaveLength(2));
  view.unmount(); const again=await mount(); again.receive([notice("alert-1","confirmed")]);
  expect(screen.queryByTestId("notification-arrival")).toBeNull();
});

it("desktop caps at three cards and an explicitly freed slot takes the next queued item",async()=>{
  const view=await mount(); view.receive(Array.from({length:7},(_,i)=>notice(`alert-${i}`)));
  await waitFor(()=>expect(screen.getAllByTestId("notification-arrival")).toHaveLength(3));
  expect(screen.getByRole("button",{name:/另有 4 条排队/})).toBeTruthy();
  fireEvent.click(within(screen.getAllByTestId("notification-arrival")[0]).getByRole("button",{name:/收起/}));
  await waitFor(()=>expect(screen.getByRole("button",{name:/另有 3 条排队/})).toBeTruthy());
  expect(screen.getAllByTestId("notification-arrival")).toHaveLength(3);
  fireEvent.click(screen.getByRole("button",{name:/通知中心/})); expect(center).toHaveBeenCalledOnce();
});

it("narrow screens show one card and retain the queue without a nested scrolling list",async()=>{
  narrow=true; const view=await mount(); view.receive([notice("a"),notice("b"),notice("c")]);
  await waitFor(()=>expect(screen.getAllByTestId("notification-arrival")).toHaveLength(1));
  expect(screen.getByRole("button",{name:/另有 2 条排队/})).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull(); expect(screen.queryByRole("listbox")).toBeNull();
});

it("click reads the exact source item and a failed read cannot synthesize new arrivals",async()=>{
  const view=await mount(); const original=notice("a","research_ready"); view.receive([original],false);
  expect(screen.queryByTestId("notification-arrival")).toBeNull(); view.receive([original]);
  const card=await screen.findByTestId("notification-arrival");
  fireEvent.click(within(card).getByRole("button",{name:/研究完成.*查看原记录/}));
  expect(read).toHaveBeenCalledWith(original); expect(read).toHaveBeenCalledOnce();
});

it("another tab's confirmed display retires a competing local card without reading it",async()=>{
  const view=await mount(); view.receive([notice()]); await screen.findByTestId("notification-arrival");
  await updateArrivalLedger(ledger=>({...ledger,entries:ledger.entries.map(entry=>({...entry,state:"displayed",owner:"other-tab",displayedAt:NOW+2000}))}));
  fireEvent(window,new StorageEvent("storage",{key:ARRIVAL_STORAGE_KEY,newValue:localStorage.getItem(ARRIVAL_STORAGE_KEY)}));
  await waitFor(()=>expect(screen.queryByTestId("notification-arrival")).toBeNull()); expect(read).not.toHaveBeenCalled();
});

it("keyboard Escape respects IME and restores focus to a remaining card without reading",async()=>{
  const view=await mount(); view.receive([notice("a"),notice("b")]);
  await waitFor(()=>expect(screen.getAllByTestId("notification-arrival")).toHaveLength(2));
  const first=within(screen.getAllByTestId("notification-arrival")[0]).getByRole("button",{name:/查看原记录/});
  first.focus(); fireEvent.keyDown(first,{key:"Escape",isComposing:true});
  fireEvent.keyDown(first,{key:"Escape",keyCode:229});
  expect(screen.getAllByTestId("notification-arrival")).toHaveLength(2);
  fireEvent.keyDown(first,{key:"Escape"});
  await waitFor(()=>expect(screen.getAllByTestId("notification-arrival")).toHaveLength(1));
  expect(document.activeElement).toBe(within(screen.getByTestId("notification-arrival")).getByRole("button",{name:/查看原记录/}));
  expect(read).not.toHaveBeenCalled();
});

it("hidden tabs defer new arrivals until visible, and source errors keep prior cards without adding new ones",async()=>{
  let hidden=false;
  vi.spyOn(document,"hidden","get").mockImplementation(()=>hidden);
  const view=await mount();
  hidden=true; fireEvent(document,new Event("visibilitychange")); view.receive([notice("a")]);
  expect(screen.queryByTestId("notification-arrival")).toBeNull();
  hidden=false; fireEvent(document,new Event("visibilitychange"));
  const original=await screen.findByTestId("notification-arrival");
  view.receive([notice("a"),notice("b","research_ready")],false);
  expect(screen.getAllByTestId("notification-arrival")).toHaveLength(1);
  expect(screen.getByTestId("notification-arrival")).toBe(original);
  view.receive([notice("a"),notice("b","research_ready")]);
  await waitFor(()=>expect(screen.getAllByTestId("notification-arrival")).toHaveLength(2));
});


function progress(phase:"research_ready"|"paper_status", evidence:Record<string,unknown>, version="v1", referenceVersion=version) {
  const item={...notice("shared",phase),body:"旧入选理由：不是本次进展。",arrivals:[{phase,version,source_as_of:new Date(NOW+1000).toISOString(),origin_id:"origin:shared"}],
    references:[{event_id:3,kind:phase,trade_date:"2026-10-24",source_id:"origin:shared",source_version:referenceVersion,source_as_of:new Date(NOW+1000).toISOString(),recorded_at:null,evidence}]};
  return notificationArrivals([item])[0];
}
it("research progress requires its exact version and a seven-report saved receipt instead of old selection prose",()=>{
  const saved={research:{run_id:"fixture-run",report_count:7}};
  expect(notificationArrivalSummary(progress("research_ready",saved))).toBe("七份报告已保存，可逐角色核对；仍待核");
  expect(notificationArrivalSummary(progress("research_ready",saved,"new-v2","old-v1"))).toBe("进展已更新，请核对原记录");
  expect(notificationArrivalSummary(progress("research_ready",{research:{run_id:"fixture-run",report_count:6}}))).toBe("进展已更新，请核对原记录");
});
it.each(["pending","rejected","cancelled"] as const)("paper %s progress shows the exact version's saved state and reason",status=>{
  const result=notificationArrivalSummary(progress("paper_status",{paper_execution:{scope:"main",status,reason:"原回执原因",order_id:status==="rejected"?null:9}}));
  expect(result).toContain(status==="pending"?"待成交":status==="rejected"?"已拒绝":"已取消");
  expect(result).toContain("原回执原因");expect(result).not.toContain("旧入选理由");
});
it("filled progress displays actual price/quantity and accepts a real zero fee",()=>{
  const summary=notificationArrivalSummary(progress("paper_status",{paper_execution:{scope:"main",status:"filled",order_id:9,filled_price:10.25,quantity:100,fee:0}}));
  expect(summary).toBe("模拟已成交：100股 · 10.25元 · 费用0.00元");
});
it.each(["filled_price","quantity","fee"] as const)("filled without %s cannot fabricate numeric execution evidence",missing=>{
  const paper:Record<string,unknown>={scope:"main",status:"filled",order_id:9,filled_price:10.25,quantity:100,fee:0};delete paper[missing];
  const summary=notificationArrivalSummary(progress("paper_status",{paper_execution:paper}));
  expect(summary).toBe("模拟成交资料未齐，请核对成交价、数量和费用。");expect(summary).not.toContain("0.00元");expect(summary).not.toContain("模拟已成交");
});
it("unknown or nonmatching paper progress never inherits a filled receipt or the first selection reason",()=>{
  expect(notificationArrivalSummary(progress("paper_status",{paper_execution:{scope:"main",status:"unknown",filled_price:10,quantity:100,fee:0}}))).toContain("未当作成交");
  const filled={paper_execution:{scope:"main",status:"filled",order_id:9,filled_price:10,quantity:100,fee:0}};
  expect(notificationArrivalSummary(progress("paper_status",filled,"new-v2","old-v1"))).toBe("进展已更新，请核对原记录");
});
it("a rendered research arrival presents its matched progress instead of the anchor's rationale",async()=>{
  const view=await mount();const arrival=progress("research_ready",{research:{run_id:"fixture-run",report_count:7}});
  view.receive([arrival.item as ArrivalNotification]);
  const card=await screen.findByTestId("notification-arrival");expect(card.textContent).toContain("七份报告已保存，可逐角色核对；仍待核");
  expect(card.textContent).not.toContain("旧入选理由");
});
