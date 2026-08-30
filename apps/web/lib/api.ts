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

/**
 * 统一错误类型（对接后端 B1 错误契约 `{detail, code}`）：
 * - `code` 用于程序化分支：`timeout` / `network_error` 可重试或提示启动后端；
 *   `upstream_failed` 数据源降级；`validation_error` 入参问题；其余按后端返回。
 * - `message` 即 detail（人可读），既有 catch(e).message 展示零破坏。
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, detail: string, code: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** 默认超时：后端卡住时前端不再永远 pending。慢端点在各自 helper 里显式放宽。 */
const DEFAULT_TIMEOUT_MS = 8000;

async function request<T>(
  path: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<{ data: T; meta: Meta }> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...init, signal: ctrl.signal });
  } catch (e) {
    const aborted = e instanceof DOMException && e.name === "AbortError";
    throw new ApiError(
      0,
      aborted ? `${path}：请求超时（${timeoutMs}ms）` : `${path}：网络错误（后端未启动或连接被拒）`,
      aborted ? "timeout" : "network_error",
    );
  } finally {
    clearTimeout(timer);
  }
  const body = (await res.json().catch(() => ({}))) as { data?: T; detail?: string; code?: string };
  if (!res.ok) {
    throw new ApiError(res.status, body.detail ?? `HTTP ${res.status}`, body.code ?? `http_${res.status}`);
  }
  return body as { data: T; meta: Meta };
}

async function getJson<T>(path: string, timeoutMs: number = DEFAULT_TIMEOUT_MS): Promise<{ data: T; meta: Meta }> {
  return request<T>(path, {}, timeoutMs);
}

async function sendJson<T = unknown>(
  path: string,
  method: string,
  body?: unknown,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
): Promise<{ data: T; meta: Meta }> {
  return request<T>(
    path,
    {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    },
    timeoutMs,
  );
}

export async function getMarketOverview(): Promise<{
  indices: Quote[];
  total_amount: number;
}> {
  const body = await getJson<{ indices: Quote[]; total_amount: number }>("/api/market/overview", 10_000);
  return body.data;
}

/** 单只行情（可指定数据源补估值字段，如 tencent）。 */
export async function getQuote(symbol: string, source?: string): Promise<Quote> {
  const qs = source ? `?source=${source}` : "";
  return (await getJson<Quote>(`/api/quotes/${symbol}${qs}`, 10_000)).data;
}

export async function getQuotes(symbols?: string[]): Promise<Quote[]> {
  const qs = symbols && symbols.length > 0 ? `?symbols=${symbols.join(",")}` : "";
  return (await getJson<Quote[]>(`/api/quotes${qs}`)).data;
}

export async function getKline(symbol: string, timeframe = "1d", limit = 250): Promise<Kline[]> {
  return (
    await getJson<{ symbol: string; timeframe: string; bars: Kline[] }>(
      `/api/kline/${symbol}?timeframe=${timeframe}&limit=${limit}`, 15_000
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
  return (await getJson<{ trade_date: string; pool: LimitUpRecord[] }>(`/api/limit-up${qs}`, 20_000)).data.pool;
}

export async function getLonghu(dateStr?: string): Promise<LongHuRecord[]> {
  const qs = dateStr ? `?date=${dateStr}` : "";
  return (await getJson<{ trade_date: string; records: LongHuRecord[] }>(`/api/longhu${qs}`, 20_000)).data.records;
}

/** 个股龙虎榜明细 + 历史（/api/longhu/{symbol}）。类型随消费方窄化。 */
export async function getLonghuDetail<T = unknown>(symbol: string): Promise<T> {
  return (await getJson<T>(`/api/longhu/${symbol}`, 20_000)).data;
}

/** 个股资金流（N 日）。 */
export async function getCapitalFlow<T = unknown>(symbol: string, days = 30): Promise<T> {
  return (await getJson<T>(`/api/capital-flow/${symbol}?days=${days}`, 15_000)).data;
}

/** 个股财务指标（periods=8 即近 8 期）。 */
export async function getFinancials<T = unknown>(symbol: string, periods = 8): Promise<T[]> {
  return (await getJson<{ symbol: string; periods: T[] }>(`/api/financials/${symbol}?periods=${periods}`, 15_000)).data.periods;
}

export interface InfoItem {
  title: string;
  date?: string | null;
  source?: string | null;
  url?: string | null;
  summary?: string | null;
}

/** 个股公告列表。类型随消费方窄化（后端字段为超集）。 */
export async function getAnnouncements<T = InfoItem>(symbol: string, limit = 8): Promise<T[]> {
  return (await getJson<{ symbol: string; items: T[] }>(`/api/announcements/${symbol}?limit=${limit}`, 15_000)).data.items;
}

/** 个股新闻列表。类型随消费方窄化（后端字段为超集）。 */
export async function getNews<T = InfoItem>(symbol: string, limit = 8): Promise<T[]> {
  return (await getJson<{ symbol: string; items: T[] }>(`/api/news/${symbol}?limit=${limit}`, 15_000)).data.items;
}

/** 板块排行（新浪闪电口径）。结构见 /api/boards。 */
export interface BoardRow {
  name: string;
  count?: number | null;
  change_pct?: number | null;
  amount?: number | null;
  leader_symbol?: string | null;
  leader_name?: string | null;
  leader_change_pct?: number | null;
  source?: string;
}

export async function getBoards(type: "hangye" | "concept"): Promise<BoardRow[]> {
  return (await getJson<{ type: string; boards: BoardRow[] }>(`/api/boards?type=${type}`, 15_000)).data.boards;
}

/** 市场宽度（全市场快照价格法）。 */
export interface Breadth {
  up: number; down: number; flat: number; limit_up: number; limit_down: number;
  total: number; total_amount: number; suspended: number;
}

export async function getBreadth(): Promise<Breadth> {
  return (await getJson<{ breadth: Breadth }>("/api/market/breadth", 15_000)).data.breadth;
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
  return (await getJson<ThemeBoardPayload>(`/api/themes${qs}`, 60_000)).data;
}

export async function searchSymbols(q: string): Promise<SymbolSearchItem[]> {
  return (await getJson<SymbolSearchItem[]>(`/api/search?q=${encodeURIComponent(q)}`)).data;
}

export interface MinutePoint {
  ts: string;
  price: number;
  volume?: number | null;
  cum_amount?: number | null;
  cum_volume?: number | null;
  avg?: number | null;
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
  return (await getJson<{ symbol: string; points: MinutePoint[] }>(`/api/minute-line/${symbol}`, 20_000)).data.points;
}

export async function getSentiment(): Promise<Sentiment> {
  return (await getJson<Sentiment>("/api/market/sentiment", 30_000)).data;
}

export async function getWatchlist(): Promise<WatchlistItem[]> {
  return (await getJson<WatchlistItem[]>("/api/watchlist")).data;
}

export async function addToWatchlist(symbol: string, name?: string, group?: string): Promise<WatchlistItem> {
  return (
    await sendJson<WatchlistItem>("/api/watchlist", "POST", { symbol, name, group: group ?? "默认" })
  ).data;
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

export async function getCompanyProfile<T = CompanyProfile>(symbol: string): Promise<T> {
  return (await getJson<T>(`/api/company/${symbol}`, 15_000)).data;
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
  return (
    await sendJson<{ id: number; status: string; filled_price?: number | null; fee?: number | null }>(
      "/api/paper/orders", "POST", { symbol, side, price, quantity }, 15_000
    )
  ).data;
}

export async function cancelPaperOrder(id: number) {
  await sendJson(`/api/paper/orders/${id}`, "DELETE", undefined, 15_000);
}

/** 重置模拟账户：清仓 + 清委托历史 + 资金回到初始额度。不可撤销。 */
export async function resetPaperAccount(initialCash?: number): Promise<PaperAccountInfo> {
  return (
    await sendJson<PaperAccountInfo>("/api/paper/reset", "POST", initialCash ? { initial_cash: initialCash } : {}, 30_000)
  ).data;
}

export async function getWatchlistGroups(): Promise<string[]> {
  return (await getJson<string[]>("/api/watchlist/groups")).data;
}

export async function updateWatchlistGroup(symbol: string, group: string): Promise<void> {
  await sendJson(`/api/watchlist/${symbol}/group`, "PUT", { group });
}

export async function removeFromWatchlist(symbol: string): Promise<void> {
  await sendJson(`/api/watchlist/${symbol}`, "DELETE");
}
