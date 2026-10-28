import type {ThemeCatalogIdentity} from "./market";

export interface ResearchClusterEvidence {
  id: string; source: string; url: string | null; published_at: string | null;
  observed_at: string; excerpt: string; evidence_type: string;
}
export interface ResearchThemeCluster {
  id: string; name: string; type: "industry_chain" | "market_narrative";
  state: "supported" | "candidate";
  support_scope: "industry_structure" | "market_narrative";
  driver_state: "candidate" | "supported"; driver: string;
  branches: {raw_name: string; official_identity: ThemeCatalogIdentity | null;
    relation: "whole" | "partial" | "candidate"; symbols: string[]; evidence_ids: string[];
    catalog_mapping_state?: "exact"|"ambiguous"|"unmapped"|"unavailable";missing?:string[]}[];
  stocks: {symbol: string; name: string | null; raw_tags: string[]; reason: string; source: string; change_pct: number | null;received_at?:string|null}[];
  evidence: ResearchClusterEvidence[];
  missing: string[]; counter_evidence: string[]; unique_stock_count: number; change_pct: null;
}
export interface ResearchClustersPayload {
  scope: "research_theme_clusters"; methodology_version: string; version: string | null;
  observed_at: string | null; trade_date: string;
  reference_mode: "current" | "saved" | "not_recorded";
  state: "ready" | "partial" | "unavailable" | "not_recorded";
  clusters: ResearchThemeCluster[];
  coverage: {source_scope: "limit_up_sample"; source_count: number; tagged_count: number;
    unique_stock_count: number; complete_universe: false};
  missing: string[];
}
