import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MinuteChart } from "./minute-chart";
import type { MinutePoint } from "@/lib/api";

const charts = vi.hoisted(() => [] as ReturnType<typeof makeChart>[]);
type Options = Record<string, unknown> & { autoscaleInfoProvider?: () => { priceRange: { minValue: number; maxValue: number } } };
function makeSeries(kind: string, options: Options) {
  return {
    kind, opts: { ...options }, values: [] as { time: number; value?: number }[],
    applyOptions(next: Options) { Object.assign(this.opts, next); },
    setData(values: { time: number; value?: number }[]) { this.values = values; },
    createPriceLine: vi.fn((opts: Options) => ({ opts, applyOptions: vi.fn() })),
  };
}
function makeChart(opts: Options) {
  const series: ReturnType<typeof makeSeries>[] = [];
  const add = (kind: string, opts: Options) => {
    const s = makeSeries(kind, opts); series.push(s); return s;
  };
  return {
    opts, series, remove: vi.fn(), applyOptions: vi.fn(),
    addBaselineSeries: (opts: Options) => add("price", opts),
    addAreaSeries: (opts: Options) => add("price", opts),
    addLineSeries: (opts: Options) => add("line", opts),
    addHistogramSeries: (opts: Options) => add("volume", opts),
    priceScale: () => ({ applyOptions: vi.fn() }),
    timeScale: () => ({ fitContent: vi.fn(), setVisibleLogicalRange: vi.fn() }),
    subscribeCrosshairMove: vi.fn(), unsubscribeCrosshairMove: vi.fn(),
    clearCrosshairPosition: vi.fn(), setCrosshairPosition: vi.fn(),
  };
}
vi.mock("lightweight-charts", () => ({
  CrosshairMode: { Normal: 0 },
  createChart: (_container: unknown, opts: Options) => { const c = makeChart(opts); charts.push(c); return c; },
}));
beforeEach(() => { charts.length = 0; });
afterEach(cleanup);
const point = (price: number, minute = "30", avg: number | null = null, day = "17"): MinutePoint => ({
  ts: `2026-09-${day}T09:${minute}:00+08:00`, price, avg, source: "test_fixture",
});
const range = (s: ReturnType<typeof makeSeries>) => s.opts.autoscaleInfoProvider?.().priceRange;

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

  it("百分比序列保留轴参与资格，仅隐藏线条", () => {
    render(<MinuteChart points={[point(10)]} prevClose={10} />);
    expect(charts[0].series[1].opts.visible).not.toBe(false);
    expect(charts[0].series[1].opts.lineVisible).toBe(false);
    expect(charts[0].opts.handleScale).toEqual({ axisPressedMouseMove: { price: false, time: true } });
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

  it("跨日以新日期重建槽位，旧日图表得到清理", () => {
    const view = render(<MinuteChart points={[point(10)]} prevClose={10} />);
    view.rerender(<MinuteChart points={[point(10, "30", null, "18")]} prevClose={10} />);
    expect(charts).toHaveLength(2);
    expect(charts[0].remove).toHaveBeenCalledOnce();
    expect(new Date(charts[1].series[0].values[0].time * 1000).toISOString()).toContain("2026-09-18");
  });

  it("低价股限价线使用传入实际值，缺失一侧不伪造", () => {
    const view = render(<MinuteChart points={[point(0.85)]} prevClose={0.85} limitPct={10} upperPrice={0.94} lowerPrice={0.77} />);
    expect(charts[0].series[0].createPriceLine.mock.calls.map(([opts]) => [opts.title, opts.price]))
      .toEqual([["昨收", 0.85], ["涨停", 0.94], ["跌停", 0.77]]);
    view.rerender(<MinuteChart points={[point(0.85)]} prevClose={0.85} limitPct={10} lowerPrice={0.77} />);
    expect(charts[1].series[0].createPriceLine.mock.calls.map(([opts]) => opts.title)).toEqual(["昨收", "跌停"]);
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
