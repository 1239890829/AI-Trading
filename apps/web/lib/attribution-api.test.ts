import {afterEach, describe, expect, it, vi} from "vitest";
import {getEventAttribution, getStockAttribution} from "./api/attribution";
import {getJson} from "./api/internal";

vi.mock("./api/internal", () => ({getJson: vi.fn()}));
const meta = {provider: "event-store", is_realtime: false, is_stale: false, last_success_refresh: null, generated_at: "2026-10-12T09:32:00+08:00"};
afterEach(() => {vi.clearAllMocks();});
describe("attribution response identity", () => {
  it("keeps symbol identity through the existing authenticated API client", async () => {
    vi.mocked(getJson).mockResolvedValue({data: {schema_version: 1, symbol: "600825", count: 0, items: []}, meta});
    expect((await getStockAttribution("600825")).symbol).toBe("600825");
    expect(getJson).toHaveBeenCalledWith("/api/events/symbol/600825/attribution?limit=12");
  });
  it.each([{schema_version: 1, symbol: "600127", items: []}, {schema_version: 2, symbol: "600825", items: []}, {schema_version: 1, symbol: "600825", items: null}])("rejects a mismatched stock/version/shape", async data => {
    vi.mocked(getJson).mockResolvedValue({data, meta});
    await expect(getStockAttribution("600825")).rejects.toThrow("身份不匹配");
  });
  it("does not accept a different event under the requested event title", async () => {
    vi.mocked(getJson).mockResolvedValue({data: {schema_version: 1, symbol: null, items: [{event_id: 20}]}, meta});
    await expect(getEventAttribution(18)).rejects.toThrow("身份不匹配");
  });
});
