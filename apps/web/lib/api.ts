import type {
  Kline,
  LimitUpRecord,
  LongHuRecord,
  Meta,
  OrderBook,
  Quote,
  SymbolSearchItem,
  Trade,
  WatchlistItem,
} from "@/types/market";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export const WS_BASE = (
  process.env.NEXT_PUBLIC_WS_BASE ?? API_BASE.replace(/^http/, "ws")
) as string;

async function getJson<T>(path: string): Promise<{ data: T; meta: Meta }> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${path}: HTTP ${res.status} ${detail.slice(0, 120)}`);
  }
  return res.json();
}

export async function getMarketOverview(): Promise<{
  indices: Quote[];
  total_amount: number;
}> {
  const body = await getJson<{ indices: Quote[]; total_amount: number }>("/api/market/overview");
  return body.data;
}

export async function getQuotes(symbols?: string[]): Promise<Quote[]> {
  const qs = symbols && symbols.length > 0 ? `?symbols=${symbols.join(",")}` : "";
  return (await getJson<Quote[]>(`/api/quotes${qs}`)).data;
}

export async function getKline(symbol: string, timeframe = "1d", limit = 250): Promise<Kline[]> {
  return (
    await getJson<{ symbol: string; timeframe: string; bars: Kline[] }>(
      `/api/kline/${symbol}?timeframe=${timeframe}&limit=${limit}`
    )
  ).data.bars;
}

export async function getOrderBook(symbol: string): Promise<OrderBook> {
  return (await getJson<OrderBook>(`/api/order-book/${symbol}`)).data;
}

export async function getTrades(symbol: string, limit = 50): Promise<Trade[]> {
  return (await getJson<Trade[]>(`/api/trades/${symbol}?limit=${limit}`)).data;
}

export async function getLimitUpPool(dateStr?: string): Promise<LimitUpRecord[]> {
  const qs = dateStr ? `?date=${dateStr}` : "";
  return (await getJson<{ trade_date: string; pool: LimitUpRecord[] }>(`/api/limit-up${qs}`)).data.pool;
}

export async function getLonghu(dateStr?: string): Promise<LongHuRecord[]> {
  const qs = dateStr ? `?date=${dateStr}` : "";
  return (await getJson<{ trade_date: string; records: LongHuRecord[] }>(`/api/longhu${qs}`)).data.records;
}

export async function searchSymbols(q: string): Promise<SymbolSearchItem[]> {
  return (await getJson<SymbolSearchItem[]>(`/api/search?q=${encodeURIComponent(q)}`)).data;
}

export async function getWatchlist(): Promise<WatchlistItem[]> {
  return (await getJson<WatchlistItem[]>("/api/watchlist")).data;
}

export async function addToWatchlist(symbol: string, name?: string): Promise<WatchlistItem> {
  const res = await fetch(`${API_BASE}/api/watchlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol, name }),
  });
  if (!res.ok) throw new Error(`addToWatchlist: HTTP ${res.status}`);
  return (await res.json()).data;
}

export async function removeFromWatchlist(symbol: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/watchlist/${symbol}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`removeFromWatchlist: HTTP ${res.status}`);
}
