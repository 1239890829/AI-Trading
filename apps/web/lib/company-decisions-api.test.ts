import {afterEach, describe, expect, it, vi} from "vitest";
import {getJson} from "./api/internal";
import {getCompanyRelationResearch, validateCompanyRelationView} from "./api/company-decisions";

vi.mock("./api/internal", () => ({getJson: vi.fn()}));
const identity = {symbol: "003021", event_id: 10, version_id: 20, observation_id: 30};
const base = () => ({...identity, state: "not_requested", protocol: "company-relation-decisions-v1", model: "gpt-6-luna",
  verification: "unvalidated_model_research", human_gold_count: 0, eligibility_changed: false,
  business_effect_verified: false, probabilities_are_accuracy: false, research_only: true,
  source_ready: true, configured: true, can_request: true, result: null});
const ready = () => ({...base(), state: "ready", can_request: false, task_id: "d".repeat(32), result: {
  items: [{fact_id: "report:0", type: "choice", choice: "insufficient", fact: {id: "report:0", kind: "product",
    quote: "公司主营为光模块。", document_title: "2026年半年度报告", period: "2026H1", page: 2,
    url: "https://static.cninfo.com.cn/finalpage/2026-08-15/1234567890.PDF", sha256: "a".repeat(64),
    published_at: "2026-08-15T23:59:59+08:00"}}], coverage: {total: 1, answered: 1, refused: 0},
  generated_at: "2026-10-30T11:00:00+08:00", source_as_of: "2026-10-30T10:00:00+08:00",
  valid_until: "2026-10-30T16:00:00+08:00", input_sha256: "b".repeat(64), usage_id: "c".repeat(32),
  elapsed_seconds: 0.3, actual_cost_usd: null}});

afterEach(() => {vi.restoreAllMocks(); vi.clearAllMocks();});
describe("company relation saved-result contract", () => {
  it("reads the exact event identity once with caller cancellation", async () => {
    vi.mocked(getJson).mockResolvedValue({data: base(), meta: {provider: "saved", is_realtime: false,
      is_stale: false, last_success_refresh: null, generated_at: "2026-10-30T11:00:00+08:00"}});
    const controller = new AbortController();
    expect((await getCompanyRelationResearch(identity, controller.signal)).state).toBe("not_requested");
    expect(getJson).toHaveBeenCalledExactlyOnceWith("/api/agent/research/company-relations/003021?event_id=10&version_id=20&observation_id=30",
      undefined, {signal: controller.signal});
  });
  it("accepts a saved evidence-bound abstention without inventing accuracy", () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-30T12:00:00+08:00"));
    expect(validateCompanyRelationView(ready(), identity).result?.items[0].choice).toBe("insufficient");
  });
  it.each(["symbol", "event_id", "version_id", "observation_id", "protocol", "model", "human_gold_count"])("rejects wrong %s", field => {
    expect(() => validateCompanyRelationView({...base(), [field]: "wrong"}, identity)).toThrow();
  });
  it("rejects permission-like readiness without real prerequisites", () => {
    expect(() => validateCompanyRelationView({...base(), source_ready: false}, identity)).toThrow();
    expect(() => validateCompanyRelationView({...base(), configured: false}, identity)).toThrow();
  });
  it.each(["expired", "source-future", "unsafe-url", "duplicates", "counts", "bad-choice", "refusal-choice"])("rejects %s", cause => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-30T12:00:00+08:00"));
    const value = ready();
    if (cause === "expired") value.result.valid_until = "2026-10-30T11:00:00+08:00";
    if (cause === "source-future") value.result.items[0].fact.published_at = "2026-11-01T11:00:00+08:00";
    if (cause === "unsafe-url") value.result.items[0].fact.url = "javascript:alert(1)";
    if (cause === "duplicates") value.result.items.push(value.result.items[0]);
    if (cause === "counts") value.result.coverage.answered = 2;
    if (cause === "bad-choice") value.result.items[0].choice = "buy";
    if (cause === "refusal-choice") value.result.items[0].type = "refusal";
    expect(() => validateCompanyRelationView(value, identity)).toThrow();
  });
  it("rejects invalid input before any read", async () => {
    await expect(getCompanyRelationResearch({...identity, observation_id: 0})).rejects.toThrow();
    expect(getJson).not.toHaveBeenCalled();
  });
});
