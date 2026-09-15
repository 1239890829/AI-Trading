/**
 * 风控：状态与下单预检
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import { getJson, sendJson } from "./internal";

export interface RiskState {
  state: string;
  reasons: string[];
  params: {
    single_stock_max_pct: number;
    total_position_max_pct: number;
    strategy_weights: Record<string, number>;
    stop_loss_pct: number;
    add_position_limit: string;
    high_position_stock_limit: string;
    drawdown_protection_pct: number;
  };
  updated_at: string | null;
}

export interface OrderCheckResult {
  allowed: boolean;
  max_qty: number;
  reasons: string[];
  warnings: string[];
  state: string;
}

export interface OrderCheckRequest {
  symbol: string;
  side: "buy" | "sell";
  price: number;
  quantity: number;
}

export async function getRiskState(): Promise<RiskState> {
  return (await getJson<RiskState>("/api/risk/state")).data;
}

export async function checkOrderRisk(req: OrderCheckRequest): Promise<OrderCheckResult> {
  return (await sendJson<OrderCheckResult>("/api/risk/check-order", "POST", req)).data;
}
