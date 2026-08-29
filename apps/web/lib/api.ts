import type {
  Kline,
  LimitUpRecord,
  LongHuRecord,
  Meta,
  OrderBook,
  Quote,
  SymbolSearchItem,
  ThemeBoardPayload,
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

/** 题材梯队看板。首次加载较慢（需回溯 5 日涨停池），后端缓存 60s。 */
export async function getThemes(opts?: {
  date?: string;
  sort?: "strength" | "boards" | "count";
  minBoards?: number;
  minCount?: number;
  limit?: number;
}): Promise<ThemeBoardPayload> {
  const p = new URLSearchParams();
  if (opts?.date) p.set("date", opts.date);
  if (opts?.sort) p.set("sort", opts.sort);
  if (opts?.minBoards) p.set("min_boards", String(opts.minBoards));
  if (opts?.minCount) p.set("min_count", String(opts.minCount));
  if (opts?.limit) p.set("limit", String(opts.limit));
  const qs = p.toString() ? `?${p.toString()}` : "";
  return (await getJson<ThemeBoardPayload>(`/api/themes${qs}`)).data;
}

export async function searchSymbols(q: string): Promise<SymbolSearchItem[]> {
  return (await getJson<SymbolSearchItem[]>(`/api/search?q=${encodeURIComponent(q)}`)).data;
}

export interface MinutePoint {
  ts: string;
  price: number;
  volume?: number | null;
  cum_amount?: number | null;
  source: string;
}

export interface Sentiment {
  phase: string;
  temperature: number;
  confidence: string;
  reasons: string[];
  misjudge_caveats: string[];
  switch_conditions: string;
  indicators: { name: string; value: string | number | null; note?: string }[];
  ladder: Record<string, number>;
  judged_at: string;
  pool_today_count?: number;
  pool_yesterday_count?: number;
}

export async function getMinuteLine(symbol: string): Promise<MinutePoint[]> {
  return (await getJson<{ symbol: string; points: MinutePoint[] }>(`/api/minute-line/${symbol}`)).data.points;
}

export async function getSentiment(): Promise<Sentiment> {
  return (await getJson<Sentiment>("/api/market/sentiment")).data;
}

export async function getWatchlist(): Promise<WatchlistItem[]> {
  return (await getJson<WatchlistItem[]>("/api/watchlist")).data;
}

export async function addToWatchlist(symbol: string, name?: string, group?: string): Promise<WatchlistItem> {
  const res = await fetch(`${API_BASE}/api/watchlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol, name, group: group ?? "默认" }),
  });
  if (!res.ok) throw new Error(`addToWatchlist: HTTP ${res.status}`);
  return (await res.json()).data;
}

export interface CompanyProfile {
  symbol: string;
  name?: string | null;
  industry?: string | null;
  profile?: string | null;
  main_business?: string | null;
  region?: string | null;
  boards?: string[];
  core_themes?: string[];
  source: string;
}

export async function getCompanyProfile(symbol: string): Promise<CompanyProfile> {
  return (await getJson<CompanyProfile>(`/api/company/${symbol}`)).data;
}

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
export const getPaperPositions = () => getJson<PaperPositionInfo[]>("/api/paper/positions").then((b) => b.data);
export const getPaperOrders = (status?: string) =>
  getJson<PaperOrderInfo[]>(`/api/paper/orders${status ? `?status=${status}` : ""}`).then((b) => b.data);
export const getPaperFills = (symbol: string) =>
  getJson<PaperFill[]>(`/api/paper/fills?symbol=${symbol}`).then((b) => b.data);

export async function placePaperOrder(symbol: string, side: string, price: number, quantity: number) {
  const res = await fetch(`${API_BASE}/api/paper/orders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol, side, price, quantity }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body.data as { id: number; status: string; filled_price?: number | null; fee?: number | null };
}

export async function cancelPaperOrder(id: number) {
  const res = await fetch(`${API_BASE}/api/paper/orders/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

/** 重置模拟账户：清仓 + 清委托历史 + 资金回到初始额度。不可撤销。 */
export async function resetPaperAccount(initialCash?: number): Promise<PaperAccountInfo> {
  const res = await fetch(`${API_BASE}/api/paper/reset`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(initialCash ? { initial_cash: initialCash } : {}),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body.data as PaperAccountInfo;
}

export async function getWatchlistGroups(): Promise<string[]> {
  return (await getJson<string[]>("/api/watchlist/groups")).data;
}

export async function updateWatchlistGroup(symbol: string, group: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/watchlist/${symbol}/group`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ group }),
  });
  if (!res.ok) throw new Error(`updateWatchlistGroup: HTTP ${res.status}`);
}

export async function removeFromWatchlist(symbol: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/watchlist/${symbol}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`removeFromWatchlist: HTTP ${res.status}`);
}
