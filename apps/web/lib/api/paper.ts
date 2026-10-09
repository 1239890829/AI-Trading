/**
 * 模拟交易：账户、持仓、订单、成交
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import { ApiError } from "./client";
import { getJson, getJsonArray, sendJson } from "./internal";

export interface PaperAccountInfo {
  cash: number;
  market_value: number;
  total: number;
  total_pnl: number;
  total_pnl_pct: number;
}

/** Observation of existing funds; an uninitialized account has unknown amounts. */
export interface PaperAccountSnapshot {
  account_created: boolean;
  cash: number | null;
  frozen_cash: number | null;
  initial_cash: number | null;
  market_value: number;
  total: number | null;
  total_pnl: number | null;
  total_pnl_pct: number | null;
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

export function getPaperAccount(readOnly: true): Promise<PaperAccountSnapshot>;
export function getPaperAccount(readOnly?: false): Promise<PaperAccountInfo>;
export function getPaperAccount(readOnly = false): Promise<PaperAccountInfo | PaperAccountSnapshot> {
  return getJson<PaperAccountInfo | PaperAccountSnapshot>(`/api/paper/account${readOnly ? "?read_only=true" : ""}`).then((body) => body.data);
}

export const getPaperPositions = () => getJsonArray<PaperPositionInfo>("/api/paper/positions");

export const getPaperOrders = (status?: string) =>
  getJsonArray<PaperOrderInfo>(`/api/paper/orders${status ? `?status=${status}` : ""}`);

export const getPaperFills = (symbol: string) =>
  getJsonArray<PaperFill>(`/api/paper/fills?symbol=${symbol}`);

type PaperResult = { replayed?: boolean; id: number; status: string; filled_price?: number | null; fee?: number | null };
const submitting = new Map<string, Promise<PaperResult>>();

/** Same unresolved draft retains its identity across network errors and tab reloads. */
export function placePaperOrder(symbol: string, side: string, price: number, quantity: number): Promise<PaperResult> {
  const draft = { symbol, side, price, quantity };
  const key = `paper-action:main:${JSON.stringify(draft)}`;
  const pending = submitting.get(key);
  if (pending) return pending;
  const run = async () => {
    // Storage failure must stop the action: otherwise a reload could lose its identity.
    const stored = sessionStorage.getItem(key);
    const identity: { request_id: string; expires_at: string } = stored
      ? JSON.parse(stored)
      : { request_id: crypto.randomUUID(), expires_at: new Date(Date.now() + 30_000).toISOString() };
    sessionStorage.setItem(key, JSON.stringify(identity));
    try {
      const result = (await sendJson<PaperResult>(
        "/api/paper/orders", "POST", { ...draft, ...identity }, 15_000,
      )).data;
      sessionStorage.removeItem(key);
      return result;
    } catch (e) {
      // A definitive rejection is safe to retry as a new, explicitly confirmed action.
      if (e instanceof ApiError && [401, 403, 422].includes(e.status)) sessionStorage.removeItem(key);
      throw e;
    }
  };
  const promise = run().finally(() => submitting.delete(key));
  submitting.set(key, promise);
  return promise;
}

export async function cancelPaperOrder(id: number) {
  await sendJson(`/api/paper/orders/${id}`, "DELETE", undefined, 15_000);
}

export async function resetPaperAccount(initialCash?: number): Promise<PaperAccountInfo> {
  return (
    await sendJson<PaperAccountInfo>("/api/paper/reset", "POST", initialCash ? { initial_cash: initialCash } : {}, 30_000)
  ).data;
}
