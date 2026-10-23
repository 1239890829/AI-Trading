"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * 增量渲染（"滚动加载更多"）单点实现。
 *
 * 为什么需要它：多个页面把后端返回的整包列表一次性渲染（题材 / 行业 / 龙虎榜 /
 * 事件 / 通知动辄 100~500 条），首屏 DOM 节点过多导致渲染卡顿。分页不是"截断"，
 * 而是把首屏节点数压到 PAGE_SIZE，滚到底继续追加——用户感知仍是"全都有"。
 *
 * ⚠️ 为什么用 scroll 事件而不是 IntersectionObserver（2026-09-04 实测结论，勿回退）：
 * IO 只在"穿越边界"时回调一次；本类列表卡片高度不固定，追加一页后哨兵可能**仍在**
 * 视口内 → IO 不再回调 → 永久卡在中间页。scroll 每帧重查哨兵位置，凡进入视口底部
 * 警戒区即追加。绑定时向上找最近可滚祖先；找不到时只监听 window 本身的滚动，
 * 原生元素 scroll 不默认冒泡。条件挂载的列表通过 active 在打开后绑定、关闭时解绑。
 *
 * `resetKey` 变化（切换维度/区间/筛选）时页码归位到首页：否则用户在新维度下会
 * 直接看到"已加载 120/200"，与"从第一页开始"的预期不符。
 */
// 模块内默认值，刻意不 export：对外 API 面只留 useIncremental / IncrementalList
const DEFAULT_PAGE_SIZE = 30;
const DEFAULT_PAGE_STEP = 30;
/** 哨兵进入视口底部多少像素内即触发追加。 */
const VIEWPORT_MARGIN = 300;

export interface IncrementalList<T> {
  /** 当前应渲染的切片（已按 visible 截断）。 */
  shown: T[];
  /** 已渲染条数。 */
  visible: number;
  /** 是否还有未渲染的条目。 */
  hasMore: boolean;
  /** 挂在列表末尾的哨兵 ref（高度 0 的 div）。 */
  sentinelRef: React.RefObject<HTMLDivElement | null>;
  /** 手动追加一页（键盘/按钮回退路径）。 */
  loadMore: () => void;
}

export function useIncremental<T>(
  items: readonly T[],
  opts?: { pageSize?: number; step?: number; resetKey?: unknown; active?: boolean },
): IncrementalList<T> {
  const pageSize = opts?.pageSize ?? DEFAULT_PAGE_SIZE;
  const step = opts?.step ?? DEFAULT_PAGE_STEP;
  const resetKey = opts?.resetKey;
  const active = opts?.active ?? true;

  const [visible, setVisible] = useState(pageSize);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  // 维度/区间/筛选切换 → 页码归位。用「渲染期调整 state」（React 官方模式，
  // 与 FadeSwap 同款）而非 effect：effect 里直接 setState 被
  // react-hooks/set-state-in-effect 禁止，且会多渲染一帧（用户能看到"已加载 120/200"闪一下）。
  const [seenKey, setSeenKey] = useState(resetKey);
  if (seenKey !== resetKey) {
    setSeenKey(resetKey);
    setVisible(pageSize);
  }

  const total = items.length;

  const loadMore = useCallback(() => {
    setVisible((v) => (v >= total ? v : v + step));
  }, [total, step]);

  const check = useCallback(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const r = sentinel.getBoundingClientRect();
    const vh = window.innerHeight || 800;
    if (r.top <= vh + VIEWPORT_MARGIN && r.bottom >= -VIEWPORT_MARGIN) loadMore();
  }, [loadMore]);

  useEffect(() => {
    if (!active) return;
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    let rootEl: HTMLElement | null = sentinel.parentElement as HTMLElement | null;
    while (rootEl && rootEl.scrollHeight <= rootEl.clientHeight + 1) {
      rootEl = rootEl.parentElement as HTMLElement | null;
    }
    const target: HTMLElement | Window = rootEl ?? window;
    target.addEventListener("scroll", check, { passive: true });
    return () => target.removeEventListener("scroll", check);
    // An initial fill can create inner overflow where none existed at binding.
    // Rediscover after an append, not on every scroll frame.
  }, [active, check, total, visible]);

  useEffect(() => {
    // Check once for a new list/activation; rebinding alone must not cascade pages.
    if (active) check();
  }, [active, check, total, resetKey]);

  return {
    shown: items.slice(0, visible) as T[],
    visible,
    hasMore: visible < total,
    sentinelRef,
    loadMore,
  };
}
