import type {MinutePoint} from "@/lib/api";
import {bjDate} from "@/lib/format";
import {hasTrustedQuoteObservation} from "@/lib/kline-live";
import {calendarPause, calendarPhase, hasExpectedPausedSource} from "@/lib/quote-quality-view";
import {bjMinuteOfDay, bjToday, isAuctionComparisonWindow} from "@/lib/market-hours";
import type {Quote} from "@/types/market";

const KNOWN_SOURCES = new Set(["ths", "tencent", "eastmoney", "sina", "tdx", "tdx_m1"]);
const ZONED = /(?:Z|[+-]\d{2}:\d{2})$/;
const positive = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value > 0;

/** A completed read rejected by display validation is distinct from a failed request. */
export class IndexOverlayError extends Error {
  constructor(message: string, readonly issue: "reference_age" | "reference" | "minutes") {
    super(message);
    this.name = "IndexOverlayError";
  }
}

export interface IndexOverlay {
  points: MinutePoint[];
  prevClose: number;
  referenceDate: string;
  mode: "live" | "historical" | "waiting";
  waitingPhase?: NonNullable<ReturnType<typeof calendarPhase>>;
  sourceClock?: string;
  /** Retained auction fact, never part of the continuous plotted series. */
  openingAnchor?: MinutePoint;
}

/** Additional display veto, including a cached live result at a phase boundary.
 * This never grants source validity or changes stock/strategy eligibility. */
export function indexOverlayMayDraw(value: {referenceDate?: string | null; mode?: IndexOverlay["mode"]}, now = new Date()): boolean {
  return value.mode !== "waiting" && !(value.referenceDate === bjToday(now) && isAuctionComparisonWindow(bjMinuteOfDay(now)));
}

/** Tencent's explicit 09:30 opening label needs its own same-packet pre-open source clock. */
export function openingAnchorObservationTime(point: MinutePoint, nowMs = Date.now()): number | null {
  if (point.time_label !== "opening_anchor" || point.source !== "tencent" || !positive(point.price)
    || !ZONED.test(point.ts) || !point.observation_ts || !ZONED.test(point.observation_ts)) return null;
  const date = bjDate(point.ts), label = Date.parse(point.ts), observed = Date.parse(point.observation_ts);
  if (!date || !Number.isFinite(label) || !Number.isFinite(observed) || !Number.isFinite(nowMs)
    || label !== Date.parse(`${date}T09:30:00+08:00`) || bjDate(point.observation_ts) !== date
    || observed < Date.parse(`${date}T09:15:00+08:00`) || observed >= label || observed > nowMs) return null;
  return observed;
}

/** Read the proved observation clock without replacing the provider's minute label. */
export function minuteObservationTime(point: MinutePoint, referenceDate: string, nowMs = Date.now()): number | null {
  const label = Date.parse(point.ts);
  if (!ZONED.test(point.ts) || bjDate(point.ts) !== referenceDate || !positive(point.price)
    || !KNOWN_SOURCES.has(point.source) || !Number.isFinite(label) || !Number.isFinite(nowMs)) return null;
  if (point.time_label === "opening_anchor") return openingAnchorObservationTime(point, nowMs);
  if (point.time_label === "interval_end") {
    const observed = point.observation_ts && ZONED.test(point.observation_ts) ? Date.parse(point.observation_ts) : NaN;
    if (point.source !== "tencent" || !Number.isFinite(observed) || observed > nowMs || observed > label
      || bjDate(point.observation_ts) !== referenceDate || label - observed > 60_000) return null;
    return observed;
  }
  return label <= nowMs ? label : null;
}

function requireIndexQuote(quote: Quote | undefined): asserts quote is Quote & {data_timestamp: string; prev_close: number} {
  if (!quote || !(quote.symbol === "sh000001" || (quote.symbol === "000001" && quote.market === "SH"))
    || (quote.market != null && quote.market !== "SH")
    || !KNOWN_SOURCES.has(quote.source) || !positive(quote.price) || !positive(quote.prev_close)
    || !quote.data_timestamp || !ZONED.test(quote.data_timestamp) || !Number.isFinite(Date.parse(quote.data_timestamp))) {
    throw new IndexOverlayError("指数基准质量或来源时刻未确认", "reference");
  }
}

/** The same quote gate is used for display and for a bounded recovery hint. */
export function indexQuoteReference(quote: Quote | undefined, mainDate: string, now = new Date()): Pick<IndexOverlay, "referenceDate" | "mode"> {
  requireIndexQuote(quote);
  if (!Number.isFinite(now.getTime()) || Date.parse(quote.data_timestamp) > now.getTime()) throw new IndexOverlayError("指数基准质量或来源时刻未确认", "reference");
  const referenceDate = bjDate(quote.data_timestamp);
  if (!referenceDate || !mainDate || mainDate !== referenceDate) throw new IndexOverlayError("指数分钟、基准与主图非同一交易日", "reference");
  if (hasTrustedQuoteObservation(quote)) return {referenceDate, mode: "live"};
  const session = quote.display_session;
  const nowMs = now.getTime();
  const pause = calendarPause(session);
  // Check the same existing calendar rule against both the server context and now.
  // An old 08:00 closed context must not authorize a new 09:30 historical read.
  const historical = quote.quality === "stale" && Array.isArray(quote.quality_reasons)
    && quote.quality_reasons.includes("market_closed")
    && quote.quality_reasons.every(reason => reason === "market_closed" || reason === "off_session")
    && session && Number.isFinite(nowMs) && bjDate(now.toISOString()) === session.trade_date
    && Date.parse(quote.data_timestamp) <= Date.parse(session.as_of)
    && Date.parse(session.as_of) <= nowMs
    && pause !== null
    && calendarPause({...session, as_of: now.toISOString()}) === pause;
  if (!historical) {
    const onlyAge = quote.quality === "stale" && Array.isArray(quote.quality_reasons) && quote.quality_reasons.length > 0
      && quote.quality_reasons.every(reason => reason === "quote_age_exceeded");
    throw new IndexOverlayError("指数基准质量或来源时刻未确认", onlyAge ? "reference_age" : "reference");
  }
  if (pause === "已收盘" && (session?.expected_source_date !== session.trade_date
    || !hasExpectedPausedSource(quote.data_timestamp, session))) {
    throw new IndexOverlayError("指数收盘基准尾点未确认", "reference");
  }
  return {referenceDate, mode: "historical"};
}

/** Historical display is separate from live quote eligibility; input quality is never upgraded. */
export function buildIndexOverlay(points: MinutePoint[], quote: Quote | undefined, mainDate: string, now = new Date()): IndexOverlay {
  requireIndexQuote(quote);
  const referenceDate = bjDate(quote.data_timestamp);
  const nowMs = now.getTime();
  if (!Number.isFinite(nowMs) || Date.parse(quote.data_timestamp) > nowMs) throw new IndexOverlayError("指数基准质量或来源时刻未确认", "reference");
  if (!Array.isArray(points)) throw new IndexOverlayError("指数尚无可用分钟记录", "minutes");
  if (!referenceDate || !mainDate || mainDate !== referenceDate) {
    throw new IndexOverlayError("指数分钟、基准与主图非同一交易日", "reference");
  }
  if (points.some(point => !ZONED.test(point.ts) || bjDate(point.ts) !== referenceDate)) {
    throw new IndexOverlayError("指数分钟、基准与主图非同一交易日", "minutes");
  }
  if (points.some(point => !positive(point.price) || !KNOWN_SOURCES.has(point.source))) {
    throw new IndexOverlayError("指数分钟价格或来源未确认", "minutes");
  }
  if (points.some(point => minuteObservationTime(point, referenceDate, nowMs) === null)) {
    throw new IndexOverlayError("指数基准或分钟来源时刻未确认", "minutes");
  }
  const openingAnchor = points.findLast(point => point.time_label === "opening_anchor");
  // Preserve the provider's original label/value separately. Neither a later
  // receive/local clock nor a fresh quote makes this old auction record a minute.
  const continuousPoints = openingAnchor ? points.filter(point => point.time_label !== "opening_anchor") : points;
  const session = quote.display_session;
  const phase = calendarPhase(session);
  const waiting = phase && session && session.trade_date === bjDate(now.toISOString())
    && referenceDate === session.trade_date
    && Date.parse(session.as_of) <= nowMs && Date.parse(quote.data_timestamp) <= Date.parse(session.as_of)
    && calendarPhase({...session, as_of: now.toISOString()}) === phase
    && (hasTrustedQuoteObservation(quote) || quote.quality === "stale" && quote.quality_reasons.length > 0
      && quote.quality_reasons.every(reason => ["quote_age_exceeded", "off_session", "market_closed"].includes(reason)));
  if (waiting) {
    if (phase === "closing_auction") {
      // 14:56 is the final continuous minute bucket, not a freshness extension.
      const start = Date.parse(`${session.trade_date}T14:56:00+08:00`);
      const tail = continuousPoints.at(-1);
      const observed = tail?.time_label === "interval_end" ? Date.parse(tail.observation_ts ?? "") : Date.parse(tail?.ts ?? "");
      if (referenceDate !== session.trade_date || !tail || Date.parse(tail.ts) < start
        || Date.parse(quote.data_timestamp) < start || observed < start) throw new IndexOverlayError("指数收盘竞价前尾点未确认", "minutes");
    }
    return {points: continuousPoints, prevClose: quote.prev_close, referenceDate, mode: "waiting", waitingPhase: phase,
      sourceClock: quote.data_timestamp, ...(openingAnchor ? {openingAnchor} : {})};
  }
  if (continuousPoints.length === 0) throw new IndexOverlayError("指数尚无可用连续分钟记录", "minutes");
  const reference = indexQuoteReference(quote, mainDate, now);
  if (reference.mode === "historical" && calendarPause(quote.display_session) === "已收盘") {
    const tail = continuousPoints.at(-1)!;
    const observed = tail.time_label === "interval_end" ? tail.observation_ts : tail.ts;
    if (!hasExpectedPausedSource(observed, quote.display_session)) throw new IndexOverlayError("指数收盘分钟尾点未确认", "minutes");
  }
  if (reference.mode === "historical" && continuousPoints.some(point => Date.parse(point.ts) > Date.parse(quote.display_session!.as_of))) {
    throw new IndexOverlayError("指数基准质量或来源时刻未确认", "minutes");
  }
  return {points: continuousPoints, prevClose: quote.prev_close, ...reference};
}
