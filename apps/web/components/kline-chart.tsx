"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickData,
  createChart,
  IChartApi,
  Time,
} from "lightweight-charts";
import type { Kline } from "@/types/market";

interface Props {
  bars: Kline[];
  height?: number;
  className?: string;
}

/** K 线图（lightweight-charts）。A 股配色：红涨绿跌。传 height 定高，或传 className="h-full" 随容器自适应。 */
export function KlineChart({ bars, height, className }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      height,
      autoSize: !height,
      layout: {
        background: { color: "transparent" },
        textColor: "#a1a1aa",
      },
      grid: {
        vertLines: { color: "rgba(120,120,130,0.12)" },
        horzLines: { color: "rgba(120,120,130,0.12)" },
      },
      timeScale: { timeVisible: false, borderVisible: false },
    });
    chartRef.current = chart;
    const series = chart.addCandlestickSeries({
      upColor: "#f43f5e",
      downColor: "#10b981",
      borderUpColor: "#f43f5e",
      borderDownColor: "#10b981",
      wickUpColor: "#f43f5e",
      wickDownColor: "#10b981",
    });

    const data: CandlestickData[] = bars
      .filter((b) => b.open != null && b.close != null)
      .map((b) => ({
        time: b.ts.slice(0, 10) as Time,
        open: b.open as number,
        high: (b.high ?? b.open) as number,
        low: (b.low ?? b.open) as number,
        close: b.close as number,
      }));
    series.setData(data);
    chart.timeScale().fitContent();

    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [bars, height]);

  return (
    <div
      ref={containerRef}
      className={`w-full ${className ?? ""}`}
      style={height ? { minHeight: height, height } : undefined}
    />
  );
}
