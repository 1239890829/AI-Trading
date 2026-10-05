"use client";

import { useCallback, useLayoutEffect, useRef, type ReactNode } from "react";

/** A visual marker only. Native buttons/links own selection, focus and commands. */
export function SelectionRail({activeKey, label, className = "", role = "group", children}: {activeKey: string; label: string; className?: string; role?: "group" | "tablist"; children: ReactNode}) {
  const root = useRef<HTMLDivElement>(null);
  const marker = useRef<HTMLSpanElement>(null);
  const measure = useCallback(() => {
    const node = root.current;
    const line = marker.current;
    if (!node || !line) return;
    const selected = node.querySelector<HTMLElement>('[aria-pressed="true"],[aria-current="page"],[aria-expanded="true"],[aria-selected="true"]');
    if (!selected) { line.style.opacity = "0"; return; }
    const box = selected.getBoundingClientRect();
    const container = node.getBoundingClientRect();
    const width = Math.max(0, box.width - 16);
    line.style.transform = `translateX(${box.left - container.left + node.scrollLeft - node.clientLeft + 8}px) scaleX(${width})`;
    line.style.opacity = box.width > 0 ? "1" : "0";
  }, []);
  useLayoutEffect(() => { measure(); }, [activeKey, measure]);
  useLayoutEffect(() => {
    const node = root.current;
    if (!node) return;
    // One observer lifetime: recreating it on selection emits an initial resize
    // notification that would cancel every pointer transition as it starts.
    const resize = () => { node.dataset.motionImmediate = "true"; measure(); };
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(resize) : null;
    observer?.observe(node);
    for (const item of node.querySelectorAll("button,a")) observer?.observe(item);
    window.addEventListener("resize", resize);
    return () => { observer?.disconnect(); window.removeEventListener("resize", resize); };
  }, [measure]);
  return <div ref={root} role={role} aria-label={label} className={`selection-rail ${className}`} data-motion-immediate="true"
    onKeyDownCapture={() => { if (root.current) root.current.dataset.motionImmediate = "true"; }}
    onClickCapture={event => { if (root.current) root.current.dataset.motionImmediate = String(event.detail === 0); }}>
    <span ref={marker} className="selection-line" aria-hidden="true" />{children}
  </div>;
}
