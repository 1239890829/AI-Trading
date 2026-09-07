/**
 * A 股交易时段粗判（北京时间）：仅供前端「盘外降频/跳过轮询」决策，非权威日历。
 *
 * - 只排除周末，不识别节假日：节假日被误判为盘中只是多打一次后端缓存
 *   （盘外分时数据不变），无害；反之交易日被误判为盘外会漏刷新，故时段
 *   边界取宽松区间（09:15 集合竞价起，15:15 收盘缓冲止）。
 * - 北京时间无夏令时，用固定 UTC+8 偏移换算，不依赖本机时区。
 */
export function isTradingSession(now: Date = new Date()): boolean {
  const bj = new Date(now.getTime() + (now.getTimezoneOffset() + 480) * 60_000);
  const day = bj.getDay();
  if (day === 0 || day === 6) return false;
  const m = bj.getHours() * 60 + bj.getMinutes();
  return (m >= 9 * 60 + 15 && m <= 11 * 60 + 35) || (m >= 12 * 60 + 55 && m <= 15 * 60 + 15);
}
