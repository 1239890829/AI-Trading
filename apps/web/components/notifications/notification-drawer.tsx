"use client";

import { IconButton, CloseButton } from "@/components/ui/icon-button";
import { HugeiconsIcon } from "@hugeicons/react";
import Notification03Icon from "@hugeicons/core-free-icons/Notification03Icon";

import { SelectionRail } from "@/components/ui/selection-rail";

import { useExitPresence } from "@/hooks/use-exit-presence";
import { useOverlayFocus } from "@/hooks/use-overlay-focus";

import { useCallback, useEffect, useLayoutEffect, useRef, useMemo, useState, useSyncExternalStore, type KeyboardEvent } from "react";
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
import { NewsModal, type NewsModalItem } from "@/components/news-modal";
import { EventFeed } from "@/components/notifications/event-feed";
import { dateTimeTextBJ, dateTimesInTextBJ } from "@/lib/format";
import { NotificationDetail, notificationKeyState, notificationSummary, notificationDeliveryHint } from "./notification-detail";
import { ExplanationMap, NarrativeText } from "@/components/ui/explanation-map";
import { ReadingRow, readingRowClasses } from "@/components/ui/reading-row";

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
 * 阅读落点：行体与详情直接打开这条消息的原依据；行情为独立动作。
 * 原时点、条件、风险、来源与渠道完整保留在同一详情中，列表只给短摘要。
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
  opportunity: "text-up-ink dark:text-up",
  daily_picks: "text-amber-800 dark:text-amber-400",
  news: "text-sky-700 dark:text-sky-300",
  risk: "text-red-700 dark:text-red-300",
  reminder: "text-zinc-700 dark:text-zinc-300",
};

const CATEGORY_LABEL: Record<NotificationItem["category"], string> = {
  opportunity: "个股机会",
  daily_picks: "每日精选",
  news: "消息面",
  risk: "策略风险",
  reminder: "自设提醒",
};

function selectionState(state: string): string {
  return ({completed: "投影完成", pending: "待投影", failed: "投影失败", no_pick_set: "无当日组合记录", no_run: "未见归档记录", unavailable: "读取失败", unknown: "状态未知", no_archive: "未见当日归档", projection_unconfirmed: "投影结果未确认", present: "记录已保存", recorded: "已有原记录", ready: "记录就绪", ran_eligible: "已通过判定"} as Record<string, string>)[state] ?? "状态未归类，请核对原记录";
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
            .map(([k, v]) => `${SHAPE_LABEL[k] ?? k} ${v}`)
            .join("、")}
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
      <NarrativeText text={dateTimesInTextBJ(plainNote(diag.note))} label="通知链诊断说明" identity={`${diag.trade_date}:${diag.as_of}`} />
      {diag.state !== "unavailable" && <p className="text-[11px] text-zinc-600 dark:text-zinc-400">
        候选 {ps.count} 只 · 最高档 {d.top_tier ?? "无"} · 判定 {d.polls} 拍
        {diag.trade_date ? ` · ${diag.trade_date}` : ""}
        {diag.as_of ? ` · 诊断于 ${dateTimeTextBJ(diag.as_of)}` : ""}
      </p>}
      {/* 原入选/买点与其他来源计数单独核对，不能把买点归档摘要当作全部通知链。 */}
      {diag.selection && <div className="space-y-1 border-t border-zinc-200 pt-2 dark:border-zinc-700" data-testid="notification-selection-diagnosis">
        <p>每日入选链：{selectionState(diag.selection.daily.state)} · {diag.selection.daily.count ?? "数量未记录"} 只</p>
        {diag.selection.daily.receipt && <p>消息投影：{selectionState(diag.selection.daily.receipt.state ?? "unknown")} · 新记录 {diag.selection.daily.receipt.created ?? "未记录"} 条</p>}
        <p>盘中入选链：{selectionState(diag.selection.intraday.state)} · 归档候选 {diag.selection.intraday.ranked_count ?? "未记录"} 只 · 原入选消息 {diag.selection.intraday.message_count ?? "未记录"} 条</p>
      </div>}
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
      {(diag.shapes || (d.latest?.length ?? 0) > 0) && <ExplanationMap label="来源与逐股原决定" identity={`${diag.trade_date}:${diag.as_of}`} center={{label:"本次通知诊断",value:NOTIF_STATE_HEADLINE[diag.state] ?? diag.state}} links={[
        ...(diag.shapes ? [{from:"center",to:"shapes",label:"读取窗口来源计数",kind:"context" as const}] : []),
        ...(d.latest ?? []).map(row => ({from:"center",to:`decision-${row.snapshot_id}`,label:"窗口内原判定 · 非全天覆盖",kind:"recorded" as const})),
      ]} steps={[
        ...(diag.shapes ? [{id: "shapes", label: "查看来源核对信息", icon: "source" as const, content: <ShapeCounts shapes={diag.shapes} />}] : []),
        ...(d.latest ?? []).map(row => ({id: `decision-${row.snapshot_id}`, label: `原决定 ${row.name || row.symbol}`, icon: "check" as const, summary: decisionLabels[row.decision] ?? "状态未知", content: <div className="text-zinc-700 dark:text-zinc-300">
              <StockLink symbol={row.symbol} className={`${readingRowClasses.action} rounded px-1 text-sky-700 dark:text-sky-400`}>{row.name || row.symbol} {row.symbol}</StockLink>
              {` · ${decisionLabels[row.decision] ?? "状态未知"} · ${dateTimeTextBJ(row.as_of)} · ${row.reason || "原因未记录"}`}
              <span className="block break-all text-[10px] text-zinc-600 dark:text-zinc-400">
                记录 {row.snapshot_id} · 决定 {row.decision_id || "未记录"} · 版本 {row.decision_version || "未记录"} · 数据 {row.data_state || "未知"}
              </span>
            </div>})),
      ]} />}
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


function NotificationRow({item, unread, onOpen, onNavigate, primaryRef}: {
  item: NotificationItem;
  unread: boolean;
  onOpen: () => void;
  onNavigate: (event: KeyboardEvent<HTMLButtonElement>) => void;
  primaryRef: (button: HTMLButtonElement | null) => void;
}) {
  const summary = notificationSummary(item);
  const shell = `rounded-lg border p-3 transition-colors ${unread
    ? "border-rose-200 bg-rose-50/40 dark:border-rose-500/25 dark:bg-rose-500/5"
    : "border-zinc-100 dark:border-zinc-800/60"}`;
  return <ReadingRow className={shell} data-testid="notification-row">
    <button ref={primaryRef} type="button" data-reading-primary onClick={onOpen} onKeyDown={onNavigate}
      className={`${readingRowClasses.primary} block w-full text-left`}>
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        {unread && <span data-testid="notification-unread-dot" aria-label="未读" title="未读" className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500" />}
        <span className={`rounded px-1.5 py-0.5 font-medium ${CATEGORY_TONE[item.category]}`}>{item.label}</span>
        <time className="ml-auto font-mono tabular-nums text-zinc-600 dark:text-zinc-400">{dateTimeTextBJ(item.ts)}</time>
      </div>
      <p className="mt-2 break-words text-sm font-medium text-zinc-900 dark:text-zinc-50" title={item.title}>{item.title}</p>
      <p data-testid="notification-summary" title={summary.endsWith("…") ? "摘要节选；打开详情查看完整依据与限定。" : undefined}
        className="mt-1 line-clamp-2 break-words text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">依据节选：{summary}</p>
    </button>
    <p data-testid="notification-facts" className="mt-2 break-words text-xs leading-relaxed text-zinc-600 dark:text-zinc-400" title={notificationKeyState(item)}>当时记录 · 当前条件需核对{notificationDeliveryHint(item) ? ` · ${notificationDeliveryHint(item)}` : ""}</p>
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
      <span>{item.symbol || CATEGORY_LABEL[item.category]} · 条件见详情</span>
      <div data-reading-action data-testid="notification-actions" className="ml-auto flex items-stretch gap-1">
        {item.symbol && <StockLink symbol={item.symbol} preferModal
          className={`${readingRowClasses.action} inline-flex min-h-9 items-center px-2 py-1`}
          title={`查看 ${item.symbol} 行情详情`}>行情 ↗</StockLink>}
        <button type="button" data-reading-action data-testid="notification-judgment" title="查看原提醒、完整依据与条件"
          className={`${readingRowClasses.action} min-h-9 rounded px-2 py-1`}
          onClick={event => {event.stopPropagation(); onOpen();}}>详情</button>
      </div>
    </div>
  </ReadingRow>;
}

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const presence = useExitPresence(open ? true : null);
  const drawerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listScroll = useRef(0);
  const rowButtons = useRef(new Map<string, HTMLButtonElement>());
  const returnToRow = useRef<string | null>(null);
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
  const [detailId, setDetailId] = useState<string | null>(null);
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
  const selectedNotice = open && mode === "opportunity" ? activeList.find(item => item.id === detailId) ?? null : null;
  const selectedNoticeId = selectedNotice?.id;
  const detailPresence = useExitPresence(selectedNotice);
  const { shown: shownNotices, visible, sentinelRef, loadMore } = useIncremental(activeList, {
    resetKey: `${tab}/${clearBefore}`,
    active: open && mode === "opportunity" && !selectedNoticeId,
  });
  const selectNotice = (item: NotificationItem) => {
    if (!selectedNoticeId) listScroll.current = listRef.current?.scrollTop ?? 0;
    markOneRead(item); setDetailId(item.id);
  };
  const navigateNotice = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey
      || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    const next = event.key === "Home" ? 0 : event.key === "End" ? shownNotices.length - 1
      : Math.max(0, Math.min(shownNotices.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)));
    const item = shownNotices[next];
    if (item) {rowButtons.current.get(item.id)?.focus(); selectNotice(item);}
  };
  const backToList = () => {returnToRow.current = detailId; setDetailId(null);};
  useLayoutEffect(() => {
    if (!open || mode !== "opportunity" || selectedNoticeId || !returnToRow.current) return;
    if (listRef.current) listRef.current.scrollTop = listScroll.current;
  }, [selectedNoticeId, open, mode]);
  // The original detail overlay releases its focus layer before returning to the row.
  useEffect(() => {
    if (!open || mode !== "opportunity" || selectedNoticeId || !returnToRow.current) return;
    rowButtons.current.get(returnToRow.current)?.focus({preventScroll:true});
    returnToRow.current = null;
  }, [selectedNoticeId, open, mode]);

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
          <div ref={drawerRef} tabIndex={-1} data-motion-state={open ? "open" : "closed"} aria-hidden={!open || undefined} inert={!open} className="motion-overlay fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-sm" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }} role="dialog" aria-modal={open || undefined} aria-label="通知中心" data-testid="notification-drawer">
            <div className="ui-glass-overlay motion-drawer flex h-full w-full max-w-sm flex-col border-l border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-950">
              {/* 头：标题 + 未读数 + 操作 */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-100 px-4 py-3 dark:border-zinc-800/80">
                <h2 className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-sm font-semibold text-zinc-900 dark:text-zinc-50">
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
                <div className="ml-auto flex shrink-0 items-center gap-2">
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
                {mode === "opportunity" && <div className="flex w-full items-center justify-end gap-3">
                  {/* 已读/清除是**推送面**的操作（作用域 = `/api/notifications` 的已读水位）。
                      资讯 tab 下隐藏：那里没有未读概念，按钮点了也没有可见反馈。 */}
                      <button
                        onClick={markAllRead}
                        disabled={unread === 0}
                        data-testid="notification-mark-all-read"
                        className="whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] text-zinc-600 transition-colors hover:bg-zinc-100 hover:text-zinc-700 disabled:opacity-40 disabled:hover:bg-transparent dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                        title="全部标记为已读（未读红点与徽标清零）"
                      >
                        全部已读
                      </button>
                      <button
                        onClick={clearAll}
                        className="whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] text-zinc-600 dark:text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                        title="按时间水位隐藏当前及更早的站内条目；服务端确认后清浏览器缓存不会恢复，原事件审计仍保留"
                      >
                        一键清除
                      </button>
                </div>}
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
                      if (key !== mode) setDetailId(null);
                      setMode(key);
                      event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`[data-testid="notification-mode-${key}"]`)?.focus();
                    }}
                    aria-selected={mode === m.key}
                    data-testid={`notification-mode-${m.key}`}
                    onClick={() => {if (m.key !== mode) setDetailId(null); setMode(m.key);}}
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
              <p className="px-4 pt-2 text-xs text-zinc-600 dark:text-zinc-400" title="入选消息按原观察时间分组，其他消息按记录时间分组；已读状态按记录时间判断。">最近 {payload?.read_window?.limit ?? 50} 条消息{payload?.read_window?.has_more === true ? "，更早记录未包含" : payload?.read_window?.has_more === null ? "，更早记录尚未确认" : ""} · 按提醒时间分组</p>
              {payload?.monitor && ["uncompleted", "unknown"].includes(payload.monitor.state) && <p role="status" data-testid="notification-monitor-state" className="mx-4 mt-2 rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:text-amber-200">
                持仓风险核对{payload.monitor.state === "unknown" ? "尚未完成" : "未完整完成"}{payload.monitor.uncompleted_symbols.length ? `（${payload.monitor.uncompleted_symbols.length} 只）` : ""}：{payload.monitor.reason || "源数据或成本依据待核对"}。不能据此确认风险已排除。
              </p>}
              {/* tab：盘前 / 盘中 / 盘后（红点 = 该时段有未读） */}
              <SelectionRail activeKey={tab} label="通知时段" className="notification-session-tabs">
                {SESSION_TABS.map((t) => (
                  <button
                    key={t.key}
                    aria-pressed={tab === t.key}
                    onClick={() => {setTab(t.key); setDetailId(null);}}
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
              <div ref={listRef} className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3" data-testid="notification-list">
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
                {shownNotices.map((i, index) => (
                  <NotificationRow
                    key={i.id}
                    item={i}
                    unread={isItemUnread(i)}
                    primaryRef={button => {if (button) rowButtons.current.set(i.id, button); else rowButtons.current.delete(i.id);}}
                    onNavigate={event => navigateNotice(event, index)}
                    onOpen={() => selectNotice(i)}
                  />
                ))}
                {/* 哨兵须在滚动容器内部（本 div overflow-y-auto），否则不随滚动移动、只触发一次 */}
                <IncrementalSentinel
                  sentinelRef={sentinelRef}
                  visible={visible}
                  total={activeList.length}
                  unit="条通知"
                  testId="notification-sentinel"
                  onLoadMore={loadMore}
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
                    提醒反映当时情况，请核对当前条件。不构成买卖建议。
                  </>
                ) : (
                  <>
                    资讯供主动浏览，不计入未读。方向由系统规则判断，不构成买卖建议。
                  </>
                )}
              </div>
            </div>
          </div>,
          document.body,
        )}

      {detailPresence.value && <NotificationDetail item={detailPresence.value} open={detailPresence.active} onClose={backToList} onOpenNews={setNewsItem} readError={pollError} />}

      {/* 原文仅在用户明确打开后读取。 */}
      <NewsModal item={newsItem} onClose={() => setNewsItem(null)} />
    </>
  );
}
