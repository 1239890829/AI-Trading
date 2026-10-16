"use client";

import type {NotificationItem} from "@/lib/api";
import {dateTimeTextBJ, dateTimesInTextBJ, formatLegacyLimitDistance, sourceLabel} from "@/lib/format";
import {workbenchUrl} from "@/lib/routing";
import {ModalShell} from "@/components/ui/modal-shell";
import {ReaderBrief, ResearchDisclosure} from "@/components/ui/reader-brief";
import {readerExcerpt, readerCautions} from "@/lib/reader-copy";
import {ConditionList} from "@/components/ui/explanation-map";
import {readingRowClasses} from "@/components/ui/reading-row";
import {inspectionClick, useInspection} from "@/components/inspection/inspection-context";
import {symbolDetailClick, useSymbolDetail} from "@/components/detail/symbol-detail-context";
import {ChannelStatus} from "./channel-status";
import type {NewsModalItem} from "@/components/news-modal";

const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown) => typeof value === "string" ? value.trim() : "";
const visibleProse = (value: string) => dateTimesInTextBJ(formatLegacyLimitDistance(value));
const clock = (value: string | null | undefined, fallback: string) => value ? dateTimeTextBJ(value, {seconds:true}) === "--" ? fallback : dateTimeTextBJ(value, {seconds:true}) : fallback;

/** A literal excerpt of the stored reason. Never split at a semicolon or infer buyability. */
export function notificationSummary(item: NotificationItem): string {
  const evidence = (item.references ?? []).map(ref => object(ref.evidence));
  const stored = evidence.map(value => text(value.pick_basis) || text(value.source_basis)).find(Boolean);
  const reason = visibleProse(stored || item.body || "原提醒依据未提供，请打开详情核对。").replace(/\s+/g, " ").trim();
  return readerExcerpt(reason);
}

export function notificationKeyState(item: NotificationItem): string {
  const observation = item.category === "risk" ? "风险时点记录 · 当前持仓需核对"
    : item.category === "reminder" ? "条件触发记录 · 当前条件需核对"
    : "原时点记录 · 当前条件需核对";
  const states = item.channels;
  if (!states) return `${observation} · 渠道状态未知`;
  const ordered = ["permanent_failed", "unknown", "expired", "suppressed", "pending", "leased", "accepted"];
  const state = ordered.find(state => states.some(channel => channel.state === state));
  const labels: Record<string, string> = {permanent_failed:"渠道发送失败", unknown:"渠道结果未知", expired:"外部提醒已过期", suppressed:"外部提醒已静默", pending:"外部提醒待处理", leased:"外部提醒处理中", accepted:"渠道已受理，未确认送达"};
  return `${observation} · ${state ? labels[state] : states.length ? "渠道状态未知" : "仅站内记录"}`;
}

export function notificationDeliveryHint(item: NotificationItem): string {
  const labels: Record<string, string> = {permanent_failed:"外部发送失败", unknown:"外部结果待核",
    expired:"外部已过期", suppressed:"外部已静默", pending:"外部待发送", leased:"外部处理中", accepted:"外部待确认"};
  const state = Object.keys(labels).find(key => item.channels?.some(channel => channel.state === key));
  return state ? labels[state] : "";
}

function evidenceLines(evidence: Record<string, unknown>): string[] {
  const lines: string[] = [];
  const labels: Record<string, string> = {tech:"技术", news:"消息", fundamental:"基本", capital:"资金", sentiment:"情绪", echelon:"梯队"};
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
  if (audit.source || audit.data_timestamp) lines.push(`原报价：${sourceLabel(text(audit.source))} · ${clock(text(audit.data_timestamp), "源时点未知")} · 质量 ${audit.quality ?? "未知"}`);
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
  return lines.length ? lines.map(visibleProse) : ["原结构化依据未提供；不使用最新行情回填。"];
}

/** One reading surface: all supplied facts are visible without a second evidence menu. */
export function NotificationDetail({item, open, onClose, onOpenNews}: {item: NotificationItem; open: boolean; onClose: () => void; onOpenNews: (item: NewsModalItem) => void}) {
  const {open: inspect} = useInspection();
  const {open: openSymbol} = useSymbolDetail();
  const actionClass = `${readingRowClasses.action} quiet-action inline-flex min-h-9 items-center rounded px-2 py-1`;
  return <ModalShell open={open} onClose={onClose} presentation="drawer" zIndex={60} label={item.title} testid="notification-detail" size="md"
    header={<div className="min-w-0"><h3 className="break-words text-sm font-semibold">{item.title}</h3><p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{item.label} · {dateTimeTextBJ(item.ts)}</p></div>}
    bodyClassName="overflow-y-auto px-4 py-4"
    footer={<p className="text-xs text-zinc-600 dark:text-zinc-400">不构成买卖建议。</p>}>
    <div className="space-y-5 break-words text-sm leading-relaxed [overflow-wrap:anywhere]">
      <ReaderBrief label="提醒简述" headline={notificationSummary(item)} facts={[
        {label:"这条提醒",value:item.category === "risk" ? "提醒你核对风险" : item.category === "reminder" ? "你设置的条件曾触发" : "选股系统曾把它列入观察"},
        {label:"发生时间",value:clock(item.source_as_of ?? item.ts, "原时点未记录")},
      ]} cautions={[...readerCautions([item.body, item.validity || ""]), "这是当时的记录，不代表现在可以买入"]}/>
      <ResearchDisclosure identity={`${item.id}:${JSON.stringify(item)}`} label="完整依据与研究记录">
      <section aria-label="原提醒内容"><h4 className="mb-1 font-medium">提醒依据</h4><p className="whitespace-pre-wrap">{visibleProse(item.body) || "原提醒内容未提供。"}</p></section>
      <section aria-label="来源与时点" className="space-y-1 text-xs text-zinc-600 dark:text-zinc-400">
        <p>来源：{sourceLabel(item.source) === "--" ? "历史事件，来源未标注" : sourceLabel(item.source)}</p>
        <p>原观察：{clock(item.source_as_of ?? item.ts, "时间未知")}</p>
        <p>记录：{clock(item.recorded_at, "历史记录钟未提供")}</p>
        {item.score != null && <p>原事件评分：{item.score}（不是胜率）</p>}
      </section>
      <ConditionList label="条件与风险" items={[visibleProse(item.validity || "条件与时效未记录，须重新核验")]} tone="warning" />
      {(item.references ?? []).map(ref => <section key={ref.event_id} aria-label={`${ref.kind === "selection" ? "原入选" : "原判定"} #${ref.event_id}`} className="space-y-2 border-t border-zinc-200 pt-4 dark:border-zinc-800">
        <h4 className="font-medium">{ref.kind === "selection" ? "原入选" : "原判定"} #{ref.event_id}</h4>
        <p className="text-xs text-zinc-600 dark:text-zinc-400">原观察：{clock(ref.source_as_of, "原时点未记录")} · 记录：{clock(ref.recorded_at, "记录时点未提供")}</p>
        <ConditionList label="原依据与条件" items={evidenceLines(object(ref.evidence))} />
        <p className="text-xs text-zinc-600 dark:text-zinc-400">来源身份：{ref.source_id ?? "未记录"} · 版本：{ref.source_version ?? "未绑定"}{ref.run_id ? ` · 归档 ${ref.run_id}` : ""}</p>
        {ref.selection_source === "daily" && ref.source_version && ref.trade_date && <a data-reading-action className={actionClass} href={`/hunting?${new URLSearchParams({view:"review", date:ref.trade_date, version:ref.source_version})}`} onClick={event => {inspectionClick(inspect, {kind:"selection-review", date:ref.trade_date!, version:ref.source_version!})(event); if (event.defaultPrevented) onClose();}}>查看此版本复盘</a>}
      </section>)}
      <section aria-label="通知渠道" className="text-xs text-zinc-600 dark:text-zinc-400"><h4 className="mb-1 font-medium">渠道状态</h4><p>未读仅表示站内尚未点开；渠道受理不等于送达。</p><p>站内记录可见；<ChannelStatus channels={item.channels} />。</p></section>
      </ResearchDisclosure>
      <div className="flex flex-wrap gap-2">
        {item.symbol && <a data-reading-action className={actionClass} href={workbenchUrl(item.symbol)} onClick={event => {symbolDetailClick(openSymbol, {symbol:item.symbol!, preferModal:true})(event); if (event.defaultPrevented) onClose();}}>查看行情</a>}
        {item.symbol && <a data-reading-action className={actionClass} href="/hunting?view=discover&sec=candidates" onClick={event => {inspectionClick(inspect, {kind:"selection-preview", section:"candidates"})(event); if (event.defaultPrevented) onClose();}}>查看当前选股条件</a>}
        {item.url?.startsWith("http") && <button type="button" data-reading-action className={actionClass} onClick={event => {event.stopPropagation(); onClose(); onOpenNews({title:item.title, url:item.url!, date:item.ts, source:item.source ?? null, kindLabel:item.label});}}>查看原文</button>}
      </div>
    </div>
  </ModalShell>;
}
