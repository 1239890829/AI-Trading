"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
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
import type { MinuteNewsEvent } from "@/lib/event-markers";

/**
 * 当日分时图（docs/minute-chart-plan.md 模块 1+2+P1，2026-08-30）。
 *
 * 坐标系：以昨收为中心对称展开（涨跌停贴边、横盘日 0.5% 地板防抖）；
 * 右轴绝对价格、左轴涨跌幅（隐藏 % 序列承载）；昨收虚线基准。
 * 曲线：价格（面积）+ 均价线（黄）+ 上证叠加（紫虚线，左轴 % 归一）+ 量能副图（红涨绿跌）。
 * 量比：精确口径（TDX 5 日同期基线）优先，缺失回退近似（昨日量 × 已开市分钟/240）。
 * 昨日量取日 K 倒数第二根提供（bars[-2] 在盘中是今日实时 bar，休市日是分时日本身，
 * 两种场景下 bars[-2] 都恰好是"分时日的上一交易日"）。分子分母同为股，单位已实测一致。
 * 交互：十字光标浮层（触摸同源），ref 直改 DOM 不走 React state。
 * prevClose 缺失时整体降级为库默认自适应坐标 + 浮层隐藏涨跌幅，绝不臆造基准。
 *
 * 创建/数据分离（2026-09-02 用户反馈"刷新闪烁"）：原实现 effect 依赖 points——
 * 每次行情 tick（WS 合成/60s 校准）整个 chart 销毁重建，闪烁且丢十字光标。
 * 现在 chart/series 只随低频配置（prevClose/叠加/竞价/基线/事件晚到）重建，
 * points 高频变化走 setData 全量原位重灌（不销毁图表、不闪）。⚠️ 不能用
 * series.update()：槽位以全天 whitespace 占位，series 最后时间点恒为 15:00，
 * update(当前分钟) 必抛 "Cannot update oldest data"（实测崩溃，见 fillAll 注释）。
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

/** 点 ts → 北京墙钟 HH:MM（槽位/游标通用）。 */
function bjHHMM(p: { ts: string }): string {
  return new Date(new Date(p.ts).getTime() + BJ_OFFSET * 1000).toISOString().slice(11, 16);
}

type SeriesBundle = {
  chart: IChartApi | null;
  price: ISeriesApi<"Area"> | null;
  pct: ISeriesApi<"Line"> | null;
  avg: ISeriesApi<"Line"> | null;
  vol: ISeriesApi<"Histogram"> | null;
  slots: number[];
  base0: number;
};

const emptyBundle = (): SeriesBundle => ({ chart: null, price: null, pct: null, avg: null, vol: null, slots: [], base0: 0 });

export function MinuteChart({
  points,
  prevClose,
  yesterdayVol,
  index,
  auction,
  exactBaseline,
  newsEvents,
  className,
}: {
  points: P[];
  prevClose?: number | null;
  yesterdayVol?: number | null;
  index?: { points: P[]; prevClose: number } | null;
  auction?: { price: number; pct: number | null } | null;
  /** 精确量比基线（最近 5 个完整交易日逐 5min 槽同期累计量均值）。缺失回退近似口径。 */
  exactBaseline?: number[] | null;
  /** 当日新闻分钟事件点（仅含时刻落在槽区间的条目；公告只有日期不进分时）。 */
  newsEvents?: MinuteNewsEvent[] | null;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);

  // 量比统一计算：精确口径（TDX 5 日同期基线）优先，缺失回退近似（昨日量×时间占比）。
  const computeLB = useCallback(
    (cum: number | null | undefined, bjIso: string): number | null => {
      if (!cum) return null;
      const elapsed = tradingMinutesElapsed(bjIso);
      if (elapsed <= 0) return null;
      if (exactBaseline && exactBaseline.length > 0) {
        const slot = Math.min(Math.max(Math.floor(elapsed / 5) - 1, 0), exactBaseline.length - 1);
        // 基线已是「5 日同期累计量均值」（后端除过 5），直接除即得量比；再除 5 会虚高 5 倍
        const base5 = exactBaseline[slot];
        if (base5 > 0) return cum / base5;
      }
      if (yesterdayVol && yesterdayVol > 0) return cum / (yesterdayVol * (elapsed / 240));
      return null;
    },
    [exactBaseline, yesterdayVol]
  );

  // 角标数据：当前量比 + 上证涨跌幅（从最后一点派生，纯计算不走请求）
  const badges = useMemo(() => {
    const last = points[points.length - 1];
    let lb: number | null = null;
    if (last) {
      const bjIso = new Date(new Date(last.ts).getTime() + 8 * 3600 * 1000).toISOString();
      lb = computeLB(last.cum_volume, bjIso);
    }
    let idxPct: number | null = null;
    if (index && index.points.length > 0) {
      const lastIdx = index.points[index.points.length - 1];
      idxPct = ((lastIdx.price - index.prevClose) / index.prevClose) * 100;
    }
    return { lb, idxPct };
  }, [points, index, computeLB]);

  // points/computeLB 的实时引用（crosshair 回调闭包读 ref，不进依赖触发重建）
  const pointsRef = useRef(points);
  useEffect(() => {
    pointsRef.current = points;
  }, [points]);
  const lbRef = useRef(computeLB);
  useEffect(() => {
    lbRef.current = computeLB;
  }, [computeLB]);

  const seriesRef = useRef<SeriesBundle>(emptyBundle());
  // 已灌数据的最后槽（HHMM 游标）：""=需要全量
  const cursorRef = useRef("");
  // 创建 effect 最近一次灌入的 points 引用（增量 effect 去重，避免同帧重复灌）
  const lastPointsRef = useRef<P[] | null>(null);

  /** 槽序列：09:25(竞价) + 09:30-11:30 + 13:00-15:00 共 243 槽，当日固定。 */
  function buildSlots(first: P): { slots: number[]; base0: number } {
    const bjDate = new Date(new Date(first.ts).getTime() + BJ_OFFSET * 1000).toISOString().slice(0, 10);
    const base0 = Math.floor(new Date(`${bjDate}T00:00:00Z`).getTime() / 1000); // 伪 UTC 当日 00:00
    const slotSecs: number[] = [base0 + 9 * 3600 + 25 * 60];
    for (let m = 570; m <= 690; m++) slotSecs.push(base0 + Math.floor(m / 60) * 3600 + (m % 60) * 60);
    for (let m = 780; m <= 900; m++) slotSecs.push(base0 + Math.floor(m / 60) * 3600 + (m % 60) * 60);
    return { slots: slotSecs, base0 };
  }

  /**
   * 全量重灌四个数据序列（首灌 / 每帧增量路径）。
   * ⚠️ 必须用 setData 而非 series.update()：槽位序列以全天 whitespace 占位
   * （X 轴固定 9:25-15:00 的视觉需求），series 内部"最后时间点"恒为 15:00——
   * update() 只允许写入不早于最后时间点的数据，盘中 update(当前分钟) 必抛
   * "Cannot update oldest data"（2026-09-02 实测把页面打进 error boundary）。
   * setData 无顺序约束且是 Canvas 原位重绘（不重建图表、不闪）：243 槽 ×
   * 4 序列在 1Hz 节奏下开销可忽略，且天然消除合成点与官方点同槽竞态。
   */
  function fillAll(s: SeriesBundle, pts: P[], base: number | null | undefined) {
    if (!s.price) return;
    const byHHMM = new Map<string, P>();
    for (const p of pts) byHHMM.set(bjHHMM(p), p);
    // 槽序遍历 → 前一有值槽的价格（量能红涨绿跌颜色判定），O(n) 无 indexOf
    const slotHHMMs = s.slots.map((sec) => new Date(sec * 1000).toISOString().slice(11, 16));
    const prevPriceByHHMM = new Map<string, number | null>();
    let prevP: number | null = null;
    for (let i = 0; i < s.slots.length; i++) {
      prevPriceByHHMM.set(slotHHMMs[i], prevP);
      const p = byHHMM.get(slotHHMMs[i]);
      if (p && p.price != null) prevP = p.price;
    }
    const volColor = (p: P): string => {
      const prevPrice = prevPriceByHHMM.get(bjHHMM(p)) ?? null;
      return prevPrice == null || p.price === prevPrice ? FLAT : p.price > prevPrice ? "rgba(239,68,68,0.5)" : "rgba(16,185,129,0.5)";
    };
    const over = <T,>(pick: (p: P) => T | null): { time: Time; value?: T }[] =>
      s.slots.map((sec, i) => {
        const p = byHHMM.get(slotHHMMs[i]);
        const v = p ? pick(p) : null;
        return (v == null ? { time: sec as Time } : { time: sec as Time, value: v }) as { time: Time; value?: T };
      });
    s.price.setData(over((p) => p.price) as never);
    if (s.pct && base != null && base > 0) {
      s.pct.setData(over((p) => (p.price != null ? ((p.price - base) / base) * 100 : null)) as never);
    }
    s.avg?.setData(over((p) => p.avg) as never);
    if (s.vol) {
      const volData: { time: Time; value?: number; color?: string }[] = s.slots.map((sec, i) => {
        const p = byHHMM.get(slotHHMMs[i]);
        if (!p || p.volume == null) return { time: sec as Time }; // whitespace
        return { time: sec as Time, value: p.volume, color: volColor(p) };
      });
      s.vol.setData(volData as never);
    }
  }

  const hasPoints = points.length > 0;
  // 低频配置（晚到即整图重建一次，次数 ≤3）：昨收/大盘叠加/竞价/量比基线/事件点
  useEffect(() => {
    if (!ref.current || !hasPoints) return;
    const cur = pointsRef.current;
    const hasBase = prevClose != null && prevClose > 0;

    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a1a1aa" },
      grid: {
        vertLines: { color: "rgba(120,120,130,0.12)" },
        horzLines: { color: "rgba(120,120,130,0.12)" },
      },
      timeScale: { timeVisible: true, secondsVisible: false, borderVisible: false, rightOffset: 1 },
      rightPriceScale: { borderVisible: false },
      leftPriceScale: hasBase ? { visible: true, borderVisible: false } : { visible: false },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const s = seriesRef.current;
    s.chart = chart;
    const { slots, base0 } = buildSlots(cur[0]);
    s.slots = slots;
    s.base0 = base0;

    // ---- 价格面积线：昨收锚定的对称区间 ----
    const series = chart.addAreaSeries({
      lineColor: "#f43f5e",
      topColor: "rgba(244,63,94,0.28)",
      bottomColor: "rgba(244,63,94,0.02)",
      lineWidth: 2,
      priceLineVisible: false,
    });
    s.price = series;

    // ---- 左轴涨跌幅（隐藏 % 序列）----
    if (hasBase) {
      s.pct = chart.addLineSeries({
        priceScaleId: "left",
        visible: false,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        priceFormat: { type: "percent", precision: 2, minMove: 0.01 },
      });
    }

    // ---- 均价线（黄）----
    s.avg = chart.addLineSeries({
      color: "#eab308",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    // ---- 量能副图：红涨绿跌 ----
    s.vol = chart.addHistogramSeries({
      priceScaleId: "vol",
      priceFormat: { type: "volume" },
      priceLineVisible: false,
      lastValueVisible: false,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });

    fillAll(s, cur, prevClose);
    cursorRef.current = bjHHMM(cur[cur.length - 1]);
    lastPointsRef.current = cur;

    if (hasBase) {
      let hi = -Infinity;
      let lo = Infinity;
      for (const p of cur) {
        if (p.price > hi) hi = p.price;
        if (p.price < lo) lo = p.price;
      }
      // 竞价点纳入对称区间，防止被裁剪出可视区
      if (auction?.price) {
        if (auction.price > hi) hi = auction.price;
        if (auction.price < lo) lo = auction.price;
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

      const halfPct = (half / prevClose!) * 100;
      s.pct?.applyOptions({
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
        const idxByHHMM = new Map<string, P>();
        for (const q of index.points) idxByHHMM.set(bjHHMM(q), q);
        const slotHHMM = (sec: number) => new Date(sec * 1000).toISOString().slice(11, 16);
        idxSeries.setData(
          slots.map((sec) => {
            const i = idxByHHMM.get(slotHHMM(sec));
            return i
              ? ({ time: sec as Time, value: ((i.price - index.prevClose) / index.prevClose) * 100 } as LineData)
              : ({ time: sec as Time } as LineData);
          }) as never
        );
        // 叠加曲线不得撑破个股的对称区间：钳制到 ±halfPct 视觉带内
        idxSeries.applyOptions({
          autoscaleInfoProvider: () => ({
            priceRange: { minValue: -halfPct, maxValue: halfPct },
          }),
        });
      }
    }

    // ---- 集合竞价点（09:25，金色）：槽序列首点即 9:25 ----
    if (auction?.price && cur.length > 0) {
      const auctionSeries = chart.addLineSeries({
        color: "#f59e0b",
        lineWidth: 1,
        pointMarkersVisible: true,
        pointMarkersRadius: 4,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      auctionSeries.setData([{ time: slots[0] as never, value: auction.price }]);
    }

    // ---- 当日新闻事件点（蓝圆点，挂在事件分钟的价格上）：只在槽已有行情时画
    // （未来槽无价格锚，跳过不臆造）；同一分钟多条已在上游合并。
    const evByHHMM = new Map((newsEvents ?? []).map((e) => [e.hhmm, e]));
    if (evByHHMM.size > 0) {
      const evData: { time: Time; value: number }[] = [];
      const byHHMM = new Map<string, P>();
      for (const p of cur) byHHMM.set(bjHHMM(p), p);
      for (const e of newsEvents ?? []) {
        const p = byHHMM.get(e.hhmm);
        if (!p) continue; // 槽尚无行情点（事件在未来/数据缺口）：不画
        const sec = base0 + Number(e.hhmm.slice(0, 2)) * 3600 + Number(e.hhmm.slice(3, 5)) * 60;
        evData.push({ time: sec as Time, value: p.price });
      }
      if (evData.length > 0) {
        const evSeries = chart.addLineSeries({
          color: "#38bdf8",
          lineWidth: 1,
          pointMarkersVisible: true,
          pointMarkersRadius: 3.5,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        evSeries.setData(evData as never);
      }
    }

    // ---- 十字光标浮层 ----
    const tooltip = tipRef.current;
    const onMove = (param: { time?: Time; point?: { x: number; y: number } }) => {
      if (!tooltip) return;
      if (!param.time || !param.point) {
        tooltip.style.opacity = "0";
        return;
      }
      // param.time 是编码后的伪 UTC（真实 ts + 8h，见槽位构建）；
      // 匹配数据点必须先减回 8h 还原真实时基——否则所有点与光标恒差 8h，
      // "最近点"永远收敛到最后一根（收盘价），浮层每个位置都显示同一条数据。
      const sec = (param.time as unknown as number) * 1000 - BJ_OFFSET * 1000;
      const pts = pointsRef.current;
      let best: P | null = null;
      let bestDiff = Infinity;
      for (const p of pts) {
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
      const lb = lbRef.current(d.cum_volume, bjIso);
      const minuteAmount =
        d.cum_amount != null && bestDiff >= 0
          ? (() => {
              const i = pts.indexOf(d);
              const prev = i > 0 ? pts[i - 1].cum_amount : null;
              return prev != null ? (d.cum_amount! - prev) / 1e4 : null; // 万
            })()
          : null;
      const pctCls = (v: number | null) => (v == null ? "" : v > 0 ? "text-red-500" : v < 0 ? "text-emerald-500" : "text-zinc-400");
      const lbCls = lb == null ? "" : lb >= 1.5 ? "text-red-500" : lb >= 0.8 ? "text-amber-500" : "text-sky-500";
      // 事件行：光标分钟（±1 分钟容差）命中当日新闻时追加，长标题截断
      const bestHHMM = bjIso.slice(11, 16);
      const hhmmMins = Number(bestHHMM.slice(0, 2)) * 60 + Number(bestHHMM.slice(3, 5));
      let ev: MinuteNewsEvent | null = null;
      for (const cand of evByHHMM.values()) {
        const cm = Number(cand.hhmm.slice(0, 2)) * 60 + Number(cand.hhmm.slice(3, 5));
        if (Math.abs(cm - hhmmMins) <= 1) {
          ev = cand;
          break;
        }
      }
      const evRow = ev
        ? `<div class="mt-1 border-t border-zinc-200 pt-1 dark:border-zinc-700"><span class="text-sky-500">📰 ${ev.count > 1 ? `×${ev.count} ` : ""}${ev.title.length > 26 ? ev.title.slice(0, 26) + "…" : ev.title}</span></div>`
        : "";
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
        </div>${evRow}`;
      tooltip.style.opacity = "1";
      const box = ref.current!;
      const w = tooltip.offsetWidth || 150;
      const x = param.point.x + 14 + w > box.clientWidth ? Math.max(4, param.point.x - 14 - w) : param.point.x + 14;
      const y = Math.min(Math.max(4, param.point.y - 10), Math.max(4, box.clientHeight - tooltip.offsetHeight - 4));
      tooltip.style.left = `${x}px`;
      tooltip.style.top = `${y}px`;
    };

    const unsub = chart.subscribeCrosshairMove(onMove);
    // 全天槽已在数据集里（whitespace 撑满 9:25-15:00），fitContent 即完整交易时段；
    // 左端 -1.5 给 9:25 竞价金点留出圆的空间
    chart.timeScale().fitContent();
    chart.timeScale().setVisibleLogicalRange({ from: -1.5, to: slots.length + 0.5 });

    return () => {
      try {
        chart.unsubscribeCrosshairMove(onMove);
      } catch {}
      chart.remove();
      seriesRef.current = emptyBundle();
      cursorRef.current = "";
      lastPointsRef.current = null;
      void unsub;
    };
    // 低频配置变化才重建；points 走增量 effect（下）。evByHHMM 供 crosshair 闭包。
  }, [hasPoints, prevClose, index, auction, exactBaseline, newsEvents]);

  // 数据增量 effect：points 高频变化（WS 合成/60s 校准）→ 全量 setData 原地重灌。
  // 不用 series.update()：槽位序列尾部是全天 whitespace（X 轴固定全程），
  // series 最后时间点恒为 15:00，update(当前分钟) 必抛 "Cannot update oldest
  // data"（2026-09-02 实测崩溃）。setData 是 Canvas 原位重绘不闪；每帧全量
  // 灌入同时天然消除合成点与官方校准点的同槽竞态，图表数据恒为数组真值。
  // lastPointsRef 去重保留：创建 effect 同帧已灌过同一引用时跳过。
  useEffect(() => {
    const s = seriesRef.current;
    if (!s.chart || !s.price || points.length === 0) return;
    if (lastPointsRef.current === points) return; // 创建 effect 刚灌过同一份数据
    lastPointsRef.current = points;
    fillAll(s, points, prevClose);
    cursorRef.current = bjHHMM(points[points.length - 1]);
  }, [points, prevClose]);

  return (
    <div className="flex h-full w-full flex-col">
      {/* 角标行：量比 + 竞价 + 上证叠加图例——独立文档流行（原 absolute right-2 top-1.5
          浮层压在图表右上角价格标签/最新价区域），不占图表绘制空间、互不遮挡 */}
      <div className="flex shrink-0 items-center justify-end gap-2 px-2 pb-0.5 pt-1 text-[11px]">
        {auction?.pct != null && (
          <span
            className={`rounded border px-1.5 py-0.5 font-mono tabular-nums ${
              auction.pct >= 2 ? "border-amber-500/40 bg-amber-500/10 text-amber-500" : "border-zinc-500/40 bg-zinc-500/10 text-zinc-400"
            }`}
            title="集合竞价（09:25 终态）：图中金色点为竞价价格；放量上攻（≥2% 且量比≥1.5）为资金先手信号"
          >
            竞价 {auction.pct > 0 ? "+" : ""}
            {auction.pct.toFixed(2)}%
          </span>
        )}
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
        {newsEvents && newsEvents.length > 0 && (
          <span
            className="rounded border border-sky-500/40 bg-sky-500/10 px-1.5 py-0.5 text-sky-500"
            title={`图中蓝色圆点为当日新闻发布时刻（挂在该分钟价格上）：${newsEvents.map((e) => e.hhmm).join(" / ")}`}
          >
            新闻 {newsEvents.length} 点
          </span>
        )}
      </div>
      <div className="relative min-h-0 w-full flex-1">
        <div ref={ref} className={`h-full w-full ${className ?? ""}`} />
        <div
          ref={tipRef}
          className="pointer-events-none absolute left-0 top-0 z-10 min-w-[150px] rounded-lg border border-zinc-200 bg-white/95 px-2.5 py-1.5 opacity-0 shadow-sm transition-opacity dark:border-zinc-700 dark:bg-zinc-900/95"
        />
      </div>
    </div>
  );
}
