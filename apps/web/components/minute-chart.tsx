"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  CrosshairMode,
  HistogramData,
  IChartApi,
  ISeriesApi,
  LineData,
  Time,
} from "lightweight-charts";
import type { MinutePoint as P } from "@/lib/api";

/**
 * 当日分时图（docs/minute-chart-plan.md 模块 1+2 落地，2026-08-30 重写）。
 *
 * 坐标系：以昨收为中心对称展开（涨跌停贴边、横盘日 0.5% 地板防抖）；
 * 右轴绝对价格、左轴涨跌幅（隐藏 % 序列承载）；昨收虚线基准。
 * 曲线：价格（面积）+ 均价线（黄，= 累计额/累计量，后端已算）+ 量能副图（红涨绿跌）。
 * 交互：十字光标浮层（鼠标/触摸统一走 LHC crosshair 事件），浮层用 ref 直改
 * DOM——mousemove 级频率不走 React state，避免整卡重渲染。
 *
 * prevClose 缺失时整体降级为库默认自适应坐标 + 浮层隐藏涨跌幅，绝不臆造基准。
 */

const UP = "#ef4444"; // 中国惯例红涨
const DOWN = "#10b981"; // 绿跌
const FLAT = "rgba(161,161,170,0.6)";

interface TipData {
  time: string;
  price: number;
  changePct: number | null;
  avg: number | null;
  avgDevPct: number | null;
  volHand: number | null;
  cumAmountYi: number | null;
}

function fmtPct(v: number | null): string {
  return v === null ? "--" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function MinuteChart({
  points,
  prevClose,
  className,
}: {
  points: P[];
  prevClose?: number | null;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || points.length === 0) return;
    const hasBase = prevClose != null && prevClose > 0;

    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a1a1aa" },
      grid: {
        vertLines: { color: "rgba(120,120,130,0.12)" },
        horzLines: { color: "rgba(120,120,130,0.12)" },
      },
      timeScale: { timeVisible: true, secondsVisible: false, borderVisible: false },
      rightPriceScale: { borderVisible: false },
      leftPriceScale: hasBase ? { visible: true, borderVisible: false } : { visible: false },
      crosshair: { mode: CrosshairMode.Normal },
    });

    // X 轴时基：LHC 对 unix 时间戳按 UTC 墙钟渲染，因此把"北京墙上时刻"编码为
    // 伪 UTC（utc_ts + 8h），X 轴才能显示 09:30-15:00 而非 01:30-07:30。
    // crosshair 返回的 param.time 与此同时基，查找/浮层时间换算保持一致。
    const BJ_OFFSET = 8 * 3600;
    const toTime = (p: P): Time => (Math.floor(new Date(p.ts).getTime() / 1000) + BJ_OFFSET) as never;

    // ---- 价格面积线：昨收锚定的对称区间 ----
    const series = chart.addAreaSeries({
      lineColor: "#f43f5e",
      topColor: "rgba(244,63,94,0.28)",
      bottomColor: "rgba(244,63,94,0.02)",
      lineWidth: 2,
      priceLineVisible: false,
    });
    series.setData(points.map((p) => ({ time: toTime(p), value: p.price })));

    if (hasBase) {
      // 对称 half：日内最大偏离与 0.5% 地板取大——涨停/跌停时曲线自然贴边，
      // 平淡走势时不让 0.2% 的波动撑满全屏（防抖地板）。
      let hi = -Infinity;
      let lo = Infinity;
      for (const p of points) {
        if (p.price > hi) hi = p.price;
        if (p.price < lo) lo = p.price;
      }
      const half = Math.max(Math.abs(hi - prevClose!), Math.abs(lo - prevClose!), prevClose! * 0.005);
      const lo0 = Math.max(prevClose! - half, 0);
      series.applyOptions({
        autoscaleInfoProvider: () => ({
          priceRange: { minValue: lo0, maxValue: prevClose! + half },
        }),
      });
      // 昨收基准虚线
      series.createPriceLine({
        price: prevClose!,
        color: "rgba(161,161,170,0.7)",
        lineWidth: 1,
        lineStyle: 2, // dotted
        axisLabelVisible: true,
        title: "昨收",
      });

      // ---- 左轴涨跌幅：隐藏 % 序列承载（与右轴同区间，刻度网格对齐）----
      const pctSeries = chart.addLineSeries({
        priceScaleId: "left",
        visible: false,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        priceFormat: { type: "percent", precision: 2, minMove: 0.01 },
      });
      const halfPct = (half / prevClose!) * 100;
      pctSeries.setData(
        points.map<LineData>((p) => ({ time: toTime(p), value: ((p.price - prevClose!) / prevClose!) * 100 }))
      );
      pctSeries.applyOptions({
        autoscaleInfoProvider: () => ({
          priceRange: { minValue: -halfPct, maxValue: halfPct },
        }),
      });
    }

    // ---- 均价线（黄）----
    const avgPoints = points.filter((p) => p.avg != null);
    let avgSeries: ISeriesApi<"Line"> | null = null;
    if (avgPoints.length > 0) {
      avgSeries = chart.addLineSeries({
        color: "#eab308",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      avgSeries.setData(avgPoints.map((p) => ({ time: toTime(p), value: p.avg! })));
    }

    // ---- 量能副图：红涨绿跌（本分钟价 vs 前一分钟价）----
    const vol = chart.addHistogramSeries({
      priceScaleId: "vol",
      priceFormat: { type: "volume" },
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const volData: HistogramData[] = [];
    for (let i = 0; i < points.length; i++) {
      const v = points[i].volume;
      if (v == null) continue;
      const prevP = i > 0 ? points[i - 1].price : null;
      const color =
        prevP == null || points[i].price === prevP ? FLAT : points[i].price > prevP ? "rgba(239,68,68,0.5)" : "rgba(16,185,129,0.5)";
      volData.push({ time: toTime(points[i]), value: v, color });
    }
    vol.setData(volData);
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });

    // ---- 十字光标浮层（ref 直改 DOM，不走 React state）----
    const tooltip = tipRef.current;
    const onMove = (param: { time?: Time; point?: { x: number; y: number } }) => {
      if (!tooltip) return;
      if (!param.time || !param.point) {
        tooltip.style.opacity = "0";
        return;
      }
      const sec = (param.time as unknown as number) * 1000;
      // 找最近的数据点（光标可能落在两点之间）
      let best: P | null = null;
      let bestDiff = Infinity;
      for (const p of points) {
        const d = Math.abs(new Date(p.ts).getTime() - sec);
        if (d < bestDiff) {
          bestDiff = d;
          best = p;
        }
      }
      if (!best) {
        tooltip.style.opacity = "0";
        return;
      }
      const d = best;
      const changePct = hasBase ? ((d.price - prevClose!) / prevClose!) * 100 : null;
      const avgDevPct = d.avg != null && d.avg > 0 ? ((d.price - d.avg) / d.avg) * 100 : null;
      const tip: TipData = {
        time: new Date(new Date(d.ts).getTime() + 8 * 3600 * 1000).toISOString().slice(11, 16), // UTC→北京时间
        price: d.price,
        changePct,
        avg: d.avg ?? null,
        avgDevPct,
        volHand: d.volume != null ? d.volume / 100 : null,
        cumAmountYi: d.cum_amount != null ? d.cum_amount / 1e8 : null,
      };
      const pctCls = (v: number | null) => (v == null ? "" : v > 0 ? "text-red-500" : v < 0 ? "text-emerald-500" : "text-zinc-400");
      tooltip.innerHTML = `
        <div class="font-mono text-[11px] text-zinc-400">${tip.time}</div>
        <div class="flex items-baseline gap-2"><span class="font-mono text-sm font-semibold tabular-nums">${tip.price.toFixed(2)}</span>
        <span class="font-mono text-[11px] tabular-nums ${pctCls(tip.changePct)}">${fmtPct(tip.changePct)}</span></div>
        <div class="mt-0.5 grid grid-cols-[auto,1fr] gap-x-2 gap-y-0.5 text-[11px] tabular-nums">
          <span class="text-zinc-500">均价</span><span class="font-mono text-amber-500">${tip.avg != null ? tip.avg.toFixed(2) : "--"} <span class="${pctCls(tip.avgDevPct)}">${tip.avgDevPct != null ? fmtPct(tip.avgDevPct) : ""}</span></span>
          <span class="text-zinc-500">分钟量</span><span class="font-mono">${tip.volHand != null ? Math.round(tip.volHand).toLocaleString() : "--"} 手</span>
          <span class="text-zinc-500">累计额</span><span class="font-mono">${tip.cumAmountYi != null ? tip.cumAmountYi.toFixed(2) + " 亿" : "--"}</span>
        </div>`;
      tooltip.style.opacity = "1";
      // 位置：光标右侧偏移；靠右半屏时翻到左侧防遮挡，并夹在容器内
      const box = ref.current!;
      const w = tooltip.offsetWidth || 150;
      const x = param.point.x + 14 + w > box.clientWidth ? Math.max(4, param.point.x - 14 - w) : param.point.x + 14;
      const y = Math.min(Math.max(4, param.point.y - 10), Math.max(4, box.clientHeight - tooltip.offsetHeight - 4));
      tooltip.style.left = `${x}px`;
      tooltip.style.top = `${y}px`;
    };

    const unsub = chart.subscribeCrosshairMove(onMove);
    chart.timeScale().fitContent();

    return () => {
      try {
        chart.unsubscribeCrosshairMove(onMove);
      } catch {}
      chart.remove();
      void unsub;
    };
  }, [points, prevClose]);

  return (
    <div className="relative h-full w-full">
      <div ref={ref} className={`h-full w-full ${className ?? ""}`} />
      <div
        ref={tipRef}
        className="pointer-events-none absolute left-0 top-0 z-10 min-w-[140px] rounded-lg border border-zinc-200 bg-white/95 px-2.5 py-1.5 opacity-0 shadow-sm transition-opacity dark:border-zinc-700 dark:bg-zinc-900/95"
      />
    </div>
  );
}
