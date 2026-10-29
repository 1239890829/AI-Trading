import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {inheritQuoteFields, quoteFieldSourceText, quoteMatchesRequest} from "./quote-field-sources";
import type {Quote} from "@/types/market";

const primary: Quote = {symbol: "600519", market: "SH", source: "ths", quality: "high", quality_reasons: [],
  price: 20, data_timestamp: "2026-10-29T15:00:00+08:00", received_at: "2026-10-29T15:00:01+08:00"};
const supplement: Quote = {...primary, source: "tencent", pe_ttm: 32, pb: -0.7, total_mktcap_yi: 100,
  limit_up_price: 22, limit_down_price: 18};

beforeEach(() => {vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-29T16:00:01+08:00"));});
afterEach(() => vi.useRealTimers());

describe("quote field inheritance", () => {
  it("moves each field and its real origin together while retaining primary facts and donor identity", () => {
    const before = JSON.stringify([primary, supplement]);
    const merged = inheritQuoteFields(primary, [supplement]);
    expect(merged.price).toBe(20);
    expect(merged.source).toBe("ths");
    expect(merged.data_timestamp).toBe(primary.data_timestamp);
    expect(merged.pb).toBe(-0.7);
    expect(merged.field_sources?.pe_ttm).toEqual({source: "tencent", data_timestamp: supplement.data_timestamp, received_at: supplement.received_at});
    expect(quoteFieldSourceText(merged, "pe_ttm")).toContain("腾讯");
    expect(JSON.stringify([primary, supplement])).toBe(before);
  });
  it.each([
    {symbol: "000001"}, {market: "SZ"}, {market: null}, {quality: "low"}, {quality: "stale"}, {quality: "invalid"},
    {data_timestamp: null}, {data_timestamp: "2026-10-29T15:00:00"}, {data_timestamp: "2026-02-30T15:00:00+08:00"},
    {data_timestamp: "2026-10-28T15:00:00+08:00"}, {received_at: "bad"},
  ] as Partial<Quote>[])("never borrows an incompatible or untrusted observation %j", change => {
    const merged = inheritQuoteFields(primary, [{...supplement, ...change}]);
    expect(merged.pe_ttm).toBeUndefined();
    expect(merged.limit_up_price).toBeUndefined();
    expect(merged.field_sources).toEqual({});
  });
  it("uses source clock difference for valuations and same source day for fixed limits", () => {
    const old = {...supplement, data_timestamp: "2026-10-29T14:58:59+08:00"};
    const merged = inheritQuoteFields(primary, [old]);
    expect(merged.pe_ttm).toBeUndefined();
    expect(merged.limit_up_price).toBe(22);
    expect(inheritQuoteFields(primary, [{...old, data_timestamp: "2026-10-29T14:59:00+08:00"}]).pe_ttm).toBe(32);
  });
  it("checks the field's original clock rather than laundering it through a newer row", () => {
    const donor = {...supplement, field_sources: {pe_ttm: {source: "tencent", data_timestamp: "2026-10-28T15:00:00+08:00", received_at: supplement.received_at}}};
    expect(inheritQuoteFields(primary, [donor]).pe_ttm).toBeUndefined();
  });
  it("does not inherit unknown clocks or non-positive capitalisation, keeps real negative ratios", () => {
    const merged = inheritQuoteFields(primary, [{...supplement, pe_ttm: -3, pb: -0.2, total_mktcap_yi: -100, float_mktcap_yi: Infinity, limit_up_price: 0}]);
    expect(merged.pe_ttm).toBe(-3);
    expect(merged.pb).toBe(-0.2);
    expect(merged.total_mktcap_yi).toBeUndefined();
    expect(merged.float_mktcap_yi).toBeUndefined();
    expect(merged.limit_up_price).toBeUndefined();
  });
  it("retains native values on new frames, but valid REST can refresh valuations with its source", () => {
    const live = {...primary, pe_ttm: 12, limit_up_price: 21};
    expect(inheritQuoteFields(live, [supplement]).pe_ttm).toBe(12);
    const merged = inheritQuoteFields(live, [supplement], true);
    expect(merged.pe_ttm).toBe(32);
    expect(merged.field_sources?.pe_ttm?.source).toBe("tencent");
    expect(merged.limit_up_price).toBe(21);
    expect(quoteFieldSourceText(merged, "limit_up_price")).toContain("同花顺");
  });
  it("native missing clocks remain unknown in the tooltip", () => {
    expect(quoteFieldSourceText({...primary, pe_ttm: 12, data_timestamp: null}, "pe_ttm")).toContain("截至 未知");
  });
  it("does not match another exchange or prefixed market mismatch", () => {
    expect(quoteMatchesRequest(primary, "600519")).toBe(true);
    expect(quoteMatchesRequest({...primary, market: "SZ"}, "600519")).toBe(false);
    expect(quoteMatchesRequest({...primary, symbol: "sz600519"}, "600519")).toBe(false);
  });
});


describe("normal closed route-shaped supplement", () => {
  const closed = {...supplement, quality: "stale" as const, quality_reasons: ["market_closed"],
    display_session: {phase: "closed" as const, trade_date: "2026-10-29", as_of: "2026-10-29T16:00:00+08:00", trade_day_result: true,
      expected_source_date: "2026-10-29", expected_source_after: "2026-10-29T14:59:50+08:00"}};
  it("borrows a confirmed tail from a normal paused route without upgrading either fact", () => {
    const main = {...primary, quality: "stale" as const, quality_reasons: ["market_closed"], display_session: closed.display_session};
    const merged = inheritQuoteFields(main, [closed]);
    expect(merged.pe_ttm).toBe(32);
    expect(merged.quality).toBe("stale");
    expect(merged.quality_reasons).toEqual(["market_closed"]);
    expect(merged.field_sources?.pe_ttm?.source).toBe("tencent");
    expect(closed.quality).toBe("stale");
  });
  it.each([
    {quality_reasons: ["refresh_failed"]}, {quality_reasons: ["source_time_regress_ignored"]},
    {data_timestamp: "2026-10-29T10:00:00+08:00"},
    {display_session: {...closed.display_session, expected_source_after: null}},
  ])("rejects a failed, old, or unproved paused row %j", change => {
    expect(inheritQuoteFields(primary, [{...closed, ...change}]).pe_ttm).toBeUndefined();
  });
});


describe("future and impossible source clocks", () => {
  it.each([
    {data_timestamp: "2026-10-29T15:00:30+08:00", received_at: "2026-10-29T15:00:31+08:00"},
    {received_at: "2026-10-29T15:00:30+08:00"},
    {received_at: "2026-10-29T14:54:59+08:00"},
    {field_sources: {pe_ttm: {source: "tencent", data_timestamp: "2026-10-29T15:00:30+08:00", received_at: "2026-10-29T15:00:31+08:00"}}},
    {field_sources: {pe_ttm: {source: "tencent", data_timestamp: "2026-10-29T15:00:05+08:00", received_at: "2026-10-29T14:55:04+08:00"}}},
  ])("rejects future or out-of-order clocks %j", change => {
    vi.setSystemTime(new Date("2026-10-29T15:00:10+08:00"));
    const merged = inheritQuoteFields(primary, [{...supplement, ...change}]);
    expect(merged.pe_ttm).toBeUndefined();
    expect(merged.field_sources?.pe_ttm).toBeUndefined();
  });
});


it("keeps the existing 300 second causal skew boundary", () => {
  vi.setSystemTime(new Date("2026-10-29T15:00:10+08:00"));
  const donor = {...supplement, received_at: "2026-10-29T14:55:00+08:00"};
  expect(inheritQuoteFields(primary, [donor]).pe_ttm).toBe(32);
});

it.each([
  {price: 10, prev_close: 10, limit_up_price: 11, limit_down_price: 9},
  {price: 20, limit_up_price: 19, limit_down_price: 18},
  {price: 20, limit_up_price: 22, limit_down_price: 23},
])("rejects source limits incompatible with the primary or source price %j", limits => {
  const main = {...primary, prev_close: 20};
  const merged = inheritQuoteFields(main, [{...supplement, ...limits}]);
  expect(merged.limit_up_price).toBeUndefined();
  expect(merged.limit_down_price).toBeUndefined();
  expect(merged.field_sources?.limit_up_price).toBeUndefined();
  expect(merged.price).toBe(20);
  expect(merged.source).toBe("ths");
});

it("does not combine individually valid partial donors into equal upper and lower limits", () => {
  const main = {...primary, limit_up_price: null, limit_down_price: null};
  const upOnly = {...primary, source: "tencent", limit_up_price: 20, limit_down_price: null};
  const downOnly = {...primary, source: "tencent", limit_up_price: null, limit_down_price: 20};
  const before = JSON.stringify([main, upOnly, downOnly]);
  const merged = inheritQuoteFields(main, [upOnly, downOnly]);
  expect(merged.limit_up_price).toBe(20);
  expect(merged.limit_down_price).toBeNull();
  expect(merged.field_sources?.limit_up_price?.source).toBe("tencent");
  expect(merged.field_sources?.limit_down_price).toBeUndefined();
  expect(merged.price).toBe(20);
  expect(merged.source).toBe("ths");
  expect(merged.data_timestamp).toBe(main.data_timestamp);
  expect(JSON.stringify([main, upOnly, downOnly])).toBe(before);
});
