import { bjDate, qualityReasonText, timeText } from "@/lib/format";
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

function calendarPhase(session: QuoteDisplaySession | null | undefined): "auction" | "pre_open_gap" | null {
  if (session?.trade_day_result !== true || bjDate(session.as_of) !== session.trade_date) return null;
  const minute = beijingMinute(session.as_of);
  if (minute === null) return null;
  if (session.phase === "auction" && minute >= 555 && minute < 565) return "auction";
  if (session.phase === "pre_open_gap" && minute >= 565 && minute < 570) return "pre_open_gap";
  return null;
}

function isKnownIndex(symbol: string | undefined, market: string | null | undefined) {
  const identity = /^(sh|sz)\d{6}$/i.test(symbol ?? "")
    ? symbol!.toLowerCase() : `${market?.toLowerCase() ?? ""}${symbol ?? ""}`;
  return ["sh000001", "sh000688", "sz399006", "sz399001", "sh000300", "sh000852"].includes(identity);
}

/** Presentation only. Neither source time nor business quality is changed. */
export function quoteQualityView(input: QuoteQualityInput) {
  const {quality, reasons = [], dataTimestamp, session} = input;
  const has = (reason: string) => reasons.includes(reason);
  let label = quality === "high" ? "正常" : quality === "invalid" ? "非法" : "时点待核";
  let tone: "quiet" | "warning" | "danger" = quality === "invalid" ? "danger" : quality === "high" ? "quiet" : "warning";
  let context = "";
  if (quality === "stale") {
    if (has("market_closed")) {label = "休市"; tone = "quiet";}
    else if (has("refresh_failed")) label = "刷新异常";
    else if (has("quote_age_exceeded")) {
      label = "报价超时";
      // Calendar context explains pauses. It never upgrades a quote or overrides
      // a rejected/failed reply. Missing or contradictory context stays unknown.
      const phase = calendarPhase(session);
      const onlyAge = reasons.every(reason => reason === "quote_age_exceeded" || reason === "off_session");
      const sourceDate = bjDate(dataTimestamp);
      const observedAt = dataTimestamp ? Date.parse(dataTimestamp) : NaN;
      const phaseAt = session ? Date.parse(session.as_of) : NaN;
      const sourceMinute = dataTimestamp ? beijingMinute(dataTimestamp) : null;
      const validTime = sourceDate && sourceDate <= (session?.trade_date ?? "") && observedAt <= phaseAt;
      if (phase && onlyAge && validTime && isKnownIndex(input.symbol, input.market)) {
        label = phase === "auction" ? "竞价等待" : "开盘等待";
        context = "交易日阶段：" + (phase === "auction" ? "集合竞价" : "开盘等待") + "；指数尚无较新的可信观测，不声明数据源必定更新";
      } else if (phase === "pre_open_gap" && onlyAge && validTime
        && sourceDate === session?.trade_date && sourceMinute !== null && sourceMinute >= 565) {
        label = "开盘等待";
        context = "当前处于09:25–09:30开盘等待；保留本日09:25之后的源观测，不据此认定最终竞价结果";
      }
    } else if (has("source_time_regress_ignored")) label = "保留可信值";
    else if (has("batch_missing") || has("index_batch_missing")) label = "本轮缺项";
    else if (has("source_missing_price_ignored")) label = "缺最新价";
    else if (has("source_time_unknown_ignored")) label = "时间待核";
    else if (has("source_invalid_ignored")) label = "源值异常";
    else if (reasons.some(reason => reason.startsWith("source_identity_"))) label = "身份待核";
  }
  const explanation = reasons.map(qualityReasonText).join("；");
  const sourceTime = dataTimestamp ? `${bjDate(dataTimestamp) || "未知日期"} ${timeText(dataTimestamp)}` : "未提供";
  const age = dataTimestamp && session ? (Date.parse(session.as_of) - Date.parse(dataTimestamp)) / 1000 : NaN;
  const detail = dataTimestamp || session ? `源数据时间：${sourceTime}${Number.isFinite(age) && age >= 0 ? `；源时间距展示时刻 ${Math.floor(age)} 秒` : ""}` : "";
  return {label, tone, title: [context, explanation, detail].filter(Boolean).join("；") || undefined};
}
