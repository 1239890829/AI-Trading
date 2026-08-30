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

/** B6 写接口鉴权配套：部署时配置 NEXT_PUBLIC_API_TOKEN 后写请求自动携带；
 *  留空=不带 header（本地 dev 零影响），与后端 settings.api_token 的 opt-in 语义对称。 */
const API_WRITE_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN;

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
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(API_WRITE_TOKEN ? { "X-API-Token": API_WRITE_TOKEN } : {}),
      },
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

/** 资讯摘要项：原字段 + 规则摘要结果（重要度/情绪/事实摘要/关键数字）。 */
export interface NewsDigestItem {
  title: string;
  date: string | null;
  url: string | null;
  source: string | null;
  type?: string | null;
  importance: "高" | "中" | "普通" | "低";
  importance_score: number;
  importance_reasons: string[];
  sentiment: "偏正面" | "偏负面" | "分歧" | "中性";
  sentiment_reasons: string[];
  digest: string;
  digest_source: string;
  numbers: string[];
}

export interface NewsDigestModel {
  requested: string;
  actual: string;
  fallback_chain: string[];
  degraded: boolean;
  reason: string;
  latency_ms: number;
}

export interface NewsDigest {
  symbol: string;
  news: NewsDigestItem[];
  announcements: NewsDigestItem[];
  model: NewsDigestModel;
}

/**
 * 个股新闻+公告摘要，按重要度倒序。
 * 规则摘要器永远可用；`model.degraded` 为真表示曾尝试 LLM 并降级，
 * 前端应据此标注来源，别让读者误以为摘要出自模型。
 */
export async function getNewsDigest(symbol: string, limit = 8): Promise<NewsDigest> {
  return (await getJson<NewsDigest>(`/api/news/digest/${symbol}?limit=${limit}`, 15_000)).data;
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

/** 云图载荷：行业分组 treemap 数据（组内个股截断为流通市值 Top12，其余并入「其他」）。 */
export interface HeatmapStock {
  symbol: string;
  name: string;
  change_pct: number;
  price: number | null;
  float_cap_yi: number;
  amount_yi: number;
  is_aggregate?: boolean;
}

export interface HeatmapGroup {
  industry: string;
  float_cap_yi: number;
  change_pct_w: number;
  count: number;
  stocks: HeatmapStock[];
}

export interface HeatmapPayload {
  updated_at: string;
  count: number;
  skipped_no_quote: number;
  industry_coverage: number;
  breadth_summary: { up: number; down: number; flat: number };
  total_amount_yi: number;
  groups: HeatmapGroup[];
}

export async function getHeatmap(): Promise<HeatmapPayload> {
  return (await getJson<HeatmapPayload>("/api/market/heatmap", 30_000)).data;
}

/** ---------------------------------------------------------------- 回测（Phase 6 后半） */

export interface BacktestTrade {
  signal_ts: string;
  fill_ts: string;
  side: "buy" | "sell";
  price: number;
  ref_price: number;
  qty: number;
  fee: number;
  ok: boolean;
  reason: string;
}

export interface BacktestEquityPoint {
  ts: string;
  value: number;
  benchmark: number;
}

export interface BacktestMetrics {
  total_return: number;
  benchmark_return: number;
  excess_return: number;
  annual_return: number;
  max_drawdown: number;
  max_drawdown_days: number;
  sharpe: number;
  sortino: number;
  calmar: number;
  win_rate: number;
  profit_loss_ratio: number;
  in_return: number;
  out_return: number;
}

export interface BacktestPayload {
  symbol: string;
  strategy_id: string;
  bars_count: number;
  metrics: BacktestMetrics;
  equity: BacktestEquityPoint[];
  trades: BacktestTrade[];
  config: Record<string, number>;
  notes: string[];
}

export interface StrategyInfo {
  id: string;
  name: string;
  params: Record<string, number>;
}

export async function getBacktestStrategies(): Promise<StrategyInfo[]> {
  return (await getJson<StrategyInfo[]>("/api/backtest/strategies")).data;
}

export async function runBacktest(req: {
  symbol: string;
  strategy_id: string;
  params?: Record<string, number>;
  bars?: number;
}): Promise<BacktestPayload> {
  return (await sendJson<BacktestPayload>("/api/backtest/run", "POST", req, 60_000)).data;
}

/** ---------------------------------------------------------------- 自选 sparkline（retro #9） */

export interface SparklineItem {
  symbol: string;
  closes: number[];
  period_change_pct: number;
}

export interface SparklinePayload {
  items: SparklineItem[];
  cached: boolean;
}

/** 批量迷你走势：近 N 日 TDX 日K收盘。后端缓存 5 分钟；失败标的缺省。 */
export async function getSparklines(symbols: string[], days = 30): Promise<SparklinePayload> {
  return (await getJson<SparklinePayload>(`/api/sparkline?symbols=${symbols.join(",")}&days=${days}`, 30_000)).data;
}

/** ---------------------------------------------------------------- 选股器（Phase 5） */

export interface ScreenerSignal {
  name: string;
  bias: "bull" | "bear" | "neutral";
  score: number;
  detail: string;
}

export interface ScreenerItem {
  symbol: string;
  name: string;
  price: number;
  change_pct: number;
  turnover_rate: number | null;
  amount_yi: number;
  float_cap_yi: number | null;
  score: number;
  grade: "A" | "B" | "C" | "D";
  bias: "bull" | "bear" | "neutral";
  signals: ScreenerSignal[];
  summary: string;
  fail_conditions: string[];
}

export interface ScreenerPayload {
  items: ScreenerItem[];
  scanned: number;
  filtered: number;
  scored: number;
  failed: number;
  snapshot_time: string | null;
  computed_at: string;
  cached: boolean;
  scorer_version: string;
  disclaimers: string[];
}

export interface ScreenerOpts {
  changeLow?: number;
  changeHigh?: number;
  minAmountYi?: number;
  minTurnover?: number;
  excludeSt?: boolean;
  excludeBj?: boolean;
  excludeNew?: boolean;
  limit?: number;
}

/** 全市场选股器：快照过滤 + TDX 日K评分。首跑约 15-25 秒（重操作），后端缓存 30 分钟。 */
export async function getScreener(opts?: ScreenerOpts): Promise<ScreenerPayload> {
  const p = new URLSearchParams();
  if (opts?.changeLow != null) p.set("change_low", String(opts.changeLow));
  if (opts?.changeHigh != null) p.set("change_high", String(opts.changeHigh));
  if (opts?.minAmountYi != null) p.set("min_amount_yi", String(opts.minAmountYi));
  if (opts?.minTurnover != null) p.set("min_turnover", String(opts.minTurnover));
  if (opts?.excludeSt != null) p.set("exclude_st", String(opts.excludeSt));
  if (opts?.excludeBj != null) p.set("exclude_bj", String(opts.excludeBj));
  if (opts?.excludeNew != null) p.set("exclude_new", String(opts.excludeNew));
  if (opts?.limit != null) p.set("limit", String(opts.limit));
  const qs = p.toString() ? `?${p.toString()}` : "";
  return (await getJson<ScreenerPayload>(`/api/screener${qs}`, 90_000)).data;
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

/** 分时 + 精确量比基线（最近 5 个完整交易日逐 5min 槽同期累计量均值；TDX 历史缺失时为 null）。 */
export async function getMinuteLineWithBaseline(symbol: string): Promise<{
  points: MinutePoint[];
  vr_baseline_5m: number[] | null;
}> {
  return (
    await getJson<{ symbol: string; points: MinutePoint[]; vr_baseline_5m: number[] | null }>(
      `/api/minute-line/${symbol}`, 20_000
    )
  ).data;
}

/** 集合竞价快照（最近交易日终态；auction_volume 单位为手）。 */
export interface AuctionData {
  symbol: string;
  name?: string | null;
  auction_price: number | null;
  auction_pct: number | null;
  auction_volume: number | null;
  auction_volume_ratio: number | null;
  auction_unmatched: number | null;
  pre_close_price: number | null;
  data_status: string | null;
}

export async function getAuction(symbol: string): Promise<AuctionData> {
  return (await getJson<AuctionData>(`/api/auction/${symbol}`, 10_000)).data;
}

export async function getSentiment(): Promise<Sentiment> {
  return (await getJson<Sentiment>("/api/market/sentiment", 30_000)).data;
}

export interface SentimentHistoryItem {
  trade_date: string;
  phase: string;
  temperature: number | null;
  confidence: string | null;
  phase_unreliable: boolean;
  source: "review" | "live";
}

export interface SentimentHistoryPayload {
  items: SentimentHistoryItem[];
  cycle: {
    start_date: string | null;
    start_phase?: string;
    days: number;
    current_group: string | null;
  };
  backfilled: number;
  notes: string[];
}

/** 情绪周期序列（retro #17）：近 N 个交易日判定 + 周期起点定位。 */
export async function getSentimentHistory(days = 10): Promise<SentimentHistoryPayload> {
  return (await getJson<SentimentHistoryPayload>(`/api/market/sentiment-history?days=${days}`, 30_000)).data;
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

/** ---------------------------------------------------------------- 预警通知（Phase 8） */

export type AlertConditionType = "price_above" | "price_below" | "change_pct_above" | "change_pct_below";
export type AlertScope = "watchlist" | "symbols" | "all";

export interface AlertRule {
  id: number;
  name: string;
  condition_type: AlertConditionType;
  threshold: number;
  symbols: string[];
  scope: AlertScope;
  cooldown_seconds: number;
  channels: string[];
  enabled: boolean;
  last_triggered_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AlertEvent {
  id: number;
  rule_id: number;
  symbol: string;
  trigger_value: number;
  threshold: number;
  triggered_at: string;
  acknowledged: boolean;
  delivered_channels: string[];
  snapshot: Quote | null;
}

export interface AlertChannels {
  available: string[];
  default: string[];
}

export interface AlertRuleCreate {
  name: string;
  condition_type: AlertConditionType;
  threshold: number;
  symbols?: string[];
  scope?: AlertScope;
  cooldown_seconds?: number;
  channels?: string[];
  enabled?: boolean;
}

export async function getAlertChannels(): Promise<AlertChannels> {
  return (await getJson<AlertChannels>("/api/alerts/channels")).data;
}

export async function listAlertRules(): Promise<AlertRule[]> {
  return (await getJson<AlertRule[]>("/api/alerts/rules")).data;
}

export async function createAlertRule(rule: AlertRuleCreate): Promise<AlertRule> {
  return (await sendJson<AlertRule>("/api/alerts/rules", "POST", rule)).data;
}

export async function updateAlertRule(id: number, rule: Partial<AlertRuleCreate>): Promise<AlertRule> {
  return (await sendJson<AlertRule>(`/api/alerts/rules/${id}`, "PUT", rule)).data;
}

export async function deleteAlertRule(id: number): Promise<void> {
  await sendJson(`/api/alerts/rules/${id}`, "DELETE");
}

export async function listAlertEvents(limit = 50, ruleId?: number): Promise<AlertEvent[]> {
  const qs = new URLSearchParams();
  qs.set("limit", String(limit));
  if (ruleId != null) qs.set("rule_id", String(ruleId));
  return (await getJson<AlertEvent[]>(`/api/alerts/events?${qs.toString()}`)).data;
}

export async function ackAlertEvent(eventId: number): Promise<void> {
  await sendJson(`/api/alerts/events/${eventId}/ack`, "POST");
}

/** ---------------------------------------------------------------- 风控（Phase 5） */

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


