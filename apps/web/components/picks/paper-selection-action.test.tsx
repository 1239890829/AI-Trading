import {act,cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,beforeEach,expect,it,vi} from "vitest";
import {ApiError} from "@/lib/api/client";
import {getPaperSelectionAction,readPendingPaperSelection,submitPaperSelectionRequest,type PaperSelectionActionData} from "@/lib/paper-selection-api";
import {PaperSelectionAction} from "./paper-selection-action";
const access=vi.hoisted(()=>({maintenance:true,open:vi.fn()}));
vi.mock("@/components/access/device-access",()=>({useDeviceAccess:()=>({device:{state:"active",maintenance:access.maintenance},open:access.open,refresh:vi.fn()})}));
vi.mock("@/lib/paper-selection-api",async()=>({...await vi.importActual<typeof import("@/lib/paper-selection-api")>("@/lib/paper-selection-api"),getPaperSelectionAction:vi.fn(),submitPaperSelectionRequest:vi.fn()}));
const NOW=Date.parse("2026-10-24T09:30:00+08:00");
const binding={symbol:"600127",selectionRef:"daily:2026-10-24:600127",sourceVersion:"source-v1"};
function ready(over:Partial<PaperSelectionActionData>={}):PaperSelectionActionData{return {scope:"main",read_only:true,selection:{ref:binding.selectionRef,symbol:binding.symbol,source_version:binding.sourceVersion},eligibility:{state:"ready",reason_code:"ready",reasons:[]},quote:{price:10,source:"fixture",as_of:new Date(NOW).toISOString(),quality:"high"},preflight:{allowed:true,side:"buy",price:10,quantity:100,fee:5,checked_at:new Date(NOW).toISOString(),expires_at:new Date(NOW+30000).toISOString(),decision_id:"decision-A",decision_version:"decision-v1"},execution:{state:"not_started",reason:"尚无关联模拟动作",order:null},permission:{maintenance_required:true,maintenance_granted:true},...over};}
const confirm=()=>screen.getByRole<HTMLButtonElement>("button",{name:"确认模拟买入"});
const inspect=()=>screen.getByRole<HTMLButtonElement>("button",{name:"模拟预检"});
async function checked(){fireEvent.click(inspect());await waitFor(()=>expect(confirm().disabled).toBe(false));}
beforeEach(()=>{sessionStorage.clear();vi.clearAllMocks();access.maintenance=true;vi.spyOn(Date,"now").mockReturnValue(NOW);vi.mocked(getPaperSelectionAction).mockResolvedValue(ready());vi.mocked(submitPaperSelectionRequest).mockResolvedValue({id:18,status:"filled",filled_price:10,fee:5});});
afterEach(()=>{cleanup();vi.restoreAllMocks();});

it("mount is read-only with no automatic requests; explicit preflight shows genuine quote/fee and enables confirmation",async()=>{
  render(<PaperSelectionAction {...binding}/>);
  expect(getPaperSelectionAction).not.toHaveBeenCalled();expect(submitPaperSelectionRequest).not.toHaveBeenCalled();expect(confirm().disabled).toBe(true);
  await checked();
  expect(getPaperSelectionAction).toHaveBeenCalledWith(binding,expect.objectContaining({quantity:100,price:undefined}));
  expect(screen.getByText(/费用 5.00 元/)).toBeTruthy();expect(screen.getByText(/fixture/)).toBeTruthy();
  expect(screen.queryByText(/本金|总资产|胜率/)).toBeNull();
});
it("changing price or lot immediately invalidates a previously allowed preflight",async()=>{
  render(<PaperSelectionAction {...binding}/>);await checked();
  fireEvent.change(screen.getByLabelText("模拟价格"),{target:{value:"11"}});expect(confirm().disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("模拟股数"),{target:{value:"150"}});expect(inspect().disabled).toBe(true);
  fireEvent.click(inspect());expect(getPaperSelectionAction).toHaveBeenCalledTimes(1);expect(submitPaperSelectionRequest).not.toHaveBeenCalled();
  expect(screen.getByText(/100股的整数倍/)).toBeTruthy();
});
it("A→B→A input does not let a late old allowed response revive the preflight",async()=>{
  let resolve!:(value:PaperSelectionActionData)=>void;
  vi.mocked(getPaperSelectionAction).mockReturnValueOnce(new Promise(done=>{resolve=done;}));
  render(<PaperSelectionAction {...binding}/>);fireEvent.click(inspect());
  fireEvent.change(screen.getByLabelText("模拟股数"),{target:{value:"200"}});fireEvent.change(screen.getByLabelText("模拟股数"),{target:{value:"100"}});
  await act(async()=>resolve(ready()));expect(confirm().disabled).toBe(true);
  await checked();expect(getPaperSelectionAction).toHaveBeenCalledTimes(2);
});
it("the original 30-second deadline disables unchanged preflight without another read",async()=>{
  vi.useFakeTimers();vi.setSystemTime(NOW);
  try{
    render(<PaperSelectionAction {...binding}/>);await act(async()=>fireEvent.click(inspect()));expect(confirm().disabled).toBe(false);
    await act(async()=>{await vi.advanceTimersByTimeAsync(30000);});
    expect(confirm().disabled).toBe(true);expect(screen.getByText("预检已过期")).toBeTruthy();
    fireEvent.click(confirm());expect(submitPaperSelectionRequest).not.toHaveBeenCalled();expect(getPaperSelectionAction).toHaveBeenCalledTimes(1);
  }finally{cleanup();vi.useRealTimers();}
});
it.each([
  ["auction","blocked","竞价阶段，不模拟成交"],
  ["market_closed","blocked","休市期间，不模拟成交"],
  ["observation_only","blocked","原入选仅观察"],
  ["quote_untrusted","unknown","报价来源尚未确认"],
] as const)("shows the actual %s gate without manufacturing an order",async(reason_code,state,reason)=>{
  vi.mocked(getPaperSelectionAction).mockResolvedValue(ready({eligibility:{state,reason_code,reasons:[reason]},preflight:null}));
  render(<PaperSelectionAction {...binding}/>);fireEvent.click(inspect());await screen.findByText(reason);
  expect(confirm().disabled).toBe(true);expect(submitPaperSelectionRequest).not.toHaveBeenCalled();
});
it.each([false,true])("ordinary pairing never grants maintenance even if server snapshot says %s",async maintenance_granted=>{
  access.maintenance=false;vi.mocked(getPaperSelectionAction).mockResolvedValue(ready({permission:{maintenance_required:true,maintenance_granted}}));
  render(<PaperSelectionAction {...binding}/>);fireEvent.click(inspect());await screen.findByText(/费用 5.00 元/);
  expect(confirm().disabled).toBe(true);fireEvent.click(screen.getByRole("button",{name:"设备与权限"}));expect(access.open).toHaveBeenCalledOnce();
  expect(submitPaperSelectionRequest).not.toHaveBeenCalled();
});
it("server permission rejection cannot be replaced by a local maintenance flag",async()=>{
  vi.mocked(getPaperSelectionAction).mockResolvedValue(ready({permission:{maintenance_required:true,maintenance_granted:false}}));
  render(<PaperSelectionAction {...binding}/>);fireEvent.click(inspect());await screen.findByText(/服务端尚未确认维护权限/);expect(confirm().disabled).toBe(true);
});
it("rapid confirmations send once, retain the bound UUID, and show only the returned simulated fill",async()=>{
  let resolve!:(value:{id:number;status:string;filled_price:number;fee:number})=>void;
  vi.mocked(submitPaperSelectionRequest).mockReturnValueOnce(new Promise(done=>{resolve=done;}));
  render(<PaperSelectionAction {...binding}/>);await checked();const button=confirm();
  fireEvent.click(button);fireEvent.click(button);expect(submitPaperSelectionRequest).toHaveBeenCalledTimes(1);
  const original=readPendingPaperSelection(binding)!;expect(original.requestId).toMatch(/^[0-9a-f-]{36}$/);
  expect(original).toMatchObject({decisionId:"decision-A",decisionVersion:"decision-v1",quantity:100,price:10});
  expect((screen.getByLabelText("模拟股数") as HTMLInputElement).disabled).toBe(true);
  await act(async()=>resolve({id:18,status:"filled",filled_price:10,fee:5}));
  expect(screen.getByText(/模拟委托 #18/)).toBeTruthy();expect(readPendingPaperSelection(binding)).toBeNull();expect(confirm().disabled).toBe(true);
});
it("unknown response keeps one UUID across reload; only explicit GET resolves it, never another POST",async()=>{
  vi.mocked(submitPaperSelectionRequest).mockRejectedValueOnce(new ApiError(0,"网络返回丢失","timeout"));
  const view=render(<PaperSelectionAction {...binding}/>);await checked();fireEvent.click(confirm());
  await screen.findByText(/提交结果未知/);const original=readPendingPaperSelection(binding)!;
  expect(inspect().disabled).toBe(true);view.unmount();
  const calls=vi.mocked(getPaperSelectionAction).mock.calls.length;render(<PaperSelectionAction {...binding}/>);
  expect(getPaperSelectionAction).toHaveBeenCalledTimes(calls);expect(confirm().disabled).toBe(true);
  fireEvent.click(screen.getByRole("button",{name:"核对原请求"}));await screen.findByText(/原请求尚无可确认回执/);
  expect(getPaperSelectionAction).toHaveBeenLastCalledWith(binding,expect.objectContaining({requestId:original.requestId,price:10,quantity:100}));
  expect(readPendingPaperSelection(binding)?.requestId).toBe(original.requestId);expect(submitPaperSelectionRequest).toHaveBeenCalledTimes(1);
  vi.mocked(getPaperSelectionAction).mockResolvedValueOnce(ready({execution:{state:"filled",reason:"原模拟成交回执",order:{id:19,status:"filled",filled_price:10,fee:5}}}));
  fireEvent.click(screen.getByRole("button",{name:"核对原请求"}));await screen.findByText(/模拟委托 #19/);
  expect(readPendingPaperSelection(binding)).toBeNull();expect(submitPaperSelectionRequest).toHaveBeenCalledTimes(1);
});
it("a definitive rejected POST is displayed as rejection, never as a fill",async()=>{
  vi.mocked(submitPaperSelectionRequest).mockRejectedValueOnce(new ApiError(422,"买点条件已失效","http_422"));
  render(<PaperSelectionAction {...binding}/>);await checked();fireEvent.click(confirm());await screen.findByText("买点条件已失效");
  expect(readPendingPaperSelection(binding)).toBeNull();expect(screen.queryByText(/模拟委托 #/)).toBeNull();expect(confirm().disabled).toBe(true);
});
it("IME actions do not trigger preflight or submit, and a stock switch discards its late response",async()=>{
  let resolve!:(value:PaperSelectionActionData)=>void;
  vi.mocked(getPaperSelectionAction).mockReturnValueOnce(new Promise(done=>{resolve=done;}));
  const view=render(<PaperSelectionAction {...binding}/>);
  fireEvent.compositionStart(screen.getByLabelText("模拟价格"));fireEvent.click(inspect());expect(getPaperSelectionAction).not.toHaveBeenCalled();
  fireEvent.compositionEnd(screen.getByLabelText("模拟价格"));fireEvent.click(inspect());
  const other={symbol:"600001",selectionRef:"daily:2026-10-24:600001",sourceVersion:"source-v2"};view.rerender(<PaperSelectionAction {...other}/>);
  await act(async()=>resolve(ready()));expect(confirm().disabled).toBe(true);expect(screen.getByRole("region",{name:"600001模拟买入"})).toBeTruthy();
  expect(submitPaperSelectionRequest).not.toHaveBeenCalled();
});

it("same-request cancellation is a known order state and never becomes a fabricated fill or replacement POST",async()=>{
  vi.mocked(submitPaperSelectionRequest).mockRejectedValueOnce(new ApiError(0,"返回未知","timeout"));
  render(<PaperSelectionAction {...binding}/>);await checked();fireEvent.click(confirm());await screen.findByText(/提交结果未知/);
  const requestId=readPendingPaperSelection(binding)!.requestId;
  vi.mocked(getPaperSelectionAction).mockResolvedValueOnce(ready({eligibility:{state:"blocked",reason_code:"execution_exists",reasons:["原模拟委托已取消"]},preflight:null,execution:{state:"cancelled",reason:"原模拟委托已取消",order:{id:20,status:"cancelled",filled_price:null,fee:null}}}));
  fireEvent.click(screen.getByRole("button",{name:"核对原请求"}));await screen.findByText(/模拟委托 #20/);
  expect(getPaperSelectionAction).toHaveBeenLastCalledWith(binding,expect.objectContaining({requestId}));
  expect(screen.getByText(/模拟委托 #20/).textContent).toContain("已取消");expect(readPendingPaperSelection(binding)).toBeNull();expect(submitPaperSelectionRequest).toHaveBeenCalledTimes(1);
});

it("a late POST for the old stock settles its own identity without displaying a fill for the new stock",async()=>{
  let resolve!:(value:{id:number;status:string;filled_price:number;fee:number})=>void;
  vi.mocked(submitPaperSelectionRequest).mockReturnValueOnce(new Promise(done=>{resolve=done;}));
  const view=render(<PaperSelectionAction {...binding}/>);await checked();fireEvent.click(confirm());expect(readPendingPaperSelection(binding)).not.toBeNull();
  const other={symbol:"600001",selectionRef:"daily:2026-10-24:600001",sourceVersion:"source-v2"};view.rerender(<PaperSelectionAction {...other}/>);
  await act(async()=>resolve({id:44,status:"filled",filled_price:10,fee:5}));
  expect(screen.queryByText(/模拟委托 #44/)).toBeNull();expect(confirm().disabled).toBe(true);expect(readPendingPaperSelection(binding)).toBeNull();
  expect(readPendingPaperSelection(other)).toBeNull();
});
it("a known fill stays known when local identity cleanup fails",async()=>{
  render(<PaperSelectionAction {...binding}/>);await checked();
  vi.spyOn(Storage.prototype,"removeItem").mockImplementation(()=>{throw new Error("storage unavailable");});
  fireEvent.click(confirm());await screen.findByText(/模拟委托 #18/);
  expect(screen.getByText(/本页请求标识仍保留/)).toBeTruthy();expect(screen.queryByText(/提交结果未知/)).toBeNull();
  expect(readPendingPaperSelection(binding)).not.toBeNull();expect(confirm().disabled).toBe(true);
});
