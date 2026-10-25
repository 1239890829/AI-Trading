import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReviewMetricHistoryPoint, ReviewMetricHistoryReference } from "@/lib/api";
import { ReviewMetricTrends } from "./review-metric-trends";

afterEach(cleanup);
const point = (date: string, change: Partial<ReviewMetricHistoryPoint> = {}): ReviewMetricHistoryPoint => ({
  date, limit_up: 3, n_break: 1, break_rate: 0.25, seal_rate: 0.75, seal_base: 4, max_board: 2,
  promo_1to2: 0.5, promo_2to3: null, promo_1to2_base: null, promo_2to3_base: null, missing: [], ...change,
});
function reference(): ReviewMetricHistoryReference {
  return {contract_version: "metric_history_reference_v1", role: "read_time_aggregate_reference", source: "sentiment.metric_history",
    requested_trade_date: "2026-10-23", window_start: "2026-09-24", window_end: "2026-10-23",
    read_at: "2026-10-25T16:00:00+08:00", dataset_updated_at: "2026-10-25T15:00:00+08:00",
    source_as_of: null, raw_source_sha256: null, available_at_original_report: "unknown", state: "partial",
    points: [point("2026-10-21"), point("2026-10-23", {limit_up: 0, n_break: 3, seal_base: 3, break_rate: 1, seal_rate: 0, max_board: 0, promo_1to2: 0})],
    unrecorded_dates: [], invalid_rows: 0, coverage: {calendar_days: 30, recorded_days: 2, effective_days: {seal_rate: 2, promo_1to2: 2, promo_2to3: 0, max_board: 2}},
    reason: "读取时参考", limitations: [],
  };
}

describe("review historical metric reference", () => {
  it("shows real zero, retained denominators and source limits without calling any new source", () => {
    render(<ReviewMetricTrends reference={reference()} tradeDate="20261023"/>);
    expect(screen.getByRole("img", {name: "封板率历史采集趋势，有效2日，空缺不连线"})).toBeTruthy();
    expect(screen.getAllByText("0.0%").length).toBe(2);
    expect(screen.getByText("0板")).toBeTruthy();
    expect(screen.getByText(/封板分母 3家。晋级基数未保存/)).toBeTruthy();
    expect(screen.getByText(/未记录28个自然日（含休市与缺档）/)).toBeTruthy();
    expect(screen.getByText(/不代表原报告当时已见/)).toBeTruthy();
    expect(screen.getByText(/逐日源钟与原始池哈希未保存/)).toBeTruthy();
    const slider = screen.getByRole("slider", {name: "查看历史日期"});
    expect(slider.getAttribute("type")).toBe("range");
    expect(slider.getAttribute("aria-valuetext")).toBe("2026-10-23");
  });

  it("selects metrics and dates with a native keyboard-operable range, preserving a missing day", () => {
    render(<ReviewMetricTrends reference={reference()} tradeDate="20261023"/>);
    fireEvent.click(screen.getByRole("button", {name: "连板高度"}));
    expect(screen.getByRole("button", {name: "连板高度"}).getAttribute("aria-pressed")).toBe("true");
    const slider = screen.getByRole("slider", {name: "查看历史日期"});
    fireEvent.change(slider, {target: {value: "27"}});
    expect(slider.getAttribute("aria-valuetext")).toBe("2026-10-21");
    expect(screen.getByText(/查看日期 · 2026-10-21 · 连板高度 2板/)).toBeTruthy();
    fireEvent.change(slider, {target: {value: "28"}});
    expect(screen.getByText(/该自然日未记录，不补成0/)).toBeTruthy();
    const facts = screen.getByRole("region", {name: "历史情绪汇总参考"}).querySelector("dl")!;
    expect(within(facts).getAllByText("未记录")).toHaveLength(4);
  });

  it.each([
    {requested_trade_date: "2026-10-22"}, {window_end: "2026-10-24"},
    {source: "other"}, {points: [point("2026-10-24")]},
    {points: [point("2026-10-23"), point("2026-10-23")]}, {points: [null]},
  ])("rejects a scope or point identity mismatch rather than relabeling it", change => {
    const value = {...reference(), ...change} as ReviewMetricHistoryReference;
    render(<ReviewMetricTrends reference={value} tradeDate="20261023"/>);
    expect(screen.getByRole("alert").textContent).toContain("历史参考身份或结构不符");
    expect(screen.queryByRole("slider")).toBeNull();
  });

  it("does not turn malformed numbers, a zero denominator or absent rows into percentages", () => {
    const value = reference();
    value.points = [point("2026-10-23", {seal_rate: null, seal_base: 0, max_board: NaN, promo_1to2: -0.1, promo_2to3: 2})];
    render(<ReviewMetricTrends reference={value} tradeDate="20261023"/>);
    expect(screen.getByText(/封板分母 未记录或为零家/)).toBeTruthy();
    expect(screen.queryByText("0.0%")).toBeNull();
    expect(screen.getByRole("img", {name: "封板率历史采集趋势，有效0日，空缺不连线"})).toBeTruthy();
  });

  it("keeps a missing reference optional and a source failure visible", () => {
    const first = render(<ReviewMetricTrends tradeDate="20261023"/>);
    expect(first.container.textContent).toBe(""); first.unmount();
    const value = reference(); value.state = "error"; value.points = []; value.reason = "本地历史汇总读取失败";
    render(<ReviewMetricTrends reference={value} tradeDate="20261023"/>);
    expect(screen.getByText("本地历史汇总读取失败")).toBeTruthy();
    expect(screen.queryByRole("slider")).toBeNull();
  });
});
