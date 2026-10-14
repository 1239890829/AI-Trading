import type {MinutePoint} from "@/lib/api";
import {bjDate} from "@/lib/format";
import {hasTrustedQuoteObservation} from "@/lib/kline-live";
import {calendarPause} from "@/lib/quote-quality-view";
import type {Quote} from "@/types/market";

const KNOWN_SOURCES = new Set(["ths", "tencent", "eastmoney", "sina", "tdx", "tdx_m1"]);
const ZONED = /(?:Z|[+-]\d{2}:\d{2})$/;
const positive = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value > 0;

export interface IndexOverlay {
  points: MinutePoint[];
  prevClose: number;
  referenceDate: string;
  mode: "live" | "historical";
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
  if (!Array.isArray(points) || points.length === 0) throw new Error("指数尚无可用分钟记录");
  if (!referenceDate || !mainDate || mainDate !== referenceDate
    || points.some(point => !ZONED.test(point.ts) || bjDate(point.ts) !== referenceDate)) {
    throw new Error("指数分钟、基准与主图非同一交易日");
  }
  if (points.some(point => !positive(point.price) || !KNOWN_SOURCES.has(point.source))) {
    throw new Error("指数分钟价格或来源未确认");
  }
  const reference = indexQuoteReference(quote, mainDate, now);
  if (reference.mode === "historical" && points.some(point => Date.parse(point.ts) > Date.parse(quote.display_session!.as_of))) {
    throw new Error("指数基准质量或来源时刻未确认");
  }
  return {points, prevClose: quote.prev_close, ...reference};
}
