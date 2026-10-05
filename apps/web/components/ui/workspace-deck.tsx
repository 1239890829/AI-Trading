"use client";

import { SelectionRail } from "./selection-rail";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";
import { useReducedMotion, useExitPresence } from "@/hooks/use-exit-presence";
import { useId, useRef, useState } from "react";
import Link from "next/link";
import { motionOrigin, type MotionOrigin } from "@/lib/surface-motion";
import { ModalShell } from "@/components/ui/modal-shell";
import { MARKET_LENSES, marketLensUrl, type WorkspaceTool } from "@/lib/workspace-tools";

gsap.registerPlugin(useGSAP);

/** A glass control dock reveals one bounded tray without changing tool identities. */
export function WorkspaceDeck({tools, onOpen, compact = false, activeTool}: {tools: readonly WorkspaceTool[]; onOpen: (key: string, origin: MotionOrigin | null) => void; compact?: boolean; activeTool?: string | null}) {
  const id = useId();
  const deck = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  function clearSearch() {
    setQuery("");
    setKeyboardOpen(true);
    searchRef.current?.focus();
  }
  const term = query.trim().toLocaleLowerCase();
  const matches = term ? tools.filter(tool => `${tool.label} ${tool.description} ${tool.group}`.toLocaleLowerCase().includes(term)) : [];
  const groups = [...new Set(tools.map(tool => tool.group))];
  const visibleGroup = groups.includes(expanded ?? "") ? expanded : null;
  const trayPresence = useExitPresence(term ? null : visibleGroup);
  const shownGroup = trayPresence.value;
  const shownIndex = groups.indexOf(shownGroup ?? "");
  useGSAP(() => {
    if (reduced || keyboardOpen || term || !visibleGroup) return;
    const tray = deck.current?.querySelector<HTMLElement>(".tool-tray:not([aria-hidden=true])");
    if (!tray) return;
    const timeline = gsap.timeline({defaults: {ease: "power3.out"}})
      .fromTo(tray, {y: 5, rotationX: -5, transformPerspective: 700, transformOrigin: "50% 0%", opacity: 0.65}, {y: 0, rotationX: 0, opacity: 1, duration: 0.2, clearProps: "transform,transformOrigin,opacity"})
      .fromTo(tray.querySelectorAll(".tool-launcher"), {y: 8, opacity: 0}, {y: 0, opacity: 1, duration: 0.22, stagger: {amount: 0.06}}, 0.04);
    const cancel = () => timeline.revert();
    deck.current?.addEventListener("keydown", cancel);
    window.addEventListener("resize", cancel);
    const node = deck.current;
    return () => { node?.removeEventListener("keydown", cancel); window.removeEventListener("resize", cancel); };
  }, {scope: deck, dependencies: [visibleGroup, keyboardOpen, term, reduced], revertOnUpdate: true});
  return <div ref={deck} className={`workspace-deck ${compact ? "workspace-deck-compact" : ""}`} data-motion-keyboard={keyboardOpen || undefined}>
    <div className="tool-dock">
      <span className="tool-shelf-title">工具收纳 <span className="shelf-total">{tools.length} 项</span></span>
    <div hidden={Boolean(term)} className="tool-folder-container"><SelectionRail className="tool-folders" activeKey={visibleGroup ?? ""} label="工具用途">
      {groups.map((group, index) => {
        const items = tools.filter(tool => tool.group === group);
        const open = visibleGroup === group;
        return <div className="tool-index-slot" data-open={open} data-tool-count={items.length} key={group}>
          <button id={`${id}-group-${index}`} className="tool-stack" aria-label={group} aria-expanded={open} aria-controls={`${id}-tray`} aria-describedby={`${id}-description-${index}`} onKeyDown={event => { if (event.key === "Escape" && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); setExpanded(null); } }} onClick={event => { setKeyboardOpen(event.detail === 0); setExpanded(open ? null : group); }}>
            <span className="tool-stack-heading"><span>{group}</span><span className="folder-count">{items.length}</span></span>
            <span id={`${id}-description-${index}`} className="tool-stack-caption">{items.map(tool => tool.label).join(" · ")}</span>
            <svg className="folder-affordance" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m7 10 5 5 5-5" /></svg>
          </button>
        </div>;
      })}
    </SelectionRail></div>
      <div className="tool-shelf-search">
        <label className="sr-only" htmlFor={`${id}-search`}>查找工具</label>
        <svg className="tool-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></svg>
        <input id={`${id}-search`} ref={searchRef} type="search" value={query} placeholder="查找工具" onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === "Escape" && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); clearSearch(); } }} />
        {query && <button type="button" className="tool-search-clear" aria-label="清空工具搜索" onClick={clearSearch}><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg></button>}
      </div>
    </div>
    {term && <div className="tool-search-results" onKeyDown={event => { if (event.key === "Escape" && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); clearSearch(); } }}>
      <p role="status" className="tool-result-count">{matches.length ? `找到 ${matches.length} 项工具` : "没有匹配的工具。请换个关键词，或清空后按用途浏览。"}</p>
      <div className="tool-search-grid">{matches.map(tool => <button key={tool.key} className="tool-launcher" aria-haspopup="dialog" onClick={event => onOpen(tool.key, motionOrigin(event))}><span className="min-w-0"><span className="tool-title">{tool.label}</span><span className="tool-description">{tool.group} · {tool.description}</span></span><span className="tool-open" aria-hidden="true">↗</span></button>)}</div>
      <button className="quiet-action" onClick={clearSearch}>返回工具收纳</button>
    </div>}
    {shownGroup && <div id={`${id}-tray`} role="region" aria-labelledby={`${id}-group-${shownIndex}`} aria-hidden={!trayPresence.active || undefined} inert={!trayPresence.active} data-exiting={!trayPresence.active || undefined} className="tool-tray" onKeyDown={event => {
      if (event.key === "Escape" && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); setExpanded(null); document.getElementById(`${id}-group-${shownIndex}`)?.focus(); }
    }}>
      {tools.filter(tool => tool.group === shownGroup).map(tool => <button key={tool.key} className="tool-launcher" data-selected={activeTool === tool.key || undefined} onClick={event => onOpen(tool.key, motionOrigin(event))} aria-haspopup="dialog">
        <span className="tool-launcher-mark" aria-hidden="true">↗</span><span className="min-w-0"><span className="tool-title">{tool.label}</span><span className="tool-description">{tool.description}</span></span>
        <svg className="tool-open" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10" /></svg>
      </button>)}
    </div>}

  </div>;
}

export function MarketLensPicker({selected, search}: {selected: string; search: string}) {
  const [open, setOpen] = useState(false);
  const [origin, setOrigin] = useState<MotionOrigin | null>(null);
  const presence = useExitPresence(open ? true : null);
  const current = MARKET_LENSES.find(lens => lens.key === selected) ?? MARKET_LENSES[0];
  return <>
    <button className="lens-trigger" aria-haspopup="dialog" aria-expanded={open} onClick={event => { setOrigin(motionOrigin(event, "capsule")); setOpen(true); }}><span className="lens-dot" aria-hidden="true" /><span><span className="lens-caption">市场观察</span><span className="lens-title">{current.label}</span></span><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m8 10 4 4 4-4"/></svg></button>
    {presence.value && <ModalShell open={presence.active} motionOrigin={origin} label="选择市场观察视角" size="md" onClose={() => setOpen(false)} header={<h2 className="text-lg font-semibold">换一个观察视角</h2>} footer="视角只改变呈现；日期、标的与返回位置继续保留。">
      <div className="lens-library">{[...new Set(MARKET_LENSES.map(lens => lens.group))].map(group => <section key={group}><h3>{group}</h3>{MARKET_LENSES.filter(lens => lens.group === group).map(lens => <Link key={lens.key} href={marketLensUrl(lens.href, search)} aria-current={selected === lens.key ? "page" : undefined} className="lens-choice" onClick={() => setOpen(false)}><strong>{lens.label}</strong><span>{lens.description}</span><span className="lens-selection" aria-hidden="true">{selected === lens.key ? "✓" : "↗"}</span></Link>)}</section>)}</div>
    </ModalShell>}
  </>;
}
