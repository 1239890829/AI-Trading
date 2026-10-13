"use client";

import {memo, useId, useLayoutEffect, useRef, useState} from "react";
import {useResource} from "@/hooks/use-resource";
import {useExitPresence} from "@/hooks/use-exit-presence";
import {getEventAttribution, getStockAttribution, type AttributionEvidenceItem, type AttributionSourceContext, type EventInterpretationRef} from "@/lib/api";
import {bjDate, eventTimeText, sourceLabel, timeTextBJ} from "@/lib/format";
import {Skeleton} from "@/components/ui/loading";
import styles from "./attribution-evidence.module.css";
import {ConditionList, ExplanationMap, FactTimeline} from "@/components/ui/explanation-map";

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
    {context ? <blockquote className={styles.sourceCopy}><p>{context.title}</p>{context.summary && <p aria-label={label}>{context.summary}</p>}</blockquote> : <p className="text-zinc-600 dark:text-zinc-400">原始内容未记录，不能核对完整语境。</p>}
    {context && !context.summary && <p className="text-zinc-600 dark:text-zinc-400">仅保存标题；正文条件尚未核对。</p>}
    {!href && <p className="text-zinc-600 dark:text-zinc-400">原文链接未记录。</p>}
  </div>;
}

/** The same explanation vocabulary serves source events, stock details and selection drawers. */
export function AttributionEvidence({item, symbol, selectedAt, recordedRef, selectionContext = false, defaultSourceExpanded = false}: {
  item: AttributionEvidenceItem; symbol?: string | null; selectedAt?: string | null; recordedRef?: EventInterpretationRef; selectionContext?: boolean; defaultSourceExpanded?: boolean;
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
  const sourceBound = typeof version === "number" && Number.isInteger(version) && version > 0 && hasSourceIdentity && observation === item.interpretation_ref.observation_id;
  const excerptLabel = !item.source_context ? "来源内容未记录" : !hasSourceIdentity ? "旧卡片摘录 · 来源观察身份未记录" : sourceBound ? "解释版本绑定的来源内容" : "来源观察内容 · 解释绑定待核";
  const stageUnknown = !["done", "proposed", "rumor"].some(value => value === item.certainty);
  const qualifiers = item.qualifiers.map(value => `${value.label}${value.matched_terms.length ? `（${value.matched_terms.join("、")}）` : ""}`);
  const relationLabel = symbol ? "个股关联" : "题材关联";
  const sourceSummary = item.source_context?.summary;
  const preview = (text: string) => text.length <= 160 ? text : `${text.slice(0,160)}…`;

  return <article className={styles.evidence} aria-label={`事件归因 ${item.event_id}`}>
    <header className={styles.evidenceHeader}><h3>{item.title}</h3>
      {!!item.dimensions.length && <ul className={styles.dimensions} aria-label="来源线索维度">{item.dimensions.map(value => <li key={value.key}>{value.label}</li>)}</ul>}
    </header>
    <p className={styles.boundary}>来源收录不等于消息属实；关联与方向仍须核对。</p>
    {item.timing_relation && <p role="note" className="text-zinc-600 dark:text-zinc-400">{item.timing_relation.label}。时点关系不证明涨停因果。</p>}
    {stageUnknown && <p role="note" className="text-amber-800 dark:text-amber-300">事件阶段尚未核实，不能按已落地事件解读。</p>}
    {(late || lateInterpretation || revised) && <p role="note" className="text-amber-800 dark:text-amber-300">{late ? "消息可用时间晚于首次加入，仅供后续复盘。" : lateInterpretation ? "当前解释形成晚于首次加入，仅供后续复盘。" : selectionContext ? "当前解释与选股记录引用的原版本不同。" : "当前解释与列表入口引用的版本不同。"}{selectionContext || selectedAt ? "不回填当时入选依据。" : "列表快照保留原记录。"}</p>}
    {selectedAt && (selected === null || available === null || effective === null) && <p className="text-zinc-600 dark:text-zinc-400">时点证据不完整，无法确认这条解释在首次加入时是否可用。</p>}
    {defaultSourceExpanded ? <section className={styles.nodeContent} aria-label="完整来源核对"><p>{item.fact_support.note}</p><SourceExcerpt context={item.source_context} label={excerptLabel}/></section>
      : sourceSummary && <section className={styles.nodeContent} aria-label="来源要点摘录"><h4>{sourceBound ? "绑定来源要点 · 原文摘录" : "来源要点 · 绑定待核"}</h4><p className={styles.sourceCopy}>{preview(sourceSummary)}</p></section>}
    {(relation.basis || relation.chain) && <section className={styles.nodeContent} aria-label="当前关联依据摘要"><h4>已记录依据 · 待核</h4>{relation.basis && <p className={styles.sourceCopy}>{preview(relation.basis)}</p>}{relation.chain && relation.chain !== relation.basis && <p className={styles.sourceCopy}>{preview(relation.chain)}</p>}</section>}
    <ExplanationMap label="归因资料关系" identity={`${item.event_id}:${version ?? "unknown"}:${observation ?? "legacy"}`}
      center={{id: "interpretation", label: "事件解释", icon: "check", value: <span>{item.state_label}<span className={styles.identity}>{version ? `解释 #${version}` : "版本未知"}</span></span>}}
      links={[
        {from: "source", to: "interpretation", label: sourceBound ? "版本引用的来源" : "来源绑定待核", kind: sourceBound ? "recorded" : "unverified"},
        {from: "interpretation", to: "relation", label: relation.status === "theme_only" ? "题材成员关联 · 待核" : "已记录关联 · 非受益证明", kind: "unverified"},
        {from: "interpretation", to: "response", label: "市场观察 · 非因果", kind: "context"},
        {from: "interpretation", to: "timing", label: "可用时点核对", kind: "sequence"},
      ]}
      steps={[
        {id: "source", label: "消息证据", icon: "source", summary: <span>{item.fact_support.label}<span className={styles.nodeFact}>{publisher || (item.source.collector ? sourceLabel(item.source.collector) : "发布者未记录")}</span>{(item.pending_context || item.withdrawal_context) && <span className={styles.nodeFact}>{item.withdrawal_context ? "有撤回记录" : "来源修订待复核"}</span>}</span>, content: <div className={styles.nodeContent}><p>{item.fact_support.note}</p>{!defaultSourceExpanded && <SourceExcerpt context={item.source_context} label={excerptLabel} />}{item.pending_context && <SourceExcerpt context={item.pending_context} label="来源已修订，待复核内容" />}{item.withdrawal_context && <SourceExcerpt context={{...item.withdrawal_context, change_kind: "withdrawal"}} label="撤回依据" />}<dl className={styles.sourceFacts}><div><dt>原始发布者</dt><dd>{publisher || "未记录"}</dd></div><div><dt>采集平台</dt><dd>{item.source.collector ? sourceLabel(item.source.collector) : "未记录"}</dd></div><div><dt>来源等级</dt><dd>{item.source.source_tier_label}</dd></div><div><dt>解释引用来源</dt><dd>{item.interpretation_ref.observation_id ? `#${item.interpretation_ref.observation_id}` : "未知"}</dd></div></dl></div>},
        {id: "relation", label: relationLabel, icon: "relation", summary: <span>{relation.label}<span className={styles.nodeFact}>{symbol ? `个股 ${symbol}` : relation.matched_themes.length ? relation.matched_themes.join("、") : "关联对象未记录"}</span></span>, content: <div className={styles.nodeContent}><p>关联与已记录方向只是已有解释记录，不证明消息属实或个股受益。</p>{relation.matched_themes.length > 0 && <ConditionList label="已记录关联题材" items={relation.matched_themes}/>}<dl className={styles.sourceFacts}><div><dt>方向原标记</dt><dd>{relation.direction === null ? "未知" : String(relation.direction)}</dd></div><div><dt>匹配口径</dt><dd>{relation.matched_by || "未记录"}</dd></div></dl>{relation.basis && <section aria-label={/权威关联|直接利好/.test(relation.basis) ? "原解释（待核）" : "关联依据"}><h4>{/权威关联|直接利好/.test(relation.basis) ? "原解释（待核）" : "关联依据"}</h4><p className={styles.sourceCopy}>{relation.basis}</p></section>}{relation.chain && <section aria-label={/权威关联|直接利好/.test(relation.chain) ? "原解释（待核）" : "传导线索"}><h4>{/权威关联|直接利好/.test(relation.chain) ? "原解释（待核）" : "传导线索"}</h4><p className={styles.sourceCopy}>{relation.chain}</p></section>}{!relation.basis && !relation.chain && <p>关联依据与传导线索未记录。</p>}</div>},
        {id: "response", label: "市场观察", icon: "response", summary: item.market_response.status_label, content: <div className={styles.nodeContent}><p className={styles.sourceCopy}>{item.market_response.basis}</p><p>市场响应不证明消息属实或涨停因果。</p><ConditionList label="其他解释与待核线索" items={item.alternatives} />{item.review_note && <section><h4>复核记录</h4><p className={styles.sourceCopy}>{item.review_note}</p></section>}</div>},
        {id: "timing", label: "核对时点", icon: "time", summary: item.timing.source_time_known ? timeLabel(item.timing.source_published_at) : "来源时间未知", content: <div className={styles.nodeContent}><p>四个时点保留各自口径；系统收到不等于原始发布，解释生效也不等于事件已落地。</p>{selectedAt && <FactTimeline label="首次加入时点" items={[{label: "首次加入", value: timeLabel(selectedAt), kind: "time"}]}/>}<dl className={styles.sourceFacts}><div><dt>解释引用来源</dt><dd>{item.interpretation_ref.observation_id ? `#${item.interpretation_ref.observation_id}` : "未知"}</dd></div></dl></div>},
      ]} />
    <FactTimeline label="来源与解释时点" items={[
      {label: "来源发布", value: item.timing.source_time_known ? timeLabel(item.timing.source_published_at) : "未知，未用收录时间补填", kind: "time"},
      {label: "系统收到", value: timeLabel(item.timing.received_at), kind: "time"},
      {label: "消息可用", value: timeLabel(item.timing.available_at), kind: "time"},
      {label: "解释生效", value: timeLabel(item.timing.interpretation_effective_at), kind: "time"},
    ]}/>
    <div className={styles.conditionArea}><ConditionList label="关键条件" items={qualifiers.length ? qualifiers : ["未记录关键条件，不等于事件没有条件。"]} tone="warning" /><ConditionList label="解读边界" items={item.limitations.length ? item.limitations : ["解读边界未记录，仍需核对完整语境。"]} tone="warning" /></div>
  </article>;
}

/** One local reading position; list navigation reuses the already loaded evidence. */
function AttributionItems({items, laterItems = [], responseContext = false, symbol, selectedAt, recordedRefs, selectionContext, defaultSourceExpanded}: {
  items: AttributionEvidenceItem[]; laterItems?: AttributionEvidenceItem[]; responseContext?: boolean; selectedAt?: string | null; recordedRefs?: EventInterpretationRef[];
  symbol?: string | null; selectionContext: boolean; defaultSourceExpanded: boolean;
}) {
  const [selection, setSelection] = useState<{id: number | null; removed: boolean}>({id: null, removed: false});
  const selectedItem = [...items, ...laterItems].find(item => item.event_id === selection.id);
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
      <AttributionEvidence item={selectedItem} symbol={symbol} selectedAt={selectedAt} recordedRef={recordedRefs?.find(ref => ref.event_id === selectedItem.event_id)} selectionContext={selectionContext} defaultSourceExpanded={defaultSourceExpanded} />
    </div> : <ul ref={list} tabIndex={-1} aria-label="归因消息列表" className="min-w-0 divide-y divide-zinc-200 dark:divide-zinc-700/60">
      {(responseContext ? [{label: "响应时点前已有记录", rows: items}, {label: "晚到或时点未明资料", rows: laterItems}] : [{label: null, rows: items}]).map(group => <li key={group.label ?? "all"} className="list-none min-w-0">
        {group.label && <h4 className="py-2 font-medium">{group.label} · {group.rows.length} 条</h4>}
        <ul className="min-w-0 divide-y divide-zinc-200 dark:divide-zinc-700/60">
        {group.rows.map(item => <li key={item.event_id} className="min-w-0"><button ref={button => {if (button) entryButtons.current.set(item.event_id, button); else entryButtons.current.delete(item.event_id);}} type="button" data-action="quiet" className={`quiet-action ${styles.entry}`} aria-label={`查看归因消息：${item.title}`} onClick={() => {
          returnFocus.current = item.event_id;
          listScroll.current = readingViewport(view.current)?.scrollTop || 0;
          setSelection({id: item.event_id, removed: false});
        }}><span className="min-w-0"><span className={styles.title}>{item.title}</span><span className={styles.meta}><span>{item.state_label}</span><span>{item.stock_relation.label}</span>{item.timing_relation && <span>{item.timing_relation.label}</span>}</span></span><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m9 6 6 6-6 6" /></svg></button></li>)}
        </ul>
      </li>)}
    </ul>}
  </div>;
}

/** No polling or per-candidate reads: only an open detail requests current evidence. */
export const StockAttributionPanel = memo(function StockAttributionPanel({symbol, eventId, active = true, title = "驱动线索", defaultExpanded = false, bounded = true, selectedAt, recordedRefs, selectionContext = false, defaultSourceExpanded = false, poolDate, responseAsOf}: {
  symbol?: string | null; eventId?: number | null; active?: boolean; title?: string; defaultExpanded?: boolean; bounded?: boolean;
  selectedAt?: string | null; recordedRefs?: EventInterpretationRef[];
  selectionContext?: boolean; defaultSourceExpanded?: boolean;
  poolDate?: string; responseAsOf?: string;
}) {
  const [open, setOpen] = useState(defaultExpanded);
  const [readingCycle, setReadingCycle] = useState(0);
  const id = useId();
  const identity = `${symbol || ""}:${eventId || ""}:${poolDate || ""}:${responseAsOf || ""}`;
  const presence = useExitPresence(open ? identity : null);
  const options = poolDate || responseAsOf ? {poolDate, responseAsOf} : undefined;
  const resource = useResource(() => eventId ? options ? getEventAttribution(eventId, symbol, options) : getEventAttribution(eventId, symbol) : options ? getStockAttribution(symbol!, options) : getStockAttribution(symbol!), {key: identity, enabled: active && open && (!!eventId || /^\d{6}$/.test(symbol || "")), intervalMs: null, marketHours: false});
  const data = resource.data;
  return <section className="min-w-0 py-1 text-xs" aria-label={title}>
    <div className="flex min-h-8 flex-wrap items-center justify-between gap-2"><button type="button" data-action="quiet" className={`quiet-action min-w-0 text-left ${styles.action} ${styles.panelToggle}`} aria-expanded={open} aria-controls={id} onClick={() => {if (!open) setReadingCycle(value => value + 1); setOpen(value => !value);}}><span>{title}</span><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={`ml-1 shrink-0 transition-transform duration-150 motion-reduce:transition-none ${open ? "rotate-180" : ""}`}><path d="m6 9 6 6 6-6" /></svg></button>{open && <button type="button" data-action="quiet" className={`quiet-action shrink-0 ${styles.action}`} disabled={resource.pending} onClick={resource.refresh}>{resource.pending && data ? "更新中" : "刷新依据"}</button>}</div>
    <div className="motion-disclosure" data-open={open} aria-hidden={!open || undefined} inert={!open}><div className="motion-disclosure-body">
    {presence.value === identity && <div data-attribution-reader={bounded || undefined} id={id} className={`min-w-0 ${bounded ? "data-scroll max-h-72 overflow-y-auto pr-1" : ""}`}>
      <p className="py-1 leading-relaxed text-zinc-600 dark:text-zinc-400">已采集消息可逐条查看；不改写首次入选记录。不构成买卖建议。</p>
      {!!recordedRefs?.length && <details className="py-1 text-zinc-600 dark:text-zinc-400"><summary className="w-fit cursor-pointer">{selectionContext ? "选股记录事件引用" : "列表入口事件引用"}</summary><ul className="mt-1 space-y-1 [overflow-wrap:anywhere]">{recordedRefs.map((ref, index) => <li key={`${ref.event_id}:${ref.version_id}:${index}`}>事件 #{ref.event_id} · {ref.version_id ? `原解释 #${ref.version_id}` : "原版本未知"} · {timeLabel(ref.available_at)}</li>)}</ul></details>}
      {resource.pending && !data && <div role="status" aria-label="归因依据加载中" className="space-y-2 py-3"><Skeleton className="h-3 w-3/4" /><Skeleton className="h-3 w-full" /><span className="sr-only">归因依据加载中</span></div>}
      {resource.error != null && <p role="alert" className="py-2 leading-relaxed text-amber-800 dark:text-amber-300">{data ? "依据刷新失败，保留上次读取内容。" : "归因依据读取失败，尚不能确认是否有相关消息。"}可使用上方“刷新依据”重试。</p>}
      {data?.items.length === 0 && !(data.later_items?.length) && <p className="py-3 leading-relaxed text-zinc-600 dark:text-zinc-400">已采集资料中暂未找到关联事件。覆盖不等于全市场，不能据此认定没有驱动消息。</p>}
      {data?.response_context && <p className="py-1 leading-relaxed text-zinc-600 dark:text-zinc-400">所查响应时点：{timeLabel(data.response_context.response_as_of)}。按已保存的来源时点分组；当前解释，非历史还原。晚到或时点未知资料不回填驱动与入选依据。</p>}
      {data && <AttributionItems laterItems={data.later_items} responseContext={!!data.response_context} key={`${identity}:${readingCycle}`} items={data.items} symbol={symbol} selectedAt={selectedAt} recordedRefs={recordedRefs} selectionContext={selectionContext} defaultSourceExpanded={defaultSourceExpanded} />}
    </div>}
    </div></div>
  </section>;
});
