"use client";

import { useCallback, useLayoutEffect, useRef, type ReactNode } from "react";

type MarkerRead = () => (() => void) | undefined;
const pendingReads = new Set<MarkerRead>();
let markerFrame: number | undefined;

// Rails often mount together inside a large chart/detail tree. Read all geometry
// after the commit, then write all markers, rather than forcing layout per rail.
function scheduleMarker(read: MarkerRead) {
  pendingReads.add(read);
  if (markerFrame !== undefined) return;
  markerFrame = requestAnimationFrame(() => {
    markerFrame = undefined;
    const reads = [...pendingReads];
    pendingReads.clear();
    const writes = reads.map(read => read());
    for (const write of writes) write?.();
  });
}
function cancelMarker(read: MarkerRead) {
  pendingReads.delete(read);
  if (!pendingReads.size && markerFrame !== undefined) {
    cancelAnimationFrame(markerFrame);
    markerFrame = undefined;
  }
}

/** A visual marker only. Native buttons/links own selection, focus and commands. */
export function SelectionRail({activeKey, label, className = "", role = "group", indicator = true, children}: {activeKey: string; label: string; className?: string; role?: "group" | "tablist"; indicator?: boolean; children: ReactNode}) {
  const root = useRef<HTMLDivElement>(null);
  const marker = useRef<HTMLSpanElement>(null);
  const immediateResize = useRef(false);
  const measure = useCallback(() => {
    const node = root.current;
    const line = marker.current;
    if (!node || !line) return;
    const selected = node.querySelector<HTMLElement>('[aria-pressed="true"],[aria-current="page"],[aria-expanded="true"],[aria-selected="true"]');
    if (!selected) return () => { immediateResize.current = false; line.style.opacity = "0"; };
    const box = selected.getBoundingClientRect();
    const container = node.getBoundingClientRect();
    const width = Math.max(0, box.width - 16);
    const x = box.left - container.left + node.scrollLeft - node.clientLeft + 8;
    return () => {
      if (immediateResize.current) { node.dataset.motionImmediate = "true"; immediateResize.current = false; }
      line.style.transform = `translateX(${x}px) scaleX(${width})`;
      line.style.opacity = box.width > 0 ? "1" : "0";
    };
  }, []);
  useLayoutEffect(() => {
    if (indicator) scheduleMarker(measure);
    return () => cancelMarker(measure);
  }, [activeKey, indicator, measure]);
  useLayoutEffect(() => {
    const node = root.current;
    if (!node || !indicator) return;
    // One observer lifetime: recreating it on selection emits an initial resize
    // notification that would cancel every pointer transition as it starts.
    const resize = () => { immediateResize.current = true; scheduleMarker(measure); };
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(resize) : null;
    observer?.observe(node);
    for (const item of node.querySelectorAll("button,a")) observer?.observe(item);
    window.addEventListener("resize", resize);
    return () => { cancelMarker(measure); observer?.disconnect(); window.removeEventListener("resize", resize); };
  }, [indicator, measure]);
  return <div ref={root} role={role} aria-label={label} className={`selection-rail ${className}`} data-motion-immediate="true"
    onKeyDownCapture={() => { if (root.current) root.current.dataset.motionImmediate = "true"; }}
    onClickCapture={event => { if (root.current) root.current.dataset.motionImmediate = String(event.detail === 0); }}>
    {indicator && <span ref={marker} className="selection-line" aria-hidden="true" />}{children}
  </div>;
}
