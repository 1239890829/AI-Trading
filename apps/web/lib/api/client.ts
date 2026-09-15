/**
 * 请求核心：基址 / 超时 / 错误契约 / 三态镜像 / 统一 JSON 收发（各域共用）
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/backend";

export type FreshnessState = "ready" | "stale" | "degraded" | "unavailable" | "unknown";

export interface Freshness {
  state: FreshnessState;
  reason?: string | null;
  as_of?: string | null;
  age_seconds?: number | null;
  source?: string | null;
}

export function wsBase(): string {
  if (process.env.NEXT_PUBLIC_WS_BASE) return process.env.NEXT_PUBLIC_WS_BASE;
  if (API_BASE.startsWith("http")) return API_BASE.replace(/^http/, "ws");
  if (typeof window === "undefined") return "";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${API_BASE}`;
}

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

export function indexDetailSymbol(symbol: string, market?: string | null): string {
  const s = symbol.trim().toLowerCase();
  if (/^(sh|sz|bj)/.test(s)) return s;
  const prefix = market?.toUpperCase() === "SZ" || s.startsWith("399") ? "sz" : "sh";
  return `${prefix}${s}`;
}

export function isIndexSymbol(symbol: string): boolean {
  return /^(sh|sz|bj)/i.test(symbol.trim());
}
