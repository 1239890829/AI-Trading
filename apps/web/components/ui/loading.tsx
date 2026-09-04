"use client";

/**
 * 统一加载态基建（2026-09-04 用户要求：骨架屏 + fade 切换动画全局统一）：
 * - Skeleton 系：数据未就绪时的占位块，animate-pulse，配色对齐 zinc 体系，
 *   各变体与真实内容块同构（高度对齐防布局跳动）。
 * - FadeSwap：子模块/tab 切换过渡——旧内容 fade-out 120ms → 换内容 → fade-in 180ms。
 *   动画时长收紧（120/180ms）避免"等动画"的迟滞感；prefers-reduced-motion 下关闭。
 */

import { useEffect, useRef, useState, type ReactNode } from "react";

const FADE_OUT_MS = 120;

/** 基础骨架块：aria-hidden（纯装饰，读屏不需要知道占位存在）。 */
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div aria-hidden className={`animate-pulse rounded-md bg-zinc-200/80 dark:bg-zinc-800/70 ${className}`} />
  );
}

/** 指标带骨架：n 个与宽度带/概览指标块同高的占位。 */
export function StatGridSkeleton({ count = 4, className = "" }: { count?: number; className?: string }) {
  return (
    <div className={`grid grid-cols-2 gap-2 md:grid-cols-4 ${className}`}>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="rounded-lg border border-zinc-200 px-2.5 py-1.5 dark:border-zinc-800">
          <Skeleton className="h-3 w-12" />
          <Skeleton className="mt-1.5 h-4 w-16" />
        </div>
      ))}
    </div>
  );
}

/** 卡片列表骨架：题材机会卡 / 方向卡等同构占位（标题行 + 两行文本）。 */
export function CardListSkeleton({ count = 3, className = "" }: { count?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="flex items-center gap-2">
            <Skeleton className="h-4 w-28" />
            <Skeleton className="h-3.5 w-12 rounded" />
            <Skeleton className="h-3.5 w-14 rounded" />
          </div>
          <Skeleton className="mt-2 h-3 w-3/5" />
        </div>
      ))}
    </div>
  );
}

/** 表格骨架：表头行 + n 行数据占位（watcher 状态/对照表/涨停池等同构）。 */
export function TableSkeleton({ rows = 4, className = "" }: { rows?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`} aria-hidden>
      <div className="flex gap-4">
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-3 flex-1" />
        ))}
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex items-center gap-4 border-t border-zinc-100 pt-2 dark:border-zinc-800/60">
          {Array.from({ length: 5 }, (_, c) => (
            <Skeleton key={c} className={`h-3.5 flex-1 ${c === 0 ? "max-w-[80px]" : ""}`} />
          ))}
        </div>
      ))}
    </div>
  );
}

/** 统计面板骨架：4 个 KPI 卡 + 一行图表占位。 */
export function StatsSkeleton({ className = "" }: { className?: string }) {
  return (
    <div className={`space-y-3 ${className}`}>
      <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="mt-2 h-6 w-14" />
            <Skeleton className="mt-1.5 h-2.5 w-24" />
          </div>
        ))}
      </div>
      <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
        <Skeleton className="h-3 w-32" />
        <Skeleton className="mt-3 h-16 w-full" />
      </div>
    </div>
  );
}

/**
 * fade 切换容器：swapKey 变化时旧内容淡出 → 换新内容淡入。
 * out 相冻结旧节点（避免"淡出的已是新内容"），in 相内容实时跟随渲染。
 * 子模块高度可能不同——外层容器自行决定是否锁高（tab 页通常锁）。
 */
export function FadeSwap({
  swapKey,
  children,
  className = "",
}: {
  swapKey: string;
  children: ReactNode;
  className?: string;
}) {
  const [display, setDisplay] = useState<{ key: string; node: ReactNode }>({ key: swapKey, node: children });
  const [phase, setPhase] = useState<"in" | "out">("in");
  // 每次 commit 后同步最新 children（ref 写入须在 effect 中，渲染期写 ref 被 react-hooks/refs 禁止）：
  // out 相结束时换入的就是最新已提交内容。
  const childrenRef = useRef<ReactNode>(children);
  useEffect(() => {
    childrenRef.current = children;
  });

  // 状态与 props 的同步用「渲染期调整」（React 官方推荐模式，react-hooks/set-state-in-effect
  // 禁止在 effect 内直接 setState）：key 变化 → 进入 out 相；快速切回 → 回到 in 相。
  if (phase === "out" && swapKey === display.key) {
    setPhase("in");
  } else if (phase === "in" && swapKey !== display.key) {
    setPhase("out");
  }

  // out 相计时：120ms 后冻结旧节点并换入新内容（定时器回调里 setState 不受限）
  useEffect(() => {
    if (phase !== "out" || swapKey === display.key) return;
    const t = setTimeout(() => {
      setDisplay({ key: swapKey, node: childrenRef.current });
      setPhase("in");
    }, FADE_OUT_MS);
    return () => clearTimeout(t);
  }, [phase, swapKey, display.key]);

  return (
    <div className={`${phase === "out" ? "anim-fade-out" : "anim-fade-in"} ${className}`}>
      {phase === "out" ? display.node : children}
    </div>
  );
}

/** 内容首次就绪时的淡入（骨架 → 真实内容替换用；重挂载即触发）。 */
export function FadeIn({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`anim-fade-in ${className}`}>{children}</div>;
}

/** 路由级 Suspense fallback：标题行 + 骨架块，替代「加载中…」纯文字（风格统一）。 */
export function PageSkeletonFallback({ label }: { label?: string }) {
  return (
    <main className="mx-auto h-full w-full max-w-[1600px] px-4 py-3" aria-busy="true" aria-label={label ?? "页面加载中"}>
      <div className="flex items-center gap-4">
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-5 w-48" />
      </div>
      <div className="mt-4 space-y-3">
        <Skeleton className="h-24 w-full rounded-xl" />
        <Skeleton className="h-40 w-full rounded-xl" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
    </main>
  );
}
