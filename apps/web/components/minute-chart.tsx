"use client";

import { useEffect, useRef } from "react";
import { createChart, IChartApi } from "lightweight-charts";
import type { MinutePoint as P } from "@/lib/api";

/** 当日分时图（面积线）。数据来自 /api/minute-line（腾讯 1 分钟）。 */
export function MinuteChart({ points, className }: { points: P[]; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || points.length === 0) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a1a1aa" },
      grid: {
        vertLines: { color: "rgba(120,120,130,0.12)" },
        horzLines: { color: "rgba(120,120,130,0.12)" },
      },
      timeScale: { timeVisible: true, secondsVisible: false, borderVisible: false },
      rightPriceScale: { borderVisible: false },
    });
    const series = chart.addAreaSeries({
      lineColor: "#f43f5e",
      topColor: "rgba(244,63,94,0.28)",
      bottomColor: "rgba(244,63,94,0.02)",
      lineWidth: 2,
      priceLineVisible: false,
    });
    series.setData(points.map((p) => ({ time: Math.floor(new Date(p.ts).getTime() / 1000) as never, value: p.price })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points]);

  return <div ref={ref} className={`w-full ${className ?? "h-full"}`} />;
}
