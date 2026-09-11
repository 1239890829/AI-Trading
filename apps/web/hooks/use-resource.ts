"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { isTradingSession } from "@/lib/market-hours";

/**
 * 前端取数的统一抽象（S2-5）。取代「各页自己 `void load(); setInterval(load, ms)`」的样板，
 * 并把三件全站性的门控**内建在唯一入口**里，而不是要求 40 个调用点各写一遍：
 *
 * 1. **可见性暂停**：`document.hidden` 期间不轮询；回可见时**立即补拉一次**再恢复节奏。
 *    背景标签页不再对后端与上游配额空转（P0-1）。
 * 2. **盘外降频**：非交易时段把基础间隔 ×5，且**封顶 120s**
 *    （封顶是为了把「开盘瞬间的追赶延迟」限制在 2 分钟内——不封顶时 60s 轮询
 *    在盘外变 300s，09:00 打开的页面可能到 09:20 才切回盘中节奏）。
 *    时段判定复用 `lib/market-hours.ts` 的粗判（宽松区间，节假日误判为盘中只是多打一次
 *    后端缓存，无害）。非行情类取数（任务中心 / 通知 / 助手）传 `marketHours: false` 关掉。
 * 3. **三态**：`status` 显式区分 `unknown`（未判定）/ `pending` / `ready` / `error`。
 *    失败**保留上一次数据**（`data` 不清空、`status` 仍为 `ready`，同时 `error` 非空），
 *    与各页既有「失败保持旧值 + 各自空态」的约定一致；只有「从未成功过」才是 `error`。
 *
 * 其余契约与 `usePollingFetch` 相同：
 * - `fn` 经 latest-ref 间接调用，调用方无须把它挂进依赖；闭包捕获的参数每次渲染取最新值。
 * - `intervalMs = null` 表示只挂载拉一次（如依赖 URL 参数的初始拉取）。
 * - `key`（参数会变的取数必须传）：变化时**立即重拉**，否则只会等下一次轮询，是静默漏刷新。
 * - `enabled = false` 表示条件轮询（如「该页签激活才拉」），关闭时不发任何请求。
 *
 * 实现要点：轮询用 **setTimeout 链**而非 setInterval —— 间隔每轮重算，因此
 * 「盘外 → 盘中」的切换能在下一拍自动生效，不需要额外的定时器去盯时段边界。
 * 所有 setState 都发生在 promise 回调 / 定时器回调 / 事件回调里，不在 effect 同步路径上
 * （react-hooks/set-state-in-effect 推荐的结构）。
 */

/** 取数三态 + 一态显式「未判定」（对应后端 Freshness 契约的 `missing_reason` 口径）。 */
export type ResourceStatus = "unknown" | "pending" | "ready" | "error";

export interface Resource<T> {
  /** 最近一次成功取数的结果；从未成功过为 `undefined`。失败时**不清空**。 */
  data: T | undefined;
  /** 首次加载 / 手动 refresh 进行中。后台轮询**不**置位（避免每次轮询闪一下 loading）。 */
  pending: boolean;
  /** 最近一次失败；成功后清空。与 `data` 并存时表示「有旧值但刷新失败」。 */
  error: unknown;
  /** 三态派生值。 */
  status: ResourceStatus;
  /** 手动立即重拉（不重置已有 data，避免闪空）。 */
  refresh: () => void;
}

export interface ResourceOptions {
  intervalMs?: number | null;
  key?: unknown;
  /** 条件轮询开关，默认 true。 */
  enabled?: boolean;
  /** 是否套用「盘外 ×5 降频」，默认 true（行情类）。非行情类取数传 false。 */
  marketHours?: boolean;
}

/** 盘外降频倍数。 */
const OFF_SESSION_FACTOR = 5;
/** 盘外间隔封顶：把开盘瞬间的追赶延迟限制在 2 分钟内（见文件头说明）。 */
const OFF_SESSION_MAX_MS = 120_000;

function isHidden(): boolean {
  // 非 "hidden"（含 SSR 无 document、以及 "prerender"）一律按可见处理：
  // 宁可多拉一次，也不要在拿不准的时候把页面冻住。
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

function nextDelay(base: number | null, marketHours: boolean): number | null {
  if (base == null) return null;
  if (!marketHours || isTradingSession()) return base;
  return Math.min(base * OFF_SESSION_FACTOR, Math.max(base, OFF_SESSION_MAX_MS));
}

export function useResource<T = unknown>(fn: () => Promise<T>, options: ResourceOptions = {}): Resource<T> {
  const { intervalMs = null, key, enabled = true, marketHours = true } = options;

  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  });

  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<unknown>(null);
  // 手动 refresh：自增 nonce 重跑 effect；refreshing 只由 refresh()（事件上下文）置位
  const [refreshing, setRefreshing] = useState(false);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => {
    setRefreshing(true);
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const clear = () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
    };

    const run = async () => {
      if (!alive) return;
      try {
        const next = await fnRef.current();
        if (!alive) return;
        setData(next);
        setError(null);
      } catch (e) {
        if (!alive) return;
        setError(e); // 保留 data：失败不清空旧值
      } finally {
        if (alive) setRefreshing(false);
      }
    };

    const schedule = () => {
      const delay = nextDelay(intervalMs, marketHours);
      if (delay == null || !alive) return;
      timer = setTimeout(() => {
        timer = null;
        if (!alive) return;
        if (isHidden()) return; // 隐藏期不拉，等 visibilitychange 补
        void run().then(schedule);
      }, delay);
    };

    const onVisibility = () => {
      if (!alive) return;
      if (isHidden()) {
        clear(); // 隐藏：停表
        return;
      }
      clear();
      void run().then(schedule); // 回可见：立即补拉一次，再重排
    };

    void run().then(schedule);
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisibility);

    return () => {
      alive = false;
      clear();
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled, intervalMs, key, marketHours, nonce]);

  const status: ResourceStatus = !enabled
    ? "unknown"
    : data !== undefined
      ? "ready"
      : error != null
        ? "error"
        : "pending";

  return { data, pending: refreshing || status === "pending", error, status, refresh };
}
