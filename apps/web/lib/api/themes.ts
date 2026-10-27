/**
 * 题材与板块：题材目录/梯队、板块资金流（L2 唯一实现）、成分股
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import type {
  ThemeBoardPayload,
  ThemeBoardCandidate,
} from "@/types/market";

import { getJson, type ReadOptions } from "./internal";
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

export type OfficialCatalogKind = "concept" | "industry";
export interface OfficialCatalogItem {
  code: string;
  name: string;
  source: "ths_official";
  kind: OfficialCatalogKind;
  synced_at: string | null;
}
export interface OfficialCatalogPayload {
  items: OfficialCatalogItem[];
  total: number;
  kind: OfficialCatalogKind;
  source: "ths_official";
  synced_at: string | null;
  catalog_state?: "ready" | "stale" | "unknown";
}
export interface OfficialBoardQuotes {
  available: boolean;
  kind: OfficialCatalogKind;
  source: "ths_official";
  source_basis: "index_snapshot";
  source_as_of: string | null;
  source_ready_at: string | null;
  received_at: string;
  rows: {code: string; change_pct: number | null; price: number | null}[];
  missing_codes: string[];
  reason?: string;
}
export async function getOfficialCatalog(kind: OfficialCatalogKind, options: ReadOptions = {}): Promise<OfficialCatalogPayload> {
  return (await getJson<OfficialCatalogPayload>(`/api/themes/catalog?kind=${kind}`, 15_000, options)).data;
}
export async function getOfficialBoardQuotes(kind: OfficialCatalogKind, codes: readonly string[], options: ReadOptions = {}): Promise<OfficialBoardQuotes> {
  const unique = [...new Set(codes)];
  if (!unique.length || unique.length > 50) throw new Error("官方板块快照每批须为1至50个代码");
  const query = new URLSearchParams({kind, codes: unique.join(",")});
  return (await getJson<OfficialBoardQuotes>(`/api/themes/catalog/quotes?${query}`, 15_000, options)).data;
}

export async function getThemesCatalog(search?: string): Promise<ThemeCatalogItem[]> {
  const qs = search ? `?search=${encodeURIComponent(search)}` : "";
  return (await getJson<{ items: ThemeCatalogItem[] }>(`/api/themes/catalog${qs}`, 30_000)).data.items;
}

export interface ThemeStrengthRow {
  code: string;
  name: string;
  count: number;
  up: number;
  down: number;
  flat: number;
  missing: number;
  limit_up_count: number;
  avg_change_pct: number | null;
  total_amount: number | null;
  coverage: {
    member_total: number;
    sampled: number;
    not_sampled: number;
    quote_returned: number;
    valid_change_count: number;
    amount_count: number;
    missing_amount: number;
    sample_limit: number;
    /** 仅表示成员全部进入样本，不代表报价覆盖或源时点已核。 */
    complete_members: boolean;
  };
  top_gainers: { symbol: string; name: string | null; change_pct: number }[];
  basis: string;
  /** 旧响应兼容，不再作为同花顺题材资金证据。 */
  board?: BoardFundRow | null;
  candidate_board?: ThemeBoardCandidate | null;
}

export async function getThemeStrength(codes?: string[], options: ReadOptions = {}): Promise<Record<string, ThemeStrengthRow>> {
  const qs = codes && codes.length > 0 ? `?codes=${codes.join(",")}` : "";
  return (await getJson<{ themes: Record<string, ThemeStrengthRow> }>(`/api/themes/catalog/strength${qs}`, 20_000, options)).data.themes;
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
  heat: number | null;
  heat_complete: boolean;
  member_count: number | null;
  member_as_of: string | null;
  hot_symbols: string[];
  concentration_pct: number | null;
  source: "ths_official";
  hot_count: number;
  best: { symbol: string; name: string | null; rank: number; rank_change: number | null } | null;
  basis: string;
}

export interface ThemesHotPayload {
  ts: string | null;
  stocks: HotStock[];
  themes: HotTheme[];
  ranking_basis: "hot_stock_concentration";
  catalog_revision: number;
  catalog_state: "ready" | "stale" | "unknown";
  membership_state: "ready" | "unavailable";
  scope: string;
}

export async function getThemesHot(limit = 30, options: ReadOptions = {}): Promise<ThemesHotPayload> {
  return (await getJson<ThemesHotPayload>(`/api/themes/hot?limit=${limit}`, 15_000, options)).data;
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
  market_enrichment?: "not_requested" | "full";
  attribution_state?: string;
}

export async function getStockThemes(symbol: string, options: ReadOptions & {enrichment?: "snapshot" | "full"} = {}): Promise<StockThemes> {
  const {enrichment = "snapshot", ...readOptions} = options;
  return (await getJson<StockThemes>(`/api/themes/stock/${encodeURIComponent(symbol)}?enrichment=${enrichment}`, 30_000, readOptions)).data;
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
  limit_up: boolean | null;
  /** 开板次数（东财涨停池增强；仅涨停成员，非涨停 null） */
  break_count: number | null;
  /** 封单额（元，ths 官方；仅涨停成员） */
  seal_amount: number | null;
  /** 连板数（ths 官方；仅涨停成员） */
  boards: number | null;
  reason: string | null;
  reason_source?: string | null;
  reason_date?: string | null;
  tags: string[];
}

export interface ConceptDetail {
  code: string;
  name: string;
  total: number;
  limit_up_count: number | null;
  members: ConceptMember[];
  tag_groups: { tag: string; symbols: string[] }[];
  meta_note: string;
  membership_state?: "ready" | "stale" | "unknown" | "source_read";
  catalog_state?: "ready" | "stale" | "unknown";
  quote_freshness?: {state: string; reason?: string | null; as_of?: string | null} | null;
  source?: "ths_official";
  kind?: OfficialCatalogKind;
  market_enrichment?: "snapshot" | "full";
  membership_saved_at?: string | null;
  membership_as_of?: string | null;
  source_as_of?: string | null;
}

export async function getConceptDetail(code: string, options: ReadOptions & {catalogKind?: OfficialCatalogKind; enrichment?: "snapshot" | "full"} = {}): Promise<ConceptDetail> {
  const {catalogKind = "concept", enrichment = "snapshot", ...readOptions} = options;
  const query = new URLSearchParams({kind: catalogKind, enrichment});
  return (await getJson<ConceptDetail>(`/api/themes/catalog/${encodeURIComponent(code)}/detail?${query}`, undefined, readOptions)).data;
}
