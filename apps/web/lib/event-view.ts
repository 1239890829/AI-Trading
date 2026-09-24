/**
 * 事件条目的**共用展示口径**（2026-09-16）。
 *
 * 背景：通知中心新增「资讯 / 事件」tab 后，同一份 `ImpactEvent` 会在**两个入口**
 * 渲染 —— 盘面页「事件影响力」标签（`components/market/events-tab.tsx`）与
 * 通知中心抽屉（`components/notifications/notification-drawer.tsx`）。
 *
 * 若不抽出本模块，就会各自维护一份「四级分类配色 / 影响力配色 / 点击落点」，
 * 两处一旦漂移，同一个事件从两个入口点开看到的东西不一样 —— 这正是 `IMP-033`
 * 已经踩过一次的形态（同族纪律：**同一份判读不得有两份拼装**）。
 *
 * 因此这里只放**纯函数与常量**（无 React / 无 IO）：
 *  - 配色表：`FOUR_STYLE` / `LEVEL_STYLE` / `TAG_STYLE`；
 *  - 落点构造：`eventNewsItem()`（有 url → 全文弹窗）、`eventDetailPayload()`
 *    （无 url → 通用详情弹窗，带事件 id 以取方向行与判定状态）。
 *
 * ⚠️ 抽屉里是**窄栏**（max-w-sm），因此点击落点的构造与盘面页**必须一致**，
 * 但版式由各自决定 —— 本模块刻意不提供 JSX。
 */
import type { EventSummary, ImpactEvent } from "@/lib/api";
import type { NewsModalItem } from "@/components/news-modal";
import type { DetailPayload } from "@/components/detail/detail-modal";

/** 四级分类配色（国际时事 / 国家政策 / 市场热点 / 原材料涨价） */
export const FOUR_STYLE: Record<string, string> = {
  international: "bg-sky-500/15 text-sky-700 border-sky-500/40 dark:text-sky-300",
  policy: "bg-amber-500/15 text-amber-800 border-amber-500/40 dark:text-amber-300",
  hot: "bg-zinc-500/15 text-zinc-700 border-zinc-500/40 dark:text-zinc-300",
  material: "bg-teal-500/15 text-teal-700 border-teal-500/40 dark:text-teal-300",
};

/**
 * 三级影响力配色。
 *
 * ⚠️ `L3` **必须有键**（2026-09-16 跨端契约登记时补）：
 * 后端 `impact_level` 的全集是 `L1/L2/L3`，默认只是**过滤**掉 L3 不发
 * （`/api/events/impact` 的 `include_l3` 默认 False），并非分级定义里没有 L3。
 * 前端此前缺 L3 键 ⇒ 一旦有人开启 `include_l3`，L3 徽标会渲染成**无色空样式**
 * （不是报错，是静默降级），而 `LEVEL_REASON` / `counts` 侧都认 L3。
 * 该缺口由 `tests/test_cross_end_contract.py` 的「覆盖性」判据抓出。
 */
export const LEVEL_STYLE: Record<string, string> = {
  L1: "bg-up/20 text-up-ink dark:text-up border-up/50",
  L2: "bg-zinc-500/15 text-zinc-600 border-zinc-500/40 dark:text-zinc-300",
  L3: "bg-zinc-500/10 text-zinc-500 border-zinc-500/30 dark:text-zinc-400",
};

export const TAG_STYLE =
  "bg-indigo-500/10 text-indigo-700 border-indigo-500/30 dark:text-indigo-300";

/** 影响力级别的悬浮说明（与后端 `impact_level` 判据同义，供 title 使用） */
export function levelTitle(level: string): string {
  if (level === "L1") return "L1 必上：政策/行业级/国际重大";
  if (level === "L2") return "L2 选上：事实类+有标的链";
  return "L3 不上：日常经营/人事变动";
}

/** 列表读取时的解释身份；旧数据不能猜测其历史版本。 */
export function eventInterpretationText(e: EventSummary): string {
  const ref = e.interpretation_ref;
  if (!ref?.version_id) return "历史解释版本未知";
  const state = { active: "生效", pending: "待复核", withdrawn: "已撤回", unknown: "未知" }[ref.state];
  return `#${ref.version_id} · ${state} · ${ref.available_at ?? "时间未知"}`;
}

/**
 * 有原文链接 ⇒ 全文弹窗载荷。
 *
 * `kindLabel` 用「快讯」与盘面页一致；`digest` 传 `summary` 让正文抓取失败时
 * 仍有内容可读（而不是一个空白弹窗）。
 */
export function eventNewsItem(e: ImpactEvent): NewsModalItem {
  return {
    title: e.title,
    url: e.url!,
    date: e.published_at ?? null,
    source: e.source ?? null,
    kindLabel: "快讯",
    digest: e.summary ?? null,
    evidence: `列表解释版本 ${eventInterpretationText(e)}`,
  };
}

/**
 * 无原文链接 ⇒ 通用详情弹窗载荷。
 *
 * 传 `eventId` 而不是仅 `kind: "event"`：详情弹窗据此拉取该事件的方向行与
 * 判定状态（`getEventStocks` / `getEventsForSymbol` 同族），
 * 只给标题会退化成"点开什么也没有"。
 */
export function eventDetailPayload(e: ImpactEvent): DetailPayload {
  const d = e.directions?.[0];
  return {
    kind: "event",
    title: e.title,
    url: null,
    eventId: e.id,
    theme: d?.target ?? null,
    source: e.source ?? null,
    date: e.published_at ?? null,
    meta: [
      { label: "列表解释版本", value: eventInterpretationText(e) },
      ...(e.judge_status_label ? [{ label: "判定", value: e.judge_status_label }] : []),
      ...(d
        ? [{ label: "方向", value: d.direction > 0 ? "利好" : d.direction < 0 ? "利空" : "待判" }]
        : []),
      ...(d?.basis ? [{ label: "依据", value: d.basis }] : []),
      ...(d?.chain ? [{ label: "传导链", value: d.chain }] : []),
    ],
    body: e.summary ?? e.judge_reason ?? null,
  };
}
