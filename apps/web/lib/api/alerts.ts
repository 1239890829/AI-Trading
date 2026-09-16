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

/** 空态诊断（`BUG-016` 子项③，2026-09-16）。

 * **为什么需要它**：空响应体本身区分不出三种处境——「真无机会（跑了但全被否）」
 * 「链路未跑」「上游空（盘前没选出）」，三者都是 `{items: [], count: 0}`。
 * 后端仅在 `items` 为空时附上本字段（非空态恒为 `null`），用于把"为什么空"讲清楚。
 *
 * ⚠️ 本字段**只是解释**，不改变任何推送口径（档位门等属交易信号口径，须用户拍板）。
 */
export interface NotificationDiagnostics {
  /** 四态 + 一降级：上游空 / 链路未跑 / 全被否 / 有通过却空 / 诊断不可用 */
  state: "no_pick_set" | "no_run" | "ran_rejected" | "ran_eligible" | "unavailable";
  trade_date: string;
  as_of: string;
  /** 读取窗口内**各事件形状的原始条数**（2026-09-16 新增）。
   *
   * **为什么必须单独报**：`state` / `decisions` 都来自买点链（`__picks_buy_point__`），
   * 只回答"买点链有没有选出票"；而通知中心实际收两个形状
   * （`buy_point` + `pre_limit`，见后端 `_NOTIF_KINDS`）——当买点链整天没触发
   * （2026-09-16 实测：`alert_rule` 表里根本没那行）而临板预警刷了一整天时，
   * 只看 `state` 会得出"上游空"的误导结论。
   *
   * 语义要点：
   *  - 键是形状名（`buy_point` / `pre_limit` / `board_low_absorb` / …），值是**原始条数**；
   *  - 白名单两键**恒在**（0 也返回）⇒ 「缺键 = 没统计」与「0 条 = 确实没有」可区分；
   *  - 统计的是**读取窗口**（后端 `_NOTIF_FETCH_LIMIT`）而非全天；窗口外的旧事件不计入。
   *  - `symbol=000000` 的板块级形状也会出现——正是它们当对照物才有诊断价值。 */
  shapes?: Record<string, number>;
  /** 当日盘前精选摘要；`unavailable` 时只有 `present` / `count` */
  pick_set: {
    present: boolean;
    count: number;
    tier_counts?: Record<string, number>;
    score_range?: [number, number] | null;
    observation_only?: number;
    no_buy_range?: number;
    gate?: {
      stand_aside: boolean;
      level: string | null;
      phase: string | null;
      reasons: string[];
    };
  };
  /** 当日 `notification` 阶段判定证据摘要；`unavailable` 时只有 `present` / `polls` */
  decisions: {
    present: boolean;
    polls: number;
    by_decision?: Record<string, number>;
    symbols?: string[];
    tier_counts?: Record<string, number>;
    top_tier?: string | null;
    unknown_tiers?: string[];
    /** 逐股否决原因；`count` 按 symbol 去重（不是记录数） */
    reasons?: { reason: string; count: number; symbols: string[] }[];
    last_as_of?: string | null;
  };
  note: string;
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
  /** 空态诊断；**非空态恒为 null**（后端刻意只在空态附加），旧后端也可能不返回该键 */
  diagnostics?: NotificationDiagnostics | null;
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
