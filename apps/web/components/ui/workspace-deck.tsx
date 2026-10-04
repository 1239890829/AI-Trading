"use client";

import { useExitPresence } from "@/hooks/use-exit-presence";
import { useState } from "react";
import Link from "next/link";
import { useMaterialTilt } from "@/hooks/use-material-tilt";
import { motionOrigin, type MotionOrigin } from "@/lib/surface-motion";
import { ModalShell } from "@/components/ui/modal-shell";
import { MARKET_LENSES, marketLensUrl, type WorkspaceTool } from "@/lib/workspace-tools";

/** Grouped tools stay dormant until opened; counts represent tools, never market health. */
function ToolStack({group, tools, onOpen, activeTool}: {group: string; tools: readonly WorkspaceTool[]; onOpen: (key: string, origin: MotionOrigin | null) => void; activeTool?: string | null}) {
  const tilt = useMaterialTilt();
  return <details className="tool-stack material-panel" {...tilt}>
    <span className="material-light" aria-hidden="true" />
    <summary className="tool-stack-summary" onKeyDown={() => tilt.onPointerCancel()} onClick={event => { event.currentTarget.parentElement!.dataset.motionInput = event.detail > 0 ? "pointer" : "keyboard"; }}>
      <span className="tool-stack-heading"><span>{group}</span><span aria-label={`${tools.length} 项工具`}>{tools.length.toString().padStart(2, "0")}</span></span>
      <span className="tool-stack-caption">{tools.map(tool => tool.label).join(" / ")}</span>
      <svg className="tray-toggle" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m8 10 4 4 4-4"/></svg>
    </summary>
    <div className="tool-tray">
      {tools.map(tool => <button key={tool.key} className="tool-launcher" data-selected={activeTool === tool.key || undefined} onClick={event => onOpen(tool.key, motionOrigin(event))} aria-haspopup="dialog">
        <span className="tool-mark" aria-hidden="true">{tool.mark}</span>
        <span className="min-w-0"><span className="tool-title">{tool.label}</span><span className="tool-description">{tool.description}</span></span>
        <svg className="tool-open" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10" /></svg>
      </button>)}
    </div>
  </details>;
}

export function WorkspaceDeck({tools, onOpen, compact = false, activeTool}: {tools: readonly WorkspaceTool[]; onOpen: (key: string, origin: MotionOrigin | null) => void; compact?: boolean; activeTool?: string | null}) {
  const groups = [...new Set(tools.map(tool => tool.group))];
  return <div className={`workspace-deck ${compact ? "workspace-deck-compact" : ""}`}>
    {groups.map(group => <ToolStack key={group} group={group} tools={tools.filter(tool => tool.group === group)} onOpen={onOpen} activeTool={activeTool} />)}
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
