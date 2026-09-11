"use client";

import { memo } from "react";

/** 迷你走势图（retro #9）：价格序列自绘 SVG polyline，零依赖。
 * 2026-09-07 起自选列表喂的是当日分时价格序列（period=minute）；
 * 颜色按涨跌红涨绿跌——传入 up（当日涨跌 vs 昨收）优先，缺省按序列首尾
 * 比较（分时下即相对开盘方向，与涨跌幅列口径不同时的兜底）。无数据灰色占位。
 *
 * P1-1（2026-09-11）：包 `memo`。本组件在自选列表里**每行一个**，而行情 3s
 * 一 tick 会让 workbench 整体重渲染 ⇒ 原实现每 tick 对每行重算 min/max/pts
 * （成本线性于自选数量）。props 全是原始值 + `closes` 引用来自 spark 缓存
 * （`sparkBySymbol` 是 useMemo 派生的 Map），引用稳定 ⇒ memo 真正生效。
 * ⚠️ 调用方**不要**写 `closes={x ?? []}`——每次渲染新建空数组会让 memo 失效，
 * 无数据时请传下面这个共享常量。
 */

/** 无数据时的共享空序列（引用恒定，保证 memo 生效）。 */
export const NO_CLOSES: number[] = [];

interface Props {
  closes: number[];
  width?: number;
  height?: number;
  /** 定色基准（可选）：true=红（涨），false=绿（跌）。未传时按 closes 首尾比较。 */
  up?: boolean;
}

export const Sparkline = memo(function Sparkline({ closes, width = 52, height = 24, up }: Props) {
  if (!closes || closes.length < 2) {
    return <div className="text-center text-[9px] text-zinc-600 dark:text-zinc-400" style={{ width, height }}>--</div>;
  }
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || 1;
  const step = width / (closes.length - 1);
  const pts = closes
    .map((c, i) => `${(i * step).toFixed(1)},${(height - 2 - ((c - min) / span) * (height - 4)).toFixed(1)}`)
    .join(" ");
  const rising = up ?? closes[closes.length - 1] >= closes[0];
  const color = rising ? "#f43f5e" : "#10b981"; // 红涨绿跌

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden className="inline-block">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
});
