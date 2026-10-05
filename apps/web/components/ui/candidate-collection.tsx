"use client";

import { Children, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { gsap } from "gsap";
import { Flip } from "gsap/Flip";
import { useGSAP } from "@gsap/react";
import { SelectionRail } from "./selection-rail";
import { useReducedMotion } from "@/hooks/use-exit-presence";

gsap.registerPlugin(Flip, useGSAP);

/** Animate the outer layout only; each candidate keeps its state and inner 3D face. */
export function CandidateCollection({children}: {children: ReactNode}) {
  const [view, setView] = useState<"cards" | "list">("cards");
  const root = useRef<HTMLDivElement>(null);
  const before = useRef<Flip.FlipState | null>(null);
  const reduced = useReducedMotion();
  useGSAP(() => {
    const state = before.current;
    before.current = null;
    if (!state || reduced) return;
    const animation = Flip.from(state, {duration: 0.26, ease: "power3.out", scale: true, nested: true});
    // A viewport change invalidates the captured geometry. Restore live layout immediately.
    const cancel = () => animation.revert();
    window.addEventListener("resize", cancel);
    return () => window.removeEventListener("resize", cancel);
  }, {scope: root, dependencies: [view, reduced], revertOnUpdate: true});

  function choose(next: "cards" | "list", event: MouseEvent<HTMLButtonElement>) {
    if (next === view) return;
    const items = root.current?.querySelectorAll<HTMLElement>("[data-collection-item]");
    if (items) {
      // Freeze the current visual position before capturing a rapid reversal.
      Flip.killFlipsOf(items, false);
      const visible = Array.from(items).filter(item => {
        const box = item.getBoundingClientRect();
        return box.width > 0 && box.height > 0;
      });
      before.current = !reduced && event.detail > 0 && visible.length ? Flip.getState(visible) : null;
    }
    setView(next);
  }

  return <div className="candidate-collection">
    <div className="collection-toolbar"><span>按形成依据逐项核对</span><SelectionRail activeKey={view} label="候选呈现方式"><button type="button" aria-pressed={view === "cards"} onClick={event => choose("cards", event)}>卡片</button><button type="button" aria-pressed={view === "list"} onClick={event => choose("list", event)}>列表</button></SelectionRail></div>
    <div ref={root} className="collection-items" data-view={view}>{Children.toArray(children).map(child => <div key={typeof child === "object" && child && "key" in child ? child.key : String(child)} data-collection-item={String(typeof child === "object" && child && "key" in child ? child.key : child)} style={{transformOrigin: "top left"}}>{child}</div>)}</div>
  </div>;
}
