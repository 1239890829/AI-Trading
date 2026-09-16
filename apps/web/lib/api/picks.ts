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
  // --- 可参与性（2026-09-15 猎场口径）+ 入选来源 ---
  /** 可参与性三态：可参与 / 不可参与 / unknown（后端 picks/tradability.assess） */
  tradability?: TradabilityJudgement | null;
  /** 入选来源：theme_linkage / event / limit_up / hot / carryover */
  source?: string | null;
  /** 来源依据（题材联动股写明"从哪个题材挖出来、为什么"） */
  source_basis?: string | null;
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

/** 闸门输入留痕（后端 `evaluate_stand_aside` 的 `signals` 原样带出）。
 *  对照「生成时 vs 当前」必须靠它——没有输入就无法解释结论为何变化，
 *  而**不允许在前端重算规则**（重算 = 两份口径必然漂移）。 */
export interface StandAsideSignals {
  promotion_1to2?: number | null;
  promotion_1to2_pctl?: number | null;
  break_rate?: number | null;
  break_rate_pctl?: number | null;
  limit_down?: number | null;
  prev_zt_median_pct?: number | null;
  promo_caliber?: string;
  break_caliber?: string;
}

/** 实时复核对照面（后端 `_live_gate` 的 `recheck`）。 */
export interface StandAsideRecheck {
  /** 本次复核用的相位 */
  phase: string | null;
  /** 本次复核锚定的交易日 */
  trade_date: string | null;
  /** 本次复核时刻（UTC ISO） */
  judged_at: string | null;
  /** 生成时刻落库的相位（对照基准） */
  stored_phase: string | null;
  /** 相位是否发生变化（闸门结论变化的**主因**，前端据此决定要不要提示复核） */
  phase_changed: boolean;
  inputs_source: string;
  break_caliber?: string | null;
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
  /** 结论来源（2026-09-16）：`stored`=组合生成时刻落库的判断；`live`=读取时刻按实时情绪重算；
   *  `unavailable`=实时复核不可用，本对象其实是生成时刻结论（附 `gate_note` 说明原因）。 */
  gate_source?: "stored" | "live" | "unavailable" | string;
  /** `gate_source="unavailable"` 时的原因说明（三态纪律：来源显式，未知不伪装成"未触发"） */
  gate_note?: string;
  /** 闸门输入留痕（解释结论为何变化时读它） */
  signals?: StandAsideSignals;
  /** 仅 `gate_source="live"` 时有值 */
  recheck?: StandAsideRecheck | null;
}

export interface DailyPicksPayload {
  date: string | null;
  items: DailyPickItem[];
  stale?: boolean;
  replaced?: { out: string; in: string; delta: number }[];
  note?: string;
  meta?: {
    weights: Record<string, number>;
    /** 组合生成时刻（ISO，带 +08:00）——「生成时 vs 当前」对照文案的时间锚点 */
    generated_at?: string;
    regime?: PickRegime;
    style_routing?: StyleRouting | null;
    gate?: StandAsideGate;
    /** **读取时刻**按实时情绪重算的闸门（2026-09-16 猎场头部动态化）。
     *
     *  `gate` 是组合生成时刻（09:26）定格的全天结论——实测该时刻在场仅 3 只涨停、
     *  最高 2 板，据此判「退潮」并撤除买入区间，而当日收盘口径是「高潮」。
     *  本字段是同一判据用实时输入重算的结果：不一致时以**它**为准展示，
     *  `gate` 作为对照保留（它仍是当日 `buy_range` 被撤的原因，复盘要它）。
     *  旧后端不返回本字段 → undefined，前端退化为只显示 `gate`。 */
    gate_live?: StandAsideGate | null;
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
    /** 可参与性口径留痕（2026-09-15）：剔除明细 + 题材联动挖掘 + 候选池来源计数 */
    tradability_policy?: {
      open_seal_cutoff: string;
      excluded_open_sealed: {
        count: number;
        items: { symbol: string; name: string | null; first_seal_time: string | null }[];
      };
      theme_linkage: {
        themes?: {
          theme: string; count: number; share: number | null; max_boards: number;
          container: string | null; candidates: number; note: string | null;
        }[];
        added?: number;
        note?: string | null;
      };
      candidate_sources: Record<string, number>;
      /** 可交易板块白名单（账户口径） */
      tradable_boards?: string;
      /** 候选池因板块权限被剔除的明细 */
      excluded_board?: {
        count: number;
        items: { symbol: string; name: string; board: string; from: string }[];
      };
    };
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

/**
 * 可参与性判定（2026-09-15 猎场口径）。
 *
 * **刻意与 `OpportunityJudgement` 分开**：那套是「机会度」（高/中/低/unknown），
 * 这套是「能不能买」（可参与/不可参与/unknown）。两者的字面量完全不同，
 * 复用一个类型会让"谁是谁"在类型层就分不清——而混用恰恰是本轮要修的病灶
 * （拿封板质量冒充可操作性）。
 */
export interface TradabilityJudgement {
  level: "可参与" | "不可参与" | "unknown";
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
  /** 首封时间（涨停池官方字段）：可参与性判据「开盘即涨停」的依据 */
  first_seal_time?: string | null;
  /** 可参与性三态（2026-09-15）：涨停梯队恒为「不可参与」——它们当日买不进 */
  tradability?: TradabilityJudgement | null;
  /** 仅作参考（涨停梯队标记；**不是**猎场候选） */
  reference_only?: boolean;
  /** 板块中文名（沪市主板/深市主板/创业板/科创板/北交所/B股）——账户权限可视 */
  board?: string | null;
  /** 账户是否有该板块交易权限（2026-09-15：仅沪深主板为 true） */
  tradable?: boolean | null;
  distinctiveness: OpportunityJudgement;
  certainty: OpportunityJudgement;
  /** 联动置信度（仅 participants：尚未涨停的题材联动候选有） */
  linkage?: OpportunityJudgement | null;
  /** 一句话入选依据（participants 由后端生成，直接展示） */
  basis?: string | null;
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
  /** 涨停梯队：**仅参考信息**（已封板/开盘即涨停，当日买不进；非主板已剔除） */
  stocks: OpportunityStock[];
  /** 猎场候选：题材内**尚未涨停**、报价可成交的联动个股（2026-09-15 口径） */
  participants?: OpportunityStock[];
  /** 未挖掘/挖空的原因（区分"没挖"与"挖空了"，空列表时读它） */
  participants_note?: string | null;
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
  /** 题材联动挖掘汇总（2026-09-15）：挖了几个题材、补入几只 */
  linkage_stats?: {
    themes_mined: number;
    candidates: number;
    /** 被板块权限挡下的成分只数（解释「候选为什么这么少」） */
    excluded_board?: number;
    excluded_board_labels?: Record<string, number>;
  } | null;
  /** 挖掘失败时的显式说明（非静默降级） */
  linkage_note?: string | null;
  /** 参考区（涨停梯队）因板块权限被剔除的只数 */
  board_excluded_reference?: number | null;
  /** 当前可交易板块白名单（账户口径） */
  tradable_boards?: string | null;
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
  /** 联动置信度（2026-09-15 新口径：items 全是尚未涨停的题材联动候选） */
  linkage?: { level: string; basis: string } | null;
  /** 可参与性三态（items 恒为「可参与」——这是保证的透出，不是事后标签） */
  tradability?: TradabilityJudgement | null;
  /** 首封时间（仅参考区有） */
  first_seal_time?: string | null;
  /** 仅作参考（涨停梯队；**不是**猎场候选） */
  reference_only?: boolean;
  /** 板块中文名（账户权限口径可视：沪市主板/深市主板/…） */
  board?: string | null;
  /** 账户是否有该板块交易权限（2026-09-15 起仅沪深主板为 true） */
  tradable?: boolean | null;
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
  /** 猎场候选：**可参与**（尚未涨停、报价可成交） */
  items: IntradayTopStock[];
  /** 涨停梯队：**仅参考信息**（已封板/开盘即涨停，当日买不进） */
  reference_items?: IntradayTopStock[];
  total_candidates: number;
  reference_total?: number;
  /** 参考区因板块权限被剔除的只数 */
  board_excluded_reference?: number;
  /** 当前可交易板块白名单（账户口径） */
  tradable_boards?: string;
  criteria: string;
  reference_criteria?: string;
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
