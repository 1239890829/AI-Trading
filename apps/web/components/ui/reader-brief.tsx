"use client";

import {useId, useState, type ReactNode} from "react";
import {useExitPresence} from "@/hooks/use-exit-presence";
import {readerExcerpt, readerCautions} from "@/lib/reader-copy";
import styles from "./reader-brief.module.css";

/** A reading hierarchy, not a causal graph. Fields stand independently. */
export function ReaderBrief({label, headline, facts = [], cautions = [], children}: {
  label: string; headline?: ReactNode; facts?: {label: string; value: ReactNode}[];
  cautions?: string[]; children?: ReactNode;
}) {
  return <section className={styles.brief} aria-label={label}>
    {headline && <p className={styles.headline}>{headline}</p>}
    {!!facts.length && <dl className={styles.facts}>{facts.map((fact, index) => <div key={`${fact.label}:${index}`}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>}
    {!!cautions.length && <ul aria-label="需要留意" className={styles.cautions}>{[...new Set(cautions)].map(value => <li key={value}>{value}</li>)}</ul>}
    {children}
  </section>;
}

/** One optional door for complete records. Identity changes retire the open record. */
export function ResearchDisclosure({children, label = "完整依据与研究记录", identity, defaultOpen = false}: {
  children: ReactNode; label?: string; identity: string; defaultOpen?: boolean;
}) {
  const id = useId();
  const [shown, setShown] = useState<string | null>(defaultOpen ? identity : null);
  if (shown !== null && shown !== identity) setShown(null);
  const open = shown === identity;
  const presence = useExitPresence(open ? identity : null);
  return <div className={styles.disclosure} onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
    <button type="button" className={styles.toggle} aria-expanded={open} aria-controls={id} onClick={() => setShown(open ? null : identity)}>
      <span>{label}</span><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className={open ? styles.turned : undefined}><path d="m6 9 6 6 6-6"/></svg>
    </button>
    <div className="motion-disclosure" data-open={open} aria-hidden={!open || undefined} inert={!open} id={id}><div className="motion-disclosure-body">{presence.value === identity && <div className={styles.records}>{children}</div>}</div></div>
  </div>;
}

/** For existing prose-only consumers. Complete text is reachable with one action. */
export function PlainExplanation({text, label = "说明", identity}: {text: string; label?: string; identity?: string; compact?: boolean}) {
  return <div className={styles.brief}>
    <ReaderBrief label={`${label}简述`} headline={readerExcerpt(text) || "本记录未提供说明"} cautions={readerCautions([text])}/>
    {text.trim() && <ResearchDisclosure label={`${label}完整内容`} identity={`${identity || ""}:${text}`}><p className="whitespace-pre-wrap">{text}</p></ResearchDisclosure>}
  </div>;
}
