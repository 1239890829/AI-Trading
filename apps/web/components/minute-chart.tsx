"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  createChart,
  CrosshairMode,
  HistogramData,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  LineData,
  Time,
} from "lightweight-charts";
import type { MinutePoint as P } from "@/lib/api";
import { computeMinuteAxis, isPositivePrice } from "@/lib/minute-axis";
import { tradingSeqFromHHMM } from "@/lib/market-hours";
import { readChartTheme, useChartTheme, type ChartTheme } from "@/lib/chart-theme";
import type { MinuteNewsEvent } from "@/lib/event-markers";

/**
 * 当日分时图（docs/archive/minute-chart-plan.md 模块 1+2+P1，2026-08-30）。
 *
 * 坐标系：单一价格域派生涨跌幅上下界，横盘日保留 0.5% 地板；
 * 右轴绝对价格、左轴涨跌幅（隐藏 % 序列承载）；昨收虚线基准。
 * 曲线：价格（面积）+ 均价线（黄）+ 上证叠加（紫虚线，左轴 % 归一）+ 量能副图（红涨绿跌）。
 * 量比：精确口径（TDX 5 日同期基线）优先，缺失回退近似（昨日量 × 已开市分钟/240）。
 * 昨日量取日 K 倒数第二根提供（bars[-2] 在盘中是今日实时 bar，休市日是分时日本身，
 * 两种场景下 bars[-2] 都恰好是"分时日的上一交易日"）。分子分母同为股，单位已实测一致。
 * 交互：十字光标浮层（触摸同源），ref 直改 DOM 不走 React state。
 * 纵轴区间（2026-09-04 用户需求）：有板块涨跌幅限制（limitPct，来自 lib/price-limit：
 * 主板 ±10（含 ST）/ 创业·科创 ±20 / 北交 ±30）→ 以名义限制区间为参考，
 * 涨停/跌停线只使用调用方提供的实际价格；指数/无法识别 → 回退原「当日波幅
 * 对称区间」（昨收中心，0.5% 地板防抖）。
 * ⚠️ 2026-09-11：真实行情若越出名义带（除权/换源/昨收口径不一致），区间并入越界点，
 * 不再把曲线裁到图外；两轴及所有叠加序列随新极值同步更新。
 * prevClose 缺失时整体降级为库默认自适应坐标 + 浮层隐藏涨跌幅，绝不臆造基准。
 *
 * 创建/数据分离（2026-09-02 用户反馈"刷新闪烁"）：原实现 effect 依赖 points——
 * 每次行情 tick（WS 合成/60s 校准）整个 chart 销毁重建，闪烁且丢十字光标。
 * 现在 chart/series 只随低频配置（prevClose/叠加/竞价/基线/事件晚到）重建，
 * points 高频变化走 setData 全量原位重灌（不销毁图表、不闪）。⚠️ 不能用
 * series.update()：槽位以全天 whitespace 占位，series 最后时间点恒为 15:00，
 * update(当前分钟) 必抛 "Cannot update oldest data"（实测崩溃，见 fillAll 注释）。
 *
 * 红涨绿跌（2026-09-02 用户反馈）：价格线用 BaselineSeries 以昨收为基值——
 * 高于昨收的时段红、低于绿（A股惯例），渐变填充随基线自动分段；昨收缺失时
 * 降级为单色面积线（无从判向，不臆造基准）。
 *
 * 悬停稳定（2026-09-02 用户反馈"悬浮框/十字线闪烁"）：实测根因是库在每次
 * 行情更新后经 updateCrosshair 重放 crosshairMove（鼠标静止时也 ~2次/秒），
 * 原 handler 每次全量重建 tooltip innerHTML（实测 48 节点/秒增删）→ 悬浮框
 * 内容节点不停撕倒重建即闪烁。修复：① 浮层改为手术式 DOM 更新——骨架只在
 * 首次写入，之后仅 patch 各字段 textContent/className（零节点增删）；
 * ② 挂容器原生 pointerenter/leave 跟踪真实悬停态，空 param 事件在悬停中
 * 一律忽略（数据更新瞬态不再隐藏浮层）；③ 时间轴关闭 whitespace 替换时的
 * 可视区间右移（allowShiftVisibleRangeOnWhitespaceReplacement=false），
 * 消除每根新分钟线导致的整图横移跳动。
 */

/**
 * 画布调色板（P2-26，2026-09-11）——与 `kline-chart-pro.tsx` 同根因、同处置。
 *
 * lightweight-charts 的颜色是创建 series 时写死的十六进制，不认识 CSS 变量、也不认识
 * Tailwind 的 `dark:`（那些选择器只作用于 DOM）。亮色下这些"深色画布专用"色全部失效：
 * 均价线 `#eab308` 在浅卡片上约 **1.9:1**、绿线 `#10b981` 2.54、竞价点 `#f59e0b` 2.14。
 * 文本对比度扫描器看不见 canvas，故此前对比度专项一路报 0 违规。
 *
 * **深色档 = 全部历史值，逐字节不变**；亮色档取同色系足够深的档位（图形 ≥3:1）。
 * 注意分时的"红"与 K 线的 `#f43f5e` 本来就不是同一个红（分时用纯红 `#ef4444`），
 * 故亮色档各自加深到本色的深档，不强行统一到 rose 系。
 */
type MinutePalette = {
  axis: string;
  grid: string;
  up: string;
  down: string;
  upFill1: string;
  upFill2: string;
  downFill1: string;
  downFill2: string;
  flat: string;
  volUp: string;
  volDown: string;
  avg: string;
  prevClose: string;
  limitUp: string;
  limitDown: string;
  index: string;
  auction: string;
  event: string;
};

const MINUTE_PALETTE: Record<ChartTheme, MinutePalette> = {
  dark: {
    axis: "#a1a1aa",
    grid: "rgba(120,120,130,0.12)",
    up: "#ef4444",
    down: "#10b981",
    upFill1: "rgba(239,68,68,0.28)",
    upFill2: "rgba(239,68,68,0.02)",
    downFill1: "rgba(16,185,129,0.28)",
    downFill2: "rgba(16,185,129,0.02)",
    flat: "rgba(161,161,170,0.6)",
    volUp: "rgba(239,68,68,0.5)",
    volDown: "rgba(16,185,129,0.5)",
    avg: "#eab308",
    prevClose: "rgba(161,161,170,0.7)",
    limitUp: "rgba(239,68,68,0.55)",
    limitDown: "rgba(16,185,129,0.55)",
    index: "rgba(167,139,250,0.85)",
    auction: "#f59e0b",
    event: "#38bdf8",
  },
  light: {
    axis: "#52525b", // zinc-600，zinc-50 上 7.03:1（原 zinc-400 仅 2.46）—— 轴标签是文字
    grid: "rgba(120,120,130,0.18)",
    up: "#dc2626", // red-600，白底 4.83:1（原 ef4444 3.76 也过线，但与填充同深更耐看）
    down: "#047857", // emerald-700，白底 5.25:1（原 10b981 仅 2.54）
    upFill1: "rgba(220,38,38,0.28)",
    upFill2: "rgba(220,38,38,0.02)",
    downFill1: "rgba(4,120,87,0.28)",
    downFill2: "rgba(4,120,87,0.02)",
    flat: "rgba(113,113,122,0.8)", // zinc-500 @80% → 白底 3.36:1
    volUp: "rgba(220,38,38,0.8)",
    volDown: "rgba(4,120,87,0.8)",
    avg: "#a16207", // yellow-700，白底 4.72:1（原 eab308 约 1.9，亮色下均价线几乎消失）
    prevClose: "rgba(113,113,122,0.8)",
    limitUp: "rgba(220,38,38,0.85)",
    limitDown: "rgba(4,120,87,0.85)",
    index: "#7c3aed", // violet-600，白底 5.70:1（原 violet-400@85% 仅 2.32）
    auction: "#b45309", // amber-700，白底 4.81:1
    event: "#0369a1", // sky-700，白底 5.69:1
  },
};
/**
 * 北京时间 HH:MM（**热路径专用，勿与其它同名实现合并**）。
 *
 * 全站另有两份语义相同的 `bjHHMM`（`lib/kline-live.ts`、`detail/minute-decision-panel.tsx`），
 * 走 `toLocaleTimeString("sv-SE", { timeZone: "Asia/Shanghai" })`。那份时区语义更稳妥，
 * 但每次调用要建 Intl formatter；此处按分钟点逐点调用（构建 byHHMM 索引、游标定位、
 * 每次 tick 重建），240 点 × 每次重渲染的量级下，手算偏移比 Intl 快一个数量级。
 * 两者对 UTC 锚定的 ISO 时间戳输出一致，故只在**这条热路径**保留手算版本。
 */
const BJ_OFFSET = 8 * 3600;

/**
 * 已开市交易分钟数（11:30-13:00 午休不计），clamp 到 [1,240]。
 *
 * 轴位来自 `lib/market-hours.ts::tradingSeqFromHHMM`（全站唯一的交易分钟轴，
 * 与后端 `_sina_bar_seq` 同口径）——2026-09-12 评审 R-3：此前本文件与
 * `flow-intraday-chart.tsx` 各写一份映射，午休段两处算法还不一致。
 * 本函数只负责**把轴位转成「已过分钟数」**：09:30 视为第 1 分钟（下界 1 而
 * 不是 0），量比分母 `yesterdayVol * (elapsed / 240)` 与基线槽位
 * `floor(elapsed / 5) - 1` 都依赖这个 1 基口径。
 */
function tradingMinutesElapsed(bjIso: string): number {
  const seq = tradingSeqFromHHMM(bjIso.slice(11, 16));
  return Math.min(Math.max(seq, 1), 240);
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
  /** 有昨收 → BaselineSeries（红涨绿跌分段）；降级（无昨收）→ AreaSeries 单色。 */
  price: ISeriesApi<"Area"> | ISeriesApi<"Baseline"> | null;
  pct: ISeriesApi<"Line"> | null;
  avg: ISeriesApi<"Line"> | null;
  vol: ISeriesApi<"Histogram"> | null;
  /** 叠加线（大盘/竞价点/事件点）与价格线：主题切换要一并换色，故留引用（P2-26）。 */
  index: ISeriesApi<"Line"> | null;
  auction: ISeriesApi<"Line"> | null;
  events: ISeriesApi<"Line"> | null;
  /** 昨收/涨停/跌停虚线：颜色随主题走，`pick` 指向调色板色槽（不依赖数组顺序）。 */
  priceLines: { pl: IPriceLine; pick: (p: MinutePalette) => string }[];
  slots: number[];
  base0: number;
};

const emptyBundle = (): SeriesBundle => ({
  chart: null,
  price: null,
  pct: null,
  avg: null,
  vol: null,
  index: null,
  auction: null,
  events: null,
  priceLines: [],
  slots: [],
  base0: 0,
});

export function MinuteChart({
  points,
  prevClose,
  yesterdayVol,
  index,
  auction,
  exactBaseline,
  newsEvents,
  limitPct,
  upperPrice,
  lowerPrice,
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
  /**
   * 板块名义涨跌幅限制（%，来自 lib/price-limit）：有值 → 作为显示范围参考；
   * null/undefined（指数/判不出）→
   * 回退当日波幅对称区间。缺失绝不臆造。
   */
  limitPct?: number | null;
  /** 实际限价只用 Quote 的已有字段，缺失一侧不推算、不画线。 */
  upperPrice?: number | null;
  lowerPrice?: number | null;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  // 画布配色随主题走（P2-26）：theme 变化 → applyTheme 把新色套到既有 series 上，不重建图表。
  const theme = useChartTheme();
  const pal = MINUTE_PALETTE[theme];
  const axis = useMemo(() => {
    if (!isPositivePrice(prevClose)) return null;
    const prices = points.flatMap((p) => [p.price, p.avg]);
    prices.push(upperPrice, lowerPrice);
    if (index && isPositivePrice(index.prevClose)) {
      for (const p of index.points) {
        if (isPositivePrice(p.price)) prices.push(prevClose * p.price / index.prevClose);
      }
    }
    return computeMinuteAxis({ prevClose, limitPct: limitPct ?? null, prices, auctionPrice: auction?.price });
  }, [points, prevClose, limitPct, upperPrice, lowerPrice, auction, index]);
  const invalidPrices = points.flatMap((p) => [p.price, p.avg]).concat(auction?.price, index?.points.map(p => p.price) ?? [])
    .filter((v) => v != null && !isPositivePrice(v)).length;

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
    if (index && isPositivePrice(index.prevClose) && index.points.length > 0) {
      const lastIdx = index.points[index.points.length - 1];
      if (isPositivePrice(lastIdx.price)) idxPct = ((lastIdx.price - index.prevClose) / index.prevClose) * 100;
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
  // 悬停稳定（2026-09-02）：容器真实悬停态 + 最近一次有效光标参数 + 浮层补丁状态。
  // 库会在每次行情更新后重放 crosshairMove（鼠标静止也 ~2次/秒），空 param 的
  // 瞬态事件不得隐藏浮层；浮层 DOM 走手术式 patch（见创建 effect 内 renderTip）。
  const insideRef = useRef(false);
  const lastParamRef = useRef<{ time: Time; point: { x: number; y: number } } | null>(null);
  const tipPatchRef = useRef<{ key: string; x: number; y: number }>({ key: "", x: -1, y: -1 });
  const renderTipRef = useRef<((time: Time, point: { x: number; y: number }) => void) | null>(null);

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
  function fillAll(s: SeriesBundle, pts: P[], base: number | null | undefined, palette: MinutePalette) {
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
      if (p && isPositivePrice(p.price)) prevP = p.price;
    }
    const volColor = (q: P): string => {
      const prevPrice = prevPriceByHHMM.get(bjHHMM(q)) ?? null;
      return prevPrice == null || q.price === prevPrice ? palette.flat : q.price > prevPrice ? palette.volUp : palette.volDown;
    };
    const over = <T,>(pick: (p: P) => T | null): { time: Time; value?: T }[] =>
      s.slots.map((sec, i) => {
        const p = byHHMM.get(slotHHMMs[i]);
        const v = p ? pick(p) : null;
        return (v == null ? { time: sec as Time } : { time: sec as Time, value: v }) as { time: Time; value?: T };
      });
    s.price.setData(over((p) => isPositivePrice(p.price) ? p.price : null) as never);
    if (s.pct && isPositivePrice(base)) {
      s.pct.setData(over((p) => (isPositivePrice(p.price) ? ((p.price - base) / base) * 100 : null)) as never);
    }
    s.avg?.setData(over((p) => isPositivePrice(p.avg) ? p.avg : null) as never);
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
  const pointDate = hasPoints ? new Date(new Date(points[0].ts).getTime() + BJ_OFFSET * 1000).toISOString().slice(0, 10) : "";
  // 低频配置（晚到即整图重建一次，次数 ≤3）：昨收/大盘叠加/竞价/量比基线/事件点
  useEffect(() => {
    if (!ref.current || !hasPoints) return;
    const cur = pointsRef.current;
    const hasBase = isPositivePrice(prevClose);
    // 首建配色同步读 DOM（不读 state）：主题是 documentElement 上的 class，它才是权威值。
    const p = MINUTE_PALETTE[readChartTheme()];

    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: p.axis },
      grid: {
        vertLines: { color: p.grid },
        horzLines: { color: p.grid },
      },
      // allowShiftVisibleRangeOnWhitespaceReplacement=false：盘中每根新分钟线
      // 把 whitespace 槽替换为数据点时，库默认会把可视区间右移 1 槽——悬停中
      // 图表整体横移一格，视觉上就是周期性跳动。全天槽位固定，无需移动。
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderVisible: false,
        rightOffset: 1,
        allowShiftVisibleRangeOnWhitespaceReplacement: false,
      },
      rightPriceScale: { borderVisible: false },
      leftPriceScale: hasBase ? { visible: true, borderVisible: false } : { visible: false },
      // 库的左右轴可独立拖伸，会破坏价↔百分比映射。纵轴统一自动缩放；时间缩放保留。
      handleScale: { axisPressedMouseMove: { price: false, time: true } },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const s = seriesRef.current;
    s.chart = chart;
    // 重建会连带作废旧 priceLine（随 chart.remove 一起销毁）⇒ 引用同步清空
    s.priceLines = [];
    // 验收/调试挂点：canvas 内容无 DOM 文本可读，agent-browser 文本通道验收
    // （校验纵轴区间/margins）依赖经容器元素读取的图表实例与区间快照。
    const dbg = ref.current as unknown as {
      __minuteChart?: IChartApi;
      __minuteRange?: { min: number; max: number } | null;
      /** 真实行情越出名义涨跌停带（除权/换源/昨收口径不一致）时为 true。 */
      __minuteOutOfBand?: boolean;
    };
    dbg.__minuteChart = chart;
    const { slots, base0 } = buildSlots(cur[0]);
    s.slots = slots;
    s.base0 = base0;

    // ---- 价格线：红涨绿跌（BaselineSeries 以昨收为基值分段变色）----
    // 高于昨收的时段红、低于绿；渐变填充同样随基线分段（A股分时惯例）。
    // 昨收缺失时降级为单色面积线（无从判向，不臆造基准，浮层同步隐藏涨跌幅）。
    const series: ISeriesApi<"Area"> | ISeriesApi<"Baseline"> = hasBase
      ? chart.addBaselineSeries({
          baseValue: { type: "price", price: prevClose! },
          topLineColor: p.up,
          topFillColor1: p.upFill1,
          topFillColor2: p.upFill2,
          bottomLineColor: p.down,
          bottomFillColor1: p.downFill1,
          bottomFillColor2: p.downFill2,
          lineWidth: 2,
          priceLineVisible: false,
        })
      : chart.addAreaSeries({
          lineColor: p.up,
          topColor: p.upFill1,
          bottomColor: p.upFill2,
          lineWidth: 2,
          priceLineVisible: false,
        });
    s.price = series;

    // ---- 左轴涨跌幅（隐藏 % 序列）----
    if (hasBase) {
      s.pct = chart.addLineSeries({
        priceScaleId: "left",
        // visible=false 会把整个序列移出轴缩放；这里只隐藏曲线。
        lineVisible: false,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        priceFormat: { type: "percent", precision: 2, minMove: 0.01 },
      });
    }

    // ---- 均价线（黄）----
    s.avg = chart.addLineSeries({
      color: p.avg,
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

    fillAll(s, cur, prevClose, p);
    cursorRef.current = bjHHMM(cur[cur.length - 1]);
    lastPointsRef.current = cur;

    if (hasBase) {
      // 两轴必须使用相同留白；所有序列的范围由下方 axis effect 统一更新。
      const margins = isPositivePrice(limitPct) ? { top: 0.02, bottom: 0.26 } : { top: 0.2, bottom: 0.1 };
      chart.priceScale("right").applyOptions({ scaleMargins: margins });
      chart.priceScale("left").applyOptions({ scaleMargins: margins });
      s.priceLines.push({
        pl: series.createPriceLine({
          price: prevClose!,
          color: p.prevClose,
          lineWidth: 1,
          lineStyle: 2, // dotted
          axisLabelVisible: true,
          title: "昨收",
        }),
        pick: (q) => q.prevClose,
      });

      // 实际限价与显示范围分离：允许单侧缺失，不用名义比例冒充交易限价。
      if (isPositivePrice(upperPrice)) {
        s.priceLines.push({
          pl: series.createPriceLine({
            price: upperPrice,
            color: p.limitUp,
            lineWidth: 1,
            lineStyle: 1, // dashed
            axisLabelVisible: true,
            title: "涨停",
          }),
          pick: (q) => q.limitUp,
        });
      }
      if (isPositivePrice(lowerPrice)) {
        s.priceLines.push({
          pl: series.createPriceLine({
            price: lowerPrice,
            color: p.limitDown,
            lineWidth: 1,
            lineStyle: 1, // dashed
            axisLabelVisible: true,
            title: "跌停",
          }),
          pick: (q) => q.limitDown,
        });
      }

      // ---- 大盘叠加：上证归一化 % 曲线（可见，左轴同刻度）----
      if (index && isPositivePrice(index.prevClose) && index.points.length > 0) {
        const idxSeries = chart.addLineSeries({
          priceScaleId: "left",
          color: p.index,
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
            return i && isPositivePrice(i.price)
              ? ({ time: sec as Time, value: ((i.price - index.prevClose) / index.prevClose) * 100 } as LineData)
              : ({ time: sec as Time } as LineData);
          }) as never
        );
        s.index = idxSeries; // 留引用：主题切换换色用（P2-26）
      }
    }

    // ---- 集合竞价点（09:25，金色）：槽序列首点即 9:25 ----
    if (isPositivePrice(auction?.price) && cur.length > 0) {
      const auctionSeries = chart.addLineSeries({
        color: p.auction,
        lineWidth: 1,
        pointMarkersVisible: true,
        pointMarkersRadius: 4,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      auctionSeries.setData([{ time: slots[0] as never, value: auction.price }]);
      s.auction = auctionSeries; // 留引用：主题切换换色用（P2-26）
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
        if (!p || !isPositivePrice(p.price)) continue; // 无有效价格锚时不画
        const sec = base0 + Number(e.hhmm.slice(0, 2)) * 3600 + Number(e.hhmm.slice(3, 5)) * 60;
        evData.push({ time: sec as Time, value: p.price });
      }
      if (evData.length > 0) {
        const evSeries = chart.addLineSeries({
          color: p.event,
          lineWidth: 1,
          pointMarkersVisible: true,
          pointMarkersRadius: 3.5,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        evSeries.setData(evData as never);
        s.events = evSeries; // 留引用：主题切换换色用（P2-26）
      }
    }

    // ---- 十字光标浮层（手术式 DOM 更新，2026-09-02 悬停闪烁修复）----
    // 实测根因：库在每次行情更新后经 updateCrosshair 重放 crosshairMove（鼠标
    // 静止也 ~2次/秒），原 handler 每次全量重建 tooltip innerHTML（48 节点/秒
    // 增删）→ 悬浮框内容节点不停撕倒重建即闪烁。骨架只写一次，之后仅 patch
    // 字段 textContent/className（零节点增删）；位置/透明度仅在变化时写。
    const tooltip = tipRef.current;
    const tipSkeleton =
      `<div class="font-mono text-[11px] text-zinc-600 dark:text-zinc-400" data-f="hhmm"></div>` +
      `<div class="flex items-baseline gap-2"><span class="font-mono text-sm font-semibold tabular-nums" data-f="price"></span>` +
      `<span data-f="pct"></span></div>` +
      `<div class="mt-0.5 grid grid-cols-[auto,1fr] gap-x-2 gap-y-0.5 text-[11px] tabular-nums">` +
      `<span class="text-zinc-600 dark:text-zinc-400">均价</span><span class="font-mono text-amber-800 dark:text-amber-500"><span data-f="avg"></span> <span data-f="avgdev"></span></span>` +
      `<span class="text-zinc-600 dark:text-zinc-400">量比</span><span class="font-mono" data-f="lb"></span>` +
      `<span class="text-zinc-600 dark:text-zinc-400">分钟量</span><span class="font-mono" data-f="vol"></span>` +
      `<span class="text-zinc-600 dark:text-zinc-400">分钟额</span><span class="font-mono" data-f="amt"></span>` +
      `<span class="text-zinc-600 dark:text-zinc-400">累计额</span><span class="font-mono" data-f="cum"></span>` +
      `</div>` +
      `<div data-f="evrow" class="mt-1 border-t border-zinc-200 pt-1 dark:border-zinc-700"><span class="text-sky-700 dark:text-sky-500" data-f="ev"></span></div>`;

    /** param.time（伪 UTC 编码）→ 最近数据点；无数据返回 null。 */
    const nearestPoint = (time: Time): P | null => {
      const sec = (time as unknown as number) * 1000 - BJ_OFFSET * 1000;
      let best: P | null = null;
      let bestDiff = Infinity;
      for (const p of pointsRef.current) {
        const d = Math.abs(new Date(p.ts).getTime() - sec);
        if (d < bestDiff) {
          bestDiff = d;
          best = p;
        }
      }
      return best;
    };

    const renderTip = (time: Time, point: { x: number; y: number }) => {
      if (!tooltip) return;
      const d = nearestPoint(time);
      if (!d) return;
      const bjIso = new Date(new Date(d.ts).getTime() + 8 * 3600 * 1000).toISOString();
      const changePct = hasBase && isPositivePrice(d.price) ? ((d.price - prevClose!) / prevClose!) * 100 : null;
      const avgDevPct = isPositivePrice(d.avg) && isPositivePrice(d.price) ? ((d.price - d.avg) / d.avg) * 100 : null;
      const lb = lbRef.current(d.cum_volume, bjIso);
      const pts = pointsRef.current;
      const minuteAmount =
        d.cum_amount != null
          ? (() => {
              const i = pts.indexOf(d);
              const prev = i > 0 ? pts[i - 1].cum_amount : null;
              return prev != null ? (d.cum_amount! - prev) / 1e4 : null; // 万
            })()
          : null;
      const pctCls = (v: number | null) => (v == null ? "text-zinc-600 dark:text-zinc-400" : v > 0 ? "text-red-700 dark:text-red-500" : v < 0 ? "text-emerald-700 dark:text-emerald-500" : "text-zinc-600 dark:text-zinc-400");
      const lbCls = lb == null ? "text-zinc-600 dark:text-zinc-400" : lb >= 1.5 ? "text-red-700 dark:text-red-500" : lb >= 0.8 ? "text-amber-800 dark:text-amber-500" : "text-sky-700 dark:text-sky-500";
      // 事件行：光标分钟（±1 分钟容差）命中当日新闻时显示，长标题截断
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

      // 骨架只建一次；之后零节点增删，仅 patch 文本与类名
      if (tipPatchRef.current.key !== "built") {
        tooltip.innerHTML = tipSkeleton;
        tipPatchRef.current.key = "built";
      }
      const put = (f: string, text: string, cls?: string) => {
        const el = tooltip.querySelector<HTMLElement>(`[data-f="${f}"]`);
        if (!el) return;
        if (el.textContent !== text) {
          // 优先直写文本节点（characterData 变更，零节点增删）；textContent
          // 赋值会替换整个文本子节点（childList 抖动，悬停闪烁的次级来源）
          const first = el.firstChild;
          if (first && first === el.lastChild && first.nodeType === Node.TEXT_NODE) {
            first.nodeValue = text;
          } else {
            el.textContent = text;
          }
        }
        if (cls !== undefined && el.getAttribute("class") !== cls) el.setAttribute("class", cls);
      };
      put("hhmm", bjIso.slice(11, 16));
      put("price", isPositivePrice(d.price) ? d.price.toFixed(2) : "--");
      put("pct", fmtPct(changePct), `font-mono text-[11px] tabular-nums ${pctCls(changePct)}`);
      put("avg", isPositivePrice(d.avg) ? d.avg.toFixed(2) : "--");
      put("avgdev", avgDevPct != null ? fmtPct(avgDevPct) : "", pctCls(avgDevPct));
      put("lb", lb != null ? lb.toFixed(2) : "--", `font-mono ${lbCls}`);
      put("vol", d.volume != null ? Math.round(d.volume / 100).toLocaleString() + " 手" : "--");
      put("amt", minuteAmount != null ? minuteAmount.toFixed(0) + " 万" : "--");
      put("cum", d.cum_amount != null ? (d.cum_amount / 1e8).toFixed(2) + " 亿" : "--");
      const evEl = tooltip.querySelector<HTMLElement>('[data-f="ev"]');
      const evRowEl = tooltip.querySelector<HTMLElement>('[data-f="evrow"]');
      if (evEl && evRowEl) {
        const evText = ev ? `📰 ${ev.count > 1 ? `×${ev.count} ` : ""}${ev.title.length > 26 ? ev.title.slice(0, 26) + "…" : ev.title}` : "";
        if (evEl.textContent !== evText) evEl.textContent = evText;
        evRowEl.classList.toggle("hidden", !ev);
      }
      if (tooltip.style.opacity !== "1") tooltip.style.opacity = "1";
      // 位置只在变化时写（悬停中重放事件坐标不变 → 零样式写入）
      const box = ref.current!;
      const w = tooltip.offsetWidth || 150;
      const x = point.x + 14 + w > box.clientWidth ? Math.max(4, point.x - 14 - w) : point.x + 14;
      const y = Math.min(Math.max(4, point.y - 10), Math.max(4, box.clientHeight - tooltip.offsetHeight - 4));
      if (tipPatchRef.current.x !== x) {
        tooltip.style.left = `${x}px`;
        tipPatchRef.current.x = x;
      }
      if (tipPatchRef.current.y !== y) {
        tooltip.style.top = `${y}px`;
        tipPatchRef.current.y = y;
      }
    };
    renderTipRef.current = renderTip;

    const onMove = (param: { time?: Time; point?: { x: number; y: number } }) => {
      if (!tooltip) return;
      if (!param.time || !param.point) {
        // 空 param：真实离开（库 mouseleave）或数据更新瞬态。悬停中一律忽略，
        // 防止瞬态事件把浮层打隐又由下一拍恢复——即"闪烁"。
        if (!insideRef.current && tooltip.style.opacity !== "0") tooltip.style.opacity = "0";
        return;
      }
      lastParamRef.current = { time: param.time, point: param.point };
      renderTip(param.time, param.point);
    };

    // 容器原生指针跟踪：区分"离开"与"数据更新瞬态"的判据；真实离开时同步
    // 清掉库的十字线并隐藏浮层（浮层 pointer-events-none 不影响指针事件）。
    const markInside = () => {
      insideRef.current = true;
    };
    const markOutside = () => {
      insideRef.current = false;
      lastParamRef.current = null;
      if (tooltip && tooltip.style.opacity !== "0") tooltip.style.opacity = "0";
      try {
        chart.clearCrosshairPosition();
      } catch {}
    };
    const boxEl = ref.current!;
    boxEl.addEventListener("pointerenter", markInside);
    boxEl.addEventListener("pointermove", markInside);
    boxEl.addEventListener("pointerleave", markOutside);
    boxEl.addEventListener("pointercancel", markOutside);

    const unsub = chart.subscribeCrosshairMove(onMove);
    // 全天槽已在数据集里（whitespace 撑满 9:25-15:00），fitContent 即完整交易时段；
    // 左端 -1.5 给 9:25 竞价金点留出圆的空间
    chart.timeScale().fitContent();
    chart.timeScale().setVisibleLogicalRange({ from: -1.5, to: slots.length + 0.5 });

    // 图表低频重建（基线/叠加/竞价晚到）后：若指针仍悬停，恢复十字线与浮层，
    // 否则重建后十字线消失、浮层停留旧数据，直到下次鼠标移动。
    if (insideRef.current && lastParamRef.current) {
      const lp = lastParamRef.current;
      const best = nearestPoint(lp.time);
      if (best) {
        try {
          chart.setCrosshairPosition(best.price, lp.time, series as never);
        } catch {}
      }
      renderTip(lp.time, lp.point);
    }

    return () => {
      try {
        chart.unsubscribeCrosshairMove(onMove);
      } catch {}
      boxEl.removeEventListener("pointerenter", markInside);
      boxEl.removeEventListener("pointermove", markInside);
      boxEl.removeEventListener("pointerleave", markOutside);
      boxEl.removeEventListener("pointercancel", markOutside);
      renderTipRef.current = null;
      delete dbg.__minuteChart;
      delete dbg.__minuteRange;
      delete dbg.__minuteOutOfBand;
      chart.remove();
      seriesRef.current = emptyBundle();
      cursorRef.current = "";
      lastPointsRef.current = null;
      void unsub;
    };
    // 低频配置变化才重建；points 走增量 effect（下）。evByHHMM 供 crosshair 闭包。
  }, [hasPoints, pointDate, prevClose, index, auction, exactBaseline, newsEvents, limitPct, upperPrice, lowerPrice]);

  // 用最终价格域逐端派生左轴，均价/竞价/事件/指数不再各自撑大某一侧。
  // 更新 provider 会使库原位重算坐标；高频行情不重建 chart 或重置时间缩放。
  useEffect(() => {
    const s = seriesRef.current;
    if (!s.chart || !ref.current) return;
    const dbg = ref.current as HTMLDivElement & { __minuteRange?: { min: number; max: number } | null; __minuteOutOfBand?: boolean };
    dbg.__minuteRange = axis ? { min: axis.min, max: axis.max } : null;
    dbg.__minuteOutOfBand = axis?.outOfBand ?? false;
    if (!axis) return;
    for (const series of [s.price, s.avg, s.auction, s.events]) {
      series?.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: axis.min, maxValue: axis.max } }) });
    }
    for (const series of [s.pct, s.index]) {
      series?.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: axis.pctMin, maxValue: axis.pctMax } }) });
    }
  }, [axis, exactBaseline, newsEvents]);

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
    // 配色同步读 DOM（与创建 effect 同源）：量柱色是逐条写进数据的，必须带上当前档
    fillAll(s, points, prevClose, MINUTE_PALETTE[readChartTheme()]);
    cursorRef.current = bjHHMM(points[points.length - 1]);
    // 悬停中数据被整体替换：主动刷新浮层数值（库的 updateCrosshair 重放通常
    // 也会触发 onMove，此处兜底保证刷新不依赖重放行为；手术式 patch 零开销）
    if (insideRef.current && lastParamRef.current) {
      renderTipRef.current?.(lastParamRef.current.time, lastParamRef.current.point);
    }
  }, [points, prevClose]);

  /**
   * 主题切换：**只换色、不重建**（P2-26）——重建会丢用户的缩放/平移，也违背本文件
   * 顶部「创建与数据分离」的既有约定。需覆盖的颜色分两类，缺一不可：
   *   ① 配置型颜色（布局文字/网格/各 series 的 color/填充/价格线）→ `applyOptions` 直接改；
   *   ② **逐条写进数据的颜色**（量柱由 fillAll 逐条带 color 灌入）→ `applyOptions` 管不到，
   *      必须用新调色板再跑一次 `fillAll`（setData 为 Canvas 原位重绘：不重建、不闪、
   *      几何完全不变，只有色值换档）。
   */
  const applyTheme = useCallback((p: MinutePalette, baseline: boolean) => {
    const s = seriesRef.current;
    const chart = s.chart;
    if (!chart) return;
    chart.applyOptions({
      layout: { textColor: p.axis },
      grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
    });
    if (s.price) {
      // price 是 Baseline | Area 的联合类型，TS 无法自行收窄，按创建时的分支显式断言
      if (baseline) {
        (s.price as ISeriesApi<"Baseline">).applyOptions({
          topLineColor: p.up,
          topFillColor1: p.upFill1,
          topFillColor2: p.upFill2,
          bottomLineColor: p.down,
          bottomFillColor1: p.downFill1,
          bottomFillColor2: p.downFill2,
        });
      } else {
        (s.price as ISeriesApi<"Area">).applyOptions({
          lineColor: p.up,
          topColor: p.upFill1,
          bottomColor: p.upFill2,
        });
      }
    }
    s.avg?.applyOptions({ color: p.avg });
    s.index?.applyOptions({ color: p.index });
    s.auction?.applyOptions({ color: p.auction });
    s.events?.applyOptions({ color: p.event });
    for (const { pl, pick } of s.priceLines) pl.applyOptions({ color: pick(p) });
  }, []);

  // 主题 effect：声明在创建 effect 之后 ⇒ 挂载时先建图、后套色（幂等）。
  // 色值一律经 readChartTheme() 读 DOM（theme 仅作触发）：hydration 首帧 state 可能
  // 仍是服务端快照 "dark" 而实际 class 已是 light，用 state 取值会闪一帧深色画布。
  useEffect(() => {
    const p = MINUTE_PALETTE[readChartTheme()];
    applyTheme(p, isPositivePrice(prevClose));
    fillAll(seriesRef.current, pointsRef.current, prevClose, p);
  }, [theme, prevClose, applyTheme]);

  return (
    <div className="flex h-full w-full flex-col">
      {/* 角标行：量比 + 竞价 + 上证叠加图例——独立文档流行（原 absolute right-2 top-1.5
          浮层压在图表右上角价格标签/最新价区域），不占图表绘制空间、互不遮挡 */}
      <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 px-2 pb-0.5 pt-1 text-[11px]">
        {invalidPrices > 0 && <span className="text-amber-800 dark:text-amber-300">{invalidPrices} 个无效价格已留空</span>}
        {!isPositivePrice(prevClose) && <span className="text-amber-800 dark:text-amber-300">涨跌幅基准无效，仅展示价格</span>}
        {index && !isPositivePrice(index.prevClose) && <span className="text-amber-800 dark:text-amber-300">叠加指数基准无效</span>}
        {axis?.outOfBand && <span className="text-amber-800 dark:text-amber-300" title="已保留越界行情，请核对日期、参考价与来源">超出名义参考范围</span>}
        {auction?.pct != null && (
          <span
            className={`rounded border px-1.5 py-0.5 font-mono tabular-nums ${
              auction.pct >= 2 ? "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-500" : "border-zinc-500/40 bg-zinc-500/10 text-zinc-600 dark:text-zinc-400"
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
                ? "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-500"
                : badges.lb >= 0.8
                  ? "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-500"
                  : "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-500"
            }`}
            title="量比（近似）= 当日累计量 / (昨日全天量 × 已开市时间占比)；≥1.5 放量"
          >
            量比 {badges.lb.toFixed(2)}
          </span>
        )}
        {badges.idxPct != null && (
          <span className="rounded border border-violet-500/40 bg-violet-500/10 px-1.5 py-0.5 font-mono tabular-nums text-violet-700 dark:text-violet-400" title="上证指数叠加（左轴 %）">
            上证 {fmtPct(badges.idxPct)}
          </span>
        )}
        {newsEvents && newsEvents.length > 0 && (
          <span
            className="rounded border border-sky-500/40 bg-sky-500/10 px-1.5 py-0.5 text-sky-700 dark:text-sky-500"
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
