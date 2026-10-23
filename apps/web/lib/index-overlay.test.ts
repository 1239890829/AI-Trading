import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {buildIndexOverlay, indexQuoteReference} from "./index-overlay";
import {hasTrustedQuoteObservation} from "./kline-live";
import type {MinutePoint} from "./api";
import type {Quote, QuoteDisplaySession} from "@/types/market";

const now = new Date("2026-10-14T08:08:35+08:00");
const points: MinutePoint[] = [{ts: "2026-10-13T15:00:00+08:00", price: 3030, source: "tencent"}];
const session: QuoteDisplaySession = {phase: "closed", trade_date: "2026-10-14", as_of: now.toISOString(), trade_day_result: true};
function closedQuote(): Quote {
  return {symbol: "000001", market: "SH", source: "tencent", quality: "stale", quality_reasons: ["market_closed"],
    price: 3030, prev_close: 3000, data_timestamp: "2026-10-13T16:15:00+08:00", received_at: now.toISOString(), display_session: {...session}};
}
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => {throw new Error("offline test: unexpected fetch");}));
  vi.stubGlobal("WebSocket", class {constructor() {throw new Error("offline test: unexpected WebSocket");}});
});
afterEach(() => vi.unstubAllGlobals());

describe("index overlay historical display is separate from live eligibility", () => {
  it.each(["09:20:00", "09:27:00"])("confirmed opening phase at%s treats an empty continuous series as waiting", clock => {
    const at = new Date(`2026-10-14T${clock}+08:00`);
    const quote: Quote = {...closedQuote(), data_timestamp: "2026-10-14T09:15:00+08:00", quality_reasons: ["quote_age_exceeded"],
      display_session: {...session, phase: clock === "09:20:00" ? "auction" as const : "pre_open_gap" as const, as_of: at.toISOString()}};
    const before = JSON.stringify(quote);
    expect(buildIndexOverlay([], quote, "2026-10-14", at)).toMatchObject({points: [], mode: "waiting"});
    expect(JSON.stringify(quote)).toBe(before);
  });

  it("confirmed closing auction keeps only a same-day proven tail and does not upgrade stale", () => {
    const at = new Date("2026-10-14T14:58:00+08:00");
    const quote = {...closedQuote(), data_timestamp: "2026-10-14T14:57:00+08:00", quality_reasons: ["quote_age_exceeded"],
      display_session: {...session, phase: "auction" as const, as_of: at.toISOString()}};
    const tail = [{...points[0], ts: "2026-10-14T14:57:00+08:00"}];
    expect(buildIndexOverlay(tail, quote, "2026-10-14", at)).toMatchObject({points: tail, mode: "waiting", waitingPhase: "closing_auction"});
    expect(quote.quality).toBe("stale");
    for (const value of [[], [{...tail[0], ts: "2026-10-14T10:00:00+08:00"}]])
      expect(() => buildIndexOverlay(value, quote, "2026-10-14", at)).toThrow();
    expect(() => buildIndexOverlay(tail, {...quote, data_timestamp: "2026-10-14T10:00:00+08:00"}, "2026-10-14", at)).toThrow();
  });

  it.each([false, true])("closing boundary accepts the final continuous-minute bucket, end-labelled=%s", endLabel => {
    const at = new Date("2026-10-14T14:57:00+08:00");
    const quote = {...closedQuote(), quality: "high" as const, quality_reasons: [], data_timestamp: "2026-10-14T14:56:59+08:00",
      display_session: {...session, phase: "auction" as const, as_of: at.toISOString()}};
    const tail: MinutePoint[] = [{...points[0], ts: `2026-10-14T${endLabel ? "14:57:00" : "14:56:00"}+08:00`,
      ...(endLabel ? {time_label: "interval_end" as const, observation_ts: quote.data_timestamp} : {})}];
    expect(buildIndexOverlay(tail, quote, "2026-10-14", at)).toMatchObject({points: tail, mode: "waiting"});
    expect(quote.quality).toBe("high");
    const aged = {...quote, quality: "stale" as const, quality_reasons: ["quote_age_exceeded"]};
    expect(buildIndexOverlay(tail, aged, "2026-10-14", new Date("2026-10-14T14:59:59+08:00")).mode).toBe("waiting");
    expect(aged.quality).toBe("stale");
    expect(() => buildIndexOverlay([{...tail[0], time_label: null, observation_ts: null, ts: "2026-10-14T14:55:00+08:00"}], quote, "2026-10-14", at)).toThrow();
    expect(() => buildIndexOverlay(tail, {...quote, data_timestamp: "2026-10-14T14:55:59+08:00"}, "2026-10-14", at)).toThrow();
  });

  it("a genuine interval-end label needs its same-response observation clock even during auction", () => {
    const at = new Date("2026-10-14T14:57:30+08:00");
    const quote = {...closedQuote(), quality: "high" as const, quality_reasons: [], data_timestamp: "2026-10-14T14:57:25+08:00",
      display_session: {...session, phase: "auction" as const, as_of: at.toISOString()}};
    const tail: MinutePoint[] = [{...points[0], ts: "2026-10-14T14:58:00+08:00", time_label: "interval_end", observation_ts: quote.data_timestamp}];
    expect(buildIndexOverlay(tail, quote, "2026-10-14", at)).toMatchObject({points: tail, mode: "waiting"});
    expect(buildIndexOverlay([{...tail[0], observation_ts: "2026-10-14T14:57:00+08:00"}], quote, "2026-10-14", at).mode).toBe("waiting");
    expect(() => buildIndexOverlay([{...tail[0], observation_ts: undefined}], quote, "2026-10-14", at)).toThrow();
    expect(() => buildIndexOverlay([{...tail[0], source: "tdx"}], quote, "2026-10-14", at)).toThrow();
    expect(() => buildIndexOverlay([{...tail[0], observation_ts: "2026-10-14T14:59:00+08:00"}], quote, "2026-10-14", at)).toThrow();
  });

  it.each(["identity", "wrong_day", "future_clock", "price", "read_failure", "calendar", "continuous", "expired_phase"])("auction waiting never hides%s", kind => {
    const at = new Date("2026-10-14T09:20:00+08:00");
    const quote: Quote = {...closedQuote(), data_timestamp: "2026-10-14T09:15:00+08:00", quality_reasons: ["quote_age_exceeded"],
      display_session: {...session, phase: "auction" as const, as_of: at.toISOString()},
      ...(kind === "identity" ? {market: "SZ"} : {}), ...(kind === "wrong_day" ? {data_timestamp: "2026-10-13T15:00:00+08:00"} : {}),
      ...(kind === "future_clock" ? {quality: "high" as const, data_timestamp: "2026-10-14T09:21:00+08:00"} : {}),
      ...(kind === "price" ? {price: 0} : {}), ...(kind === "read_failure" ? {quality_reasons: ["refresh_failed"]} : {})};
    if (kind === "calendar") quote.display_session!.trade_day_result = null;
    if (kind === "continuous") quote.display_session = {...quote.display_session!, phase: "continuous"};
    expect(() => buildIndexOverlay([], quote, "2026-10-14", kind === "expired_phase" ? new Date("2026-10-14T09:30:00+08:00") : at)).toThrow();
  });
  it("recovery quote reference uses the same live/historical gate without borrowing stock minutes", () => {
    const quote = closedQuote(), before = JSON.stringify(quote);
    expect(indexQuoteReference(quote, "2026-10-13", now)).toEqual({referenceDate: "2026-10-13", mode: "historical"});
    expect(indexQuoteReference({...quote, quality: "high", quality_reasons: []}, "2026-10-13", now).mode).toBe("live");
    expect(JSON.stringify(quote)).toBe(before);
    for (const reason of ["index_batch_missing", "source_time_regress_ignored", "quote_age_exceeded"])
      expect(() => indexQuoteReference({...quote, quality_reasons: ["market_closed", reason]}, "2026-10-13", now)).toThrow(/基准/);
    expect(() => indexQuoteReference(quote, "2026-10-14", now)).toThrow(/交易日/);
    expect(() => indexQuoteReference({...quote, source: "unknown"}, "2026-10-13", now)).toThrow(/基准/);
  });
  it("closed-only stale same-day data can be displayed with its date without upgrading quality", () => {
    const quote = closedQuote(), before = JSON.stringify({quote, points});
    const result = buildIndexOverlay(points, quote, "2026-10-13", now);
    expect(result).toEqual({points, prevClose: 3000, referenceDate: "2026-10-13", mode: "historical"});
    expect(result.points).toBe(points);
    expect(JSON.stringify({quote, points})).toBe(before);
    expect(quote.quality).toBe("stale");
    expect(hasTrustedQuoteObservation(quote)).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(["high", "medium"] as const)("%s keeps the existing trusted-live path", quality => {
    expect(buildIndexOverlay(points, {...closedQuote(), quality, quality_reasons: [], display_session: undefined}, "2026-10-13", now).mode).toBe("live");
  });

  it("a confirmed weekend can display the matching prior session, without a trading-day guess", () => {
    const saturday = new Date("2026-10-17T10:00:00+08:00");
    const quote = {...closedQuote(), data_timestamp: "2026-10-16T15:00:00+08:00", display_session: {...session, trade_date: "2026-10-17", as_of: saturday.toISOString(), trade_day_result: false}};
    expect(buildIndexOverlay([{...points[0], ts: quote.data_timestamp}], quote, "2026-10-16", saturday).mode).toBe("historical");
  });

  it.each(["low", "invalid"] as const)("%s cannot use the historical exception", quality => {
    expect(() => buildIndexOverlay(points, {...closedQuote(), quality}, "2026-10-13", now)).toThrow(/基准/);
  });

  it.each(["refresh_failed", "quote_age_exceeded", "batch_missing", "index_batch_missing", "source_time_unknown", "source_time_regress_ignored", "source_invalid_ignored", "future_unknown_failure"])("closed mixed with %s is rejected", reason => {
    expect(() => buildIndexOverlay(points, {...closedQuote(), quality_reasons: ["market_closed", reason]}, "2026-10-13", now)).toThrow(/基准/);
  });

  it("off_session is allowed only alongside an explicit market_closed reason", () => {
    expect(buildIndexOverlay(points, {...closedQuote(), quality_reasons: ["market_closed", "off_session"]}, "2026-10-13", now).mode).toBe("historical");
    for (const quality_reasons of [[], ["off_session"]]) expect(() => buildIndexOverlay(points, {...closedQuote(), quality_reasons}, "2026-10-13", now)).toThrow(/基准/);
  });

  it.each(["09:15:00", "09:25:00", "09:30:00", "11:30:00", "13:00:00", "15:00:00"])("a frozen 08:08 closed context cannot authorize a new read at %s", clock => {
    expect(() => buildIndexOverlay(points, closedQuote(), "2026-10-13", new Date(`2026-10-14T${clock}+08:00`))).toThrow(/基准/);
  });

  it.each([
    {phase: "continuous"}, {phase: "auction"}, {phase: "unknown"},
    {trade_day_result: null}, {trade_date: "2026-10-13"}, {as_of: "2026-10-14T08:08:35"},
    {as_of: "2026-10-14T08:09:00+08:00"},
  ] as Partial<QuoteDisplaySession>[])("invalid calendar context %j is rejected", patch => {
    expect(() => buildIndexOverlay(points, {...closedQuote(), display_session: {...session, ...patch}}, "2026-10-13", now)).toThrow(/基准/);
  });

  it("missing calendar or a new calendar day cannot authorize historical display", () => {
    expect(() => buildIndexOverlay(points, {...closedQuote(), display_session: null}, "2026-10-13", now)).toThrow(/基准/);
    expect(() => buildIndexOverlay(points, closedQuote(), "2026-10-13", new Date("2026-10-15T08:00:00+08:00"))).toThrow(/基准/);
  });

  it.each(["", "unknown", "chain(unknown)"])("unconfirmed quote source %s is rejected", source => {
    expect(() => buildIndexOverlay(points, {...closedQuote(), source}, "2026-10-13", now)).toThrow(/基准/);
  });

  it.each([null, "bad", "2026-10-13T15:00:00", "2026-10-14T09:00:00+08:00"])("unconfirmed/future source time %s is rejected", data_timestamp => {
    expect(() => buildIndexOverlay(points, {...closedQuote(), data_timestamp}, "2026-10-13", now)).toThrow();
  });

  it.each([0, -1, NaN, Infinity, null])("nonpositive/nonfinite reference or quote price %s is rejected", value => {
    expect(() => buildIndexOverlay(points, {...closedQuote(), prev_close: value}, "2026-10-13", now)).toThrow(/基准/);
    expect(() => buildIndexOverlay(points, {...closedQuote(), price: value}, "2026-10-13", now)).toThrow(/基准/);
  });

  it("Shenzhen 000001 and ambiguous unprefixed identities cannot become the Shanghai overlay", () => {
    for (const patch of [{symbol: "sz000001"}, {market: "SZ"}, {market: undefined}, {symbol: "sh000001", market: "SZ"}]) expect(() => buildIndexOverlay(points, {...closedQuote(), ...patch}, "2026-10-13", now)).toThrow(/基准/);
    expect(buildIndexOverlay(points, {...closedQuote(), symbol: "sh000001", market: undefined}, "2026-10-13", now).mode).toBe("historical");
  });

  it("a noon historical response cannot draw later same-day minutes", () => {
    const noon = new Date("2026-10-14T12:00:00+08:00");
    const quote = {...closedQuote(), data_timestamp: "2026-10-14T11:30:00+08:00", display_session: {...session, as_of: noon.toISOString()}};
    expect(() => buildIndexOverlay([{...points[0], ts: "2026-10-14T15:00:00+08:00"}], quote, "2026-10-14", noon)).toThrow(/基准/);
    expect(buildIndexOverlay([{...points[0], ts: "2026-10-14T11:30:00+08:00"}], quote, "2026-10-14", noon).mode).toBe("historical");
  });

  it("empty, mixed-date, bad source/price, naive and wrong-main-date minutes are rejected", () => {
    const examples = [[], [...points, {...points[0], ts: "2026-10-14T10:00:00+08:00"}],
      [{...points[0], source: "unknown"}], [{...points[0], price: 0}], [{...points[0], price: NaN}],
      [{...points[0], ts: "2026-10-13T15:00:00"}], [{...points[0], ts: "bad"}]];
    for (const value of examples) expect(() => buildIndexOverlay(value, closedQuote(), "2026-10-13", now)).toThrow();
    expect(() => buildIndexOverlay(points, closedQuote(), "2026-10-14", now)).toThrow(/交易日/);
    expect(() => buildIndexOverlay(points, closedQuote(), "", now)).toThrow(/交易日/);
  });
});
