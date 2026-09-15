/**
 * 选股链路：每日精选、盘中跟踪、潜伏池、接力排序、观察台账
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import { getJson, getJsonArray, sendJson } from "./internal";

export interface StopLossRef {
  pct: number;
  price: number;
  basis: string;
}

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

export interface PickRegime {
  regime: string;
  weights: Record<string, number>;
  basis: string;
  calendar_window: boolean;
  earnings_ratio: number | null;
}

export interface StyleRouting {
  phase: string | null;
  style: string;
  label: string;
  offsets: Record<string, number>;
  basis: string;
  routed: boolean;
  /** 相位来源：live=读取时刻实时重算；unavailable=实时不可用退回生成时刻快照；unknown_phase=相位未识别 */
  phase_source?: "live" | "unavailable" | "unknown_phase" | string;
  phase_note?: string;
}

export interface StandAsideGate {
  stand_aside: boolean;
  level: "none" | "mild" | "strong";
  reasons: string[];
  advice: string;
  disclaimer?: string;
  /** 触发时的情绪相位原样带出（下游分档判据用；level 是理由条数的计数产物，不足以表达信号性质） */
  phase?: string | null;
  /** 本次是否**撤除买入区间**（相位级信号/多信号叠加 → true；单条量化擦线 → false）。
   *  后端算好带出，前端只读不算（重算 = 两份口径必然漂移）。旧行无此字段时按 undefined 处理。 */
  strip_buy_range?: boolean;
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
    /** 容量上限（不是"每天必须凑满"的目标数） */
    max_picks?: number;
    /** 换股门槛（生效值，可被控制台参数白名单覆盖） */
    replace_threshold?: number;
    /** 每日换股上限（生效值，同上） */
    max_swaps_per_day?: number;
    /** 入选门槛（综合分下限）：低于此分不入选，名单长度由质量决定 */
    min_pick_score?: number;
    /** 实际入选只数（= 名单长度，≤ max_picks） */
    kept_count?: number;
    /** 昨日成员出列留痕（跌破门槛/掉出候选池/容量截断），供复盘归因 */
    removed?: { symbol: string; score: number | null; reason: string }[];
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
  return getJsonArray<{ date: string; symbols: (string | null)[]; score_avg: number }>(
    `/api/picks/history?limit=${limit}`, 10_000,
  );
}

export async function getPickReviews(date?: string): Promise<PickReviewRow[]> {
  const qs = date ? `?date=${date}` : "";
  return getJsonArray<PickReviewRow>(`/api/picks/review${qs}`, 15_000);
}

export interface PickReviewGenerateResult {
  date: string;
  reviews: PickReviewRow[];
  market_pct: number | null;
}

export async function generatePickReview(): Promise<PickReviewGenerateResult> {
  return (await sendJson<PickReviewGenerateResult>("/api/picks/review/generate", "POST", {}, 30_000)).data;
}

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

export interface MacroEvent {
  region: string;
  label: string;
  event: string;
  time: string | null;
  actual: string | number | null;
  forecast: string | number | null;
  previous: string | number | null;
  importance: number | null;
  /** 后端格式化好的单行文案（前端只渲染不拼接，避免两处口径） */
  line: string;
}

export interface OvernightBiasEvidence {
  key: string;
  label: string;
  /** "%"（涨跌幅）| "bp"（收益率绝对变化） */
  unit: string;
  /** false = 仅记录、不参与方向判定（实测否决项） */
  weighted: boolean;
  note: string;
  date: string | null;
  value: number | null;
  prev_value: number | null;
  change: number | null;
  zone: "升" | "降" | "平" | null;
  bullish: boolean | null;
  skip_reason?: string;
}

export interface ClimateLink {
  target: string;
  strength: number;
  direction: number;
  chain: string;
}

export interface Climate {
  /** null = 未判定（数据不足 / 源滞后）；"neutral" 是**真信息**，两者不可混 */
  state: "el_nino" | "la_nina" | "neutral" | null;
  /** 已越线但未满 5 季的「预警态」；null = 无 */
  alert: "el_nino" | "la_nina" | null;
  strength: "weak" | "moderate" | "strong" | "very_strong" | null;
  consecutive: number;
  peak_abs: number | null;
  threshold: number;
  persist_seasons: number;
  latest: { season: string; year: number; anom: number; end_date: string } | null;
  series: { season: string; year: number; anom: number }[];
  candidate_links: ClimateLink[];
  unjudged_reason: string | null;
  as_of: string;
  timing_note: string;
  chain_caveat: string;
  /** 人工传导链的实测判读（**未获支持**）——必须渲染 */
  empirical_verdict: string;
  disclaimer: string;
}

export interface OvernightBias {
  stance: "走强" | "中性" | "承压" | null;
  score: number | null;
  score_range: string;
  available_weight: number;
  unjudged_reason: string | null;
  evidence: OvernightBiasEvidence[];
  missing: string[];
  invalidation: string[];
  horizon_note: string;
  disclaimer: string;
  as_of: Record<string, string>;
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
  /** 非农先验提醒（零外呼纯规则；仅非农日/其后 3 日内非空） */
  macro_note?: string | null;
  /** 财经日历高信号事件；null = 源不可得（见 missing），[] = 当日确无高信号事件 */
  macro_events?: MacroEvent[] | null;
  /** 隔夜海外输入 → 大盘方向偏向（P1-34）；null = 整块不可用（见 missing） */
  overnight_bias?: OvernightBias | null;
  /** 气候一阶相位（ENSO/ONI，P1-32）；null = 源不可得 → 整块不渲染 */
  climate?: Climate | null;
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

export async function generateMorningBrief(): Promise<MorningBrief> {
  return (await sendJson<MorningBrief>("/api/picks/morning-brief/generate", "POST", {}, 60_000)).data;
}

export async function getWatcherState(): Promise<WatcherState> {
  return (await getJson<WatcherState>("/api/picks/watcher/state")).data;
}

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

export async function getSignalHealth(): Promise<SignalHealthPayload> {
  return (await getJson<SignalHealthPayload>("/api/picks/signal-health")).data;
}

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
  /** 同花顺官方涨停原因原串（`+` 分隔）。ladder 行自带；缺失为 null。 */
  reason: string | null;
  hot_rank: number | null;
  distinctiveness: OpportunityJudgement;
  certainty: OpportunityJudgement;
  /** 现价/止损/出场：与 intraday-top 同源补全（attach_risk_fields），缺失显式 null。 */
  price?: number | null;
  stop_ref?: { pct: number; price: number; basis: string } | null;
  exit_plan?: Record<string, unknown> | null;
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
  /** 机会三层（需求 4）：today_strongest / quiet_starting / brewing */
  opportunity_layer?: string | null;
  layer_basis?: string | null;
  /** 簇→官方概念挂靠（09-08「代糖/玉米搜不到」修复）：成分重叠 ≥2、过滤大概念，命中降序 ≤3 */
  official_matches?: { code: string; name: string; hits: number }[];
  /** 概念详情弹窗入口的目录代码（最高命中官方概念 > 簇名精确同名） */
  catalog_code?: string | null;
}

export interface IntradayOpportunities {
  trade_date: string | null;
  themes: OpportunityTheme[];
  summary: { limit_up_total: number | null; market_max_boards: number | null; top_theme: string | null };
  hot_available: boolean;
  caveats: string[];
}

export interface WatchLedgerRow {
  id: number;
  trade_date: string;
  symbol: string;
  name: string;
  /** 机会三层：today_strongest / quiet_starting / brewing */
  layer: string;
  source_theme: string;
  /** 入选依据（首见时刻的证据快照）：题材催化/资金异动/技术形态/龙头角色 */
  reason: Record<string, unknown>;
  is_leader: boolean;
  boards: number;
  entry_price: number | null;
  entry_time: string;
  status: "tracking" | "settled";
  close_price: number | null;
  pnl_pct: number | null;
  verdict: "success" | "fail" | "flat" | null;
  verdict_reason: string | null;
  merged_into_picks: boolean;
}

export interface WatchLedgerStats {
  trade_date: string;
  total: number;
  settled: number;
  tracking: number;
  success: number;
  fail: number;
  flat: number;
  win_rate: number | null;
  avg_pnl_pct: number | null;
}

export interface WatchLedgerDay {
  trade_date: string;
  stats: WatchLedgerStats;
  rows: WatchLedgerRow[];
}

export interface WatchLedgerPayload {
  trade_date: string;
  rows: WatchLedgerRow[];
  stats: WatchLedgerStats;
  history: WatchLedgerDay[];
}

export async function getWatchLedger(date?: string, days = 5): Promise<WatchLedgerPayload> {
  const q = new URLSearchParams({ days: String(days) });
  if (date) q.set("date", date);
  return (await getJson<WatchLedgerPayload>(`/api/picks/watch-ledger?${q.toString()}`)).data;
}

export async function getIntradayOpportunities(): Promise<IntradayOpportunities> {
  return (await getJson<IntradayOpportunities>(`/api/picks/intraday-opportunities`)).data;
}

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
  /** 现价（全市场快照；缺失 null 显式 --） */
  price?: number | null;
  /** 止损参考位（risk.stop_loss_reference：档位基准/1.5ATR clamp 3%~12%） */
  stop_ref?: { pct: number; price: number; basis: string } | null;
  /** 出场纪律（risk.exit_discipline：止损/跟踪止盈/ROI 分档） */
  exit_plan?: Record<string, unknown> | null;
}

export interface IntradayTopPayload {
  trade_date: string | null;
  items: IntradayTopStock[];
  total_candidates: number;
  criteria: string;
  hot_available: boolean;
  caveats: string[];
}

export async function getIntradayTop(): Promise<IntradayTopPayload> {
  return (await getJson<IntradayTopPayload>(`/api/picks/intraday-top?limit=8`, 15_000)).data;
}

export async function runIntradayReview(): Promise<{ brief_date: string; directions: { direction: string; outcome: string }[] }> {
  return (
    await sendJson<{ brief_date: string; directions: { direction: string; outcome: string }[] }>(
      "/api/picks/intraday-review/run", "POST", {}, 60_000,
    )
  ).data;
}

export async function getPositionLabels(): Promise<Record<string, "sim" | "real">> {
  return (await getJson<{ labels: Record<string, "sim" | "real"> }>("/api/picks/position-labels")).data.labels;
}

export interface RelayRankItem {
  symbol: string;
  name: string;
  boards: number;
  reason: string;
  kmid2: number | null;
  max20: number | null;
}

export interface LurkPoolItem {
  symbol: string;
  confirm_ms: number;
}

export interface LurkPoolPayload {
  trade_date: string;
  as_of: string;
  stale_days: number;
  stale: boolean;
  stale_note: string;
  items: LurkPoolItem[];
}

export async function getRelayRank(): Promise<{ trade_date: string; items: RelayRankItem[] }> {
  return (await getJson<{ trade_date: string; items: RelayRankItem[] }>("/api/picks/relay-rank")).data;
}

export async function getLurkPool(): Promise<LurkPoolPayload> {
  return (await getJson<LurkPoolPayload>("/api/picks/lurk-pool")).data;
}

export interface StrategyHealthWindow {
  groups?: number;
  total_picks?: number;
  win_rate?: number | null;
  good?: number;
  bad?: number;
  flat?: number;
  [k: string]: unknown;
}

export interface StrategyHealthItem {
  strategy_key: string;
  name?: string;
  lifecycle?: string;
  basis?: string;
  source?: string;
  note?: string;
  /** ok / warning / drift = 有判定；insufficient / thin / no_pipeline / unknown / error = **判不出** */
  status?: string;
  window?: StrategyHealthWindow;
  [k: string]: unknown;
}

export interface StrategyHealthPayload {
  strategies: StrategyHealthItem[];
  counts?: { total?: number; evaluable?: number; attention?: number };
  caveat?: string;
}

export interface StrategyRegistryItem {
  key: string;
  name?: string;
  status?: string;
  basis?: string;
  source?: string;
  note?: string;
  /** 核验结论三态（S2-11）：有值时该条状态背后有可回查证据 */
  verification?: {
    available: boolean;
    stale?: boolean | null;
    age_days?: number | null;
    verdict?: string | null;
    headline?: string | null;
    reason?: string | null;
  } | null;
  [k: string]: unknown;
}

export async function getStrategyHealth(): Promise<StrategyHealthPayload> {
  return (await getJson<StrategyHealthPayload>("/api/picks/strategy-health")).data;
}

export async function getStrategyRegistry(): Promise<StrategyRegistryItem[]> {
  return (await getJson<{ strategies: StrategyRegistryItem[] }>("/api/picks/strategy-registry"))
    .data.strategies ?? [];
}
