"use client";

import { useEffect, useRef } from "react";

/**
 * 挂载即拉取 + 定时轮询的统一入口（替代散落各页的 `void load(); setInterval(load, ms)` 样板）。
 *
 * - `fn` 经 latest-ref 间接调用：调用方无须把 fn 挂进依赖，fn 闭包捕获的参数
 *   （如 URL date）每次渲染都会被最新一次引用，挂载/轮询始终跑最新版本；
 * - `intervalMs = null` 表示只挂载拉一次（如依赖 URL 参数的初始拉取）；
 * - `key`（2026-09-10 新增）：**参数会变的取数必须传**。effect 只在 `intervalMs`
 *   或 `key` 变化时重跑——若取数依赖 code/date 之类会变的入参而 key 不传，
 *   参数变化时不会立即重拉（只会等下一次轮询），是静默的漏刷新。
 * - setState 都发生在 promise 回调/定时器回调里（异步路径），不在 effect 同步
 *   路径上——这是 react-hooks/set-state-in-effect 规则推荐的结构
 *   （"subscribe for updates, calling setState in a callback"）。
 *
 * 卸载后不再触发（alive 哨兵）；调用方自行负责 catch（fn 内部吞错）或在此处
 * 统一吞错（轮询型页面的既有约定：失败保持上一次数据 + 各自空态）。
 */
export function usePollingFetch(fn: () => Promise<unknown>, intervalMs: number | null, key?: unknown) {
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  });
  useEffect(() => {
    let alive = true;
    const run = () => {
      if (alive) void fnRef.current();
    };
    run();
    if (intervalMs == null) {
      return () => {
        alive = false;
      };
    }
    const t = setInterval(run, intervalMs);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [intervalMs, key]);
}
