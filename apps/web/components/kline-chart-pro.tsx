"use client";

import { useEffect, useRef, useState } from "react";
import { CandlestickData, createChart, HistogramData, IChartApi, LineData, LineStyle, SeriesMarker, Time } from "lightweight-charts";
import { calcEMA } from "@/lib/technical-analysis";
import type { Kline } from "@/types/market";

interface Props {
  bars: Kline[];
  className?: string;
  tradeMarks?: { date: string; side: string; price: number; quantity: number }[];
  /** 当前持仓摊薄成本，>0 时在主图画虚线 */
  costPrice?: number | null;
}

type Indicators = { ma5: boolean; ma10: boolean; ma20: boolean; ma60: boolean; vol: boolean; macd: boolean; boll: boolean; amt: boolean; bs: boolean };

const MA_DEFS: [keyof Indicators, number, string][] = [
  ["ma5", 5, "#facc15"],
  ["ma10", 10, "#38bdf8"],
  ["ma20", 20, "#c084fc"],
  ["ma60", 60, "#fb923c"],
];

function calcMA(values: number[], n: number): (number | null)[] {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= n) sum -= values[i - n];
    out.push(i >= n - 1 ? +(sum / n).toFixed(3) : null);
  }
  return out;
}

function calcBOLL(closes: number[], n = 20, k = 2) {
  const ma = calcMA(closes, n);
  const up: (number | null)[] = [];
  const low: (number | null)[] = [];
  for (let i = 0; i < closes.length; i++) {
    if (ma[i] == null) {
      up.push(null);
      low.push(null);
      continue;
    }
    const win = closes.slice(i - n + 1, i + 1);
    const mean = ma[i] as number;
    const sd = Math.sqrt(win.reduce((acc, v) => acc + (v - mean) ** 2, 0) / n);
    up.push(+(mean + k * sd).toFixed(3));
    low.push(+(mean - k * sd).toFixed(3));
  }
  return { ma, up, low };
}

/** K 线图（专业版）：MA5/10/20/60、BOLL(20,2)、成交量+均量线(5/10/20)、MACD/成交额副图、
 * 指标开关、缩放按钮。默认聚焦最近 20 根。 */
export function KlineChartPro({ bars, className, tradeMarks, costPrice }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [ind, setInd] = useState<Indicators>({ ma5: true, ma10: true, ma20: true, ma60: true, vol: true, macd: false, boll: false, amt: false, bs: true });

  useEffect(() => {
    if (!containerRef.current || bars.length === 0) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a1a1aa" },
      grid: { vertLines: { color: "rgba(120,120,130,0.10)" }, horzLines: { color: "rgba(120,120,130,0.10)" } },
      timeScale: { timeVisible: false, borderVisible: false },
      rightPriceScale: { borderVisible: false },
    });
    chartRef.current = chart;
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

    // 主图均线
    for (const [key, n, color] of MA_DEFS) {
      if (!ind[key]) continue;
      const line = calcMA(closes, n)
        .map((v, i) => ({ time: times[i], value: v }))
        .filter((x) => x.value != null) as LineData[];
      chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(line);
    }

    // BOLL(20,2)
    if (ind.boll) {
      const boll = calcBOLL(closes);
      const mk = (vals: (number | null)[]): LineData[] =>
        vals.map((v, i) => ({ time: times[i], value: v })).filter((x) => x.value != null) as LineData[];
      chart.addLineSeries({ color: "#e879f9", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(mk(boll.up));
      chart.addLineSeries({ color: "#e879f9", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(mk(boll.low));
      chart.addLineSeries({ color: "rgba(161,161,170,0.7)", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(mk(boll.ma));
    }

    // 成交量副图 + 均量线 5/10/20
    if (ind.vol) {
      const vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
      const vd: HistogramData[] = bars
        .filter((b) => b.volume != null)
        .map((b) => ({ time: b.ts.slice(0, 10) as Time, value: b.volume as number, color: (b.close ?? 0) >= (b.open ?? 0) ? "rgba(244,63,94,0.45)" : "rgba(16,185,129,0.45)" }));
      vol.setData(vd);
      const vols = vd.map((d) => d.value as number);
      const volTimes = vd.map((d) => d.time);
      for (const [n, color] of [[5, "#facc15"], [10, "#38bdf8"], [20, "#c084fc"]] as const) {
        const vma = calcMA(vols, n);
        const line = vma
          .map((v, i) => ({ time: volTimes[i], value: v }))
          .filter((x) => x.value != null) as LineData[];
        chart.addLineSeries({ priceScaleId: "vol", color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(line);
      }
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    }

    // 成交额副图
    if (ind.amt) {
      const amtSeries = chart.addHistogramSeries({ priceScaleId: "amt", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
      amtSeries.setData(
        bars
          .filter((b) => b.amount != null)
          .map((b) => ({ time: b.ts.slice(0, 10) as Time, value: b.amount as number, color: (b.close ?? 0) >= (b.open ?? 0) ? "rgba(244,63,94,0.45)" : "rgba(16,185,129,0.45)" }))
      );
      chart.priceScale("amt").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    }

    // MACD 副图
    if (ind.macd) {
      const ema12 = calcEMA(closes, 12);
      const ema26 = calcEMA(closes, 26);
      const dif = closes.map((_, i) => ema12[i] - ema26[i]);
      const dea = calcEMA(dif, 9);
      const hist = chart.addHistogramSeries({ priceScaleId: "macd", priceLineVisible: false, lastValueVisible: false });
      hist.setData(data.map((d, i) => ({ time: d.time, value: (dif[i] - dea[i]) * 2, color: dif[i] > dea[i] ? "rgba(244,63,94,0.6)" : "rgba(16,185,129,0.6)" })));
      chart.addLineSeries({ priceScaleId: "macd", color: "#facc15", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(data.map((d, i) => ({ time: d.time, value: dif[i] })));
      chart.addLineSeries({ priceScaleId: "macd", color: "#38bdf8", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(data.map((d, i) => ({ time: d.time, value: dea[i] })));
      chart.priceScale("macd").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    }

    const markers: SeriesMarker<Time>[] = [];
    // 真实 B/S 点：模拟交易成交记录（B=买入日 红上箭头，S=卖出日 绿下箭头）
    if (ind.bs) {
      for (const t of tradeMarks ?? []) {
        if (!bars.some((b) => b.ts.slice(0, 10) === t.date)) continue;
        markers.push({
          time: t.date as Time,
          position: t.side === "buy" ? "belowBar" : "aboveBar",
          color: t.side === "buy" ? "#f43f5e" : "#10b981",
          shape: t.side === "buy" ? "arrowUp" : "arrowDown",
          text: `${t.side === "buy" ? "B" : "S"} ${t.quantity}股`,
        });
      }
      markers.sort((a, b) => String(a.time).localeCompare(String(b.time)));
      candle.setMarkers(markers);
    }

    // 持仓成本线：模拟交易摊薄成本（无持仓则不画）
    if (costPrice != null && costPrice > 0) {
      candle.createPriceLine({
        price: costPrice,
        color: "#fbbf24",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "成本",
      });
    }

    // 默认聚焦最近 20 根
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, data.length - 20), to: data.length + 2 });
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [bars, ind, tradeMarks, costPrice]);

  const toggles: [keyof Indicators, string, string?][] = [
    ["ma5", "MA5", "#facc15"],
    ["ma10", "MA10", "#38bdf8"],
    ["ma20", "MA20", "#c084fc"],
    ["ma60", "MA60", "#fb923c"],
    ["vol", "成交量"],
    ["boll", "BOLL"],
    ["amt", "成交额"],
    ["macd", "MACD"],
    ["bs", "BS点"],
  ];

  function zoomTime(factor: number) {
    const ts = chartRef.current?.timeScale();
    if (!ts) return;
    const range = ts.getVisibleLogicalRange();
    if (!range) return;
    const center = (range.from + range.to) / 2;
    const half = ((range.to - range.from) / 2) * factor;
    ts.setVisibleLogicalRange({ from: center - half, to: center + half });
  }

  return (
    <div className={`relative flex min-h-0 flex-col ${className ?? ""}`}>
      <div className="absolute right-2 top-2 z-10 flex gap-1">
        <button onClick={() => zoomTime(0.7)} className="h-6 w-6 rounded border border-zinc-700 bg-zinc-900/80 text-xs text-zinc-300 hover:bg-zinc-800" aria-label="放大">＋</button>
        <button onClick={() => zoomTime(1.4)} className="h-6 w-6 rounded border border-zinc-700 bg-zinc-900/80 text-xs text-zinc-300 hover:bg-zinc-800" aria-label="缩小">−</button>
        <button
          onClick={() => {
            const n = bars.length;
            chartRef.current?.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 20), to: n + 2 });
          }}
          className="h-6 rounded border border-zinc-700 bg-zinc-900/80 px-1.5 text-[10px] text-zinc-300 hover:bg-zinc-800"
          aria-label="回到最近20日"
        >
          20D
        </button>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-zinc-100 px-3 py-1 text-[11px] dark:border-zinc-800/60">
        <span className="text-zinc-400">指标：</span>
        {toggles.map(([key, label, color]) => (
          <button
            key={key}
            onClick={() => setInd((p) => ({ ...p, [key]: !p[key] }))}
            className={`rounded px-1.5 py-0.5 font-mono transition-colors ${ind[key] ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"}`}
            style={ind[key] && color ? { color } : undefined}
          >
            {label}
          </button>
        ))}
        <span className="ml-auto text-[10px] text-zinc-500">金叉/死叉为 MA5×MA10 技术信号 · 紫[榜]=龙虎榜日 · B/S=模拟交易成交 · 黄虚线=持仓成本</span>
      </div>
      <div ref={containerRef} className={`min-h-0 w-full flex-1 ${className ?? ""}`} />
    </div>
  );
}
