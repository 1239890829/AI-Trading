/**
 * 行情与个股：指数概览、报价、盘口、成交、K 线/分时、涨停跌停、龙虎榜、资金流、财务公告
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import type {
  Kline,
  LimitDownRecord,
  LimitUpRecord,
  LongHuRecord,
  OrderBook,
  Quote,
  SymbolSearchItem,
  Trade,
  TradingStatusInfo,
} from "@/types/market";

import type { Freshness } from "./client";
import { getJson, getJsonArray } from "./internal";

export async function getMarketOverview(): Promise<{
  indices: Quote[];
  total_amount: number | null;
  total_amount_freshness: Freshness | null;
}> {
  const body = await getJson<{
    indices: Quote[];
    total_amount: number | null;
    total_amount_freshness: Freshness | null;
  }>("/api/market/overview", 10_000);
  return body.data;
}

export async function getQuote(symbol: string, source?: string): Promise<Quote> {
  const qs = source ? `?source=${source}` : "";
  return (await getJson<Quote>(`/api/quotes/${symbol}${qs}`, 10_000)).data;
}

export async function getQuotes(symbols?: string[]): Promise<Quote[]> {
  const qs = symbols && symbols.length > 0 ? `?symbols=${symbols.join(",")}` : "";
  return getJsonArray<Quote>(`/api/quotes${qs}`);
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

export async function getOrderBook(symbol: string): Promise<OrderBook> {
  return (await getJson<OrderBook>(`/api/order-book/${symbol}`)).data;
}

export async function getTrades(symbol: string, limit = 50): Promise<Trade[]> {
  return getJsonArray<Trade>(`/api/trades/${symbol}?limit=${limit}`);
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

export async function getCapitalFlow<T = unknown>(symbol: string, days = 30): Promise<T> {
  return (await getJson<T>(`/api/capital-flow/${symbol}?days=${days}`, 15_000)).data;
}

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

export async function getAnnouncements<T = InfoItem>(symbol: string, limit = 8): Promise<T[]> {
  return (await getJson<{ symbol: string; items: T[] }>(`/api/announcements/${symbol}?limit=${limit}`, 15_000)).data.items;
}

export interface EntryChecklistLayer {
  phase?: string | null;
  stage?: string | null;
  blocked: boolean;
  note: string;
}

export interface EntryChecklist {
  symbol: string;
  name?: string | null;
  found: boolean;
  trade_date?: string | null;
  theme?: string | null;
  theme_stage_basis?: string | null;
  health_note?: string | null;
  risks?: string[];
  role?: string | null;
  boards?: number | null;
  dragon_grade?: string | null;
  dragon?: { score?: number; grade?: string; basis?: string[]; missing?: string[] };
  market_phase?: string | null;
  market_layer: EntryChecklistLayer;
  theme_layer: EntryChecklistLayer;
  conditions: string[];
  avoid: string[];
  invalidation: string[];
  timing: string;
  /** 未取到的输入（三态：缺失显式列出，不做中性假设）。 */
  missing: string[];
  note: string;
}

export async function getEntryChecklist(symbol: string, dateStr?: string): Promise<EntryChecklist> {
  const qs = new URLSearchParams({ symbol });
  if (dateStr) qs.set("date", dateStr);
  return (await getJson<EntryChecklist>(`/api/market/entry-checklist?${qs}`, 20_000)).data;
}

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

export interface BoardFundRow {
  board_code: string | null;
  name: string;
  kind: string | null;
  change_pct: number | null;
  main_net_yi: number | null;
  main_net_ratio: number | null;
  streak: number | null;
}

export interface SymbolBoardFund extends Omit<BoardFundRow, "name"> {
  board_name: string;
  /** industry = 东财行业三级 L2（主口径）；concept = 无行业段时的回落，已如实标注 */
  level: "industry" | "concept" | null;
}

export async function getBoardFundBySymbols(symbols: string[]): Promise<Record<string, SymbolBoardFund>> {
  if (symbols.length === 0) return {};
  const payload = await getJson<{ boards: Record<string, SymbolBoardFund> }>(
    `/api/market/board-fund/by-symbols?symbols=${symbols.join(",")}`,
    25_000,
  );
  return payload.data.boards;
}

export interface Breadth {
  up: number; down: number; flat: number; limit_up: number; limit_down: number;
  total: number; total_amount: number; suspended: number;
}

export async function getBreadth(): Promise<Breadth> {
  return (await getJson<{ breadth: Breadth }>("/api/market/breadth", 15_000)).data.breadth;
}

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

export async function getSparklines(
  symbols: string[],
  days = 30,
  period: "daily" | "minute" = "daily",
): Promise<SparklinePayload> {
  return (
    await getJson<SparklinePayload>(`/api/sparkline?symbols=${symbols.join(",")}&days=${days}&period=${period}`, 30_000)
  ).data;
}

export interface SkyrocketRow {
  rank: number;
  symbol: string;
  name: string | null;
  heat: number | null;
  rank_change: number | null;
  ts: string | null;
  source: string;
}

export async function getSkyrocket(period: "day" | "hour" = "day"): Promise<SkyrocketRow[]> {
  return (await getJson<{ rows: SkyrocketRow[]; period: string }>(
    `/api/market/heat/skyrocket?period=${period}`,
    15_000,
  )).data.rows;
}

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

export async function getLonghuThemeTrail(days = 5): Promise<LonghuTrailPayload> {
  return (await getJson<LonghuTrailPayload>(
    `/api/market/longhu/theme-trail?days=${days}`,
    20_000,
  )).data;
}

export async function searchSymbols(q: string): Promise<SymbolSearchItem[]> {
  return getJsonArray<SymbolSearchItem>(`/api/search?q=${encodeURIComponent(q)}`);
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

export interface AuctionBenchmarkItem {
  symbol: string;
  name: string | null;
  /** 竞价涨幅 %；0 是真实"平开"，与 null（缺失）语义不同，不可互相替代 */
  auction_pct: number | null;
  tags: string[];
}

export async function getAuctionBenchmark(date?: string): Promise<AuctionBenchmarkItem[]> {
  const qs = date ? `?date=${encodeURIComponent(date)}` : "";
  return getJsonArray<AuctionBenchmarkItem>(`/api/auction-benchmark${qs}`, 15_000);
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

export async function getSentimentHistory(days = 10): Promise<SentimentHistoryPayload> {
  return (await getJson<SentimentHistoryPayload>(`/api/market/sentiment-history?days=${days}`, 30_000)).data;
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

export interface MinuteIndicatorHit {
  key: string;
  name: string;
  weight: number;
  /** -1 低吸方向 / +1 高抛方向 */
  direction: number;
  trigger_value: number;
  threshold: number;
  evidence: string;
}

export interface MinuteSignalItem {
  /** 触发时刻（= 确认完成的那根 bar，UTC ISO） */
  ts: string;
  signal_price: number;
  bias: string;
  score: number;
  confidence: string;
  triggered: MinuteIndicatorHit[];
  invalidate_condition: string;
  basis?: string;
}

export interface MinuteSignalsPayload {
  symbol: string;
  signals: MinuteSignalItem[];
  observed: number;
  degraded: string[];
  /** 本次请求新落库的条数（幂等：重复请求恒为 0） */
  recorded: number;
  basis: Record<string, unknown>;
}

export interface MinuteErrorAttribution {
  primary_cause?: string | null;
  primary_name?: string;
  score_without?: number | null;
  pivotal?: boolean;
  counter_evidence?: string;
}

export interface MinuteDecisionItem {
  decision_id: string;
  symbol: string;
  trade_date: string;
  trigger_ts: string;
  signal_price: number;
  bias: string;
  score: number;
  confidence: string;
  triggered: MinuteIndicatorHit[];
  invalidate_condition: string;
  executed: boolean;
  executed_price: number | null;
  realized_spread_pct: number | null;
  best_price: number | null;
  worst_price: number | null;
  optimal_spread_pct: number | null;
  /** null = 未结算（窗口未走完）；expired = 数据不足不判定 */
  outcome: string | null;
  error_attribution: MinuteErrorAttribution | null;
}

export interface MinuteDecisionsPayload {
  items: MinuteDecisionItem[];
  settled: number;
  outcomes: Record<string, number>;
  open_count: number;
  note: string;
}

export async function getMinuteSignals(symbol: string): Promise<MinuteSignalsPayload> {
  return (await getJson<MinuteSignalsPayload>(`/api/market/minute-signals/${symbol}`, 20_000)).data;
}

export async function getMinuteDecisions(symbol: string, limit = 30): Promise<MinuteDecisionsPayload> {
  const q = new URLSearchParams({ symbol, limit: String(limit) });
  return (await getJson<MinuteDecisionsPayload>(`/api/market/minute-decisions?${q.toString()}`)).data;
}
