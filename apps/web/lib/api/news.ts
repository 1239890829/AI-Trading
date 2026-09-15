/**
 * 资讯：摘要与正文
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import { getJson } from "./internal";
import type { InfoItem } from "./market";

export async function getNews<T = InfoItem>(symbol: string, limit = 8): Promise<T[]> {
  return (await getJson<{ symbol: string; items: T[] }>(`/api/news/${symbol}?limit=${limit}`, 15_000)).data.items;
}

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

export async function getNewsDigest(symbol: string, limit = 8): Promise<NewsDigest> {
  // 超时 40s：后端首次生成实测 ~32s（抓原文+摘要），15s 会稳定超时失败。
  // 调用方（feed 弹窗）是后台加载不阻塞 UI，慢只是慢，不是卡死。
  return (await getJson<NewsDigest>(`/api/news/digest/${symbol}?limit=${limit}`, 40_000)).data;
}

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
