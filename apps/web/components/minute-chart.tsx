"use client";

import { useEffect, useMemo, useRef } from "react";
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
 * 当日分时图（docs/minute-chart-plan.md 模块 1+2+P1，2026-08-30）。
 *
 * 坐标系：以昨收为中心对称展开（涨跌停贴边、横盘日 0.5% 地板防抖）；
 * 右轴绝对价格、左轴涨跌幅（隐藏 % 序列承载）；昨收虚线基准。
 * 曲线：价格（面积）+ 均价线（黄）+ 上证叠加（紫虚线，左轴 % 归一化）+ 量能副图（红涨绿跌）。
 * 量比（近似口径）：点 i 量比 = 当日累计量_i / (昨日全天量 × 已开市分钟/240)；
 * 昨日量由日 K 倒数第二根提供（bars[-1] 在盘中是今日实时 bar，休市日是分时日本身，
 * 两种场景下 bars[-2] 都恰好是"分时日的上一交易日"）。分子分母同为股，单位已实测一致。
 * 交互：十字光标浮层（触摸同源），ref 直改 DOM 不走 React state。
 * prevClose 缺失时整体降级为库默认自适应坐标 + 浮层隐藏涨跌幅，绝不臆造基准。
 */

const UP = "#ef4444"; // 中国惯例红涨
const DOWN = "#10b981"; // 绿跌
const FLAT = "rgba(161,161,170,0.6)";
const BJ_OFFSET = 8 * 3600;

/** 已开市交易分钟数（11:30-13:00 午休不计），clamp 到 [1,240]。 */
function tradingMinutesElapsed(bjIso: string): number {
  const hhmm = bjIso.slice(11, 16);
  const mins = Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));
  const am = Math.min(Math.max(mins - 570, 0), 120); // 09:30 起
  const pm = Math.min(Math.max(mins - 780, 0), 120); // 13:00 起
  return Math.min(Math.max(am + pm, 1), 240);
}

function fmtPct(v: number | null): string {
  return v === null ? "--" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function MinuteChart({
  points,
  prevClose,
  yesterdayVol,
  index,
  className,
}: {
  points: P[];
  prevClose?: number | null;
  yesterdayVol?: number | null;
  index?: { points: P[]; prevClose: number } | null;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const badgeRef = useRef<HTMLDivElement>(null);

  // 角标数据：当前量比 + 上证涨跌幅（从最后一点派生，纯计算不走请求）
  const badges = useMemo(() => {
    const last = points[points.length - 1];
    let lb: number | null = null;
    if (last && yesterdayVol && yesterdayVol > 0 && last.cum_volume) {
      const elapsed = tradingMinutesElapsed(new Date(new Date(last.ts).getTime() + 8 * 3600 * 1000).toISOString());
      lb = last.cum_volume / (yesterdayVol * (elapsed / 240));
    }
    let idxPct: number | null = null;
    if (index && index.points.length > 0) {
      const lastIdx = index.points[index.points.length - 1];
      idxPct = ((lastIdx.price - index.prevClose) / index.prevClose) * 100;
    }
    return { lb, idxPct };
  }, [points, yesterdayVol, index]);

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

    // X 轴时基：LHC 对 unix 时间戳按 UTC 墙钟渲染，把"北京墙上时刻"编码为
    // 伪 UTC（utc_ts + 8h），X 轴才显示 09:30-15:00。crosshair param.time 同时基。
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
      let hi = -Infinity;
      let lo = Infinity;
      for (const p of points) {
        if (p.price > hi) hi = p.price;
        if (p.price < lo) lo = p.price;
      }
      const half = Math.max(Math.abs(hi - prevClose!), Math.abs(lo - prevClose!), prevClose! * 0.005);
      series.applyOptions({
        autoscaleInfoProvider: () => ({
          priceRange: { minValue: Math.max(prevClose! - half, 0), maxValue: prevClose! + half },
        }),
      });
      series.createPriceLine({
        price: prevClose!,
        color: "rgba(161,161,170,0.7)",
        lineWidth: 1,
        lineStyle: 2, // dotted
        axisLabelVisible: true,
        title: "昨收",
      });

      // ---- 左轴涨跌幅（隐藏 % 序列）----
      const pctSeries = chart.addLineSeries({
        priceScaleId: "left",
        visible: false,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        priceFormat: { type: "percent", precision: 2, minMove: 0.01 },
      });
      const halfPct = (half / prevClose!) * 100;
      pctSeries.setData(points.map<LineData>((p) => ({ time: toTime(p), value: ((p.price - prevClose!) / prevClose!) * 100 })));
      pctSeries.applyOptions({
        autoscaleInfoProvider: () => ({
          priceRange: { minValue: -halfPct, maxValue: halfPct },
        }),
      });

      // ---- 大盘叠加：上证归一化 % 曲线（可见，左轴同刻度）----
      if (index && index.points.length > 0) {
        const idxSeries = chart.addLineSeries({
          priceScaleId: "left",
          color: "rgba(167,139,250,0.85)",
          lineWidth: 1,
          lineStyle: 3, // dashed
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          priceFormat: { type: "percent", precision: 2, minMove: 0.01 },
        });
        idxSeries.setData(
          index.points.map<LineData>((p) => ({
            time: toTime(p),
            value: ((p.price - index.prevClose) / index.prevClose) * 100,
          }))
        );
        // 叠加曲线不得撑破个股的对称区间：钳制到 ±halfPct 视觉带内
        idxSeries.applyOptions({
          autoscaleInfoProvider: () => ({
            priceRange: { minValue: -halfPct, maxValue: halfPct },
          }),
        });
      }
    }

    // ---- 均价线（黄）----
    const avgPoints = points.filter((p) => p.avg != null);
    if (avgPoints.length > 0) {
      const avgSeries = chart.addLineSeries({
        color: "#eab308",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      avgSeries.setData(avgPoints.map((p) => ({ time: toTime(p), value: p.avg! })));
    }

    // ---- 量能副图：红涨绿跌 ----
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

    // ---- 十字光标浮层 ----
    const tooltip = tipRef.current;
    const onMove = (param: { time?: Time; point?: { x: number; y: number } }) => {
      if (!tooltip) return;
      if (!param.time || !param.point) {
        tooltip.style.opacity = "0";
        return;
      }
      // param.time 是编码后的伪 UTC（真实 ts + 8h，见 toTime）；
      // 匹配数据点必须先减回 8h 还原真实时基——否则所有点与光标恒差 8h，
      // "最近点"永远收敛到最后一根（收盘价），浮层每个位置都显示同一条数据。
      const sec = (param.time as unknown as number) * 1000 - BJ_OFFSET * 1000;
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
      const bjIso = new Date(new Date(d.ts).getTime() + 8 * 3600 * 1000).toISOString();
      const changePct = hasBase ? ((d.price - prevClose!) / prevClose!) * 100 : null;
      const avgDevPct = d.avg != null && d.avg > 0 ? ((d.price - d.avg) / d.avg) * 100 : null;
      const lb =
        yesterdayVol && yesterdayVol > 0 && d.cum_volume
          ? d.cum_volume / (yesterdayVol * (tradingMinutesElapsed(bjIso) / 240))
          : null;
      const minuteAmount =
        d.cum_amount != null && bestDiff >= 0
          ? (() => {
              const i = points.indexOf(d);
              const prev = i > 0 ? points[i - 1].cum_amount : null;
              return prev != null ? (d.cum_amount! - prev) / 1e4 : null; // 万
            })()
          : null;
      const pctCls = (v: number | null) => (v == null ? "" : v > 0 ? "text-red-500" : v < 0 ? "text-emerald-500" : "text-zinc-400");
      const lbCls = lb == null ? "" : lb >= 1.5 ? "text-red-500" : lb >= 0.8 ? "text-amber-500" : "text-sky-500";
      tooltip.innerHTML = `
        <div class="font-mono text-[11px] text-zinc-400">${bjIso.slice(11, 16)}</div>
        <div class="flex items-baseline gap-2"><span class="font-mono text-sm font-semibold tabular-nums">${d.price.toFixed(2)}</span>
        <span class="font-mono text-[11px] tabular-nums ${pctCls(changePct)}">${fmtPct(changePct)}</span></div>
        <div class="mt-0.5 grid grid-cols-[auto,1fr] gap-x-2 gap-y-0.5 text-[11px] tabular-nums">
          <span class="text-zinc-500">均价</span><span class="font-mono text-amber-500">${d.avg != null ? d.avg.toFixed(2) : "--"} <span class="${pctCls(avgDevPct)}">${avgDevPct != null ? fmtPct(avgDevPct) : ""}</span></span>
          <span class="text-zinc-500">量比</span><span class="font-mono ${lbCls}">${lb != null ? lb.toFixed(2) : "--"}</span>
          <span class="text-zinc-500">分钟量</span><span class="font-mono">${d.volume != null ? Math.round(d.volume / 100).toLocaleString() : "--"} 手</span>
          <span class="text-zinc-500">分钟额</span><span class="font-mono">${minuteAmount != null ? minuteAmount.toFixed(0) + " 万" : "--"}</span>
          <span class="text-zinc-500">累计额</span><span class="font-mono">${d.cum_amount != null ? (d.cum_amount / 1e8).toFixed(2) + " 亿" : "--"}</span>
        </div>`;
      tooltip.style.opacity = "1";
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
  }, [points, prevClose, yesterdayVol, index]);

  return (
    <div className="relative h-full w-full">
      <div ref={ref} className={`h-full w-full ${className ?? ""}`} />
      {/* 角标：量比 + 上证叠加图例 */}
      <div ref={badgeRef} className="pointer-events-none absolute right-2 top-1.5 z-10 flex items-center gap-2 text-[11px]">
        {badges.lb != null && (
          <span
            className={`rounded border px-1.5 py-0.5 font-mono tabular-nums ${
              badges.lb >= 1.5
                ? "border-red-500/40 bg-red-500/10 text-red-500"
                : badges.lb >= 0.8
                  ? "border-amber-500/40 bg-amber-500/10 text-amber-500"
                  : "border-sky-500/40 bg-sky-500/10 text-sky-500"
            }`}
            title="量比（近似）= 当日累计量 / (昨日全天量 × 已开市时间占比)；≥1.5 放量"
          >
            量比 {badges.lb.toFixed(2)}
          </span>
        )}
        {badges.idxPct != null && (
          <span className="rounded border border-violet-500/40 bg-violet-500/10 px-1.5 py-0.5 font-mono tabular-nums text-violet-400" title="上证指数叠加（左轴 %）">
            上证 {fmtPct(badges.idxPct)}
          </span>
        )}
      </div>
      <div
        ref={tipRef}
        className="pointer-events-none absolute left-0 top-0 z-10 min-w-[150px] rounded-lg border border-zinc-200 bg-white/95 px-2.5 py-1.5 opacity-0 shadow-sm transition-opacity dark:border-zinc-700 dark:bg-zinc-900/95"
      />
    </div>
  );
}
