/**
 * 题材与板块：题材目录/梯队、板块资金流（L2 唯一实现）、成分股
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import type {
  ThemeBoardPayload,
} from "@/types/market";

import { getJson } from "./internal";
import type { BoardFundRow } from "./market";

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

export interface ThemeCatalogItem {
  code: string;
  name: string;
}

export async function getThemesCatalog(search?: string): Promise<ThemeCatalogItem[]> {
  const qs = search ? `?search=${encodeURIComponent(search)}` : "";
  return (await getJson<{ items: ThemeCatalogItem[] }>(`/api/themes/catalog${qs}`, 30_000)).data.items;
}

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
  /** 同名东财板块的资金（f62 口径，P1-5 新口径；匹配不到为 null） */
  board?: BoardFundRow | null;
}

export async function getThemeStrength(codes?: string[]): Promise<Record<string, ThemeStrengthRow>> {
  const qs = codes && codes.length > 0 ? `?codes=${codes.join(",")}` : "";
  return (await getJson<{ themes: Record<string, ThemeStrengthRow> }>(`/api/themes/catalog/strength${qs}`, 20_000)).data.themes;
}

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

export async function getThemesHot(limit = 30): Promise<ThemesHotPayload> {
  return (await getJson<ThemesHotPayload>(`/api/themes/hot?limit=${limit}`, 15_000)).data;
}

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

export interface ConceptMember {
  symbol: string;
  name: string;
  change_pct: number | null;
  price: number | null;
  /** 换手率 %（全市场快照口径） */
  turnover_rate: number | null;
  /** 流通市值（亿元，快照口径） */
  float_market_cap_yi: number | null;
  limit_up: boolean;
  /** 开板次数（东财涨停池增强；仅涨停成员，非涨停 null） */
  break_count: number | null;
  /** 封单额（元，ths 官方；仅涨停成员） */
  seal_amount: number | null;
  /** 连板数（ths 官方；仅涨停成员） */
  boards: number | null;
  reason: string | null;
  tags: string[];
}

export interface ConceptDetail {
  code: string;
  name: string;
  total: number;
  limit_up_count: number;
  members: ConceptMember[];
  tag_groups: { tag: string; symbols: string[] }[];
  meta_note: string;
}

export async function getConceptDetail(code: string): Promise<ConceptDetail> {
  return (await getJson<ConceptDetail>(`/api/themes/catalog/${encodeURIComponent(code)}/detail`)).data;
}
