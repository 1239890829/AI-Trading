import {act, cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {useDeviceAccess} from "@/components/access/device-access";
import {createAgentTask, getAgentTask, type AgentTask} from "@/lib/api/agent";
import {REFERENCE_RESEARCH_METHODS} from "@/lib/api/picks";
import {ReferenceMethodResearch} from "./reference-method-research";

vi.mock("@/components/access/device-access", () => ({useDeviceAccess:vi.fn()}));
vi.mock("@/lib/api/agent", () => ({createAgentTask:vi.fn(), getAgentTask:vi.fn()}));
const done = vi.fn();
const method = REFERENCE_RESEARCH_METHODS[0];
function task(status: AgentTask["status"] = "queued"): AgentTask {
  return {id:"1".repeat(32),type:"reference_strategies",status,params:{},steps:[],error:null,risk_level:"L0",created_by:"maintenance",created_at:null,started_at:null,finished_at:null,
    result_ref:status === "succeeded" ? {kind:"reference_strategies",reused:true,runs:REFERENCE_RESEARCH_METHODS.map(method => ({method,run_id:"batch-23",trade_date:"2026-10-23",source_version:"source-23",content_version:"a".repeat(64),strategy_version:"v1",state:"saved"}))} : null};
}
beforeEach(() => {
  done.mockReset(); vi.mocked(createAgentTask).mockReset().mockResolvedValue(task()); vi.mocked(getAgentTask).mockReset().mockResolvedValue(task());
  vi.mocked(useDeviceAccess).mockReturnValue({device:{state:"active",maintenance:true},open:vi.fn(),refresh:vi.fn(async()=>{})});
});
afterEach(() => {cleanup();vi.useRealTimers();});

it("ordinary readers do not create research or request private task state", () => {
  vi.mocked(useDeviceAccess).mockReturnValue({device:{state:"active",maintenance:false},open:vi.fn(),refresh:vi.fn(async()=>{})});
  render(<ReferenceMethodResearch method={method} version="v1" onComplete={done}/>);
  expect(screen.queryByRole("button",{name:"运行研究筛选"})).toBeNull();
  expect(createAgentTask).not.toHaveBeenCalled(); expect(getAgentTask).not.toHaveBeenCalled();
});

it("one explicit shared scan reads the exact task and publishes the selected method's frozen identity", async () => {
  let finish!: (value:AgentTask)=>void;
  vi.mocked(createAgentTask).mockImplementation(() => new Promise(resolve=>{finish=resolve;}));
  vi.mocked(getAgentTask).mockResolvedValue(task("succeeded"));
  render(<ReferenceMethodResearch method={method} version="v1" onComplete={done}/>);
  expect(createAgentTask).not.toHaveBeenCalled();
  const run = screen.getByRole("button",{name:"运行研究筛选"}); fireEvent.click(run); fireEvent.click(run);
  expect(createAgentTask).toHaveBeenCalledExactlyOnceWith("reference_strategies",{});
  await act(async()=>finish(task()));
  await waitFor(()=>expect(done).toHaveBeenCalledTimes(1));
  expect(getAgentTask).toHaveBeenCalledExactlyOnceWith("1".repeat(32));
  expect(done.mock.calls[0][0]).toMatchObject({method,trade_date:"2026-10-23",run_id:"batch-23",content_version:"a".repeat(64)});
  expect(screen.getByText(/2026-10-23 · 复用同版存档，未重扫/)).toBeTruthy();
});

it("an explicit current page date can be changed to latest completed source without changing the page date", async () => {
  vi.mocked(getAgentTask).mockResolvedValue(task("succeeded"));
  window.history.replaceState({},"","/hunting?browse=methods&date=2026-10-26");
  render(<ReferenceMethodResearch method={method} version="v1" date="2026-10-26" onComplete={done}/>);
  expect(screen.getByLabelText("研究源日")).toHaveProperty("value","2026-10-26");
  fireEvent.click(screen.getByRole("button",{name:"使用最新已完成日"}));
  expect(window.location.search).toContain("date=2026-10-26");
  fireEvent.click(screen.getByRole("button",{name:"运行研究筛选"}));
  await waitFor(()=>expect(done).toHaveBeenCalledTimes(1));
  expect(createAgentTask).toHaveBeenCalledExactlyOnceWith("reference_strategies",{});
  expect(done.mock.calls[0][0].trade_date).toBe("2026-10-23");
  expect(screen.getByText(/2026-10-23 · 复用同版存档/)).toBeTruthy();
});

it.each(["missing", "foreign", "version", "mixed", "date"])("does not replace saved results when task completion has %s identity", async issue => {
  const value = task("succeeded");
  if(issue === "missing")value.result_ref!.runs!.pop();
  if(issue === "foreign")value.result_ref!.runs![0].method = "daily_picks";
  if(issue === "version")value.result_ref!.runs![0].strategy_version = "other-version";
  if(issue === "mixed")value.result_ref!.runs![1].source_version = "foreign-source";
  if(issue === "date")value.result_ref!.runs!.forEach(row=>{row.trade_date="2026-10-22";});
  vi.mocked(createAgentTask).mockResolvedValue({...task(),params:{trade_date:"2026-10-23"}});
  vi.mocked(getAgentTask).mockResolvedValue(value);
  render(<ReferenceMethodResearch method={method} version="v1" date="2026-10-23" onComplete={done}/>);
  fireEvent.click(screen.getByRole("button",{name:"运行研究筛选"}));
  await screen.findByText("结果身份或覆盖未确认，未替换当前保存记录。");
  expect(done).not.toHaveBeenCalled();
  expect(createAgentTask).toHaveBeenCalledWith("reference_strategies",{trade_date:"2026-10-23"});
});

it("an unknown submit does not retry or claim zero matches", async () => {
  vi.mocked(createAgentTask).mockRejectedValue(new Error("response lost"));
  render(<ReferenceMethodResearch method={method} version="v1" onComplete={done}/>);
  fireEvent.click(screen.getByRole("button",{name:"运行研究筛选"}));
  await screen.findByText(/提交结果未确认，已停止重复提交/);
  expect(screen.getByRole("button",{name:"运行研究筛选"})).toHaveProperty("disabled",true);
  expect(createAgentTask).toHaveBeenCalledTimes(1); expect(getAgentTask).not.toHaveBeenCalled(); expect(done).not.toHaveBeenCalled();
});

it("invalid dates never become today's implicit scan", () => {
  render(<ReferenceMethodResearch method={method} version="v1" date="2026-02-30" onComplete={done}/>);
  expect(screen.getByRole("button",{name:"运行研究筛选"})).toHaveProperty("disabled",true);
  expect(screen.getByRole("alert").textContent).toContain("日期无效");
  expect(createAgentTask).not.toHaveBeenCalled();
});

it("maintenance expiry stops polling and late completion from an unmounted scope is ignored", async () => {
  let finish!: (value:AgentTask)=>void;
  vi.mocked(getAgentTask).mockImplementation(() => new Promise(resolve=>{finish=resolve;}));
  const view = render(<ReferenceMethodResearch method={method} version="v1" onComplete={done}/>);
  fireEvent.click(screen.getByRole("button",{name:"运行研究筛选"}));
  await waitFor(()=>expect(getAgentTask).toHaveBeenCalledTimes(1));
  vi.mocked(useDeviceAccess).mockReturnValue({device:{state:"active",maintenance:false},open:vi.fn(),refresh:vi.fn(async()=>{})});
  view.rerender(<ReferenceMethodResearch method={method} version="v1" onComplete={done}/>);
  expect(screen.queryByRole("button",{name:"运行研究筛选"})).toBeNull();
  view.unmount(); await act(async()=>finish(task("succeeded")));
  expect(done).not.toHaveBeenCalled(); expect(getAgentTask).toHaveBeenCalledTimes(1);
});
