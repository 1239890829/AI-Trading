"use client";

import { useCallback, useRef, useState } from "react";
import { fmtYi, signedYi } from "@/lib/format";
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

/** HH:MM → 交易分钟序（0..240），与后端 _sina_bar_seq 同口径。 */
export function hmToSeq(t: string): number {
  const [h, m] = t.split(":").map(Number);
  const hm = h * 60 + m;
  if (hm <= 570) return 0;
  if (hm <= 690) return hm - 570;
  return Math.min(240, 120 + (hm - 780));
}

/**
 * 分钟级五档资金流累计曲线（大盘/板块通用，标题由调用方在外部给出）。
 * 交互：鼠标悬停 → 最近分钟列对齐导引线 + 各档圆点标记 + tooltip 五档明细。
 * SVG 定高（h-44）+ preserveAspectRatio=none + non-scaling-stroke；圆点/tooltip 为 HTML 元素不变形。
 */
export function FlowIntradayChart({ items }: { items: FlowIntradayPoint[] }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const maxAbs = Math.max(
    1,
    ...items.flatMap((p) => TIER_META.map((m) => Math.abs(p[m.key] ?? 0))),
  );
  const yPct = (v: number) => 50 - (v / maxAbs) * 46; // viewBox 纵向百分比（中轴 50%）
  const toPath = (key: keyof FlowTier) =>
    items
      .map((p, i) => {
        const v = p[key];
        if (v == null) return "";
        return `${i === 0 || items[i - 1][key] == null ? "M" : "L"}${hmToSeq(p.t).toFixed(1)},${yPct(v).toFixed(1)}`;
      })
      .join(" ");

  const onMove = useCallback(
    (e: React.MouseEvent) => {
      const rect = wrapRef.current?.getBoundingClientRect();
      if (!rect || rect.width === 0) return;
      const seq = Math.round(((e.clientX - rect.left) / rect.width) * 240);
      let best = 0;
      let bestDist = Infinity;
      items.forEach((p, i) => {
        const d = Math.abs(hmToSeq(p.t) - seq);
        if (d < bestDist) {
          bestDist = d;
          best = i;
        }
      });
      setHoverIdx(best);
    },
    [items],
  );

  if (items.length === 0) return null;
  const hp = hoverIdx != null ? items[hoverIdx] : null;
  const hoverLeftPct = hp ? (hmToSeq(hp.t) / 240) * 100 : 0;

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
          {TIER_META.map((m) => (
            <path key={m.key} d={toPath(m.key)} fill="none" stroke={m.color} strokeWidth={m.key === "main" ? 2 : 1.1} opacity={m.key === "main" ? 1 : 0.8} vectorEffect="non-scaling-stroke" />
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
