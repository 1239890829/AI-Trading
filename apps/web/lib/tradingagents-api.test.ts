import {beforeEach, expect, it, vi} from "vitest";
import {getAgentTask, getTradingAgentsResearch, recoverTradingAgentsResearch} from "./api/agent";
import {getJson, sendJson} from "./api/internal";

vi.mock("./api/internal", () => ({getJson: vi.fn(), getJsonArray: vi.fn(), sendJson: vi.fn()}));
const fixture = () => ({symbol: "002121", enabled: false, executable: false, blockers: [], admission: {state: "not_admitted"}, verification: "unverified_full_graph_research", production_eligible: false, latest: null, task: null});
const lineage = () => ({version: 1, source_task_id: "1".repeat(32), source_run_id: "2".repeat(32), recovery_task_id: "3".repeat(32),
  source_method_sha256: "a".repeat(64), recovery_method_sha256: "b".repeat(64), reused_original_outputs: 9,
  new_call_from: 11, new_call_to: 24, inherited_unknown_usage_count: 1});
const completed = () => ({...fixture(), latest: {symbol: "002121", version: 1, state: "completed", run_id: "2".repeat(32),
  generated_at: "2026-10-21T17:00:00+08:00", as_of: "2026-10-21T15:30:12+08:00", generated_today: true,
  upstream_sha: "upstream", input_sha256: "c".repeat(64), method_sha256: "b".repeat(64),
  verification: "unverified_full_graph_research", production_eligible: false, review_state: "unreviewed",
  review_summary: {finite_failures: 0, peer_review_required: 7, source_verified: false, independent_human_gold: false},
  reports: Object.fromEntries(["market_report", "sentiment_report", "news_report", "fundamentals_report", "investment_plan", "trader_investment_plan", "final_trade_decision"].map(key => [key, "原报告保留，尚待复核"]))}});
beforeEach(() => {vi.clearAllMocks();});

it("accepts a completed report reuse only with the exact succeeded task result", async () => {
  const task = {id: "task", status: "succeeded", result_run_id: "2".repeat(32), reused_identical_selection: true};
  vi.mocked(getJson).mockResolvedValue({data: {...completed(), task}, meta: {}} as never);
  expect((await getTradingAgentsResearch("002121")).task?.reused_identical_selection).toBe(true);
});

it.each([
  {status: "running", result_run_id: "2".repeat(32), reused_identical_selection: true},
  {status: "succeeded", result_run_id: null, reused_identical_selection: true},
  {status: "succeeded", result_run_id: "2".repeat(32), reused_identical_selection: "true"},
])("rejects an unsupported report reuse claim %j", async task => {
  vi.mocked(getJson).mockResolvedValue({data: {...completed(), task}, meta: {}} as never);
  await expect(getTradingAgentsResearch("002121")).rejects.toThrow("身份或报告不完整");
});

it("reads exactly the declared ordinary route without task submission or arbitrary fields", async () => {
  vi.mocked(getJson).mockResolvedValue({data: fixture(), meta: {}} as never);
  expect((await getTradingAgentsResearch("002121")).symbol).toBe("002121");
  expect(getJson).toHaveBeenCalledWith("/api/agent/research/tradingagents/002121");
});

it.each(["sh000001", "../002121", "002121?model=other"])("rejects invalid symbol %s before a request", async symbol => {
  await expect(getTradingAgentsResearch(symbol)).rejects.toThrow("六位证券代码");
  expect(getJson).not.toHaveBeenCalled();
});

it.each([
  {...fixture(), symbol: "600276"},
  {...fixture(), executable: true},
  {...fixture(), production_eligible: true},
  {...fixture(), latest: {symbol: "600276", reports: {}}},
  {...fixture(), latest: {symbol: "002121", verification: "unverified_full_graph_research", production_eligible: false, reports: {market_report: "只到一个报告"}}},
])("rejects foreign, contradictory or incomplete report responses", async data => {
  vi.mocked(getJson).mockResolvedValue({data, meta: {}} as never);
  await expect(getTradingAgentsResearch("002121")).rejects.toThrow("身份或报告不完整");
});

it("keeps existing reports without recovery provenance unchanged and accepts the single bounded recovery contract", async () => {
  const old = completed();
  vi.mocked(getJson).mockResolvedValueOnce({data: old, meta: {}} as never);
  expect((await getTradingAgentsResearch("002121")).latest).toEqual(old.latest);
  const recovered = {...completed(), latest: {...completed().latest, recovery_lineage: lineage()}};
  vi.mocked(getJson).mockResolvedValueOnce({data: recovered, meta: {}} as never);
  const report = (await getTradingAgentsResearch("002121")).latest!;
  expect(report.recovery_lineage).toEqual(lineage());
  expect(report.as_of).toBe("2026-10-21T15:30:12+08:00");
});

it.each([null, [], {...lineage(), version: 2}, {...lineage(), source_task_id: "not-a-task"},
  {...lineage(), source_run_id: "A".repeat(32)}, {...lineage(), recovery_task_id: "../task"},
  {...lineage(), source_method_sha256: "old-method"}, {...lineage(), recovery_method_sha256: "d".repeat(64)},
  {...lineage(), reused_original_outputs: true}, {...lineage(), reused_original_outputs: -1}, {...lineage(), reused_original_outputs: 10001},
  {...lineage(), inherited_unknown_usage_count: -1}, {...lineage(), inherited_unknown_usage_count: 10001},
  {...lineage(), new_call_from: 0}, {...lineage(), new_call_to: 10}, {...lineage(), new_call_to: Number.MAX_SAFE_INTEGER + 1},
  {...lineage(), continuation_method_sha256: "b".repeat(64)}, {...lineage(), context: "unrequested context must never appear"},
])("rejects malformed, conflicting or undeclared recovery provenance without inventing cost certainty %j", async recovery_lineage => {
  vi.mocked(getJson).mockResolvedValue({data: {...completed(), latest: {...completed().latest, recovery_lineage}}, meta: {}} as never);
  await expect(getTradingAgentsResearch("002121")).rejects.toThrow("身份或报告不完整");
});

it.each([0, 10000])("accepts declared count boundary %s without inferring a full bill", async count => {
  const recovery_lineage = {...lineage(), reused_original_outputs: count, inherited_unknown_usage_count: count};
  vi.mocked(getJson).mockResolvedValue({data: {...completed(), latest: {...completed().latest, recovery_lineage}}, meta: {}} as never);
  expect((await getTradingAgentsResearch("002121")).latest!.recovery_lineage).toEqual(recovery_lineage);
});

it("uses the saved-only read and retains valid originals when a role projection is incomplete",async()=>{
  const value={...completed(),latest:{...completed().latest,roles:[{role:"Market Analyst",call_id:1,output_sha256:"d".repeat(64),fields:{content:"唯一原答"}}],role_projection_state:"available"}};
  vi.mocked(getJson).mockResolvedValueOnce({data:value,meta:{}} as never);
  const result=await getTradingAgentsResearch("002121",{savedOnly:true});
  expect(getJson).toHaveBeenCalledWith("/api/agent/research/tradingagents/002121?saved_only=true");
  expect(result.latest!.reports).toEqual(value.latest.reports);
  expect(result.latest!.roles).toBeUndefined();
  expect(result.latest!.role_projection_state).toBe("unavailable");
  expect(value.latest.roles).toHaveLength(1);
});

const recovery = () => ({state: "available", source_task_id: "1".repeat(32), run_id: "2".repeat(32), blockers: []});
it("checks recovery explicitly without checking normal launch conditions or submitting a task", async () => {
  const value = {...fixture(), recovery: recovery()};
  vi.mocked(getJson).mockResolvedValue({data: value, meta: {}} as never);
  expect((await getTradingAgentsResearch("002121", {savedOnly: true, checkRecovery: true})).recovery).toEqual(recovery());
  expect(getJson).toHaveBeenCalledWith("/api/agent/research/tradingagents/002121/recovery");
  expect(sendJson).not.toHaveBeenCalled();
});

it.each([
  {...recovery(), state: "other"}, {...recovery(), source_task_id: null}, {...recovery(), run_id: "../run"},
  {...recovery(), blockers: [{code: "blocked", message: "已阻断"}]}, {...recovery(), blockers: null},
])("rejects contradictory or unbound recovery conditions %j", async value => {
  vi.mocked(getJson).mockResolvedValue({data: {...fixture(), recovery: value}, meta: {}} as never);
  await expect(getTradingAgentsResearch("002121", {checkRecovery: true})).rejects.toThrow("身份或报告不完整");
});

it("does not accept an available recovery grant from an ordinary unchecked response", async () => {
  vi.mocked(getJson).mockResolvedValue({data: {...fixture(), recovery: recovery()}, meta: {}} as never);
  await expect(getTradingAgentsResearch("002121", {savedOnly: true})).rejects.toThrow("身份或报告不完整");
});

it("recovers only the exact original task and run without model, grant or runtime parameters", async () => {
  vi.mocked(sendJson).mockResolvedValue({data: {id: "3".repeat(32)}, meta: {}} as never);
  await recoverTradingAgentsResearch("1".repeat(32), "2".repeat(32));
  expect(sendJson).toHaveBeenCalledExactlyOnceWith(`/api/agent/tasks/${"1".repeat(32)}/recover`, "POST", {run_id: "2".repeat(32)});
});

it.each([["../task", "2".repeat(32)], ["1".repeat(32), "A".repeat(32)]])("rejects invalid recovery identity %j before a request", async (taskId, runId) => {
  await expect(recoverTradingAgentsResearch(taskId, runId)).rejects.toThrow("准确的原任务与运行编号");
  expect(sendJson).not.toHaveBeenCalled();
});

it.each(["running", "failed"])("rejects a %s task falsely claiming a saved result_run_id", async status => {
  vi.mocked(getJson).mockResolvedValue({data: {...completed(), task: {id: "1".repeat(32), status, result_run_id: "2".repeat(32)}}, meta: {}} as never);
  await expect(getTradingAgentsResearch("002121")).rejects.toThrow("身份或报告不完整");
});

it.each(["not_checked", "blocked", "none", "unknown"])("retains explicit non-executable recovery state %s without inferring permission", async state => {
  const value = {...fixture(), recovery: {state, source_task_id: null, run_id: null, blockers: []}};
  vi.mocked(getJson).mockResolvedValue({data: value, meta: {}} as never);
  expect((await getTradingAgentsResearch("002121", {savedOnly: true})).recovery?.state).toBe(state);
});

it("retains the successful task's exact report run identity without replacing it with the latest symbol report", async () => {
  const value = {...completed(), task: {id: "1".repeat(32), status: "succeeded", result_run_id: "4".repeat(32)}};
  vi.mocked(getJson).mockResolvedValue({data: value, meta: {}} as never);
  const result = await getTradingAgentsResearch("002121", {savedOnly: true});
  expect(result.task?.result_run_id).toBe("4".repeat(32));
  expect(result.latest!.run_id).toBe("2".repeat(32));
});

it("reads only the exact submitted maintenance task and rejects a foreign task response", async () => {
  const id="1".repeat(32);
  vi.mocked(getJson).mockResolvedValueOnce({data:{id},meta:{}} as never);
  expect((await getAgentTask(id)).id).toBe(id);
  expect(getJson).toHaveBeenCalledWith(`/api/agent/tasks/${id}`);
  vi.mocked(getJson).mockResolvedValueOnce({data:{id:"2".repeat(32)},meta:{}} as never);
  await expect(getAgentTask(id)).rejects.toThrow("任务编号不符");
});

it.each(["../task", "A".repeat(32), ""])("rejects unsafe single-task read identity %s", async id => {
  await expect(getAgentTask(id)).rejects.toThrow("准确任务编号");
  expect(getJson).not.toHaveBeenCalled();
});
