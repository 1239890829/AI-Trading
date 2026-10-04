"use client";

import { useEffect, useRef, type PointerEvent, type RefObject, type Dispatch, type SetStateAction } from "react";
import { drawerSnap, drawerTravel, SURFACE_EASE } from "@/lib/surface-motion";

export function useDrawerGesture(panel: RefObject<HTMLDivElement | null>, open: boolean, compact: boolean, setCompact: Dispatch<SetStateAction<boolean>>, prepare: (animate?: boolean) => void, reduced: boolean) {
  const drag = useRef<{ id: number; start: number; last: number; time: number; velocity: number; handle: HTMLElement } | null>(null);
  const animation = useRef<Animation | null>(null);
  const suppressClick = useRef(false);
  useEffect(() => {
    const el = panel.current;
    return () => {
      const active = drag.current;
      if (active?.handle.hasPointerCapture?.(active.id)) active.handle.releasePointerCapture(active.id);
      drag.current = null;
      animation.current?.cancel();
      if (el) { el.style.removeProperty("transform"); el.style.removeProperty("transition"); }
    };
  }, [open, reduced, panel]);
  function restore() {
    const el = panel.current;
    if (!el) return;
    const from = el.style.transform;
    el.style.removeProperty("transform");
    el.style.removeProperty("transition");
    if (!reduced && from && typeof el.animate === "function") {
      animation.current = el.animate([{ transform: from }, { transform: "translateY(-2px)", offset: .78 }, { transform: "none" }], { duration: 240, easing: SURFACE_EASE });
    }
  }
  function release(event: PointerEvent<HTMLElement>, canceled = false) {
    const active = drag.current;
    if (!active || active.id !== event.pointerId) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture?.(active.id)) event.currentTarget.releasePointerCapture(active.id);
    suppressClick.current = canceled || Math.abs(event.clientY - active.start) > 4;
    if (canceled) { restore(); return; }
    const delta = event.clientY - active.start;
    // A pause before release is not a flick; don't reuse an old high velocity.
    const velocity = event.timeStamp - active.time < 90 ? active.velocity : 0;
    const next = drawerSnap(compact, delta, velocity);
    if (next === compact) { restore(); return; }
    prepare(!reduced);
    if (panel.current) { panel.current.style.removeProperty("transform"); panel.current.style.removeProperty("transition"); }
    setCompact(next);
  }
  return {
    compact,
    toggle(animate: boolean) {
      if (animate && suppressClick.current) { suppressClick.current = false; return; }
      prepare(animate); setCompact(value => !value);
    },
    handlers: {
      onPointerDown(event: PointerEvent<HTMLElement>) {
        if (!open || drag.current || event.isPrimary === false || event.button !== 0 || !window.matchMedia?.("(max-width: 639px)").matches) return;
        prepare(false);
        suppressClick.current = false;
        animation.current?.cancel();
        drag.current = { id: event.pointerId, start: event.clientY, last: event.clientY, time: event.timeStamp, velocity: 0, handle: event.currentTarget };
        event.currentTarget.setPointerCapture?.(event.pointerId);
        if (panel.current) panel.current.style.setProperty("transition", "none");
      },
      onPointerMove(event: PointerEvent<HTMLElement>) {
        const active = drag.current;
        if (!active || active.id !== event.pointerId) return;
        const elapsed = event.timeStamp - active.time;
        if (elapsed > 0) active.velocity = (event.clientY - active.last) / elapsed;
        active.last = event.clientY;
        active.time = event.timeStamp;
        if (panel.current && !reduced) panel.current.style.setProperty("transform", `translateY(${drawerTravel(event.clientY - active.start, compact)}px)`);
      },
      onPointerUp: (event: PointerEvent<HTMLElement>) => release(event),
      onPointerCancel: (event: PointerEvent<HTMLElement>) => release(event, true),
      onLostPointerCapture: (event: PointerEvent<HTMLElement>) => release(event, true),
    },
  };
}
