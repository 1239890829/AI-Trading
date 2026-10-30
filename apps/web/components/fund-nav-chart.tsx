"use client";

import {useMemo, useState} from "react";
import type {FundNavPoint} from "@/lib/api/assets";

/** Published NAV only. Missing points split the line; no synthetic intraday marks. */
export function FundNavChart({points, currency}: {points: FundNavPoint[]; currency: string}) {
  const [basis, setBasis] = useState<"unit_nav" | "adj_nav">("unit_nav");
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const rows = useMemo(() => [...points].sort((a, b) => a.nav_date.localeCompare(b.nav_date)), [points]);
  const values = rows.map(row => row[basis]);
  const numeric = values.filter((value): value is number => value !== null && Number.isFinite(value));
  if (!rows.length || !numeric.length) return <div className="asset-chart-empty" role="status">{basis === "unit_nav" ? "单位净值" : "复权净值"}未返回，不绘制估算曲线。{basis === "adj_nav" ? <button className="bc-compact-button" onClick={() => setBasis("unit_nav")}>查看单位净值</button> : rows.some(row => row.adj_nav !== null) && <button className="bc-compact-button" onClick={() => setBasis("adj_nav")}>查看复权净值</button>}</div>;
  const low = Math.min(...numeric), high = Math.max(...numeric);
  const padding = Math.max((high - low) * 0.12, Math.abs(high) * 0.002, 0.0001);
  const min = low - padding, max = high + padding;
  const x = (index: number) => 70 + index / Math.max(1, rows.length - 1) * 650;
  const y = (value: number) => 16 + (max - value) / (max - min) * 178;
  const selectedIndex = selectedDate ? rows.findIndex(row => row.nav_date === selectedDate) : -1;
  const selected = selectedIndex < 0 ? rows.length - 1 : selectedIndex;
  const point = rows[selected];
  const path = values.map((value, index) => {
    if (value === null || !Number.isFinite(value)) return "";
    const previous = values[index - 1];
    const command = previous != null && Number.isFinite(previous) ? "L" : "M";
    return `${command}${x(index).toFixed(2)},${y(value).toFixed(2)}`;
  }).join(" ");
  const label = basis === "unit_nav" ? "单位净值" : "复权净值";
  const selectAt = (clientX: number, rect: DOMRect) => {
    // The default xMidYMid meet transform may leave horizontal space on wide
    // panes. Map through that transform instead of treating it as chart data.
    const scale = Math.min(rect.width / 744, rect.height / 220);
    if (!Number.isFinite(clientX) || !Number.isFinite(scale) || scale <= 0) return;
    const insetX = (rect.width - 744 * scale) / 2;
    const position = (clientX - rect.left - insetX) / scale;
    const index = Math.max(0, Math.min(rows.length - 1, Math.round((position - 70) / 650 * (rows.length - 1))));
    setSelectedDate(rows[index].nav_date);
  };
  return <div className="asset-nav-chart">
    <div className="asset-chart-toolbar">
      <div className="asset-segments" aria-label="净值口径">
        <button type="button" aria-pressed={basis === "unit_nav"} onClick={() => setBasis("unit_nav")}>单位净值</button>
        <button type="button" aria-pressed={basis === "adj_nav"} disabled={!rows.some(row => row.adj_nav !== null)} onClick={() => setBasis("adj_nav")}>复权净值</button>
      </div>
      <output aria-live="polite">{point.nav_date} <strong>{point[basis]?.toFixed(4) ?? "缺失"}</strong> {currency}</output>
    </div>
    <svg viewBox="0 0 744 220" role="img" aria-label={`${label}曲线，${rows[0].nav_date}至${rows.at(-1)!.nav_date}`} onPointerMove={event => selectAt(event.clientX, event.currentTarget.getBoundingClientRect())}>
      {[max, (min + max) / 2, min].map(value => <g key={value}><line x1="70" x2="720" y1={y(value)} y2={y(value)} className="asset-chart-grid"/><text x="63" y={y(value) + 4} textAnchor="end">{value.toFixed(4)}</text></g>)}
      <path d={path} className="asset-chart-line" fill="none"/>
      {point[basis] !== null && <><line x1={x(selected)} x2={x(selected)} y1="12" y2="194" className="asset-chart-cursor"/><circle cx={x(selected)} cy={y(point[basis]!)} r="3" className="asset-chart-point"/></>}
      <text x="70" y="216">{rows[0].nav_date}</text><text x="720" y="216" textAnchor="end">{rows.at(-1)!.nav_date}</text>
    </svg>
    <input type="range" min={0} max={rows.length - 1} value={selected} onChange={event => setSelectedDate(rows[Number(event.target.value)].nav_date)} aria-label="选择净值日期" aria-valuetext={`${point.nav_date}，${label}${point[basis] ?? "缺失"} ${currency}`} />
    <p className="asset-note">按官方净值日期展示；单位净值变化不等于含分红总回报。</p>
  </div>;
}
