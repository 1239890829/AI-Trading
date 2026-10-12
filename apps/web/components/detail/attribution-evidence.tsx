"use client";

import {memo, useId, useState} from "react";
import {useResource} from "@/hooks/use-resource";
import {getEventAttribution, getStockAttribution, type AttributionEvidenceItem, type AttributionSourceContext, type EventInterpretationRef} from "@/lib/api";
import {bjDate, eventTimeText, sourceLabel, timeTextBJ} from "@/lib/format";
import {Skeleton} from "@/components/ui/loading";

function clock(value: string | null | undefined): number | null {
  if (!value || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const result = Date.parse(value);
  return Number.isFinite(result) ? result : null;
}

function sourceUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : null;} catch {return null;}
}

function timeLabel(value: string | null | undefined): string {
  return clock(value) !== null ? `${bjDate(value!)} ${timeTextBJ(value!)}` : eventTimeText(value) || "未记录";
}

function SourceExcerpt({context, label}: {context: AttributionSourceContext | null; label: string}) {
  const href = sourceUrl(context?.url);
  return <div className="min-w-0 space-y-1">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-medium">{label}</h4>{href && <a data-action="quiet" href={href} target="_blank" rel="noopener noreferrer" className="quiet-action shrink-0" aria-label={`${label}原文`}>查看原文</a>}</div>
    {context ? <blockquote className="space-y-1 whitespace-pre-wrap [overflow-wrap:anywhere] text-zinc-700 dark:text-zinc-300"><p>{context.title}</p>{context.summary && <p>{context.summary}</p>}</blockquote> : <p className="text-zinc-600 dark:text-zinc-400">原始内容未记录，不能核对完整语境。</p>}
    {context && !context.summary && <p className="text-zinc-600 dark:text-zinc-400">仅保存标题；正文条件尚未核对。</p>}
    {!href && <p className="text-zinc-600 dark:text-zinc-400">原文链接未记录。</p>}
  </div>;
}

/** The same explanation vocabulary serves source events, stock details and selection drawers. */
export function AttributionEvidence({item, selectedAt, recordedRef, selectionContext = false}: {
  item: AttributionEvidenceItem; selectedAt?: string | null; recordedRef?: EventInterpretationRef; selectionContext?: boolean;
}) {
  const available = clock(item.timing.available_at);
  const effective = clock(item.timing.interpretation_effective_at);
  const selected = clock(selectedAt);
  const late = available !== null && selected !== null && available > selected;
  const lateInterpretation = effective !== null && selected !== null && effective > selected;
  const revised = !!recordedRef?.version_id && recordedRef.version_id !== item.interpretation_ref.version_id;
  const version = item.interpretation_ref.version_id;
  const publisher = item.source.original_publisher;
  const relation = item.stock_relation;
  const observation = item.source_context?.observation_id;
  const hasSourceIdentity = typeof observation === "number" && Number.isInteger(observation) && observation > 0;
  const excerptLabel = !item.source_context ? "来源内容未记录" : !hasSourceIdentity ? "旧卡片摘录 · 来源观察身份未记录" : observation === item.interpretation_ref.observation_id ? "解释版本绑定的来源内容" : "来源观察内容 · 解释绑定待核";
  const stageUnknown = !["done", "proposed", "rumor"].some(value => value === item.certainty);
  return <article className="min-w-0 space-y-2 border-b border-zinc-200 py-3 text-xs leading-relaxed last:border-b-0 dark:border-zinc-700/60" aria-label={`事件归因 ${item.event_id}`}>
    <div className="flex flex-wrap items-start justify-between gap-2"><h3 className="min-w-0 flex-1 font-medium [overflow-wrap:anywhere] text-zinc-900 dark:text-zinc-100">{item.title}</h3><span className="shrink-0 text-zinc-600 dark:text-zinc-400">{item.state_label} · {version ? `解释 #${version}` : "版本未知"}</span></div>
    {!!item.dimensions.length && <p className="text-zinc-600 dark:text-zinc-400" title="来源内容中的线索维度；规则识别不证明各维度的涨停贡献">{item.dimensions.map(value => value.label).join(" · ")}</p>}
    <dl className="grid grid-cols-1 gap-x-5 gap-y-1 min-[480px]:grid-cols-3">
      <div className="min-w-0"><dt className="text-zinc-600 dark:text-zinc-400">消息证据</dt><dd className="[overflow-wrap:anywhere]" title={item.fact_support.note}>{item.fact_support.label}</dd></div>
      <div className="min-w-0"><dt className="text-zinc-600 dark:text-zinc-400">个股关联</dt><dd className="[overflow-wrap:anywhere]">{relation.label}</dd></div>
      <div className="min-w-0"><dt className="text-zinc-600 dark:text-zinc-400">市场响应</dt><dd className="[overflow-wrap:anywhere]">{item.market_response.status_label}</dd></div>
    </dl>
    {relation.basis && <p className="[overflow-wrap:anywhere]"><span className="text-zinc-600 dark:text-zinc-400">关联依据：</span>{relation.basis}</p>}
    {relation.chain && <p className="[overflow-wrap:anywhere]"><span className="text-zinc-600 dark:text-zinc-400">传导线索：</span>{relation.chain}</p>}
    {!!item.qualifiers.length && <p className="[overflow-wrap:anywhere] text-amber-800 dark:text-amber-300"><span className="font-medium">关键条件：</span>{item.qualifiers.map(value => `${value.label}${value.matched_terms.length ? `（${value.matched_terms.join("、")}）` : ""}`).join("；")}</p>}
    {stageUnknown && <p role="note" className="text-amber-800 dark:text-amber-300">事件阶段尚未核实，不能按已落地事件解读。</p>}
    {(late || lateInterpretation || revised) && <p role="note" className="text-amber-800 dark:text-amber-300">{late ? "消息可用时间晚于首次加入，仅供后续复盘。" : lateInterpretation ? "当前解释形成晚于首次加入，仅供后续复盘。" : selectionContext ? "当前解释与选股记录引用的原版本不同。" : "当前解释与列表入口引用的版本不同。"}{selectionContext || selectedAt ? "不回填当时入选依据。" : "列表快照保留原记录。"}</p>}
    {selectedAt && (selected === null || available === null || effective === null) && <p className="text-zinc-600 dark:text-zinc-400">时点证据不完整，无法确认这条解释在首次加入时是否可用。</p>}
    <details className="min-w-0">
      <summary className="w-fit cursor-pointer rounded py-1 font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2">核对原文与时点</summary>
      <div className="mt-2 space-y-3">
        <p className="text-zinc-600 dark:text-zinc-400">{item.fact_support.note} {item.market_response.basis}。市场响应不证明消息属实或涨停因果。</p>
        <SourceExcerpt context={item.source_context} label={excerptLabel} />
        {item.pending_context && <SourceExcerpt context={item.pending_context} label="来源已修订，待复核内容" />}
        {item.withdrawal_context && <SourceExcerpt context={{...item.withdrawal_context, change_kind: "withdrawal"}} label="撤回依据" />}
        {item.review_note && <p className="[overflow-wrap:anywhere] text-zinc-600 dark:text-zinc-400">复核记录：{item.review_note}</p>}
        <dl className="space-y-1 [overflow-wrap:anywhere] text-zinc-600 dark:text-zinc-400">
          <div><dt className="inline">原始发布者：</dt><dd className="inline">{publisher || "未记录"} · 采集 {item.source.collector ? sourceLabel(item.source.collector) : "未记录"} · {item.source.source_tier_label}</dd></div>
          <div><dt className="inline">来源发布时间：</dt><dd className="inline">{item.timing.source_time_known ? timeLabel(item.timing.source_published_at) : "未知，未用收录时间补填"}</dd></div>
          <div><dt className="inline">系统收到：</dt><dd className="inline">{timeLabel(item.timing.received_at)}</dd></div>
          <div><dt className="inline">消息可用：</dt><dd className="inline">{timeLabel(item.timing.available_at)}</dd></div>
          <div><dt className="inline">解释生效：</dt><dd className="inline">{timeLabel(item.timing.interpretation_effective_at)}</dd></div>
          <div><dt className="inline">来源记录：</dt><dd className="inline">{item.interpretation_ref.observation_id ? `#${item.interpretation_ref.observation_id}` : "未知"}</dd></div>
        </dl>
        {!!item.alternatives.length && <div><h4 className="font-medium">其他解释与待核线索</h4><ul className="mt-1 list-disc space-y-1 pl-4 [overflow-wrap:anywhere]">{item.alternatives.map(value => <li key={value}>{value}</li>)}</ul></div>}
        {!!item.limitations.length && <ul className="space-y-1 [overflow-wrap:anywhere] text-zinc-600 dark:text-zinc-400">{item.limitations.map(value => <li key={value}>{value}</li>)}</ul>}
      </div>
    </details>
  </article>;
}

/** No polling or per-candidate reads: only an open detail requests current evidence. */
export const StockAttributionPanel = memo(function StockAttributionPanel({symbol, eventId, active = true, title = "驱动线索", defaultExpanded = false, bounded = true, selectedAt, recordedRefs, selectionContext = false}: {
  symbol?: string | null; eventId?: number | null; active?: boolean; title?: string; defaultExpanded?: boolean; bounded?: boolean;
  selectedAt?: string | null; recordedRefs?: EventInterpretationRef[];
  selectionContext?: boolean;
}) {
  const [open, setOpen] = useState(defaultExpanded);
  const id = useId();
  const identity = `${symbol || ""}:${eventId || ""}`;
  const resource = useResource(() => eventId ? getEventAttribution(eventId, symbol) : getStockAttribution(symbol!), {key: identity, enabled: active && open && (!!eventId || /^\d{6}$/.test(symbol || "")), intervalMs: null, marketHours: false});
  const data = resource.data;
  return <section className="min-w-0 py-1 text-xs" aria-label={title}>
    <div className="flex min-h-8 items-center justify-between gap-2"><button type="button" data-action="quiet" className="quiet-action min-w-0 text-left" aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}><span>{title}</span><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={`ml-1 transition-transform duration-150 motion-reduce:transition-none ${open ? "rotate-180" : ""}`}><path d="m6 9 6 6 6-6" /></svg></button>{open && <button type="button" data-action="quiet" className="quiet-action shrink-0" disabled={resource.pending} onClick={resource.refresh}>{resource.pending && data ? "更新中" : "刷新依据"}</button>}</div>
    {open && <div id={id} className={`min-w-0 ${bounded ? "data-scroll max-h-72 overflow-y-auto pr-1" : ""}`}>
      <p className="py-1 leading-relaxed text-zinc-600 dark:text-zinc-400">最新已采集事件解读；不代表已核实涨停因果，不改写首次入选记录。不构成买卖建议。</p>
      {!!recordedRefs?.length && <details className="py-1 text-zinc-600 dark:text-zinc-400"><summary className="w-fit cursor-pointer">{selectionContext ? "选股记录事件引用" : "列表入口事件引用"}</summary><ul className="mt-1 space-y-1 [overflow-wrap:anywhere]">{recordedRefs.map((ref, index) => <li key={`${ref.event_id}:${ref.version_id}:${index}`}>事件 #{ref.event_id} · {ref.version_id ? `原解释 #${ref.version_id}` : "原版本未知"} · {timeLabel(ref.available_at)}</li>)}</ul></details>}
      {resource.pending && !data && <div role="status" aria-label="归因依据加载中" className="space-y-2 py-3"><Skeleton className="h-3 w-3/4" /><Skeleton className="h-3 w-full" /><span className="sr-only">归因依据加载中</span></div>}
      {resource.error != null && <p role="alert" className="py-2 leading-relaxed text-amber-800 dark:text-amber-300">{data ? "依据刷新失败，保留上次读取内容。" : "归因依据读取失败，尚不能确认是否有相关消息。"}可使用上方“刷新依据”重试。</p>}
      {data?.items.length === 0 && <p className="py-3 leading-relaxed text-zinc-600 dark:text-zinc-400">已采集资料中暂未找到关联事件。覆盖不等于全市场，不能据此认定没有驱动消息。</p>}
      {data?.items.map(item => <AttributionEvidence key={item.event_id} item={item} selectedAt={selectedAt} recordedRef={recordedRefs?.find(ref => ref.event_id === item.event_id)} selectionContext={selectionContext} />)}
    </div>}
  </section>;
});
