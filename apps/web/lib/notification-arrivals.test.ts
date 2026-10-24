import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import type {NotificationItem} from "@/lib/api";
import {
  ARRIVAL_STORAGE_KEY, MAX_ARRIVAL_LEDGER, MAX_ARRIVAL_QUEUE, confirmArrivalDisplay,
  notificationArrivals, parseArrivalLedger, reconcileArrivalLedger, reserveArrival, updateArrivalLedger,
  type ArrivalNotification,
} from "./notification-arrivals";

const now = Date.parse("2026-10-24T09:30:00+08:00");
function item(id = "alert-1", over: Partial<ArrivalNotification> = {}): ArrivalNotification {
  return {id, category:"opportunity", label:"入选观察", session:"intraday", ts:new Date(now).toISOString(), title:"原通知", body:"原始依据", symbol:"600001", url:null, score:null,
    arrival:{phase:"selected", version:"v1", source_as_of:new Date(now).toISOString(), origin_id:"selection:1"}, ...over};
}
function baseline(items: ArrivalNotification[] = [item()]) {
  return reconcileArrivalLedger(parseArrivalLedger(null), notificationArrivals(items), now);
}
beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("notification phase identities", () => {
  it("one response preserves every same-stock phase and prefers the full arrivals array", () => {
    const first=item();
    const confirmed={phase:"confirmed" as const,version:"confirm-v1",origin_id:"confirm:1",source_as_of:new Date(now+1000).toISOString()};
    const ready={phase:"research_ready" as const,version:"report-v1",origin_id:"result:1",source_as_of:new Date(now+2000).toISOString()};
    const all=notificationArrivals([{...first,arrival:ready,arrivals:[first.arrival!,confirmed,ready,ready]}]);
    expect(all.map(arrival=>arrival.kind)).toEqual(["selection","confirmation","research_ready"]);
    expect(new Set(all.map(arrival=>arrival.key)).size).toBe(3);
    expect(all.map(arrival=>arrival.version)).toEqual(["v1","confirm-v1","report-v1"]);
  });
  it("same message ID acquires a distinct confirmation, research and paper phase without inspecting prose", () => {
    const original = item();
    const phase = (key: "confirmed" | "research_ready" | "paper_status") => item("alert-1", {arrival:{phase:key, version:"v2", origin_id:"origin:2", source_as_of:new Date(now+1000).toISOString()}});
    const all = notificationArrivals([original, phase("confirmed"), phase("research_ready"), phase("paper_status")]);
    expect(all.map(arrival => arrival.kind)).toEqual(expect.arrayContaining(["selection","confirmation","research_ready","paper_status"]));
    expect(new Set(all.map(arrival => arrival.key)).size).toBe(4);
    expect(notificationArrivals([{...original, body:"改写的正文", title:"新标题"}])[0].key).toBe(all.find(arrival => arrival.kind === "selection")!.key);
  });
  it("legacy merged references keep original event/version identities and original source clocks", () => {
    const original: NotificationItem = {...item(), references:[{event_id:1, kind:"selection", selection_source:"daily", trade_date:"2026-10-24", source_id:"daily:1", source_version:"v1", source_as_of:new Date(now).toISOString(), recorded_at:new Date(now+9000).toISOString(), evidence:{}}, {event_id:2, kind:"buy_point", trade_date:"2026-10-24", source_id:"buy:2", source_version:"v2", source_as_of:new Date(now+1000).toISOString(), recorded_at:new Date(now+10000).toISOString(), evidence:{}}]};
    delete (original as ArrivalNotification).arrival;
    const all = notificationArrivals([original]);
    expect(all.map(arrival => arrival.kind)).toEqual(["selection","confirmation"]);
    expect(all.map(arrival => arrival.at)).toEqual([now,now+1000]);
    expect(all.map(arrival => arrival.source)).toEqual(["每日精选","盘中条件判定"]);
  });
  it("missing clocks, invalid origins and unknown explicit phases cannot become fresh reminders", () => {
    expect(notificationArrivals([item("alert-1",{arrival:{phase:"research_ready",version:"v1",source_as_of:null,origin_id:"result:1"}})])).toEqual([]);
    expect(notificationArrivals([item("alert-1",{arrival:{phase:"selected",version:"",source_as_of:new Date(now).toISOString(),origin_id:"result:1"}})])).toEqual([]);
  });
});

describe("bounded arrival ledger", () => {
  it("first history only establishes a baseline; later same-ID phases become pending once", () => {
    const old = baseline();
    expect(old.entries[0].state).toBe("baseline");
    const fresh = item("alert-1", {arrival:{phase:"confirmed",version:"confirm:1",origin_id:"buy:1",source_as_of:new Date(now+1000).toISOString()}});
    const next = reconcileArrivalLedger(old,notificationArrivals([item(),fresh]),now+1000);
    expect(next.entries.map(entry=>entry.state)).toEqual(["baseline","pending"]);
    expect(reconcileArrivalLedger(next,notificationArrivals([fresh]),now+2000).entries).toEqual(next.entries);
  });
  it("a late read of an old report never uses reception time as new completion evidence", () => {
    const fresh = item("alert-2",{arrival:{phase:"research_ready",version:"old-report",origin_id:"result:2",source_as_of:new Date(now-1000).toISOString()}});
    const next = reconcileArrivalLedger(baseline(),notificationArrivals([fresh]),now+10000);
    expect(next.entries.every(entry=>entry.state==="baseline")).toBe(true);
  });
  it("claiming is not display; only the owning rendered consumer can acknowledge", () => {
    const fresh=notificationArrivals([item("alert-2",{ts:new Date(now+1000).toISOString(),arrival:{phase:"selected",version:"v1",origin_id:"selection:2",source_as_of:new Date(now+1000).toISOString()}})]);
    const next=reconcileArrivalLedger(baseline(),fresh,now+1000);
    const claimed=reserveArrival(next,fresh[0].key,"tab-A",now+1000);
    expect(claimed.entries.at(-1)).toMatchObject({state:"claimed",owner:"tab-A"});
    expect(claimed.entries.at(-1)?.displayedAt).toBeUndefined();
    expect(confirmArrivalDisplay(claimed,[fresh[0].key],"tab-B",now+1001)).toEqual(claimed);
    const shown=confirmArrivalDisplay(claimed,[fresh[0].key],"tab-A",now+1001);
    expect(shown.entries.at(-1)).toMatchObject({state:"displayed",displayedAt:now+1001});
    expect(reconcileArrivalLedger(parseArrivalLedger(JSON.stringify(shown)),fresh,now+2000).entries.at(-1)?.state).toBe("displayed");
  });
  it("a lost pre-render claim becomes pending after its bounded lease; shown cards do not", () => {
    const fresh=notificationArrivals([item("alert-2")]);
    const pending=reconcileArrivalLedger(baseline([]),fresh,now+1000);
    const claimed=reserveArrival(pending,fresh[0].key,"lost-tab",now+1000);
    expect(reconcileArrivalLedger(claimed,fresh,now+6000).entries[0].state).toBe("pending");
  });
  it("queue and stored identities remain bounded; deferred entries are not falsely called displayed", () => {
    const many=Array.from({length:MAX_ARRIVAL_LEDGER+30},(_,i)=>item(`alert-${i}`,{arrival:{phase:"selected",version:"v1",origin_id:`selection:${i}`,source_as_of:new Date(now+i).toISOString()}}));
    const next=reconcileArrivalLedger(baseline([]),notificationArrivals(many),now+1000);
    expect(next.entries).toHaveLength(MAX_ARRIVAL_LEDGER);
    expect(next.entries.filter(entry=>entry.state==="pending")).toHaveLength(MAX_ARRIVAL_QUEUE);
    expect(next.entries.filter(entry=>entry.state==="displayed")).toHaveLength(0);
    const raw=JSON.stringify(next);
    expect(raw).not.toContain("原始依据"); expect(raw).not.toContain("600001"); expect(raw).not.toContain("原通知");
  });
  it("malformed storage cannot inject bodies or restore an unbounded history", () => {
    expect(parseArrivalLedger("broken").initialized).toBe(false);
    const value={...baseline(),entries:[{...baseline().entries[0],body:"private prose"}]};
    expect(JSON.stringify(parseArrivalLedger(JSON.stringify(value)))).not.toContain("private prose");
  });
  it("same-origin mutation uses Web Locks and rereads the shared ledger", async () => {
    const request=vi.fn(async (_name:string,task:()=>unknown)=>task());
    vi.stubGlobal("navigator",{locks:{request}});
    await updateArrivalLedger(()=>baseline());
    await updateArrivalLedger(current=>({...current,baselineAt:current.baselineAt+1000}));
    expect(request).toHaveBeenCalledTimes(2);
    expect(parseArrivalLedger(localStorage.getItem(ARRIVAL_STORAGE_KEY)).baselineAt).toBe(now+1000);
  });
});

it("quota failure keeps same-tab identities instead of re-establishing history on every pass",async()=>{
  const write=vi.spyOn(Storage.prototype,"setItem").mockImplementation(()=>{throw new DOMException("full","QuotaExceededError");});
  try {
    await updateArrivalLedger(()=>baseline());
    let preserved=false;
    await updateArrivalLedger(current=>{preserved=current.initialized && current.entries[0]?.key===baseline().entries[0].key; return current;});
    expect(preserved).toBe(true);
  } finally {write.mockRestore();}
});
