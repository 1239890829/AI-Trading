"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { calcEMA, calcMA } from "@/lib/technical-analysis";
import { fmt, fmtAmount } from "@/lib/format";
import { readChartTheme, useChartTheme, type ChartTheme } from "@/lib/chart-theme";
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

// [key, 周期, 图例文字类]
// ⚠️ 画线色**不在这里**——它随主题变（见 KLINE_PALETTE.ma），而 Tailwind 类名需字面量才能生成，
//   两者生命周期不同，故分开放。图例文字档（第 3 位）与画线亮色档刻意取**同色系同档**
//   （yellow-800 ↔ #854d0e 等），保证「图例颜色 = 线颜色」在两种模式下都成立。
// 2026-09-11 P2-24：图例原用内联 style={{color}} 直接取画线色 ⇒ 亮色下 MA5 的 #facc15
//   在白卡上仅 1.47:1（AA 需 4.5:1），等于看不见；改用双模式文字类。
const MA_DEFS: [keyof Indicators, number, string][] = [
  ["ma5", 5, "text-yellow-800 dark:text-yellow-400"],
  ["ma10", 10, "text-sky-700 dark:text-sky-400"],
  ["ma20", 20, "text-purple-700 dark:text-purple-400"],
  ["ma60", 60, "text-orange-800 dark:text-orange-400"],
];
const MA_LEGEND_CLS = new Map(MA_DEFS.map(([k, , ink]) => [k as string, ink]));

/**
 * 画布调色板（P2-26，2026-09-11）。
 *
 * lightweight-charts 的颜色是**创建 series 时写死的十六进制**，它不认识 CSS 变量、
 * 也不认识 Tailwind 的 `dark:`（那些选择器只作用于 DOM 元素，canvas 一次性绘制的像素
 * 不会因 class 变化重画）。于是亮色下这些高饱和"深色画布专用"色全部失效：
 * MA5 `#facc15` 在浅卡片上 **1.47:1**、MA10 2.14、MA20 2.60、MA60 2.30（图形需 ≥3:1）。
 * 文本对比度扫描器看不见 canvas，所以此前两轮对比度专项（P2-19/P2-24）一路报 0 违规。
 *
 * **深色档 = 全部历史值，逐字节不变**（本轮只改亮色渲染）。
 * 亮色档取「同色系足够深的档位」，实测下限见注释；MA 与图例文字档同色系。
 */
type KlinePalette = {
  axis: string;
  grid: string;
  up: string;
  down: string;
  volUp: string;
  volDown: string;
  macdUp: string;
  macdDown: string;
  ma: [string, string, string, string];
  vma: [string, string, string];
  macdDif: string;
  macdDea: string;
  boll: string;
  bollMid: string;
  cost: string;
  markBuy: string;
  markSell: string;
  markNews: string;
  markAnn: string;
};

const KLINE_PALETTE: Record<ChartTheme, KlinePalette> = {
  dark: {
    axis: "#a1a1aa",
    grid: "rgba(120,120,130,0.10)",
    up: "#f43f5e",
    down: "#10b981",
    volUp: "rgba(244,63,94,0.45)",
    volDown: "rgba(16,185,129,0.45)",
    macdUp: "rgba(244,63,94,0.6)",
    macdDown: "rgba(16,185,129,0.6)",
    ma: ["#facc15", "#38bdf8", "#c084fc", "#fb923c"],
    vma: ["#facc15", "#38bdf8", "#c084fc"],
    macdDif: "#facc15",
    macdDea: "#38bdf8",
    boll: "#e879f9",
    bollMid: "rgba(161,161,170,0.7)",
    cost: "#fbbf24",
    markBuy: "#f43f5e",
    markSell: "#10b981",
    markNews: "#38bdf8",
    markAnn: "#f59e0b",
  },
  light: {
    // zinc-600 在 zinc-50 底上 7.03:1（原 zinc-400 仅 2.46）—— 轴标签是文字，需 4.5:1
    axis: "#52525b",
    grid: "rgba(120,120,130,0.16)",
    // up-deep / down-deep（白底 4.70 / 5.25）。原 down `#10b981` 白底仅 2.54 ⇒ 亮色下绿柱发飘
    up: "#e11d48",
    down: "#047857",
    // 量柱是半透明大面积填充：0.7 叠在浅底上实测 3.16:1（0.45 会淡到看不见）
    volUp: "rgba(225,29,72,0.7)",
    volDown: "rgba(4,120,87,0.7)",
    macdUp: "rgba(225,29,72,0.85)",
    macdDown: "rgba(4,120,87,0.85)",
    // 与 MA_DEFS 图例文字同色系：yellow-800 / sky-700 / purple-700 / orange-800，zinc-50 上 6.5~7.0:1
    ma: ["#854d0e", "#0369a1", "#7e22ce", "#9a3412"],
    vma: ["#854d0e", "#0369a1", "#7e22ce"],
    macdDif: "#854d0e",
    macdDea: "#0369a1",
    boll: "#a21caf",
    bollMid: "#71717a",
    cost: "#b45309",
    markBuy: "#e11d48",
    markSell: "#047857",
    markNews: "#0369a1",
    markAnn: "#b45309",
  },
};

/**
 * 收敛说明（2026-09-11 冗余清理）：此处原有一份与本文件完全同体的 `calcMA`，
 * 与 `lib/technical-analysis.ts` 的导出版逐字节等价（仅形参名不同）。
 * 指标计算属「口径唯一」类代码，两份并存会导致日后只改一处、图上 MA 与
 * 信号判定用的 MA 悄悄分叉——故统一走 lib 版本。
 */

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
  // 画布配色随主题走（P2-26）：theme 变化 → 下面的 applyTheme 把新色套到既有 series 上，
  // **不重建图表**（重建会丢缩放/平移位置）。
  const theme = useChartTheme();
  const pal = KLINE_PALETTE[theme];
  // events 默认**关闭**（2026-09-02 改）：新闻/公告圆点逐日刷屏、遮挡 K 线形体，
  // 且多数条目与走势无关——对标通达信"信息地雷"，做成可开关且默认不打扰。
  // 需要对照事件与价格时再在工具栏打开（下方工具栏"事件"按钮）。
  const [ind, setInd] = useState<Indicators>({ ma5: true, ma10: true, ma20: true, ma60: true, vol: true, macd: false, boll: false, amt: false, bs: true, events: false });
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
  // 2026-09-02：markers 与成本线拆到独立 effect——原实现每次 WS 合成 tick（每秒）
  // 都 setMarkers + removePriceLine/createPriceLine，高频重设造成视觉扰动。
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
        .map((b) => ({ time: b.ts.slice(0, 10) as Time, value: b.volume as number, color: (b.close ?? 0) >= (b.open ?? 0) ? pal.volUp : pal.volDown }));
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
          .map((b) => ({ time: b.ts.slice(0, 10) as Time, value: b.amount as number, color: (b.close ?? 0) >= (b.open ?? 0) ? pal.volUp : pal.volDown }))
      );
    }

    // MACD 副图
    if (s.macd.hist && s.macd.dif && s.macd.dea) {
      const ema12 = calcEMA(closes, 12);
      const ema26 = calcEMA(closes, 26);
      const dif = closes.map((_, i) => ema12[i] - ema26[i]);
      const dea = calcEMA(dif, 9);
      s.macd.hist.setData(data.map((d, i) => ({ time: d.time, value: (dif[i] - dea[i]) * 2, color: dif[i] > dea[i] ? pal.macdUp : pal.macdDown })));
      s.macd.dif.setData(data.map((d, i) => ({ time: d.time, value: dif[i] })));
      s.macd.dea.setData(data.map((d, i) => ({ time: d.time, value: dea[i] })));
    }

    if (followLatest) {
      const n = data.length;
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 20), to: n + 2 });
    }
  };
  // markers 与成本线：只随标记/开关/成本变化重设（低频），不进每秒的 fill 路径。
  // 总是重设（含空数组）——数据切换后旧标记必须清掉，原实现只在非空时 set 会残留上一标的的标记。
  const applyMarks = () => {
    const chart = chartRef.current;
    const s = seriesRef.current;
    if (!chart || !s.candle || bars.length === 0) return;
    const markers: SeriesMarker<Time>[] = [];
    if (ind.bs) {
      for (const t of tradeMarks ?? []) {
        if (!bars.some((b) => b.ts.slice(0, 10) === t.date)) continue;
        markers.push({
          time: t.date as Time,
          position: t.side === "buy" ? "belowBar" : "aboveBar",
          color: t.side === "buy" ? pal.markBuy : pal.markSell,
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
          color: isAnn ? pal.markAnn : pal.markNews,
          shape: "circle",
          // 只在重要度「高」的条目上标 "!"；普通条目画成**纯色小点不带文字**。
          // 原实现每个点都渲染"公告/新闻"字样，逐日刷屏且压住 K 线形体（用户反馈太丑）。
          // 标题仍可通过 hover 信息条与资讯页签查看，信息不丢失。
          text: m.important ? "!" : "",
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
        color: pal.cost,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "成本",
      });
    }
  };
  // fill / applyMarks 的引用经 effect 同步到 ref（渲染期写 ref 会被 react-hooks 规则拦截）；
  // 本 effect 声明在创建 effect 之前，保证创建 effect 每次跑时拿到的是最新版本
  const fillRef = useRef<() => void>(() => {});
  const marksRef = useRef<() => void>(() => {});
  useEffect(() => {
    fillRef.current = fill;
    marksRef.current = applyMarks;
  });

  // 创建 effect：只随指标开关/数据有无变化重建；数据更新走 fillRef（不重建）
  const hasBars = bars.length > 0;
  useEffect(() => {
    if (!containerRef.current || !hasBars) return;
    // 首建配色**同步读 DOM**，不读 state：主题是 documentElement 上的 class，它才是权威值。
    // 读 state 会受 useSyncExternalStore 在 hydration 首帧返回 server snapshot（"dark"）影响，
    // 亮色用户会看到一帧深色画布。
    const p = KLINE_PALETTE[readChartTheme()];
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: p.axis },
      grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
      timeScale: { timeVisible: false, borderVisible: false },
      rightPriceScale: { borderVisible: false },
    });
    chartRef.current = chart;
    const s = seriesRef.current;
    s.candle = chart.addCandlestickSeries({
      upColor: p.up, downColor: p.down, borderUpColor: p.up, borderDownColor: p.down,
      wickUpColor: p.up, wickDownColor: p.down,
    });
    s.ma = MA_DEFS.map(([key], i) => (ind[key] ? chart.addLineSeries({ color: p.ma[i], lineWidth: 1, priceLineVisible: false, lastValueVisible: false }) : null));
    if (ind.boll) {
      s.boll = {
        up: chart.addLineSeries({ color: p.boll, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
        low: chart.addLineSeries({ color: p.boll, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
        mid: chart.addLineSeries({ color: p.bollMid, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
      };
    } else {
      s.boll = { up: null, low: null, mid: null };
    }
    if (ind.vol) {
      s.vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
      s.vma = [5, 10, 20].map((_, i) => chart.addLineSeries({ priceScaleId: "vol", color: p.vma[i], lineWidth: 1, priceLineVisible: false, lastValueVisible: false }));
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
        dif: chart.addLineSeries({ priceScaleId: "macd", color: p.macdDif, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
        dea: chart.addLineSeries({ priceScaleId: "macd", color: p.macdDea, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
      };
      chart.priceScale("macd").applyOptions({ scaleMargins: { top: 1 - subHRef.current, bottom: 0 } });
    } else {
      s.macd = { hist: null, dif: null, dea: null };
    }

    fillRef.current();
    marksRef.current();
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

  // 数据更新 effect：bars（含 WS 合成的当日 bar）变化 → 原地重灌序列数据；
  // 标记/成本线独立 effect（低频）——不进每秒的 fill 路径
  useEffect(() => {
    if (!chartRef.current) return;
    fillRef.current();
  }, [bars]);

  /**
   * 主题切换：把新调色板**套到既有 chart/series 上**（P2-26）。
   * 刻意不重建图表——重建会丢掉用户的缩放与平移位置（图表的「创建与填充分离」约定，
   * 见文件顶部注释）。canvas 的像素只画一次，不做这一步亮色下画线仍是深色画布专用色。
   *
   * 只读 refs、不闭包 props，故依赖为空、恒稳定。
   */
  const applyTheme = useCallback((p: KlinePalette) => {
    const chart = chartRef.current;
    const s = seriesRef.current;
    if (!chart) return;
    chart.applyOptions({
      layout: { textColor: p.axis },
      grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
    });
    s.candle?.applyOptions({
      upColor: p.up, downColor: p.down, borderUpColor: p.up, borderDownColor: p.down,
      wickUpColor: p.up, wickDownColor: p.down,
    });
    s.ma.forEach((line, i) => line?.applyOptions({ color: p.ma[i] }));
    s.boll.up?.applyOptions({ color: p.boll });
    s.boll.low?.applyOptions({ color: p.boll });
    s.boll.mid?.applyOptions({ color: p.bollMid });
    s.vma.forEach((line, i) => line?.applyOptions({ color: p.vma[i] }));
    s.macd.dif?.applyOptions({ color: p.macdDif });
    s.macd.dea?.applyOptions({ color: p.macdDea });
  }, []);

  // 主题 effect 声明在创建 effect **之后**：首建由创建 effect 负责，这里只管"切换后"。
  // 量柱/额柱/MACD 柱与买卖/事件标记的颜色是**逐条**写进数据的，applyOptions 管不到，
  // 故补一次 fill + applyMarks 重灌。
  // 色值经 readChartTheme() 读 DOM（`theme` 仅作触发）：hydration 首帧 state 可能仍是
  // 服务端快照 "dark" 而实际 class 已是 light——若用 state 取值，会把创建 effect 刚按
  // 亮色建好的图**覆盖回深色档**（哪怕只有一帧，也是可见的闪跳）。
  useEffect(() => {
    if (!chartRef.current) return;
    applyTheme(KLINE_PALETTE[readChartTheme()]);
    fillRef.current();
    marksRef.current();
  }, [theme, applyTheme]);

  useEffect(() => {
    if (!chartRef.current) return;
    marksRef.current();
  }, [bars.length, tradeMarks, costPrice, eventMarks, ind.bs, ind.events]);

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

  // 颜色不再在这里重复一份（原实现与 MA_DEFS 各写一遍，改色要改两处）——
  // 激活态文字色统一查 MA_LEGEND_CLS；非 MA 指标本就没有专属色。
  const toggles: [keyof Indicators, string][] = [
    ["ma5", "MA5"],
    ["ma10", "MA10"],
    ["ma20", "MA20"],
    ["ma60", "MA60"],
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
  const pctCls = (v: number | null) => (v == null ? "text-zinc-600 dark:text-zinc-400" : v > 0 ? "text-up-ink dark:text-up" : v < 0 ? "text-down-ink dark:text-down" : "text-zinc-600 dark:text-zinc-400");
  const dClose = d?.close ?? null;
  const dOpen = d?.open ?? null;

  return (
    <div className={`relative flex min-h-0 flex-col ${className ?? ""}`}>
      {/* OHLC 信息条（对标同花顺）：hover 切换该日数据，离开回退最新 */}
      {d && (
        <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-0.5 border-b border-zinc-100 px-3 py-1 font-mono text-[11px] tabular-nums dark:border-zinc-800/60">
          <span className="text-zinc-600 dark:text-zinc-400">{d.ts.slice(0, 10)}{hoverIdx != null && hoverIdx !== bars.length - 1 && <span className="ml-1 text-zinc-600 dark:text-zinc-400">（历史）</span>}</span>
          <span className="text-zinc-600 dark:text-zinc-400">开 <span className={pctCls(dOpen != null && dClose != null ? dClose - dOpen : null)}>{fmt(d.open)}</span></span>
          <span className="text-zinc-600 dark:text-zinc-400">高 <span className="text-up-ink dark:text-up">{fmt(d.high)}</span></span>
          <span className="text-zinc-600 dark:text-zinc-400">低 <span className="text-down-ink dark:text-down">{fmt(d.low)}</span></span>
          <span className="text-zinc-600 dark:text-zinc-400">收 <span className={pctCls(dPct)}>{fmt(d.close)}</span></span>
          <span className={pctCls(dPct)}>{dPct != null ? `${dPct > 0 ? "+" : ""}${dPct.toFixed(2)}%` : "--"}</span>
          {d.volume != null && <span className="text-zinc-600 dark:text-zinc-400">量 <span className="text-zinc-700 dark:text-zinc-200">{(d.volume / 100).toLocaleString()}手</span></span>}
          {d.amount != null && <span className="text-zinc-600 dark:text-zinc-400">额 <span className="text-zinc-700 dark:text-zinc-200">{fmtAmount(d.amount)}</span></span>}
          {MA_DEFS.filter(([key]) => ind[key]).map(([key, , inkCls]) => (
            <span key={key} className={`hidden lg:inline ${inkCls}`}>
              {key.toUpperCase()} <span className="text-zinc-700 dark:text-zinc-200">{hoverMA[key]?.[Math.max(0, Math.min(idx, (hoverMA[key]?.length ?? 1) - 1))] != null ? fmt(hoverMA[key][Math.max(0, Math.min(idx, (hoverMA[key]?.length ?? 1) - 1))]) : "--"}</span>
            </span>
          ))}
          {dEvents.length > 0 && (
            <span className="max-w-[280px] truncate text-sky-700 dark:text-sky-500" title={dEvents.map((m) => `${m.kind}${m.important ? "（重要）" : ""}：${m.title}`).join("\n")}>
              ◆ {dEvents[0].title}
            </span>
          )}
        </div>
      )}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-zinc-100 px-3 py-1 text-[11px] dark:border-zinc-800/60">
        <span className="text-zinc-600 dark:text-zinc-400">指标：</span>
        {toggles.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setInd((p) => ({ ...p, [key]: !p[key] }))}
            className={`rounded px-1.5 py-0.5 font-mono transition-colors ${
              ind[key]
                ? `bg-zinc-100 font-medium dark:bg-zinc-800 ${MA_LEGEND_CLS.get(key) ?? ""}`
                : "text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"
            }`}
          >
            {label}
          </button>
        ))}
        <span className="ml-auto hidden text-[10px] text-zinc-600 dark:text-zinc-400 xl:inline" title="B/S=模拟交易成交；紫[榜]=龙虎榜日；琥珀●=公告 蓝●=新闻（!=重要度高）——消息面与价格走势对照；hover 信息条显示当日事件标题">
          金叉/死叉为 MA5×MA10 技术信号 · 紫[榜]=龙虎榜日 · B/S=模拟交易成交 · 黄虚线=持仓成本 · 琥珀●=公告 蓝●=新闻(!=重要度高)
        </span>
        {/* 缩放控件：入文档流（原 absolute right-2 top-2 会压住 OHLC 信息条右端的 MA 数值） */}
        <div className="ml-auto flex shrink-0 items-center gap-1 xl:ml-2">
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
