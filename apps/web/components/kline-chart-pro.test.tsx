import { StrictMode } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KlineChartPro } from "./kline-chart-pro";
import type { Kline } from "@/types/market";
import type { EventMark } from "@/lib/event-markers";

type Options = Record<string, unknown>;
type Datum = Record<string, unknown>;
function makeSeries(kind: string, options: Options) {
  return {
    kind, options, setData: vi.fn<(values: Datum[]) => void>(),
    setMarkers: vi.fn<(values: Datum[]) => void>(), applyOptions: vi.fn<(options: Options) => void>(),
    createPriceLine: vi.fn((options: Options) => ({ options })), removePriceLine: vi.fn(),
  };
}
function makeChart(options: Options) {
  const series: ReturnType<typeof makeSeries>[] = [];
  const timeScale = { setVisibleLogicalRange: vi.fn(), getVisibleLogicalRange: () => ({ from: 4, to: 12 }) };
  const add = (kind: string, options: Options) => {
    const value = makeSeries(kind, options); series.push(value); return value;
  };
  return {
    options, series, remove: vi.fn(), applyOptions: vi.fn<(options: Options) => void>(),
    addCandlestickSeries: (options: Options) => add("candle", options),
    addLineSeries: (options: Options) => add("line", options),
    addHistogramSeries: (options: Options) => add("histogram", options),
    priceScale: () => ({ applyOptions: vi.fn() }), timeScale: () => timeScale, subscribeCrosshairMove: vi.fn(),
  };
}
const charts = vi.hoisted(() => [] as ReturnType<typeof makeChart>[]);
vi.mock("lightweight-charts", () => ({
  LineStyle: { Dashed: 2 },
  createChart: (_element: HTMLElement, options: Options) => {
    const chart = makeChart(options); charts.push(chart); return chart;
  },
}));

const bars = (count = 120, shift = 0): Kline[] => Array.from({ length: count }, (_, index) => {
  const close = 10 + index / 10 + shift;
  return {
    symbol: "600127", timeframe: "1d", ts: new Date(Date.UTC(2026, 5, index + 1)).toISOString(),
    open: close + (index % 2 ? 0.2 : -0.2), close, high: close + 0.4, low: close - 0.4,
    volume: 1000 + index, amount: 10_000 + index,
    source: "test_fixture", quality: "high", quality_reasons: [], received_at: "2026-10-14T15:01:00+08:00",
  };
});
const candle = (chart = charts.at(-1)!) => chart.series.find(series => series.kind === "candle")!;
const lastData = (series: ReturnType<typeof makeSeries>) => series.setData.mock.calls.at(-1)![0];
const marks = [{ date: "2026-09-28", side: "buy", price: 22, quantity: 100 }];
const events: EventMark[] = [
  { date: "2026-09-27", kind: "新闻", title: "fixture news", important: true },
  { date: "2026-09-28", kind: "公告", title: "fixture announcement", important: false },
];

beforeEach(() => {
  charts.length = 0; localStorage.clear(); document.documentElement.classList.remove("dark");
});
afterEach(() => { cleanup(); localStorage.clear(); document.documentElement.classList.remove("dark"); });

describe("KlineChartPro first-paint data ownership", () => {
  it.each(["light", "dark"])("synchronously fills each initial %s series and marker once", theme => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    const input = bars();
    render(<KlineChartPro bars={input} tradeMarks={marks} costPrice={18} />);
    expect(charts).toHaveLength(1);
    const chart = charts[0];
    expect(chart.options).toMatchObject({ autoSize: true });
    expect(chart.series).toHaveLength(9);
    for (const series of chart.series) expect(series.setData).toHaveBeenCalledOnce();
    expect(lastData(candle(chart)).at(-1)).toMatchObject({ time: "2026-09-28", close: input.at(-1)!.close });
    expect(candle(chart).setMarkers).toHaveBeenCalledExactlyOnceWith([expect.objectContaining({ time: "2026-09-28", text: "B 100股" })]);
    expect(candle(chart).createPriceLine).toHaveBeenCalledOnce();
    expect(candle(chart).removePriceLine).not.toHaveBeenCalled();
    expect(chart.timeScale().setVisibleLogicalRange).toHaveBeenCalledExactlyOnceWith({ from: 100, to: 122 });
  });

  it("fills new same-length bar data immediately without rebuilding, changing zoom or touching marks", () => {
    const view = render(<KlineChartPro bars={bars()} tradeMarks={marks} costPrice={18} />);
    const chart = charts[0];
    for (const series of chart.series) series.setData.mockClear();
    candle(chart).setMarkers.mockClear(); candle(chart).createPriceLine.mockClear();
    chart.timeScale().setVisibleLogicalRange.mockClear();
    const next = bars(120, 5);
    view.rerender(<KlineChartPro bars={next} tradeMarks={marks} costPrice={18} />);
    expect(charts).toHaveLength(1);
    expect(chart.remove).not.toHaveBeenCalled();
    for (const series of chart.series) expect(series.setData).toHaveBeenCalledOnce();
    expect(lastData(candle(chart)).at(-1)).toMatchObject({ close: next.at(-1)!.close });
    expect(chart.timeScale().setVisibleLogicalRange).not.toHaveBeenCalled();
    expect(candle(chart).setMarkers).not.toHaveBeenCalled();
    expect(candle(chart).createPriceLine).not.toHaveBeenCalled();
  });

  it("immediately fills a rebuilt indicator chart even when its bars and marks references are unchanged", () => {
    const input = bars();
    render(<KlineChartPro bars={input} tradeMarks={marks} costPrice={18} />);
    const previous = charts[0];
    fireEvent.click(screen.getByRole("button", { name: "MA5" }));
    expect(charts).toHaveLength(2);
    expect(previous.remove).toHaveBeenCalledOnce();
    const chart = charts[1];
    expect(chart.series).toHaveLength(8);
    for (const series of chart.series) expect(series.setData).toHaveBeenCalledOnce();
    expect(lastData(candle(chart)).at(-1)).toMatchObject({ close: input.at(-1)!.close });
    expect(candle(chart).setMarkers).toHaveBeenCalledOnce();
    expect(candle(chart).createPriceLine).toHaveBeenCalledOnce();
  });

  it("repaints per-bar colors, series, cost and both event kinds when the real DOM theme changes", async () => {
    document.documentElement.classList.add("dark");
    render(<KlineChartPro bars={bars()} tradeMarks={marks} eventMarks={events} costPrice={18} />);
    for (const name of ["BOLL", "成交额", "MACD", "事件"]) fireEvent.click(screen.getByRole("button", { name }));
    const chart = charts.at(-1)!;
    const chartCount = charts.length;
    for (const series of chart.series) series.setData.mockClear();
    candle(chart).setMarkers.mockClear(); candle(chart).createPriceLine.mockClear();
    candle(chart).removePriceLine.mockClear(); chart.timeScale().setVisibleLogicalRange.mockClear();
    await act(async () => { document.documentElement.classList.remove("dark"); });
    expect(charts).toHaveLength(chartCount);
    expect(chart.remove).not.toHaveBeenCalled();
    expect(chart.applyOptions).toHaveBeenLastCalledWith(expect.objectContaining({ layout: { textColor: "#52525b" } }));
    for (const series of chart.series) expect(series.setData).toHaveBeenCalledOnce();
    expect(candle(chart).applyOptions).toHaveBeenLastCalledWith(expect.objectContaining({ upColor: "#e11d48", downColor: "#047857" }));
    const histograms = chart.series.filter(series => series.kind === "histogram");
    expect(lastData(histograms[0])).toEqual(expect.arrayContaining([expect.objectContaining({ color: "rgba(225,29,72,0.7)" }), expect.objectContaining({ color: "rgba(4,120,87,0.7)" })]));
    expect(lastData(histograms[1])).toEqual(expect.arrayContaining([expect.objectContaining({ color: "rgba(225,29,72,0.7)" })]));
    expect(lastData(histograms[2]).every(value => ["rgba(225,29,72,0.85)", "rgba(4,120,87,0.85)"].includes(value.color as string))).toBe(true);
    expect(candle(chart).setMarkers).toHaveBeenCalledExactlyOnceWith(expect.arrayContaining([
      expect.objectContaining({ text: "B 100股", color: "#e11d48" }),
      expect.objectContaining({ shape: "circle", text: "!", color: "#0369a1" }),
      expect.objectContaining({ shape: "circle", text: "", color: "#b45309" }),
    ]));
    expect(candle(chart).removePriceLine).toHaveBeenCalledOnce();
    expect(candle(chart).createPriceLine).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ price: 18, color: "#b45309" }));
    expect(chart.timeScale().setVisibleLogicalRange).not.toHaveBeenCalled();
  });

  it("combines new bars and a theme change without losing either update or filling the same basis twice", async () => {
    document.documentElement.classList.add("dark");
    const view = render(<KlineChartPro bars={bars()} tradeMarks={marks} costPrice={18} />);
    const chart = charts[0];
    for (const series of chart.series) series.setData.mockClear();
    candle(chart).setMarkers.mockClear(); candle(chart).createPriceLine.mockClear();
    const next = bars(120, 5);
    await act(async () => {
      document.documentElement.classList.remove("dark");
      view.rerender(<KlineChartPro bars={next} tradeMarks={marks} costPrice={18} />);
    });
    expect(charts).toHaveLength(1);
    for (const series of chart.series) expect(series.setData).toHaveBeenCalledOnce();
    expect(lastData(candle(chart)).at(-1)).toMatchObject({ close: next.at(-1)!.close });
    expect(chart.applyOptions).toHaveBeenLastCalledWith(expect.objectContaining({ layout: { textColor: "#52525b" } }));
    expect(candle(chart).setMarkers).toHaveBeenCalledOnce();
    expect(candle(chart).createPriceLine).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ color: "#b45309" }));
  });

  it("updates or clears marks and cost immediately without refilling unchanged prices", () => {
    const input = bars();
    const view = render(<KlineChartPro bars={input} tradeMarks={marks} costPrice={18} />);
    const chart = charts[0];
    for (const series of chart.series) series.setData.mockClear();
    candle(chart).setMarkers.mockClear(); candle(chart).createPriceLine.mockClear(); candle(chart).removePriceLine.mockClear();
    const sells = [{ date: "2026-09-28", side: "sell", price: 22, quantity: 200 }];
    view.rerender(<KlineChartPro bars={input} tradeMarks={sells} costPrice={19} />);
    expect(candle(chart).setMarkers).toHaveBeenCalledExactlyOnceWith([expect.objectContaining({ text: "S 200股", position: "aboveBar" })]);
    expect(candle(chart).createPriceLine).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ price: 19 }));
    view.rerender(<KlineChartPro bars={input} tradeMarks={[]} costPrice={null} />);
    expect(candle(chart).setMarkers).toHaveBeenLastCalledWith([]);
    expect(candle(chart).removePriceLine).toHaveBeenCalledTimes(2);
    expect(candle(chart).createPriceLine).toHaveBeenCalledOnce();
    for (const series of chart.series) expect(series.setData).not.toHaveBeenCalled();
  });

  it("keeps followLatest range updates once per new data input", () => {
    const view = render(<KlineChartPro bars={bars()} followLatest />);
    const chart = charts[0];
    expect(chart.timeScale().setVisibleLogicalRange).toHaveBeenCalledExactlyOnceWith({ from: 100, to: 122 });
    view.rerender(<KlineChartPro bars={bars(121)} followLatest />);
    expect(chart.timeScale().setVisibleLogicalRange).toHaveBeenCalledTimes(2);
    expect(chart.timeScale().setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: 101, to: 123 });
  });

  it("isolates StrictMode generations and empty-to-populated charts", () => {
    const input = bars();
    const view = render(<StrictMode><KlineChartPro bars={input} tradeMarks={marks} costPrice={18} /></StrictMode>);
    expect(charts).toHaveLength(2);
    expect(charts[0].remove).toHaveBeenCalledOnce();
    for (const chart of charts) {
      for (const series of chart.series) expect(series.setData).toHaveBeenCalledOnce();
      expect(candle(chart).setMarkers).toHaveBeenCalledOnce();
    }
    const current = charts.at(-1)!;
    view.rerender(<StrictMode><KlineChartPro bars={[]} /></StrictMode>);
    expect(current.remove).toHaveBeenCalledOnce();
    view.rerender(<StrictMode><KlineChartPro bars={input} tradeMarks={marks} costPrice={18} /></StrictMode>);
    expect(charts.at(-1)).not.toBe(current);
    for (const series of charts.at(-1)!.series) expect(series.setData).toHaveBeenCalledOnce();
  });

  it("hydrates a light DOM with light per-bar and cost colors on the first fill", async () => {
    const input = bars();
    const element = <KlineChartPro bars={input} tradeMarks={marks} costPrice={18} />;
    const container = document.createElement("div");
    container.innerHTML = renderToString(element); document.body.appendChild(container);
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => { root = hydrateRoot(container, element); });
      expect(charts).toHaveLength(1);
      const chart = charts[0];
      for (const series of chart.series) expect(series.setData).toHaveBeenCalledOnce();
      const volume = chart.series.find(series => series.kind === "histogram")!;
      expect(volume.setData.mock.calls[0][0][0]).toMatchObject({ color: "rgba(225,29,72,0.7)" });
      expect(candle(chart).createPriceLine).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ color: "#b45309" }));
    } finally {
      await act(async () => { root?.unmount(); }); container.remove();
    }
  });
});
