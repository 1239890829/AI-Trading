import {bjDate, dateTimeTextBJ, sourceLabel} from "@/lib/format";
import type {Quote, QuoteFieldSource, QuoteSupplementField} from "@/types/market";
import {hasExpectedPausedSource, quoteQualityView} from "@/lib/quote-quality-view";

const fields: QuoteSupplementField[] = ["pe_ttm", "pb", "total_mktcap_yi", "float_mktcap_yi", "limit_up_price", "limit_down_price"];
// Quote.freshness uses the same existing 60-second observation window.
const valuationWindowMs = 60_000;
// Same causal clock tolerance as core.freshness.MAX_FUTURE_SKEW_SECONDS.
const sourceClockSkewMs = 300_000;
const valuationField = (field: QuoteSupplementField) => !field.startsWith("limit_");
function clock(value: string | null | undefined): number | null {
  const parts = typeof value === "string" && /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!parts) return null;
  const local = new Date(`${parts[1]}Z`), parsed = Date.parse(value!);
  return Number.isFinite(parsed) && Number.isFinite(local.getTime()) && local.toISOString().slice(0, 19) === parts[1] ? parsed : null;
}
function identity(q: Quote): string | null {
  const market = q.market?.toUpperCase(), symbol = q.symbol.toLowerCase();
  if (!market || !["SH", "SZ", "BJ"].includes(market)) return null;
  if (/^(sh|sz|bj)\d{6}$/.test(symbol)) return symbol.slice(0, 2).toUpperCase() === market ? symbol : null;
  return /^\d{6}$/.test(symbol) ? market.toLowerCase() + symbol : null;
}
export function quoteMatchesRequest(q: Quote, symbol: string): boolean {
  const code = symbol.toLowerCase();
  const bare = /^(sh|sz|bj)\d{6}$/.test(code) ? code.slice(2) : code;
  const expectedMarket = code !== bare ? code.slice(0, 2) : /^(4|8|920)/.test(bare) ? "bj" : /^(6|9|5)/.test(bare) ? "sh" : "sz";
  return identity(q) === expectedMarket + bare;
}
function validValue(value: unknown, field: QuoteSupplementField): value is number {
  return typeof value === "number" && Number.isFinite(value) && (field === "pe_ttm" || field === "pb" ? value !== 0 : value > 0);
}
export function quoteFieldOrigin(q: Quote, field: QuoteSupplementField): QuoteFieldSource | null {
  const hasOrigin = Object.prototype.hasOwnProperty.call(q.field_sources ?? {}, field);
  const supplied = q.field_sources?.[field];
  if (hasOrigin && !supplied) return null;
  const row = hasOrigin ? supplied! : {source: q.source, data_timestamp: q.data_timestamp, received_at: q.received_at};
  if (!row.source?.trim() || clock(row.data_timestamp) === null || clock(row.received_at) === null) return null;
  return {source: row.source, data_timestamp: row.data_timestamp!, received_at: row.received_at};
}
function trustworthyDonor(donor: Quote): boolean {
  if (donor.quality === "high") return donor.quality_reasons.every(reason => reason === "off_session");
  if (donor.quality !== "stale" || donor.quality_reasons.some(reason => !["market_closed", "off_session", "quote_age_exceeded"].includes(reason))) return false;
  const view = quoteQualityView({quality: donor.quality, reasons: donor.quality_reasons, symbol: donor.symbol, market: donor.market,
    dataTimestamp: donor.data_timestamp, session: donor.display_session});
  return view.sessionLabel !== null && view.sessionIssue === null && view.label === view.sessionLabel
    && hasExpectedPausedSource(donor.data_timestamp, donor.display_session);
}
function compatibleLimits(primary: Quote, donor: Quote): boolean {
  if (typeof primary.price !== "number" || !Number.isFinite(primary.price) || primary.price <= 0) return false;
  const up = validValue(donor.limit_up_price, "limit_up_price") ? donor.limit_up_price : null;
  const down = validValue(donor.limit_down_price, "limit_down_price") ? donor.limit_down_price : null;
  if ((up !== null && (donor.price! > up || primary.price > up))
      || (down !== null && (donor.price! < down || primary.price < down))
      || (up !== null && down !== null && down >= up)) return false;
  const mergedUp = validValue(primary.limit_up_price, "limit_up_price") ? primary.limit_up_price : up;
  const mergedDown = validValue(primary.limit_down_price, "limit_down_price") ? primary.limit_down_price : down;
  return mergedUp === null || mergedDown === null || mergedDown < mergedUp;
}
function canBorrow(primary: Quote, donor: Quote, field: QuoteSupplementField): boolean {
  if (!identity(primary) || identity(primary) !== identity(donor) || !trustworthyDonor(donor) || !validValue(donor[field], field)
      || typeof donor.price !== "number" || !Number.isFinite(donor.price) || donor.price <= 0) return false;
  const origin = quoteFieldOrigin(donor, field), primaryClock = clock(primary.data_timestamp), donorClock = clock(donor.data_timestamp);
  const originClock = origin ? clock(origin.data_timestamp) : null, originReceived = origin ? clock(origin.received_at) : null;
  const donorReceived = clock(donor.received_at), now = Date.now();
  if (!origin || primaryClock === null || donorClock === null || originClock === null || originReceived === null || donorReceived === null
      || primaryClock > now || donorClock > now || donorReceived > now || originClock > now || originReceived > now
      || donorClock - donorReceived > sourceClockSkewMs || originClock - originReceived > sourceClockSkewMs
      || bjDate(primary.data_timestamp) !== bjDate(donor.data_timestamp)
      || bjDate(origin.data_timestamp) !== bjDate(primary.data_timestamp)) return false;
  return valuationField(field) ? Math.abs(originClock - primaryClock) <= valuationWindowMs : compatibleLimits(primary, donor);
}

/** Preserve primary price/clock/quality; each inherited value keeps its own origin. */
export function inheritQuoteFields(primary: Quote, donors: readonly (Quote | null | undefined)[], replaceValuation = false): Quote {
  const result: Quote = {...primary, field_sources: Object.fromEntries(Object.entries(primary.field_sources ?? {}).map(([key, value]) => [key, {...value}]))};
  for (const field of fields) {
    if (primary[field] != null && !validValue(primary[field], field)) {
      result[field] = null;
      delete result.field_sources![field];
    }
    if (!replaceValuation || !valuationField(field)) {
      if (validValue(primary[field], field)) continue;
    }
    const donor = donors.find(row => row && canBorrow(result, row, field));
    if (!donor) continue;
    result[field] = donor[field];
    result.field_sources![field] = quoteFieldOrigin(donor, field)!;
  }
  return result;
}
export function quoteFieldSourceText(q: Quote, field: QuoteSupplementField): string {
  const supplied = q.field_sources?.[field];
  const hasOrigin = Object.prototype.hasOwnProperty.call(q.field_sources ?? {}, field);
  const source = hasOrigin ? supplied?.source : q.source;
  const timestamp = hasOrigin ? supplied?.data_timestamp : q.data_timestamp;
  return `${hasOrigin ? "来源" : "行来源"}：${source?.trim() ? sourceLabel(source) : "未知"} · 截至 ${clock(timestamp) !== null ? dateTimeTextBJ(timestamp, {seconds: true}) : "未知"}`;
}
