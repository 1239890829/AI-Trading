import {getJson} from "./internal";
import type {EventInterpretationRef} from "./events";

export interface AttributionSourceContext {
  title: string;
  summary: string | null;
  url: string | null;
  observation_id: number | null;
  change_kind: string | null;
}

/** Current, evidence-bound explanation. It never replaces an archived selection basis. */
export interface AttributionEvidenceItem {
  event_id: number;
  title: string;
  summary: string | null;
  url: string | null;
  state: string;
  state_label: string;
  is_active: boolean;
  certainty: string | null;
  dimensions: {key: string; label: string; matched_terms: string[]}[];
  source: {
    collector: string; original_publisher: string | null; source_tier: number | null;
    source_tier_label: string; source_item_id: string | null; content_hash: string | null;
  };
  timing: {
    source_published_at: string | null; received_at: string | null; available_at: string | null;
    interpretation_effective_at: string | null; card_published_at: string | null; source_time_known: boolean;
  };
  interpretation_ref: EventInterpretationRef;
  fact_support: {status: string; label: string; note: string};
  stock_relation: {
    status: string; label: string; direction: number | null; chain: string; basis: string;
    matched_by: string | null; matched_themes: string[];
  };
  market_response: {
    status: string; status_label: string; verification_scope: "market_response";
    fact_verified: false; causality_verified: false; basis: string;
  };
  qualifiers: {kind: string; label: string; matched_terms: string[]}[];
  source_context: AttributionSourceContext | null;
  pending_context: AttributionSourceContext | null;
  review_note?: string | null;
  withdrawal_context?: (Omit<AttributionSourceContext, "change_kind"> & {event_id: number; source: string; source_item_id: string | null; available_at: string | null; linked_at: string | null; review_note: string | null}) | null;
  alternatives: string[];
  limitations: string[];
}

export interface StockAttributionPayload {
  schema_version: number;
  symbol: string | null;
  count: number;
  items: AttributionEvidenceItem[];
}

export async function getStockAttribution(symbol: string): Promise<StockAttributionPayload> {
  const result = (await getJson<StockAttributionPayload>(`/api/events/symbol/${encodeURIComponent(symbol)}/attribution?limit=12`)).data;
  // A response identity mismatch must never put another security's evidence under this title.
  if (result.symbol !== symbol || result.schema_version !== 1 || !Array.isArray(result.items)) {
    throw new Error("归因资料身份不匹配，请重新读取");
  }
  return result;
}

export async function getEventAttribution(eventId: number, symbol?: string | null): Promise<StockAttributionPayload> {
  const query = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
  const result = (await getJson<StockAttributionPayload>(`/api/events/${eventId}/attribution${query}`)).data;
  if (result.symbol !== (symbol || null) || result.schema_version !== 1 || !Array.isArray(result.items) || result.items.some(item => item.event_id !== eventId)) {
    throw new Error("事件归因资料身份不匹配，请重新读取");
  }
  return result;
}
