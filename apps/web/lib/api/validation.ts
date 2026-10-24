import { getJson, getJsonArray, type ReadOptions } from "./internal";

export interface ValidationSelectionEntry {
  id: string; date: string; symbol: string; name: string; scope: "daily" | "intraday";
  state: string; selected_at: string | null; reference_price: number | null; reference_change_pct: number | null;
  return_pct: number | null; outcome_state: string; outcome_date?: string | null; outcome_reason?: string | null; quote_as_of?: string | null; version: string | null;
  coverage_state?: string; method_version?: string | null; feature_version?: string | null;
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
    exposure_trade_days: number | null; episode_trade_days: number | null;
    payoff_ratio?: number | null; net_expectancy_pct?: number | null; drawdown_basis?: string};
  coverage?: {reasons: {state: string; label: string; count: number}[];
    strata?: {dimension:string;label:string;scope:string;method_version:string|null;feature_version:string|null;count:number;observed:number;observed_days:number;mean_pct:number|null;median_pct:number|null}[];
    versions: {scope: string; method_version: string | null; feature_version: string | null; version_state: string; count: number; observed: number; observed_days: number; mean_pct: number | null; median_pct: number | null}[]; basis: string};
  entries: ValidationSelectionEntry[]; episodes: TradeEpisode[];
  daily: {date: string; count: number; observed: number; mean_pct: number | null; median_pct: number | null}[];
  win_curve: {date: string; episode_id: number; denominator: number; win_rate: number; mean_pct: number; median_pct: number}[];
  cohorts: {reason: string; total: number; denominator: number; wins: number; losses: number; mean_pct: number | null; median_pct: number | null}[];
  basis: {actual: string; selection: string; coverage: string; improvement: string};
}
export async function getSelectionValidation(start: string, end: string, horizon = "d1_close", scope = "all", options: ReadOptions = {}) {
  return (await getJson<ValidationReport>(`/api/picks/validation?${new URLSearchParams({start, end, horizon, scope})}`, 15_000, options)).data;
}

/** One reader per mounted range. Unchanged responses reuse only the server-confirmed revision. */
export function createSelectionValidationReader(start: string, end: string, horizon: string, scope: string) {
  let previous: {revision: string; data: ValidationReport} | undefined;
  return async (signal: AbortSignal): Promise<ValidationReport> => {
    const params = new URLSearchParams({start, end, horizon, scope});
    if (previous) params.set("known_revision", previous.revision);
    const result = await getJson<ValidationReport | null>(`/api/picks/validation?${params}`, 15_000, {signal});
    const meta = result.meta as {revision?: string; not_modified?: boolean};
    if (signal.aborted) throw new DOMException("Request canceled", "AbortError");
    if (meta.not_modified && previous && previous.revision === meta.revision) return previous.data;
    if (!result.data) throw new Error("统计版本不完整，请重试");
    previous = meta.revision ? {revision: meta.revision, data: result.data} : undefined;
    return result.data;
  };
}

export async function getValidationEntries(symbol: string) {
  return getJsonArray<ValidationSelectionEntry>(`/api/picks/validation/entries?${new URLSearchParams({symbol})}`, 15_000);
}

export interface ExecutionReport {
  start:string;end:string;basis:string;limitation:string;
  accounts:{scope:string;label:string;orders:number;filled:number;pending:number;rejected:number;cancelled:number;unknown:number}[];
  orders:{id:number;scope:string;symbol:string;side:string;submitted_at:string;state:string;price:number|null;filled_price:number|null;quantity:number;fee:number|null;reason:string}[];
  attempts:{id:string;date:string;symbol:string;state:string;order_id:number|null;order_state:string;reason:string;issue:string;decision_id:string;snapshot_id:string}[];
  runs:{date:string;state:string;reason:string;pick_date:string|null}[];
}
export async function getExecutionValidation(start:string,end:string) {
  return (await getJson<ExecutionReport>(`/api/picks/validation/execution?${new URLSearchParams({start,end})}`,15_000)).data;
}
