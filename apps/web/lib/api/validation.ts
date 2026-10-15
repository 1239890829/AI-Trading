import { getJson, getJsonArray } from "./internal";

export interface ValidationSelectionEntry {
  id: string; date: string; symbol: string; name: string; scope: "daily" | "intraday";
  state: string; selected_at: string | null; reference_price: number | null; reference_change_pct: number | null;
  return_pct: number | null; outcome_state: string; quote_as_of?: string | null; version: string | null;
  reason: string; conditions: string[]; selection_refs: string[]; observation_calendar_days: number;
}
export interface TradeEpisode {
  id: number; symbol: string; name: string; opened_at: string | null; open_date: string;
  closed_at: string | null; close_date: string | null; quantity: number; cost: number; proceeds: number;
  buy_fee: number; sell_fee: number; selection_ref: string | null; state: "open" | "closed" | "excluded";
  return_pct: number | null; holding_trade_days: number | null; holding_calendar_days: number | null;
  fill_ids: number[]; issues: string[]; entry_reason?: string;
}
export interface ValidationReport {
  start: string; end: string; scope: string; horizon: string;
  selection: {count: number; observed: number; missing: number; mean_pct: number | null; median_pct: number | null};
  actual: {denominator: number; win_rate: number | null; mean_pct: number | null; median_pct: number | null;
    wins: number; losses: number; flat: number; open: number; excluded: number; legacy_fills: number;
    exposure_trade_days: number | null; episode_trade_days: number | null};
  entries: ValidationSelectionEntry[]; episodes: TradeEpisode[];
  daily: {date: string; count: number; observed: number; mean_pct: number | null; median_pct: number | null}[];
  win_curve: {date: string; episode_id: number; denominator: number; win_rate: number; mean_pct: number; median_pct: number}[];
  cohorts: {reason: string; total: number; denominator: number; wins: number; losses: number; mean_pct: number | null; median_pct: number | null}[];
  basis: {actual: string; selection: string; coverage: string; improvement: string};
}
export async function getSelectionValidation(start: string, end: string, horizon = "d1_close", scope = "all") {
  return (await getJson<ValidationReport>(`/api/picks/validation?${new URLSearchParams({start, end, horizon, scope})}`, 15_000)).data;
}

export async function getValidationEntries(symbol: string) {
  return getJsonArray<ValidationSelectionEntry>(`/api/picks/validation/entries?${new URLSearchParams({symbol})}`, 15_000);
}
