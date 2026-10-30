import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {useDeviceAccess} from "@/components/access/device-access";
import {createAgentTask, type AgentTask} from "@/lib/api/agent";
import {ApiError} from "@/lib/api/client";
import {getCompanyRelationResearch, type CompanyRelationView} from "@/lib/api/company-decisions";
import {CompanyRelationResearch} from "./company-relation-research";

vi.mock("@/components/access/device-access", () => ({useDeviceAccess: vi.fn()}));
vi.mock("@/lib/api/agent", () => ({createAgentTask: vi.fn()}));
vi.mock("@/lib/api/company-decisions", async original => ({...await original<typeof import("@/lib/api/company-decisions")>(),
  getCompanyRelationResearch: vi.fn()}));
const identity = {symbol: "003021", event_id: 10, version_id: 20, observation_id: 30};
const empty = (): CompanyRelationView => ({...identity, state: "not_requested", protocol: "company-relation-decisions-v1",
  model: "gpt-6-luna", verification: "unvalidated_model_research", human_gold_count: 0,
  eligibility_changed: false, research_only: true, source_ready: true, configured: true, can_request: true, result: null});
function ready(): CompanyRelationView {
  const fact = {id: "report:0", kind: "product" as const, quote: "公司主营为光模块。", document_title: "2026年半年度报告",
    period: "2026H1", page: 2, url: "https://static.cninfo.com.cn/finalpage/2026-08-15/1234567890.PDF",
    sha256: "a".repeat(64), published_at: "2026-08-15T23:59:59+08:00"};
  return {...empty(), state: "ready", can_request: false, task_id: "d".repeat(32), result: {
    items: [{fact_id: "report:0", type: "choice", choice: "insufficient", fact},
      {fact_id: "report:1", type: "refusal", fact: {...fact, id: "report:1", kind: "customer"}}],
    coverage: {total: 2, answered: 1, refused: 1}, generated_at: "2026-10-30T11:00:00+08:00",
    source_as_of: "2026-10-30T10:00:00+08:00", valid_until: "2026-10-30T16:00:00+08:00",
    input_sha256: "b".repeat(64), usage_id: "c".repeat(32), elapsed_seconds: 0.3, actual_cost_usd: null}};
}
const task = (): AgentTask => ({id: "d".repeat(32), type: "company_relation_research", status: "queued", params: {...identity},
  steps: [], result_ref: null, error: null, risk_level: "L0", created_by: "maintenance",
  created_at: null, started_at: null, finished_at: null});
function maintenance(value: boolean) {
  vi.mocked(useDeviceAccess).mockReturnValue({device: {state: "active", maintenance: value}, open: vi.fn(), refresh: vi.fn(async () => {})});
}
async function open() {
  fireEvent.click(screen.getByRole("button", {name: "读取关联核验"}));
  await waitFor(() => expect(getCompanyRelationResearch).toHaveBeenCalled());
  await act(async () => {});
}
beforeEach(() => {
  vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-30T12:00:00+08:00"));
  maintenance(false);
  vi.mocked(getCompanyRelationResearch).mockResolvedValue(empty());
  vi.mocked(createAgentTask).mockResolvedValue(task());
});
afterEach(() => {cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); vi.useRealTimers();});

describe("manual saved company-relation reader", () => {
  it("does not read on mount or visibility, reads once on expansion and never generates for ordinary readers", async () => {
    render(<CompanyRelationResearch identity={identity}/>);
    expect(getCompanyRelationResearch).not.toHaveBeenCalled();
    fireEvent(document, new Event("visibilitychange"));
    expect(getCompanyRelationResearch).not.toHaveBeenCalled();
    await open();
    expect(screen.getByText("尚无本版本核验记录")).toBeTruthy();
    expect(screen.queryByRole("button", {name: "请求关联核验"})).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "收起关联核验"}));
    fireEvent.click(screen.getByRole("button", {name: "读取关联核验"}));
    fireEvent(document, new Event("visibilitychange"));
    await act(async () => {});
    expect(getCompanyRelationResearch).toHaveBeenCalledTimes(1);
    expect(createAgentTask).not.toHaveBeenCalled();
  });
  it("reads saved answers for ordinary users with evidence filters and optional original text", async () => {
    vi.mocked(getCompanyRelationResearch).mockResolvedValue(ready());
    render(<CompanyRelationResearch identity={identity}/>); await open();
    const list = screen.getByRole("list", {name: "逐条公司依据核验"});
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText("证据不够")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "拒答 1"}));
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(within(list).getByText("客户")).toBeTruthy();
    expect(within(list).getByText("公司主营为光模块。").closest("details")?.open).toBe(false);
    fireEvent.click(within(list).getByText("客户").closest("summary")!);
    expect(within(list).getByText("公司主营为光模块。").closest("details")?.open).toBe(true);
    expect(within(list).getByRole("link", {name: "查看披露原文"}).getAttribute("rel")).toBe("noopener noreferrer");
    expect(createAgentTask).not.toHaveBeenCalled();
  });
  it("does not regenerate a saved ready answer even for maintenance", async () => {
    maintenance(true); vi.mocked(getCompanyRelationResearch).mockResolvedValue({...ready(), can_request: true});
    render(<CompanyRelationResearch identity={identity}/>); await open();
    expect(screen.getByRole("button", {name: "请求关联核验"})).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("button", {name: "请求关联核验"}));
    expect(createAgentTask).not.toHaveBeenCalled();
  });
  it("retires a result at its actual expiry without any background request", async () => {
    vi.mocked(Date.now).mockRestore();
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-30T15:59:59+08:00"));
    vi.mocked(getCompanyRelationResearch).mockResolvedValue(ready());
    render(<CompanyRelationResearch identity={identity}/>);
    fireEvent.click(screen.getByRole("button", {name: "读取关联核验"}));
    await act(async () => {});
    expect(screen.getByRole("list", {name: "逐条公司依据核验"})).toBeTruthy();
    act(() => {vi.advanceTimersByTime(1000);});
    expect(screen.queryByRole("list", {name: "逐条公司依据核验"})).toBeNull();
    expect(screen.getByText(/旧判断不作当前依据/)).toBeTruthy();
    expect(getCompanyRelationResearch).toHaveBeenCalledTimes(1);
    expect(createAgentTask).not.toHaveBeenCalled();
  });
  it("submits exact params once and only rereads after a separate user action", async () => {
    maintenance(true);
    let finish!: (value: AgentTask) => void;
    vi.mocked(createAgentTask).mockImplementation(() => new Promise(resolve => {finish = resolve;}));
    render(<CompanyRelationResearch identity={identity}/>); await open();
    const request = screen.getByRole("button", {name: "请求关联核验"});
    fireEvent.click(request); fireEvent.click(request);
    expect(createAgentTask).toHaveBeenCalledExactlyOnceWith("company_relation_research", identity);
    await act(async () => finish(task()));
    expect(getCompanyRelationResearch).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/尚未确认保存结果/)).toBeTruthy();
    vi.mocked(getCompanyRelationResearch).mockResolvedValue({...ready(), task_id: "a".repeat(32)});
    fireEvent.click(screen.getByRole("button", {name: "重新读取"})); await act(async () => {});
    expect(screen.queryByRole("list", {name: "逐条公司依据核验"})).toBeNull();
    vi.mocked(getCompanyRelationResearch).mockResolvedValue(ready());
    fireEvent.click(screen.getByRole("button", {name: "重新读取"})); await act(async () => {});
    expect(screen.getByRole("list", {name: "逐条公司依据核验"})).toBeTruthy();
    expect(createAgentTask).toHaveBeenCalledTimes(1);
  });
  it.each(["network", "task-id", "params"])("locks an uncertain %s submission across collapse and reread", async cause => {
    maintenance(true);
    if (cause === "network") vi.mocked(createAgentTask).mockRejectedValue(new Error("lost"));
    else vi.mocked(createAgentTask).mockResolvedValue(cause === "task-id" ? {...task(), id: "wrong"} : {...task(), params: {...identity, version_id: 21}});
    render(<CompanyRelationResearch identity={identity}/>); await open();
    fireEvent.click(screen.getByRole("button", {name: "请求关联核验"}));
    await screen.findByText(/提交结果未确认/);
    fireEvent.click(screen.getByRole("button", {name: "收起关联核验"}));
    fireEvent.click(screen.getByRole("button", {name: "读取关联核验"}));
    fireEvent.click(screen.getByRole("button", {name: "重新读取"})); await act(async () => {});
    expect(screen.getByRole("button", {name: "请求关联核验"})).toHaveProperty("disabled", true);
    expect(createAgentTask).toHaveBeenCalledTimes(1);
  });
  it("refuses generation after read failure, missing prerequisites or a revoked maintenance permission", async () => {
    maintenance(true); vi.mocked(getCompanyRelationResearch).mockResolvedValue({...empty(), source_ready: false,
      can_request: false, reason_code: "company_evidence_missing"});
    const view = render(<CompanyRelationResearch identity={identity}/>); await open();
    expect(screen.getByText("尚无已保存的公司原文")).toBeTruthy();
    expect(screen.getByRole("button", {name: "请求关联核验"})).toHaveProperty("disabled", true);
    vi.mocked(getCompanyRelationResearch).mockRejectedValue(new Error("read failed"));
    fireEvent.click(screen.getByRole("button", {name: "重新读取"})); await screen.findByText(/读取失败/);
    expect(screen.getByRole("button", {name: "请求关联核验"})).toHaveProperty("disabled", true);
    maintenance(false); view.rerender(<CompanyRelationResearch identity={identity}/>);
    expect(screen.queryByRole("button", {name: "请求关联核验"})).toBeNull();
    expect(createAgentTask).not.toHaveBeenCalled();
  });
  it("a definitive forbidden response is honest and not automatically retried", async () => {
    maintenance(true); vi.mocked(createAgentTask).mockRejectedValue(new ApiError(403, "forbidden", "forbidden"));
    render(<CompanyRelationResearch identity={identity}/>); await open();
    fireEvent.click(screen.getByRole("button", {name: "请求关联核验"})); await screen.findByText(/请求未获准/);
    expect(createAgentTask).toHaveBeenCalledTimes(1);
  });
  it("aborts an old reference and ignores its late response without auto-reading the new one", async () => {
    let resolve!: (value: CompanyRelationView) => void;
    vi.mocked(getCompanyRelationResearch).mockImplementation(() => new Promise(done => {resolve = done;}));
    const view = render(<CompanyRelationResearch identity={identity}/>); await open();
    const signal = vi.mocked(getCompanyRelationResearch).mock.calls[0][1];
    view.rerender(<CompanyRelationResearch identity={{...identity, version_id: 21}}/>);
    expect(signal?.aborted).toBe(true);
    await act(async () => resolve(ready()));
    expect(screen.queryByRole("list", {name: "逐条公司依据核验"})).toBeNull();
    expect(getCompanyRelationResearch).toHaveBeenCalledTimes(1);
    expect(createAgentTask).not.toHaveBeenCalled();
  });
});
