"use client";

import { IconButton, CloseButton } from "@/components/ui/icon-button";
import { HugeiconsIcon } from "@hugeicons/react";
import Notification03Icon from "@hugeicons/core-free-icons/Notification03Icon";

import { SelectionRail } from "@/components/ui/selection-rail";

import { useExitPresence } from "@/hooks/use-exit-presence";
import { useOverlayFocus } from "@/hooks/use-overlay-focus";

import { useCallback, useEffect, useRef, useMemo, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

import {
  getNotifications,
  getNotificationDiagnostics,
  type NotificationDiagnostics,
  type NotificationItem,
  type NotificationsPayload,
} from "@/lib/api";
import {
  countUnread,
  getPrefsSnapshot,
  getPrefsSyncSnapshot,
  getServerPrefsSnapshot,
  getServerPrefsSyncSnapshot,
  isCleared,
  isUnread,
  setPrefs,
  subscribePrefs,
  subscribePrefsSync,
  withAllRead,
  withRead,
} from "@/lib/notification-read";
import { StockLink } from "@/components/stock-link";
import { IncrementalSentinel } from "@/components/ui/incremental-sentinel";
import { useIncremental } from "@/hooks/use-incremental";
import { useResource } from "@/hooks/use-resource";
import { ChannelStatus } from "@/components/notifications/channel-status";
import { useDetailModal, type DetailPayload } from "@/components/detail/detail-modal";
import { useSymbolDetail } from "@/components/detail/symbol-detail-context";
import { NewsModal, type NewsModalItem } from "@/components/news-modal";
import { EventFeed } from "@/components/notifications/event-feed";
import { formatLegacyLimitDistance } from "@/lib/format";

/**
 * 站内通知中心（2026-09-07 用户需求③）：导航栏铃铛 → 右侧抽屉。
 *
 * 机会消息读取原入选与买点记录，同股同日合并，保留原时点和后续条件变化。
 * 真实持仓风险与用户自设条件提醒独立保留。普通临板/开板、大单与板块异动
 * 仅供观察，不因此取得机会消息资格。新闻与事件在独立浏览页签读取。
 * 抽屉内一层 tab 按 盘前/盘中/盘后 分类（后端判定：交易日历优先，非交易日归盘前）。
 * 新闻不逐条推送；旧 news_min_score 参数只为客户端兼容保留。
 *
 * ⚠️ 2026-09-16（用户需求①）**收口不变，但补回可见性**：
 * `IMP-028` 的收口让「资讯 / 事件」在通知中心**彻底不可见**（实测生产库当日
 * `event_card` 798 条、通知中心 0 条）。现于抽屉**顶层**加一个「资讯 / 事件」tab：
 *  - 它是**浏览面**（只读 `GET /api/events/impact`，与盘面页事件标签同源），
 *    **不是**推送面 —— 铃铛徽标与未读红点由个股消息、风险与自设提醒驱动；
 *  - 顶层两个 tab 是两条**正交**的轴：`个股提醒`（消息，按时段分页）
 *    与 `资讯事件`（浏览，按排序/影响力筛选）。**不把资讯塞进时段 tab** ——
 *    盘前/盘中/盘后是时间轴，塞进内容轴会让「盘中」语义含混。
 *
 * 已读规则（2026-09-11 重做，见 lib/notification-read.ts 的根因说明）：
 *  - 未读 = **条目级**：`ts` 晚于已读水位、且未被单独点开；未读条目左侧带红点；
 *  - 点开某条 → 该条已读、红点消失；「全部已读」→ 水位推进到当下，红点全消；
 *  - 铃铛徽标 = 未读条数（**派生自本次 payload**，不再用会失真的字符串比较）；
 *  - 打开抽屉**不再**自动全部已读（否则红点会一闪即逝，看不到自己没读什么）。
 *
 * 持久化（2026-09-12 缺陷修复）：已读状态**双写** —— localStorage 作首帧缓存 +
 * 服务端权威副本（`/api/notifications/read-state`）。此前只存 localStorage，
 * 而它按 origin 命名空间，换源/换 profile/清站点数据就让已读整体归零
 * （实测 localhost↔127.0.0.1 徽标回到 65）。同步逻辑全在 `lib/notification-read.ts`，
 * 本组件只消费外部存储快照，因此无需改动。
 *
 * ⚠️ 历史缺陷（勿回退）：旧实现把「已读水位」写成 `toISOString()`（UTC 带 T），
 * 却与后端 `ts`（北京 naive 带空格）做**字面比较** —— 当天条目恒被判为已读，
 * 跨日又整天一起计入未读，表现为「一键已读后计数没了，来了新的却在旧累积上累加」。
 * 一律先 `parseTs` 转 epoch 再比大小。
 *
 * 落点口径（2026-09-16 `IMP-033`）：
 *  - **有代码的条目（个股机会）** ⇒ 行体与「行情 ↗」**都开该股详情弹窗**，
 *    与悬浮球 `openSymbolDetail`、猎场 `StockLink` **三处同落点**（[[KB-ENG-92]] 详情弹窗化）；
 *  - **判读全文**（分类 / 评分 / 理由）改由行右侧**「判读」**入口打开 ⇒ 行体换落点
 *    保留原消息标题与正文的全文核对入口；
 *  - **无代码的条目**（如消息面）保持行体 → 通用详情弹窗，不静默失败。
 */

/** 顶层模式：个股消息、风险、自设提醒 ↔ 资讯 / 事件浏览。 */
type DrawerMode = "opportunity" | "events";

const MODE_TABS: { key: DrawerMode; label: string; title: string }[] = [
  { key: "opportunity", label: "个股提醒", title: "原入选/买点消息、真实持仓风险与自设条件提醒（按盘前/盘中/盘后分页）" },
  { key: "events", label: "资讯 / 事件", title: "事件影响力视图（浏览面，不计入未读；与盘面页事件标签同源）" },
];

const SESSION_TABS: { key: NotificationItem["session"]; label: string }[] = [
  { key: "pre_open", label: "盘前" },
  { key: "intraday", label: "盘中" },
  { key: "after_close", label: "盘后" },
];


const CATEGORY_TONE: Record<NotificationItem["category"], string> = {
  opportunity: "bg-up/10 text-up-ink dark:text-up",
  daily_picks: "bg-amber-500/10 text-amber-800 dark:text-amber-400",
  news: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  risk: "bg-red-500/10 text-red-700 dark:text-red-300",
  reminder: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
};

const CATEGORY_LABEL: Record<NotificationItem["category"], string> = {
  opportunity: "个股机会",
  daily_picks: "每日精选",
  news: "消息面",
  risk: "策略风险",
  reminder: "自设提醒",
};

function timeText(ts: string | null): string {
  if (!ts) return "--";
  // 后端两种 ts 语义都截 HH:MM 展示（日期在标题里）；跨天场景标题带日期足够
  return ts.length >= 16 ? ts.slice(11, 16) : ts;
}

/**
 * **判读详情**弹窗的载荷（2026-09-16 `IMP-033`）。
 *
 * 抽成**单一构造函数**而不是在两处各拼一份：行体与「判读」按钮展示的是**同一份判读**，
 * 两份拼装一旦漂移（如忘了带 `symbol`、或分类映射改了），
 * 就会出现"同一个东西从两个入口点开看到不同内容"——正是本项要消除的那类不一致。
 */
function selectionState(state: string): string {
  return ({completed: "投影完成", pending: "待投影", failed: "投影失败", no_pick_set: "无当日组合记录", no_run: "未见归档记录", unavailable: "读取失败", unknown: "状态未知", no_archive: "未见当日归档", projection_unconfirmed: "投影结果未确认", present: "记录已保存", recorded: "已有原记录", ready: "记录就绪", ran_eligible: "已通过判定"} as Record<string, string>)[state] ?? "状态未归类，请核对原记录";
}

function evidenceLines(evidence: Record<string, unknown>): string[] {
  const lines: string[] = [];
  const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
  const labels: Record<string, string> = {tech: "技术", news: "消息", fundamental: "基本", capital: "资金", sentiment: "情绪", echelon: "梯队"};
  for (const key of ["source_basis", "pick_basis"]) if (typeof evidence[key] === "string") lines.push(String(evidence[key]));
  for (const [key, value] of Object.entries(object(evidence.bases))) if (typeof value === "string") lines.push(`${labels[key] ?? key}：${value}`);
  const confidence = object(evidence.confidence);
  if (confidence.label || confidence.tier) lines.push(`原规则档位：${confidence.label ?? confidence.tier}（不是胜率）；${Array.isArray(confidence.reasons) ? confidence.reasons.join("；") : "理由未记录"}`);
  for (const [key, label] of [["linkage", "联动"], ["tradability", "可参与性"]]) {
    const value = object(evidence[key]);
    if (value.level || value.basis) lines.push(`${label}：${value.level ?? "未知"} · ${value.basis ?? "依据未记录"}`);
  }
  const range = object(evidence.buy_range);
  if (typeof range.low === "number" && typeof range.high === "number") lines.push(`原参考区间：${range.low}–${range.high}；${range.basis ?? "依据未记录"}（不是成交）`);
  if (evidence.observation_only === true) lines.push("原生成时仅观察，不代表当前执行资格。");
  for (const [key, label] of [["invalidations", "失效条件"], ["vetoes", "否决依据"]]) if (Array.isArray(evidence[key]) && evidence[key].length) lines.push(`${label}：${evidence[key].filter(value => typeof value === "string").join("；")}`);
  const audit = object(evidence.quote_audit);
  if (audit.source || audit.data_timestamp) lines.push(`原报价：${audit.source ?? "来源未知"} · ${audit.data_timestamp ?? "源时点未知"} · 质量 ${audit.quality ?? "未知"}`);
  for (const [key, raw] of Object.entries(object(evidence.dimension_evidence))) {
    const value = object(raw);
    if (["partial", "missing", "error"].includes(String(value.state))) lines.push(`${labels[key] ?? key}依据缺项：${value.reason ?? "原因未记录"}`);
  }
  if (Array.isArray(evidence.theme_sources)) for (const raw of evidence.theme_sources) {
    const source = object(raw);
    lines.push(`题材来源：${source.theme ?? "未记录"} · ${source.strength_tier ?? "强度未记录"}；${source.pick_basis ?? object(source.linkage).basis ?? "依据未记录"}`);
  }
  if (Array.isArray(evidence.related_event_refs)) for (const raw of evidence.related_event_refs) {
    const event = object(raw);
    lines.push(`原关联事件：${event.title ?? event.event_id ?? event.id ?? "事件身份未记录"}${event.source_version ? ` · 版本 ${event.source_version}` : ""}`);
  }
  const execution = object(evidence.execution_ref);
  if (execution.decision_id || execution.decision_version) lines.push(`原执行引用：${execution.decision_id ?? "身份未记录"} · ${execution.decision_version ?? "版本未记录"}（不是成交）`);
  return lines.length ? lines : ["原结构化依据未提供；不使用最新行情回填。"] ;
}

function judgmentPayload(item: NotificationItem): DetailPayload {
  return {
    kind: item.category === "news" ? "event" : "generic",
    title: item.title,
    body: [formatLegacyLimitDistance(item.body), ...(item.references ?? []).flatMap(ref => [
      `原记录 #${ref.event_id} · ${ref.source_as_of ?? "原时点未记录"} · 版本 ${ref.source_version ?? "未绑定"}`,
      ...evidenceLines(ref.evidence),
    ])].join("\n"),
    symbol: item.symbol,
    source: item.source ?? null,
    date: item.ts,
    meta: [
      { label: "分类", value: item.label },
      ...(item.recorded_at ? [{label: "记录时点", value: item.recorded_at}] : []),
      ...(item.score != null ? [{ label: "评分", value: String(item.score) }] : []),
    ],
  };
}

/**
 * 空态文案与诊断（`BUG-016` 子项③，2026-09-16）。
 *
 * **为什么要有**：用户现场反馈「为什么消息通知一个也没有呢」。旧实现只有一句
 * 「盘中暂无通过多维筛选的个股机会」——而它同时覆盖了三种**完全不同的处境**：
 * 「跑了但全被否」「链路根本没跑」「盘前就没选出候选」。三者要做的事不一样
 * （看原因 / 查调度时段 / 看盘前选股），一句话讲不清，等于把"无证据"写成"证据表明没有"。
 *
 * ⚠️ 诊断字段**只在整份 payload 为空时**才由后端附带（非空态恒 `null`）：
 * 因此「本时段空、别的时段有」不要展示诊断（那不是"空态"，只是切到了没内容的 tab）。
 */
const NOTIF_STATE_HEADLINE: Record<NotificationDiagnostics["state"], string> = {
  no_pick_set: "当日精选记录缺失或无候选",
  no_run: "未找到今日买点判定归档",
  ran_rejected: "已归档候选均被否决",
  ran_eligible: "有候选通过或被去重，需核对原事件",
  ran_unknown: "判定状态未能确认",
  unavailable: "诊断不可用",
};

/** 后端 `note` 按 Markdown 写（`**强调**` / `` `行内代码` ``）；抽屉里是**纯文本** ⇒ 剥掉标记。

 * 实测踩到（2026-09-16）：只剥 `**` 时，note 里的 `` `快照无现价` `` 会**把反引号原样显示**
 * 在界面上（渲染快照确凿可见）——"以实际渲染为准"才会发现这类问题，
 * 读代码时它看起来"已经处理了 Markdown"。
 */
function plainNote(s: string): string {
  return s.replace(/\*\*/g, "").replace(/`/g, "");
}

/** 事件形状（后端 `snapshot.kind`）的外显名。
 *
 * ⚠️ 未知形状**原样显示键名**，不吞成"其他"——诊断面上"看到一个没见过的形状"
 * 恰恰是最该被看见的信息，归一化成"其他"等于把新情况藏起来。
 */
const SHAPE_LABEL: Record<string, string> = {
  selection: "入选",
  buy_point: "买点",
  pre_limit: "临板预警",
  flow_surge: "大单异动",
  board_low_absorb: "板块低吸",
  board_flow_surge: "板块资金异动",
  falsify: "方向证伪",
  high_board_break: "高板断裂",
  timeout: "判定超时",
  signal_health: "信号健康",
  real_exit_alert: "真实持仓风险",
};

/** 机会消息的原事件来源。计数不等于消息资格或合并后的消息条数；风险/自设独立。 */
const NOTIF_KINDS = ["selection", "buy_point"] as const;

/** 读取窗口内的原事件计数；不凭空列表推断调度、消息资格或字段校验的结果。 */
function ShapeCounts({ shapes }: { shapes: Record<string, number> }) {
  const keys = Object.keys(shapes);
  // 空对象与缺键都表示计数未知，不能按 0 条解释，更不能证明未运行。
  if (keys.length === 0) {
    return (
      <p
        data-testid="notification-empty-shapes"
        data-stock-level="unknown"
        className="border-t border-zinc-200 pt-2 text-zinc-600 dark:border-zinc-700 dark:text-zinc-400"
      >
        来源形状计数未提供；无法判断规则是否运行或是否出现入选结果。风险和自设提醒独立核对。
      </p>
    );
  }
  const stockLevel = NOTIF_KINDS.reduce((n, k) => n + (shapes[k] ?? 0), 0);
  const complete = NOTIF_KINDS.every((k) => typeof shapes[k] === "number");
  const rest = Object.entries(shapes)
    .filter(([k, v]) => v > 0 && !(NOTIF_KINDS as readonly string[]).includes(k))
    .sort((a, b) => b[1] - a[1]);
  const restTotal = rest.reduce((n, [, v]) => n + v, 0);
  return (
    <div
      data-testid="notification-empty-shapes"
      data-stock-level={complete ? stockLevel : "unknown"}
      className="space-y-1 border-t border-zinc-200 pt-2 dark:border-zinc-700"
    >
      <p className="text-zinc-700 dark:text-zinc-300">
        <span className="text-zinc-600 dark:text-zinc-400">
          <strong className="font-medium">原入选/买点记录</strong>（读取窗口内）：
        </span>
        {NOTIF_KINDS.map((k) => `${SHAPE_LABEL[k]} ${shapes[k] ?? "未提供"}`).join(" · ")}
      </p>
      {!complete ? (
        <p className="text-zinc-600 dark:text-zinc-400">
          原入选/买点计数未完整提供；无法判断读取窗口内是否有记录，也不能据此判断规则是否运行。
        </p>
      ) : stockLevel === 0 ? (
        <p className="text-zinc-600 dark:text-zinc-400">
          读取窗口内未见原入选/买点记录；不代表全天未运行或没有入选结果。
        </p>
      ) : (
        <p className="text-zinc-600 dark:text-zinc-400">
          原记录共 {stockLevel} 条；消息按同股同日合并，不能据此判断消息资格或未进入列表的原因。请核对原事件、时点与条件。
        </p>
      )}
      <p className="text-zinc-600 dark:text-zinc-400">风险和自设提醒独立核对。</p>
      {restTotal > 0 && (
        <p className="text-zinc-600 dark:text-zinc-400">
          其余来源记录 {restTotal} 条（不计入原入选/买点合计）：
          {rest
            .slice(0, 4)
            .map(([k, v]) => `${SHAPE_LABEL[k] ?? k} ${v}`)
            .join("、")}
          {rest.length > 4 ? ` 等 ${rest.length} 种` : ""}
          。普通临板/开板、大单与板块异动仅供观察，不因此取得机会消息资格。
        </p>
      )}
    </div>
  );
}

function NotificationDiagnosis({ diag }: { diag: NotificationDiagnostics }) {
  const d = diag.decisions;
  const ps = diag.pick_set;
  const reasons = d.reasons ?? [];
  const decisionLabels: Record<string, string> = {
    rejected: "硬门拒绝", eligible: "通过判定，派发待核对",
    notified: "已登记提醒事件", suppressed: "当日去重",
  };
  return (
    <div
      data-testid="notification-empty-diagnosis"
      data-state={diag.state}
      className="space-y-2 rounded-lg border border-zinc-200 bg-zinc-50/70 px-3 py-3 text-xs dark:border-zinc-700 dark:bg-zinc-800/40"
    >
      <p className="font-medium text-zinc-800 dark:text-zinc-100">
        今日买点链：{NOTIF_STATE_HEADLINE[diag.state] ?? diag.state}
      </p>
      <p className="text-[11px] text-zinc-600 dark:text-zinc-400">
        仅汇总今日已归档买点判定；不代表全部每日或盘中入选结果，也不代表全部通知链。
      </p>
      <p className="text-zinc-600 dark:text-zinc-400">{plainNote(diag.note)}</p>
      {diag.state !== "unavailable" && <p className="text-[11px] text-zinc-600 dark:text-zinc-400">
        候选 {ps.count} 只 · 最高档 {d.top_tier ?? "无"} · 判定 {d.polls} 拍
        {diag.trade_date ? ` · ${diag.trade_date}` : ""}
        {diag.as_of ? ` · 诊断于 ${diag.as_of.slice(11, 16)}` : ""}
      </p>}
      {/* 原入选/买点与其他来源计数单独核对，不能把买点归档摘要当作全部通知链。 */}
      {diag.selection && <div className="space-y-1 border-t border-zinc-200 pt-2 dark:border-zinc-700" data-testid="notification-selection-diagnosis">
        <p>每日入选链：{selectionState(diag.selection.daily.state)} · {diag.selection.daily.count ?? "数量未记录"} 只</p>
        {diag.selection.daily.receipt && <p>消息投影：{selectionState(diag.selection.daily.receipt.state ?? "unknown")} · 新记录 {diag.selection.daily.receipt.created ?? "未记录"} 条</p>}
        <p>盘中入选链：{selectionState(diag.selection.intraday.state)} · 归档候选 {diag.selection.intraday.ranked_count ?? "未记录"} 只 · 原入选消息 {diag.selection.intraday.message_count ?? "未记录"} 条</p>
      </div>}
      {diag.shapes && <details className="border-t border-zinc-200 pt-2 text-[11px] dark:border-zinc-700">
        <summary className="cursor-pointer text-zinc-600 dark:text-zinc-400">查看来源核对信息</summary>
        <div className="mt-1"><ShapeCounts shapes={diag.shapes} /></div>
      </details>}
      {reasons.length > 0 && (
        <ul className="space-y-1 border-t border-zinc-200 pt-2 dark:border-zinc-700">
          {reasons.map((r) => (
            <li key={r.reason} className="text-zinc-700 dark:text-zinc-300">
              <span className="text-zinc-600 dark:text-zinc-400">{r.count} 只：</span>
              {r.reason}
              {r.symbols.length > 0 && (
                <span className="text-zinc-600 dark:text-zinc-400">（{r.symbols.join("、")}）</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {(d.latest?.length ?? 0) > 0 && (
        <details className="border-t border-zinc-200 pt-2 dark:border-zinc-700">
          <summary className="cursor-pointer text-zinc-600 dark:text-zinc-400">查看逐股原决定与版本</summary>
          <ul className="mt-1 space-y-1">
            {d.latest?.map((row) => <li key={row.snapshot_id} className="text-zinc-700 dark:text-zinc-300">
              <StockLink symbol={row.symbol} className="text-sky-700 dark:text-sky-400">{row.name || row.symbol} {row.symbol}</StockLink>
              {` · ${decisionLabels[row.decision] ?? "状态未知"} · ${row.as_of} · ${row.reason || "原因未记录"}`}
              <span className="block break-all text-[10px] text-zinc-600 dark:text-zinc-400">
                记录 {row.snapshot_id} · 决定 {row.decision_id || "未记录"} · 版本 {row.decision_version || "未记录"} · 数据 {row.data_state || "未知"}
              </span>
            </li>)}
          </ul>
        </details>
      )}
    </div>
  );
}

function NotificationEmptyState({ payload, tab, clearBefore }: {
  payload: NotificationsPayload;
  tab: NotificationItem["session"];
  clearBefore: number;
}) {
  if (payload.errors?.alerts) {
    return <p className="px-2 py-8 text-center text-xs text-amber-700 dark:text-amber-300">通知来源读取失败，当前条目不可确认；请刷新后重试。</p>;
  }
  const cleared = payload.items.some((item) => isCleared(item, clearBefore));
  if (cleared && payload.items.every((item) => isCleared(item, clearBefore))) {
    return <p className="px-2 py-8 text-center text-xs text-zinc-600 dark:text-zinc-400">当前读取范围的通知已被一键清除隐藏；原事件记录仍保留。</p>;
  }
  if (payload.count > 0) {
    return <p className="px-2 py-8 text-center text-xs text-zinc-600 dark:text-zinc-400">该时段暂无可见通知，请查看其他时段。</p>;
  }
  if (payload.diagnostics) return <NotificationDiagnosis diag={payload.diagnostics} />;
  return <p className="px-2 py-8 text-center text-xs text-zinc-600 dark:text-zinc-400">
    {tab === "intraday" ? "盘中通知状态未知，请查看未提醒原因" : "该时段通知状态未知，请查看未提醒原因"}
  </p>;
}


function NotificationRow({
  item,
  unread,
  onRead,
  onOpenNews,
}: {
  item: NotificationItem;
  /** 未读 → 左侧红点；点开即消失（用户 2026-09-11 需求） */
  unread: boolean;
  onRead: () => void;
  onOpenNews: (n: NewsModalItem) => void;
}) {
  const { open: openDetail } = useDetailModal();
  // 2026-09-16 `IMP-033`：个股提醒的落点与悬浮球 / 猎场统一（就地开该股详情弹窗）。
  const { open: openSymbolDetail } = useSymbolDetail();
  // 2026-09-09：无 url 的通知（快讯类大多无原文链接）此前渲染成死 div 点不动。
  // 现统一可点：有 url 走 NewsModal 看原文；无 url 走通用详情弹窗看 body+评分。
  const clickable = item.url?.startsWith("http") ?? false;

  /** 行体（标题 + 正文）的落点：原文 > 个股详情 > 通用判读。 */
  const openLanding = () => {
    onRead();
    if (clickable) {
      onOpenNews({ title: item.title, url: item.url!, date: item.ts, source: null, kindLabel: item.label });
    } else if (item.symbol) {
      // 2026-09-16 `IMP-033` **落点统一**：有代码 ⇒ 开**该股详情弹窗**，
      // 与悬浮球（`openSymbolDetail`）与猎场（`StockLink`）三处同落点。
      // ⚠️ 判读全文**不由此处承载** ⇒ 走下方「判读」入口——
      // **统一落点不得以丢失能力为代价**（同族纪律见 `IMP-031`）。
      // 无代码的条目（如消息面）保持通用详情弹窗，不静默失败。
      openSymbolDetail({ symbol: item.symbol });
    } else {
      openDetail(judgmentPayload(item));
    }
  };

  const shell = `rounded-lg border p-2.5 transition-colors ${
    unread
      ? "border-rose-200 bg-rose-50/40 dark:border-rose-500/25 dark:bg-rose-500/5"
      : "border-zinc-100 dark:border-zinc-800/60"
  }`;
  return (
    // ⚠️ 卡片是 `<div>`、主体是内层 `<button>`：**不能**把整个卡片做成 button。
    // 2026-09-16 用户要求「行情与判读一体化，放标签下方右边」——两者必须与主体并列
    // 落在**同一张卡片内**，而 `<button>` 嵌 `<button>`/`<a>` 是非法 HTML
    // （浏览器会拆标签、点击语义互相吞掉）。故改成"卡片容器 + 内层主体按钮 + 尾部操作组"。
    <div className={shell} data-testid="notification-row">
      <button
        type="button"
        className={`block w-full text-left ${clickable ? "cursor-pointer" : ""}`}
        onClick={openLanding}
      >
        <div className="flex flex-wrap items-center gap-1.5">
          {unread && (
            <span
              data-testid="notification-unread-dot"
              aria-label="未读"
              title="未读"
              className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500"
            />
          )}
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${CATEGORY_TONE[item.category]}`}>
            {item.label}
          </span>
          <span className="min-w-0 flex-1 truncate text-xs font-medium text-zinc-900 dark:text-zinc-50" title={item.title}>
            {item.title}
          </span>
          <span className="shrink-0 font-mono text-xs text-zinc-600 dark:text-zinc-400">{timeText(item.ts)}</span>
        </div>
        <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">{formatLegacyLimitDistance(item.body)}</p>
      </button>
      <p className="mt-1 text-xs leading-relaxed text-zinc-500 dark:text-zinc-400" data-testid="notification-facts">
        来源：{item.source ?? "历史事件，来源未标注"} · 原观察：{item.source_as_of ?? item.ts ?? "时间未知"} · 记录：{item.recorded_at ?? "历史记录钟未提供"} · {item.validity ?? "条件与时效未记录，须重新核验"}
        <br />站内记录可见；<ChannelStatus channels={item.channels} />。未读仅表示站内尚未点开。
      </p>
      {(item.references?.length ?? 0) > 0 && <details className="mt-2 text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">
        <summary className="cursor-pointer">原入选与判定依据（{item.references?.length} 条）</summary>
        <ul className="mt-2 space-y-3">{item.references?.map(ref => <li key={ref.event_id} className="min-w-0 break-words">
          <p>{ref.kind === "selection" ? "原入选" : "原判定"} · {ref.source_as_of ?? "原时点未记录"}</p>
          <p className="break-all">版本：{ref.source_version ?? "未绑定"}{ref.run_id ? ` · 归档 ${ref.run_id}` : ""}</p>
          {ref.selection_source === "daily" && ref.source_version && ref.trade_date && <a className="quiet-action mt-1 inline-flex" href={`/hunting?${new URLSearchParams({view: "review", date: ref.trade_date, version: ref.source_version})}`}>查看此版本复盘</a>}
          {evidenceLines(ref.evidence).map((line, index) => <p key={index}>{line}</p>)}
        </li>)}</ul>
        {item.symbol && <a href="/hunting?view=discover&sec=candidates" className="quiet-action mt-2 inline-flex">查看当前选股条件</a>}
      </details>}
      {/* 标签行：左侧仍是分类/评分；右侧 = **一体化的操作组**（行情 + 判读） */}
      <div className="mt-1 flex items-center gap-2 text-[10px] text-zinc-600 dark:text-zinc-400">
        <span className="rounded bg-zinc-100 px-1 py-px dark:bg-zinc-800">{CATEGORY_LABEL[item.category]}</span>
        {item.score != null && (
          <span className="font-mono" title="事件评分（与时事新闻板块同源）：影响力/题材共振/新鲜度/来源综合">
            评分 {item.score}
          </span>
        )}
        {clickable && <span className="text-sky-700 dark:text-sky-500">查看全文 ↗</span>}
        {/* 一体化操作组（2026-09-16 用户要求）：两个动作**同一容器、共享边框、以竖线分隔**，
            而不是两个各自带框、飘在卡片外的按钮。无代码条目不渲染（点了会弹一只空股）。 */}
        {item.symbol && (
          <div
            data-testid="notification-actions"
            className="ml-auto flex items-stretch overflow-hidden rounded border border-zinc-200 text-[10px] dark:border-zinc-700"
          >
            <StockLink
              symbol={item.symbol}
              className="px-1.5 py-0.5 text-zinc-600 dark:text-zinc-400"
              title={`查看 ${item.symbol} 行情详情`}
            >
              行情 ↗
            </StockLink>
            <span className="w-px self-stretch bg-zinc-200 dark:bg-zinc-700" aria-hidden />
            <button
              type="button"
              data-testid="notification-judgment"
              title="查看提醒依据与判读；未生成 AI 判读时仅显示原事件内容"
              className="px-1.5 py-0.5 text-zinc-600 transition-colors hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
              onClick={() => {
                onRead();
                openDetail(judgmentPayload(item));
              }}
            >
              判读
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const presence = useExitPresence(open ? true : null);
  const drawerRef = useRef<HTMLDivElement>(null);
  useOverlayFocus(drawerRef, () => setOpen(false), open);
  const notifications = useResource(getNotifications, {intervalMs: 60_000, marketHours: false});
  const payload = notifications.data ?? null;
  const loading = notifications.pending;
  const error = notifications.error ? "通知读取失败" : null;
  const pollError = notifications.error !== null;
  const load = () => {if (!notifications.pending) notifications.refresh();};
  const [diagnosis, setDiagnosis] = useState<NotificationDiagnostics | null>(null);
  const [diagnosisLoading, setDiagnosisLoading] = useState(false);
  const [diagnosisError, setDiagnosisError] = useState(false);
  const [tab, setTab] = useState<NotificationItem["session"]>("intraday");
  // 顶层模式（2026-09-16 需求①）：默认停在**推送面**——铃铛点开要看的是机会，
  // 不是资讯流。资讯 tab 只在用户显式切过去时才拉数据（`EventFeed` 的 `enabled`）。
  const [mode, setMode] = useState<DrawerMode>("opportunity");
  // 顶部「刷新」在资讯 tab 下的落点：`EventFeed` 是自取数的，故用自增令牌通知它重拉
  // （与 `sort` 一并作为轮询 hook 的 key）。不改用 lifting state：那会把 events 的
  // 三态（items/countsAll/error）搬进本组件，而它们只在那个 tab 里有意义。
  const [eventsRefresh, setEventsRefresh] = useState(0);
  const [newsItem, setNewsItem] = useState<NewsModalItem | null>(null);
  // 已读偏好（水位 + 逐条 id + 清除水位）：**外部存储订阅**，见 lib/notification-read.ts。
  // 首帧（含 hydration）给服务端空快照，hydration 后自动切到 localStorage 真实值。
  const prefs = useSyncExternalStore(subscribePrefs, getPrefsSnapshot, getServerPrefsSnapshot);
  const syncStatus = useSyncExternalStore(subscribePrefsSync, getPrefsSyncSnapshot, getServerPrefsSyncSnapshot);
  const { read: readState, clearBefore } = prefs;

  /** 一键已读：水位推进到「现在」——覆盖所有更早条目（含拉取窗口外的历史）。 */
  const markAllRead = useCallback(() => {
    setPrefs({ read: withAllRead(readState), clearBefore });
  }, [readState, clearBefore]);

  /** 一键清除：记录清除时刻——更早条目整体隐藏；顺带把已读水位一并推到该时刻。 */
  const clearAll = useCallback(() => {
    const ts = Date.now();
    setPrefs({ read: withAllRead(readState, ts), clearBefore: ts });
  }, [readState]);

  const markOneRead = useCallback(
    (item: NotificationItem) => {
      const nextRead = withRead(readState, item);
      if (nextRead !== readState) setPrefs({ read: nextRead, clearBefore });
    },
    [readState, clearBefore],
  );

  const loadDiagnosis = useCallback(async () => {
    setDiagnosisLoading(true);
    setDiagnosisError(false);
    try {
      setDiagnosis(await getNotificationDiagnostics());
    } catch {
      setDiagnosisError(true);
    } finally {
      setDiagnosisLoading(false);
    }
  }, []);

  // Mount, polling and explicit refresh share the same resource and reply guard.

  // 未读数：**派生自当前 payload**（单一真相源是 payload.items + readState），
  // 不再用「上一次轮询算出的数字」——那正是"计数在旧累积上叠加"的来源。
  const unread = useMemo(
    () => countUnread(payload?.items ?? [], readState, clearBefore),
    [payload, readState, clearBefore],
  );

  const bySession = useMemo(() => {
    const m: Record<NotificationItem["session"], NotificationItem[]> = { pre_open: [], intraday: [], after_close: [] };
    for (const i of payload?.items ?? []) {
      if (isCleared(i, clearBefore)) continue;
      m[i.session].push(i);
    }
    return m;
  }, [payload, clearBefore]);

  const unreadBySession = useMemo(() => {
    const m: Record<NotificationItem["session"], number> = { pre_open: 0, intraday: 0, after_close: 0 };
    for (const i of payload?.items ?? []) {
      if (isCleared(i, clearBefore)) continue;
      if (isUnread(i, readState)) m[i.session] += 1;
    }
    return m;
  }, [payload, readState, clearBefore]);

  const isItemUnread = useCallback(
    (item: NotificationItem) => !isCleared(item, clearBefore) && isUnread(item, readState),
    [clearBefore, readState],
  );

  // 增量渲染（#55）：/api/notifications 默认 60 条（实测 16KB），每条是富文本卡片，
  // 一次性全铺会拖慢抽屉打开的首帧。切换时段 tab / 清空历史时重置到首页。
  const activeList = bySession[tab];
  const { shown: shownNotices, visible, sentinelRef } = useIncremental(activeList, {
    resetKey: `${tab}/${clearBefore}`,
  });

  return (
    <>
      <IconButton
        onClick={() => {setOpen(true); load();}}
        aria-label={pollError ? `打开通知中心（上次读取的未读数 ${unread}）` : unread > 0 ? `打开通知中心（${unread} 条未读）` : "打开通知中心"}
        title={pollError ? "通知刷新失败：未读数依据上次读取结果" : "通知中心：个股机会、持仓风险与资讯浏览；未读仅指站内浏览状态"}
        className="header-action notification-trigger"
      >
        <HugeiconsIcon icon={Notification03Icon} size={18} strokeWidth={1.6} aria-hidden="true" />
        {unread > 0 && (
          <span
            data-testid="notification-badge"
            className="notification-badge"
            aria-label={`${unread} 条未读通知`}
          >
            <span key={Math.min(unread, 100)} className="notification-count" aria-hidden="true">{unread > 99 ? "99+" : unread}</span>
          </span>
        )}
      </IconButton>

      {presence.value &&
        createPortal(
          <div ref={drawerRef} tabIndex={-1} data-motion-state={open ? "open" : "closed"} aria-hidden={!open || undefined} inert={!open} className="motion-overlay fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-sm" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }} role="dialog" aria-modal="true" aria-label="通知中心" data-testid="notification-drawer">
            <div className="ui-glass-overlay motion-drawer flex h-full w-full max-w-sm flex-col border-l border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-950">
              {/* 头：标题 + 未读数 + 操作 */}
              <div className="flex items-center justify-between border-b border-zinc-100 px-4 py-3 dark:border-zinc-800/80">
                <h2 className="flex items-center gap-1.5 text-sm font-semibold text-zinc-900 dark:text-zinc-50">
                  通知中心
                  {unread > 0 && (
                    <span
                      data-testid="notification-unread-summary"
                      className="rounded-full bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-medium text-rose-700 dark:text-rose-300"
                    >
                      未读 {unread}
                    </span>
                  )}
                </h2>
                <div className="flex items-center gap-2">
                  {/* 已读/清除是**推送面**的操作（作用域 = `/api/notifications` 的已读水位）。
                      资讯 tab 下隐藏：那里没有未读概念，按钮点了也没有可见反馈。 */}
                  {mode === "opportunity" && (
                    <>
                      <button
                        onClick={markAllRead}
                        disabled={unread === 0}
                        data-testid="notification-mark-all-read"
                        className="rounded px-1.5 py-0.5 text-[11px] text-zinc-600 transition-colors hover:bg-zinc-100 hover:text-zinc-700 disabled:opacity-40 disabled:hover:bg-transparent dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                        title="全部标记为已读（未读红点与徽标清零）"
                      >
                        全部已读
                      </button>
                      <button
                        onClick={clearAll}
                        className="rounded px-1.5 py-0.5 text-[11px] text-zinc-600 dark:text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                        title="按时间水位隐藏当前及更早的站内条目；服务端确认后清浏览器缓存不会恢复，原事件审计仍保留"
                      >
                        一键清除
                      </button>
                    </>
                  )}
                  <IconButton
                    onClick={() => (mode === "opportunity" ? void load() : setEventsRefresh((n) => n + 1))}
                    className="rounded p-1 text-zinc-600 dark:text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                    aria-label="刷新通知"
                    title="重新拉取（页面打开期间每 60s 自动检查新内容）"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" />
                    </svg>
                  </IconButton>
                  <CloseButton onClose={() => setOpen(false)} />
                </div>
              </div>

              {/* 顶层 tab：个股机会（推送面）/ 资讯·事件（浏览面）。
                  与下方时段 tab 是**两条正交的轴**，故单独一层，不与之合并。 */}
              <SelectionRail activeKey={mode} role="tablist" label="通知中心内容类型" className="notification-mode-tabs"
              >
                {MODE_TABS.map((m) => (
                  <button
                    key={m.key}
                    role="tab"
                    tabIndex={mode === m.key ? 0 : -1}
                    onKeyDown={event => {
                      if (event.nativeEvent.isComposing || event.keyCode === 229) return;
                      const offset = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
                      if (!offset && event.key !== "Home" && event.key !== "End") return;
                      event.preventDefault();
                      const index = MODE_TABS.findIndex(item => item.key === m.key);
                      const next = event.key === "Home" ? 0 : event.key === "End" ? MODE_TABS.length - 1 : (index + offset + MODE_TABS.length) % MODE_TABS.length;
                      const key = MODE_TABS[next].key;
                      setMode(key);
                      event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`[data-testid="notification-mode-${key}"]`)?.focus();
                    }}
                    aria-selected={mode === m.key}
                    data-testid={`notification-mode-${m.key}`}
                    onClick={() => setMode(m.key)}
                    title={m.title}
                    className={`flex-1 rounded-md px-3 py-1 text-xs transition-colors ${
                      mode === m.key
                        ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                        : "text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </SelectionRail>

              {mode === "events" ? (
                <EventFeed active={open && mode === "events"} refreshToken={eventsRefresh} />
              ) : (
                <>
              <div className="border-b border-zinc-100 px-4 py-2 text-[11px] dark:border-zinc-800/80">
                <button type="button" onClick={() => diagnosis ? setDiagnosis(null) : void loadDiagnosis()} disabled={diagnosisLoading}
                  className="text-sky-700 hover:underline disabled:opacity-50 dark:text-sky-400"
                  data-testid="notification-explain">
                  {diagnosisLoading ? "正在核对原因…" : diagnosis ? "收起今日未提醒原因" : "查看今日未提醒原因"}
                </button>
                {diagnosisError && <p className="mt-1 text-amber-700 dark:text-amber-300">原因记录读取失败，当前状态未知；请重试。</p>}
                {diagnosis && <div className="mt-2 max-h-52 overflow-y-auto"><NotificationDiagnosis diag={diagnosis} /></div>}
              </div>
              <p className="px-4 pt-2 text-xs text-zinc-600 dark:text-zinc-400">本次读取最近 {payload?.read_window?.limit ?? 50} 条消息{payload?.read_window?.has_more === true ? "，更早记录未包含" : payload?.read_window?.has_more === null ? "，更早记录是否存在尚未确认" : ""}；入选消息按原观察钟分时段，其他消息沿事件记录钟；已读按记录钟判断。</p>
              {payload?.monitor && ["uncompleted", "unknown"].includes(payload.monitor.state) && <p role="status" data-testid="notification-monitor-state" className="mx-4 mt-2 rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:text-amber-200">
                持仓风险核对{payload.monitor.state === "unknown" ? "尚未完成" : "未完整完成"}{payload.monitor.uncompleted_symbols.length ? `（${payload.monitor.uncompleted_symbols.length} 只）` : ""}：{payload.monitor.reason || "源数据或成本依据待核对"}。不能据此确认风险已排除。
              </p>}
              {/* tab：盘前 / 盘中 / 盘后（红点 = 该时段有未读） */}
              <SelectionRail activeKey={tab} label="通知时段" className="notification-session-tabs">
                {SESSION_TABS.map((t) => (
                  <button
                    key={t.key}
                    aria-pressed={tab === t.key}
                    onClick={() => setTab(t.key)}
                    className={`flex items-center gap-1 rounded-full px-3 py-1 text-xs transition-colors ${
                      tab === t.key
                        ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                        : "text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                    }`}
                  >
                    {t.label}
                    {unreadBySession[t.key] > 0 && (
                      <span
                        data-testid={`notification-tab-dot-${t.key}`}
                        aria-label={`${unreadBySession[t.key]} 条未读`}
                        className="h-1.5 w-1.5 rounded-full bg-rose-500"
                      />
                    )}
                    <span className="text-[10px] opacity-70">{bySession[t.key].length}</span>
                  </button>
                ))}
              </SelectionRail>

              {/* 列表 */}
              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3" data-testid="notification-list">
                {loading && !payload && (
                  <div className="space-y-2">
                    {[0, 1, 2].map((i) => (
                      <div key={i} className="h-16 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-800/60" />
                    ))}
                  </div>
                )}
                {error && (
                  <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
                    {error}（下方为上次读取结果；可点右上刷新重试）
                  </p>
                )}
                {pollError && !error && <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">自动刷新失败；下方是上次读取结果，不能代表当前通知状态。</p>}
                {payload && bySession[tab].length === 0 && (
                  <NotificationEmptyState payload={payload} tab={tab} clearBefore={clearBefore} />
                )}
                {/* 单一渲染路径（2026-09-16 用户要求「行情与判读一体化」后简化）：
                    此前按有无 symbol 分成两支，个股条目在卡片**外面**并排挂
                    「行情 ↗」与「判读」两个独立按钮 ⇒ 视觉上三块互不相干、且与卡片
                    不对齐。现两者收进卡片内成为**一个操作组**（见 `NotificationRow`），
                    故这里不再需要分支与包裹层。 */}
                {shownNotices.map((i) => (
                  <NotificationRow
                    key={i.id}
                    item={i}
                    unread={isItemUnread(i)}
                    onRead={() => markOneRead(i)}
                    onOpenNews={setNewsItem}
                  />
                ))}
                {/* 哨兵须在滚动容器内部（本 div overflow-y-auto），否则不随滚动移动、只触发一次 */}
                <IncrementalSentinel
                  sentinelRef={sentinelRef}
                  visible={visible}
                  total={activeList.length}
                  unit="条通知"
                  testId="notification-sentinel"
                />
                {payload?.errors && (
                  <p className="pt-1 text-[10px] text-amber-500/90" title={Object.entries(payload.errors).map(([k, v]) => `${k}: ${v}`).join("；")}>
                    ⚠ 部分来源降级：{Object.keys(payload.errors).join("、")}
                  </p>
                )}
              </div>
                </>
              )}

              <div className="border-t border-zinc-100 px-4 py-2 text-[10px] leading-relaxed text-zinc-600 dark:text-zinc-400 dark:border-zinc-800/80">
                {mode === "opportunity" ? (
                  <>
                    <p data-testid="notification-sync-status" className={syncStatus === "local_only" ? "text-amber-700 dark:text-amber-300" : ""}>
                      已读与清除：{syncStatus === "synced" ? "本页变更已获服务端确认" : syncStatus === "syncing" ? "正在同步服务端" : "服务端未确认；当前页暂存"}。
                    </p>
                    个股机会与持仓风险提醒来自触发时点；板块与资讯事件在浏览页。已读只表示站内点开，
                    外部渠道受理不等于送达。条件变化后须重新核验，不构成买卖建议。
                  </>
                ) : (
                  <>
                    资讯 / 事件是<strong className="font-medium">浏览面</strong>
                    （源自东财快讯等公开渠道的事件影响力视图），不计入未读、不主动打断；
                    方向判读由规则引擎给出，不构成买卖建议。
                  </>
                )}
              </div>
            </div>
          </div>,
          document.body,
        )}

      {/* 新闻全文复用全站资讯弹窗（评分达标的新闻点击看正文） */}
      <NewsModal item={newsItem} onClose={() => setNewsItem(null)} />
    </>
  );
}
