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
  source: string;
  created_at: string | null;
  updated_at: string | null;
}
