"use client";

import {useId, useRef, useState, type KeyboardEvent, type ReactNode} from "react";
import styles from "./explanation-map.module.css";

type ExplanationIcon = "source" | "relation" | "response" | "time" | "risk" | "check";
type ExplanationTone = "neutral" | "warning" | "positive" | "negative";

export type ExplanationStep = {
  id: string;
  label: string;
  summary?: ReactNode;
  content: ReactNode;
  icon?: ExplanationIcon;
  tone?: ExplanationTone;
};

function ReadingIcon({kind = "source"}: {kind?: ExplanationIcon}) {
  return <svg aria-hidden="true" focusable="false" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    {kind === "source" && <><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8Z"/><path d="M14 3v5h5M9 12h6M9 16h6"/></>}
    {kind === "relation" && <><circle cx="5" cy="12" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="m7 11 10-4M7 13l10 4"/></>}
    {kind === "response" && <><path d="M4 19h16M6 15v-4M12 15V5M18 15V8"/><path d="m4 7 4-3 4 2 7-3"/></>}
    {kind === "time" && <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>}
    {kind === "risk" && <><path d="M10.3 4.6a2 2 0 0 1 3.4 0l7 12a2 2 0 0 1-1.7 3H5a2 2 0 0 1-1.7-3Z"/><path d="M12 9v4M12 16h.01"/></>}
    {kind === "check" && <><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></>}
  </svg>;
}

type ExplanationMapProps = {label: string; steps: ExplanationStep[]; identity?: string; footer?: ReactNode};

/** A reading path, never a generated summary or a claim that the links prove causality. */
export function ExplanationMap(props: ExplanationMapProps) {
  // Object changes remount the reader before commit. Old content cannot survive an exit animation.
  return <ExplanationReader key={props.identity ?? "default"} {...props}/>;
}

function ExplanationReader({label, steps, footer}: ExplanationMapProps) {
  const uid = useId();
  const [selectedId, setSelectedId] = useState(steps[0]?.id ?? null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const selected = steps.find(step => step.id === selectedId) ?? steps[0];

  // A removed node must not silently reopen if a later refresh reintroduces it.
  if ((selected?.id ?? null) !== selectedId) setSelectedId(selected?.id ?? null);

  function navigate(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    switch (event.key) {
      case "ArrowRight": case "ArrowDown": next = (index + 1) % steps.length; break;
      case "ArrowLeft": case "ArrowUp": next = (index + steps.length - 1) % steps.length; break;
      case "Home": next = 0; break;
      case "End": next = steps.length - 1; break;
      default: return;
    }
    event.preventDefault();
    event.stopPropagation();
    setSelectedId(steps[next].id);
    buttons.current.get(steps[next].id)?.focus({preventScroll: true});
  }

  return <section className={styles.map} aria-label={label} onClick={event => event.stopPropagation()}>
    <div className={styles.heading}><h4>{label}</h4>{steps.length > 1 && <span className={styles.position}>{steps.findIndex(step => step.id === selected?.id) + 1} / {steps.length}</span>}</div>
    {steps.length > 1 && <p id={`${uid}-order`} className={styles.orderNote}>连线表示资料阅读顺序，不代表已证实的因果。</p>}
    {steps.length ? <>
      <div role="tablist" aria-label={`${label}阅读目录`} aria-describedby={steps.length > 1 ? `${uid}-order` : undefined} className={styles.path}>
        {steps.map((step, index) => <button key={step.id} ref={button => {if (button) buttons.current.set(step.id, button); else buttons.current.delete(step.id);}} type="button" role="tab" id={`${uid}-tab-${index}`} aria-selected={selected?.id === step.id} aria-controls={`${uid}-content`} tabIndex={selected?.id === step.id ? 0 : -1} aria-label={step.label} className={styles.node} data-action="quiet" data-tone={step.tone ?? "neutral"} onKeyDown={event => navigate(event, index)} onClick={() => setSelectedId(step.id)}>
          <span className={styles.nodeTop}><span className={styles.nodeIcon}><ReadingIcon kind={step.icon}/></span><span className={styles.nodeLabel}>{step.label}</span></span>
          {step.summary !== undefined && <span className={styles.nodeSummary}>{step.summary}</span>}
        </button>)}
      </div>
      <div key={selected.id} id={`${uid}-content`} role="tabpanel" aria-labelledby={`${uid}-tab-${steps.findIndex(step => step.id === selected.id)}`} tabIndex={0} className={styles.reading} data-tone={selected.tone ?? "neutral"} onKeyDown={event => {
        if (event.target !== event.currentTarget && (event.target as Element).closest("a,button,input,select,textarea,[role=button]")) event.stopPropagation();
      }}>
        {selected.content}
      </div>
    </> : <p className={styles.empty}>暂无可阅读资料。</p>}
    {footer && <div className={styles.footer}>{footer}</div>}
  </section>;
}

/** Preserve every supplied condition. No importance ranking or truncation is inferred. */
export function ConditionList({label, items, tone = "neutral"}: {label: string; items: string[]; tone?: "neutral" | "warning"}) {
  if (!items.length) return null;
  return <section className={styles.conditions} aria-label={label} data-tone={tone}>
    <h4>{label}</h4>
    <ul>{items.map((item, index) => <li key={`${index}:${item}`}><span className={styles.conditionIcon}><ReadingIcon kind={tone === "warning" ? "risk" : "check"}/></span><span className={styles.original}>{item}</span></li>)}</ul>
  </section>;
}

/** Split only at original paragraph or sentence boundaries and keep their exact characters. */
function readingParts(text: string): string[] {
  const paragraphs: string[] = [];
  const paragraphBreak = /\r?\n[ \t]*\r?\n/g;
  let start = 0;
  for (const match of text.matchAll(paragraphBreak)) {
    const end = match.index! + match[0].length;
    paragraphs.push(text.slice(start, end));
    start = end;
  }
  if (start < text.length) paragraphs.push(text.slice(start));

  const parts: string[] = [];
  let whitespace = "";
  for (const original of paragraphs) {
    // Whitespace remains in the original text, but is not a blank navigation node.
    if (!original.trim()) {whitespace += original; continue;}
    const paragraph = whitespace + original;
    whitespace = "";
    if (paragraph.length <= 360) {parts.push(paragraph); continue;}
    // Decimal points, commas and semicolons are not sentence boundaries.
    const sentenceEnd = /[。！？!?]+[”’」』）)\]"]*/g;
    let from = 0;
    for (const match of paragraph.matchAll(sentenceEnd)) {
      const end = match.index! + match[0].length;
      if (end - from >= 240) {parts.push(paragraph.slice(from, end)); from = end;}
    }
    if (from < paragraph.length) parts.push(paragraph.slice(from));
  }
  if (whitespace && parts.length) parts[parts.length - 1] += whitespace;
  if (parts.length > 8) {
    const groupSize = Math.ceil(parts.length / 6);
    return Array.from({length: Math.ceil(parts.length / groupSize)}, (_, index) => parts.slice(index * groupSize, (index + 1) * groupSize).join(""));
  }
  return parts;
}

export function NarrativeText(props: {text: string; label?: string; identity?: string}) {
  return <NarrativeReader key={props.identity ?? "default"} {...props}/>;
}

function NarrativeReader({text, label = "文字说明", identity}: {text: string; label?: string; identity?: string}) {
  const [full, setFull] = useState(false);
  const parts = readingParts(text);
  if (!text.trim()) return <p className={styles.empty}>未提供原文。</p>;
  if (text.length <= 360 && parts.length <= 3) return <div className={styles.narrative} aria-label={label}><div className={styles.original}>{text}</div></div>;
  // A single unbroken paragraph remains whole; splitting a condition mid-sentence is unsafe.
  if (parts.length <= 1) return <div className={styles.narrative} aria-label={label}><div className={styles.original}>{text}</div></div>;

  const switcher = <button type="button" data-action="quiet" className={styles.originalToggle} aria-pressed={full} onClick={event => {event.stopPropagation(); setFull(value => !value);}}>{full ? "返回分段阅读" : "完整原文"}</button>;
  return <div className={styles.narrative}>
    <div className={styles.narrativeToolbar}><span className={styles.orderNote}>分段展示原文，未自动摘要或改写。</span>{switcher}</div>
    {full ? <section className={styles.fullText} aria-label={`${label}完整原文`}><h4>{label}</h4><div className={styles.original}>{text}</div></section> : <ExplanationMap label={label} identity={identity} steps={parts.map((part, index) => ({id: `part-${index}`, label: `片段 ${index + 1}`, summary: part.trim().slice(0, 36) + (part.trim().length > 36 ? "…" : ""), content: <div className={styles.original}>{part}</div>, icon: "source"}))}/>}
  </div>;
}

/** A compact source-time track; values and unknowns belong to the caller. */
export function FactTimeline({label, items}: {label: string; items: {label: string; value: ReactNode}[]}) {
  if (!items.length) return null;
  return <section className={styles.timeline} aria-label={label}><h4>{label}</h4><dl>{items.map((item, index) => <div key={`${index}:${item.label}`} className={styles.timePoint}><dt><span className={styles.timeDot} aria-hidden="true"/>{item.label}</dt><dd>{item.value}</dd></div>)}</dl></section>;
}
