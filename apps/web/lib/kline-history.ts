import type {Kline} from "@/types/market";
import {bjDate} from "./format";

export function klineDay(row: Kline): string {
  const day = bjDate(row.ts);
  if (!day) throw new Error("K线日期无效，未合并历史");
  return day;
}

export function checkKlineIdentity(rows: readonly Kline[], symbol: string): void {
  let previous = "";
  for (const row of rows) {
    if (row.symbol !== symbol || row.timeframe !== "1d") throw new Error("K线证券或周期不匹配");
    const day = klineDay(row);
    if (day <= previous) throw new Error("K线日期重复或乱序，未合并历史");
    previous = day;
  }
}

function seriesIdentity(rows: readonly Kline[]): string {
  let identity = "";
  for (const row of rows) {
    if (!["high", "medium"].includes(row.quality)) throw new Error("K线来源质量不足，暂停历史混拼；保留原图，可手动重试");
    if (!["qfq", "hfq", "none"].includes(row.price_basis ?? "unknown")) throw new Error("复权口径未知，暂停加载历史；可手动重试");
    if (!row.source?.trim()) throw new Error("K线来源未知，未合并历史");
    if (![row.open, row.high, row.low, row.close].every(value => typeof value === "number" && Number.isFinite(value) && value > 0)) throw new Error("历史OHLC缺失或无效，未合并");
    if (row.high! < Math.max(row.open!, row.close!, row.low!) || row.low! > Math.min(row.open!, row.close!, row.high!)) throw new Error("历史OHLC区间无效，未合并");
    const next = `${row.source}:${row.price_basis}`;
    if (identity && identity !== next) throw new Error("K线来源或复权口径不一致，未合并历史");
    identity = next;
  }
  return identity;
}

function samePrices(left: Kline, right: Kline): boolean {
  return (["open", "high", "low", "close"] as const).every(key => left[key] === right[key]);
}

export function klineHistoryWindow(rows: readonly Kline[]): {start: string; end: string; boundary: string} {
  if (!rows.length) throw new Error("尚无日K基座，不能读取历史");
  seriesIdentity(rows);
  const boundary = klineDay(rows[0]);
  const start = new Date(`${boundary}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - 500);
  return {start: start.toISOString().slice(0, 10), end: klineDay(rows[Math.min(4, rows.length - 1)]), boundary};
}

/** Both paths compare with the owner's current series, never a request's old copy. */
export function prependKlineHistory(current: readonly Kline[], page: readonly Kline[], symbol: string, window: {start: string; end: string}): Kline[] {
  checkKlineIdentity(current, symbol);
  checkKlineIdentity(page, symbol);
  if (!page.length) throw new Error("本次历史窗口未返回记录；不能据此认定历史已结束，可手动重试");
  if (!current.length) throw new Error("日K基座已退出，未合并历史");
  if (page.some(row => klineDay(row) < window.start || klineDay(row) > window.end)) throw new Error("历史页超出请求日期范围，未合并");
  if (seriesIdentity(current) !== seriesIdentity(page)) throw new Error("历史页与当前日K来源或复权口径不同，未合并");
  const existing = new Map(current.map(row => [klineDay(row), row]));
  const overlap = page.filter(row => existing.has(klineDay(row)));
  if (!overlap.length) throw new Error("历史页缺少重叠交易日，无法核实复权锚，未合并");
  if (overlap.some(row => !samePrices(existing.get(klineDay(row))!, row))) throw new Error("重叠OHLC已变化，可能复权锚修订；保留原图，暂停混拼");
  const older = page.filter(row => klineDay(row) < klineDay(current[0]));
  if (!older.length) throw new Error("本次窗口没有更早记录；未认定全历史结束，可手动重试");
  return [...older, ...current];
}

export function refreshKlineHistory(current: readonly Kline[], latest: readonly Kline[], symbol: string, today: string): Kline[] {
  checkKlineIdentity(current, symbol);
  checkKlineIdentity(latest, symbol);
  if (!latest.length) throw new Error("最新日K未返回记录；保留已加载历史，当前刷新待核");
  if (seriesIdentity(current) !== seriesIdentity(latest)) throw new Error("最新日K来源或复权口径已变；保留旧历史，暂停混拼");
  const existing = new Map(current.map(row => [klineDay(row), row]));
  const overlap = latest.filter(row => existing.has(klineDay(row)));
  if (!overlap.length) throw new Error("最新日K与已加载历史没有重叠；保留旧历史，未混拼");
  if (overlap.some(row => klineDay(row) !== today && !samePrices(existing.get(klineDay(row))!, row))) throw new Error("已收盘重叠OHLC修订；保留旧历史，暂停混拼");
  const merged = new Map(existing);
  for (const row of latest) merged.set(klineDay(row), row);
  return [...merged.values()].sort((a, b) => klineDay(a).localeCompare(klineDay(b)));
}
