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

export interface ReviewReportDetail {
  review_id: string;
  trade_date: string;
  generated_at: string;
  methodology_version: string;
  model: { requested?: string; actual?: string; degraded?: boolean; reason?: string };
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
  return (await getJson<ReviewReportDetail>(`/api/review/reports/${tradeDate}`)).data;
}

export async function getReviewEffectiveness(): Promise<ReviewEffectiveness> {
  return (await getJson<ReviewEffectiveness>("/api/review/effectiveness")).data;
}

export type ActionItemStatus = "pending" | "confirmed" | "applied" | "rejected" | "reverted";

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
): Promise<ReviewActionItem> {
  return (await sendJson<ReviewActionItem>(`/api/review/action-items/${item.id}`, "PATCH", {
    status,
    note,
    trade_date: item.trade_date,
    category: item.category,
    title: item.title,
  })).data;
}
