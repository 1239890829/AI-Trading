/**
 * 模拟交易：账户、持仓、订单、成交
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import { getJson, getJsonArray, sendJson } from "./internal";

export interface PaperAccountInfo {
  cash: number;
  market_value: number;
  total: number;
  total_pnl: number;
  total_pnl_pct: number;
}

export interface PaperPositionInfo {
  symbol: string;
  quantity: number;
  available: number;
  cost_price: number;
  last_price?: number | null;
  pnl?: number | null;
  pnl_pct?: number | null;
}

export interface PaperOrderInfo {
  id: number;
  symbol: string;
  side: string;
  price: number;
  quantity: number;
  status: string;
  filled_price?: number | null;
  fee?: number | null;
  reason?: string | null;
  created_at?: string | null;
}

export interface PaperFill {
  symbol: string;
  date: string;
  side: string;
  price: number;
  quantity: number;
  fee: number;
}

export const getPaperAccount = () => getJson<PaperAccountInfo>("/api/paper/account").then((b) => b.data);

export const getPaperPositions = () => getJsonArray<PaperPositionInfo>("/api/paper/positions");

export const getPaperOrders = (status?: string) =>
  getJsonArray<PaperOrderInfo>(`/api/paper/orders${status ? `?status=${status}` : ""}`);

export const getPaperFills = (symbol: string) =>
  getJsonArray<PaperFill>(`/api/paper/fills?symbol=${symbol}`);

export async function placePaperOrder(symbol: string, side: string, price: number, quantity: number) {
  return (
    await sendJson<{ id: number; status: string; filled_price?: number | null; fee?: number | null }>(
      "/api/paper/orders", "POST", { symbol, side, price, quantity }, 15_000
    )
  ).data;
}

export async function cancelPaperOrder(id: number) {
  await sendJson(`/api/paper/orders/${id}`, "DELETE", undefined, 15_000);
}

export async function resetPaperAccount(initialCash?: number): Promise<PaperAccountInfo> {
  return (
    await sendJson<PaperAccountInfo>("/api/paper/reset", "POST", initialCash ? { initial_cash: initialCash } : {}, 30_000)
  ).data;
}
