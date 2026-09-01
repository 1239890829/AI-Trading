"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickData,
  createChart,
  HistogramData,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  LineData,
  LineStyle,
  SeriesMarker,
  Time,
} from "lightweight-charts";
import { calcEMA } from "@/lib/technical-analysis";
import { fmt, fmtAmount } from "@/lib/format";
import type { EventMark } from "@/lib/event-markers";
import type { Kline } from "@/types/market";

interface Props {
  bars: Kline[];
  className?: string;
  tradeMarks?: { date: string; side: string; price: number; quantity: number }[];
  /** 当前持仓摊薄成本，>0 时在主图画虚线 */
  costPrice?: number | null;
  /** 新闻/公告事件点（P1-8）：已按 bar 日期对齐 */
  eventMarks?: EventMark[];
  /** 数据更新时重聚焦最近 20 根（历史回放跟随进度）；详情页 false，保用户缩放位置 */
  followLatest?: boolean;
}

type Indicators = { ma5: boolean; ma10: boolean; ma20: boolean; ma60: boolean; vol: boolean; macd: boolean; boll: boolean; amt: boolean; bs: boolean; events: boolean };

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

/**
 * K 线图（专业版）：MA5/10/20/60、BOLL(20,2)、成交量+均量线(5/10/20)、MACD/成交额副图、
 * 指标开关、缩放按钮、副图高度拖拽（布局 #2）。默认聚焦最近 20 根。
 *
 * 创建与填充分离（2026-08-31）：此前 bars 一变（盘中 WS 合成当日 bar，秒级）整个 chart
 * 销毁重建——闪烁且丢失用户的缩放/平移位置，实时刷新根本没法用。现在 chart/series
 * 只随指标开关重建，数据变化走 fill() 对既有 series setData。
 */
export function KlineChartPro({ bars, className, tradeMarks, costPrice, eventMarks, followLatest = false }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [ind, setInd] = useState<Indicators>({ ma5: true, ma10: true, ma20: true, ma60: true, vol: true, macd: false, boll: false, amt: false, bs: true, events: true });
  // 布局 #2：副图高度占比可拖拽（0.10-0.45，localStorage 持久化）
  const [subH, setSubH] = useState(0.18);
  const subHRef = useRef(subH);
  useEffect(() => {
    const saved = Number(localStorage.getItem("ashare-sub-h"));
    if (saved >= 0.1 && saved <= 0.45) {
      // rAF 延迟：set-state-in-effect 规则禁止 effect 体内同步 setState
      const raf = requestAnimationFrame(() => {
        setSubH(saved);
        subHRef.current = saved;
      });
      return () => cancelAnimationFrame(raf);
    }
  }, []);

  // hover 联动（2026-09-01 用户反馈 #2/#3）：十字光标所在 bar 的 OHLC/量额/MA/事件
  // 显示在顶部信息条；离开图表回退到最新一根。bars 用 ref（crosshair 回调闭包拿最新值）
  const barsRef = useRef(bars);
  useEffect(() => {
    barsRef.current = bars;
  }, [bars]);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  // fill 时算好的 MA 序列快照（按 data 索引），hover 信息条直接取值
  const maSnapRef = useRef<Record<string, (number | null)[]>>({});

  // series 引用：创建 effect 按当前指标开关建好，fill() 只往里 setData
  const seriesRef = useRef<{
    candle: ISeriesApi<"Candlestick"> | null;
    ma: (ISeriesApi<"Line"> | null)[];
    boll: { up: ISeriesApi<"Line"> | null; low: ISeriesApi<"Line"> | null; mid: ISeriesApi<"Line"> | null };
    vol: ISeriesApi<"Histogram"> | null;
    vma: (ISeriesApi<"Line"> | null)[];
    amt: ISeriesApi<"Histogram"> | null;
    macd: { hist: ISeriesApi<"Histogram"> | null; dif: ISeriesApi<"Line"> | null; dea: ISeriesApi<"Line"> | null };
  }>({ candle: null, ma: [], boll: { up: null, low: null, mid: null }, vol: null, vma: [], amt: null, macd: { hist: null, dif: null, dea: null } });
  const priceLineRef = useRef<IPriceLine | null>(null);

  // fill：把当前 props 数据灌入已存在的 series（幂等，全量 setData；bars 只有 120 根，成本低）。
  // 存进 ref 供创建 effect 调用最新版本，避免把 bars 放进创建依赖导致整图重建。
  const fill = () => {
    const chart = chartRef.current;
    const s = seriesRef.current;
    if (!chart || !s.candle || bars.length === 0) return;

    const data: CandlestickData[] = bars
      .filter((b) => b.open != null && b.close != null)
      .map((b) => ({ time: b.ts.slice(0, 10) as Time, open: b.open as number, high: (b.high ?? b.open) as number, low: (b.low ?? b.open) as number, close: b.close as number }));
    s.candle.setData(data);
    const closes = data.map((d) => d.close);
    const times = data.map((d) => d.time);

    // 主图均线（MA 序列快照供 hover 信息条取值）
    const maSnap: Record<string, (number | null)[]> = {};
    MA_DEFS.forEach(([key], i) => {
      const line = s.ma[i];
      const n = MA_DEFS[i][1];
      const vals = calcMA(closes, n);
      maSnap[key] = vals;
      if (!line) return;
      s.ma[i]!.setData(
        vals.map((v, j) => ({ time: times[j], value: v })).filter((x) => x.value != null) as LineData[]
      );
    });
    maSnapRef.current = maSnap;

    // BOLL(20,2)
    if (s.boll.up && s.boll.low && s.boll.mid) {
      const boll = calcBOLL(closes);
      const mk = (vals: (number | null)[]): LineData[] =>
        vals.map((v, i) => ({ time: times[i], value: v })).filter((x) => x.value != null) as LineData[];
      s.boll.up.setData(mk(boll.up));
      s.boll.low.setData(mk(boll.low));
      s.boll.mid.setData(mk(boll.ma));
    }

    // 成交量副图 + 均量线 5/10/20
    if (s.vol) {
      const vd: HistogramData[] = bars
        .filter((b) => b.volume != null)
        .map((b) => ({ time: b.ts.slice(0, 10) as Time, value: b.volume as number, color: (b.close ?? 0) >= (b.open ?? 0) ? "rgba(244,63,94,0.45)" : "rgba(16,185,129,0.45)" }));
      s.vol.setData(vd);
      const vols = vd.map((d) => d.value as number);
      const volTimes = vd.map((d) => d.time);
      s.vma.forEach((line, i) => {
        if (!line) return;
        const n = [5, 10, 20][i];
        line.setData(
          calcMA(vols, n).map((v, j) => ({ time: volTimes[j], value: v })).filter((x) => x.value != null) as LineData[]
        );
      });
    }

    // 成交额副图
    if (s.amt) {
      s.amt.setData(
        bars
          .filter((b) => b.amount != null)
          .map((b) => ({ time: b.ts.slice(0, 10) as Time, value: b.amount as number, color: (b.close ?? 0) >= (b.open ?? 0) ? "rgba(244,63,94,0.45)" : "rgba(16,185,129,0.45)" }))
      );
    }

    // MACD 副图
    if (s.macd.hist && s.macd.dif && s.macd.dea) {
      const ema12 = calcEMA(closes, 12);
      const ema26 = calcEMA(closes, 26);
      const dif = closes.map((_, i) => ema12[i] - ema26[i]);
      const dea = calcEMA(dif, 9);
      s.macd.hist.setData(data.map((d, i) => ({ time: d.time, value: (dif[i] - dea[i]) * 2, color: dif[i] > dea[i] ? "rgba(244,63,94,0.6)" : "rgba(16,185,129,0.6)" })));
      s.macd.dif.setData(data.map((d, i) => ({ time: d.time, value: dif[i] })));
      s.macd.dea.setData(data.map((d, i) => ({ time: d.time, value: dea[i] })));
    }

    // 标记：真实 B/S 点 + 新闻/公告事件点。总是调用（含空数组）——
    // 数据切换后旧标记必须清掉，原实现只在非空时 set 会残留上一标的的标记
    const markers: SeriesMarker<Time>[] = [];
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
    }
    if (ind.events) {
      for (const m of eventMarks ?? []) {
        if (!bars.some((b) => b.ts.slice(0, 10) === m.date)) continue;
        const isAnn = m.kind === "公告";
        markers.push({
          time: m.date as Time,
          position: isAnn ? "aboveBar" : "belowBar",
          color: isAnn ? "#f59e0b" : "#38bdf8",
          shape: "circle",
          text: `${m.kind}${m.important ? "!" : ""}`,
        });
      }
    }
    markers.sort((a, b) => String(a.time).localeCompare(String(b.time)));
    s.candle.setMarkers(markers);

    // 持仓成本线：先移除旧线再按需创建（costPrice 变化/清除时同步）
    if (priceLineRef.current) {
      s.candle.removePriceLine(priceLineRef.current);
      priceLineRef.current = null;
    }
    if (costPrice != null && costPrice > 0) {
      priceLineRef.current = s.candle.createPriceLine({
        price: costPrice,
        color: "#fbbf24",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "成本",
      });
    }

    if (followLatest) {
      const n = data.length;
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 20), to: n + 2 });
    }
  };
  // fill 的引用经 effect 同步到 ref（渲染期写 ref 会被 react-hooks 规则拦截）；
  // 本 effect 声明在创建 effect 之前，保证创建 effect 每次跑时拿到的是最新 fill
  const fillRef = useRef<() => void>(() => {});
  useEffect(() => {
    fillRef.current = fill;
  });

  // 创建 effect：只随指标开关/数据有无变化重建；数据更新走 fillRef（不重建）
  const hasBars = bars.length > 0;
  useEffect(() => {
    if (!containerRef.current || !hasBars) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a1a1aa" },
      grid: { vertLines: { color: "rgba(120,120,130,0.10)" }, horzLines: { color: "rgba(120,120,130,0.10)" } },
      timeScale: { timeVisible: false, borderVisible: false },
      rightPriceScale: { borderVisible: false },
    });
    chartRef.current = chart;
    const s = seriesRef.current;
    s.candle = chart.addCandlestickSeries({
      upColor: "#f43f5e", downColor: "#10b981", borderUpColor: "#f43f5e", borderDownColor: "#10b981",
      wickUpColor: "#f43f5e", wickDownColor: "#10b981",
    });
    s.ma = MA_DEFS.map(([key]) => (ind[key] ? chart.addLineSeries({ color: MA_DEFS.find((k) => k[0] === key)![2], lineWidth: 1, priceLineVisible: false, lastValueVisible: false }) : null));
    if (ind.boll) {
      s.boll = {
        up: chart.addLineSeries({ color: "#e879f9", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
        low: chart.addLineSeries({ color: "#e879f9", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
        mid: chart.addLineSeries({ color: "rgba(161,161,170,0.7)", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
      };
    } else {
      s.boll = { up: null, low: null, mid: null };
    }
    if (ind.vol) {
      s.vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
      s.vma = [5, 10, 20].map((n) => chart.addLineSeries({ priceScaleId: "vol", color: n === 5 ? "#facc15" : n === 10 ? "#38bdf8" : "#c084fc", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }));
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 1 - subHRef.current, bottom: 0 } });
    } else {
      s.vol = null;
      s.vma = [];
    }
    s.amt = ind.amt ? chart.addHistogramSeries({ priceScaleId: "amt", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false }) : null;
    if (ind.amt) chart.priceScale("amt").applyOptions({ scaleMargins: { top: 1 - subHRef.current, bottom: 0 } });
    if (ind.macd) {
      s.macd = {
        hist: chart.addHistogramSeries({ priceScaleId: "macd", priceLineVisible: false, lastValueVisible: false }),
        dif: chart.addLineSeries({ priceScaleId: "macd", color: "#facc15", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
        dea: chart.addLineSeries({ priceScaleId: "macd", color: "#38bdf8", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
      };
      chart.priceScale("macd").applyOptions({ scaleMargins: { top: 1 - subHRef.current, bottom: 0 } });
    } else {
      s.macd = { hist: null, dif: null, dea: null };
    }

    fillRef.current();
    // 首次聚焦最近 20 根（followLatest 时 fill 内每次都会重设，这里不必重复）
    if (!followLatest) {
      const n = Math.min(20, bars.length);
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - n), to: bars.length + 2 });
    }
    // hover 联动（用户反馈 #2/#3）：十字光标 → 顶部信息条切换到该日数据；
    // 离开图表（param.time 为空）回退最新。param.time 即 bar 的 'YYYY-MM-DD'。
    const onCross = (param: { time?: Time }) => {
      if (!param.time) {
        setHoverIdx(null);
        return;
      }
      const d = String(param.time).slice(0, 10);
      const idx = barsRef.current.findIndex((b) => b.ts.slice(0, 10) === d);
      setHoverIdx(idx >= 0 ? idx : null);
    };
    chart.subscribeCrosshairMove(onCross);
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = { candle: null, ma: [], boll: { up: null, low: null, mid: null }, vol: null, vma: [], amt: null, macd: { hist: null, dif: null, dea: null } };
      priceLineRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ind, hasBars]);

  // 数据更新 effect：bars（含 WS 合成的当日 bar）/标记/成本线变化 → 原地重灌数据
  useEffect(() => {
    if (!chartRef.current) return;
    fillRef.current();
  }, [bars, tradeMarks, costPrice, eventMarks]);

  // 布局 #2：副图高度变化 → applyOptions 动态调整（不重建 chart），主图 bottom 随之让位
  useEffect(() => {
    const c = chartRef.current;
    if (!c) return;
    const subTop = 1 - subH;
    for (const id of ["vol", "amt", "macd"]) {
      try {
        c.priceScale(id).applyOptions({ scaleMargins: { top: subTop, bottom: 0 } });
      } catch {
        // 该副图未开启时 scale 不存在，跳过
      }
    }
    try {
      c.priceScale("right").applyOptions({ scaleMargins: { top: 0.08, bottom: Math.min(subH + 0.02, 0.5) } });
    } catch {
      // 防御
    }
  }, [subH, ind, hasBars]);

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
    ["events", "事件"],
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

  // 信息条数据（用户反馈 #2/#3）：hover 的 bar，未 hover 取最新一根
  const idx = hoverIdx ?? bars.length - 1;
  const d = bars.length > 0 ? bars[Math.max(0, Math.min(idx, bars.length - 1))] : null;
  const prev = d && idx > 0 ? bars[idx - 1] : null;
  const dPct = d?.close != null && prev?.close ? ((d.close - prev.close) / prev.close) * 100 : null;
  // 信息条 MA 值：渲染期从 bars 直算（与 fill() 的 calcMA 同口径）——
  // 读 maSnapRef 属渲染期访问 ref，react-hooks 规则禁止
  const hoverMA = useMemo(() => {
    const closes = bars.filter((b) => b.open != null && b.close != null).map((b) => b.close as number);
    const out: Record<string, (number | null)[]> = {};
    MA_DEFS.forEach(([key, n]) => {
      out[key] = calcMA(closes, n);
    });
    return out;
  }, [bars]);
  const dEvents =
    d && ind.events
      ? (eventMarks ?? []).filter((m) => m.date === d.ts.slice(0, 10))
      : [];
  const pctCls = (v: number | null) => (v == null ? "text-zinc-400" : v > 0 ? "text-up" : v < 0 ? "text-down" : "text-zinc-400");
  const dClose = d?.close ?? null;
  const dOpen = d?.open ?? null;

  return (
    <div className={`relative flex min-h-0 flex-col ${className ?? ""}`}>
      <div className="absolute right-2 top-2 z-10 flex gap-1">
        <button onClick={() => zoomTime(0.7)} className="h-6 w-6 rounded border border-zinc-300 bg-white/80 text-xs text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900/80 dark:text-zinc-300 dark:hover:bg-zinc-800" aria-label="放大">＋</button>
        <button onClick={() => zoomTime(1.4)} className="h-6 w-6 rounded border border-zinc-300 bg-white/80 text-xs text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900/80 dark:text-zinc-300 dark:hover:bg-zinc-800" aria-label="缩小">−</button>
        <button
          onClick={() => {
            const n = bars.length;
            chartRef.current?.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 20), to: n + 2 });
          }}
          className="h-6 rounded border border-zinc-300 bg-white/80 px-1.5 text-[10px] text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900/80 dark:text-zinc-300 dark:hover:bg-zinc-800"
          aria-label="回到最近20日"
        >
          20D
        </button>
      </div>
      {/* OHLC 信息条（对标同花顺）：hover 切换该日数据，离开回退最新 */}
      {d && (
        <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-0.5 border-b border-zinc-100 px-3 py-1 font-mono text-[11px] tabular-nums dark:border-zinc-800/60">
          <span className="text-zinc-400">{d.ts.slice(0, 10)}{hoverIdx != null && hoverIdx !== bars.length - 1 && <span className="ml-1 text-zinc-300 dark:text-zinc-600">（历史）</span>}</span>
          <span className="text-zinc-400">开 <span className={pctCls(dOpen != null && dClose != null ? dClose - dOpen : null)}>{fmt(d.open)}</span></span>
          <span className="text-zinc-400">高 <span className="text-up">{fmt(d.high)}</span></span>
          <span className="text-zinc-400">低 <span className="text-down">{fmt(d.low)}</span></span>
          <span className="text-zinc-400">收 <span className={pctCls(dPct)}>{fmt(d.close)}</span></span>
          <span className={pctCls(dPct)}>{dPct != null ? `${dPct > 0 ? "+" : ""}${dPct.toFixed(2)}%` : "--"}</span>
          {d.volume != null && <span className="text-zinc-400">量 <span className="text-zinc-700 dark:text-zinc-200">{(d.volume / 100).toLocaleString()}手</span></span>}
          {d.amount != null && <span className="text-zinc-400">额 <span className="text-zinc-700 dark:text-zinc-200">{fmtAmount(d.amount)}</span></span>}
          {MA_DEFS.filter(([key]) => ind[key]).map(([key, , color]) => (
            <span key={key} style={{ color }} className="hidden lg:inline">
              {key.toUpperCase()} <span className="text-zinc-700 dark:text-zinc-200">{hoverMA[key]?.[Math.max(0, Math.min(idx, (hoverMA[key]?.length ?? 1) - 1))] != null ? fmt(hoverMA[key][Math.max(0, Math.min(idx, (hoverMA[key]?.length ?? 1) - 1))]) : "--"}</span>
            </span>
          ))}
          {dEvents.length > 0 && (
            <span className="max-w-[280px] truncate text-sky-500" title={dEvents.map((m) => `${m.kind}${m.important ? "（重要）" : ""}：${m.title}`).join("\n")}>
              ◆ {dEvents[0].title}
            </span>
          )}
        </div>
      )}
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
        <span className="ml-auto text-[10px] text-zinc-500" title="B/S=模拟交易成交；紫[榜]=龙虎榜日；琥珀●=公告 蓝●=新闻（!=重要度高）——消息面与价格走势对照；hover 信息条显示当日事件标题">
          金叉/死叉为 MA5×MA10 技术信号 · 紫[榜]=龙虎榜日 · B/S=模拟交易成交 · 黄虚线=持仓成本 · 琥珀●=公告 蓝●=新闻(!=重要度高)
        </span>
      </div>
      <div className={`relative min-h-0 w-full flex-1 ${className ?? ""}`}>
        <div ref={containerRef} className="h-full w-full" />
        {/* 副图高度拖拽条：贴在副图区顶缘（mouseup 挂 window——释放时鼠标已离开拖拽条） */}
        <div
          onMouseDown={(e) => {
            e.preventDefault();
            const rect = containerRef.current?.getBoundingClientRect();
            if (!rect) return;
            document.body.style.userSelect = "none";
            const move = (ev: MouseEvent) => {
              const ratio = 1 - (ev.clientY - rect.top) / rect.height;
              const v = Math.min(0.45, Math.max(0.1, +ratio.toFixed(3)));
              setSubH(v);
              subHRef.current = v;
            };
            const up = () => {
              document.body.style.userSelect = "";
              window.removeEventListener("mousemove", move);
              window.removeEventListener("mouseup", up);
              setSubH((v) => {
                localStorage.setItem("ashare-sub-h", String(v));
                return v;
              });
            };
            window.addEventListener("mousemove", move);
            window.addEventListener("mouseup", up);
          }}
          className="absolute left-0 right-0 z-10 h-2 cursor-row-resize hover:bg-sky-500/20"
          style={{ top: `calc(${((1 - subH) * 100).toFixed(2)}% - 4px)` }}
          title="拖拽调整副图高度"
          aria-label="拖拽调整副图高度"
        />
      </div>
    </div>
  );
}
