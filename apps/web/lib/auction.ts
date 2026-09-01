import type { AuctionBenchmarkItem } from "./api";

/**
 * 竞价标杆排序：按竞价涨幅降序。
 *
 * ⚠️ `auction_pct` 为 null 表示**数据缺失**，与 0（平开）语义完全不同——
 * 实测 2026-07-15 榜单含 `风华高科 +0.00%` 的真实平开条目，若把 null 当 0
 * 参与比较，缺失条目会挤进榜单中部、伪装成"平开"。故缺失一律沉到末尾。
 */
export function sortAuctionBenchmark(
  items: AuctionBenchmarkItem[] | null | undefined
): AuctionBenchmarkItem[] {
  if (!items || items.length === 0) return [];
  // 复制后排序，不原地改动入参（入参来自 React state，直接 sort 会变异）
  return [...items].sort(
    (a, b) => (b.auction_pct ?? -Infinity) - (a.auction_pct ?? -Infinity)
  );
}
