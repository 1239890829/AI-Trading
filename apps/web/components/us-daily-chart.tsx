"use client";

import {useState} from "react";
import type {UsDailyPoint} from "@/lib/api/us-assets";

function validPoints(points: UsDailyPoint[]) {
  return points.length <= 120 && points.every((row, index) => /^\d{4}-\d{2}-\d{2}$/.test(row.date)
    && Number.isFinite(Date.parse(`${row.date}T00:00:00Z`))
    && new Date(`${row.date}T00:00:00Z`).toISOString().slice(0, 10) === row.date
    && (index === 0 || row.date > points[index - 1].date)
    && [row.open, row.high, row.low, row.close].every(value => Number.isFinite(value) && value > 0)
    && row.high >= Math.max(row.open, row.close, row.low) && row.low <= Math.min(row.open, row.close)
    && Number.isFinite(row.volume_shares) && row.volume_shares >= 0);
}
function price(value: number) {return value.toLocaleString("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 4});}
function axis(value: number) {return value >= 1_000_000 ? value.toExponential(2) : value.toFixed(value < 1 ? 4 : 2);}

/** Source daily OHLC only. Range/hover/keyboard never request or manufacture bars. */
export function UsDailyChart({points, symbol}: {points: UsDailyPoint[]; symbol: string}) {
  const [count, setCount] = useState<20 | 60 | 120>(60);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  if (!points.length) return <div className="asset-chart-empty" role="status">暂无来源日K，不以当前报价补成历史曲线。</div>;
  if (!validPoints(points)) return <div className="asset-chart-empty" role="alert">日K序列包含无效或重复数据，已停止绘制。</div>;
  const rows = points.slice(-count), selectedIndex = selectedDate ? rows.findIndex(row => row.date === selectedDate) : -1;
  const selected = selectedIndex < 0 ? rows.length - 1 : selectedIndex, point = rows[selected];
  const low = Math.min(...rows.map(row => row.low)), high = Math.max(...rows.map(row => row.high));
  const padding = Math.max((high - low) * .08, high * .0005, .0001), min = low - padding, max = high + padding;
  const x = (index: number) => 96 + index / Math.max(1, rows.length - 1) * 624;
  const y = (value: number) => 12 + (max - value) / (max - min) * 140;
  const body = Math.min(10, Math.max(1.5, 624 / Math.max(1, rows.length) * .62));
  const maxVolume = Math.max(...rows.map(row => row.volume_shares), 1);
  function selectAt(clientX: number, rect: DOMRect) {
    const scale = Math.min(rect.width / 744, rect.height / 220);
    if (!Number.isFinite(clientX) || !Number.isFinite(scale) || scale <= 0) return;
    const position = (clientX - rect.left - (rect.width - 744 * scale) / 2) / scale;
    const index = Math.max(0, Math.min(rows.length - 1, Math.round((position - 96) / 624 * (rows.length - 1))));
    setSelectedDate(rows[index].date);
  }
  return <div className="asset-nav-chart asset-us-daily-chart">
    <div className="asset-chart-toolbar"><div className="asset-segments" aria-label="美股日K范围">{([20, 60, 120] as const).map(value => <button key={value} type="button" aria-pressed={count === value} onClick={() => setCount(value)}>近{value}根</button>)}</div><span className="asset-note">{rows.length} 根 · 原始非复权 · USD</span></div>
    <output className="asset-note" aria-live="polite">{point.date} · 开 {price(point.open)} · 高 {price(point.high)} · 低 {price(point.low)} · 收 <strong>{price(point.close)}</strong> · 量 {point.volume_shares.toLocaleString("zh-CN")} 股</output>
    <svg viewBox="0 0 744 220" role="img" aria-label={`${symbol}原始非复权日K，${rows[0].date}至${rows.at(-1)!.date}`} onPointerMove={event => selectAt(event.clientX, event.currentTarget.getBoundingClientRect())}>
      {[max, (max + min) / 2, min].map(value => <g key={value}><line x1="96" x2="720" y1={y(value)} y2={y(value)} className="asset-chart-grid"/><text x="88" y={y(value) + 4} textAnchor="end">{axis(value)}</text></g>)}
      {rows.map((row, index) => {
        const color = row.close > row.open ? "var(--tick-up-from)" : row.close < row.open ? "var(--tick-down-from)" : "var(--ui-muted)";
        const height = Math.max(1, Math.abs(y(row.open) - y(row.close)));
        const volume = row.volume_shares / maxVolume * 28;
        return <g key={row.date}><line x1={x(index)} x2={x(index)} y1={y(row.high)} y2={y(row.low)} stroke={color}/><rect x={x(index) - body / 2} y={Math.min(y(row.open), y(row.close))} width={body} height={height} fill={color}/><rect x={x(index) - body / 2} y={194 - volume} width={body} height={volume} fill={color} opacity=".5"/></g>;
      })}
      <line x1={x(selected)} x2={x(selected)} y1="8" y2="196" className="asset-chart-cursor"/>
      <text x="96" y="162">量（股）</text><text x="96" y="216">{rows[0].date}</text><text x="720" y="216" textAnchor="end">{rows.at(-1)!.date}</text>
    </svg>
    <input type="range" min={0} max={rows.length - 1} value={selected} onChange={event => setSelectedDate(rows[Number(event.target.value)].date)} aria-label="选择美股日K日期" aria-valuetext={`${point.date}，收盘${point.close}美元，成交量${point.volume_shares}股`}/>
    <p className="asset-note">纽约交易日；未复权价格，不代表含分红或拆股调整后的投资回报。</p>
  </div>;
}
