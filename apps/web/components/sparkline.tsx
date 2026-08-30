"use client";

/** 迷你走势图（retro #9）：近 N 日收盘自绘 SVG polyline，零依赖。
 * 颜色按区间涨跌红涨绿跌；无数据时显示灰色占位。 */

interface Props {
  closes: number[];
  width?: number;
  height?: number;
}

export function Sparkline({ closes, width = 64, height = 24 }: Props) {
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
  const up = closes[closes.length - 1] >= closes[0];
  const color = up ? "#f43f5e" : "#10b981"; // 红涨绿跌

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden className="inline-block">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
