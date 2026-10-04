"use client";

import { useEffect, useRef, type PointerEvent } from "react";
import { useReducedMotion } from "@/hooks/use-exit-presence";
import { SURFACE_EASE } from "@/lib/surface-motion";

/** One frame per active pointer; no idle loop and no financial text interpolation. */
export function useMaterialTilt() {
  const ref = useRef<HTMLDetailsElement>(null);
  const reduced = useReducedMotion();
  const frame = useRef<number | null>(null);
  const rect = useRef<DOMRect | null>(null);
  const animation = useRef<Animation | null>(null);
  function reset(spring = true) {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
    rect.current = null;
    const el = ref.current;
    if (!el) return;
    animation.current?.cancel();
    const from = el.style.transform;
    el.style.transform = "";
    delete el.dataset.materialActive;
    if (spring && !reduced && from && typeof el.animate === "function") {
      animation.current = el.animate([{ transform: from }, { transform: "perspective(900px) rotateX(-.3deg) rotateY(.3deg)", offset: .72 }, { transform: "none" }], { duration: 260, easing: SURFACE_EASE });
    }
  }
  useEffect(() => {
    const el = ref.current;
    if (reduced && el) { el.style.transform = ""; delete el.dataset.materialActive; }
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      animation.current?.cancel();
      if (el) { el.style.transform = ""; delete el.dataset.materialActive; }
      frame.current = null;
      rect.current = null;
    };
  }, [reduced]);
  function move(event: PointerEvent<HTMLDetailsElement>) {
    if (reduced || event.isPrimary === false || (event.pointerType === "touch" && !event.buttons)) return;
    const el = event.currentTarget;
    rect.current ??= el.getBoundingClientRect();
    const r = rect.current;
    if (!r.width || !r.height) return;
    const x = Math.max(-1, Math.min(1, (event.clientX - r.left) / r.width * 2 - 1));
    const y = Math.max(-1, Math.min(1, (event.clientY - r.top) / r.height * 2 - 1));
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      animation.current?.cancel();
      el.style.transform = `perspective(900px) rotateX(${-y * 2.5}deg) rotateY(${x * 2.5}deg)`;
      el.dataset.materialActive = "true";
      const light = el.querySelector<HTMLElement>(".material-light");
      if (light) light.style.transform = `translate(${x * 24}%, ${y * 24}%)`;
    });
  }
  return { ref, onPointerDown: move, onPointerMove: move, onPointerLeave: () => reset(), onPointerUp: (event: PointerEvent<HTMLDetailsElement>) => { if (event.pointerType === "touch") reset(); }, onPointerCancel: () => reset(false) };
}
