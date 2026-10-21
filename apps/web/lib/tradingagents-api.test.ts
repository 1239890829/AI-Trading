import {beforeEach, expect, it, vi} from "vitest";
import {getTradingAgentsResearch} from "./api/agent";
import {getJson} from "./api/internal";

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
