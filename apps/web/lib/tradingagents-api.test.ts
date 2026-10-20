import {beforeEach, expect, it, vi} from "vitest";
import {getTradingAgentsResearch} from "./api/agent";
import {getJson} from "./api/internal";

vi.mock("./api/internal", () => ({getJson: vi.fn(), getJsonArray: vi.fn(), sendJson: vi.fn()}));
const fixture = () => ({symbol: "002121", enabled: false, executable: false, blockers: [], admission: {state: "not_admitted"}, verification: "unverified_full_graph_research", production_eligible: false, latest: null, task: null});
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
