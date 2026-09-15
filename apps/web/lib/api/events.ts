/**
 * 事件与情绪：事件方向、情绪历史、事件标的池
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import { getJson } from "./internal";

export interface EventDirectionRow {
  target_type: string;
  target: string;
  direction: number; // -1 利空 / 0 待判 / +1 利好
  strength: number;
  chain: string;
  basis: string;
  /** 题材辨识度记忆（KB-STOCK-25 / P1-1）：该题材近 30 日历史龙头 top3，仅题材方向附带 */
  memory_leaders?: { symbol: string; name: string; max_boards: number; hit_days: number }[];
}

export interface EventSummary {
  id: number;
  title: string;
  url: string | null;
  /** 正文摘要（快讯源 summary 字段；无原文/抓原文失败时弹窗降级展示） */
  summary?: string | null;
  source: string;
  source_tier: number;
  published_at: string | null;
  fact_kind: string; // fact / opinion / rumor
  certainty: string; // done / proposed / rumor
  category: string; // policy / statement / data / rumor / corporate / other
  half_life_hours: number;
  source_symbol: string | null;
  is_active: boolean;
  directions: EventDirectionRow[];
  /** 判定状态机（2026-09-09）：judged 已判定 / pending 待判 / neutral 待判超时收敛 / expired 过期 */
  judge_status?: "judged" | "pending" | "neutral" | "expired" | "unknown";
  judge_status_label?: string;
  judged_at?: string | null;
  judge_reason?: string | null;
}

export interface EventStockPool {
  target: string;
  direction?: number;
  strength?: number;
  chain?: string;
  basis?: string;
  stocks: { symbol: string; name: string }[];
  note?: string;
}

export interface RankFactors {
  theme_best?: { name: string; chg_pct: number };
  stock_mean_pct?: number;
  phase?: string;
  phase_weight?: number;
  age_hours?: number;
  subtotal?: number;
}

export interface ImpactEvent extends EventSummary {
  four_category: string; // international / policy / hot / material
  four_label: string;
  impact_level: "L1" | "L2" | "L3";
  tags: string[]; // 业绩/公告/异动/资金/行业（规则派生，可多挂）
  rank_score?: number; // sort=relevance 时返回
  rank_reasons?: string[];
  rank_factors?: RankFactors;
}

export type EventSort = "relevance" | "time" | "impact";

export async function getImpactEvents(
  includeL3 = false,
  limit = 100,
  sort: EventSort = "relevance",
): Promise<{
  count: number;
  countsAll: Record<string, number>;
  fourCounts: Record<string, number>;
  tagCounts: Record<string, number>;
  items: ImpactEvent[];
}> {
  const r = await getJson<{
    count: number;
    counts_all: Record<string, number>;
    four_counts: Record<string, number>;
    tag_counts: Record<string, number>;
    items: ImpactEvent[];
  }>(`/api/events/impact?include_l3=${includeL3}&limit=${limit}&sort=${sort}`, 30_000);
  return {
    count: r.data.count,
    countsAll: r.data.counts_all,
    fourCounts: r.data.four_counts,
    tagCounts: r.data.tag_counts,
    items: r.data.items,
  };
}

export async function getEventsForSymbol(
  symbol: string,
): Promise<{ symbol: string; themes: string[]; count: number; items: EventSummary[] }> {
  const r = await getJson<{ symbol: string; themes: string[]; count: number; items: EventSummary[] }>(
    `/api/events/symbol/${symbol}`,
    30_000,
  );
  return r.data;
}

export async function getEventStocks(id: number): Promise<EventStockPool[]> {
  return (await getJson<{ pools: EventStockPool[] }>(`/api/events/${id}/stocks`, 60_000)).data.pools;
}
