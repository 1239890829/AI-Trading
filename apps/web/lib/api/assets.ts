import {getJson, sendJson} from "./internal";
import type {ReadOptions} from "./internal";

export interface AssetInstrument {
  instrument_id: string; asset_type: "fund"; fund_type: "fund-otc" | "fund-etf" | "fund-lof" | "fund-reits";
  market: string; symbol: string; source_symbol: string; currency: string; name: string; source: "ths_official";
}
export interface AssetEvidence {
  endpoint: string; params: Record<string, string | number>; received_at: string; payload_sha256: string; receipt_id: string; raw_available: boolean;
}
export interface AssetReadSection {
  state: "ready" | "partial" | "unavailable"; reason: string | null; missing: string[];
  source: "ths_official" | "system_calculation" | "mixed_public_sources"; evidence: AssetEvidence[];
}
export interface FundPublicField {
  key: string; label: string; value: string; source: "eastmoney_fund" | "huaan_fund";
  source_url: string; source_as_of: string | null;
}
export interface FundPublicProfile {
  source_symbol: string; state: "ready" | "partial" | "unavailable"; fields: FundPublicField[];
  missing: string[]; conflicts: string[]; evidence: AssetEvidence[];
  document_scope: "public_product_description_not_verified_regulatory_document"; recommendation: false;
}
export interface FundPublicReference {
  source_symbol: string; state: "ready" | "partial" | "stale" | "unavailable";
  scope: "reported_top_holdings_reference"; source: "sina_fund"; warning: string; scope_note: string;
  source_as_of: string | null; trading_date: string | null; is_current_day: boolean; age_seconds: number | null;
  disclosed_nav: number | null; disclosed_nav_date: string | null; heavy_stock_us: boolean | null;
  curves: {key: "nav_pct" | "nav2_pct"; label: string; latest_pct: number | null;
    points: {as_of: string; percent_pct: number | null; reference_value: number | null}[]}[];
  missing: string[]; evidence: AssetEvidence[]; estimated_fund_nav: null; estimated_fund_percent: null;
}
export interface FundCatalogCandidate {
  source_symbol: string; source_name: string | null; state: "candidate" | "conflict" | "out_of_scope";
  source_position: number; memberships: {group_key: string; group_position: number; row_position: number}[]; occurrence_count: number;
  quota_raw: string | null; quota_status: "reported" | "unlimited" | "unknown" | "conflict"; quota_unit: null;
  year_raw: string | null; classify: string[] | null; missing: string[]; conflicting_fields: string[];
}
export type FundCatalogPurchase = "all" | "source_buyable";
export interface FundCatalog {
  state: "ready" | "partial" | "unavailable"; source: "ths_official"; tab: "meiguo"; purchase: FundCatalogPurchase;
  groups: {source_key: string; source_position: number; source_rows: number; unique_symbols: number}[];
  items: FundCatalogCandidate[];
  excluded: {group_key: string | null; group_position: number | null; row_position: number | null; source_symbol: string | null; reason: string}[];
  coverage: {source_rows: number; unique_symbols: number; of_symbols: number; candidate_symbols: number; out_of_scope_symbols: number; conflict_symbols: number; placeholder_count: number; excluded_rows: number; complete_source_response: boolean};
  source_as_of: null; evidence: AssetEvidence[]; missing: string[];
  scope: "source_meiguo_catalog_not_investment_mandate"; scope_note: string; purchase_note: string; recommendation: false;
}
export interface FundNavPoint {nav_date: string; unit_nav: number | null; adj_nav: number | null}
export interface FundHolding {
  source_symbol: string; name: string; asset_type: string; weight_pct: number | null;
  report_date: string | null; published_at: string | null; modified_at: string | null;
}
export interface FundExposure {source_symbol: string; name: string; weight_pct: number; via: string[]; report_date: string}
export interface FundTerm {label: string; value: string; condition: string | null}
export interface FundSnapshot {
  instrument: AssetInstrument; state: "ready" | "partial"; received_at: string;
  nav: AssetReadSection & {currency: string; points: FundNavPoint[]; latest: FundNavPoint | null; published_at: string | null; publication_time_known: boolean; basis: "official_nav"};
  holdings: AssetReadSection & {items: FundHolding[]; disclosed_weight_pct: number | null; report_date: string | null; current_positions: false};
  lookthrough: AssetReadSection & {items: FundExposure[]; covered_weight_pct: number; uncovered_weight_pct: number; scope: "disclosed_equity_one_fund_layer"; current_positions: false};
  terms: AssetReadSection & {manager_name: string | null; fees: FundTerm[]; dealing_rules: FundTerm[]; investment_scope: string | null; benchmark: string | null; subscription_status: string | null};
  research: AssetReadSection & {eligible: boolean; checks: FundTerm[]; disclaimer: string};
  estimate: AssetReadSection & {value: number | null; currency: string; basis: "estimate"};
  public_profile?: FundPublicProfile | null;
  reference?: FundPublicReference | null;
}
export interface AssetCapability {
  asset_type: "fund" | "us_equity"; state: "available" | "blocked"; reason: string;
  capabilities: string[]; unavailable: string[]; source: string | null; trading: false;
}
export interface AssetSearchResult {
  query: string; asset_type: "fund" | "us_equity"; state: "ready" | "partial" | "blocked";
  items: AssetInstrument[]; reason: string | null; issues: string[]; coverage: "source_search_window"; evidence: AssetEvidence[];
}
export interface AssetRecord {
  instrument_id: string; asset_type: "fund"; source_symbol: string; name: string | null; currency: string;
  is_watched: boolean; units: number | null; cost_total: number | null; note: string | null;
  version: number; updated_at: string; basis: "manual_not_broker_verified";
}
export interface AssetRecordInput {
  source_symbol: string; name?: string | null; is_watched: boolean; units: number | null; cost_total: number | null;
  note?: string | null; expected_version: number;
}

export async function getAssetCapabilities(options?: ReadOptions): Promise<{items: AssetCapability[]}> {
  return (await getJson<{items: AssetCapability[]}>("/api/assets/capabilities", 15_000, options)).data;
}
export async function searchAssets(query: string, asset: "fund" | "us_equity", options?: ReadOptions): Promise<AssetSearchResult> {
  const params = new URLSearchParams({q: query, asset_type: asset, limit: "20"});
  return (await getJson<AssetSearchResult>(`/api/assets/search?${params}`, 30_000, options)).data;
}
export async function getFundSnapshot(symbol: string, range: "month" | "year", options?: ReadOptions): Promise<FundSnapshot> {
  return (await getJson<FundSnapshot>(`/api/assets/funds/${encodeURIComponent(symbol)}/snapshot?range=${range}`, 60_000, options)).data;
}
export async function getFundCatalog(purchase: FundCatalogPurchase = "all", options?: ReadOptions): Promise<FundCatalog> {
  return (await getJson<FundCatalog>(`/api/assets/funds/catalog?tab=meiguo&purchase=${purchase}`, 30_000, options)).data;
}
export async function getAssetRecords(options?: ReadOptions): Promise<{items: AssetRecord[]}> {
  return (await getJson<{items: AssetRecord[]}>("/api/assets/records?asset_type=fund", 15_000, options)).data;
}
export async function saveAssetRecord(id: string, input: AssetRecordInput): Promise<AssetRecord> {
  return (await sendJson<AssetRecord>(`/api/assets/records/${encodeURIComponent(id)}`, "PUT", input, 45_000)).data;
}
export async function deleteAssetRecord(id: string, version: number): Promise<void> {
  await sendJson(`/api/assets/records/${encodeURIComponent(id)}?expected_version=${version}`, "DELETE");
}
