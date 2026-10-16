import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { MinuteChart } from "./minute-chart";
import type { MinutePoint } from "@/lib/api";
import { mergeQuoteIntoMinutes } from "@/lib/kline-live";
import type { Quote } from "@/types/market";

const charts = vi.hoisted(() => [] as ReturnType<typeof makeChart>[]);
type Options = Record<string, unknown> & { autoscaleInfoProvider?: () => { priceRange: { minValue: number; maxValue: number } } };
type SeriesPoint = { time: number; value?: number; color?: string };
function makeSeries(kind: string, options: Options) {
  const opts = { ...options };
  return {
    kind, opts, values: [] as SeriesPoint[],
    applyOptions: vi.fn((next: Options) => { Object.assign(opts, next); }),
    setData: vi.fn(function(this: { values: SeriesPoint[] }, values: SeriesPoint[]) { this.values = values; }),
    createPriceLine: vi.fn((opts: Options) => ({ opts, applyOptions: vi.fn() })),
  };
}
function makeChart(opts: Options) {
  const series: ReturnType<typeof makeSeries>[] = [];
  const timeScale = { fitContent: vi.fn(), setVisibleLogicalRange: vi.fn() };
  const add = (kind: string, opts: Options) => {
    const s = makeSeries(kind, opts); series.push(s); return s;
  };
  return {
    opts, series, remove: vi.fn(), applyOptions: vi.fn(),
    addBaselineSeries: (opts: Options) => add("price", opts),
    addAreaSeries: (opts: Options) => add("price", opts),
    addLineSeries: (opts: Options) => add("line", opts),
    addHistogramSeries: (opts: Options) => add("volume", opts),
    priceScale: () => ({ applyOptions: vi.fn(), width: () => 68 }),
    timeScale: () => timeScale,
    subscribeCrosshairMove: vi.fn(), unsubscribeCrosshairMove: vi.fn(),
    clearCrosshairPosition: vi.fn(), setCrosshairPosition: vi.fn(),
  };
}
vi.mock("lightweight-charts", () => ({
  CrosshairMode: { Normal: 0, Magnet: 1 },
  createChart: (_container: unknown, opts: Options) => { const c = makeChart(opts); charts.push(c); return c; },
}));
beforeEach(() => { charts.length = 0; });
afterEach(cleanup);
const point = (price: number, minute = "30", avg: number | null = null, day = "17"): MinutePoint => ({
  ts: `2026-09-${day}T09:${minute}:00+08:00`, price, avg, source: "test_fixture",
});
const range = (s: ReturnType<typeof makeSeries>) => s.opts.autoscaleInfoProvider?.().priceRange;

describe("MinuteChart 首提交主题工作", () => {
  beforeEach(() => document.documentElement.classList.remove("dark"));
  afterEach(() => document.documentElement.classList.remove("dark"));
  const core = (chart: ReturnType<typeof makeChart>) => chart.series.filter(series => series.opts.lineStyle !== 3);

  it.each([{ theme: "dark", base: 10 }, { theme: "light", base: 10 }, { theme: "light", base: null }])(
    "$theme／参考价$base首建直接完整填入，只跳过已应用的同主题工作", ({ theme, base }) => {
      document.documentElement.classList.toggle("dark", theme === "dark");
      render(<MinuteChart points={[{ ...point(10), volume: 1000 }, { ...point(11, "31", 10.5), volume: 2000 }]} prevClose={base} />);
      expect(charts).toHaveLength(1);
      expect(charts[0].opts).toMatchObject({ autoSize: true });
      for (const series of core(charts[0])) expect(series.setData).toHaveBeenCalledOnce();
      expect(charts[0].series[0].values.some(value => value.value === 11)).toBe(true);
      expect(charts[0].applyOptions).not.toHaveBeenCalled();
    },
  );

  it("真实换主题仍重染量柱、主线、指数、竞价、事件和三条价格线，保留缩放", async () => {
    document.documentElement.classList.add("dark");
    render(<MinuteChart
      points={[{ ...point(10), volume: 1000 }, { ...point(11, "31", 10.5), volume: 2000 }]}
      prevClose={10} upperPrice={11} lowerPrice={9}
      auction={{ price: 9.5, pct: -5 }}
      index={{ prevClose: 3000, points: [point(3000), point(3030, "31")] }}
      newsEvents={[{ hhmm: "09:31", title: "fixture", count: 1, important: true }]}
    />);
    const chart = charts[0];
    for (const series of chart.series) series.setData.mockClear();
    chart.timeScale().setVisibleLogicalRange.mockClear();
    await act(async () => { document.documentElement.classList.remove("dark"); });
    expect(charts).toHaveLength(1);
    expect(chart.remove).not.toHaveBeenCalled();
    expect(chart.applyOptions).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ layout: { textColor: "#52525b" } }));
    expect(chart.series[0].opts.topLineColor).toBe("#dc2626");
    expect(chart.series.find(series => series.kind === "volume")?.values).toEqual(expect.arrayContaining([expect.objectContaining({ value: 2000, color: "rgba(220,38,38,0.8)" })]));
    expect(chart.series.find(series => series.opts.lineStyle === 3)?.opts.color).toBe("#7c3aed");
    expect(chart.series.find(series => series.opts.pointMarkersRadius === 4)?.opts.color).toBe("#b45309");
    expect(chart.series.find(series => series.opts.pointMarkersRadius === 3.5)?.opts.color).toBe("#0369a1");
    const priceLines = chart.series[0].createPriceLine.mock.results.map(result => result.value);
    expect(priceLines).toHaveLength(3);
    expect(priceLines[0].applyOptions).toHaveBeenCalledExactlyOnceWith({ color: "rgba(113,113,122,0.8)" });
    expect(priceLines[1].applyOptions).toHaveBeenCalledExactlyOnceWith({ color: "rgba(220,38,38,0.85)" });
    expect(priceLines[2].applyOptions).toHaveBeenCalledExactlyOnceWith({ color: "rgba(4,120,87,0.85)" });
    expect(chart.timeScale().setVisibleLogicalRange).not.toHaveBeenCalled();
  });

  it("无基准、基准晚到、基准变化、跨日和日期失效均为新图完整首填", () => {
    const points = [point(10), point(11, "31")];
    const view = render(<MinuteChart points={points} prevClose={null} referenceDate="2026-09-17" />);
    view.rerender(<MinuteChart points={points} prevClose={10} referenceDate="2026-09-17" />);
    view.rerender(<MinuteChart points={points} prevClose={11} referenceDate="2026-09-17" />);
    const tomorrow = [point(12, "30", null, "18")];
    view.rerender(<MinuteChart points={tomorrow} prevClose={11} referenceDate="2026-09-18" />);
    view.rerender(<MinuteChart points={tomorrow} prevClose={11} referenceDate={null} />);
    expect(charts).toHaveLength(5);
    for (const chart of charts) for (const series of core(chart)) expect(series.setData).toHaveBeenCalledOnce();
    expect(charts[1].series[0].opts.baseValue).toEqual({ type: "price", price: 10 });
    expect(charts[2].series[0].opts.baseValue).toEqual({ type: "price", price: 11 });
    expect(charts[3].series[0].values.some(value => value.value === 12)).toBe(true);
    expect(charts[4].series[0].opts.baseValue).toBeUndefined();
    for (const chart of charts.slice(0, -1)) expect(chart.remove).toHaveBeenCalledOnce();
  });

  it("后续数据与迟到指数继续原位更新，旧主题记录不抑制数据更新", () => {
    const initial = [point(10)];
    const view = render(<MinuteChart points={initial} prevClose={10} />);
    const chart = charts[0];
    for (const series of chart.series) series.setData.mockClear();
    chart.timeScale().setVisibleLogicalRange.mockClear();
    const next = [point(10), point(10.5, "31")];
    view.rerender(<MinuteChart points={next} prevClose={10} />);
    expect(chart.series[0].setData).toHaveBeenCalledOnce();
    expect(chart.series[1].setData).toHaveBeenCalledOnce();
    for (const series of core(chart).slice(2)) expect(series.setData).not.toHaveBeenCalled();
    view.rerender(<MinuteChart points={next} prevClose={10} index={{ prevClose: 3000, points: [point(3030, "31")] }} />);
    expect(charts).toHaveLength(1);
    expect(chart.series[0].values.some(value => value.value === 10.5)).toBe(true);
    expect(chart.series.find(series => series.opts.lineStyle === 3)?.values.some(value => value.value === 1)).toBe(true);
    expect(chart.series[0].setData).toHaveBeenCalledOnce();
    expect(chart.series[1].setData).toHaveBeenCalledOnce();
    for (const series of core(chart).slice(2)) expect(series.setData).not.toHaveBeenCalled();
    expect(chart.timeScale().setVisibleLogicalRange).not.toHaveBeenCalled();
  });

  it("浅色DOM hydration首次填色即正确，不为服务端主题快照再次重灌", async () => {
    const element = <MinuteChart points={[point(10), { ...point(11, "31", 10.5), volume: 2000 }]} prevClose={10} />;
    const container = document.createElement("div");
    container.innerHTML = renderToString(element); document.body.appendChild(container);
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => { root = hydrateRoot(container, element); });
      expect(charts).toHaveLength(1);
      for (const series of core(charts[0])) expect(series.setData).toHaveBeenCalledOnce();
      expect(charts[0].series.find(series => series.kind === "volume")?.setData.mock.calls[0][0]).toEqual(expect.arrayContaining([expect.objectContaining({ color: "rgba(220,38,38,0.8)" })]));
    } finally {
      await act(async () => { root?.unmount(); }); container.remove();
    }
  });
});

const restMinutes: MinutePoint[] = [
  { ts: "2026-10-09T09:59:00+08:00", price: 104.8, avg: 104.8, volume: 100_000, cum_volume: 900_000, cum_amount: 94_320_000, source: "tencent" },
  { ts: "2026-10-09T10:00:00+08:00", price: 104.8, avg: 104.8, volume: 100_000, cum_volume: 1_000_000, cum_amount: 104_800_000, source: "tencent" },
];
const liveMinuteQuote: Quote = {
  symbol: "600519", price: 105.3, volume: 1_100_000, amount: 115_830_000,
  data_timestamp: "2026-10-09T10:00:30+08:00", received_at: "2026-10-09T02:00:31Z", source: "tencent", quality: "high", quality_reasons: [],
};

describe("MinuteChart 实际轴域更新", () => {
  it("指数叠加晚到、同日推进和撤回均原位更新，主图与十字线订阅不重建", () => {
    const points = [point(100), point(101, "31")];
    const view = render(<MinuteChart points={points} prevClose={100} />);
    const chart = charts[0];
    const overlay = {points: [point(3000)], prevClose: 3000, referenceDate: "2026-09-17"};
    view.rerender(<MinuteChart points={points} prevClose={100} index={overlay} />);
    const series = chart.series.find(series => series.kind === "line" && series.opts.lineStyle === 3)!;
    expect(series.values.some(value => value.value === 0)).toBe(true);
    view.rerender(<MinuteChart points={points} prevClose={100} index={{...overlay, points: [point(3000), point(3030, "31")]}} />);
    expect(series.values.find(value => value.time === Date.UTC(2026, 8, 17, 9, 31) / 1000)?.value).toBe(1);
    view.rerender(<MinuteChart points={points} prevClose={100} index={{...overlay, referenceDate: "2026-09-18"}} />);
    expect(series.values.every(value => value.value === undefined)).toBe(true);
    expect(charts).toHaveLength(1);
    expect(chart.remove).not.toHaveBeenCalled();
    expect(chart.subscribeCrosshairMove).toHaveBeenCalledTimes(1);
    expect(chart.series[0].values.some(value => value.value === 101)).toBe(true);
  });
  it("经源时刻证明的结束标签原位跟价，浮层区分桶结束与实际报价钟", () => {
    const source: MinutePoint[] = [{ ...restMinutes[1], ts: "2026-10-09T10:01:00+08:00",
      time_label: "interval_end", observation_ts: "2026-10-09T10:00:30+08:00" }];
    const merged = mergeQuoteIntoMinutes(source, { ...liveMinuteQuote, data_timestamp: "2026-10-09T10:00:50+08:00" })!;
    const view = render(<MinuteChart points={source} prevClose={104} referenceDate="2026-10-09" />);
    const chart = charts[0];
    view.rerender(<MinuteChart points={merged} prevClose={104} referenceDate="2026-10-09" />);
    const time = Date.UTC(2026, 9, 9, 10, 1) / 1000;
    expect(chart.series[0].values.find(value => value.time === time)?.value).toBe(105.3);
    const move = chart.subscribeCrosshairMove.mock.calls[0][0] as (p: {time:number; point:{x:number;y:number}}) => void;
    fireEvent.pointerEnter(view.container.querySelector('[data-chart-surface]')!);
    act(() => move({ time, point: { x: 100, y: 100 } }));
    expect(view.container.querySelector('[data-f="hhmm"]')?.textContent).toBe("分钟截至 10:01 · 报价 10:00:50");
    expect(charts).toHaveLength(1);
  });
  it("域内 tick 继续更新数据，但不重复写入相同的坐标范围", () => {
    const view = render(<MinuteChart points={[point(100)]} prevClose={100} limitPct={10} />);
    const chart = charts[0];
    for (const series of chart.series) series.applyOptions.mockClear();
    view.rerender(<MinuteChart points={[point(101, "31", 100.5)]} prevClose={100} limitPct={10} />);
    expect(charts).toHaveLength(1);
    expect(chart.series[0].values.some(value => value.value === 101)).toBe(true);
    const axisWrites = chart.series.flatMap(series => series.applyOptions.mock.calls)
      .filter(([options]) => options.autoscaleInfoProvider);
    expect(axisWrites).toHaveLength(0);
    view.rerender(<MinuteChart points={[point(120, "32", 110)]} prevClose={100} limitPct={10} />);
    expect(range(chart.series[0])?.maxValue).toBeGreaterThanOrEqual(120);
    expect(chart.series[0].applyOptions).toHaveBeenCalled();
  });

  it("低频配置创建新图时，数值相同的轴域仍安装到新的系列", () => {
    const points = [point(100)];
    const view = render(<MinuteChart points={points} prevClose={100} limitPct={10} />);
    view.rerender(<MinuteChart points={points} prevClose={100} limitPct={10} exactBaseline={[1000]} />);
    expect(charts).toHaveLength(2);
    expect(range(charts[1].series[0])).toEqual(range(charts[0].series[0]));
  });
});

describe("MinuteChart 消费真实 WS 合并结果", () => {
  // lightweight-charts is a series recorder here; these checks cover data and DOM, not Canvas.
  it("同分钟累计量额推导可信分钟值，原位刷新均价线、量柱与已悬停浮层", () => {
    const view = render(<MinuteChart points={restMinutes} prevClose={104} referenceDate="2026-10-09" />);
    const chart = charts[0];
    const minuteTime = Date.UTC(2026, 9, 9, 10, 0) / 1000;
    const move = chart.subscribeCrosshairMove.mock.calls[0][0] as (p: {time:number; point:{x:number;y:number}}) => void;
    fireEvent.pointerEnter(view.container.querySelector('[data-chart-surface]')!);
    act(() => move({ time: minuteTime, point: { x: 100, y: 100 } }));
    const field = (name: string) => view.container.querySelector(`[data-f="${name}"]`)!;
    const hoveredAvg = field("avg");
    expect(hoveredAvg.textContent).toBe("104.80");
    expect(field("vol").textContent).toBe("1,000 手");
    expect(field("amt").textContent).toBe("1048 万");

    const merged = mergeQuoteIntoMinutes(restMinutes, liveMinuteQuote);
    expect(merged).not.toBeNull();
    view.rerender(<MinuteChart points={merged!} prevClose={104} referenceDate="2026-10-09" />);

    expect(charts).toHaveLength(1);
    expect(chart.remove).not.toHaveBeenCalled();
    expect(chart.series[0].values.find(value => value.time === minuteTime)?.value).toBe(105.3);
    expect(chart.series[2].values.find(value => value.time === minuteTime)?.value).toBe(105.3);
    expect(chart.series.find(series => series.kind === "volume")?.values.find(value => value.time === minuteTime)?.value).toBe(200_000);
    expect(field("avg")).toBe(hoveredAvg);
    expect(field("avg").textContent).toBe("105.30");
    expect(field("vol").textContent).toBe("2,000 手");
    expect(field("amt").textContent).toBe("2151 万");
    expect(field("cum").textContent).toBe("1.16 亿");
  });

  it.each([1, 3])("WS 跨 %s 分钟只更新价格、累计额和均价，分钟量额留空；REST 校准后原位恢复", (nextMinute) => {
    const view = render(<MinuteChart points={restMinutes} prevClose={104} referenceDate="2026-10-09" />);
    const chart = charts[0];
    const latestTime = Date.UTC(2026, 9, 9, 10, nextMinute) / 1000;
    const gapQuote: Quote = {
      ...liveMinuteQuote, price: 106.5, volume: 1_300_000, amount: 138_450_000,
      data_timestamp: `2026-10-09T10:0${nextMinute}:20+08:00`, received_at: `2026-10-09T02:0${nextMinute}:21Z`,
    };
    const merged = mergeQuoteIntoMinutes(restMinutes, gapQuote);
    expect(merged).not.toBeNull();
    view.rerender(<MinuteChart points={merged!} prevClose={104} referenceDate="2026-10-09" />);

    const volumes = chart.series.find(series => series.kind === "volume")!;
    expect(volumes.values.find(value => value.time === latestTime)).toEqual({ time: latestTime });
    for (let minute = 1; minute < nextMinute; minute++) {
      const missingTime = Date.UTC(2026, 9, 9, 10, minute) / 1000;
      expect(chart.series[0].values.find(value => value.time === missingTime)).toEqual({ time: missingTime });
      expect(volumes.values.find(value => value.time === missingTime)).toEqual({ time: missingTime });
    }
    expect(chart.series[0].values.find(value => value.time === latestTime)?.value).toBe(106.5);
    expect(chart.series[2].values.find(value => value.time === latestTime)?.value).toBe(106.5);
    const move = chart.subscribeCrosshairMove.mock.calls[0][0] as (p: {time:number; point:{x:number;y:number}}) => void;
    fireEvent.pointerEnter(view.container.querySelector('[data-chart-surface]')!);
    act(() => move({ time: latestTime, point: { x: 100, y: 100 } }));
    const field = (name: string) => view.container.querySelector(`[data-f="${name}"]`)!;
    expect(field("avg").textContent).toBe("106.50");
    expect(field("vol").textContent).toBe("--");
    expect(field("amt").textContent).toBe("--");
    expect(field("cum").textContent).toBe("1.38 亿");

    const calibrated: MinutePoint[] = nextMinute === 1 ? [
      ...restMinutes,
      { ts: "2026-10-09T10:01:00+08:00", price: 106.5, avg: 106.5, volume: 300_000, cum_volume: 1_300_000, cum_amount: 138_450_000, source: "tencent" },
    ] : [
      ...restMinutes,
      { ts: "2026-10-09T10:01:00+08:00", price: 105.3, avg: 105.3, volume: 100_000, cum_volume: 1_100_000, cum_amount: 115_830_000, source: "tencent" },
      { ts: "2026-10-09T10:02:00+08:00", price: 106, avg: 106, volume: 100_000, cum_volume: 1_200_000, cum_amount: 127_200_000, source: "tencent" },
      { ts: "2026-10-09T10:03:00+08:00", price: 106.5, avg: 106.5, volume: 100_000, cum_volume: 1_300_000, cum_amount: 138_450_000, source: "tencent" },
    ];
    view.rerender(<MinuteChart points={calibrated} prevClose={104} referenceDate="2026-10-09" />);

    expect(charts).toHaveLength(1);
    expect(chart.remove).not.toHaveBeenCalled();
    expect(volumes.values.find(value => value.time === latestTime)?.value).toBe(nextMinute === 1 ? 300_000 : 100_000);
    expect(field("vol").textContent).toBe(nextMinute === 1 ? "3,000 手" : "1,000 手");
    expect(field("amt").textContent).toBe(nextMinute === 1 ? "3365 万" : "1125 万");
    expect(field("cum").textContent).toBe("1.38 亿");
  });
});

it("magnetic focus shows the exact selected minute price and hides on leaving", () => {
  const view = render(<MinuteChart points={[point(10), point(10.257, "31")]} prevClose={10} />);
  expect(charts[0].opts.crosshair).toEqual({mode:1});
  const surface = view.container.querySelector('[data-chart-surface]')!;
  fireEvent.pointerEnter(surface);
  const callback = charts[0].subscribeCrosshairMove.mock.calls[0][0] as (p: {time:number; point:{x:number;y:number}}) => void;
  act(() => callback({time:Date.UTC(2026,8,17,9,31)/1000,point:{x:100,y:100}}));
  const focus = screen.getByLabelText("图表当前焦点");
  expect(focus.hidden).toBe(false);
  expect(focus.textContent).toContain("09:31");
  expect(focus.textContent).toContain("10.26");
  expect(focus.textContent).not.toContain("10.25");
  act(() => callback({time:Date.UTC(2026,8,17,12)/1000,point:{x:240,y:100}}));
  expect(focus.textContent).toContain("09:31");
  expect(charts[0].setCrosshairPosition).toHaveBeenLastCalledWith(10.257, Date.UTC(2026,8,17,9,31)/1000, charts[0].series[0]);
  fireEvent.pointerLeave(surface);
  expect(focus.hidden).toBe(true);
});

describe("MinuteChart 轴契约", () => {
  it("新极值原位更新两轴，不销毁图表", () => {
    const view = render(<MinuteChart points={[point(10)]} prevClose={10} limitPct={10} />);
    const c = charts[0];
    view.rerender(<MinuteChart points={[point(10), point(11.5, "31")]} prevClose={10} limitPct={10} />);
    expect(charts).toHaveLength(1);
    expect(c.remove).not.toHaveBeenCalled();
    expect(range(c.series[0])?.maxValue).toBeCloseTo(11.5);
    expect(range(c.series[1])?.minValue).toBeCloseTo(-10);
    expect(range(c.series[1])?.maxValue).toBeCloseTo(15);
  });

  it("百分比序列保留轴参与资格，仅隐藏线条；formatter 固定两位且无负零", () => {
    render(<MinuteChart points={[point(10)]} prevClose={10} />);
    const pct = charts[0].series[1];
    expect(pct.opts.visible).not.toBe(false);
    expect(pct.opts.lineVisible).toBe(false);
    expect(pct.opts.priceFormat).toMatchObject({ type: "custom", minMove: 0.01 });
    const formatter = (pct.opts.priceFormat as { formatter: (value: number) => string }).formatter;
    const labels = [-0.12, -0.08, -0.04, -0.0049, 0, 0.04, 0.08, 0.12].map(formatter);
    expect(new Set(labels).size).toBe(labels.length - 1); // -0.0049 与 0 都应归一为同一个 0.00%
    expect(labels).not.toContain("−0.00%");
    expect(formatter(0.1)).toBe("0.10%");
    expect(charts[0].opts.handleScale).toEqual({ axisPressedMouseMove: { price: false, time: true } });
  });

  it("竞价百分比沿同一格式规则，近零值不显示负零", () => {
    render(<MinuteChart points={[point(10)]} prevClose={10} auction={{ price: 10, pct: -0.0049 }} />);
    expect(screen.getByText("竞价 0.00%")).toBeTruthy();
    expect(screen.queryByText(/−0\.00%/)).toBeNull();
  });

  it("均价、竞价和叠加共用同一价格域，叠加极值不会只撑大左轴", () => {
    render(<MinuteChart points={[point(10, "30", 12)]} prevClose={10} limitPct={10}
      auction={{ price: 8, pct: -20 }} index={{ prevClose: 100, points: [point(150)] }} />);
    const s = charts[0].series;
    for (const entry of s.filter(x => x.kind !== "volume")) {
      const r = range(entry);
      expect(r).toBeDefined();
      expect(r?.minValue).toBeCloseTo(entry.opts.priceScaleId === "left" ? -20 : 8);
      expect(r?.maxValue).toBeCloseTo(entry.opts.priceScaleId === "left" ? 50 : 15);
    }
  });

  it("无效价格留空槽并提示，不绘制零价或伪百分比", () => {
    render(<MinuteChart points={[point(0), point(-1, "31"), point(10, "32", Infinity)]} prevClose={10} />);
    const s = charts[0].series;
    expect(s[0].values.filter(x => x.value !== undefined).map(x => x.value)).toEqual([10]);
    expect(s[1].values.filter(x => x.value !== undefined).map(x => x.value)).toEqual([0]);
    expect(s[2].values.filter(x => x.value !== undefined)).toEqual([]);
    expect(screen.getByText(/无效价格/)).toBeTruthy();
  });

  it.each([0, -1, NaN, Infinity])("非法昨收 %s 隐藏百分比，并告知缺少有效基准", (base) => {
    render(<MinuteChart points={[point(10)]} prevClose={base} />);
    expect(charts[0].series.some(x => x.opts.priceScaleId === "left")).toBe(false);
    expect(screen.getByText(/涨跌幅基准无效/)).toBeTruthy();
  });

  it("跨日以新日期与新参考日重建槽位，旧日图表得到清理", () => {
    const view = render(<MinuteChart points={[point(10)]} prevClose={10} referenceDate="2026-09-17" />);
    view.rerender(<MinuteChart points={[point(10, "30", null, "18")]} prevClose={10} referenceDate="2026-09-18" />);
    expect(charts).toHaveLength(2);
    expect(charts[0].remove).toHaveBeenCalledOnce();
    expect(new Date(charts[1].series[0].values[0].time * 1000).toISOString()).toContain("2026-09-18");
  });

  it("参考日未知/不一致/分钟点混日时只展示真实价格，不伪造百分比轴", () => {
    const view = render(<MinuteChart points={[point(10)]} prevClose={10} referenceDate={null} />);
    expect(charts[0].series.some(x => x.opts.priceScaleId === "left")).toBe(false);
    expect(screen.getByText(/参考日未知/)).toBeTruthy();

    view.rerender(<MinuteChart points={[point(10)]} prevClose={10} referenceDate="2026-09-18" />);
    expect(charts.at(-1)?.series.some(x => x.opts.priceScaleId === "left")).toBe(false);
    expect(screen.getByText(/参考日不一致/)).toBeTruthy();

    const beforeMixed = charts.at(-1)!;
    view.rerender(<MinuteChart points={[point(10), point(10.1, "31", null, "18")]} prevClose={10} referenceDate="2026-09-17" />);
    expect(beforeMixed.remove).toHaveBeenCalled();
    expect(screen.getByText(/分时交易日混合/)).toBeTruthy();
    expect(screen.queryByText(/量比/)).toBeNull();
  });

  it("叠加指数必须与自身参考日及主图交易日一致，否则不画归一线", () => {
    const sameDay = { prevClose: 100, referenceDate: "2026-09-17", points: [point(101)] };
    const view = render(
      <MinuteChart points={[point(10)]} prevClose={10} referenceDate="2026-09-17" index={sameDay} />
    );
    expect(charts[0].series.filter(x => x.opts.priceScaleId === "left")).toHaveLength(2);

    view.rerender(
      <MinuteChart
        points={[point(10)]}
        prevClose={10}
        referenceDate="2026-09-17"
        index={{ prevClose: 100, referenceDate: "2026-09-18", points: [point(101, "30", null, "18")] }}
      />
    );
    expect(screen.getByText("叠加指数与主图非同一交易日")).toBeTruthy();
    const latest = charts.at(-1)!;
    const indexSeries = latest.series.find(x => x.opts.priceScaleId === "left" && x.opts.lineStyle === 3)!;
    expect(indexSeries.values.every(value => value.value === undefined)).toBe(true);
    expect(charts).toHaveLength(1);

    view.rerender(
      <MinuteChart
        points={[point(10)]}
        prevClose={10}
        referenceDate="2026-09-17"
        index={{ prevClose: 100, referenceDate: "2026-09-18", points: [point(101)] }}
      />
    );
    expect(screen.getByText("叠加指数参考日不一致")).toBeTruthy();
  });

  it("同日换源只原位灌新数据，不销毁图表", () => {
    const first = { ...point(10), source: "tencent" };
    const view = render(<MinuteChart points={[first]} prevClose={10} referenceDate="2026-09-17" />);
    const c = charts[0];
    view.rerender(<MinuteChart points={[{ ...point(10.2), source: "tdx" }]} prevClose={10} referenceDate="2026-09-17" />);
    expect(charts).toHaveLength(1);
    expect(c.remove).not.toHaveBeenCalled();
    expect(c.series[0].values.some(v => v.value === 10.2)).toBe(true);
  });

  it("切股 key 变化强制重挂载，上一标的轴与数据不会串到下一标的", () => {
    const view = render(<MinuteChart key="600519" points={[point(10)]} prevClose={10} referenceDate="2026-09-17" />);
    const first = charts[0];
    view.rerender(<MinuteChart key="000001" points={[point(20)]} prevClose={20} referenceDate="2026-09-17" />);
    expect(charts).toHaveLength(2);
    expect(first.remove).toHaveBeenCalledOnce();
    expect(charts[1].series[0].values.some(v => v.value === 20)).toBe(true);
    expect(charts[1].series[1].values.filter(v => v.value !== undefined).map(v => v.value)).toEqual([0]);
  });

  it("低价股限价线使用传入实际值，缺失一侧不伪造", () => {
    const view = render(<MinuteChart points={[point(0.85)]} prevClose={0.85} limitPct={10} upperPrice={0.94} lowerPrice={0.77} />);
    expect(charts[0].series[0].createPriceLine.mock.calls.map(([opts]) => [opts.title, opts.price]))
      .toEqual([["昨收", 0.85], ["涨停", 0.94], ["跌停", 0.77]]);
    view.rerender(<MinuteChart points={[point(0.85)]} prevClose={0.85} limitPct={10} lowerPrice={0.77} />);
    expect(charts[1].series[0].createPriceLine.mock.calls.map(([opts]) => opts.title)).toEqual(["昨收", "跌停"]);
    expect(screen.getByText("涨停价缺失")).toBeTruthy();

    view.rerender(<MinuteChart points={[point(0.85)]} prevClose={0.85} limitPct={10} upperPrice={0.94} />);
    expect(screen.getByText("跌停价缺失")).toBeTruthy();

    view.rerender(<MinuteChart points={[point(0.85)]} prevClose={0.85} limitPct={10} upperPrice={-1} lowerPrice={0} />);
    expect(screen.getByText("涨跌停价缺失")).toBeTruthy();
  });

  it("参考范围外的有效价格保留并披露，不当成脏点删除", () => {
    render(<MinuteChart points={[point(25)]} prevClose={10} limitPct={10} />);
    expect(charts[0].series[0].values.some(p => p.value === 25)).toBe(true);
    expect(screen.getByText("超出名义参考范围")).toBeTruthy();
  });

  it("叠加指数的无效价格和参考价同样披露", () => {
    render(<MinuteChart points={[point(10)]} prevClose={10} index={{ prevClose: Infinity, points: [point(0)] }} />);
    expect(screen.getByText("叠加指数基准无效")).toBeTruthy();
    expect(screen.getByText(/无效价格已留空/)).toBeTruthy();
  });
});

describe("同帧只灌入变化的分时序列", () => {
  it("仅价格变化不重灌未变化的均价与分钟量", () => {
    const first = [point(10, "30", 9.9), point(10.1, "31", 10)];
    const view = render(<MinuteChart points={first} prevClose={9.8}/>);
    const chart = charts[0];
    const avg = chart.series.find(s => s.kind === "line" && s.values.some(p => p.value === 9.9))!;
    const vol = chart.series.find(s => s.kind === "volume")!;
    const before = [avg.setData.mock.calls.length, vol.setData.mock.calls.length];
    view.rerender(<MinuteChart points={[first[0], {...first[1], price: 10.2}]} prevClose={9.8}/>);
    expect(avg.setData.mock.calls.length).toBe(before[0]);
    expect(vol.setData.mock.calls.length).toBe(before[1]);
    expect(chart.series.find(s=>s.kind === "price")!.values.some(p=>p.value === 10.2)).toBe(true);
  });
});


it("bounds price labels above volume without clipping actual out-of-band values", () => {
  const view = render(<MinuteChart points={[point(15.27)]} prevClose={16.97} limitPct={10} upperPrice={18.67} lowerPrice={15.27} />);
  const chart = charts[0];
  const left = chart.series.find(series => series.opts.priceScaleId === "left")!;
  const formatter = () => (left.opts.priceFormat as {formatter: (value: number) => string}).formatter;
  expect(formatter()(-15)).toBe("");
  expect(formatter()(-10)).toBe("−10.00%");
  expect(screen.queryByText("超出名义参考范围")).toBeNull();
  view.rerender(<MinuteChart points={[point(14)]} prevClose={16.97} limitPct={10} upperPrice={18.67} lowerPrice={15.27} />);
  expect(formatter()(-15)).toBe("−15.00%");
  expect(charts).toHaveLength(1);
  expect(chart.remove).not.toHaveBeenCalled();
});
