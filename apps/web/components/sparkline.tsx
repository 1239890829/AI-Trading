"use client";

/** 迷你走势图（retro #9）：价格序列自绘 SVG polyline，零依赖。
 * 2026-09-07 起自选列表喂的是当日分时价格序列（period=minute）；
 * 颜色按涨跌红涨绿跌——传入 up（当日涨跌 vs 昨收）优先，缺省按序列首尾
 * 比较（分时下即相对开盘方向，与涨跌幅列口径不同时的兜底）。无数据灰色占位。 */

interface Props {
  closes: number[];
  width?: number;
  height?: number;
  /** 定色基准（可选）：true=红（涨），false=绿（跌）。未传时按 closes 首尾比较。 */
  up?: boolean;
}

export function Sparkline({ closes, width = 52, height = 24, up }: Props) {
  if (!closes || closes.length < 2) {
    return <div className="text-center text-[9px] text-zinc-600" style={{ width, height }}>--</div>;
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
}
