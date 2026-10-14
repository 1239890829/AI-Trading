import type {LimitPoolSnapshot} from "@/lib/api";
import type {LimitUpRecord} from "@/types/market";

const zonedClock = (value: string) => /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
/** Positive source observations establish same-day past membership, never current sealing. */
export function limitQualification(data: LimitPoolSnapshot<LimitUpRecord> | undefined, symbol: string, date: string | null) {
  if (!date || data?.trade_date !== date) return {eligible: false, row: undefined, kind: null, previous: undefined};
  const usable = (row: LimitUpRecord) => row.symbol === symbol && row.trade_date === date && !!row.source && (row.quality === "high" || row.quality === "medium");
  const up = (!data.coverage || data.coverage.limit_up.state === "success") ? data.pool.find(usable) : undefined;
  const broken = data.coverage?.limit_break.state === "success" ? data.break_pool?.find(usable) : undefined;
  const row = up || broken;
  const candidateObservations = data.qualification_observations?.[symbol];
  const observations = Array.isArray(candidateObservations) ? candidateObservations : [];
  const proof = observations.find(item => item.symbol === symbol && item.trade_date === date && !!item.source
    && (item.kind === "limit_up" || item.kind === "limit_break") && zonedClock(item.observed_at));
  const reasonObservations = data.reason_observations?.[symbol];
  const previous = (Array.isArray(reasonObservations) ? reasonObservations : []).filter(item => item.symbol === symbol && item.trade_date === date
    && !!item.source && !!item.reason?.trim() && zonedClock(item.observed_at))
    .sort((a, b) => Date.parse(b.observed_at) - Date.parse(a.observed_at))[0];
  return {eligible: !!(row || proof), row, previous, kind: up ? "limit_up" as const : broken ? "limit_break" as const : proof?.kind || null};
}
