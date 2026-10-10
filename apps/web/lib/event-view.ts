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
import type { EventDirectionRow, EventSummary, ImpactEvent } from "@/lib/api";
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

type EventJudgement = Pick<EventSummary, "judge_status" | "judge_status_label" | "judge_reason" | "llm_judged_at" | "llm_aux_note">;
const MUTED_DIRECTION = "text-zinc-600 dark:text-zinc-400";

/** The event state and a target's direction are separate facts. */
export function eventJudgeView(e: EventJudgement): { text: string; cls: string; title: string } {
  const labels = { judged: "已判定", pending: "方向未明", neutral: "未明超时", expired: "已过期", unknown: "状态未知" };
  const status = e.judge_status ?? "unknown";
  const text = status === "unknown" && e.judge_status_label?.includes("复核")
    ? "待复核" : labels[status];
  const fallback = status === "pending"
    ? "当前证据不足，未明确方向；不代表正在后台排队判读。"
    : status === "neutral"
      ? "未明确方向且观察时限已到；不代表已经证实为中性。"
      : status === "expired"
        ? "事件已超过有效时限；原方向仅保留用于查看依据。"
        : status === "judged"
          ? "事件包含已判定方向；不代表每个关联标的都有明确方向。"
          : "当前判定状态不可确认；不要据此推断方向。";
  return {
    text,
    cls: status === "pending" || status === "unknown" ? "text-amber-700 dark:text-amber-300" : MUTED_DIRECTION,
    title: [e.judge_status_label, e.judge_reason, fallback,
      e.llm_judged_at ? `AI辅助曾于 ${e.llm_judged_at} 尝试；不能据此确认当前方向。` : "",
      e.llm_aux_note ? `AI有假设，未应用：${e.llm_aux_note}` : ""].filter(Boolean).join(" ｜ "),
  };
}

/** A zero row only establishes association, including in an otherwise judged event. */
export function eventDirectionView(
  d: Pick<EventDirectionRow, "direction" | "matched_by" | "chain" | "basis">,
  e?: EventJudgement,
): { text: string; cls: string; title: string } {
  const nonzero = d.direction !== 0;
  const hypothesis = nonzero && d.matched_by === "llm_aux";
  const text = d.direction > 0 ? `利好${hypothesis ? "假设" : ""}`
    : d.direction < 0 ? `利空${hypothesis ? "假设" : ""}` : "方向未明";
  const boundary = hypothesis ? "LLM 辅助方向尚未经人工验证，不能作为已判定事实。"
    : !nonzero ? "仅确认关联，当前证据不足以明确该标的方向。" : "";
  return {
    text,
    cls: hypothesis ? "text-amber-700 dark:text-amber-300"
      : d.direction > 0 ? "text-up-ink dark:text-up" : d.direction < 0 ? "text-down-ink dark:text-down" : MUTED_DIRECTION,
    title: [d.chain, d.basis, boundary, e ? eventJudgeView(e).title : ""].filter(Boolean).join(" ｜ "),
  };
}

export function eventJudgementExplanation(e: EventSummary): string {
  const hypothesis = e.directions.some(d => d.matched_by === "llm_aux" && d.direction !== 0);
  return [eventJudgeView(e).title, hypothesis ? "LLM 辅助方向尚未经人工验证，不能作为已判定事实。" : ""].filter(Boolean).join("\n");
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
    evidence: `列表解释版本 ${eventInterpretationText(e)} · ${eventJudgeView(e).text}${e.directions.some(d => d.matched_by === "llm_aux") ? " · 模型假设未验证" : ""}${e.llm_aux_note ? " · AI有假设，未应用" : ""}`,
    judgement: eventJudgementExplanation(e),
  };
}

/**
 * 事件 ⇒ 通用详情弹窗载荷。保留原文链接、方向与判定边界。
 *
 * 保留 `eventId` 身份。方向、状态与依据来自同一次列表读取，
 * 详情展示该快照，不伪造尚未拉取的更新结果。
 */
export function eventDetailPayload(e: EventSummary): DetailPayload {
  const d = e.directions?.[0];
  return {
    kind: "event",
    title: e.title,
    url: e.url ?? null,
    eventId: e.id,
    symbol: d?.target_type === "symbol" ? d.target : null,
    theme: d?.target_type === "theme" ? d.target : null,
    source: e.source ?? null,
    date: e.published_at ?? null,
    meta: [
      { label: "列表解释版本", value: eventInterpretationText(e) },
      { label: "判定状态", value: eventJudgeView(e).text },
      { label: "来源级别", value: `${e.source_tier}/5 · ${e.certainty}` },
      ...(d?.target ? [{ label: d.target_type === "symbol" ? "关联个股" : "关联题材", value: d.target }] : []),
      ...(d
        ? [{ label: "方向", value: eventDirectionView(d, e).text }]
        : []),
      ...(d?.basis ? [{ label: "依据", value: d.basis }] : []),
      ...(d?.chain ? [{ label: "传导链", value: d.chain }] : []),
    ],
    body: [e.summary, `判定说明：${eventJudgementExplanation(e)}`].filter(Boolean).join("\n\n"),
  };
}
