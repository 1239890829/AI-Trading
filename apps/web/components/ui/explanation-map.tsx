"use client";

import {createContext, useContext, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode} from "react";
import {useExitPresence} from "@/hooks/use-exit-presence";
import {readingClues, readingClueContext, firstSourceSentence, type ReadingClue} from "@/lib/reading-clues";
import styles from "./explanation-map.module.css";

export type ExplanationIcon = "source" | "relation" | "response" | "time" | "risk" | "check";
export type ExplanationTone = "neutral" | "warning" | "positive" | "negative";
export type ExplanationStep = {id: string; label: string; summary?: ReactNode; content: ReactNode; icon?: ExplanationIcon; tone?: ExplanationTone};
export type ExplanationLink = {from: string; to: string; label: string; kind?: "recorded" | "context" | "sequence" | "unverified"};
export type ExplanationCenter = {id?: string; label: string; value?: ReactNode; icon?: ExplanationIcon};
export type ExplanationMapProps = {label: string; steps: ExplanationStep[]; identity?: string; footer?: ReactNode; links?: ExplanationLink[]; center?: ExplanationCenter};
export type FactItem = {label: string; value: ReactNode; kind?: "time" | "fact"};
export type ExplanationBounds = {left: number; top: number; right: number; bottom: number};
type RoutePoint = {x: number; y: number};

/** Route through measured row gaps or reserved outer gutters, never through another card. */
export function routeExplanationLink({link, rects, width, height, centerId = "center"}: {link: ExplanationLink; rects: Record<string, ExplanationBounds>; width: number; height: number; centerId?: string}) {
  const source = rects[link.from];
  const target = rects[link.to];
  if (!source || !target || link.from === link.to) return null;
  const nodes = Object.entries(rects).filter(([id]) => id !== centerId);
  const sourceX = (source.left + source.right) / 2;
  const targetX = (target.left + target.right) / 2;
  const sourceRowBottom = link.from === centerId ? source.bottom : Math.max(source.bottom, ...nodes.filter(([, rect]) => Math.abs(rect.top - source.top) < 3).map(([, rect]) => rect.bottom));
  const nextTop = Math.min(height, ...nodes.filter(([, rect]) => rect.top > sourceRowBottom).map(([, rect]) => rect.top));
  const leaveY = sourceRowBottom + Math.min(8, Math.max(2, (nextTop - sourceRowBottom) / 2));
  const targetIsCenter = link.to === centerId;
  const previousBottom = Math.max(0, ...Object.entries(rects).filter(([id, rect]) => id !== link.to && rect.bottom <= target.top).map(([, rect]) => rect.bottom));
  const approachY = targetIsCenter ? target.bottom + 8 : target.top - Math.min(8, Math.max(2, (target.top - previousBottom) / 2));
  const sourceY = source.bottom;
  const targetY = targetIsCenter ? target.bottom : target.top;
  const low = Math.min(leaveY, approachY);
  const high = Math.max(leaveY, approachY);
  // The two-column centre gutter is usable only when its complete span is unobstructed.
  const centreGutterClear = link.from === centerId && !nodes.some(([id, rect]) => id !== link.to && sourceX > rect.left - 3 && sourceX < rect.right + 3 && rect.bottom > low && rect.top < high);
  const laneX = centreGutterClear ? sourceX : targetX < width / 2 ? 2 : width - 2;
  const raw: RoutePoint[] = [
    {x: sourceX, y: sourceY}, {x: sourceX, y: leaveY},
    {x: laneX, y: leaveY}, {x: laneX, y: approachY},
    {x: targetX, y: approachY}, {x: targetX, y: targetY},
  ];
  const points = raw.filter((point, index) => !index || point.x !== raw[index - 1].x || point.y !== raw[index - 1].y);
  // Each corner stays within the empty lane. Curvature is bounded by both adjacent segments.
  let d = `M ${points[0].x} ${points[0].y}`;
  for (let index = 1; index < points.length - 1; index++) {
    const before = points[index - 1], corner = points[index], after = points[index + 1];
    const incoming = Math.hypot(corner.x - before.x, corner.y - before.y);
    const outgoing = Math.hypot(after.x - corner.x, after.y - corner.y);
    const radius = Math.min(3, incoming / 2, outgoing / 2);
    const enter = {x: corner.x + (before.x - corner.x) * radius / incoming, y: corner.y + (before.y - corner.y) * radius / incoming};
    const exit = {x: corner.x + (after.x - corner.x) * radius / outgoing, y: corner.y + (after.y - corner.y) * radius / outgoing};
    d += ` L ${enter.x} ${enter.y} Q ${corner.x} ${corner.y} ${exit.x} ${exit.y}`;
  }
  const last = points[points.length - 1];
  d += ` L ${last.x} ${last.y}`;
  return {d, points};
}
const ExplanationDetailContext = createContext(false);
export function useExplanationDetail() {return useContext(ExplanationDetailContext);}

function ReadingIcon({kind = "source"}: {kind?: ExplanationIcon}) {
  return <svg aria-hidden="true" focusable="false" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    {kind === "source" && <><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8Z"/><path d="M14 3v5h5M9 12h6M9 16h6"/></>}
    {kind === "relation" && <><circle cx="5" cy="12" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="m7 11 10-4M7 13l10 4"/></>}
    {kind === "response" && <><path d="M4 19h16M6 15v-4M12 15V5M18 15V8"/><path d="m4 7 4-3 4 2 7-3"/></>}
    {kind === "time" && <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>}
    {kind === "risk" && <><path d="M10.3 4.6a2 2 0 0 1 3.4 0l7 12a2 2 0 0 1-1.7 3H5a2 2 0 0 1-1.7-3Z"/><path d="M12 9v4M12 16h.01"/></>}
    {kind === "check" && <><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></>}
  </svg>;
}

/** Connectors show only the caller's declared relationships; no causal edge is inferred. */
export function ExplanationMap(props: ExplanationMapProps) {
  return <ExplanationReader key={props.identity ?? "default"} {...props}/>;
}

function ExplanationReader({label, steps, footer, links, center}: ExplanationMapProps) {
  const uid = useId();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focusId, setFocusId] = useState(steps[0]?.id ?? null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const graph = useRef<HTMLDivElement>(null);
  const centerElement = useRef<HTMLDivElement>(null);
  const [geometry, setGeometry] = useState<{width: number; height: number; paths: {d: string; link: ExplanationLink}[]} | null>(null);
  const centerId = center?.id ?? "center";
  const declared = links ?? steps.map(step => ({from: centerId, to: step.id, label: "资料类别", kind: "context" as const}));
  const validIds = new Set([centerId, ...steps.map(step => step.id)]);
  const edges = declared.filter(link => validIds.has(link.from) && validIds.has(link.to) && link.from !== link.to);
  const selected = steps.find(step => step.id === selectedId);
  const presence = useExitPresence(selected?.id ?? null);
  const displayed = steps.find(step => step.id === presence.value);
  // Retire vanished nodes immediately. A later refresh cannot revive their old detail state.
  if (selectedId && !selected) setSelectedId(null);
  if (focusId && !steps.some(step => step.id === focusId)) setFocusId(steps[0]?.id ?? null);

  const geometryKey = JSON.stringify({centerId, edges, steps: steps.map(step => step.id)});
  useEffect(() => {
    const area = graph.current;
    if (!area) return;
    let frame = 0;
    function measure() {
      if (!area) return;
      const areaRect = area.getBoundingClientRect();
      if (!areaRect.width || !areaRect.height) return;
      const rects: Record<string, ExplanationBounds> = {};
      const collect = (id: string, element: HTMLElement | null | undefined) => {
        if (!element) return;
        const rect = element.getBoundingClientRect();
        rects[id] = {left: rect.left - areaRect.left, top: rect.top - areaRect.top, right: rect.right - areaRect.left, bottom: rect.bottom - areaRect.top};
      };
      collect(centerId, centerElement.current);
      buttons.current.forEach((button, id) => collect(id, button));
      const paths = edges.flatMap(link => {
        const route = routeExplanationLink({link, rects, width: areaRect.width, height: areaRect.height, centerId});
        return route ? [{d: route.d, link}] : [];
      });
      setGeometry({width: areaRect.width, height: areaRect.height, paths});
    }
    const schedule = () => {if (frame) cancelAnimationFrame(frame); frame = requestAnimationFrame(measure);};
    // One batched DOM measurement per size change, never on a quote tick or mouse move.
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
    observer?.observe(area);
    if (centerElement.current) observer?.observe(centerElement.current);
    buttons.current.forEach(button => observer?.observe(button));
    window.addEventListener("resize", schedule);
    schedule();
    return () => {observer?.disconnect(); window.removeEventListener("resize", schedule); if (frame) cancelAnimationFrame(frame);};
    // Edges are serialized so a caller's equivalent object allocation does not restart the observer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geometryKey]);

  function closeDetail() {
    const id = selected?.id;
    setSelectedId(null);
    if (id) {setFocusId(id); buttons.current.get(id)?.focus({preventScroll: true});}
  }
  function navigate(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key === "Escape") {event.preventDefault(); event.stopPropagation(); closeDetail(); return;}
    let next: number;
    switch (event.key) {
      case "ArrowRight": case "ArrowDown": next = (index + 1) % steps.length; break;
      case "ArrowLeft": case "ArrowUp": next = (index + steps.length - 1) % steps.length; break;
      case "Home": next = 0; break;
      case "End": next = steps.length - 1; break;
      default: return;
    }
    event.preventDefault(); event.stopPropagation();
    setFocusId(steps[next].id); setSelectedId(steps[next].id);
    buttons.current.get(steps[next].id)?.focus({preventScroll: true});
  }

  return <section className={styles.map} aria-label={label} onClick={event => event.stopPropagation()}>
    <div className={styles.heading}><h4>{label}</h4><span className={styles.position}>{steps.length} 个资料节点</span></div>
    <p id={`${uid}-order`} className={styles.orderNote}>要点直接展示，点节点核对完整依据。连线展示资料关系，不证明因果。</p>
    {steps.length ? <>
      <div ref={graph} className={styles.graph} data-node-count={steps.length}>
        {geometry && <svg className={styles.connectors} aria-hidden="true" focusable="false" width={geometry.width} height={geometry.height} viewBox={`0 0 ${geometry.width} ${geometry.height}`}>
          <defs><marker id={`${uid}-arrow`} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M 0 0 L 6 3 L 0 6" className={styles.arrowHead}/></marker></defs>
          {geometry.paths.map(({d, link}, index) => <path key={`${index}:${link.from}:${link.to}`} d={d} data-kind={link.kind ?? "context"} data-active={selectedId === link.from || selectedId === link.to} markerEnd={links ? `url(#${uid}-arrow)` : undefined}/>) }
        </svg>}
        <div ref={centerElement} className={styles.center} data-node-id={centerId}>
          <span className={styles.centerIcon}><ReadingIcon kind={center?.icon ?? "relation"}/></span>
          <div><strong>{center?.label ?? label}</strong>{center?.value !== undefined && <div className={styles.centerValue}>{center.value}</div>}{edges.some(link => link.to === centerId) && <div className={styles.edgeLabels}>{edges.filter(link => link.to === centerId).map((link, index) => <span key={index} className={styles.edgeLabel} data-kind={link.kind ?? "context"} title={`${steps.find(item => item.id === link.from)?.label} → ${center?.label ?? label}`}><span aria-hidden="true" className={styles.edgeMark}/>{link.label}</span>)}</div>}</div>
        </div>
        <div role="tablist" aria-label={`${label}节点`} aria-describedby={`${uid}-order`} className={styles.branches}>
          {steps.map((step, index) => {
            const incoming = edges.filter(link => link.to === step.id);
            return <button key={step.id} ref={button => {if (button) buttons.current.set(step.id, button); else buttons.current.delete(step.id);}} type="button" role="tab" id={`${uid}-tab-${index}`} aria-selected={selected?.id === step.id} aria-controls={selected?.id === step.id ? `${uid}-content` : undefined} tabIndex={(focusId ?? steps[0]?.id) === step.id ? 0 : -1} aria-label={step.label} aria-describedby={step.summary !== undefined ? `${uid}-summary-${index}` : undefined} className={styles.node} data-action="quiet" data-tone={step.tone ?? "neutral"} data-node-id={step.id} onFocus={() => setFocusId(step.id)} onKeyDown={event => navigate(event, index)} onClick={() => {setFocusId(step.id); setSelectedId(value => value === step.id ? null : step.id);}}>
              <span className={styles.nodeTop}><span className={styles.nodeIcon}><ReadingIcon kind={step.icon}/></span><span className={styles.nodeLabel}>{step.label}</span><svg className={styles.nodeArrow} aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="m9 5 7 7-7 7"/></svg></span>
              {step.summary !== undefined && <span id={`${uid}-summary-${index}`} className={styles.nodeSummary}>{step.summary}</span>}
              {incoming.length > 0 && <span className={styles.edgeLabels}>{incoming.map((link, edgeIndex) => <span key={edgeIndex} className={styles.edgeLabel} data-kind={link.kind ?? "context"} title={`${link.from === centerId ? center?.label ?? label : steps.find(item => item.id === link.from)?.label} → ${step.label}`}><span aria-hidden="true" className={styles.edgeMark}/>{link.label}</span>)}</span>}
            </button>;
          })}
        </div>
      </div>
      {displayed && <div key={displayed.id} id={`${uid}-content`} role={presence.active ? "tabpanel" : undefined} aria-hidden={!presence.active || undefined} inert={!presence.active} aria-labelledby={`${uid}-tab-${steps.findIndex(step => step.id === displayed.id)}`} tabIndex={presence.active ? 0 : -1} className={styles.reading} data-exiting={!presence.active || undefined} data-tone={displayed.tone ?? "neutral"} onKeyDown={event => {
        if (event.key === "Escape") {event.preventDefault(); event.stopPropagation(); closeDetail();}
        else if (event.target !== event.currentTarget && (event.target as Element).closest("a,button,input,select,textarea,[role=button]")) event.stopPropagation();
      }}>
        <div className={styles.detailToolbar}><strong>{displayed.label}</strong><button type="button" className={styles.closeDetail} data-action="quiet" aria-label={`收起${displayed.label}详情`} onClick={closeDetail}><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="m6 6 12 12M18 6 6 18"/></svg><span>收起</span></button></div>
        <ExplanationDetailContext.Provider value={true}>{displayed.content}</ExplanationDetailContext.Provider>
      </div>}
    </> : <p className={styles.empty}>暂无资料节点。有原文后可逐项核对。</p>}
    {footer && <div className={styles.footer}>{footer}</div>}
  </section>;
}

/** Every condition remains fully visible; the card layout does not choose or weaken conditions. */
export function ConditionList({label, items, tone = "neutral"}: {label: string; items: string[]; tone?: "neutral" | "warning"}) {
  if (!items.length) return null;
  return <section className={styles.conditions} aria-label={label} data-tone={tone}><h4>{label}</h4><ul>{items.map((item, index) => <li key={`${index}:${item}`}><span className={styles.conditionIcon}><ReadingIcon kind={tone === "warning" ? "risk" : "check"}/></span><span className={styles.original}>{item}</span></li>)}</ul></section>;
}

export function FactCards({label, items}: {label: string; items: FactItem[]}) {
  if (!items.length) return null;
  return <section className={styles.facts} aria-label={label}><h4>{label}</h4><dl>{items.map((item, index) => <div key={`${index}:${item.label}`}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl></section>;
}

/** Source clocks are shown separately from non-temporal metadata. Values are not inferred. */
export function FactTimeline({label, items}: {label: string; items: FactItem[]}) {
  if (!items.length) return null;
  const timeLabel = /发布|发生|收到|收录|生效|更新|时点|时间|撤回|记录于|日期/;
  const time = items.filter(item => item.kind === "time" || (item.kind !== "fact" && timeLabel.test(item.label)));
  const facts = items.filter(item => !time.includes(item));
  return <section className={styles.timeline} aria-label={label}><h4>{label}</h4>{time.length > 0 && <><p className={styles.orderNote}>按记录口径展示，位置不代表时间先后。</p><dl className={styles.timeTrack}>{time.map((item, index) => <div key={`${index}:${item.label}`} className={styles.timePoint}><dt><span className={styles.timeDot} aria-hidden="true"/><ReadingIcon kind="time"/>{item.label}</dt><dd>{item.value}</dd></div>)}</dl></>}{facts.length > 0 && <FactCards label={time.length ? "记录信息" : "资料信息"} items={facts}/>}</section>;
}

export function SourceClueCards(props: {items: ReadingClue[]; label?: string; identity?: string}) {
  return <SourceClueReader key={props.identity ?? "default"} {...props}/>;
}

function SourceClueReader({items, label = "原文线索"}: {items: ReadingClue[]; label?: string; identity?: string}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const buttonRefs = useRef(new Map<string, HTMLButtonElement>());
  const cards: {id: string; context: string; original: string; qualification: boolean}[] = [];
  for (const clue of items) {
    if (clue.kind === "date") continue;
    const context = readingClueContext(clue);
    const id = `${clue.sourceOffset}:${context}`;
    const found = cards.find(card => card.id === id);
    if (found) found.qualification ||= clue.kind === "qualification";
    else cards.push({id, context, original: clue.sourceText, qualification: clue.kind === "qualification"});
  }
  const selected = cards.find(card => card.id === selectedId);
  const presence = useExitPresence(selected?.id ?? null);
  const displayed = cards.find(card => card.id === presence.value);
  if (selectedId && !selected) setSelectedId(null);
  if (!cards.length) return null;
  return <section className={styles.sourceClues} aria-label={label} onClick={event => event.stopPropagation()}>
    <div className={styles.heading}><h4>{label}</h4><span className={styles.position}>{cards.length} 条原句线索</span></div>
    <p className={styles.orderNote}>数值与限定措辞直接保留原句，未判定条件适用性。点击只查看更长语境。</p>
    <div className={styles.clueCards}>{cards.map(card => <button key={card.id} ref={button => {if (button) buttonRefs.current.set(card.id, button); else buttonRefs.current.delete(card.id);}} type="button" data-action="quiet" aria-expanded={selected?.id === card.id} className={styles.clueCard} data-qualified={card.qualification} title="查看更长原句语境" onClick={() => setSelectedId(value => value === card.id ? null : card.id)}>
      <span className={styles.clueCardIcon}><ReadingIcon kind={card.qualification ? "risk" : "response"}/></span><span className={styles.clueContext}>{card.context}</span><svg aria-hidden="true" className={styles.nodeArrow} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="m9 5 7 7-7 7"/></svg>
    </button>)}</div>
    {displayed && <div className={styles.clueDetail} data-exiting={!presence.active || undefined} role={presence.active ? "region" : undefined} aria-hidden={!presence.active || undefined} inert={!presence.active} aria-label={`${label}对应原句`} onKeyDown={event => {if (event.key === "Escape") {event.preventDefault(); event.stopPropagation(); setSelectedId(null); buttonRefs.current.get(displayed.id)?.focus({preventScroll:true});}}}><div className={styles.detailToolbar}><strong>对应原句 · 未改写</strong><button type="button" className={styles.closeDetail} data-action="quiet" onClick={() => {setSelectedId(null); buttonRefs.current.get(displayed.id)?.focus({preventScroll:true});}} aria-label="收起对应原句"><span>收起</span></button></div><blockquote className={styles.original}>{displayed.original}</blockquote></div>}
  </section>;
}

/** Dates, numbers and qualification words are lexical source clues, not verified conditions. */
export function NarrativeText(props: {text: string; label?: string; identity?: string; compact?: boolean}) {
  const detail = useExplanationDetail();
  if (detail) return <div className={styles.narrative} aria-label={props.label ?? "文字说明"}><div className={styles.original}>{props.text.trim() ? props.text : "未提供原文。"}</div></div>;
  return <NarrativeReader key={props.identity ?? "default"} {...props}/>;
}

/** Source prose remains a literal excerpt, never an inferred account of the event. */
export function SourceExcerpt({text, label = "原文摘录"}: {text: string; label?: string}) {
  const excerpt = firstSourceSentence(text);
  if (!excerpt) return null;
  return <section className={styles.sourceExcerpt} aria-label={label}><h4>{label}</h4><blockquote className={styles.original}>{excerpt}</blockquote></section>;
}

/** Inline paragraph/heading cues retain source order. They do not invent semantic headings. */
export function SourceOutline({paragraphs}: {paragraphs: string[]}) {
  const headings = paragraphs.flatMap(paragraph => paragraph.split(/\r?\n/).filter(line => /^\s*#{1,6}\s+/.test(line)));
  const outline = headings.length ? headings : paragraphs.slice(1).map(firstSourceSentence).filter(Boolean);
  if (!outline.length) return null;
  return <section className={styles.sourceOutline} aria-label="原文大纲"><h4>原文大纲</h4><ul>{outline.map((line, index) => <li key={`${index}:${line}`}>{line}</li>)}</ul></section>;
}

function NarrativeReader({text, label = "文字说明", identity, compact}: {text: string; label?: string; identity?: string; compact?: boolean}) {
  const [full, setFull] = useState(false);
  const clues = readingClues(text);
  if (!text.trim()) return <p className={styles.empty}>未提供原文，暂不能核对线索。</p>;
  if (text.length <= 120) return <div className={styles.narrative} aria-label={label}><div className={styles.original}>{text}</div></div>;
  const hasClues = clues.some(clue => clue.kind !== "date");
  const paragraphs = text.split(/\r?\n[ \t]*\r?\n/).filter(paragraph => paragraph.trim());
  return <div className={styles.narrative} aria-label={label} data-compact={compact || undefined} onClick={event => event.stopPropagation()}>
    <div className={styles.narrativeToolbar}><span className={styles.orderNote}>按原文内容整理，未自动摘要、归因或核实。</span><button type="button" data-action="quiet" className={styles.originalToggle} aria-pressed={full} onClick={() => setFull(value => !value)}>{full ? "返回要点阅读" : "完整原文"}</button></div>
    {full ? <section className={styles.fullText} aria-label={`${label}完整原文`}><h4>{label}</h4><div className={styles.original}>{text}</div></section> : <><SourceExcerpt text={text}/>{hasClues ? <SourceClueCards items={clues} identity={identity}/> : <SourceOutline paragraphs={paragraphs}/>}</>}
  </div>;
}
