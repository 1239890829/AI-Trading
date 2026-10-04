"use client";

import { useExitPresence } from "@/hooks/use-exit-presence";
import { useId, useState, type CSSProperties } from "react";
import { SpatialSurface } from "@/components/ui/spatial-surface";
import Link from "next/link";
import { motionOrigin, type MotionOrigin } from "@/lib/surface-motion";
import { ModalShell } from "@/components/ui/modal-shell";
import { MARKET_LENSES, marketLensUrl, type WorkspaceTool } from "@/lib/workspace-tools";

/** One shared tray keeps every folder aligned. Back plates occupy reserved space. */
export function WorkspaceDeck({tools, onOpen, compact = false, activeTool}: {tools: readonly WorkspaceTool[]; onOpen: (key: string, origin: MotionOrigin | null) => void; compact?: boolean; activeTool?: string | null}) {
  const id = useId();
  const [expanded, setExpanded] = useState<string | null>(null);
  const groups = [...new Set(tools.map(tool => tool.group))];
  const visibleGroup = groups.includes(expanded ?? "") ? expanded : null;
  return <div className={`workspace-deck ${compact ? "workspace-deck-compact" : ""}`}>
    <div className="tool-folders">
      {groups.map((group, index) => {
        const items = tools.filter(tool => tool.group === group);
        const open = visibleGroup === group;
        return <SpatialSurface className="tool-folder" faceClassName="folder-plane" data-open={open} key={group}>
          <span className="folder-layer folder-layer-back" aria-hidden="true" />
          <span className="folder-layer folder-layer-front" aria-hidden="true" />
          <button id={`${id}-group-${index}`} className="tool-stack" aria-label={group} aria-expanded={open} aria-controls={`${id}-tray-${index}`} onClick={() => setExpanded(open ? null : group)}>
            <span className="tool-stack-heading"><span>{group}</span><span className="folder-count" aria-label={`${items.length} 项工具`}>{items.length.toString().padStart(2, "0")}</span></span>
            <span className="tool-stack-caption">{items.map(tool => tool.label).join(" · ")}</span>
            <span className="folder-affordance" aria-hidden="true">{open ? "收起工具" : "展开工具"}<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d={open ? "m6 14 6-6 6 6" : "m6 10 6 6 6-6"}/></svg></span>
          </button>
        </SpatialSurface>;
      })}
    </div>
    {groups.map((group, index) => <div key={group} id={`${id}-tray-${index}`} hidden={visibleGroup !== group} role="region" aria-labelledby={`${id}-group-${index}`} className="tool-tray" onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); setExpanded(null); document.getElementById(`${id}-group-${index}`)?.focus(); }
    }}>
      {visibleGroup === group && tools.filter(tool => tool.group === group).map((tool, toolIndex) => <button key={tool.key} style={{"--tool-order": toolIndex} as CSSProperties} className="tool-launcher" data-selected={activeTool === tool.key || undefined} onClick={event => onOpen(tool.key, motionOrigin(event))} aria-haspopup="dialog">
        <span className="min-w-0"><span className="tool-title">{tool.label}</span><span className="tool-description">{tool.description}</span></span>
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
