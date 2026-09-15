/**
 * 自选股：列表与增删
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import type {
  WatchlistItem,
} from "@/types/market";

import { getJsonArray, sendJson } from "./internal";

export async function getWatchlist(): Promise<WatchlistItem[]> {
  return getJsonArray<WatchlistItem>("/api/watchlist");
}

export async function addToWatchlist(symbol: string, name?: string, group?: string): Promise<WatchlistItem> {
  return (
    await sendJson<WatchlistItem>("/api/watchlist", "POST", { symbol, name, group: group ?? "默认" })
  ).data;
}

export async function getWatchlistGroups(): Promise<string[]> {
  return getJsonArray<string>("/api/watchlist/groups");
}

export async function createWatchlistGroup(name: string): Promise<void> {
  await sendJson("/api/watchlist/groups", "POST", { name });
}

export async function renameWatchlistGroup(name: string, newName: string): Promise<void> {
  await sendJson(`/api/watchlist/groups/${encodeURIComponent(name)}`, "PUT", { new_name: newName });
}

export async function deleteWatchlistGroup(name: string): Promise<void> {
  await sendJson(`/api/watchlist/groups/${encodeURIComponent(name)}`, "DELETE");
}

export async function updateWatchlistGroup(symbol: string, group: string): Promise<void> {
  await sendJson(`/api/watchlist/${symbol}/group`, "PUT", { group });
}

export async function removeFromWatchlist(symbol: string): Promise<void> {
  await sendJson(`/api/watchlist/${symbol}`, "DELETE");
}
