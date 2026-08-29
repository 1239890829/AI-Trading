"use client";

import { useEffect, useRef, useState } from "react";
import { CandlestickData, createChart, HistogramData, IChartApi, LineData, SeriesMarker, Time } from "lightweight-charts";
import { calcEMA } from "@/lib/technical-analysis";
import type { Kline } from "@/types/market";

interface Props {
  bars: Kline[];
  className?: string;
  lhbDates?: { date: string; note?: string }[];
}

type Indicators = { ma5: boolean; ma10: boolean; ma20: boolean; ma30: boolean; vol: boolean; macd: boolean; bs: boolean };

const MA_DEFS: [keyof Indicators, number, string][] = [
  ["ma5", 5, "#facc15"],
  ["ma10", 10, "#38bdf8"],
  ["ma20", 20, "#c084fc"],
  ["ma30", 30, "#fb923c"],
];

function calcMA(closes: number[], n: number): (number | null)[] {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i];
    if (i >= n) sum -= closes[i - n];
    out.push(i >= n - 1 ? +(sum / n).toFixed(2) : null);
  }
  return out;
}

/** K 线图（专业版）：均线 MA5/10/20/30、成交量副图、金叉死叉技术信号、龙虎榜日标记、指标开关。 */
export function KlineChartPro({ bars, className, lhbDates }: { bars: Kline[]; className?: string; lhbDates?: { date: string; note?: string }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [ind, setInd] = useState<Indicators>({ ma5: true, ma10: true, ma20: true, ma30: false, vol: true, macd: false, bs: true });

  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a1a1aa" },
      grid: { vertLines: { color: "rgba(120,120,130,0.10)" }, horzLines: { color: "rgba(120,120,130,0.10)" } },
      timeScale: { timeVisible: false, borderVisible: false },
      rightPriceScale: { borderVisible: false },
    });
    const candle = chart.addCandlestickSeries({
      upColor: "#f43f5e", downColor: "#10b981", borderUpColor: "#f43f5e", borderDownColor: "#10b981",
      wickUpColor: "#f43f5e", wickDownColor: "#10b981",
    });
    const data: CandlestickData[] = bars
      .filter((b) => b.open != null && b.close != null)
      .map((b) => ({ time: b.ts.slice(0, 10) as Time, open: b.open as number, high: (b.high ?? b.open) as number, low: (b.low ?? b.open) as number, close: b.close as number }));
    candle.setData(data);

    const closes = data.map((d) => d.close);
    const times = data.map((d) => d.time);

    // 均线
    for (const [key, n, color] of MA_DEFS) {
      if (!ind[key]) continue;
      const ma = calcMA(closes, n);
      const line: LineData[] = ma
        .map((v, i) => ({ time: times[i], value: v }))
        .filter((x) => x.value != null) as LineData[];
      chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(line);
    }

    // 成交量副图
    if (ind.vol) {
      const vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
      const vd: HistogramData[] = bars
        .filter((b) => b.volume != null)
        .map((b) => ({ time: b.ts.slice(0, 10) as Time, value: b.volume as number, color: (b.close ?? 0) >= (b.open ?? 0) ? "rgba(244,63,94,0.45)" : "rgba(16,185,129,0.45)" }));
      vol.setData(vd);
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    }

    const markers: SeriesMarker<Time>[] = [];
    // 技术信号：MA5 上穿/下穿 MA10（金叉/死叉）。真实 BS 点=模拟交易成交记录，Phase 6 接入
    if (ind.bs) {
      const ma5 = calcMA(closes, 5);
      const ma10 = calcMA(closes, 10);
      for (let i = 1; i < data.length; i++) {
        const a0 = ma5[i - 1], b0 = ma10[i - 1], a1 = ma5[i], b1 = ma10[i];
        if (a0 == null || b0 == null || a1 == null || b1 == null) continue;
        if (a0 <= b0 && a1 > b1) markers.push({ time: times[i], position: "belowBar", color: "#f43f5e", shape: "arrowUp", text: "金叉" });
        else if (a0 >= b0 && a1 < b1) markers.push({ time: times[i], position: "aboveBar", color: "#10b981", shape: "arrowDown", text: "死叉" });
      }
    }

    // 龙虎榜日标记（紫点，hover 显示上榜原因）
    for (const lhb of lhbDates ?? []) {
      if (!markers.some((mk) => mk.time === (lhb.date as Time))) {
        markers.push({ time: lhb.date as Time, position: "aboveBar", color: "#a855f7", shape: "circle", text: "榜" });
      }
    }
    markers.sort((a, b) => String(a.time).localeCompare(String(b.time)));
    candle.setMarkers(markers.slice(-80));

    // 默认聚焦最近 20 根（可自行缩放查看全部 120 根）
    chart.timeScale().setVisibleLogicalRange({ from: data.length - 20, to: data.length + 2 });
    return () => chart.remove();
  }, [bars, ind, lhbDates]);

  const toggles: [keyof Indicators, string, string?][] = [
    ["ma5", "MA5", "#facc15"],
    ["ma10", "MA10", "#38bdf8"],
    ["ma20", "MA20", "#c084fc"],
    ["ma30", "MA30", "#fb923c"],
    ["vol", "成交量"],
    ["macd", "MACD"],
    ["bs", "技术信号"],
  ];

  return (
    <div className={`flex min-h-0 flex-col ${className ?? ""}`}>
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-zinc-100 px-3 py-1.5 text-xs dark:border-zinc-800/60">
        <span className="text-zinc-400">指标：</span>
        {toggles.map(([key, label, color]) => (
          <button
            key={key}
            onClick={() => setInd((p) => ({ ...p, [key]: !p[key] }))}
            className={`rounded px-1.5 py-0.5 font-mono transition-colors ${
              ind[key] ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"
            }`}
            style={ind[key] && color ? { color } : undefined}
          >
            {label}
          </button>
        ))}
        <span className="ml-auto text-[10px] text-zinc-500">金叉/死叉为 MA5×MA10 技术信号 · 紫[榜]=龙虎榜日 · 真实BS点随模拟交易上线</span>
      </div>
      <div ref={ref} className={`min-h-0 w-full flex-1 ${className ?? ""}`} />
    </div>
  );
}
