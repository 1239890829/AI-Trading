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
    ["09:30:00", "continuous", "报价未更新"],
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
    expect(quoteQualityView({...aged, session: session("09:20:00", "auction", day)}).label).toBe("报价未更新");
  });
  it("missing, contradictory or expired phase context cannot infer an expected pause", () => {
    expect(quoteQualityView(aged).label).toBe("报价未更新");
    expect(quoteQualityView({...aged, session: session("09:30:00", "auction")}).label).toBe("报价未更新");
    expect(quoteQualityView({...aged, session: {...session("09:20:00", "auction"), trade_date: "2026-10-12"}}).label).toBe("报价未更新");
    expect(quoteQualityView({...aged, dataTimestamp: "bad-time", session: session("09:20:00", "auction")}).label).toBe("报价未更新");
  });
  it("a bare stock code that collides with an index stays a stock", () => {
    expect(quoteQualityView({...aged, symbol: "000001", market: "SZ", session: session("09:20:00", "auction")}).label).toBe("报价未更新");
    expect(quoteQualityView({...aged, symbol: "000001", market: "SH", session: session("09:20:00", "auction")}).label).toBe("竞价等待");
  });
  it("a stock's opening pause requires a same-day source observation at or after 09:25", () => {
    const input = {...aged, symbol: "600127", session: session("09:27:00", "pre_open_gap")};
    expect(quoteQualityView(input).label).toBe("报价未更新");
    expect(quoteQualityView({...input, dataTimestamp: "2026-10-13T09:24:59+08:00"}).label).toBe("报价未更新");
    const view = quoteQualityView({...input, dataTimestamp: "2026-10-13T09:25:00+08:00"});
    expect(view.label).toBe("开盘等待");
    expect(view.title).toContain("不据此认定最终竞价结果");
    expect(quoteQualityView({...input, dataTimestamp: "2026-10-13T09:28:00+08:00"}).label).toBe("报价未更新");
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
  it("source rejection plus age remains an explicit rejection warning", () => {
    expect(quoteQualityView({...aged, reasons: ["quote_age_exceeded", "source_time_regress_ignored"], session: session("09:20:00", "auction")}).label).toBe("保留可信值");
  });
  it("unknown reason strings stay verbatim, visible as a warning, and block expected-pause relabelling", () => {
    const reason = "future_reason_v99:尚未核验";
    const input = {...aged, reasons:["quote_age_exceeded", reason], session:session("09:20:00", "auction")};
    const before = JSON.stringify(input);
    const view = quoteQualityView(input);
    expect(view.label).toBe("报价未更新");
    expect(view.tone).toBe("warning");
    expect(view.title).toContain(reason);
    expect(JSON.stringify(input)).toBe(before);
  });
});


describe("calendar-backed closed display preserves stale", () => {
  it("a first observation without source time remains unconfirmed rather than a closed retained value", () => {
    const input = {...aged, dataTimestamp: null, reasons: ["source_time_unknown", "market_closed"], session: session("19:34:00", "closed")};
    const before = JSON.stringify(input);
    const view = quoteQualityView(input);
    expect(view.label).toBe("时间待核");
    expect(view.tone).toBe("warning");
    expect(view.title).toContain("源行情未提供数据时间");
    expect(view.title).toContain("保留本次返回数值");
    expect(view.title).not.toContain("保留最近可信值");
    expect(JSON.stringify(input)).toBe(before);
    expect(quoteQualityView({...input, reasons: ["source_time_unknown", "market_closed", "refresh_failed"]}).label).toBe("刷新异常");
    expect(quoteQualityView({...input, reasons: ["source_time_unknown", "market_closed", "source_invalid_ignored"]}).label).toBe("源值异常");
    expect(quoteQualityView({...input, reasons: ["source_time_unknown", "market_closed", "batch_missing"]}).label).toBe("本轮缺项");
  });
  it("an explicit closed reason still needs trustworthy calendar and source context", () => {
    const base = {...aged, reasons: ["market_closed"]};
    for (const context of [undefined, session("19:34:00", "closed", null), session("10:00:00", "closed")]) {
      const view = quoteQualityView({...base, session: context});
      expect(view.label).toBe("时点待核");
      expect(view.tone).toBe("warning");
    }
    expect(quoteQualityView({...base, session: session("19:34:00", "closed")}).label).toBe("已收盘");
    expect(quoteQualityView({...base, session: session("10:00:00", "closed", false)}).label).toBe("休市");
  });
  it.each(["sh000001", "sz399001", "600519", "000001"])("%s uses source age rather than HTTP timeout even without calendar context", symbol => {
    const input = {...aged, symbol};
    const before = JSON.stringify(input);
    const view = quoteQualityView(input);
    expect(view.label).toBe("报价未更新");
    expect(view.tone).toBe("warning");
    expect(view.title).toContain("不属于实时数据");
    expect(JSON.stringify(input)).toBe(before);
  });

  it.each([
    ["08:00:00", "未开盘"], ["11:31:00", "午间休息"],
    ["12:59:59", "午间休息"], ["15:01:00", "已收盘"], ["19:34:00", "已收盘"],
  ])("closed at %s explains %s for stocks and indices without upgrading source quality", (clock, label) => {
    for (const symbol of ["sh000001", "600519"]) {
      const input = {...aged, symbol, reasons: ["quote_age_exceeded", "off_session"], session: session(clock, "closed")};
      const before = JSON.stringify(input);
      const view = quoteQualityView(input);
      expect(view.label).toBe(label);
      expect(view.tone).toBe("warning");
      expect(view.title).toContain("报价未更新");
      expect(view.title).toContain("不属于实时数据");
      expect(JSON.stringify(input)).toBe(before);
    }
  });

  it.each(["09:15:00", "09:29:59", "09:30:00", "11:30:59", "13:00:00", "15:00:59"])("closed conflicts with trade-day %s and remains an age warning", clock => {
    expect(quoteQualityView({...aged, session: session(clock, "closed")}).label).toBe("报价未更新");
  });

  it("unknown, wrong-date, future-source or naive clocks cannot establish a closed pause", () => {
    const examples = [
      {...aged, session: session("19:34:00", "closed", null)},
      {...aged, session: {...session("19:34:00", "closed"), trade_day_result: undefined} as unknown as QuoteDisplaySession},
      {...aged, session: {...session("19:34:00", "closed"), trade_date: "2026-10-12"}},
      {...aged, session: {...session("19:34:00", "closed"), as_of: "2026-10-13T19:34:00"}},
      {...aged, dataTimestamp: "2026-10-13T20:00:00+08:00", session: session("19:34:00", "closed")},
      {...aged, dataTimestamp: "2026-10-13T15:00:00", session: session("19:34:00", "closed")},
    ];
    for (const input of examples) {
      const view = quoteQualityView(input);
      expect(view.label).toBe("报价未更新");
      expect(view.tone).toBe("warning");
    }
  });

  it.each([
    ["refresh_failed", "刷新异常"], ["index_batch_missing", "本轮缺项"],
    ["batch_missing", "本轮缺项"], ["source_time_regress_ignored", "保留可信值"],
    ["source_missing_price_ignored", "缺最新价"], ["source_time_unknown_ignored", "时间待核"],
    ["source_invalid_ignored", "源值异常"], ["source_identity_market_mismatch_ignored", "身份待核"],
  ])("closed, age and market_closed never obscure %s", (reason, label) => {
    const view = quoteQualityView({...aged, reasons: ["quote_age_exceeded", "off_session", "market_closed", reason], session: session("19:34:00", "closed")});
    expect(view.label).toBe(label);
    expect(view.tone).toBe("warning");
  });

  it("an unknown reason blocks closed-pause relabelling and remains visible", () => {
    const view = quoteQualityView({...aged, reasons: ["quote_age_exceeded", "off_session", "future_unknown_failure"], session: session("19:34:00", "closed")});
    expect(view.label).toBe("报价未更新");
    expect(view.tone).toBe("warning");
    expect(view.title).toContain("future_unknown_failure");
  });
});
