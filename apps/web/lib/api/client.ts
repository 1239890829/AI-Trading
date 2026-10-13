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
  const usable = (value: string): string | null => {
    try {
      const target = new URL(value);
      if (!["ws:", "wss:"].includes(target.protocol) || target.username || target.password || target.search || target.hash) return null;
      if (typeof window !== "undefined") {
        if (window.location.protocol === "https:" && target.protocol !== "wss:") return null;
        const loopback = (host: string) => /^(?:localhost|127(?:\.\d{1,3}){3}|\[::1\]|0\.0\.0\.0)$/i.test(host);
        const page = new URL(`${window.location.protocol}//${window.location.host}`);
        // A LAN phone's localhost is the phone, never this machine's backend.
        if (loopback(target.hostname) && !loopback(page.hostname)) return null;
      }
      return value.replace(/\/$/, "");
    } catch {return null;}
  };
  const configured = process.env.NEXT_PUBLIC_WS_BASE && usable(process.env.NEXT_PUBLIC_WS_BASE);
  if (configured) return configured;
  if (API_BASE.startsWith("http")) {
    const direct = usable(API_BASE.replace(/^http/, "ws"));
    if (direct) return direct;
  }
  if (typeof window === "undefined") return "";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const relative = API_BASE.startsWith("http") || API_BASE.startsWith("//") ? "/backend" : API_BASE;
  const path = relative.replace(/^\/+|\/+$/g, "");
  return `${proto}//${window.location.host}${path ? `/${path}` : ""}`;
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
