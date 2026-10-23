import { describe, expect, it } from "vitest";
import { quoteQualityView } from "./quote-quality-view";
import type { QuoteDisplaySession } from "@/types/market";

function session(clock: string, phase: QuoteDisplaySession["phase"], day: boolean | null = true): QuoteDisplaySession {
  return {phase, trade_date: "2026-10-13", as_of: `2026-10-13T${clock}+08:00`, trade_day_result: day};
}
function closedSession(clock: string, expectedSourceDate: string | null, day: boolean | null = true,
  expectedSourceAfter = expectedSourceDate ? `${expectedSourceDate}T14:59:50+08:00` : null): QuoteDisplaySession {
  return {...session(clock, "closed", day), expected_source_date: expectedSourceDate, expected_source_after: expectedSourceAfter};
}
const aged = {quality: "stale", reasons: ["quote_age_exceeded"], symbol: "sh000001", dataTimestamp: "2026-10-12T15:00:00+08:00"};

describe("calendar session and source quality are independent", () => {
  it.each(["high", "medium", "low", "stale", "invalid"])("%s cannot replace a confirmed lunch session or upgrade source quality", quality => {
    const input = {...aged, quality, reasons: ["source_time_regress_ignored"], dataTimestamp: null,
      session: closedSession("12:20:00", "2026-10-13", true, "2026-10-13T11:29:50+08:00")};
    const before = JSON.stringify(input);
    expect(quoteQualityView(input).sessionLabel).toBe("午间休息");
    expect(JSON.stringify(input)).toBe(before);
  });
  it.each([
    [session("09:20:00", "auction"), "竞价等待"],
    [session("09:27:00", "pre_open_gap"), "开盘等待"],
    [session("14:57:00", "auction"), "收盘竞价"],
    [session("14:59:59", "auction"), "收盘竞价"],
    [session("08:00:00", "closed"), "未开盘"],
    [session("19:00:00", "closed"), "已收盘"],
    [session("10:00:00", "closed", false), "休市"],
  ] as const)("session %j remains visible even with no source observation", (context, label) => {
    expect(quoteQualityView({quality: "high", session: context}).sessionLabel).toBe(label);
  });
  it.each([
    {...session("09:20:00", "auction"), as_of: "2026-10-13T09:20:00"},
    {...session("09:20:00", "auction"), as_of: "2026-02-30T09:20:00+08:00", trade_date: "2026-03-02"},
    {...session("09:20:00", "auction"), as_of: "2026-10-13T24:00:00+08:00", trade_date: "2026-10-14"},
    session("10:00:00", "closed"), session("09:30:00", "auction"), session("09:27:00", "auction"),
    session("09:20:00", "auction", false), session("09:20:00", "auction", null),
    {...session("09:20:00", "auction"), trade_date: "2026-10-12"},
    session("10:00:00", "continuous"), session("12:00:00", "unknown"),
    session("14:56:59", "auction"), session("15:00:00", "auction"),
  ])("invalid, unknown or continuous phase does not invent a pause %j", context => {
    expect(quoteQualityView({quality: "high", session: context}).sessionLabel).toBeNull();
  });
});

describe("Quote quality display is explanatory, not trading eligibility", () => {
  it("收盘竞价只解释本日到达14:57的源观察，拒绝原因和旧尾仍显式待核", () => {
    const input = {...aged, dataTimestamp: "2026-10-13T14:57:00+08:00", session: session("14:58:00", "auction")};
    const before = JSON.stringify(input);
    expect(quoteQualityView(input)).toMatchObject({sessionLabel: "收盘竞价", label: "收盘竞价", tone: "warning"});
    expect(quoteQualityView({...input, dataTimestamp: "2026-10-13T10:00:00+08:00"})).toMatchObject({sessionLabel: "收盘竞价", label: "报价未更新"});
    expect(quoteQualityView({...input, reasons: ["refresh_failed"]})).toMatchObject({sessionLabel: "收盘竞价", label: "刷新异常"});
    expect(JSON.stringify(input)).toBe(before);
  });
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
    expect(quoteQualityView({...base, dataTimestamp: "2026-10-13T15:00:00+08:00", session: closedSession("19:34:00", "2026-10-13")}).label).toBe("已收盘");
    expect(quoteQualityView({...base, session: closedSession("10:00:00", "2026-10-12", false)}).label).toBe("休市");
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
      const sourceDate = clock === "08:00:00" ? "2026-10-12" : "2026-10-13";
      const sourceTime = clock === "11:31:00" || clock === "12:59:59" ? "11:30:00" : "15:00:00";
      const expectedAfter = `${sourceDate}T${sourceTime === "11:30:00" ? "11:29:50" : "14:59:50"}+08:00`;
      const input = {...aged, symbol, dataTimestamp: `${sourceDate}T${sourceTime}+08:00`, reasons: ["quote_age_exceeded", "off_session"], session: closedSession(clock, sourceDate, true, expectedAfter)};
      const before = JSON.stringify(input);
      const view = quoteQualityView(input);
      expect(view.label).toBe(label);
      expect(view.tone).toBe("warning");
      expect(view.title).toContain("报价未更新");
      expect(view.title).toContain("不属于实时数据");
      expect(JSON.stringify(input)).toBe(before);
    }
  });

  it.each([
    [["market_closed"], "时点待核"],
    [["quote_age_exceeded", "off_session"], "报价未更新"],
  ])("a closed calendar does not hide the wrong source date for %s", (reasons, label) => {
    for (const sourceDate of ["2026-10-12", "2026-10-09"]) {
      const input = {...aged, reasons, dataTimestamp: `${sourceDate}T15:00:00+08:00`, session: closedSession("19:34:00", "2026-10-13")};
      const before = JSON.stringify(input);
      const view = quoteQualityView(input);
      expect(view.label).toBe(label);
      expect(view.tone).toBe("warning");
      expect(view.title).toContain(sourceDate);
      expect(JSON.stringify(input)).toBe(before);
    }
  });

  it.each([
    [["market_closed"], "时点待核"],
    [["quote_age_exceeded", "off_session"], "报价未更新"],
  ])("missing expected source date remains unconfirmed for %s", (reasons, label) => {
    for (const expectedSourceDate of [undefined, null, "not-a-date"]) {
      const input = {...aged, reasons, session: {...session("19:34:00", "closed"), expected_source_date: expectedSourceDate}};
      expect(quoteQualityView(input).label).toBe(label);
      expect(quoteQualityView(input).tone).toBe("warning");
    }
  });

  it("closed source-date context cannot conceal a future source clock or a naive timestamp", () => {
    const context = closedSession("19:34:00", "2026-10-13");
    for (const reasons of [["market_closed"], ["quote_age_exceeded", "off_session"]]) {
      const expectedLabel = reasons.includes("market_closed") ? "时点待核" : "报价未更新";
      for (const dataTimestamp of ["2026-10-13T20:00:00+08:00", "2026-10-13T15:00:00"]) {
        expect(quoteQualityView({...aged, reasons, dataTimestamp, session: context}).label).toBe(expectedLabel);
      }
    }
  });

  it.each([
    ["19:34:00", "2026-10-13T14:59:50+08:00"],
    ["11:31:00", "2026-10-13T11:29:50+08:00"],
  ])("same-day morning data is still an outage at %s", (clock, expectedAfter) => {
    for (const reasons of [["market_closed"], ["quote_age_exceeded", "off_session"]]) {
      const view = quoteQualityView({...aged, reasons, dataTimestamp: "2026-10-13T10:00:00+08:00", session: closedSession(clock, "2026-10-13", true, expectedAfter)});
      expect(view.label).toBe(reasons.includes("market_closed") ? "时点待核" : "报价未更新");
      expect(view.tone).toBe("warning");
      expect(view.title).toContain("10:00:00");
    }
  });

  it.each([
    ["08:00:00", "2026-10-12", "14:59:50", "14:59:49", "未开盘"],
    ["11:31:00", "2026-10-13", "11:29:50", "11:29:49", "午间休息"],
    ["19:34:00", "2026-10-13", "14:59:50", "14:59:49", "已收盘"],
  ])("%s uses the server source-clock boundary without a frontend allowance", (clock, sourceDate, boundary, before, label) => {
    const context = closedSession(clock, sourceDate, true, `${sourceDate}T${boundary}+08:00`);
    for (const reasons of [["market_closed"], ["quote_age_exceeded", "off_session"]]) {
      expect(quoteQualityView({...aged, reasons, dataTimestamp: `${sourceDate}T${before}+08:00`, session: context}).label).toBe(reasons.includes("market_closed") ? "时点待核" : "报价未更新");
      expect(quoteQualityView({...aged, reasons, dataTimestamp: `${sourceDate}T${boundary}+08:00`, session: context}).label).toBe(label);
    }
  });

  it("missing, malformed, naive or future expected source clocks cannot quiet a warning", () => {
    for (const expectedSourceAfter of [undefined, null, "bad-time", "2026-10-13T14:59:50", "2026-10-13T20:00:00+08:00"]) {
      const context = {...closedSession("19:34:00", "2026-10-13"), expected_source_after: expectedSourceAfter};
      for (const reasons of [["market_closed"], ["quote_age_exceeded", "off_session"]]) {
        const view = quoteQualityView({...aged, reasons, dataTimestamp: "2026-10-13T15:00:00+08:00", session: context});
        expect(view.label).toBe(reasons.includes("market_closed") ? "时点待核" : "报价未更新");
        expect(view.tone).toBe("warning");
      }
    }
  });

  it.each(["09:15:00", "09:29:59", "09:30:00", "11:30:59", "13:00:00", "15:00:59"])("closed conflicts with trade-day %s and remains an age warning", clock => {
    expect(quoteQualityView({...aged, dataTimestamp: "2026-10-13T09:00:00+08:00", session: closedSession(clock, "2026-10-13", true, "2026-10-13T08:59:50+08:00")}).label).toBe("报价未更新");
  });

  it("unknown, wrong-date, future-source or naive clocks cannot establish a closed pause", () => {
    const current = {...aged, dataTimestamp: "2026-10-13T15:00:00+08:00", session: closedSession("19:34:00", "2026-10-13")};
    const examples = [
      {...current, session: {...current.session, trade_day_result: null}},
      {...current, session: {...current.session, trade_day_result: undefined} as unknown as QuoteDisplaySession},
      {...current, session: {...current.session, trade_date: "2026-10-12"}},
      {...current, session: {...current.session, as_of: "2026-10-13T19:34:00"}},
      {...current, dataTimestamp: "2026-10-13T20:00:00+08:00"},
      {...current, dataTimestamp: "2026-10-13T15:00:00"},
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
    const view = quoteQualityView({...aged, dataTimestamp: "2026-10-13T15:00:00+08:00", reasons: ["quote_age_exceeded", "off_session", "market_closed", reason], session: closedSession("19:34:00", "2026-10-13")});
    expect(view.label).toBe(label);
    expect(view.tone).toBe("warning");
  });

  it("an unknown reason blocks closed-pause relabelling and remains visible", () => {
    const view = quoteQualityView({...aged, dataTimestamp: "2026-10-13T15:00:00+08:00", reasons: ["quote_age_exceeded", "off_session", "future_unknown_failure"], session: closedSession("19:34:00", "2026-10-13")});
    expect(view.label).toBe("报价未更新");
    expect(view.tone).toBe("warning");
    expect(view.title).toContain("future_unknown_failure");
  });
});
