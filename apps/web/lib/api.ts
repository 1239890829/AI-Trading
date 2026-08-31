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

/**
 * API 基址。默认走**同源相对路径** `/backend`（由 next.config.ts 的 rewrite
 * 反向代理到后端），而不是硬编码 `http://127.0.0.1:8000`。
 *
 * 原因：`NEXT_PUBLIC_*` 在**构建期**内联。硬编码地址部署到 NAS/云主机后，
 * 浏览器会去连"访问者自己电脑的 8000 端口"，前端直接废掉，且换主机必须重新构建。
 * 走同源后，真实后端地址由服务端环境变量 `BACKEND_ORIGIN` 在**运行时**决定。
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/backend";

/**
 * WebSocket 基址。相对路径无法用协议字符串替换推导，必须基于当前页面 origin，
 * 因此做成函数（要读 window，不能在模块顶层求值）。
 *
 * 注意：Next 的 rewrite 不代理 WebSocket 升级。同源模式下若没有前置反代处理
 * upgrade，WS 会连接失败——`useQuoteStream` 会自动降级为 HTTP 轮询，功能不受影响。
 * 要在生产用上 WS，前置 nginx 反代 /backend 并放开 Upgrade 头，或显式设置
 * `NEXT_PUBLIC_WS_BASE`。
 */
export function wsBase(): string {
  if (process.env.NEXT_PUBLIC_WS_BASE) return process.env.NEXT_PUBLIC_WS_BASE;
  if (API_BASE.startsWith("http")) return API_BASE.replace(/^http/, "ws");
  if (typeof window === "undefined") return "";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${API_BASE}`;
}

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

/**
 * 指数详情 symbol 规范化：overview.indices 里的 symbol 是裸 6 位（000001/399001），
 * 直接喂给行情链路会被规范化成个股——裸 "000001" 在深市语境是平安银行，
 * 上证指数必须用 "sh000001"（腾讯源口径，kline/minute-line/quote 均原生支持带前缀代码）。
 *
 * 规则：已带 sh/sz/bj 前缀原样返回（腾讯源时 indices 可能已带前缀）；
 * 否则按市场字段（SH/SZ），缺市场时按号码规则兜底（399/395 开头=深指，其余=沪指）。
 */
export function indexDetailSymbol(symbol: string, market?: string | null): string {
  const s = symbol.trim().toLowerCase();
  if (/^(sh|sz|bj)/.test(s)) return s;
  const prefix = market?.toUpperCase() === "SZ" || s.startsWith("399") ? "sz" : "sh";
  return `${prefix}${s}`;
}

/** 是否为指数形态的 symbol（带市场前缀；个股 symbol 在本系统内恒为裸 6 位）。 */
export function isIndexSymbol(symbol: string): boolean {
  return /^(sh|sz|bj)/i.test(symbol.trim());
}

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

/** 官方题材目录条目（themes/catalog）。 */
export interface ThemeCatalogItem {
  code: string;
  name: string;
}

export async function getThemesCatalog(search?: string): Promise<ThemeCatalogItem[]> {
  const qs = search ? `?search=${encodeURIComponent(search)}` : "";
  return (await getJson<{ items: ThemeCatalogItem[] }>(`/api/themes/catalog${qs}`, 30_000)).data.items;
}

/** 涨速榜（/api/speed-rank）：最近 5 分钟涨跌幅，同花顺行情"涨速"列同口径。
 *  sampled=false 表示采样历史不足 5 分钟（后端刚启动/标的刚进入关注），显示"采样中"。 */
export interface SpeedRankRow {
  symbol: string;
  name?: string | null;
  price?: number | null;
  change_pct?: number | null;
  speed: number | null;
  sampled: boolean;
  sample_span_sec?: number | null;
}

export interface SpeedRankPayload {
  theme: string | null;
  theme_name: string | null;
  window: string;
  basis: string;
  items: SpeedRankRow[];
  note?: string;
}

export async function getSpeedRank(theme?: string, symbols?: string[]): Promise<SpeedRankPayload> {
  const p = new URLSearchParams();
  if (theme) p.set("theme", theme);
  if (symbols && symbols.length > 0) p.set("symbols", symbols.join(","));
  return (await getJson<SpeedRankPayload>(`/api/speed-rank?${p.toString()}`, 20_000)).data;
}

/** 题材内资金合力（P1-5）：官方成分批量快照聚合（涨跌家数/等权涨幅/成交额/涨停家数）。 */
export interface ThemeStrengthRow {
  name: string;
  count: number;
  up: number;
  down: number;
  flat: number;
  missing: number;
  limit_up_count: number;
  avg_change_pct: number | null;
  total_amount: number;
  top_gainers: { symbol: string; name: string | null; change_pct: number }[];
  basis: string;
}

export async function getThemeStrength(codes?: string[]): Promise<Record<string, ThemeStrengthRow>> {
  const qs = codes && codes.length > 0 ? `?codes=${codes.join(",")}` : "";
  return (await getJson<{ themes: Record<string, ThemeStrengthRow> }>(`/api/themes/catalog/strength${qs}`, 20_000)).data.themes;
}

/** 官方板块指数日 K（ths 发布的 88xxxx.TI 指数序列，非自算）。 */
export interface ThemeIndexPayload {
  code: string;
  series: { date: string; close: number }[];
  chg_3d: number | null;
  chg_5d: number | null;
  chg_10d: number | null;
  basis: string;
}

export async function getThemeIndex(code: string, days = 60): Promise<ThemeIndexPayload> {
  return (await getJson<ThemeIndexPayload>(`/api/themes/catalog/index?code=${code}&days=${days}`, 20_000)).data;
}

/** ===== 真实持仓（CONTEXT.md: Real Position 域；与 /api/paper/* 模拟账户完全独立）=====
 *  记账必须用实际成交价（fill_price），行情现价只是录入默认值。 */

export interface RealPositionRow {
  symbol: string;
  name: string | null;
  quantity: number;
  avg_cost: number | null; // 摊薄成本价（含买入费用；覆盖后以覆盖为准）
  cost_total: number; // 总成本
  last_price: number | null; // 实时价，缺行情回退成本价
  day_change_pct: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pct: number | null;
  realized_pnl: number;
  overridden: boolean; // 是否被手动修正过
  trade_count: number;
  last_traded_at: string | null;
}

export interface RealClearedRow {
  symbol: string;
  name: string | null;
  realized_pnl: number;
  trade_count: number;
  last_traded_at: string | null;
}

export interface RealPositionsPayload {
  items: RealPositionRow[];
  cleared: RealClearedRow[];
  total: { market_value: number; cost_total: number; unrealized_pnl: number; realized_pnl: number };
  count: number;
}

export interface RealTradeIn {
  symbol: string;
  name?: string | null;
  side: "buy" | "sell";
  fill_price: number;
  quantity: number;
  fee?: number;
  traded_at?: string;
  note?: string | null;
}

export async function getRealPositions(): Promise<RealPositionsPayload> {
  return (await getJson<RealPositionsPayload>("/api/real/positions", 15_000)).data;
}

export async function createRealTrade(t: RealTradeIn): Promise<void> {
  await sendJson("/api/real/trades", "POST", t);
}

export async function deleteRealTrade(id: number): Promise<void> {
  await sendJson(`/api/real/trades/${id}`, "DELETE");
}

export async function overrideRealPosition(symbol: string, quantity: number, total_cost: number): Promise<void> {
  await sendJson(`/api/real/positions/${symbol}`, "PATCH", { quantity, total_cost });
}

export async function deleteRealPosition(symbol: string): Promise<void> {
  await sendJson(`/api/real/positions/${symbol}`, "DELETE");
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

/** 回测 mandate：yaml 声明的可复跑配置（backend/mandates/）。id 是 load 标识符，file 仅展示。 */
export interface BacktestMandate {
  id: string;
  name: string;
  file: string;
  description: string;
  symbol: string;
  strategy_id: string;
  params: Record<string, number>;
  bars: number;
  valid: boolean;
  error?: string;
}

export async function getBacktestMandates(): Promise<BacktestMandate[]> {
  return (await getJson<BacktestMandate[]>("/api/backtest/mandates")).data;
}

/** 回测运行结果：payload + meta（meta.applied 逐字段说明 默认/mandate/请求 的取值来源）。 */
export interface BacktestRunResult {
  payload: BacktestPayload;
  meta: { applied?: string[]; mandate?: string | null; [k: string]: unknown };
}

export async function runBacktest(req: {
  symbol?: string;
  strategy_id?: string;
  params?: Record<string, number>;
  bars?: number;
  /** 给了 mandate 时其余字段可省略；显式字段优先级高于 mandate（meta.applied 说明来源） */
  mandate?: string;
}): Promise<BacktestRunResult> {
  const env = await sendJson<BacktestPayload>("/api/backtest/run", "POST", req, 60_000);
  const meta = env.meta as unknown as BacktestRunResult["meta"];
  return { payload: env.data, meta: meta ?? {} };
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

/** ---------------------------------------------------------------- 题材人气（B1 热股榜） */

export interface HotStock {
  rank: number;
  symbol: string;
  name: string | null;
  heat: number | null;
  rank_change: number | null;
  themes: string[];
}

export interface HotTheme {
  theme: string;
  theme_code: string | null;
  heat: number;
  hot_count: number;
  best: { symbol: string; name: string | null; rank: number; rank_change: number | null } | null;
  basis: string;
}

export interface ThemesHotPayload {
  ts: string | null;
  stocks: HotStock[];
  themes: HotTheme[];
}

/** ths 热股榜 × 官方成分聚合的题材人气（24 小时榜，后端缓存 60s；人气为估算数据）。 */
export async function getThemesHot(limit = 30): Promise<ThemesHotPayload> {
  return (await getJson<ThemesHotPayload>(`/api/themes/hot?limit=${limit}`, 15_000)).data;
}

export async function searchSymbols(q: string): Promise<SymbolSearchItem[]> {
  return (await getJson<SymbolSearchItem[]>(`/api/search?q=${encodeURIComponent(q)}`)).data;
}

/** 个股题材归属（linkage-design §3.2）：官方成分（L3 结构性）+ 当日涨停归因（L2 行为性）。 */
export interface StockThemeLink {
  theme_code: string;
  theme_name: string;
  source: string; // ths_official | manual
}

export interface StockThemeAttribution {
  theme_name: string;
  date: string;
}

export interface StockThemes {
  symbol: string;
  official: StockThemeLink[];
  attribution: StockThemeAttribution[];
}

export async function getStockThemes(symbol: string): Promise<StockThemes> {
  return (await getJson<StockThemes>(`/api/themes/stock/${symbol}`, 30_000)).data;
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

/** 事件驱动（linkage-design §4）：EventCard 摘要与方向映射。 */
export interface EventDirectionRow {
  target_type: string;
  target: string;
  direction: number; // -1 利空 / 0 待判 / +1 利好
  strength: number;
  chain: string;
  basis: string;
}

export interface EventSummary {
  id: number;
  title: string;
  url: string | null;
  source: string;
  source_tier: number;
  published_at: string | null;
  fact_kind: string; // fact / opinion / rumor
  certainty: string; // done / proposed / rumor
  category: string; // policy / statement / data / rumor / corporate / other
  half_life_hours: number;
  source_symbol: string | null;
  is_active: boolean;
  directions: EventDirectionRow[];
}

export interface EventStockPool {
  target: string;
  direction?: number;
  strength?: number;
  chain?: string;
  basis?: string;
  stocks: { symbol: string; name: string }[];
  note?: string;
}

export async function getEvents(active = true, limit = 20): Promise<EventSummary[]> {
  const r = await getJson<{ count: number; items: EventSummary[] }>(
    `/api/events?active=${active}&limit=${limit}`,
    30_000,
  );
  return r.data.items;
}

/** 个股相关活跃事件（E2：方向题材命中官方归属 或 事件源自该股）。 */
export async function getEventsForSymbol(
  symbol: string,
): Promise<{ symbol: string; themes: string[]; count: number; items: EventSummary[] }> {
  const r = await getJson<{ symbol: string; themes: string[]; count: number; items: EventSummary[] }>(
    `/api/events/symbol/${symbol}`,
    30_000,
  );
  return r.data;
}

export async function getEventStocks(id: number): Promise<EventStockPool[]> {
  return (await getJson<{ pools: EventStockPool[] }>(`/api/events/${id}/stocks`, 60_000)).data.pools;
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


