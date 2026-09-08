import type {
  Kline,
  LimitDownRecord,
  LimitUpRecord,
  LongHuRecord,
  Meta,
  OrderBook,
  Quote,
  SymbolSearchItem,
  ThemeBoardPayload,
  Trade,
  TradingStatusInfo,
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

export interface KlinePayload {
  symbol: string;
  timeframe: string;
  bars: Kline[];
  /** 停牌判定。仅 timeframe=1d 有值；null = 未判定（非日线），**不是**"正常交易"。 */
  trading_status: TradingStatusInfo | null;
}

export async function getKlinePayload(symbol: string, timeframe = "1d", limit = 250): Promise<KlinePayload> {
  return (
    await getJson<KlinePayload>(
      `/api/kline/${symbol}?timeframe=${timeframe}&limit=${limit}`, 15_000
    )
  ).data;
}

export async function getKline(symbol: string, timeframe = "1d", limit = 250): Promise<Kline[]> {
  return (await getKlinePayload(symbol, timeframe, limit)).bars;
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

export async function getLimitDownPool(dateStr?: string): Promise<LimitDownRecord[]> {
  const qs = dateStr ? `?date=${dateStr}` : "";
  return (await getJson<{ trade_date: string; pool: LimitDownRecord[] }>(`/api/limit-down${qs}`, 20_000)).data.pool;
}

export async function getLonghu(dateStr?: string): Promise<LongHuRecord[]> {
  const qs = dateStr ? `?date=${dateStr}` : "";
  return (await getJson<{ trade_date: string; records: LongHuRecord[] }>(`/api/longhu${qs}`, 20_000)).data.records;
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
  /** 数据源三态：null=正常；非 null=该侧数据源失败已降级（显式提示，非静默空） */
  news_error: string | null;
  announcements_error: string | null;
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

/** ===== 每日精选（CONTEXT.md: Daily Picks 域；≤5 只，收盘定次日+换股门槛 15 分）===== */

/** 止损参考位（CONTEXT.md: Risk Tier 的出场纪律；不是操作指令） */
export interface StopLossRef {
  pct: number;
  price: number;
  basis: string;
}

/** 出场纪律参考（借鉴 freqtrade 的 trailing stop / ROI table，参数按 A 股重设） */
export interface ExitDiscipline {
  trailing_pct: number;
  roi_ladder: { gain_pct: number; action: string }[];
  note: string;
  disclaimer: string;
}

export interface DailyPickItem {
  symbol: string;
  name: string | null;
  price: number | null;
  change_pct: number | null;
  // 估值（后端 fill_valuation 从腾讯补；链首 ths 快照本身不带这些字段）。
  // 可能为 null：数据源未提供（新股/亏损/长期停牌），前端显示"暂无"并说明原因，不臆造。
  pe_ttm?: number | null;
  pb?: number | null;
  score: number;
  sub_scores: Record<string, number>; // sentiment/news/tech/fundamental/capital/echelon
  bases: Record<string, string>;      // 各维度可解释依据
  vetoes: string[];
  buy_range: { low: number; high: number; basis: string } | null; // 空仓闸门触发时为 null
  themes: string[];
  related_events: string[];
  // --- 联合研判（梯队地位 × 题材阶段）与风险档位 ---
  echelon_role?: string | null;   // 空间板/龙头/中军/反包/领涨/补涨/首板/同步/跟风/滞涨/断板
  echelon_basis?: string;
  theme?: string | null;
  theme_stage?: string | null;    // 启动/发酵/高潮/分歧/退潮
  boards?: number | null;         // 连板高度（非涨停/旧数据 null，不臆造）
  risk_tier?: string | null;      // 龙头博弈/趋势跟随/情绪低位
  stop_loss?: StopLossRef | null;
  exit_discipline?: ExitDiscipline | null;
  invalidations?: string[];
  observation_only?: boolean;     // 空仓闸门触发：不给买入范围（历史字段，三态都为 true）
  // 空仓闸门三态（审查 §4.2）：blocked 禁买 / observe 仅观察 / followable 可跟。
  // 可跟 ≠ 可买：仍无买入范围，参与须经影子持仓先验证；非闸门日/旧数据缺省。
  follow_state?: "blocked" | "observe" | "followable" | null;
  follow_reasons?: string[];
  // --- meta 置信层（规则版）：综合分+相位+筹码+红线 → 三档置信 ---
  confidence?: {
    tier: "strong" | "executable" | "observe";
    label: string;
    reasons: string[];
  } | null;
  // --- 筹码信号（CYQ 近似 × 量价；None=未触发，available=false=数据缺失）---
  chip_signal?: {
    available: boolean;
    signal: "distribution_warning" | "launch_watch" | null;
    label: string | null;
    reasons: string[];
    reason?: string;
    metrics?: {
      profit_ratio: number | null;
      concentration: number | null;
      main_peak: number | null;
      last_close: number | null;
      price_pos: number | null;
      vol_ratio_5_20: number | null;
      chg3_pct: number | null;
    } | null;
    approx?: boolean;
    as_of?: string | null;
  } | null;
}

/** 炒作阶段（CONTEXT.md: Speculation Regime）—— 六维权重的选择器 */
export interface PickRegime {
  regime: string;
  weights: Record<string, number>;
  basis: string;
  calendar_window: boolean;
  earnings_ratio: number | null;
}

/** 相位→风格路由（审查 §4.1）：当日风格 + 六维权重偏移（routed=false=相位缺失未路由） */
export interface StyleRouting {
  phase: string | null;
  style: string;
  label: string;
  offsets: Record<string, number>;
  basis: string;
  routed: boolean;
}

/** 空仓闸门（CONTEXT.md: Stand-aside Gate） */
export interface StandAsideGate {
  stand_aside: boolean;
  level: "none" | "mild" | "strong";
  reasons: string[];
  advice: string;
  disclaimer?: string;
}

export interface DailyPicksPayload {
  date: string | null;
  items: DailyPickItem[];
  stale?: boolean;
  replaced?: { out: string; in: string; delta: number }[];
  note?: string;
  meta?: {
    weights: Record<string, number>;
    regime?: PickRegime;
    style_routing?: StyleRouting | null;
    gate?: StandAsideGate;
    market_phase?: string | null;
    candidate_count?: number;
    limit_up_count?: number;
    market_max_boards?: number;
  } | null;
}

export interface PickReviewRow {
  date: string;
  symbol: string;
  name: string | null;
  verdict: "good" | "flat" | "bad";
  reason_category: string;
  excess_pct: number;
  note: string;
}

export async function getTodayPicks(): Promise<DailyPicksPayload> {
  return (await getJson<DailyPicksPayload>("/api/picks/today", 15_000)).data;
}

export async function generatePicks(): Promise<DailyPicksPayload> {
  return (await sendJson<DailyPicksPayload>("/api/picks/generate", "POST", {}, 60_000)).data;
}

export async function getPicksHistory(limit = 10): Promise<{ date: string; symbols: (string | null)[]; score_avg: number }[]> {
  return (await getJson<{ date: string; symbols: (string | null)[]; score_avg: number }[]>(`/api/picks/history?limit=${limit}`, 10_000)).data;
}

export async function getPickReviews(date?: string): Promise<PickReviewRow[]> {
  const qs = date ? `?date=${date}` : "";
  return (await getJson<PickReviewRow[]>(`/api/picks/review${qs}`, 15_000)).data;
}

/** 复盘生成结果。date 是服务端实际复盘的交易日（可能与"今天"不同，如休市时复盘最近交易日） */
export interface PickReviewGenerateResult {
  date: string;
  reviews: PickReviewRow[];
  market_pct: number | null;
}

export async function generatePickReview(): Promise<PickReviewGenerateResult> {
  return (await sendJson<PickReviewGenerateResult>("/api/picks/review/generate", "POST", {}, 30_000)).data;
}

/** 按梯队角色的胜率分布（回答"能不能按题材抓妖"的直接证据） */
export interface RolePerformance {
  role: string;
  count: number;
  good: number;
  bad: number;
  flat: number;
  win_rate: number;
  avg_excess: number;
}

export async function getPicksMeta(): Promise<{
  reason_distribution: Record<string, number>;
  role_performance?: RolePerformance[];
  note: string;
}> {
  return (
    await getJson<{
      reason_distribution: Record<string, number>;
      role_performance?: RolePerformance[];
      note: string;
    }>("/api/picks/meta", 10_000)
  ).data;
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
  /** daily=近 N 日收盘升序；minute=当日 1 分钟分时价格（盘外=最近交易日全天） */
  closes: number[];
  /** daily=区间涨跌 %；minute=相对当日开盘变动 %（前端列表定色用行情 change_pct，不消费此字段） */
  period_change_pct: number;
}

export interface SparklinePayload {
  items: SparklineItem[];
  cached: boolean;
}

/** 批量迷你走势（2026-09-07 起自选列表默认 minute=当日分时）：后端缓存 daily 5min / minute 60s；失败标的缺省。 */
export async function getSparklines(
  symbols: string[],
  days = 30,
  period: "daily" | "minute" = "daily",
): Promise<SparklinePayload> {
  return (
    await getJson<SparklinePayload>(`/api/sparkline?symbols=${symbols.join(",")}&days=${days}&period=${period}`, 30_000)
  ).data;
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

/** ths 飙升榜行（B1）：与热股榜排名逻辑不同——「正在变热」的更早信号。 */
export interface SkyrocketRow {
  rank: number;
  symbol: string;
  name: string | null;
  heat: number | null;
  rank_change: number | null;
  ts: string | null;
  source: string;
}

/** ths 飙升榜（后端缓存 60s；人气为估算数据，榜单有延迟不构成交易信号）。 */
export async function getSkyrocket(period: "day" | "hour" = "day"): Promise<SkyrocketRow[]> {
  return (await getJson<{ rows: SkyrocketRow[]; period: string }>(
    `/api/market/heat/skyrocket?period=${period}`,
    15_000,
  )).data.rows;
}

/** 龙虎榜跨日题材轨迹（B3）：概念等分守恒口径（非真实拆分），仅日榜参与。 */
export interface LonghuTrailPoint {
  date: string;
  net: number | null; // 当日该概念无净额记录为 null（不冒充 0）
}

export interface LonghuTrailRow {
  concept: string;
  daily: LonghuTrailPoint[];
  total: number | null;
  first: number | null;
  last: number | null;
}

export interface LonghuTrailPayload {
  days: string[];
  trail: LonghuTrailRow[];
  degraded: string[];
  note: string;
}

/** 龙虎榜题材迁徙（后端缓存 300s；榜单 T-1 披露后不变）。 */
export async function getLonghuThemeTrail(days = 5): Promise<LonghuTrailPayload> {
  return (await getJson<LonghuTrailPayload>(
    `/api/market/longhu/theme-trail?days=${days}`,
    20_000,
  )).data;
}

export async function searchSymbols(q: string): Promise<SymbolSearchItem[]> {
  return (await getJson<SymbolSearchItem[]>(`/api/search?q=${encodeURIComponent(q)}`)).data;
}

/** 个股题材归属（linkage-design §3.2）：官方成分（L3 结构性）+ 当日涨停归因（L2 行为性）。 */
export interface StockThemeLink {
  theme_code: string;
  theme_name: string;
  source: string; // ths_official | manual
  /** 官方板块指数当日涨跌幅 %（2026-09-01：chips 排序与徽标用；拉取失败为 null） */
  theme_chg_1d?: number | null;
  /** 与今日整体涨跌行情的联动度（方向一致家数占比 0-1，2026-09-01 排序依据 #2）；不可得为 null */
  theme_align_1d?: number | null;
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

/** 竞价基准条目（ths 短线风向标竞价基准，按日）：auction_pct 为竞价涨幅 %。 */
export interface AuctionBenchmarkItem {
  symbol: string;
  name: string | null;
  /** 竞价涨幅 %；0 是真实"平开"，与 null（缺失）语义不同，不可互相替代 */
  auction_pct: number | null;
  tags: string[];
}

/**
 * 短线风向标竞价基准（按日，含题材 tags）。
 *
 * 非交易日/无数据返回 **502**（后端 provider 抛 ProviderError），调用方须静默降级。
 * date 参数真实有效、非静默回退（2026-09-01 实测：08-31 / 08-28 / 07-15 内容各不相同）。
 */
export async function getAuctionBenchmark(date?: string): Promise<AuctionBenchmarkItem[]> {
  const qs = date ? `?date=${encodeURIComponent(date)}` : "";
  return (await getJson<AuctionBenchmarkItem[]>(`/api/auction-benchmark${qs}`, 15_000)).data;
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

/** 事件影响力视图（§六.4 拍板）：四级分类 + L1/L2/L3。 */
export interface RankFactors {
  theme_best?: { name: string; chg_pct: number };
  stock_mean_pct?: number;
  phase?: string;
  phase_weight?: number;
  age_hours?: number;
  subtotal?: number;
}

export interface ImpactEvent extends EventSummary {
  four_category: string; // international / policy / hot / material
  four_label: string;
  impact_level: "L1" | "L2" | "L3";
  tags: string[]; // 业绩/公告/异动/资金/行业（规则派生，可多挂）
  rank_score?: number; // sort=relevance 时返回
  rank_reasons?: string[];
  rank_factors?: RankFactors;
}

export type EventSort = "relevance" | "time" | "impact";

export async function getImpactEvents(
  includeL3 = false,
  limit = 100,
  sort: EventSort = "relevance",
): Promise<{
  count: number;
  countsAll: Record<string, number>;
  fourCounts: Record<string, number>;
  tagCounts: Record<string, number>;
  items: ImpactEvent[];
}> {
  const r = await getJson<{
    count: number;
    counts_all: Record<string, number>;
    four_counts: Record<string, number>;
    tag_counts: Record<string, number>;
    items: ImpactEvent[];
  }>(`/api/events/impact?include_l3=${includeL3}&limit=${limit}&sort=${sort}`, 30_000);
  return {
    count: r.data.count,
    countsAll: r.data.counts_all,
    fourCounts: r.data.four_counts,
    tagCounts: r.data.tag_counts,
    items: r.data.items,
  };
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

/** 新建空分组（重名 409 → 抛错给调用方提示）。 */
export async function createWatchlistGroup(name: string): Promise<void> {
  await sendJson("/api/watchlist/groups", "POST", { name });
}

/** 重命名分组（后端级联成员）。 */
export async function renameWatchlistGroup(name: string, newName: string): Promise<void> {
  await sendJson(`/api/watchlist/groups/${encodeURIComponent(name)}`, "PUT", { new_name: newName });
}

/** 删除分组（后端把成员回落「默认」）。 */
export async function deleteWatchlistGroup(name: string): Promise<void> {
  await sendJson(`/api/watchlist/groups/${encodeURIComponent(name)}`, "DELETE");
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
  /** name → 是否已配置到可真正发出（如 feishu 是否配了 webhook）；缺省视为已配置 */
  configured?: Record<string, boolean>;
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

/** ---------------------------------------------------------------- 站内通知中心（2026-09-07） */

/** 通知条目（后端 /api/notifications 三源合并：个股机会/每日精选/新闻评分过滤）。 */
export interface NotificationItem {
  id: string;
  /** opportunity=个股机会（watcher 确认/证伪）| daily_picks=每日精选 | news=消息面/新闻/政策 | risk=策略风险（信号健康度预警） */
  category: "opportunity" | "daily_picks" | "news" | "risk";
  /** 展示标签：确认/证伪/跟踪/健康预警/每日精选/国家政策/国际时事/市场热点/原材料涨价 */
  label: string;
  /** 盘前/盘中/盘后（北京时间墙钟划分） */
  session: "pre_open" | "intraday" | "after_close";
  ts: string | null;
  title: string;
  body: string;
  symbol: string | null;
  url: string | null;
  /** 新闻评分为 0-100（与时事新闻板块 events ranking 同源）；其余为 null */
  score: number | null;
}

export interface NotificationsPayload {
  items: NotificationItem[];
  count: number;
  generated_at: string;
  news_min_score: number;
  /** 单源失败显式透出（降级可见），全部成功为 null */
  errors: Record<string, string> | null;
}

export async function getNotifications(): Promise<NotificationsPayload> {
  return (await getJson<NotificationsPayload>("/api/notifications")).data;
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



// ---- 复盘方法论闭环（研究页复盘 tab，A2）：报告 / 有效性统计 ----

export interface ReviewReportSummary {
  review_id: string;
  trade_date: string;
  methodology_version: string;
  model_actual: string;
  model_degraded: boolean;
  summary: string;
  gap_count: number;
  action_item_count: number;
  generated_at: string;
}

export interface ReviewReportDetail {
  review_id: string;
  trade_date: string;
  generated_at: string;
  methodology_version: string;
  model: { requested?: string; actual?: string; degraded?: boolean; reason?: string };
  dimensions: { key: string; title: string; status: string; findings: string[]; judgements: string[]; gaps: string[] }[];
  action_items: {
    id: string;
    title: string;
    category: string;
    priority: string;
    expected_impact: string;
    evidence: string;
    target: string;
    proposed_change: string;
    /** pending | confirmed | applied | rejected | reverted */
    status: string;
    resolution_note: string;
  }[];
  meta_insights: { dimension: string; observation: string; effectiveness: string; evidence: string; suggestion: string }[];
  summary: string;
}

export interface ReviewEffectiveness {
  by_category: Record<
    string,
    { total: number; confirmed: number; reverted: number; rejected: number; pending: number; adoption_rate: number; revert_rate: number | null }
  >;
}

export async function getReviewReports(): Promise<ReviewReportSummary[]> {
  return (await getJson<ReviewReportSummary[]>("/api/review/reports")).data;
}

export async function getReviewReport(tradeDate: string): Promise<ReviewReportDetail> {
  return (await getJson<ReviewReportDetail>(`/api/review/reports/${tradeDate}`)).data;
}

export async function getReviewEffectiveness(): Promise<ReviewEffectiveness> {
  return (await getJson<ReviewEffectiveness>("/api/review/effectiveness")).data;
}

/** 改进项处置状态。pending 表示"撤销处置"。 */
export type ActionItemStatus = "pending" | "confirmed" | "applied" | "rejected" | "reverted";

export interface ReviewActionItem {
  id: number;
  review_id: string;
  trade_date: string;
  title: string;
  category: string;
  priority: string;
  target: string;
  proposed_change: string;
  status: ActionItemStatus;
  resolution_note: string;
  resolved_at: string | null;
}

/**
 * 处置寻址 + id 漂移守卫三元组。
 *
 * id 是 SQLite rowid 别名且无 AUTOINCREMENT：报告重跑删除重建后 id 会被
 * 复用甚至跨日串号（2026-09-01 实测 111→72）。仅凭 id 寻址，重跑前打开的
 * 页面点处置会把结论挂到恰好复用该 id 的不相干改进项上（静默错配）。
 * 后端用 (trade_date, category, title) 校验 id 仍指向同一条内容，不符返回 409。
 */
export interface ActionItemRef {
  id: string;
  trade_date: string;
  category: string;
  title: string;
}

/**
 * 处置单条改进项（PDCA 闭环的落点）。
 *
 * 改进项只能产出、无法消费时，"方法论自我迭代"是空转的——2026-09-01 核查时
 * 107 条改进项全部 pending、采纳率 0%，连带让「采纳率<20% → 该维度疑似产出
 * 噪音」的演进建议永远触发且毫无意义。
 *
 * rejected / reverted 后端强制要求 note：没有理由的处置后期无法归因。
 * 守卫三元组不匹配时抛 ApiError(409)，message 即"请刷新后重试"的人读提示。
 */
export async function updateActionItemStatus(
  item: ActionItemRef,
  status: ActionItemStatus,
  note = "",
): Promise<ReviewActionItem> {
  return (await sendJson<ReviewActionItem>(`/api/review/action-items/${item.id}`, "PATCH", {
    status,
    note,
    trade_date: item.trade_date,
    category: item.category,
    title: item.title,
  })).data;
}

// ---------------------------------------------------------------- 盘中跟踪（选股 2.0 批次 B/C）

/** 盘前简报单方向（含盘后复盘回填的 review）。 */
export interface BriefDirectionReview {
  outcome: string;
  failure_class: string | null;
  confirmed: boolean;
  falsified: boolean;
  falsify_keys: string[];
  closing_met: number;
  closing_total: number;
  closing_unknown: number;
  actual_pct: number | null;
  actual_limit_up: number | null;
  actual_max_boards: number | null;
  actual_leader: string | null;
  missing_ratio: number | null;
  note: string;
  source: string;
}

export interface BriefDirection {
  direction: string;
  score: number;
  basis: string;
  logic: string;
  defensive: boolean;
  stage: string | null;
  entry_mode: string;
  entry_basis: string;
  pool: { symbol: string; name: string; boards: number; role: string }[];
  trigger_conditions: string[];
  falsify_conditions: string[];
  review?: BriefDirectionReview | null;
}

export interface BriefAlertReturns {
  ref_date: string;
  ref_price: number | null;
  t1_date: string | null;
  t1_return: number | null;
  t3_date: string | null;
  t3_return: number | null;
  complete: boolean;
}

export interface BriefAlert {
  key: string;
  kind: string;
  direction: string;
  symbol: string;
  name: string;
  text: string;
  at: string;
  meta: { returns?: BriefAlertReturns; [k: string]: unknown };
}

export interface MorningBrief {
  brief_date: string;
  generated_at: string;
  trigger: string;
  engine_version: string;
  env: {
    phase: string | null;
    promo_percentile: number | null;
    bands_source: string | null;
    pool_date: string | null;
    is_trading_day: boolean | null;
  };
  missing: string[];
  directions: BriefDirection[];
  alerts: BriefAlert[];
  review?: {
    reviewed_at: string;
    trigger: string;
    pool_date: string | null;
    tracker_source: boolean;
    outcomes: Record<string, string>;
    missing: string[];
  } | null;
}

export interface WatcherState {
  active: boolean;
  enabled?: boolean;
  brief_exists?: boolean;
  note?: string;
  started_at?: string;
  beat_count?: number;
  trackers?: {
    direction: string;
    beats: number;
    missing_beats: number;
    peak_pct: number | null;
    below_zero_beats: number;
    confirmed: boolean;
    falsified: boolean;
    falsify_triggers: { key: string; detail: string }[];
    alerted_symbols: string[];
    last_confirm: { strength?: number } | null;
  }[];
}

export interface IntradayReviewStats {
  daily: {
    date: string;
    directions: number;
    reviewed: number;
    fermented: number;
    half: number;
    falsified: number;
    flat: number;
    alerts: number;
  }[];
  directions: {
    total: number;
    outcomes: Record<string, number>;
    failures: Record<string, number>;
  };
  alert_t1: { n: number; win_rate: number | null; avg_win: number | null; avg_loss: number | null; profit_loss_ratio: number | null; avg_return: number | null };
  alert_t3: { n: number; win_rate: number | null; avg_win: number | null; avg_loss: number | null; profit_loss_ratio: number | null; avg_return: number | null };
  alerts: { date: string; direction: string; symbol: string; name: string; t1_return: number | null; t3_return: number | null }[];
  sample_note: string;
}

export async function getMorningBriefToday(): Promise<MorningBrief> {
  return (await getJson<MorningBrief>("/api/picks/morning-brief/today")).data;
}

/** 生成/刷新今日简报（覆盖当日文件，盘中已产生的 alerts 会丢）。 */
export async function generateMorningBrief(): Promise<MorningBrief> {
  return (await sendJson<MorningBrief>("/api/picks/morning-brief/generate", "POST", {}, 60_000)).data;
}

export async function getWatcherState(): Promise<WatcherState> {
  return (await getJson<WatcherState>("/api/picks/watcher/state")).data;
}

/** 手动推进一拍（与盘中 watcher_loop 同代码路径；取证/调试用）。 */
export async function runWatcherBeat(): Promise<{ alerts: { key: string; dispatched: boolean }[] }> {
  return (
    await sendJson<{ alerts: { key: string; dispatched: boolean }[] }>(
      "/api/picks/watcher/beat", "POST", {}, 30_000,
    )
  ).data;
}

export async function getIntradayReview(limit = 30): Promise<IntradayReviewStats> {
  return (await getJson<IntradayReviewStats>(`/api/picks/intraday-review?limit=${limit}`)).data;
}

/** 信号健康度（每日精选命中记录的滚动胜率 + CUSUM 下漂，方向 1×5 反馈环）。
 *  status 三态纪律：insufficient = 样本不足显式不判 ok，绝不显示 0%；
 *  win_rate 为 0-1 小数（后端 round(good/total,4)），展示层 ×100。 */
export interface SignalHealthPayload {
  status: "ok" | "warning" | "drift" | "insufficient" | "error";
  reason?: string;
  window: {
    groups: number;
    total_picks: number;
    win_rate: number | null;
    good: number;
    bad: number;
    flat: number;
    mean_excess: number | null;
  } | null;
  cusum: { mu0: number; s_max: number; threshold: number; delta: number; drift: boolean } | null;
  history: { date: string; phase: string | null; n: number; good: number; bad: number; flat: number; mean_excess: number | null }[];
  counts: { groups: number };
}

/** 信号健康度（GET /api/picks/signal-health；猎场批次②统计条转正接入）。 */
export async function getSignalHealth(): Promise<SignalHealthPayload> {
  return (await getJson<SignalHealthPayload>("/api/picks/signal-health")).data;
}

/** 盘中机会：判定类三态——unknown 表示判不出（数据缺失），不是"低"。 */
export interface OpportunityJudgement {
  level: "高" | "中" | "低" | "unknown";
  basis: string;
}

export interface OpportunityStock {
  symbol: string;
  name: string | null;
  role: string | null;
  boards: number | null;
  change_pct: number | null;
  reason: string | null;
  hot_rank: number | null;
  distinctiveness: OpportunityJudgement;
  certainty: OpportunityJudgement;
}

export interface OpportunityTheme {
  theme: string;
  stage: string | null;
  stage_basis: string[];
  strength_score: number | null;
  strength_tier: string | null;
  tier_basis: string | null;
  formation: string | null;
  health_note: string | null;
  risks: string[];
  max_boards: number | null;
  limit_up_count: number | null;
  has_succession: boolean | null;
  stocks: OpportunityStock[];
}

export interface IntradayOpportunities {
  trade_date: string | null;
  themes: OpportunityTheme[];
  summary: { limit_up_total: number | null; market_max_boards: number | null; top_theme: string | null };
  hot_available: boolean;
  caveats: string[];
}

/** 盘中机会视图：先题材（强度/阶段/依据）后题材内个股（辨识度/确定性）。 */
export async function getIntradayOpportunities(): Promise<IntradayOpportunities> {
  return (await getJson<IntradayOpportunities>(`/api/picks/intraday-opportunities`)).data;
}

/** 盘中跟踪「最推荐标的」（opportunities 多维筛选切片，tier 1~3 = 机会度梯队）。 */
export interface IntradayTopStock {
  symbol: string;
  name: string | null;
  role: string | null;
  boards: number | null;
  change_pct: number | null;
  theme: string | null;
  stage: string | null;
  strength_tier: string | null;
  distinctiveness: { level: string; basis: string } | null;
  certainty: { level: string; basis: string } | null;
  reason: string | null;
  tier: number;
  pick_basis: string;
}

export interface IntradayTopPayload {
  trade_date: string | null;
  items: IntradayTopStock[];
  total_candidates: number;
  criteria: string;
  hot_available: boolean;
  caveats: string[];
}

/** 盘中跟踪最推荐标的（工作台动态分组数据源；确定性优先、辨识度次之）。 */
export async function getIntradayTop(): Promise<IntradayTopPayload> {
  return (await getJson<IntradayTopPayload>(`/api/picks/intraday-top?limit=8`, 15_000)).data;
}

/** 手动执行当日方向对照 + 提醒收益回填（15:35 调度的同代码路径）。 */
export async function runIntradayReview(): Promise<{ brief_date: string; directions: { direction: string; outcome: string }[] }> {
  return (
    await sendJson<{ brief_date: string; directions: { direction: string; outcome: string }[] }>(
      "/api/picks/intraday-review/run", "POST", {}, 60_000,
    )
  ).data;
}

// ==================================================================== 资金流向（市场页 fund tab，2026-09-04）

/** 五档资金净额（亿元）。main=主力（超大+大单），口径为东财大盘资金流。 */
export interface FlowTier {
  main: number | null;
  super_: number | null;
  big: number | null;
  mid: number | null;
  small: number | null;
}

export interface TurnoverToday {
  today_amount_yi: number | null;
  prev_date: string | null;
  prev_same_time_yi: number | null;
  prev_total_yi: number | null;
  diff_yi: number | null;
  est_full_day_yi: number | null;
  est_method: "closed" | "prev-dist" | "linear" | null;
  today_series: { t: string; cum: number }[] | null;
  prev_series: { t: string; cum: number }[] | null;
  sina_available: boolean;
  updated_at: string;
  degraded: string[];
}

export interface FundFlowRealtime {
  available: boolean;
  reason?: string;
  as_of: string | null;
  items?: { total: FlowTier; sh: FlowTier; sz: FlowTier };
  degraded: string[];
}

export type FlowIntradayPoint = { t: string } & FlowTier;

export interface FundFlowIntraday {
  items: FlowIntradayPoint[];
  updated_at: string;
  degraded: string[];
}

export interface FlowHistoryDay extends FlowTier {
  date: string;
  close_pct: number | null;
}

export interface FundFlowHistory {
  items: FlowHistoryDay[];
  updated_at: string | null;
  refreshed?: boolean;
  degraded: string[];
}

export interface TurnoverHistoryDay {
  date: string;
  total_yi: number | null;
  prev_total_yi: number | null;
  diff_yi: number | null;
}

export interface TurnoverDayCompare {
  available: boolean;
  reason?: string;
  date: string;
  prev_date: string | null;
  total_yi: number | null;
  prev_total_yi: number | null;
  diff_yi: number | null;
  series: { t: string; cum: number }[] | null;
  prev_series: { t: string; cum: number }[] | null;
  degraded: string[];
}

export async function getTurnoverToday(): Promise<TurnoverToday> {
  return (await getJson<TurnoverToday>("/api/market/turnover", 40_000)).data;
}

export async function getTurnoverDay(date: string): Promise<TurnoverDayCompare> {
  return (await getJson<TurnoverDayCompare>(`/api/market/turnover/day?date=${date}`, 40_000)).data;
}

export async function getTurnoverHistory(days = 10): Promise<{ items: TurnoverHistoryDay[]; degraded: string[] }> {
  return (await getJson<{ items: TurnoverHistoryDay[]; degraded: string[] }>(
    `/api/market/turnover/history?days=${days}`, 40_000,
  )).data;
}

export async function getFundFlowRealtime(): Promise<FundFlowRealtime> {
  return (await getJson<FundFlowRealtime>("/api/market/fund-flow", 50_000)).data;
}

export async function getFundFlowIntraday(): Promise<FundFlowIntraday> {
  return (await getJson<FundFlowIntraday>("/api/market/fund-flow/intraday", 50_000)).data;
}

export async function getFundFlowHistory(days = 20): Promise<FundFlowHistory> {
  return (await getJson<FundFlowHistory>(`/api/market/fund-flow/history?days=${days}`, 50_000)).data;
}

// ---------- 板块资金流（L2 唯一实现 /api/market/board-fund-flow*，docs/fund-flow-redesign.md） ----------

export type BoardFlowKind = "concept" | "industry";
export type BoardFlowRange = "intraday" | "5d" | "10d" | "20d";

export interface BoardFlowRow {
  board_code: string;
  name: string;
  kind: string;
  change_pct: number | null;
  /** 今日主力净额（亿元，东财官方板块口径，不与个股新浪口径混算） */
  main_net_yi: number | null;
  /** 主力净额/成交额×100（f184 官方字段） */
  main_net_ratio: number | null;
  super_net_yi: number | null;
  main_net_5d_yi: number | null;
  main_net_10d_yi: number | null;
  /** 仅 20d 视图返回 */
  main_net_20d_yi?: number | null;
  leader_name: string | null;
  leader_symbol: string | null;
  /** 连续净流入天数（null=未沉淀；0=今日净流出，区别于 null） */
  streak: number | null;
  rank: number;
  /** 正=排名上升（vs 昨日落盘榜位；null=无基线） */
  rank_delta: number | null;
}

export interface BoardFundFlowPayload {
  available: boolean;
  reason?: string;
  kind: BoardFlowKind;
  range: BoardFlowRange;
  rows: BoardFlowRow[];
  total_boards?: number;
  /** 20d 视图：本地沉淀板块数 */
  coverage?: number | null;
  updated_at?: string;
  rank_basis_date?: string | null;
  degraded: string[];
}

export interface BoardFlowMinutePayload {
  available: boolean;
  reason?: string;
  board_code: string;
  items: FlowIntradayPoint[];
  /** 日度主力净额序列（仅收盘快照沉淀过的 Top 板块有，零外呼） */
  daily_bars: { date: string; main_yi: number | null; close_pct: number | null }[];
  delayed?: boolean;
  updated_at?: string;
  degraded: string[];
}

export interface BoardFlowMember {
  symbol: string;
  name: string;
  price: number | null;
  change_pct: number | null;
  main_net_yi: number | null;
  super_net_yi: number | null;
  big_net_yi: number | null;
  main_net_ratio: number | null;
}

export interface BoardFlowMembersPayload {
  available: boolean;
  reason?: string;
  board_code: string;
  rows: BoardFlowMember[];
  delayed?: boolean;
  updated_at?: string;
  degraded: string[];
}

export async function getBoardFundFlow(
  kind: BoardFlowKind = "concept",
  range: BoardFlowRange = "intraday",
): Promise<BoardFundFlowPayload> {
  return (await getJson<BoardFundFlowPayload>(
    `/api/market/board-fund-flow?kind=${kind}&range=${range}`, 30_000,
  )).data;
}

export async function getBoardFlowMinute(boardCode: string): Promise<BoardFlowMinutePayload> {
  return (await getJson<BoardFlowMinutePayload>(
    `/api/market/board-fund-flow/${boardCode}/minute`, 30_000,
  )).data;
}

export async function getBoardFlowMembers(boardCode: string): Promise<BoardFlowMembersPayload> {
  return (await getJson<BoardFlowMembersPayload>(
    `/api/market/board-fund-flow/${boardCode}/members`, 30_000,
  )).data;
}

// ---------- 资讯正文（弹窗展示，/api/news/content） ----------

/** 正文有序块：表格/图片按原文档顺序内嵌渲染（2026-09-04 弹窗排版升级） */
export type ArticleBlock =
  | { type: "p"; text: string }
  | { type: "table"; rows: string[][]; header: boolean; truncated_rows?: boolean }
  | { type: "img"; src: string };

export interface ArticleContent {
  kind: "news" | "notice";
  title: string | null;
  source_label: string | null;
  published: string | null;
  /** 兼容字段：纯文本段落（= blocks 中 type==="p" 的子集） */
  paragraphs: string[];
  blocks: ArticleBlock[];
  truncated: boolean;
  cached?: boolean;
  url: string;
}

export async function getNewsContent(url: string): Promise<ArticleContent> {
  return (await getJson<ArticleContent>(`/api/news/content?url=${encodeURIComponent(url)}`, 20_000)).data;
}

// ============================================================================
// AI 控制台（大脑 + 执行层，docs/ai-agent-console-plan.md）
// ============================================================================

/** 任务状态机：queued → running → succeeded/failed/canceled；needs_confirm 为 L1/L2 预览态（P1 接入） */
export type AgentTaskStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "canceled"
  | "needs_confirm";

/** 任务步骤轨迹（可追溯三件套第一件：做过什么、依据什么、花了多久） */
export interface AgentTaskStep {
  index: number;
  name: string;
  input_summary: string;
  output_summary: string;
  duration_ms: number;
  ok: boolean;
  llm?: { model: string; prompt_hash: string; enhanced: boolean };
}

export interface AgentTask {
  id: string;
  type: string;
  status: AgentTaskStatus;
  params: Record<string, unknown>;
  steps: AgentTaskStep[];
  result_ref: { kind: string; id: string } | null;
  error: { code: string; message: string; retryable: boolean } | null;
  risk_level: "L0" | "L1" | "L2";
  created_by: string;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface AgentTaskType {
  type: string;
  label: string;
  risk: "L0" | "L1" | "L2";
  desc: string;
}

export interface AgentAuditEntry {
  id: number;
  actor: string;
  action: string;
  target: string;
  before: unknown;
  after: unknown;
  task_id: string | null;
  rollback_ref: string | null;
  at: string | null;
}

export async function getAgentTaskTypes(): Promise<AgentTaskType[]> {
  return (await getJson<AgentTaskType[]>("/api/agent/task-types")).data;
}

export async function getAgentTasks(type?: string): Promise<AgentTask[]> {
  const q = type ? `?type=${encodeURIComponent(type)}` : "";
  return (await getJson<AgentTask[]>(`/api/agent/tasks${q}`)).data;
}

export async function getAgentTask(id: string): Promise<AgentTask> {
  return (await getJson<AgentTask>(`/api/agent/tasks/${id}`)).data;
}

export async function createAgentTask(type: string, params: Record<string, unknown> = {}): Promise<AgentTask> {
  return (await sendJson<AgentTask>("/api/agent/tasks", "POST", { type, params })).data;
}

export async function cancelAgentTask(id: string): Promise<AgentTask> {
  return (await sendJson<AgentTask>(`/api/agent/tasks/${id}/cancel`, "POST")).data;
}

/** 悬浮球提醒气泡（AI 判读为 notify 且未确认的） */
export interface AgentBubble {
  id: number;
  event_id: number;
  verdict: string;
  reason: string;
  /** llm=AI 判读 / rules=确定性去重 / llm_fallback=LLM 不可用按规则提醒（界面须标注） */
  model: string;
  acked: boolean;
  symbol: string;
  trigger_value: number | null;
  threshold: number | null;
  created_at: string | null;
}

export async function getAgentBubbles(limit = 5): Promise<AgentBubble[]> {
  return (await getJson<AgentBubble[]>(`/api/agent/triage/pending?limit=${limit}`)).data;
}

export async function ackAgentTriage(id: number): Promise<boolean> {
  return (await sendJson<{ ok: boolean }>(`/api/agent/triage/${id}/ack`, "POST")).data.ok;
}

export async function getAgentAudit(taskId?: string): Promise<AgentAuditEntry[]> {
  const q = taskId ? `?task_id=${encodeURIComponent(taskId)}` : "";
  return (await getJson<AgentAuditEntry[]>(`/api/agent/audit${q}`)).data;
}

// ---- 参数配置（P1-B：变更单 + 回滚 + 证据门槛）----

export interface AgentParamInfo {
  key: string;
  label: string;
  desc: string;
  risk: string;
  current: string;
  default: string;
}

export interface AgentParamChange {
  id: number;
  key: string;
  before: unknown;
  after: unknown;
  source_type: string;
  source_id: string;
  evidence: Record<string, unknown> | null;
  status: "draft" | "applied" | "rolled_back";
  created_at: string | null;
  applied_at: string | null;
  rolled_back_at: string | null;
  task_id: string | null;
}

export async function getAgentParams(): Promise<AgentParamInfo[]> {
  return (await getJson<AgentParamInfo[]>("/api/agent/params")).data;
}

export async function getAgentParamChanges(key?: string): Promise<AgentParamChange[]> {
  const q = key ? `?key=${encodeURIComponent(key)}` : "";
  return (await getJson<AgentParamChange[]>(`/api/agent/params/changes${q}`)).data;
}

export async function createAgentParamChange(
  key: string,
  after: string,
  opts: { source_type?: string; source_id?: string; evidence?: Record<string, unknown> } = {},
): Promise<AgentParamChange> {
  return (await sendJson<AgentParamChange>("/api/agent/params/change", "POST", {
    key, after, ...opts,
  })).data;
}

export async function applyAgentParamChange(changeId: number): Promise<AgentParamChange> {
  return (await sendJson<AgentParamChange>(`/api/agent/params/changes/${changeId}/apply`, "POST")).data;
}

export async function rollbackAgentParamChange(changeId: number): Promise<AgentParamChange> {
  return (await sendJson<AgentParamChange>(`/api/agent/params/changes/${changeId}/rollback`, "POST")).data;
}

// ---- AI 大脑：每日进化议程（v2）----

export interface AgentAgendaItem {
  class: "A" | "B" | "C";
  finding: string;
  evidence: Record<string, unknown>;
  action: string;
  expected_effect: string;
  verification: string;
  priority: number;
  param?: { key: string; after: unknown };
  summary?: string;
  status: "pending" | "executed" | "deferred" | "rejected" | "failed";
  result: string;
}

export interface AgentAgenda {
  id: number;
  date: string;
  status: "generating" | "ready" | "executed" | "failed" | "skipped";
  inputs: Record<string, unknown>;
  items: AgentAgendaItem[];
  budget: Record<string, unknown>;
  error: { code: string; message: string } | null;
  created_at: string | null;
  finished_at: string | null;
}

export async function getAgentAgenda(date?: string): Promise<AgentAgenda | null> {
  const q = date ? `?date=${encodeURIComponent(date)}` : "";
  return (await getJson<AgentAgenda | null>(`/api/agent/agenda${q}`)).data;
}

export async function getAgentAgendas(limit = 14): Promise<AgentAgenda[]> {
  return (await getJson<AgentAgenda[]>(`/api/agent/agendas?limit=${limit}`)).data;
}

export async function runAgentAgenda(): Promise<AgentAgenda> {
  return (await sendJson<AgentAgenda>("/api/agent/agenda/run", "POST", {}, 180_000)).data;
}

// ---- 实验记录本（后置验证：A 类变更的 30 日窗口与自动回滚）----

export interface AgentExperiment {
  id: number;
  change_id: number;
  param_key: string;
  hypothesis: string;
  baseline: { status?: string; win_rate?: number | null; mean_excess?: number | null; taken_at?: string };
  verification_date: string | null;
  status: "running" | "concluded" | "rolled_back" | "concluded_insufficient";
  result: { conclusion: string; win_rate_delta?: number; current?: Record<string, unknown>; rollback?: unknown } | null;
  extensions: number;
  created_at: string | null;
  concluded_at: string | null;
}

export async function getAgentExperiments(): Promise<AgentExperiment[]> {
  return (await getJson<AgentExperiment[]>("/api/agent/experiments")).data;
}
