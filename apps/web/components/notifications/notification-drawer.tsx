"use client";

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
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
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { useDetailModal, type DetailPayload } from "@/components/detail/detail-modal";
import { useSymbolDetail } from "@/components/detail/symbol-detail-context";
import { NewsModal, type NewsModalItem } from "@/components/news-modal";
import { EventFeed } from "@/components/notifications/event-feed";

/**
 * 站内通知中心（2026-09-07 用户需求③）：导航栏铃铛 → 右侧抽屉。
 *
 * 内容保留具名买点、临板/开板与真实持仓风险。板块异动、题材方向、每日精选与新闻留在各自分析页面，
 * 不再作为会打断用户的通知。
 * 抽屉内一层 tab 按 盘前/盘中/盘后 分类（后端判定：交易日历优先，非交易日归盘前）。
 * 新闻不逐条推送；旧 news_min_score 参数只为客户端兼容保留。
 *
 * ⚠️ 2026-09-16（用户需求①）**收口不变，但补回可见性**：
 * `IMP-028` 的收口让「资讯 / 事件」在通知中心**彻底不可见**（实测生产库当日
 * `event_card` 798 条、通知中心 0 条）。现于抽屉**顶层**加一个「资讯 / 事件」tab：
 *  - 它是**浏览面**（只读 `GET /api/events/impact`，与盘面页事件标签同源），
 *    **不是**推送面 —— 铃铛徽标与未读红点**仍只由个股机会驱动**；
 *  - 顶层两个 tab 是两条**正交**的轴：`个股机会`（推送，按时段分页）
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
 *    **不以丢失能力为代价**（同族：`IMP-031` 收敛主按钮时补「全部 N 条」）；
 *  - **无代码的条目**（如消息面）保持行体 → 通用详情弹窗，不静默失败。
 */

/** 顶层模式：推送面（个股机会）↔ 浏览面（资讯 / 事件）。 */
type DrawerMode = "opportunity" | "events";

const MODE_TABS: { key: DrawerMode; label: string; title: string }[] = [
  { key: "opportunity", label: "个股提醒", title: "个股机会与真实持仓风险提醒（按盘前/盘中/盘后分页）" },
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
};

const CATEGORY_LABEL: Record<NotificationItem["category"], string> = {
  opportunity: "个股机会",
  daily_picks: "每日精选",
  news: "消息面",
  risk: "策略风险",
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
function judgmentPayload(item: NotificationItem): DetailPayload {
  return {
    kind: item.category === "news" ? "event" : "generic",
    title: item.title,
    body: item.body,
    symbol: item.symbol,
    source: item.source ?? null,
    date: item.ts,
    meta: [
      { label: "分类", value: item.label },
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
  buy_point: "买点",
  pre_limit: "临板预警",
  flow_surge: "大单异动",
  board_low_absorb: "板块低吸",
  board_flow_surge: "板块资金异动",
  falsify: "方向证伪",
  high_board_break: "高板断裂",
  timeout: "判定超时",
  signal_health: "信号健康",
};

/** 通知中心**只收**这两个形状（与后端 `_NOTIF_KINDS` 同源，勿单侧改动）。
 *  两者都要求带真实代码 ⇒ 它们为 0 时，"空"是上游问题而非筛选问题。 */
const NOTIF_KINDS = ["buy_point", "pre_limit"] as const;

/**
 * 形状计数（2026-09-16 用户实盘反馈）。
 *
 * **为什么必须单独报**：`state` / `decisions` 都来自买点链，只回答"买点链有没有
 * 选出票"；而通知中心实际收**两个**形状。当日实测：`__picks_buy_point__` 规则
 * 整天未触发（`alert_rule` 表里根本没那行，规则是懒创建的），而 `pre_limit`
 * 刷了 121 条 —— 只看 `state` 会读成"上游空"，**把"另一个形状有货"整个漏掉**，
 * 这正是"用户看到机会却没收到通知"的读侧成因。
 */
function ShapeCounts({ shapes }: { shapes: Record<string, number> }) {
  const keys = Object.keys(shapes);
  // 空对象 ≠ 各键为 0：后端在「连规则行都没有」时返回 `{}`（规则懒创建、从未触发），
  // 而"统计过，确实是 0"返回的是 `{buy_point: 0, pre_limit: 0}`。
  // 两者要查的方向不同（查规则注册 / 查扫描调度），**不可合并成同一句话**。
  if (keys.length === 0) {
    return (
      <p
        data-testid="notification-empty-shapes"
        data-stock-level={0}
        className="border-t border-zinc-200 pt-2 text-zinc-600 dark:border-zinc-700 dark:text-zinc-400"
      >
        当前没有两个来源规则的记录，无法从通知列表判断是否出现机会；请核对规则注册与调度。
      </p>
    );
  }
  const stockLevel = NOTIF_KINDS.reduce((n, k) => n + (shapes[k] ?? 0), 0);
  const rest = Object.entries(shapes)
    .filter(([k, v]) => v > 0 && !(NOTIF_KINDS as readonly string[]).includes(k))
    .sort((a, b) => b[1] - a[1]);
  const restTotal = rest.reduce((n, [, v]) => n + v, 0);
  return (
    <div
      data-testid="notification-empty-shapes"
      data-stock-level={stockLevel}
      className="space-y-1 border-t border-zinc-200 pt-2 dark:border-zinc-700"
    >
      <p className="text-zinc-700 dark:text-zinc-300">
        <span className="text-zinc-500 dark:text-zinc-500">
          <strong className="font-medium">个股级</strong>事件（读取窗口内）：
        </span>
        {NOTIF_KINDS.map((k) => `${SHAPE_LABEL[k]} ${shapes[k] ?? 0}`).join(" · ")}
      </p>
      {stockLevel === 0 ? (
        <p className="text-zinc-600 dark:text-zinc-400">
          读取窗口内两种个股级形状均为 0；本次空列表
          <strong className="font-medium">不是被形状筛选挡掉</strong>，请核对临板扫描与买点规则。
        </p>
      ) : (
        <p className="text-zinc-600 dark:text-zinc-400">
          个股级共 {stockLevel} 条却未进列表；可能因
          <strong className="font-medium">缺股票名称</strong>
          等字段校验未通过，须核对原事件。
        </p>
      )}
      {restTotal > 0 && (
        <p className="text-zinc-500 dark:text-zinc-500">
          其余 {restTotal} 条为本就不进通知中心的形状（词义不是买点，如大单异动）：
          {rest
            .slice(0, 4)
            .map(([k, v]) => `${SHAPE_LABEL[k] ?? k} ${v}`)
            .join("、")}
          {rest.length > 4 ? ` 等 ${rest.length} 种` : ""}
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
      <p className="text-zinc-600 dark:text-zinc-400">{plainNote(diag.note)}</p>
      {diag.state !== "unavailable" && <p className="text-[11px] text-zinc-500 dark:text-zinc-500">
        候选 {ps.count} 只 · 最高档 {d.top_tier ?? "无"} · 判定 {d.polls} 拍
        {diag.trade_date ? ` · ${diag.trade_date}` : ""}
        {diag.as_of ? ` · 诊断于 ${diag.as_of.slice(11, 16)}` : ""}
      </p>}
      {/* 形状计数：`state`/`decisions` 只看买点链，答不了"临板预警有没有触发"
          ⇒ 必须并列报出（当日 121 条临板全未进列表就是靠这一对照才定位到的）。 */}
      {diag.shapes && <details className="border-t border-zinc-200 pt-2 text-[11px] dark:border-zinc-700">
        <summary className="cursor-pointer text-zinc-600 dark:text-zinc-400">查看来源核对信息</summary>
        <div className="mt-1"><ShapeCounts shapes={diag.shapes} /></div>
      </details>}
      {reasons.length > 0 && (
        <ul className="space-y-1 border-t border-zinc-200 pt-2 dark:border-zinc-700">
          {reasons.map((r) => (
            <li key={r.reason} className="text-zinc-700 dark:text-zinc-300">
              <span className="text-zinc-500 dark:text-zinc-500">{r.count} 只：</span>
              {r.reason}
              {r.symbols.length > 0 && (
                <span className="text-zinc-500 dark:text-zinc-500">（{r.symbols.join("、")}）</span>
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
              <span className="block break-all text-[10px] text-zinc-500 dark:text-zinc-500">
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

function ChannelStatus({ item }: { item: NotificationItem }) {
  const channels = item.channels;
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
          <span className="shrink-0 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{timeText(item.ts)}</span>
        </div>
        <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">{item.body}</p>
      </button>
      <p className="mt-1 text-[10px] leading-relaxed text-zinc-500 dark:text-zinc-400" data-testid="notification-facts">
        来源：{item.source ?? "历史事件，来源未标注"} · 触发：{item.ts ?? "时间未知"} · {item.validity ?? "条件与时效未记录，须重新核验"}
        <br />站内记录可见；<ChannelStatus item={item} />。未读仅表示站内尚未点开。
      </p>
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
  const [payload, setPayload] = useState<NotificationsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState(false);
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

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const p = await getNotifications();
      setPayload(p);
      setPollError(false);
    } catch (e) {
      setError((e as Error).message || "通知加载失败");
      setPollError(true);
    } finally {
      setLoading(false);
    }
  }, []);

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

  // 打开抽屉即拉取（关闭后由下方 60s 轮询维持未读点新鲜）
  //
  // 下方 void load() 为 **C 类显式豁免**（P1-27）：load 首行是
  // `setLoading(true)/setError(null)` 的**同步** loading 标志——这是"点击即骨架"
  // 的既定契约，规则想防的级联渲染在这里不成立；改成微任务延后会引入一帧
  // 无骨架的闪空，反而更差。
  useEffect(() => {
    if (!open) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [open, load]);

  // 60s 轮询：只刷新 payload，**不再在 effect 里改已读状态**
  // （旧实现打开抽屉就写水位，导致"红点一闪即逝"且水位格式两套，见文件头说明）。
  // 2026-09-11（S2-5）：裸 setInterval → 统一入口（获得可见性暂停）。
  // `marketHours: false` —— 通知含「盘后」时段条目，盘外正是需要及时看到的时候，
  // 不能套用行情类的盘外 ×5 降频（60s 会变 120s）。
  usePollingFetch(
    async () => {
      try {
        const p = await getNotifications();
        setPayload(p);
        setPollError(false);
      } catch {
        // 保留旧列表，同时明确它已不能代表当前状态。
        setPollError(true);
      }
    },
    60_000,
    undefined,
    { marketHours: false }
  );

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
      <button
        onClick={() => setOpen(true)}
        aria-label={pollError ? `打开通知中心（上次读取的未读数 ${unread}）` : unread > 0 ? `打开通知中心（${unread} 条未读）` : "打开通知中心"}
        title={pollError ? "通知刷新失败：未读数依据上次读取结果" : "通知中心：个股机会、持仓风险与资讯浏览；未读仅指站内浏览状态"}
        className="relative rounded-md border border-zinc-200 p-2 text-sm text-zinc-600 transition-colors hover:text-zinc-900 dark:border-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {unread > 0 && (
          <span
            data-testid="notification-badge"
            className="absolute -right-1.5 -top-1.5 min-w-[16px] rounded-full bg-up-deep px-1 text-center text-[10px] font-semibold leading-4 text-white"
            aria-label={`${unread} 条未读通知`}
          >
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open &&
        createPortal(
          <div className="anim-backdrop-in fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-sm" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }} role="dialog" aria-modal="true" aria-label="通知中心" data-testid="notification-drawer">
            <div className="anim-slide-in-right flex h-full w-full max-w-sm flex-col border-l border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-950">
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
                  <button
                    onClick={() => (mode === "opportunity" ? void load() : setEventsRefresh((n) => n + 1))}
                    className="rounded p-1 text-zinc-600 dark:text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                    aria-label="刷新通知"
                    title="重新拉取（页面打开期间每 60s 自动检查新内容）"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" />
                    </svg>
                  </button>
                  <button
                    onClick={() => setOpen(false)}
                    className="rounded p-1 text-zinc-600 dark:text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                    aria-label="关闭"
                  >
                    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                      <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                    </svg>
                  </button>
                </div>
              </div>

              {/* 顶层 tab：个股机会（推送面）/ 资讯·事件（浏览面）。
                  与下方时段 tab 是**两条正交的轴**，故单独一层，不与之合并。 */}
              <div
                className="flex gap-1 border-b border-zinc-100 px-4 py-2 dark:border-zinc-800/80"
                data-testid="notification-mode-tabs"
                role="tablist"
                aria-label="通知中心内容类型"
              >
                {MODE_TABS.map((m) => (
                  <button
                    key={m.key}
                    role="tab"
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
              </div>

              {mode === "events" ? (
                <EventFeed active={mode === "events"} refreshToken={eventsRefresh} />
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
              {/* tab：盘前 / 盘中 / 盘后（红点 = 该时段有未读） */}
              <div className="flex gap-1 border-b border-zinc-100 px-4 py-2 dark:border-zinc-800/80">
                {SESSION_TABS.map((t) => (
                  <button
                    key={t.key}
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
              </div>

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
