import { bjDate, qualityReasonText, timeText } from "@/lib/format";
import { isContinuousSession } from "@/lib/market-hours";
import type { QuoteDisplaySession } from "@/types/market";

export interface QuoteQualityInput {
  quality?: string | null;
  reasons?: readonly string[];
  symbol?: string;
  market?: string | null;
  dataTimestamp?: string | null;
  session?: QuoteDisplaySession | null;
}


function beijingMinute(timestamp: string): number | null {
  const date = new Date(timestamp);
  if (!Number.isFinite(date.getTime())) return null;
  const shifted = new Date(date.getTime() + 8 * 60 * 60 * 1000);
  return shifted.getUTCHours() * 60 + shifted.getUTCMinutes();
}

function validSessionClock(session: QuoteDisplaySession | null | undefined): boolean {
  if (!session || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(session.as_of)
    || !Number.isFinite(Date.parse(session.as_of)) || bjDate(session.as_of) !== session.trade_date) return false;
  // Date.parse repairs February 30 and 24:00. Do not treat that repair as calendar evidence.
  const local = new Date(`${session.as_of.slice(0, 19)}Z`);
  return Number.isFinite(local.getTime()) && local.toISOString().slice(0, 19) === session.as_of.slice(0, 19);
}

export function calendarPhase(session: QuoteDisplaySession | null | undefined): "auction" | "pre_open_gap" | "closing_auction" | null {
  if (session?.trade_day_result !== true || !validSessionClock(session)) return null;
  const minute = beijingMinute(session.as_of);
  if (minute === null) return null;
  if (session.phase === "auction" && minute >= 555 && minute < 565) return "auction";
  if (session.phase === "auction" && minute >= 897 && minute < 900) return "closing_auction";
  if (session.phase === "pre_open_gap" && minute >= 565 && minute < 570) return "pre_open_gap";
  return null;
}

export function calendarPause(session: QuoteDisplaySession | null | undefined): "未开盘" | "午间休息" | "已收盘" | "休市" | null {
  if (!session || session.phase !== "closed" || (session.trade_day_result !== true && session.trade_day_result !== false)
    || !validSessionClock(session)) return null;
  const minute = beijingMinute(session.as_of);
  if (minute === null) return null;
  if (session.trade_day_result === false) return "休市";
  // Display closes at 15:00; the inclusive 15:00 chart bucket is a data label.
  if (minute >= 900) return "已收盘";
  // Other calendar pauses must agree with the existing minute-session bounds.
  if (isContinuousSession(minute)) return null;
  if (minute < 555) return "未开盘";
  if (minute > 690 && minute < 780) return "午间休息";
  return null;
}

function isKnownIndex(symbol: string | undefined, market: string | null | undefined) {
  const identity = /^(sh|sz)\d{6}$/i.test(symbol ?? "")
    ? symbol!.toLowerCase() : `${market?.toLowerCase() ?? ""}${symbol ?? ""}`;
  return ["sh000001", "sh000688", "sz399006", "sz399001", "sh000300", "sh000852"].includes(identity);
}

export function hasExpectedPausedSource(dataTimestamp: string | null | undefined, session: QuoteDisplaySession | null | undefined): boolean {
  const after = session?.expected_source_after;
  if (!session || !dataTimestamp || !after || !/(?:Z|[+-]\d{2}:\d{2})$/.test(dataTimestamp)
    || !/(?:Z|[+-]\d{2}:\d{2})$/.test(after)) return false;
  const observedAt = Date.parse(dataTimestamp);
  const expectedAfter = Date.parse(after);
  const asOf = Date.parse(session.as_of);
  return bjDate(dataTimestamp) === session.expected_source_date && bjDate(after) === session.expected_source_date
    && Number.isFinite(observedAt) && Number.isFinite(expectedAfter)
    && expectedAfter <= asOf && observedAt >= expectedAfter && observedAt <= asOf;
}

/** Presentation only. Neither source time nor business quality is changed. */
export function quoteQualityView(input: QuoteQualityInput) {
  const {quality, reasons = [], dataTimestamp, session} = input;
  const phase = calendarPhase(session);
  // Exchange phase and source quality are independent facts. A rejected quote
  // cannot end lunch, and a lunch label cannot validate a rejected quote.
  const sessionLabel = phase === "auction" ? "竞价等待" : phase === "closing_auction" ? "收盘竞价" : phase === "pre_open_gap" ? "开盘等待" : calendarPause(session);
  const has = (reason: string) => reasons.includes(reason);
  let label = quality === "high" ? "正常" : quality === "invalid" ? "非法" : "时点待核";
  let tone: "quiet" | "warning" | "danger" = quality === "invalid" ? "danger" : quality === "high" ? "quiet" : "warning";
  let context = "";
  let expectedSession = false;
  if (quality === "stale") {
    // Actual failed or rejected reads outrank an age or pause explanation.
    if (has("refresh_failed")) label = "刷新异常";
    else if (has("source_time_regress_ignored")) label = "保留可信值";
    else if (has("batch_missing") || has("index_batch_missing")) label = "本轮缺项";
    else if (has("source_missing_price_ignored")) label = "缺最新价";
    else if (has("source_invalid_ignored")) label = "源值异常";
    else if (reasons.some(reason => reason.startsWith("source_identity_"))) label = "身份待核";
    else if (has("source_time_unknown") || has("source_time_unknown_ignored")) label = "时间待核";
    else if (has("quote_age_exceeded")) {
      label = "报价未更新";
      context = "源报价未更新，此提示不表示请求超时";
      // Calendar context explains pauses. It never upgrades a quote or overrides
      // a rejected/failed reply. Missing or contradictory context stays unknown.
      const phase = calendarPhase(session);
      const onlyAge = reasons.every(reason => reason === "quote_age_exceeded" || reason === "off_session" || reason === "market_closed");
      const sourceDate = bjDate(dataTimestamp);
      const observedAt = dataTimestamp ? Date.parse(dataTimestamp) : NaN;
      const phaseAt = session ? Date.parse(session.as_of) : NaN;
      const sourceMinute = dataTimestamp ? beijingMinute(dataTimestamp) : null;
      const validTime = dataTimestamp && /(?:Z|[+-]\d{2}:\d{2})$/.test(dataTimestamp)
        && sourceDate && sourceDate <= (session?.trade_date ?? "") && Number.isFinite(observedAt) && observedAt <= phaseAt;
      const pause = calendarPause(session);
      if (pause && onlyAge && hasExpectedPausedSource(dataTimestamp, session)) {
        label = pause;
        context += `；日历确认${pause}，仍展示最近源观测，不声明源报价已更新`;
      } else if (phase && onlyAge && validTime && (phase === "closing_auction"
        ? sourceDate === session?.trade_date && sourceMinute !== null && sourceMinute >= 897
        : isKnownIndex(input.symbol, input.market))) {
        expectedSession = true;
        label = phase === "auction" ? "竞价等待" : phase === "closing_auction" ? "收盘竞价" : "开盘等待";
        context += phase === "closing_auction" ? "；当前处于收盘集合竞价，保留本日14:57之后的源观测，不据此认定最终收盘价"
          : "；交易日阶段：" + (phase === "auction" ? "集合竞价" : "开盘等待") + "；指数尚无较新的可信观测，不声明数据源必定更新";
      } else if (phase === "pre_open_gap" && onlyAge && validTime
        && sourceDate === session?.trade_date && sourceMinute !== null && sourceMinute >= 565) {
        expectedSession = true;
        label = "开盘等待";
        context += "；当前处于09:25–09:30开盘等待；保留本日09:25之后的源观测，不据此认定最终竞价结果";
      }
    } else if (has("market_closed") && reasons.every(reason => reason === "market_closed" || reason === "off_session")) {
      const pause = calendarPause(session);
      if (pause && hasExpectedPausedSource(dataTimestamp, session)) {
        label = pause;
        context = `日历确认${pause}，仍展示最近源观测，不声明源报价已更新`;
        tone = "quiet";
      } else {
        context = "已有休市标记，当前日历或源时点未确认";
      }
    }
  }
  const explanation = reasons.map(qualityReasonText).join("；");
  const sourceTime = dataTimestamp ? `${bjDate(dataTimestamp) || "未知日期"} ${timeText(dataTimestamp)}` : "未提供";
  const age = dataTimestamp && session ? (Date.parse(session.as_of) - Date.parse(dataTimestamp)) / 1000 : NaN;
  const detail = dataTimestamp || session ? `源数据时间：${sourceTime}${Number.isFinite(age) && age >= 0 ? `；源时间距展示时刻 ${Math.floor(age)} 秒` : ""}` : "";
  const title = [context, explanation, detail].filter(Boolean).join("；") || undefined;
  const hasSessionIssue = sessionLabel !== null && (!!quality && quality !== "high" && label !== sessionLabel
    || reasons.some(reason => !["quote_age_exceeded", "market_closed", "off_session"].includes(reason)));
  const sessionIssue = hasSessionIssue ? {label: label === "正常" ? "源数据待核" : label, title} : null;
  return {label, tone, expectedSession, sessionLabel, sessionIssue, title};
}
