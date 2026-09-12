"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { fmtYi, signedYi } from "@/lib/format";
import { tradingSeqFromHHMM } from "@/lib/market-hours";
import type { FlowIntradayPoint, FlowTier } from "@/lib/api";

/**
 * 资金流五档图表与共用工具（自 fund-tab.tsx 抽出，2026-09-07）：
 * 大盘分钟资金流曲线（fund-tab）与板块分钟资金流曲线（board-flow 下钻抽屉）共用，
 * 避免跨组件循环导入（fund-tab → BoardFlowPanel → FlowIntradayChart）。
 *
 * 颜色纪律（A 股惯例）：净流入=红、净流出=绿；各档资金用固定色区分。
 */

export const TIER_META: { key: keyof FlowTier; label: string; color: string; desc: string }[] = [
  { key: "main", label: "主力", color: "#dc2626", desc: "主力净额（超大单+大单）" },
  { key: "super_", label: "超大单", color: "#9f1239", desc: "超大单净额" },
  { key: "big", label: "大单", color: "#f87171", desc: "大单净额" },
  { key: "mid", label: "中单", color: "#16a34a", desc: "中单净额" },
  { key: "small", label: "小单", color: "#4ade80", desc: "小单净额" },
];

// fmtYi / signedYi 已下沉至 lib/format（2026-09-10）：题材卡等非图表场景也要用
// 同一套金额格式，留在组件文件里会造成无谓的跨组件依赖。此处 re-export 保持
// 既有引用路径（board-flow / fund-tab）不变，signedFmt 作为历史调用名的包装。
export { fmtYi, signedYi };

/** 兼容包装（历史调用名）：板块/大盘资金流组件的「±X.X亿」。 */
export function signedFmt(v: number | null): string {
  return signedYi(v, 1);
}

// 交易分钟序（HH:MM → 0..240 轴）已下沉到 `lib/market-hours.ts::tradingSeqFromHHMM`
// （2026-09-12 评审 R-3）。此前本文件自持一份 `hmToSeq`，而
// `components/minute-chart.tsx` 另有一份「已开市交易分钟数」——两者是同一条交易
// 分钟轴上的两个量（0 基轴位 / 1 基已过分钟数），却各写一份映射，且午休段算法不一致。
// 现在轴只有一份，本文件与 minute-chart 都从 `lib/market-hours` 取。

/**
 * 分钟级五档资金流累计曲线（大盘/板块通用，标题由调用方在外部给出）。
 * 交互：鼠标悬停 → 最近分钟列对齐导引线 + 各档圆点标记 + tooltip 五档明细。
 * SVG 定高（h-44）+ preserveAspectRatio=none + non-scaling-stroke；圆点/tooltip 为 HTML 元素不变形。
 */
export function FlowIntradayChart({ items }: { items: FlowIntradayPoint[] }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  // P0-4（2026-09-11）：纵轴归一 + 5 条 SVG path 只在**数据变化**时重建。
  //
  // 原实现把它们放在渲染体内（每次渲染都算）⇒ 悬停时每个 mousemove 都触发
  // setHoverIdx → 重渲染 → 把「N 点 × 5 档」的字符串整批重拼一遍。更贵的是
  // 紧接着 React 会把这 5 个 <path> 的 d 写回 DOM → 布局失效 → **下一次
  // mousemove 的 getBoundingClientRect() 被迫同步重排**（正是审查点名的
  // "每次 move 重算 5 条 path + 同步读布局"组合，后者是前者的后果）。
  // memo 后悬停只改导引线与圆点：paths 引用不变 → React 跳过这 5 个节点。
  const maxAbs = useMemo(
    () => Math.max(1, ...items.flatMap((p) => TIER_META.map((m) => Math.abs(p[m.key] ?? 0)))),
    [items]
  );
  const yPct = useCallback((v: number) => 50 - (v / maxAbs) * 46, [maxAbs]); // viewBox 纵向百分比（中轴 50%）
  const paths = useMemo(
    () =>
      TIER_META.map((m) =>
        items
          .map((p, i) => {
            const v = p[m.key];
            if (v == null) return "";
            return `${i === 0 || items[i - 1][m.key] == null ? "M" : "L"}${tradingSeqFromHHMM(p.t).toFixed(1)},${yPct(v).toFixed(1)}`;
          })
          .join(" ")
      ),
    [items, yPct]
  );
  // 命中测试用的分钟序：同样按数据 memo（原实现每次 move 都对全表重算 hmToSeq）
  const seqs = useMemo(() => items.map((p) => tradingSeqFromHHMM(p.t)), [items]);

  const onMove = useCallback(
    (e: React.MouseEvent) => {
      const rect = wrapRef.current?.getBoundingClientRect();
      if (!rect || rect.width === 0) return;
      const seq = Math.round(((e.clientX - rect.left) / rect.width) * 240);
      let best = 0;
      let bestDist = Infinity;
      seqs.forEach((s, i) => {
        const d = Math.abs(s - seq);
        if (d < bestDist) {
          bestDist = d;
          best = i;
        }
      });
      setHoverIdx(best);
    },
    [seqs],
  );

  if (items.length === 0) return null;
  const hp = hoverIdx != null ? items[hoverIdx] : null;
  const hoverLeftPct = hp ? (tradingSeqFromHHMM(hp.t) / 240) * 100 : 0;

  return (
    <div className="w-full" data-testid="flow-intraday-chart">
      <div
        ref={wrapRef}
        className="relative h-44 w-full cursor-crosshair"
        onMouseMove={onMove}
        onMouseLeave={() => setHoverIdx(null)}
      >
        <svg viewBox="0 0 240 100" preserveAspectRatio="none" className="h-full w-full" role="img" aria-label="分钟级五档资金流累计曲线">
          <line x1="0" y1="50" x2="240" y2="50" stroke="currentColor" className="text-zinc-700 dark:text-zinc-700" strokeWidth="1" strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />
          <line x1="60" y1="0" x2="60" y2="100" stroke="currentColor" className="text-zinc-900 dark:text-zinc-800/80" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <line x1="120" y1="0" x2="120" y2="100" stroke="currentColor" className="text-zinc-900 dark:text-zinc-800/80" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <line x1="180" y1="0" x2="180" y2="100" stroke="currentColor" className="text-zinc-900 dark:text-zinc-800/80" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          {TIER_META.map((m, i) => (
            <path key={m.key} d={paths[i]} fill="none" stroke={m.color} strokeWidth={m.key === "main" ? 2 : 1.1} opacity={m.key === "main" ? 1 : 0.8} vectorEffect="non-scaling-stroke" />
          ))}
        </svg>

        {/* 悬停：列导引线 + 各档圆点标记（HTML 元素，preserveAspectRatio=none 下不变形） */}
        {hp && (
          <>
            <div className="pointer-events-none absolute inset-y-0 w-px bg-zinc-400/60 dark:bg-zinc-500/60" style={{ left: `${hoverLeftPct}%` }} />
            {TIER_META.map((m) => {
              const v = hp[m.key];
              if (v == null) return null;
              return (
                <div
                  key={m.key}
                  className="pointer-events-none absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-white/80 shadow-sm dark:border-zinc-900/80"
                  style={{ left: `${hoverLeftPct}%`, top: `${yPct(v)}%`, backgroundColor: m.color }}
                />
              );
            })}
            {/* tooltip：靠近右缘时翻转到指针左侧 */}
            <div
              className="pointer-events-none absolute top-1 z-10 w-44 rounded-md border border-zinc-200 bg-white/95 p-2 shadow-lg dark:border-zinc-700 dark:bg-zinc-900/95"
              style={{ left: `${hoverLeftPct}%`, transform: hoverLeftPct > 60 ? "translateX(calc(-100% - 8px))" : "translateX(8px)" }}
              data-testid="flow-tooltip"
            >
              <p className="mb-1 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{hp.t}｜分钟累计净额（亿元）</p>
              {TIER_META.map((m) => {
                const v = hp[m.key];
                return (
                  <p key={m.key} className="flex items-center justify-between py-px text-[10px]">
                    <span className="inline-flex items-center gap-1 text-zinc-600 dark:text-zinc-400">
                      <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ backgroundColor: m.color }} />
                      {m.label}
                    </span>
                    <span className={`font-mono tabular-nums ${v == null ? "text-zinc-600 dark:text-zinc-400" : v >= 0 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down"}`}>{signedFmt(v)}</span>
                  </p>
                );
              })}
            </div>
          </>
        )}
      </div>
      {/* 时间轴：HTML 行（替代 SVG 内文字，定高下不变形） */}
      <div className="flex justify-between font-mono text-[9px] text-zinc-600 dark:text-zinc-400">
        <span>09:30</span>
        <span>10:30</span>
        <span>11:30/13:00</span>
        <span>14:00</span>
        <span>15:00</span>
      </div>
    </div>
  );
}
