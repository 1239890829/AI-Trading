/**
 * 告警与通知：规则、事件、通道、站内通知与已读态
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import type {
  Quote,
} from "@/types/market";

import { getJson, getJsonArray, sendJson } from "./internal";

export type AlertConditionType = "price_above" | "price_below" | "change_pct_above" | "change_pct_below";

export type AlertScope = "watchlist" | "symbols" | "all";

export interface AlertRule {
  id: number;
  name: string;
  condition_type: AlertConditionType;
  threshold: number;
  symbols: string[];
  scope: AlertScope;
  cooldown_seconds: number;
  channels: string[];
  enabled: boolean;
  last_triggered_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AlertEvent {
  /** AI 判读合并（告警面板重设计）：notify=提醒 / ignore=已降噪 / escalate=需关注
   *  model 为判读模型名；`llm_fallback` = AI 不可用、按规则提醒（P1-36 展示降级） */
  triage?: { verdict: string; reason: string; model?: string | null } | null;
  id: number;
  rule_id: number;
  symbol: string;
  trigger_value: number;
  threshold: number;
  triggered_at: string;
  acknowledged: boolean;
  delivered_channels: string[];
  snapshot: Quote | null;
}

export interface AlertChannels {
  available: string[];
  default: string[];
  /** name → 是否已配置到可真正发出（如 feishu 是否配了 webhook）；缺省视为已配置 */
  configured?: Record<string, boolean>;
}

export interface AlertRuleCreate {
  name: string;
  condition_type: AlertConditionType;
  threshold: number;
  symbols?: string[];
  scope?: AlertScope;
  cooldown_seconds?: number;
  channels?: string[];
  enabled?: boolean;
}

export async function getAlertChannels(): Promise<AlertChannels> {
  return (await getJson<AlertChannels>("/api/alerts/channels")).data;
}

export async function listAlertRules(): Promise<AlertRule[]> {
  return getJsonArray<AlertRule>("/api/alerts/rules");
}

export async function createAlertRule(rule: AlertRuleCreate): Promise<AlertRule> {
  return (await sendJson<AlertRule>("/api/alerts/rules", "POST", rule)).data;
}

export async function updateAlertRule(id: number, rule: Partial<AlertRuleCreate>): Promise<AlertRule> {
  return (await sendJson<AlertRule>(`/api/alerts/rules/${id}`, "PUT", rule)).data;
}

export async function deleteAlertRule(id: number): Promise<void> {
  await sendJson(`/api/alerts/rules/${id}`, "DELETE");
}

export async function listAlertEvents(limit = 50, ruleId?: number): Promise<AlertEvent[]> {
  const qs = new URLSearchParams();
  qs.set("limit", String(limit));
  if (ruleId != null) qs.set("rule_id", String(ruleId));
  return getJsonArray<AlertEvent>(`/api/alerts/events?${qs.toString()}`);
}

export interface NotificationItem {
  id: string;
  /** opportunity=个股机会（watcher 确认/证伪）| daily_picks=每日精选 | news=消息面/新闻/政策 | risk=策略风险（信号健康度预警） */
  category: "opportunity" | "daily_picks" | "news" | "risk";
  /** 展示标签：确认/证伪/跟踪/健康预警/每日精选/国家政策/国际时事/市场热点/原材料涨价 */
  label: string;
  /** 盘前/盘中/盘后（后端按交易日历优先判定：非交易日归盘前；日历缺失回退墙钟） */
  session: "pre_open" | "intraday" | "after_close";
  ts: string | null;
  title: string;
  body: string;
  symbol: string | null;
  url: string | null;
  /** 新闻评分为 0-100（与时事新闻板块 events ranking 同源）；其余为 null */
  score: number | null;
}

export interface NotificationsPayload {
  items: NotificationItem[];
  count: number;
  generated_at: string;
  news_min_score: number;
  /** 当前固定为只展示多维门控后的个股机会；字段用于防前后端策略漂移。 */
  policy: "stock_opportunities_only";
  /** 单源失败显式透出（降级可见），全部成功为 null */
  errors: Record<string, string> | null;
}

export async function getNotifications(): Promise<NotificationsPayload> {
  return (await getJson<NotificationsPayload>("/api/notifications")).data;
}

export interface NotificationReadStatePayload {
  seen_before: number;
  read_ids: string[];
  clear_before: number;
  updated_at: string | null;
}

export async function getNotificationReadState(): Promise<NotificationReadStatePayload> {
  return (await getJson<NotificationReadStatePayload>("/api/notifications/read-state")).data;
}

export async function saveNotificationReadState(
  state: Omit<NotificationReadStatePayload, "updated_at">,
): Promise<NotificationReadStatePayload> {
  return (await sendJson<NotificationReadStatePayload>("/api/notifications/read-state", "PUT", state)).data;
}
