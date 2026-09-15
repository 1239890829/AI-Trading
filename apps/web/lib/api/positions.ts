/**
 * 真实持仓：持仓明细、已清仓、成交回填
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import { getJson, sendJson } from "./internal";

export interface RealPositionRow {
  symbol: string;
  name: string | null;
  quantity: number;
  avg_cost: number | null; // 摊薄成本价（含买入费用；覆盖后以覆盖为准）
  cost_total: number; // 总成本
  last_price: number | null; // 实时价，缺行情回退成本价
  day_change_pct: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pct: number | null;
  realized_pnl: number;
  overridden: boolean; // 是否被手动修正过
  trade_count: number;
  last_traded_at: string | null;
}

export interface RealClearedRow {
  symbol: string;
  name: string | null;
  realized_pnl: number;
  trade_count: number;
  last_traded_at: string | null;
}

export interface RealPositionsPayload {
  items: RealPositionRow[];
  cleared: RealClearedRow[];
  total: { market_value: number; cost_total: number; unrealized_pnl: number; realized_pnl: number };
  count: number;
}

export interface RealTradeIn {
  symbol: string;
  name?: string | null;
  side: "buy" | "sell";
  fill_price: number;
  quantity: number;
  fee?: number;
  traded_at?: string;
  note?: string | null;
}

export async function getRealPositions(): Promise<RealPositionsPayload> {
  return (await getJson<RealPositionsPayload>("/api/real/positions", 15_000)).data;
}

export async function createRealTrade(t: RealTradeIn): Promise<void> {
  await sendJson("/api/real/trades", "POST", t);
}

export async function deleteRealTrade(id: number): Promise<void> {
  await sendJson(`/api/real/trades/${id}`, "DELETE");
}

export async function overrideRealPosition(symbol: string, quantity: number, total_cost: number): Promise<void> {
  await sendJson(`/api/real/positions/${symbol}`, "PATCH", { quantity, total_cost });
}

export async function deleteRealPosition(symbol: string): Promise<void> {
  await sendJson(`/api/real/positions/${symbol}`, "DELETE");
}
