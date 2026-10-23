import type {MinutePoint} from "@/lib/api";
import {bjDate} from "@/lib/format";
import {hasTrustedQuoteObservation} from "@/lib/kline-live";
import {calendarPause, calendarPhase} from "@/lib/quote-quality-view";
import type {Quote} from "@/types/market";

const KNOWN_SOURCES = new Set(["ths", "tencent", "eastmoney", "sina", "tdx", "tdx_m1"]);
const ZONED = /(?:Z|[+-]\d{2}:\d{2})$/;
const positive = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value > 0;

export interface IndexOverlay {
  points: MinutePoint[];
  prevClose: number;
  referenceDate: string;
  mode: "live" | "historical" | "waiting";
  waitingPhase?: NonNullable<ReturnType<typeof calendarPhase>>;
  sourceClock?: string;
}

function requireIndexQuote(quote: Quote | undefined): asserts quote is Quote & {data_timestamp: string; prev_close: number} {
  if (!quote || !(quote.symbol === "sh000001" || (quote.symbol === "000001" && quote.market === "SH"))
    || (quote.market != null && quote.market !== "SH")
    || !KNOWN_SOURCES.has(quote.source) || !positive(quote.price) || !positive(quote.prev_close)
    || !quote.data_timestamp || !ZONED.test(quote.data_timestamp) || !Number.isFinite(Date.parse(quote.data_timestamp))) {
    throw new Error("指数基准质量或来源时刻未确认");
  }
}

/** The same quote gate is used for display and for a bounded recovery hint. */
export function indexQuoteReference(quote: Quote | undefined, mainDate: string, now = new Date()): Pick<IndexOverlay, "referenceDate" | "mode"> {
  requireIndexQuote(quote);
  if (!Number.isFinite(now.getTime()) || Date.parse(quote.data_timestamp) > now.getTime()) throw new Error("指数基准质量或来源时刻未确认");
  const referenceDate = bjDate(quote.data_timestamp);
  if (!referenceDate || !mainDate || mainDate !== referenceDate) throw new Error("指数分钟、基准与主图非同一交易日");
  if (hasTrustedQuoteObservation(quote)) return {referenceDate, mode: "live"};
  const session = quote.display_session;
  const nowMs = now.getTime();
  // Check the same existing calendar rule against both the server context and now.
  // An old 08:00 closed context must not authorize a new 09:30 historical read.
  const historical = quote.quality === "stale" && Array.isArray(quote.quality_reasons)
    && quote.quality_reasons.includes("market_closed")
    && quote.quality_reasons.every(reason => reason === "market_closed" || reason === "off_session")
    && session && Number.isFinite(nowMs) && bjDate(now.toISOString()) === session.trade_date
    && Date.parse(quote.data_timestamp) <= Date.parse(session.as_of)
    && Date.parse(session.as_of) <= nowMs
    && calendarPause(session) !== null
    && calendarPause({...session, as_of: now.toISOString()}) !== null;
  if (!historical) throw new Error("指数基准质量或来源时刻未确认");
  return {referenceDate, mode: "historical"};
}

/** Historical display is separate from live quote eligibility; input quality is never upgraded. */
export function buildIndexOverlay(points: MinutePoint[], quote: Quote | undefined, mainDate: string, now = new Date()): IndexOverlay {
  requireIndexQuote(quote);
  const referenceDate = bjDate(quote.data_timestamp);
  const nowMs = now.getTime();
  if (!Number.isFinite(nowMs) || Date.parse(quote.data_timestamp) > nowMs) throw new Error("指数基准质量或来源时刻未确认");
  if (!Array.isArray(points)) throw new Error("指数尚无可用分钟记录");
  if (!referenceDate || !mainDate || mainDate !== referenceDate
    || points.some(point => !ZONED.test(point.ts) || bjDate(point.ts) !== referenceDate)) {
    throw new Error("指数分钟、基准与主图非同一交易日");
  }
  if (points.some(point => !positive(point.price) || !KNOWN_SOURCES.has(point.source))) {
    throw new Error("指数分钟价格或来源未确认");
  }
  if (points.some(point => {
    const label = Date.parse(point.ts);
    if (!Number.isFinite(label)) return true;
    if (point.time_label === "interval_end") {
      const observed = point.observation_ts && ZONED.test(point.observation_ts) ? Date.parse(point.observation_ts) : NaN;
      return point.source !== "tencent" || !Number.isFinite(observed) || observed > nowMs || observed > label
        || bjDate(point.observation_ts) !== referenceDate || label - observed > 60_000;
    }
    return label > nowMs;
  })) throw new Error("指数基准或分钟来源时刻未确认");
  const session = quote.display_session;
  const phase = calendarPhase(session);
  const waiting = phase && session && session.trade_date === bjDate(now.toISOString())
    && Date.parse(session.as_of) <= nowMs && Date.parse(quote.data_timestamp) <= Date.parse(session.as_of)
    && calendarPhase({...session, as_of: now.toISOString()}) === phase
    && (hasTrustedQuoteObservation(quote) || quote.quality === "stale" && quote.quality_reasons.length > 0
      && quote.quality_reasons.every(reason => ["quote_age_exceeded", "off_session", "market_closed"].includes(reason)));
  if (waiting) {
    if (phase === "closing_auction") {
      // 14:56 is the final continuous minute bucket, not a freshness extension.
      const start = Date.parse(`${session.trade_date}T14:56:00+08:00`);
      const tail = points.at(-1);
      const observed = tail?.time_label === "interval_end" ? Date.parse(tail.observation_ts ?? "") : Date.parse(tail?.ts ?? "");
      if (referenceDate !== session.trade_date || !tail || Date.parse(tail.ts) < start
        || Date.parse(quote.data_timestamp) < start || observed < start) throw new Error("指数收盘竞价前尾点未确认");
    }
    return {points, prevClose: quote.prev_close, referenceDate, mode: "waiting", waitingPhase: phase, sourceClock: quote.data_timestamp};
  }
  if (points.length === 0) throw new Error("指数尚无可用分钟记录");
  const reference = indexQuoteReference(quote, mainDate, now);
  if (reference.mode === "historical" && points.some(point => Date.parse(point.ts) > Date.parse(quote.display_session!.as_of))) {
    throw new Error("指数基准质量或来源时刻未确认");
  }
  return {points, prevClose: quote.prev_close, ...reference};
}
