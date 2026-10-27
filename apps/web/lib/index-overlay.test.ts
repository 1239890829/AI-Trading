import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {buildIndexOverlay, indexOverlayMayDraw, indexQuoteReference, minuteObservationTime} from "./index-overlay";
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

describe("minute observation clock shared by the request gate and overlay", () => {
  const date = "2026-10-14", at = Date.parse(`${date}T09:30:05+08:00`);
  const point: MinutePoint = {ts: `${date}T09:31:00+08:00`, price: 20, source: "tencent",
    time_label: "interval_end", observation_ts: `${date}T09:30:03+08:00`};
  it.each(["09:30:03", "09:30:00"])("accepts proved future minute label with observation %s and keeps the raw label", clock => {
    const input = {...point, observation_ts: `${date}T${clock}+08:00`}, before = JSON.stringify(input);
    expect(minuteObservationTime(input, date, at)).toBe(Date.parse(input.observation_ts));
    expect(JSON.stringify(input)).toBe(before);
  });
  it.each([
    {source: "tdx"}, {source: "unknown"}, {time_label: null}, {observation_ts: undefined},
    {observation_ts: `${date}T09:30:03`}, {observation_ts: "2026-10-13T09:30:03+08:00"},
    {observation_ts: `${date}T09:30:06+08:00`}, {observation_ts: `${date}T09:31:01+08:00`},
    {observation_ts: `${date}T09:29:59.999+08:00`}, {ts: "2026-10-13T09:31:00+08:00"},
    {price: 0}, {price: Number.NaN},
  ] as Partial<MinutePoint>[])("rejects unproved minute clock %j", patch => {
    expect(minuteObservationTime({...point, ...patch}, date, at)).toBeNull();
  });
});

describe("index overlay historical display is separate from live eligibility", () => {
  it.each(["09:15:00", "09:25:00", "09:29:59", "14:57:00", "14:59:59"])("当前%s不因保留live缓存继续画指数", clock => {
    expect(indexOverlayMayDraw({referenceDate: "2026-10-14", mode: "live"}, new Date(`2026-10-14T${clock}+08:00`))).toBe(false);
  });
  it("显示禁窗只加否决，不能把waiting或未核输入升级；有效09:30及15:00另走原校验", () => {
    expect(indexOverlayMayDraw({referenceDate: "2026-10-14", mode: "waiting"}, new Date("2026-10-14T09:30:00+08:00"))).toBe(false);
    for (const clock of ["09:30:00", "14:56:59", "15:00:00"])
      expect(indexOverlayMayDraw({referenceDate: "2026-10-14", mode: "live"}, new Date(`2026-10-14T${clock}+08:00`))).toBe(true);
  });
  it.each([
    ["reference_age", "age"], ["reference", "identity"], ["reference", "quote_future"],
    ["minutes", "minute_future"], ["minutes", "minute_date"], ["minutes", "minute_price"], ["minutes", "age_and_minute_future"],
  ])("rejected %s input %s keeps its exact validation failure distinct from an HTTP failure", (issue, kind) => {
    const at = new Date("2026-10-14T10:00:02+08:00");
    const quote: Quote = {...closedQuote(), quality: "high", quality_reasons: [], data_timestamp: "2026-10-14T10:00:00+08:00",
      display_session: {...session, phase: "continuous", as_of: at.toISOString()}};
    const input: MinutePoint[] = [{ts: quote.data_timestamp!, price: 3010, source: "tencent"}];
    if (kind === "age" || kind === "age_and_minute_future") {quote.quality = "stale"; quote.quality_reasons = ["quote_age_exceeded"];}
    if (kind === "identity") quote.market = "SZ";
    if (kind === "quote_future") quote.data_timestamp = "2026-10-14T10:01:00+08:00";
    if (kind === "minute_future" || kind === "age_and_minute_future") input[0].ts = "2026-10-14T10:01:00+08:00";
    if (kind === "minute_date") input[0].ts = "2026-10-13T10:00:00+08:00";
    if (kind === "minute_price") input[0].price = 0;
    const before = JSON.stringify({quote, input});
    let error: unknown;
    try {buildIndexOverlay(input, quote, "2026-10-14", at);} catch (caught) {error = caught;}
    expect(error).toMatchObject({name: "IndexOverlayError", issue});
    expect(JSON.stringify({quote, input})).toBe(before);
  });
  it.each(["09:20:00", "09:27:00"])("confirmed opening phase at%s treats an empty continuous series as waiting", clock => {
    const at = new Date(`2026-10-14T${clock}+08:00`);
    const quote: Quote = {...closedQuote(), data_timestamp: "2026-10-14T09:15:00+08:00", quality_reasons: ["quote_age_exceeded"],
      display_session: {...session, phase: clock === "09:20:00" ? "auction" as const : "pre_open_gap" as const, as_of: at.toISOString()}};
    const before = JSON.stringify(quote);
    expect(buildIndexOverlay([], quote, "2026-10-14", at)).toMatchObject({points: [], mode: "waiting"});
    expect(JSON.stringify(quote)).toBe(before);
  });

  const openingAnchor = (clock = "09:25:19", volume = 12_880_400): MinutePoint => ({
    ts: "2026-10-14T09:30:00+08:00", time_label: "opening_anchor", source: "tencent", price: 4.5,
    observation_ts: `2026-10-14T${clock}+08:00`, received_at: "2026-10-14T09:25:22+08:00",
    cum_volume: volume, cum_amount: volume * 4.5,
  });
  const openingQuote = (clock: string, phase: "auction" | "pre_open_gap" | "continuous" = "pre_open_gap"): Quote => ({
    ...closedQuote(), data_timestamp: `2026-10-14T${clock}+08:00`, quality_reasons: ["quote_age_exceeded"],
    display_session: {...session, phase, as_of: `2026-10-14T${clock}+08:00`},
  });
  it.each([["09:18:00", "auction", 0], ["09:25:22", "pre_open_gap", 12_880_400]] as const)("opening anchor at%s retains auction facts without drawing a future continuous minute", (clock, phase, volume) => {
    const anchor = openingAnchor(clock === "09:18:00" ? clock : "09:25:19", volume);
    const quote = openingQuote(clock, phase), input = [anchor], before = JSON.stringify({input, quote});
    expect(buildIndexOverlay(input, quote, "2026-10-14", new Date(`2026-10-14T${clock}+08:00`)))
      .toMatchObject({points: [], openingAnchor: anchor, mode: "waiting", waitingPhase: phase});
    expect(JSON.stringify({input, quote})).toBe(before);
    expect(quote.quality).toBe("stale");
  });
  it.each([
    {time_label: null}, {source: "tdx"}, {observation_ts: null}, {observation_ts: "2026-10-14T09:25:19"},
    {observation_ts: "2026-10-13T09:25:19+08:00"}, {observation_ts: "2026-10-14T09:25:23+08:00"},
    {observation_ts: "2026-10-14T09:14:59+08:00"}, {observation_ts: "2026-10-14T09:30:00+08:00"},
    {ts: "2026-10-14T09:31:00+08:00"}, {price: 0},
  ] as Partial<MinutePoint>[])("opening anchor never hides malformed/generic future input %j", patch => {
    expect(() => buildIndexOverlay([{...openingAnchor(), ...patch}], openingQuote("09:25:22"), "2026-10-14", new Date("2026-10-14T09:25:22+08:00"))).toThrow();
  });
  it("advancing the local clock never upgrades an old opening anchor to live or closing-auction minutes", () => {
    const quote = {...openingQuote("09:30:02", "continuous"), quality: "high" as const, quality_reasons: []};
    const delayed = {...openingAnchor(), received_at: "2026-10-14T09:30:02+08:00"};
    expect(() => buildIndexOverlay([delayed], quote, "2026-10-14", new Date("2026-10-14T09:30:02+08:00"))).toThrow();
    const closing = {...openingQuote("14:57:00", "auction"), quality: "high" as const, quality_reasons: []};
    expect(() => buildIndexOverlay([openingAnchor()], closing, "2026-10-14", new Date("2026-10-14T14:57:00+08:00"))).toThrow();
    const actual: MinutePoint = {ts: "2026-10-14T09:31:00+08:00", price: 3010, source: "tencent"};
    const result = buildIndexOverlay([openingAnchor(), actual], {...quote, data_timestamp: actual.ts}, "2026-10-14", new Date(actual.ts));
    expect(result).toMatchObject({points: [actual], mode: "live"});
  });
  it("an opening anchor cannot rescue a failed quote, unknown calendar or wrong reference day", () => {
    const at = new Date("2026-10-14T09:25:22+08:00"), quote = openingQuote("09:25:22");
    expect(() => buildIndexOverlay([openingAnchor()], {...quote, quality_reasons: ["refresh_failed"]}, "2026-10-14", at)).toThrow();
    expect(() => buildIndexOverlay([openingAnchor()], {...quote, display_session: {...quote.display_session!, trade_day_result: null}}, "2026-10-14", at)).toThrow();
    expect(() => buildIndexOverlay([openingAnchor()], quote, "2026-10-13", at)).toThrow();
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

  it.each(["15:00:00", "15:00:59", "15:01:00"])("收盘从%s承接合法历史尾点，不放行空分钟、失败或未知日历", clock => {
    const at = new Date(`2026-10-13T${clock}+08:00`);
    const quote: Quote = {...closedQuote(), data_timestamp: points[0].ts,
      display_session: {...session, trade_date: "2026-10-13", as_of: at.toISOString(),
        expected_source_date: "2026-10-13", expected_source_after: "2026-10-13T14:59:50+08:00"}};
    const before = JSON.stringify(quote);
    expect(buildIndexOverlay(points, quote, "2026-10-13", at)).toMatchObject({mode: "historical", points});
    expect(() => buildIndexOverlay([], quote, "2026-10-13", at)).toThrow();
    expect(() => buildIndexOverlay(points, {...quote, quality_reasons: ["market_closed", "refresh_failed"]}, "2026-10-13", at)).toThrow();
    expect(() => buildIndexOverlay(points, {...quote, display_session: {...quote.display_session!, trade_day_result: null}}, "2026-10-13", at)).toThrow();
    expect(JSON.stringify(quote)).toBe(before);
    expect(quote.quality).toBe("stale");
  });

  it.each(["quote_before_tail", "minutes_before_tail", "forming_before_tail", "missing_tail_contract"])("收盘历史%s不冒充完整收盘叠加", kind => {
    for (const clock of ["15:00:00", "15:01:00"]) {
      const at = new Date(`2026-10-13T${clock}+08:00`);
      const quote: Quote = {...closedQuote(), data_timestamp: kind === "quote_before_tail" ? "2026-10-13T10:00:00+08:00" : points[0].ts,
        display_session: {...session, trade_date: "2026-10-13", as_of: at.toISOString(), expected_source_date: "2026-10-13",
          expected_source_after: kind === "missing_tail_contract" ? null : "2026-10-13T14:59:50+08:00"}};
      const input: MinutePoint[] = kind === "minutes_before_tail" ? [{...points[0], ts: "2026-10-13T10:00:00+08:00"}]
        : kind === "forming_before_tail" ? [{...points[0], time_label: "interval_end", observation_ts: "2026-10-13T14:59:40+08:00"}] : points;
      expect(() => buildIndexOverlay(input, quote, "2026-10-13", at)).toThrow();
    }
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

  it.each(["09:15:00", "09:25:00", "09:30:00", "11:30:00", "13:00:00", "15:00:00", "15:00:59", "15:01:00"])("a frozen 08:08 closed context cannot authorize a new read at %s", clock => {
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
