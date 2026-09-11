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
 * 警戒区即追加，稳。滚动容器向上找最近可滚祖先（找不到退化为 window——真实页面任何
 * 容器滚动都会冒泡到 window，监听 window 永不失联）。
 *
 * `resetKey` 变化（切换维度/区间/筛选）时页码归位到首页：否则用户在新维度下会
 * 直接看到"已加载 120/200"，与"从第一页开始"的预期不符。
 */
export const DEFAULT_PAGE_SIZE = 30;
export const DEFAULT_PAGE_STEP = 30;
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
  opts?: { pageSize?: number; step?: number; resetKey?: unknown },
): IncrementalList<T> {
  const pageSize = opts?.pageSize ?? DEFAULT_PAGE_SIZE;
  const step = opts?.step ?? DEFAULT_PAGE_STEP;
  const resetKey = opts?.resetKey;

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

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    let rootEl: HTMLElement | null = sentinel.parentElement as HTMLElement | null;
    while (rootEl && rootEl.scrollHeight <= rootEl.clientHeight + 1) {
      rootEl = rootEl.parentElement as HTMLElement | null;
    }
    const check = () => {
      const r = sentinel.getBoundingClientRect();
      const vh = window.innerHeight || 800;
      if (r.top <= vh + VIEWPORT_MARGIN && r.bottom >= -VIEWPORT_MARGIN) loadMore();
    };
    const target: HTMLElement | Window = rootEl ?? window;
    target.addEventListener("scroll", check, { passive: true });
    check(); // 首帧：内容不足一屏时立即补载到撑满
    return () => target.removeEventListener("scroll", check);
  }, [loadMore, total]);

  return {
    shown: items.slice(0, visible) as T[],
    visible,
    hasMore: visible < total,
    sentinelRef,
    loadMore,
  };
}
