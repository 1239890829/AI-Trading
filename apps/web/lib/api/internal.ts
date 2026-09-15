/**
 * 模块内助手：请求实现与三个 JSON 收发包装（**刻意不由门面转发**，对外可见面因此不变）
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import type {
  Meta,
} from "@/types/market";

import { API_BASE, ApiError } from "./client";

const DEFAULT_TIMEOUT_MS = 8000;

async function request<T>(
  path: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<{ data: T; meta: Meta }> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...init, signal: ctrl.signal });
  } catch (e) {
    const aborted = e instanceof DOMException && e.name === "AbortError";
    throw new ApiError(
      0,
      aborted ? `${path}：请求超时（${timeoutMs}ms）` : `${path}：网络错误（后端未启动或连接被拒）`,
      aborted ? "timeout" : "network_error",
    );
  } finally {
    clearTimeout(timer);
  }
  const body = (await res.json().catch(() => ({}))) as { data?: T; detail?: string; code?: string };
  if (!res.ok) {
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
}

async function getJsonArray<T>(path: string, timeoutMs?: number): Promise<T[]> {
  const body = await getJson<T[] | null>(path, timeoutMs);
  return body.data ?? [];
}

async function getJson<T>(path: string, timeoutMs: number = DEFAULT_TIMEOUT_MS): Promise<{ data: T; meta: Meta }> {
  return request<T>(path, {}, timeoutMs);
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
