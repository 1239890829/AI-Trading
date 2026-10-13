import { describe, expect, it } from "vitest";
import { quoteQualityView } from "./quote-quality-view";
import type { QuoteDisplaySession } from "@/types/market";

function session(clock: string, phase: QuoteDisplaySession["phase"], day: boolean | null = true): QuoteDisplaySession {
  return {phase, trade_date: "2026-10-13", as_of: `2026-10-13T${clock}+08:00`, trade_day_result: day};
}
const aged = {quality: "stale", reasons: ["quote_age_exceeded"], symbol: "sh000001", dataTimestamp: "2026-10-12T15:00:00+08:00"};

describe("Quote quality display is explanatory, not trading eligibility", () => {
  it.each([
    ["09:15:00", "auction", "竞价等待"],
    ["09:24:59", "auction", "竞价等待"],
    ["09:25:00", "pre_open_gap", "开盘等待"],
    ["09:29:59", "pre_open_gap", "开盘等待"],
    ["09:30:00", "continuous", "报价超时"],
  ] as const)("at %s a known calendar phase gives %s context without upgrading yesterday's index", (clock, phase, label) => {
    const input = {...aged, session: session(clock, phase)};
    const before = JSON.stringify(input);
    const view = quoteQualityView(input);
    expect(view.label).toBe(label);
    expect(view.tone).toBe("warning");
    expect(view.title).toContain("2026-10-12");
    expect(view.title).toContain("不属于实时数据");
    expect(view.title).toContain("源时间距展示时刻");
    expect(JSON.stringify(input)).toBe(before);
  });

  it.each([false, null])("closed/unknown calendar (%s) never guesses auction from wall clock", day => {
    expect(quoteQualityView({...aged, session: session("09:20:00", "auction", day)}).label).toBe("报价超时");
  });
  it("missing, contradictory or expired phase context cannot relabel true timeout", () => {
    expect(quoteQualityView(aged).label).toBe("报价超时");
    expect(quoteQualityView({...aged, session: session("09:30:00", "auction")}).label).toBe("报价超时");
    expect(quoteQualityView({...aged, session: {...session("09:20:00", "auction"), trade_date: "2026-10-12"}}).label).toBe("报价超时");
    expect(quoteQualityView({...aged, dataTimestamp: "bad-time", session: session("09:20:00", "auction")}).label).toBe("报价超时");
  });
  it("a bare stock code that collides with an index stays a stock", () => {
    expect(quoteQualityView({...aged, symbol: "000001", market: "SZ", session: session("09:20:00", "auction")}).label).toBe("报价超时");
    expect(quoteQualityView({...aged, symbol: "000001", market: "SH", session: session("09:20:00", "auction")}).label).toBe("竞价等待");
  });
  it("a stock's opening pause requires a same-day source observation at or after 09:25", () => {
    const input = {...aged, symbol: "600127", session: session("09:27:00", "pre_open_gap")};
    expect(quoteQualityView(input).label).toBe("报价超时");
    expect(quoteQualityView({...input, dataTimestamp: "2026-10-13T09:24:59+08:00"}).label).toBe("报价超时");
    const view = quoteQualityView({...input, dataTimestamp: "2026-10-13T09:25:00+08:00"});
    expect(view.label).toBe("开盘等待");
    expect(view.title).toContain("不据此认定最终竞价结果");
    expect(quoteQualityView({...input, dataTimestamp: "2026-10-13T09:28:00+08:00"}).label).toBe("报价超时");
  });
  it.each([
    ["source_time_regress_ignored", "保留可信值"],
    ["index_batch_missing", "本轮缺项"],
    ["batch_missing", "本轮缺项"],
    ["refresh_failed", "刷新异常"],
  ])("%s during auction remains a real warning", (reason, label) => {
    const view = quoteQualityView({...aged, reasons: [reason], session: session("09:20:00", "auction")});
    expect(view.label).toBe(label);
    expect(view.tone).toBe("warning");
  });
  it("source rejection plus age does not receive an expected-pause label", () => {
    expect(quoteQualityView({...aged, reasons: ["quote_age_exceeded", "source_time_regress_ignored"], session: session("09:20:00", "auction")}).label).toBe("报价超时");
  });
  it("unknown reason strings stay verbatim, visible as a warning, and block expected-pause relabelling", () => {
    const reason = "future_reason_v99:尚未核验";
    const input = {...aged, reasons:["quote_age_exceeded", reason], session:session("09:20:00", "auction")};
    const before = JSON.stringify(input);
    const view = quoteQualityView(input);
    expect(view.label).toBe("报价超时");
    expect(view.tone).toBe("warning");
    expect(view.title).toContain(reason);
    expect(JSON.stringify(input)).toBe(before);
  });
});
