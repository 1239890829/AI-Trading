/**
 * 模块内助手：请求实现与三个 JSON 收发包装（**刻意不由门面转发**，对外可见面因此不变）
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * 对外仍由 `lib/api.ts` 统一转发；并发复用只覆盖已核实的行情/自选读取，
 * 不缓存完成值，不合并模拟账户初始化、撮合、事件归档或模型类 GET。
 */

import type {
  Meta,
} from "@/types/market";

import { API_BASE, ApiError } from "./client";

const DEFAULT_TIMEOUT_MS = 8000;

export interface ReadOptions {
  signal?: AbortSignal;
  /** 人工/写后校准不能加入旧读取。 */
  fresh?: boolean;
}

// Only unfinished reads share; source times and completed values are never cached here.
// events/impact can build its leader archive, paper GET can settle/init, and digest
// can generate a model summary. Those consumers deliberately remain outside this list.
const SAFE_READ_PATHS = new Set(["/api/watchlist", "/api/watchlist/groups", "/api/market/overview", "/api/limit-up", "/api/limit-down", "/api/minute-line/sh000001"]);
const sharedReads = new Map<string, SharedRead>();
const MAX_SHARED_READS = 32;
let readEpoch = 0;
let sessionEpoch = 0;
export function deviceSessionEpoch() {return sessionEpoch;}

/** A new pairing must never inherit unfinished reads from the previous device session. */
export function clearDeviceSessionReads() {
  sessionEpoch += 1;
  readEpoch += 1;
  for (const entry of sharedReads.values()) entry.controller.abort();
  sharedReads.clear();
}
type Envelope<T> = {data: T; meta: Meta};
interface SharedRead {
  path: string;
  controller: AbortController;
  promise: Promise<Envelope<unknown>>;
  owners: number;
  settled: boolean;
}

function invalidateReads(writePath: string) {
  // These local writes have verified, narrow consumers. Unknown commands keep
  // the broad isolation below, including failures with an unknown commit state.
  if (writePath === "/api/notifications/read-state") return;
  if (/^\/api\/watchlist(?:\/|$)/.test(writePath)) {
    for (const [key, entry] of sharedReads) {
      if (/^\/api\/watchlist(?:[/?]|$)/.test(entry.path)) sharedReads.delete(key);
    }
    return;
  }
  readEpoch += 1;
  // 旧消费者仍可收自己的回包；后续消费者只能加入新代际。
  sharedReads.clear();
}

function canceled(path: string) {
  return new ApiError(0, `${path}：请求已取消`, "canceled");
}

function readKey(path: string, init: RequestInit, timeoutMs: number): string | null {
  // 不在 SSR 跨用户复用，也不推定外部 API 的 cookie/token 鉴权契约。
  if (typeof window === "undefined" || API_BASE !== "/backend" || !SAFE_READ_PATHS.has(path.split("?")[0]) || init.body != null) return null;
  const headers = [...new Headers(init.headers).entries()].sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify([readEpoch, window.location.origin, `${API_BASE}${path}`, timeoutMs,
    headers, init.credentials ?? "same-origin", document.cookie,
    init.cache ?? "no-store", init.mode, init.redirect, init.referrer, init.referrerPolicy, init.integrity]);
}

async function performRequest<T>(
  path: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<{ data: T; meta: Meta }> {
  const requestSession = sessionEpoch;
  if (init.signal?.aborted) throw canceled(path);
  const ctrl = new AbortController();
  const onCancel = () => ctrl.abort();
  init.signal?.addEventListener("abort", onCancel, {once: true});
  const started = performance.now();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  const expired = () => ctrl.signal.aborted || performance.now() - started >= timeoutMs;
  const timeout = () => new ApiError(0, `${path}：请求超时（${timeoutMs}ms）`, "timeout");
  const abortError = () => init.signal?.aborted ? canceled(path) : timeout();
  try {
    let res: Response;
    try {
      res = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...init, signal: ctrl.signal });
    } catch (e) {
      if (expired() || (e instanceof DOMException && e.name === "AbortError")) throw abortError();
      throw new ApiError(0, `${path}：网络错误（后端未启动或连接被拒）`, "network_error");
    }
    let raw: unknown;
    try {
      raw = await res.json();
    } catch (e) {
      // 正文读取也受同一预算约束；中止不能被归为非 JSON 的 bad_envelope。
      if (expired() || (e instanceof DOMException && e.name === "AbortError")) throw abortError();
      raw = {};
    }
    // 同步 JSON 解析会占住主线程，计时器尚未运行也必须核对完整耗时。
    if (requestSession !== sessionEpoch) throw canceled(path);
    if (expired()) throw abortError();
    const body = (raw !== null && typeof raw === "object" ? raw : {}) as { data?: T; detail?: string; code?: string };
    if (!res.ok) {
      if (res.status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event("ashare-access-expired"));
      throw new ApiError(res.status, body.detail ?? `HTTP ${res.status}`, body.code ?? `http_${res.status}`);
    }
  // 200 但没有 Envelope 的 `data` 键 = **契约破坏**（响应体不是合法 JSON，或请求落到了非 API 路由）。
  // 若不校验而直接 `return body`，调用方拿到的是 `undefined`：`.catch(() => [])` 兜得住「抛错」、
  // **兜不住「返回 undefined」**，于是崩溃点会漂到离根因很远的地方——实测表现为
  // `Cannot read properties of undefined (reading 'length')`（V8）/
  // `undefined is not an object (evaluating 'reviews.length')`（JSC），
  // 现场看像是页面自身逻辑出错，实际根因是接口响应不是 Envelope。
  // 三态纪律：契约破坏要**显式失败**，不能伪装成「成功且无数据」（KB-ENG-37）。
    if (!("data" in body)) {
      throw new ApiError(
        res.status,
        `${path}：响应缺少 data 字段（HTTP ${res.status} 但非 API Envelope）`,
        "bad_envelope",
      );
    }
    return body as { data: T; meta: Meta };
  } finally {
    clearTimeout(timer);
    init.signal?.removeEventListener("abort", onCancel);
  }
}

function joinRead<T>(path: string, key: string, entry: SharedRead, signal?: AbortSignal | null): Promise<Envelope<T>> {
  entry.owners += 1;
  return new Promise((resolve, reject) => {
    let released = false;
    const release = () => {
      if (released) return false;
      released = true;
      signal?.removeEventListener("abort", onCancel);
      entry.owners -= 1;
      if (entry.owners === 0 && !entry.settled) {
        if (sharedReads.get(key) === entry) sharedReads.delete(key);
        entry.controller.abort();
      }
      return true;
    };
    const onCancel = () => { if (release()) reject(canceled(path)); };
    if (signal?.aborted) onCancel();
    else signal?.addEventListener("abort", onCancel, {once: true});
    // 不共享嵌套对象的可变引用；结果只含已解析 JSON。
    entry.promise.then(value => {
      if (!release()) return;
      try { resolve(JSON.parse(JSON.stringify(value)) as Envelope<T>); }
      catch (error) { reject(error); }
    }, error => { if (release()) reject(error); });
  });
}

async function request<T>(path: string, init: RequestInit, timeoutMs: number, options: ReadOptions = {}): Promise<Envelope<T>> {
  const signal = options.signal ?? init.signal;
  if (signal?.aborted) throw canceled(path);
  const isRead = (init.method ?? "GET").toUpperCase() === "GET";
  // 即使客户端看到失败/超时，写操作也可能已经提交。前后两侧都隔离旧读。
  if (!isRead) invalidateReads(path);
  try {
    const key = isRead ? readKey(path, init, timeoutMs) : null;
    if (key !== null) {
      // Fresh bypasses this exact identity; unrelated reads keep their owners.
      if (options.fresh) sharedReads.delete(key);
      const existing = sharedReads.get(key);
      if (existing) return await joinRead<T>(path, key, existing, signal);
      if (sharedReads.size < MAX_SHARED_READS) {
        const controller = new AbortController();
        const promise = performRequest<unknown>(path, {...init, signal: controller.signal}, timeoutMs).finally(() => {
          entry.settled = true;
          if (sharedReads.get(key) === entry) sharedReads.delete(key);
        });
        const entry: SharedRead = {path, controller, owners: 0, settled: false, promise};
        sharedReads.set(key, entry);
        return await joinRead<T>(path, key, entry, signal);
      }
    }
    return await performRequest<T>(path, {...init, signal}, timeoutMs);
  } finally {
    if (!isRead) invalidateReads(path);
  }
}

async function getJsonArray<T>(path: string, timeoutMs?: number, options?: ReadOptions): Promise<T[]> {
  const body = await getJson<T[] | null>(path, timeoutMs, options);
  return body.data ?? [];
}

async function getJson<T>(path: string, timeoutMs: number = DEFAULT_TIMEOUT_MS, options?: ReadOptions): Promise<{ data: T; meta: Meta }> {
  return request<T>(path, {}, timeoutMs, options);
}

async function sendJson<T = unknown>(
  path: string,
  method: string,
  body?: unknown,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
): Promise<{ data: T; meta: Meta }> {
  return request<T>(
    path,
    {
      method,
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    },
    timeoutMs,
  );
}

/**
 * 切片附加的**导出提升**（原文件里这三个是本模块私有助手）。
 * 门面**刻意不转发本文件** ⇒ 对外可见的导出面与拆分前完全一致。
 */
export { DEFAULT_TIMEOUT_MS, getJson, getJsonArray, request, sendJson };
