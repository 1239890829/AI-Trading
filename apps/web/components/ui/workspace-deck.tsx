"use client";

import { useExitPresence } from "@/hooks/use-exit-presence";
import { useId, useRef, useState, type CSSProperties } from "react";
import Link from "next/link";
import { motionOrigin, type MotionOrigin } from "@/lib/surface-motion";
import { ModalShell } from "@/components/ui/modal-shell";
import { MARKET_LENSES, marketLensUrl, type WorkspaceTool } from "@/lib/workspace-tools";

/** A glass control dock reveals one bounded tray without changing tool identities. */
export function WorkspaceDeck({tools, onOpen, compact = false, activeTool}: {tools: readonly WorkspaceTool[]; onOpen: (key: string, origin: MotionOrigin | null) => void; compact?: boolean; activeTool?: string | null}) {
  const id = useId();
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
  return <div className={`workspace-deck ${compact ? "workspace-deck-compact" : ""}`} data-motion-keyboard={keyboardOpen || undefined}>
    <div className="tool-dock">
      <span className="tool-shelf-title">工具收纳 <span className="shelf-total">{tools.length} 项</span></span>
    <div className="tool-folders" hidden={Boolean(term)}>
      {groups.map((group, index) => {
        const items = tools.filter(tool => tool.group === group);
        const open = visibleGroup === group;
        return <div className="tool-index-slot" data-open={open} key={group}>
          <button id={`${id}-group-${index}`} className="tool-stack" aria-label={group} aria-expanded={open} aria-controls={`${id}-tray-${index}`} aria-describedby={`${id}-description-${index}`} onClick={event => { setKeyboardOpen(event.detail === 0); setExpanded(open ? null : group); }}>
            <span className="tool-stack-heading"><span>{group}</span><span className="folder-count">{items.length}</span></span>
            <span id={`${id}-description-${index}`} className="tool-stack-caption">{items.map(tool => tool.label).join(" · ")}</span>
            <svg className="folder-affordance" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m7 10 5 5 5-5" /></svg>
          </button>
        </div>;
      })}
    </div>
      <div className="tool-shelf-search">
        <label className="sr-only" htmlFor={`${id}-search`}>查找工具</label>
        <svg className="tool-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></svg>
        <input id={`${id}-search`} ref={searchRef} type="search" value={query} placeholder="查找工具" onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === "Escape" && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); clearSearch(); } }} />
        {query && <button type="button" className="tool-search-clear" aria-label="清空工具搜索" onClick={clearSearch}><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg></button>}
      </div>
    </div>
    {term && <div className="tool-search-results" onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); clearSearch(); } }}>
      <p role="status" className="tool-result-count">{matches.length ? `找到 ${matches.length} 项工具` : "没有匹配的工具。请换个关键词，或清空后按用途浏览。"}</p>
      <div className="tool-search-grid">{matches.map(tool => <button key={tool.key} className="tool-launcher" aria-haspopup="dialog" onClick={event => onOpen(tool.key, motionOrigin(event))}><span className="min-w-0"><span className="tool-title">{tool.label}</span><span className="tool-description">{tool.group} · {tool.description}</span></span><span className="tool-open" aria-hidden="true">↗</span></button>)}</div>
      <button className="quiet-action" onClick={clearSearch}>返回工具收纳</button>
    </div>}
    {groups.map((group, index) => <div key={group} id={`${id}-tray-${index}`} hidden={Boolean(term) || visibleGroup !== group} role="region" aria-labelledby={`${id}-group-${index}`} className="tool-tray" onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); setExpanded(null); document.getElementById(`${id}-group-${index}`)?.focus(); }
    }}>
      {visibleGroup === group && tools.filter(tool => tool.group === group).map((tool, toolIndex) => <button key={tool.key} style={{"--tool-order": toolIndex} as CSSProperties} className="tool-launcher" data-selected={activeTool === tool.key || undefined} onClick={event => onOpen(tool.key, motionOrigin(event))} aria-haspopup="dialog">
        <span className="tool-launcher-mark" aria-hidden="true">↗</span><span className="min-w-0"><span className="tool-title">{tool.label}</span><span className="tool-description">{tool.description}</span></span>
        <svg className="tool-open" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10" /></svg>
      </button>)}
    </div>)}
  </div>;
}

export function MarketLensPicker({selected, search}: {selected: string; search: string}) {
  const [open, setOpen] = useState(false);
  const [origin, setOrigin] = useState<MotionOrigin | null>(null);
  const presence = useExitPresence(open ? true : null);
  const current = MARKET_LENSES.find(lens => lens.key === selected) ?? MARKET_LENSES[0];
  return <>
    <button className="lens-trigger" aria-haspopup="dialog" onClick={event => { setOrigin(motionOrigin(event, "capsule")); setOpen(true); }}><span className="lens-dot" aria-hidden="true" /><span><span className="lens-caption">市场观察</span><span className="lens-title">{current.label}</span></span><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m8 10 4 4 4-4"/></svg></button>
    {presence.value && <ModalShell open={presence.active} motionOrigin={origin} label="选择市场观察视角" size="md" onClose={() => setOpen(false)} header={<h2 className="text-lg font-semibold">换一个观察视角</h2>} footer="视角只改变呈现；日期、标的与返回位置继续保留。">
      <div className="lens-library">{[...new Set(MARKET_LENSES.map(lens => lens.group))].map(group => <section key={group}><h3>{group}</h3>{MARKET_LENSES.filter(lens => lens.group === group).map(lens => <Link key={lens.key} href={marketLensUrl(lens.href, search)} aria-current={selected === lens.key ? "page" : undefined} className="lens-choice" onClick={() => setOpen(false)}><strong>{lens.label}</strong><span>{lens.description}</span></Link>)}</section>)}</div>
    </ModalShell>}
  </>;
}
