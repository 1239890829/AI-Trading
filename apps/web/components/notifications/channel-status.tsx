import type { DeliveryChannelState } from "@/lib/api";

export function ChannelStatus({ channels }: {channels: DeliveryChannelState[] | null | undefined}) {
  if (channels === undefined || channels === null) return <span>外部渠道状态未知</span>;
  if (channels.length === 0) return <span>无外部渠道意图记录</span>;
  const labels: Record<string, string> = {
    pending: "待处理", leased: "处理中", accepted: "渠道已受理（未确认送达）",
    unknown: "受理结果未知", expired: "已过期", suppressed: "已静默",
    permanent_failed: "发送失败",
  };
  const reasonLabels: Record<string, string> = {
    intent_expired: "等待超时", send_window_closed: "发送时效已过",
    outside_trading_window: "不在交易时段", symbol_no_longer_in_scope: "标的已不在关注范围",
    condition_no_longer_met: "触发条件已变化", quote_not_fresh: "行情已过期",
    quote_time_unknown: "行情时间未知", quote_time_untrusted: "行情时间不可信",
    event_or_rule_removed_or_disabled: "规则已停用或原事件失效",
    rule_or_channels_changed: "规则或渠道设置已变化",
    channel_unconfigured_or_target_changed: "渠道未配置或目标已变化",
    buy_point_decision_superseded: "买点决定已有新版本",
    buy_point_decision_no_longer_eligible: "原买点决定不再满足条件",
    buy_point_execution_not_ready: "执行快照未就绪",
    buy_point_event_execution_not_ready: "原事件执行快照未就绪",
    acceptance_unconfirmed: "平台受理回执未确认",
    lease_lost_after_send_started: "发送已开始但回执丢失",
  };
  const bjTime = (ms: number) => new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(ms));
  return <span>{channels.map((c) => {
    const reason = ["unknown", "expired", "suppressed", "permanent_failed"].includes(c.state)
      ? `；原因：${reasonLabels[c.reason] ?? "未归类，请核对原记录"}` : "";
    const time = c.state === "accepted" && c.accepted_at_ms
      ? `，受理于 ${bjTime(c.accepted_at_ms)}`
      : c.expires_at_ms ? `，意图截止 ${bjTime(c.expires_at_ms)}` : "";
    return `${c.channel === "feishu" ? "飞书" : c.channel}：${labels[c.state] ?? "状态未知"}${reason}${time}`;
  }).join("；")}</span>;
}
