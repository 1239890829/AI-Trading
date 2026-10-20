import {getJson} from "./internal";
import type {EventInterpretationRef} from "./events";

export interface AttributionSourceContext {
  title: string;
  summary: string | null;
  url: string | null;
  observation_id: number | null;
  change_kind: string | null;
}

export interface PrimaryAttributionReason {
  status: "source_clue" | "later_clue" | "unconfirmed";
  label: string;
  reason: string;
  event_id: number | null;
  version_id: number | null;
  observation_id: number | null;
  relation: "direct_symbol" | "source_association" | "unknown";
  timing_relation: "known_before_response" | "late_source_observation" | "interpretation_after_response" | "time_unknown" | "response_time_unknown";
  excerpt: {field: "title" | "summary"; quote: string; truncated: boolean} | null;
  qualifiers: AttributionEvidenceItem["qualifiers"];
  causality_verified: false;
  fact_verified: false;
  interpretation_view: "current";
}

export interface AttributionAuxJudgment {
  event_id: number;
  input_version_id: number | null;
  observation_id: number | null;
  direction: number;
  theme: string | null;
  reason: string;
  chain: string;
  evidence: {field: "title" | "summary"; quote: string};
  state: "no_direction" | "hypothesis";
  verification: "unverified";
  eligibility_changed: false;
}

/** Current, evidence-bound explanation. It never replaces an archived selection basis. */
export interface StockReasoning {
  symbol: string | null; event_id: number; version_id: number | null; observation_id: number | null;
  status: "source_related" | "theme_only" | "insufficient" | "inactive";
  explanation: string; basis: string[]; transmission: string | null;
  ai_scope: "company_context" | "not_applicable"; ai_scope_reason: string; missing: string;
  source_excerpt: {field: "title" | "summary"; quote: string} | null;
  eligibility_changed: false; method: "saved_evidence_projection"; business_effect_verified: false;
}

/** A saved, source-reviewed current explanation; it is not a historical selection basis. */
export interface CompanyImpact {
  symbol: string; event_id: number; version_id: number; observation_id: number;
  input_sha256: string; generated_at: string; valid_until: string; facts_captured_at: string;
  company_name: string;
  facts: {id: string; kind: string; quote: string; document_title: string; url: string; published_at: string; published_precision?: "day"; period: string; page: number; sha256: string}[];
  analysis: {
    status: "conditional" | "mixed" | "insufficient" | "no_supported_link";
    summary: string; chain: {text: string; evidence_ids: string[]}[];
    counterarguments: string[]; missing: string[];
  };
  model: {provider: string; model: string};
  verification: "author_source_reviewed_not_gold"; eligibility_changed: false;
  current_context_only: true; business_effect_verified: false;
}

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
  timing_relation?: {status: "known_before_response" | "late_source_observation" | "interpretation_after_response" | "time_unknown"; label: string};
  alternatives: string[];
  limitations: string[];
  llm_aux?: AttributionAuxJudgment | null;
  stock_reasoning?: StockReasoning | null;
  company_impact?: CompanyImpact | null;
}

export interface StockAttributionPayload {
  schema_version: number;
  symbol: string | null;
  count: number;
  items: AttributionEvidenceItem[];
  later_items?: AttributionEvidenceItem[];
  /** Source-reviewed current research outside the ordinary message window; never a primary trigger. */
  company_items?: AttributionEvidenceItem[];
  primary_reason?: PrimaryAttributionReason;
  evidence_counts?: {scope: "returned_rows_only"; returned: number; theme_only: number; unknown: number; source_time_unknown: number};
  response_context?: {pool_date: string | null; response_as_of: string | null; view: "current_with_response_context"; historical_replay: false};
}

export interface AttributionOptions {poolDate?: string; responseAsOf?: string}
function contextQuery(options?: AttributionOptions): string {
  const params = new URLSearchParams();
  if (options?.poolDate) params.set("pool_date", options.poolDate);
  if (options?.responseAsOf) params.set("response_as_of", options.responseAsOf);
  return params.size ? `&${params}` : "";
}

export async function getStockAttribution(symbol: string, options?: AttributionOptions): Promise<StockAttributionPayload> {
  const result = (await getJson<StockAttributionPayload>(`/api/events/symbol/${encodeURIComponent(symbol)}/attribution?limit=12${contextQuery(options)}`)).data;
  // A response identity mismatch must never put another security's evidence under this title.
  if (result.symbol !== symbol || result.schema_version !== 1 || !Array.isArray(result.items) || (result.later_items !== undefined && !Array.isArray(result.later_items))
    || (result.company_items !== undefined && (!Array.isArray(result.company_items) || result.company_items.length > 4))) {
    throw new Error("归因资料身份不匹配，请重新读取");
  }
  return result;
}

export async function getEventAttribution(eventId: number, symbol?: string | null, options?: AttributionOptions): Promise<StockAttributionPayload> {
  const query = `?${symbol ? `symbol=${encodeURIComponent(symbol)}&` : ""}limit=12${contextQuery(options)}`;
  const result = (await getJson<StockAttributionPayload>(`/api/events/${eventId}/attribution${query}`)).data;
  if (result.symbol !== (symbol || null) || result.schema_version !== 1 || !Array.isArray(result.items) || (result.later_items !== undefined && !Array.isArray(result.later_items))
    || (result.company_items !== undefined && (!Array.isArray(result.company_items) || result.company_items.length > 4))
    || [...result.items, ...(result.later_items || []), ...(result.company_items || [])].some(item => item.event_id !== eventId)) {
    throw new Error("事件归因资料身份不匹配，请重新读取");
  }
  return result;
}
