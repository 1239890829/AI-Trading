"use client";

import {memo, useId, useLayoutEffect, useRef, useState, type ReactNode} from "react";
import {useResource} from "@/hooks/use-resource";
import {useExitPresence} from "@/hooks/use-exit-presence";
import {getEventAttribution, getStockAttribution, type AttributionEvidenceItem, type AttributionSourceContext, type EventInterpretationRef, type StockAttributionPayload} from "@/lib/api";
import {bjDate, eventTimeText, sourceLabel, timeTextBJ} from "@/lib/format";
import {Skeleton} from "@/components/ui/loading";
import styles from "./attribution-evidence.module.css";
import {ConditionList, FactTimeline} from "@/components/ui/explanation-map";
import {limitReasonView, matchSourceReason} from "@/lib/limit-reason-view";
import {ReaderBrief, ResearchDisclosure} from "@/components/ui/reader-brief";
import {readerExcerpt, readerCautions} from "@/lib/reader-copy";
import {ModalShell} from "@/components/ui/modal-shell";

/** A primary clue must bind a saved source and its exact interpretation. */
function boundPrimaryItem(data: StockAttributionPayload): AttributionEvidenceItem | null {
  const primary = data.primary_reason;
  if (!primary?.event_id || !primary.version_id || !primary.observation_id || !primary.excerpt?.quote) return null;
  return [...data.items, ...(data.later_items || [])].find(item => item.event_id === primary.event_id && item.interpretation_ref.event_id === primary.event_id
    && item.interpretation_ref.version_id === primary.version_id && item.interpretation_ref.observation_id === primary.observation_id
    && !!item.source_context && item.source_context.observation_id === primary.observation_id
    && typeof item.source_context[primary.excerpt!.field] === "string"
    && item.source_context[primary.excerpt!.field]!.includes(primary.excerpt!.quote)) ?? null;
}

/** Saved auxiliary prose needs the same exact source and earlier input journal as the primary view. */
function boundAuxiliary(item: AttributionEvidenceItem) {
  const aux = item.llm_aux;
  const source = aux && item.source_context?.[aux.evidence.field];
  return aux && aux.event_id === item.event_id && Number.isInteger(aux.input_version_id) && aux.input_version_id! > 0
    && Number.isInteger(item.interpretation_ref.version_id) && aux.input_version_id! < item.interpretation_ref.version_id!
    && aux.observation_id === item.interpretation_ref.observation_id && aux.observation_id === item.source_context?.observation_id
    && !!aux.evidence.quote && typeof source === "string" && source.includes(aux.evidence.quote) ? aux : null;
}

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
export function AttributionEvidence({item, symbol, selectedAt, recordedRef, selectionContext = false, defaultSourceExpanded = false, complete = false}: {
  item: AttributionEvidenceItem; symbol?: string | null; selectedAt?: string | null; recordedRef?: EventInterpretationRef; selectionContext?: boolean; defaultSourceExpanded?: boolean; complete?: boolean;
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
  const savedAux = boundAuxiliary(item);
  const qualifiers = item.qualifiers.map(value => `${value.label}${value.matched_terms.length ? `（${value.matched_terms.join("、")}）` : ""}`);
  const relationLabel = symbol ? "个股关联" : "题材关联";
  const sourceSummary = item.source_context?.summary;
  const preview = (text: string) => text.length <= 160 ? text : `${text.slice(0,160)}…`;

  if (!complete) return <section className={styles.evidence} aria-label={`事件归因 ${item.event_id}`}>
    <ReaderBrief label="消息与个股说明" headline={item.title} facts={[
      {label: "怎么相关", value: relation.status === "theme_only" ? "只关联到题材，尚不能确认这只股票受益" : relation.status === "direct_symbol" ? "消息直接提到这家公司，影响仍需核实" : relation.status === "source_association" ? "来源记录了公司关联，影响仍需核实" : "还没有足够的公司关联依据"},
      {label: "消息进展", value: !item.is_active || item.interpretation_ref.state === "withdrawn" ? "已失效或撤回，不作为当前依据" : item.certainty === "proposed" ? "仍是计划，尚未落地" : item.certainty === "rumor" ? "传闻，尚未证实" : item.certainty === "done" ? "来源称已发生，未独立核实" : "进展还不清楚"},
      {label: savedAux ? "AI解读（待核）" : "内容摘录", value: readerExcerpt(savedAux?.reason || sourceSummary || item.source_context?.title) || "来源正文未保存"},
    ]} cautions={[...readerCautions([sourceSummary || "", relation.basis, ...qualifiers, ...item.limitations]), ...(savedAux ? ["AI解读未独立验证"] : []), ...(!sourceBound ? ["来源与解释尚未对应完整"] : []), ...(late || lateInterpretation ? ["这条解释晚于入选，仅供事后复盘"] : []), ...(revised ? ["解释已变更，不替换原入选依据"] : []), ...(selectedAt && (selected === null || available === null || effective === null) ? ["时间记录不全，无法确认入选时是否已知"] : [])]}/>
    <ResearchDisclosure identity={`${item.event_id}:${version}:${observation}:${item.state}:${JSON.stringify(item)}`}>
      <AttributionEvidence item={item} symbol={symbol} selectedAt={selectedAt} recordedRef={recordedRef} selectionContext={selectionContext} defaultSourceExpanded complete/>
    </ResearchDisclosure>
  </section>;

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
    {!complete && (relation.basis || relation.chain) && <section className={styles.nodeContent} aria-label="当前关联依据摘要"><h4>已记录依据 · 待核</h4>{relation.basis && <p className={styles.sourceCopy}>{preview(relation.basis)}</p>}{relation.chain && relation.chain !== relation.basis && <p className={styles.sourceCopy}>{preview(relation.chain)}</p>}</section>}
    {<div className={styles.nodeContent} aria-label="完整依据核对">
      <section><h4>来源与解释引用</h4><p>{item.fact_support.label} · {item.market_response.status_label}</p><p>事件 #{item.event_id} · 解释 {version ? `#${version}` : "未知"} · 来源观察 {observation ? `#${observation}` : "未知"}</p>
        <dl className={styles.sourceFacts}><div><dt>原始发布者</dt><dd>{publisher || "未记录"}</dd></div><div><dt>采集平台</dt><dd>{item.source.collector ? sourceLabel(item.source.collector) : "未记录"}</dd></div><div><dt>来源等级</dt><dd>{item.source.source_tier_label}</dd></div><div><dt>来源绑定</dt><dd>{sourceBound ? "本解释引用的来源" : "待核，未补推"}</dd></div></dl>
        {item.pending_context && <SourceExcerpt context={item.pending_context} label="来源已修订，待复核内容" />}
        {item.withdrawal_context && <SourceExcerpt context={{...item.withdrawal_context, change_kind: "withdrawal"}} label="撤回依据" />}
      </section>
      <section aria-label="完整关联依据"><h4>{relationLabel} · {relation.label}</h4><p>关联与已记录方向不证明消息属实或个股受益。</p>
        {relation.basis && <p className={styles.sourceCopy}>{relation.basis}</p>}{relation.chain && relation.chain !== relation.basis && <p className={styles.sourceCopy}>{relation.chain}</p>}
        {!relation.basis && !relation.chain && <p>关联依据与传导线索未记录。</p>}
        <p>方向原标记：{relation.direction === null ? "未知" : String(relation.direction)} · 匹配口径：{relation.matched_by || "未记录"}</p>
        {relation.matched_themes.length > 0 && <ConditionList label="已记录关联题材" items={relation.matched_themes}/>}
      </section>
      <section><h4>市场响应 · 待核</h4><p className={styles.sourceCopy}>{item.market_response.basis}</p><p>市场响应不证明消息属实或涨停因果。</p><ConditionList label="其他解释与待核线索" items={item.alternatives} />{item.review_note && <p>{item.review_note}</p>}</section>
    </div>}
    {savedAux && <section className={styles.nodeContent} aria-label="已保存辅助判读"><h4>{savedAux.state === "no_direction" ? "辅助判读：无明确方向" : "辅助方向假设"}</h4><p>尚未验证，不影响选股资格。</p><p className={styles.sourceCopy}>{savedAux.reason}</p>{savedAux.chain && <p className={styles.sourceCopy}>{savedAux.chain}</p>}<blockquote className={styles.sourceCopy}>{savedAux.evidence.quote}</blockquote><p>输入解释 {savedAux.input_version_id ? `#${savedAux.input_version_id}` : "未知"} · 来源观察 {savedAux.observation_id ? `#${savedAux.observation_id}` : "未知"}</p></section>}
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

/** Toolbar reads only on open; visible reason fallbacks read once, with no polling. */
export const StockAttributionPanel = memo(function StockAttributionPanel({symbol, eventId, active = true, title = "驱动线索", defaultExpanded = false, bounded = true, selectedAt, recordedRefs, selectionContext = false, defaultSourceExpanded = false, poolDate, responseAsOf, primarySummary = false, inlinePrimary = false, sourceReason, sourceCore, sourceAudit}: {
  symbol?: string | null; eventId?: number | null; active?: boolean; title?: string; defaultExpanded?: boolean; bounded?: boolean;
  selectedAt?: string | null; recordedRefs?: EventInterpretationRef[];
  selectionContext?: boolean; defaultSourceExpanded?: boolean;
  poolDate?: string; responseAsOf?: string; primarySummary?: boolean; inlinePrimary?: boolean; sourceReason?: string | null;
  /** Inline cause readers keep their already-visible source core and full audit in one reading flow. */
  sourceCore?: string; sourceAudit?: ReactNode;
}) {
  const [open, setOpen] = useState(defaultExpanded);
  const [readingCycle, setReadingCycle] = useState(0);
  const id = useId();
  const identity = `${symbol || ""}:${eventId || ""}:${poolDate || ""}:${responseAsOf || ""}`;
  const presence = useExitPresence(active && open ? identity : null);
  const [openedIdentity, setOpenedIdentity] = useState(identity);
  if (openedIdentity !== identity) {setOpenedIdentity(identity); if (primarySummary) setOpen(false);}
  if (primarySummary && !active && open) setOpen(false);
  const options = poolDate || responseAsOf ? {poolDate, responseAsOf} : undefined;
  const resource = useResource(() => eventId ? options ? getEventAttribution(eventId, symbol, options) : getEventAttribution(eventId, symbol) : options ? getStockAttribution(symbol!, options) : getStockAttribution(symbol!), {key: identity, enabled: active && (open || primarySummary || inlinePrimary) && (!!eventId || /^\d{6}$/.test(symbol || "")), intervalMs: null, marketHours: false});
  const data = resource.data;
  const primary = data?.primary_reason;
  const boundItem = data ? boundPrimaryItem(data) : null;
  const sourceMatch = boundItem ? matchSourceReason(boundItem, sourceReason) : null;
  const primaryItem = inlinePrimary && boundItem && (!["direct_symbol", "source_association"].includes(boundItem.stock_relation.status) || !sourceMatch) ? null : boundItem;
  const otherItems = primaryItem ? data!.items.filter(item => item.event_id !== primaryItem.event_id) : data?.items || [];
  const otherLater = primaryItem ? (data!.later_items || []).filter(item => item.event_id !== primaryItem.event_id) : data?.later_items || [];
  if (inlinePrimary) {
    const boundAux = primaryItem ? boundAuxiliary(primaryItem) : null;
    const sourceText = [sourceReason, primaryItem?.source_context?.title, primaryItem?.source_context?.summary].filter(Boolean).join("\n");
    const sourceConditions = limitReasonView(sourceText).conditions;
    const conditionKey = (value: string) => value.replace(/\s+/g, "").replace(/[。；;！？!?]+$/, "");
    const fullConditions = sourceConditions.map(conditionKey).join("\n");
    // A complete source sentence carries its own qualifiers. Keep a label only
    // when the saved qualifier has no corresponding visible source wording.
    const qualifiers = [...(primaryItem ? primary?.qualifiers || [] : []), ...(primaryItem?.qualifiers || [])].filter(value =>
      value.matched_terms.length ? !value.matched_terms.every(term => fullConditions.includes(conditionKey(term))) : !fullConditions.includes(conditionKey(value.label))
    ).map(value => `${value.label}${value.matched_terms.length ? `（${value.matched_terms.join("、")}）` : ""}`);
    const seen = new Set(sourceCore ? [conditionKey(sourceCore)] : []);
    const conditions = [...sourceConditions, ...qualifiers, ...(primaryItem?.limitations || [])].filter(value => {
      const key = conditionKey(value);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    const auditIdentity = `${identity}:${sourceReason || ""}:${primaryItem?.interpretation_ref.version_id || ""}:${primaryItem?.interpretation_ref.observation_id || ""}`;
    return <section aria-label={title} className="min-w-0 space-y-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-2"><h3 className="font-medium">已保存解读{boundAux?.state === "no_direction" ? " · 无明确方向" : ""}</h3>{!!resource.error && <button type="button" data-action="secondary" disabled={resource.pending} onClick={resource.refresh}>重试解读</button>}</div>
      {resource.error ? <p role="alert" className="text-amber-800 dark:text-amber-300">解读读取失败{data ? " · 保留已有内容" : "，暂不能核对"}</p> : resource.pending && !data && <p role="status" className="text-zinc-600 dark:text-zinc-400">读取已保存解读…</p>}
      {data && !primaryItem && <p className="text-zinc-600 dark:text-zinc-400">暂无可核对的直接来源线索，不用题材标签补填原因。</p>}
      {primaryItem && <>
        <ReaderBrief label="原因与依据关系" facts={[
          {label: boundAux ? "AI解读（待核）" : "消息摘录", value: readerExcerpt(boundAux?.reason || primary?.excerpt?.quote)},
          {label: "与公司", value: primaryItem.stock_relation.status === "direct_symbol" ? "消息直接提到公司，尚不能证明涨停由此引起" : "来源提及公司关联，实际影响待核实"},
          {label: "进展", value: !primaryItem.is_active || primaryItem.interpretation_ref.state === "withdrawn" ? "已失效或撤回" : primaryItem.certainty === "proposed" ? "尚属计划" : primaryItem.certainty === "rumor" ? "传闻待证实" : primaryItem.certainty === "done" ? "来源称已发生" : "尚不清楚"},
        ]} cautions={[...readerCautions(conditions), ...(conditions.length ? ["完整限制条件见依据"] : []), ...(primaryItem.timing_relation && primaryItem.timing_relation.status !== "known_before_response" ? ["消息时间未能对应首封时点"] : []), ...(boundAux ? ["AI解读仅作参考，未独立验证"] : [])]}/>
      </>}
      {!primaryItem && conditions.length > 0 && <ReaderBrief label="来源原因限制" cautions={readerCautions(conditions)}/>}
      <p className="text-zinc-600 dark:text-zinc-400">来源解释，尚不能确认是涨停的真正原因。</p>
      {(sourceAudit || primaryItem) && <ResearchDisclosure identity={auditIdentity} label="来源原文与完整核对"><div className="min-w-0 space-y-3 py-2"><ConditionList label="必要条件" items={conditions} tone="warning"/>
        {sourceAudit}
        {primaryItem && <>
          <p className="text-zinc-600 dark:text-zinc-400">{primary?.label} · 当前保存解释，不回填首封或入选依据。</p>
          <p className="text-zinc-600 dark:text-zinc-400">与来源原因交叉：{sourceMatch}，仅作展示关联核对。</p>
          {boundAux && <p className="text-zinc-600 dark:text-zinc-400">已保存辅助判读 · {boundAux.state === "no_direction" ? "无明确方向" : "待验证假设"}，尚未验证，不改变选股资格。</p>}
          {primaryItem.timing_relation && <p className="text-zinc-600 dark:text-zinc-400">{primaryItem.timing_relation.label}，不回填首封时的原因。</p>}
          <FactTimeline label="关键来源时点" items={[{label: "消息发布", value: primaryItem.timing.source_time_known ? timeLabel(primaryItem.timing.source_published_at) : "未知", kind: "time"}, {label: "解释生效", value: timeLabel(primaryItem.timing.interpretation_effective_at), kind: "time"}]}/>
          <AttributionEvidence item={primaryItem} symbol={symbol} selectedAt={selectedAt} selectionContext={selectionContext} defaultSourceExpanded complete/>
        </>}
      </div></ResearchDisclosure>}
    </section>;
  }
  const primaryReading = primary && <div className="min-w-0 space-y-3">
    <p className="text-zinc-600 dark:text-zinc-400">{primary.label} · {primary.reason}。来源线索不是已核实因果，不回填入选与首封依据。</p>
    {!!primary.event_id && !primaryItem && <p role="alert" className="text-amber-800 dark:text-amber-300">主线索来源绑定待核，未自动选择其他资料。</p>}
    {primaryItem && <AttributionEvidence item={primaryItem} symbol={symbol} selectedAt={selectedAt} recordedRef={recordedRefs?.find(ref => ref.event_id === primaryItem.event_id)} selectionContext={selectionContext} defaultSourceExpanded />}
    {(otherItems.length > 0 || otherLater.length > 0) && <details><summary className="cursor-pointer text-zinc-600 dark:text-zinc-400">其他关联资料 · {otherItems.length + otherLater.length} 条</summary><AttributionItems items={otherItems} laterItems={otherLater} responseContext={!!data?.response_context} symbol={symbol} selectedAt={selectedAt} recordedRefs={recordedRefs} selectionContext={selectionContext} defaultSourceExpanded={defaultSourceExpanded}/></details>}
  </div>;
  if (primarySummary) return <section aria-label={title} className="min-w-0 py-1 text-xs">
    <div className="flex min-w-0 items-center gap-2">
      {resource.error ? <p role="alert" className="min-w-0 text-amber-800 dark:text-amber-300">来源线索读取失败{data ? " · 保留结果" : ""}</p>
        : resource.pending && !data ? <p role="status" className="text-zinc-600 dark:text-zinc-400">来源线索读取中…</p>
          : <span className="min-w-0 flex-1 truncate" title={primary ? `${primary.label}：${primary.reason}；不证明因果` : "主线索未确认"}><span className="mr-1 text-zinc-600 dark:text-zinc-400">{primary?.label || "主线索未确认"}</span>{primaryItem ? primary?.excerpt?.quote : primary?.reason || "已保存资料尚不能建立主线索"}</span>}
      {!!resource.error && <button type="button" data-action="secondary" disabled={resource.pending} onClick={resource.refresh}>重读</button>}
      {data && <button type="button" data-action="secondary" className="shrink-0" aria-label="查看主线索完整依据" aria-haspopup="dialog" onClick={() => setOpen(true)}>核对全文</button>}
    </div>
    {presence.value === identity && <ModalShell presentation="drawer" open={presence.active} onClose={() => setOpen(false)} label={title} size="md" zIndex={60} header={<h2 className="text-sm font-medium">{title}</h2>} bodyTabIndex={0} bodyClassName="data-scroll overflow-y-auto px-4 py-3 text-xs [overflow-wrap:anywhere]">
      {presence.active && <><button type="button" data-action="secondary" disabled={resource.pending} onClick={resource.refresh}>刷新依据</button>{data?.response_context && <p className="py-2 text-zinc-600 dark:text-zinc-400">响应时点 {timeLabel(data.response_context.response_as_of)} · 当前解释，非历史还原。</p>}{primaryReading || <><p className="py-2">主线索未记录，不能选第一条资料冒充原因。</p>{data && (otherItems.length > 0 || otherLater.length > 0) && <details><summary className="cursor-pointer">其他关联资料 · {otherItems.length + otherLater.length} 条</summary><AttributionItems items={otherItems} laterItems={otherLater} responseContext={!!data.response_context} symbol={symbol} selectedAt={selectedAt} recordedRefs={recordedRefs} selectionContext={selectionContext} defaultSourceExpanded /></details>}</>}<p className="py-2 text-zinc-600 dark:text-zinc-400">来源解释供核对，不构成买卖建议。</p></>}
    </ModalShell>}
  </section>;
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
      {primaryReading || (data && <AttributionItems laterItems={data.later_items} responseContext={!!data.response_context} key={`${identity}:${readingCycle}`} items={data.items} symbol={symbol} selectedAt={selectedAt} recordedRefs={recordedRefs} selectionContext={selectionContext} defaultSourceExpanded={defaultSourceExpanded} />)}
    </div>}
    </div></div>
  </section>;
});
