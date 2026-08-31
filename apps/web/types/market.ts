export type Quality = "high" | "medium" | "low" | "stale" | "invalid";

export interface AuditFields {
  source: string;
  quality: Quality;
  quality_reasons: string[];
  received_at: string;
}

export interface Quote extends AuditFields {
  symbol: string;
  name?: string | null;
  market?: string | null;
  price?: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  prev_close?: number | null;
  change?: number | null;
  change_pct?: number | null;
  volume?: number | null;
  amount?: number | null;
  turnover_rate?: number | null;
  pe_ttm?: number | null;
  pb?: number | null;
  total_mktcap_yi?: number | null;
  float_mktcap_yi?: number | null;
  limit_up_price?: number | null;
  limit_down_price?: number | null;
  data_timestamp?: string | null;
}

export interface OrderBookLevel {
  price?: number | null;
  volume?: number | null;
}

export interface OrderBook extends AuditFields {
  symbol: string;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  data_timestamp?: string | null;
}

export interface Trade extends AuditFields {
  symbol: string;
  ts?: string | null;
  price?: number | null;
  volume?: number | null;
  side?: string | null;
}

export interface Kline extends AuditFields {
  symbol: string;
  timeframe: string;
  ts: string;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close?: number | null;
  volume?: number | null;
  amount?: number | null;
  change_pct?: number | null;
  turnover_rate?: number | null;
}

export interface LimitUpRecord extends AuditFields {
  symbol: string;
  name?: string | null;
  trade_date: string;
  price?: number | null;
  change_pct?: number | null;
  first_seal_time?: string | null;
  last_seal_time?: string | null;
  break_count?: number | null;
  seal_amount?: number | null;
  turnover_rate?: number | null;
  consecutive_boards?: number | null;
  boards_stat?: string | null;
  reason?: string | null;
}

/** 题材梯队看板 —— 一张题材卡片 */
export interface LadderRung {
  symbol: string;
  name?: string | null;
  role: string; // 空间板/龙头/中军/反包/补涨/跟风/首板
  is_primary: boolean; // 是否为该股的主属性题材
  other_themes: string[];
  boards: number;
  boards_stat?: string | null;
  seal_amount?: number | null;
  break_count?: number | null;
  turnover_rate?: number | null;
  float_market_cap?: number | null;
  amount?: number | null;
  industry_board?: string | null;
  first_seal_time?: string | null;
  last_seal_time?: string | null;
  seal_phase?: string | null; // 早盘/上午/午后/尾盘
  change_pct?: number | null;
  /** 是否为 THS 官方概念成分（L5 徽标）；缺省 = 目录服务未同步，不得当负面信号 */
  official?: boolean;
}

export interface ThemeBoardMetrics {
  board_code?: string | null;
  name?: string | null;
  kind?: string | null;
  change_pct?: number | null;
  amount?: number | null;
  turnover_rate?: number | null;
  main_net_inflow?: number | null;
  main_net_ratio?: number | null;
  up_count?: number | null;
  down_count?: number | null;
  leader_name?: string | null;
  leader_symbol?: string | null;
  chg_3d?: number | null;
  chg_5d?: number | null;
  chg_10d?: number | null;
}

export interface ThemePerformance {
  limit_up_count: number;
  max_boards: number;
  echelon_levels: Record<string, number>;
  echelon_completeness: number;
  has_succession: boolean;
  reopen_rate: number;
  seal_success_rate: number;
  market_break_rate?: number | null;
  seal_time_distribution: Record<string, number>;
  seal_quality: number;
  turnover_median?: number | null;
  amount_total?: number | null;
  float_cap_median?: number | null;
  has_middle_weight: boolean;
  active_days: number;
  daily_limit_up_counts: [string, number][];
  premium_median?: number | null;
  premium_samples: number;
}

export interface ThemeCard {
  theme: string;
  raw_tags: string[];
  is_unclassified: boolean;
  strength_score: number;
  strength_tier: string; // 领涨/强势/活跃/观察
  tier_basis: string;
  sort_basis: string;
  stage: string; // 启动/发酵/高潮/分歧/退潮
  stage_basis: string[];
  formation: string; // 成建制/初步成形/零散/个股行情
  health_note: string;
  risks: string[];
  core?: { type: string; label: string; basis?: string; votes?: Record<string, number> };
  persistence?: Record<string, unknown>;
  board?: ThemeBoardMetrics | null;
  board_matched: boolean;
  performance: ThemePerformance;
  ladder: LadderRung[];
  leaders: {
    main?: { symbol: string; name?: string | null; boards: number; role: string } | null;
    middle_weights: { symbol: string; name?: string | null; boards: number }[];
    candidates: {
      symbol: string;
      name?: string | null;
      boards: number;
      role: string;
      reason: string;
    }[];
  };
}

export interface ThemeBoardPayload {
  trade_date: string;
  prev_trade_date?: string | null;
  themes: ThemeCard[];
  broken_ladder: {
    symbol: string;
    name?: string | null;
    prev_boards: number;
    themes: string[];
    role: string;
  }[];
  summary: {
    limit_up_total: number;
    theme_count: number;
    market_max_boards: number;
    market_break_rate?: number | null;
    top_theme?: string | null;
  };
  caveats: string[];
  filters?: { sort: string; min_boards: number; min_count: number; limit: number };
}

export interface LongHuRecord extends AuditFields {
  symbol: string;
  name?: string | null;
  trade_date: string;
  close?: number | null;
  change_pct?: number | null;
  turnover_rate?: number | null;
  amount?: number | null;
  net_buy?: number | null;
  buy_amount?: number | null;
  sell_amount?: number | null;
  reason?: string | null;
}

export interface SymbolSearchItem {
  symbol: string;
  name?: string | null;
  market?: string | null;
  source: string;
  is_realtime: boolean;
}

export interface Meta {
  provider: string;
  is_realtime: boolean;
  is_stale: boolean;
  last_success_refresh: string | null;
  generated_at: string;
}

export interface WatchlistItem {
  symbol: string;
  name?: string | null;
  note?: string | null;
  group_name?: string;
  source: string;
  created_at: string | null;
  updated_at: string | null;
}
