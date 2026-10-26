/**
 * 复盘：报告、有效性、改进项
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import { getJson, getJsonArray, sendJson } from "./internal";

export interface ReviewReportSummary {
  review_id: string;
  trade_date: string;
  methodology_version: string;
  model_actual: string;
  model_degraded: boolean;
  summary: string;
  gap_count: number;
  action_item_count: number;
  generated_at: string;
}

export interface ReviewProvenance {
  state: "verified" | "partial" | "unverified" | "unavailable" | "mismatch";
  source: string | null;
  source_as_of: string | null;
  received_at: string | null;
  source_trade_date: string | null;
  requested_trade_date: string;
  reason: string;
}

export interface ReviewGap {field: string; source?: string; reason: string; impact?: string; severity?: string}
export interface ReviewIndexQuote {
  symbol: string; name: string; close: number | null; change_pct: number | null; amount?: number | null;
  source?: string | null; data_timestamp?: string | null; received_at?: string | null;
  quality?: string | null; binding_state?: string;
}
export interface ReviewSectorBoard {
  board_code: string; name: string; change_pct: number | null; main_net_yi: number | null;
  source?: string | null; source_trade_date?: string | null; source_as_of?: string | null;
  source_date_ms?: number | null;
  binding_state?: string;
}
export interface ReviewData {
  trade_date: string;
  collected_at?: string;
  outlook?: {
    state: "saved" | "missing" | "mismatch" | "error";
    trade_date: string;
    source: string;
    generated_at: string | null;
    directions: {direction: string; logic: string; basis: string; entry_mode: string; entry_basis: string; trigger_conditions: string[] | null; falsify_conditions: string[] | null; review: Record<string, unknown> | null; review_state?: "saved" | "unknown"; reviewed_at?: string | null}[];
    missing: string[];
    reason: string;
  };
  selection_journey?: {
    state: "saved" | "empty" | "partial" | "error";
    trade_date: string; source: string; reason: string;
    items: {
      symbol: string; name: string | null; selection_source: string; selection_ref: string; source_version: string;
      source_as_of: string | null; origin_event_id: number; basis: string | null; event_refs: Record<string, unknown>[];
      notification: {in_app_recorded: boolean; outbox: {id: number; channel: string; state: string; reason: string; accepted_at: string | null}[]; delivery_state: string; read_state: string};
      execution: {state: string; reason: string; order: Record<string, unknown> | null};
    }[];
  };
  market: {
    trade_date: string;
    contract_version?: string;
    provenance?: Record<string, ReviewProvenance>;
    indices: ReviewIndexQuote[];
    breadth: Record<string, unknown>;
    sentiment: Record<string, unknown>;
    themes: Record<string, unknown>[];
    theme_summary?: Record<string, unknown>;
    sector_boards?: {industry: ReviewSectorBoard[]; concept: ReviewSectorBoard[]};
    sector_board_summary?: Record<string, unknown>;
    auction_premium?: Record<string, unknown> | null;
    reported_values?: Record<string, unknown>;
    gaps: ReviewGap[];
  };
  trading: {
    trade_date: string;
    contract_version?: string;
    provenance?: Record<string, ReviewProvenance>;
    account: Record<string, unknown>;
    positions: {symbol: string; quantity: number; frozen_today?: number; cost_price?: number; last_price?: number | null; pnl?: number | null; pnl_pct?: number | null}[];
    orders: {id: number; symbol: string; side: string; price: number; quantity: number; status: string; filled_price?: number | null; fee?: number; reason?: string | null; created_at?: string | null}[];
    realized_pnl?: number | null;
    trade_count: number;
    gaps: ReviewGap[];
  };
  picks?: {
    trade_date: string; combo_date?: string | null;
    items: {symbol: string; name?: string | null; score?: number | null; themes: string[]; observation_only: boolean; basis?: string | null; selection_ref?: Record<string, unknown> | null; source_version?: string | null; event_refs?: Record<string, unknown>[]}[];
    reviews: {symbol: string; name?: string | null; verdict: string; reason_category: string; excess_pct?: number | null; note?: string | null; binding_state: string; statistics_eligible: boolean; review_context?: Record<string, unknown>}[];
    gaps: ReviewGap[];
  } | null;
}

export interface ReviewMetricHistoryPoint {
  date: string;
  limit_up: number | null; n_break: number | null; max_board: number | null;
  break_rate: number | null; seal_rate: number | null; seal_base: number | null;
  promo_1to2: number | null; promo_2to3: number | null;
  promo_1to2_base: null; promo_2to3_base: null;
  missing: string[];
}

export interface ReviewMetricHistoryReference {
  contract_version: "metric_history_reference_v1";
  role: "read_time_aggregate_reference"; source: "sentiment.metric_history";
  requested_trade_date: string; window_start: string; window_end: string; read_at: string;
  dataset_updated_at: string | null; source_as_of: null; raw_source_sha256: null;
  available_at_original_report: "unknown";
  state: "partial" | "unavailable" | "error";
  points: ReviewMetricHistoryPoint[]; unrecorded_dates: string[]; invalid_rows: number;
  coverage: {calendar_days: number; recorded_days: number; effective_days: Record<"seal_rate" | "promo_1to2" | "promo_2to3" | "max_board", number>};
  reason: string; limitations: string[];
}

export interface ReviewReportDetail {
  review_id: string;
  trade_date: string;
  generated_at: string;
  methodology_version: string;
  model: { requested?: string; actual?: string; degraded?: boolean; reason?: string };
  /** Older reports remain readable; absence is not a zero-valued market snapshot. */
  data?: ReviewData;
  /** Added when read; not part of the frozen original report or its known inputs. */
  metric_history_reference?: ReviewMetricHistoryReference;
  dimensions: { key: string; title: string; status: string; findings: string[]; judgements: string[]; gaps: string[] }[];
  action_items: {
    id: string;
    title: string;
    category: string;
    priority: string;
    expected_impact: string;
    evidence: string;
    target: string;
    proposed_change: string;
    /** pending | confirmed | applied | rejected | reverted */
    status: string;
    resolution_note: string;
    resolved_at?: string | null;
    disposition_basis?: string;
    implementation_evidence?: ImplementationEvidence;
  }[];
  meta_insights: { dimension: string; observation: string; effectiveness: string; evidence: string; suggestion: string }[];
  summary: string;
}

export interface ReviewEffectiveness {
  by_category: Record<
    string,
    { total: number; confirmed: number; reverted: number; rejected: number; pending: number; adoption_rate: number; revert_rate: number | null }
  >;
}

export async function getReviewReports(): Promise<ReviewReportSummary[]> {
  return getJsonArray<ReviewReportSummary>("/api/review/reports");
}

export async function getReviewReport(tradeDate: string): Promise<ReviewReportDetail> {
  return (await getJson<ReviewReportDetail>(`/api/review/reports/${encodeURIComponent(tradeDate.replaceAll("-", ""))}`)).data;
}

export async function getReviewEffectiveness(): Promise<ReviewEffectiveness> {
  return (await getJson<ReviewEffectiveness>("/api/review/effectiveness")).data;
}

export interface ImplementationEvidence {implementation_ref?: string; release_version?: string; verification_ref?: string}

export type ActionItemStatus = "pending" | "recorded" | "confirmed" | "applied" | "rejected" | "reverted";

export interface ReviewActionItem {
  id: number;
  review_id: string;
  trade_date: string;
  title: string;
  category: string;
  priority: string;
  target: string;
  proposed_change: string;
  status: ActionItemStatus;
  resolution_note: string;
  resolved_at: string | null;
}

export interface ActionItemRef {
  id: string;
  trade_date: string;
  category: string;
  title: string;
}

export async function updateActionItemStatus(
  item: ActionItemRef,
  status: ActionItemStatus,
  note = "",
  implementationEvidence?: ImplementationEvidence,
): Promise<ReviewActionItem> {
  return (await sendJson<ReviewActionItem>(`/api/review/action-items/${item.id}`, "PATCH", {
    status,
    note,
    ...(implementationEvidence ? {implementation_evidence: implementationEvidence} : {}),
    trade_date: item.trade_date,
    category: item.category,
    title: item.title,
  })).data;
}
