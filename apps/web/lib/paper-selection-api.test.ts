import {afterEach,beforeEach,expect,it,vi} from "vitest";
import {ApiError} from "@/lib/api/client";
import {getJson} from "@/lib/api/internal";
import {placePaperOrder} from "@/lib/api/paper";
import {createPaperSelectionRequest,currentPaperPreflight,getPaperSelectionAction,parsePaperSelectionAction,readPendingPaperSelection,resolvePaperSelectionRequest,submitPaperSelectionRequest,type PaperSelectionActionData} from "./paper-selection-api";
vi.mock("@/lib/api/internal",()=>({getJson:vi.fn()}));
vi.mock("@/lib/api/paper",()=>({placePaperOrder:vi.fn()}));
const NOW=Date.parse("2026-10-24T09:30:00+08:00");
const binding={symbol:"600127",selectionRef:"daily:2026-10-24:600127",sourceVersion:"source-v1"};
function ready():PaperSelectionActionData {return {scope:"main",read_only:true,selection:{ref:binding.selectionRef,symbol:binding.symbol,source_version:binding.sourceVersion},eligibility:{state:"ready",reason_code:"ready",reasons:[]},quote:{price:10,source:"fixture",as_of:new Date(NOW).toISOString(),quality:"high"},preflight:{allowed:true,side:"buy",price:10,quantity:100,fee:5,checked_at:new Date(NOW).toISOString(),expires_at:new Date(NOW+30000).toISOString(),decision_id:"decision-A",decision_version:"decision-v1"},execution:{state:"not_started",reason:"尚无关联模拟动作",order:null},permission:{maintenance_required:true,maintenance_granted:true}};}
const envelope=(data:unknown)=>({data,meta:{provider:"fixture",is_realtime:false,is_stale:false,last_success_refresh:null,generated_at:new Date(NOW).toISOString()}});
beforeEach(()=>{sessionStorage.clear();vi.clearAllMocks();vi.spyOn(Date,"now").mockReturnValue(NOW);});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});
it("explicit preflight reads bind exact source, quantity and optional original UUID",async()=>{
  vi.mocked(getJson).mockResolvedValue(envelope(ready()));
  const requestId="f97dde63-3f36-4f35-a81c-63af0086a167";
  await getPaperSelectionAction(binding,{quantity:100,price:10,requestId});
  const [path,,options]=vi.mocked(getJson).mock.calls[0];
  const query=new URL(String(path),"http://fixture").searchParams;
  expect(query.get("selection_ref")).toBe(binding.selectionRef);expect(query.get("source_version")).toBe("source-v1");
  expect(query.get("request_id")).toBe(requestId);expect(query.get("quantity")).toBe("100");expect(query.get("price")).toBe("10");
  expect(options?.fresh).toBe(true);expect(placePaperOrder).not.toHaveBeenCalled();
});
it.each(["foreign-symbol","foreign-version","wrong-scope","not-read-only","missing-permission","infinite-price","contradictory-ready","fake-filled"])("malformed response fails closed: %s",fault=>{
  const data=ready();
  if(fault==="foreign-symbol")data.selection.symbol="600001";
  if(fault==="foreign-version")data.selection.source_version="other";
  if(fault==="wrong-scope")(data as unknown as {scope:string}).scope="hunting-shadow";
  if(fault==="not-read-only")(data as unknown as {read_only:boolean}).read_only=false;
  if(fault==="missing-permission")delete (data as Partial<PaperSelectionActionData>).permission;
  if(fault==="infinite-price")data.preflight!.price=Infinity;
  if(fault==="contradictory-ready")data.eligibility.state="blocked";
  if(fault==="fake-filled")data.execution={state:"filled",reason:"unknown",order:null};
  expect(()=>parsePaperSelectionAction(data,binding)).toThrow("未能核实");
});
it("invalid lots never issue a read, and mismatched echoed parameters cannot enable submission",async()=>{
  await expect(getPaperSelectionAction(binding,{quantity:150})).rejects.toThrow();expect(getJson).not.toHaveBeenCalled();
  vi.mocked(getJson).mockResolvedValue(envelope(ready()));
  await expect(getPaperSelectionAction(binding,{quantity:200})).rejects.toThrow("未能核实");
});
it("preflight expires at its actual deadline and never covers changed parameters",()=>{
  const data=ready();expect(currentPaperPreflight(data,100,undefined,NOW)).toBe(true);
  expect(currentPaperPreflight(data,200,undefined,NOW)).toBe(false);expect(currentPaperPreflight(data,100,11,NOW)).toBe(false);
  expect(currentPaperPreflight(data,100,10,NOW+30000)).toBe(false);
});
it("unknown POST preserves the exact persisted UUID/deadline, and never creates a replacement intent",async()=>{
  const pending=createPaperSelectionRequest(binding,ready(),100);
  vi.mocked(placePaperOrder).mockRejectedValue(new ApiError(0,"response lost","timeout"));
  await expect(submitPaperSelectionRequest(pending)).rejects.toThrow("response lost");
  expect(readPendingPaperSelection(binding)).toEqual(pending);
  expect(()=>createPaperSelectionRequest(binding,ready(),100)).toThrow("原模拟请求尚未核实");
  expect(placePaperOrder).toHaveBeenCalledWith("600127","buy",10,100,{selection_ref:binding.selectionRef,source_version:"source-v1",decision_id:"decision-A",decision_version:"decision-v1",request_id:pending.requestId,expires_at:pending.expiresAt});
  expect(resolvePaperSelectionRequest(pending)).toBe(true);expect(readPendingPaperSelection(binding)).toBeNull();
});
it("storage failure prevents creating or sending an untraceable request",()=>{
  vi.spyOn(Storage.prototype,"setItem").mockImplementation(()=>{throw new Error("quota");});
  expect(()=>createPaperSelectionRequest(binding,ready(),100)).toThrow("quota");expect(placePaperOrder).not.toHaveBeenCalled();
});
it("cleanup failure does not erase an original identity or invent an unknown model/order result",()=>{
  const pending=createPaperSelectionRequest(binding,ready(),100);
  vi.spyOn(Storage.prototype,"removeItem").mockImplementation(()=>{throw new Error("quota");});
  expect(resolvePaperSelectionRequest(pending)).toBe(false);expect(readPendingPaperSelection(binding)?.requestId).toBe(pending.requestId);
});


it("unknown quote fields remain explicit unknown evidence instead of becoming ready or fictitious prices",()=>{
  const data=ready();data.quote={price:null,source:null,as_of:null,quality:null};data.preflight=null;
  data.eligibility={state:"unknown",reason_code:"quote_untrusted",reasons:["报价来源或时点未能确认"]};
  expect(parsePaperSelectionAction(data,binding).eligibility.state).toBe("unknown");
  expect(currentPaperPreflight(data,100,undefined,NOW)).toBe(false);
});


it("HTTP LAN environments can preserve a UUID using secure random bytes without randomUUID",()=>{
  vi.stubGlobal("crypto",{getRandomValues:(bytes:Uint8Array)=>{bytes.fill(12);return bytes;}});
  const pending=createPaperSelectionRequest(binding,ready(),100);
  expect(pending.requestId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  expect(readPendingPaperSelection(binding)?.requestId).toBe(pending.requestId);
});


it("a foreign POST receipt is not treated as this stock's fill and preserves its unresolved UUID",async()=>{
  const pending=createPaperSelectionRequest(binding,ready(),100);
  vi.mocked(placePaperOrder).mockResolvedValue({id:22,status:"filled",symbol:"600001",side:"buy",filled_price:10,fee:5});
  await expect(submitPaperSelectionRequest(pending)).rejects.toThrow("未能核实");
  expect(readPendingPaperSelection(binding)?.requestId).toBe(pending.requestId);
});
