import {afterEach, expect, it, vi} from "vitest";
import {getLimitUpPoolSnapshot} from "./api/market";

afterEach(() => vi.unstubAllGlobals());
const empty = () => ({trade_date: "2026-10-14", pool: [], break_pool: [], coverage: {limit_up: {state: "success", error: null}, limit_break: {state: "success", error: null}}, qualification_observations: {}});
it("enhanced qualification is opt-in and preserves independently unknown break coverage", async () => {
  const data = {...empty(), break_pool: null, coverage: {...empty().coverage, limit_break: {state: "error", error: "synthetic unavailable"}}};
  const fetcher = vi.fn(async (_url: RequestInfo | URL) => new Response(JSON.stringify({data, meta: {}})));
  vi.stubGlobal("fetch", fetcher);
  expect(await getLimitUpPoolSnapshot("2026-10-14", {includeBreaks: true})).toEqual(data);
  expect(String(fetcher.mock.calls[0][0])).toContain("include_breaks=true");
  expect(String(fetcher.mock.calls[0][0])).toContain("date=2026-10-14");
  await getLimitUpPoolSnapshot("2026-10-14");
  expect(String(fetcher.mock.calls[1][0])).not.toContain("include_breaks");
});
it.each([
  {...empty(), trade_date: "2026-10-13"}, {...empty(), coverage: undefined},
  {...empty(), break_pool: undefined}, {...empty(), break_pool: [{trade_date: "2026-10-13"}]},
])("rejects wrong-date or incomplete enhanced envelopes", async data => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({data, meta: {}}))));
  await expect(getLimitUpPoolSnapshot("2026-10-14", {includeBreaks: true})).rejects.toThrow(/身份或覆盖状态/);
});
