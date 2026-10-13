"use client";

import {memo, useId, useLayoutEffect, useRef, useState} from "react";
import {useResource} from "@/hooks/use-resource";
import {useExitPresence} from "@/hooks/use-exit-presence";
import {getEventAttribution, getStockAttribution, type AttributionEvidenceItem, type AttributionSourceContext, type EventInterpretationRef} from "@/lib/api";
import {bjDate, eventTimeText, sourceLabel, timeTextBJ} from "@/lib/format";
import {Skeleton} from "@/components/ui/loading";
import styles from "./attribution-evidence.module.css";
import {ConditionList, ExplanationMap, FactTimeline, NarrativeText} from "@/components/ui/explanation-map";

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

/** Stay inside known local readers; never move the page or document viewport. */
function readingViewport(element: HTMLElement | null): HTMLElement | null {
  let ancestor = element?.parentElement;
  while (ancestor && ancestor !== document.body && ancestor !== document.documentElement) {
    if (ancestor.hasAttribute("data-attribution-reader")) return ancestor;
    if (!ancestor.matches(".task-scroll, .workspace-content") && ancestor.matches(".data-scroll, .overflow-y-auto, .overflow-auto, .stock-context-brief, .stock-workspace, .task-panel-body")) {
      const style = getComputedStyle(ancestor);
      if (/^(auto|scroll)$/.test(style.overflowY || style.overflow)) return ancestor;
    }
    if (ancestor.matches('[role="dialog"]')) return null;
    ancestor = ancestor.parentElement;
  }
  return null;
}

function SourceExcerpt({context, label}: {context: AttributionSourceContext | null; label: string}) {
  const href = sourceUrl(context?.url);
  return <div className="min-w-0 space-y-1">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="min-w-0 font-medium [overflow-wrap:anywhere]">{label}</h4>{href && <a data-action="quiet" href={href} target="_blank" rel="noopener noreferrer" className={`quiet-action shrink-0 ${styles.action}`} aria-label={`${label}原文`}>查看原文</a>}</div>
    {context ? <blockquote className="text-zinc-700 dark:text-zinc-300"><p>{context.title}</p>{context.summary && <NarrativeText text={context.summary} label={label} identity={`${context.observation_id ?? "legacy"}:${context.title}`}/>}</blockquote> : <p className="text-zinc-600 dark:text-zinc-400">原始内容未记录，不能核对完整语境。</p>}
    {context && !context.summary && <p className="text-zinc-600 dark:text-zinc-400">仅保存标题；正文条件尚未核对。</p>}
    {!href && <p className="text-zinc-600 dark:text-zinc-400">原文链接未记录。</p>}
  </div>;
}

/** The same explanation vocabulary serves source events, stock details and selection drawers. */
export function AttributionEvidence({item, selectedAt, recordedRef, selectionContext = false}: {
  item: AttributionEvidenceItem; selectedAt?: string | null; recordedRef?: EventInterpretationRef; selectionContext?: boolean; defaultSourceExpanded?: boolean;
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
  const qualifiers = item.qualifiers.map(value => `${value.label}${value.matched_terms.length ? `（${value.matched_terms.join("、")}）` : ""}`);
  return <article className="min-w-0 space-y-4 py-3 text-xs leading-relaxed" aria-label={`事件归因 ${item.event_id}`}>
    <header className="space-y-2"><h3 className="min-w-0 text-sm font-semibold [overflow-wrap:anywhere] text-zinc-900 dark:text-zinc-100">{item.title}</h3><p className="text-zinc-600 [overflow-wrap:anywhere] dark:text-zinc-400">{item.state_label} · {version ? `解释 #${version}` : "版本未知"}</p>
      {!!item.dimensions.length && <ul className="flex flex-wrap gap-2" aria-label="来源线索维度">{item.dimensions.map(value => <li key={value.label} className="rounded bg-zinc-100 px-2 py-1 dark:bg-zinc-800">{value.label}</li>)}</ul>}
    </header>
    <p className="text-zinc-600 dark:text-zinc-400">来源收录不等于消息属实；关联与方向仍须核对。</p>
    <ConditionList label="关键条件" items={qualifiers} tone="warning" /><ConditionList label="解读边界" items={item.limitations} tone="warning" />
    {stageUnknown && <p role="note" className="text-amber-800 dark:text-amber-300">事件阶段尚未核实，不能按已落地事件解读。</p>}
    {(late || lateInterpretation || revised) && <p role="note" className="text-amber-800 dark:text-amber-300">{late ? "消息可用时间晚于首次加入，仅供后续复盘。" : lateInterpretation ? "当前解释形成晚于首次加入，仅供后续复盘。" : selectionContext ? "当前解释与选股记录引用的原版本不同。" : "当前解释与列表入口引用的版本不同。"}{selectionContext || selectedAt ? "不回填当时入选依据。" : "列表快照保留原记录。"}</p>}
    {selectedAt && (selected === null || available === null || effective === null) && <p className="text-zinc-600 dark:text-zinc-400">时点证据不完整，无法确认这条解释在首次加入时是否可用。</p>}
    <ExplanationMap label="归因资料关系" identity={`${item.event_id}:${version ?? "unknown"}:${observation ?? "legacy"}`} steps={[
      {id: "source", label: "消息证据", icon: "source", summary: item.fact_support.label, content: <div className="space-y-4"><p>{item.fact_support.note}</p><SourceExcerpt context={item.source_context} label={excerptLabel} />{item.pending_context && <SourceExcerpt context={item.pending_context} label="来源已修订，待复核内容" />}{item.withdrawal_context && <SourceExcerpt context={{...item.withdrawal_context, change_kind: "withdrawal"}} label="撤回依据" />}<p className="text-zinc-600 dark:text-zinc-400">原始发布者：{publisher || "未记录"} · 采集 {item.source.collector ? sourceLabel(item.source.collector) : "未记录"} · {item.source.source_tier_label}</p></div>},
      {id: "relation", label: "个股关联", icon: "relation", summary: relation.label, content: <div className="space-y-4"><p>关联与已记录方向只是已有解释记录，不证明消息属实或个股受益。</p>{relation.basis && <NarrativeText label={/权威关联|直接利好/.test(relation.basis) ? "原解释（待核）" : "关联依据"} text={relation.basis} />}{relation.chain && <NarrativeText label={/权威关联|直接利好/.test(relation.chain) ? "原解释（待核）" : "传导线索"} text={relation.chain} />}{!relation.basis && !relation.chain && <p>关联依据与传导线索未记录。</p>}</div>},
      {id: "response", label: "市场响应", icon: "response", summary: item.market_response.status_label, content: <div className="space-y-4"><p>{item.market_response.basis}。市场响应不证明消息属实或涨停因果。</p><ConditionList label="其他解释与待核线索" items={item.alternatives} />{item.review_note && <NarrativeText label="复核记录" text={item.review_note} />}</div>},
      {id: "timing", label: "核对时点", icon: "time", summary: item.timing.source_time_known ? timeLabel(item.timing.source_published_at) : "来源时间未知", content: <FactTimeline label="来源与解释时点" items={[
        {label:"来源发布时间", value:item.timing.source_time_known ? timeLabel(item.timing.source_published_at) : "未知，未用收录时间补填"},
        {label:"系统收到", value:timeLabel(item.timing.received_at)}, {label:"消息可用", value:timeLabel(item.timing.available_at)},
        {label:"解释生效", value:timeLabel(item.timing.interpretation_effective_at)}, {label:"来源记录", value:item.interpretation_ref.observation_id ? `#${item.interpretation_ref.observation_id}` : "未知"},
      ]} />},
    ]} />
  </article>;
}

/** One local reading position; list navigation reuses the already loaded evidence. */
function AttributionItems({items, selectedAt, recordedRefs, selectionContext, defaultSourceExpanded}: {
  items: AttributionEvidenceItem[]; selectedAt?: string | null; recordedRefs?: EventInterpretationRef[];
  selectionContext: boolean; defaultSourceExpanded: boolean;
}) {
  const [selection, setSelection] = useState<{id: number | null; removed: boolean}>({id: null, removed: false});
  const selectedItem = items.find(item => item.event_id === selection.id);
  const entryButtons = useRef(new Map<number, HTMLButtonElement>());
  const backButton = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const returnFocus = useRef<number | null>(null);
  const listScroll = useRef(0);
  const view = useRef<HTMLDivElement>(null);
  const selectedEventId = selectedItem?.event_id ?? null;

  // Reset a removed selection before commit, so a later refresh cannot reopen it silently.
  if (selection.id !== null && !selectedItem) setSelection({id: null, removed: true});

  useLayoutEffect(() => {
    const viewport = readingViewport(view.current);
    if (selectedEventId !== null) {
      if (viewport && backButton.current) viewport.scrollTop = Math.max(0, viewport.scrollTop + backButton.current.getBoundingClientRect().top - viewport.getBoundingClientRect().top - 8);
      backButton.current?.focus({preventScroll: true});
    } else if (returnFocus.current !== null) {
      if (viewport) viewport.scrollTop = listScroll.current;
      const target = entryButtons.current.get(returnFocus.current) || entryButtons.current.values().next().value || list.current;
      target?.focus({preventScroll: true});
      returnFocus.current = null;
    }
  }, [selectedEventId]);

  return <div ref={view} className="min-w-0">
    {selection.removed && <p role="status" className="py-2 leading-relaxed text-zinc-600 dark:text-zinc-400">所选消息已不在本次资料中，已返回列表。</p>}
    {selectedItem ? <div className="anim-fade-in min-w-0">
      <button ref={backButton} type="button" data-action="quiet" className={`quiet-action ${styles.action}`} onClick={() => setSelection({id: null, removed: false})}><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="mr-1"><path d="m14 6-6 6 6 6" /></svg>返回消息列表</button>
      <AttributionEvidence item={selectedItem} selectedAt={selectedAt} recordedRef={recordedRefs?.find(ref => ref.event_id === selectedItem.event_id)} selectionContext={selectionContext} defaultSourceExpanded={defaultSourceExpanded} />
    </div> : <ul ref={list} tabIndex={-1} aria-label="归因消息列表" className="min-w-0 divide-y divide-zinc-200 dark:divide-zinc-700/60">
      {items.map(item => <li key={item.event_id} className="min-w-0"><button ref={button => {if (button) entryButtons.current.set(item.event_id, button); else entryButtons.current.delete(item.event_id);}} type="button" data-action="quiet" className={`quiet-action ${styles.entry}`} aria-label={`查看归因消息：${item.title}`} onClick={() => {
        returnFocus.current = item.event_id;
        listScroll.current = readingViewport(view.current)?.scrollTop || 0;
        setSelection({id: item.event_id, removed: false});
      }}><span className="min-w-0"><span className={styles.title}>{item.title}</span><span className={styles.meta}><span>{item.state_label}</span><span>{item.stock_relation.label}</span></span></span><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m9 6 6 6-6 6" /></svg></button></li>)}
    </ul>}
  </div>;
}

/** No polling or per-candidate reads: only an open detail requests current evidence. */
export const StockAttributionPanel = memo(function StockAttributionPanel({symbol, eventId, active = true, title = "驱动线索", defaultExpanded = false, bounded = true, selectedAt, recordedRefs, selectionContext = false, defaultSourceExpanded = false}: {
  symbol?: string | null; eventId?: number | null; active?: boolean; title?: string; defaultExpanded?: boolean; bounded?: boolean;
  selectedAt?: string | null; recordedRefs?: EventInterpretationRef[];
  selectionContext?: boolean; defaultSourceExpanded?: boolean;
}) {
  const [open, setOpen] = useState(defaultExpanded);
  const [readingCycle, setReadingCycle] = useState(0);
  const id = useId();
  const identity = `${symbol || ""}:${eventId || ""}`;
  const presence = useExitPresence(open ? identity : null);
  const resource = useResource(() => eventId ? getEventAttribution(eventId, symbol) : getStockAttribution(symbol!), {key: identity, enabled: active && open && (!!eventId || /^\d{6}$/.test(symbol || "")), intervalMs: null, marketHours: false});
  const data = resource.data;
  return <section className="min-w-0 py-1 text-xs" aria-label={title}>
    <div className="flex min-h-8 flex-wrap items-center justify-between gap-2"><button type="button" data-action="quiet" className={`quiet-action min-w-0 text-left ${styles.action} ${styles.panelToggle}`} aria-expanded={open} aria-controls={id} onClick={() => {if (!open) setReadingCycle(value => value + 1); setOpen(value => !value);}}><span>{title}</span><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={`ml-1 shrink-0 transition-transform duration-150 motion-reduce:transition-none ${open ? "rotate-180" : ""}`}><path d="m6 9 6 6 6-6" /></svg></button>{open && <button type="button" data-action="quiet" className={`quiet-action shrink-0 ${styles.action}`} disabled={resource.pending} onClick={resource.refresh}>{resource.pending && data ? "更新中" : "刷新依据"}</button>}</div>
    <div className="motion-disclosure" data-open={open} aria-hidden={!open || undefined} inert={!open}><div className="motion-disclosure-body">
    {presence.value === identity && <div data-attribution-reader={bounded || undefined} id={id} className={`min-w-0 ${bounded ? "data-scroll max-h-72 overflow-y-auto pr-1" : ""}`}>
      <p className="py-1 leading-relaxed text-zinc-600 dark:text-zinc-400">已采集消息可逐条查看；不改写首次入选记录。不构成买卖建议。</p>
      {!!recordedRefs?.length && <details className="py-1 text-zinc-600 dark:text-zinc-400"><summary className="w-fit cursor-pointer">{selectionContext ? "选股记录事件引用" : "列表入口事件引用"}</summary><ul className="mt-1 space-y-1 [overflow-wrap:anywhere]">{recordedRefs.map((ref, index) => <li key={`${ref.event_id}:${ref.version_id}:${index}`}>事件 #{ref.event_id} · {ref.version_id ? `原解释 #${ref.version_id}` : "原版本未知"} · {timeLabel(ref.available_at)}</li>)}</ul></details>}
      {resource.pending && !data && <div role="status" aria-label="归因依据加载中" className="space-y-2 py-3"><Skeleton className="h-3 w-3/4" /><Skeleton className="h-3 w-full" /><span className="sr-only">归因依据加载中</span></div>}
      {resource.error != null && <p role="alert" className="py-2 leading-relaxed text-amber-800 dark:text-amber-300">{data ? "依据刷新失败，保留上次读取内容。" : "归因依据读取失败，尚不能确认是否有相关消息。"}可使用上方“刷新依据”重试。</p>}
      {data?.items.length === 0 && <p className="py-3 leading-relaxed text-zinc-600 dark:text-zinc-400">已采集资料中暂未找到关联事件。覆盖不等于全市场，不能据此认定没有驱动消息。</p>}
      {data && <AttributionItems key={`${identity}:${readingCycle}`} items={data.items} selectedAt={selectedAt} recordedRefs={recordedRefs} selectionContext={selectionContext} defaultSourceExpanded={defaultSourceExpanded} />}
    </div>}
    </div></div>
  </section>;
});
