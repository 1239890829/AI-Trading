import {getJson, sendJson} from "./internal";
import type {ReadOptions} from "./internal";
import type {AssetEvidence, AssetRecord} from "./assets";

export interface UsInstrument {
  instrument_id: string; asset_type: "us_equity"; market: "US"; symbol: string;
  source_symbol: string; currency: "USD"; name: string; english_name: string | null;
  exchange_source_code: string; security_type?: "stock" | "etf"; source: "tencent";
}
export interface UsSearchCandidate {
  symbol: string; source_symbol: string; market: "US"; name: string;
  source_kind: string; security_type?: "stock" | "etf"; source: "tencent";
}
export interface UsSearchResult {
  query: string; asset_type: "us_equity"; state: "ready" | "partial" | "blocked";
  items: UsSearchCandidate[]; reason: string | null; issues: string[]; evidence: AssetEvidence[];
}
export interface UsDailyPoint {
  date: string; open: number; high: number; low: number; close: number; volume_shares: number;
}
export interface UsUnavailableSession {state: "unavailable"; reason: string}
export interface UsOvernightData {
  state: "disabled" | "missing_credentials" | "authorization_unverified" | "unavailable" | "stale" | "available";
  reason: string; instrument_id: string | null; symbol: string | null;
  source: "alpaca"; feed: "overnight"; session: "overnight";
  authorization_verified: boolean; source_age_seconds: number | null; freshness_limit_seconds: 60;
  received_at: string | null; checked_at: string; calendar_status: "unverified"; calendar_note: string;
  missing: string[]; evidence: AssetEvidence[];
  quote: {bid_price: number | null; ask_price: number | null; bid_size_shares: number; ask_size_shares: number;
    source_time: string; source_timestamp: string; received_at: string; currency: "USD";
    price_kind: "indicative_bid_ask"; last_trade_available: false; source: "alpaca"; feed: "overnight"} | null;
}
export interface UsSnapshot {
  state: "partial"; instrument: UsInstrument;
  regular: {
    state: "ready"; price: number; previous_close: number; change: number | null; change_pct: number | null;
    change_basis: "source_previous_regular_close"; open: number | null; high: number | null; low: number | null;
    volume_shares: number | null; turnover: number | null; source_time: string;
    source_timezone: "America/New_York"; received_at: string; observation: "regular_quote" | "regular_close" | "stale" | "session_unverified";
    feed_delay_seconds: null; realtime_verified: false; source: "tencent"; evidence: AssetEvidence[];
  };
  daily: {state: "ready" | "unavailable"; reason: string | null; points: UsDailyPoint[];
    price_basis: "none"; source: "tencent"; source_symbol: string; evidence: AssetEvidence[]};
  pre_market: UsUnavailableSession; post_market: UsUnavailableSession;
  overnight: UsUnavailableSession; overnight_data?: UsOvernightData | null; minute: UsUnavailableSession;
  clock_phase: "pre_market" | "regular" | "post_market" | "overnight" | "weekend";
  calendar_status: "unverified"; calendar_note: string; received_at: string; missing: string[];
}
export type UsAssetRecord = Omit<AssetRecord, "asset_type"> & {asset_type: "us_equity"};

export function validUsSourceSymbol(value: string | null | undefined): value is string {
  if (typeof value !== "string") return false;
  const match = /^([A-Z][A-Z0-9.\-]{0,23})\.([A-Z]{1,4})$/.exec(value);
  return !!match && ["OQ", "N", "AM"].includes(match[2]);
}
export function usInstrumentMatches(instrument: UsInstrument) {
  return instrument.asset_type === "us_equity" && instrument.market === "US" && instrument.currency === "USD"
    && instrument.source === "tencent" && validUsSourceSymbol(instrument.source_symbol)
    && instrument.source_symbol === `${instrument.symbol}.${instrument.exchange_source_code}`
    && instrument.instrument_id === `us_equity:US:${instrument.source_symbol}:USD`;
}
export async function getUsSnapshot(code: string, options?: ReadOptions): Promise<UsSnapshot> {
  return (await getJson<UsSnapshot>(`/api/assets/us/${encodeURIComponent(code)}/snapshot?limit=120`, 45_000, options)).data;
}
export async function searchUsAssets(query: string, options?: ReadOptions): Promise<UsSearchResult> {
  const params = new URLSearchParams({q: query, asset_type: "us_equity", limit: "20"});
  return (await getJson<UsSearchResult>(`/api/assets/search?${params}`, 30_000, options)).data;
}
export async function getUsAssetRecords(options?: ReadOptions): Promise<{items: UsAssetRecord[]}> {
  return (await getJson<{items: UsAssetRecord[]}>("/api/assets/records?asset_type=us_equity", 15_000, options)).data;
}
export async function saveUsWatchRecord(instrument: UsInstrument, version: number, previous?: UsAssetRecord): Promise<UsAssetRecord> {
  if (!usInstrumentMatches(instrument) || previous && (previous.asset_type !== "us_equity" || previous.instrument_id !== instrument.instrument_id
    || previous.source_symbol !== instrument.source_symbol || previous.currency !== "USD"
    || previous.units != null || previous.cost_total != null)) throw new Error("自选身份或旧记录字段待核，原记录未变");
  const input = {source_symbol: instrument.source_symbol, name: previous?.name ?? (instrument.name.length <= 80 ? instrument.name : null),
    is_watched: true, units: null, cost_total: null, note: previous?.note ?? null, expected_version: version};
  return (await sendJson<UsAssetRecord>(`/api/assets/records/${encodeURIComponent(instrument.instrument_id)}`, "PUT", input, 45_000)).data;
}
export async function deleteUsWatchRecord(id: string, version: number): Promise<void> {
  const code = /^us_equity:US:(.+):USD$/.exec(id)?.[1];
  if (!validUsSourceSymbol(code) || !Number.isInteger(version) || version < 1) throw new Error("美股自选身份或版本无效，原记录未变");
  await sendJson(`/api/assets/records/${encodeURIComponent(id)}?expected_version=${version}`, "DELETE");
}
