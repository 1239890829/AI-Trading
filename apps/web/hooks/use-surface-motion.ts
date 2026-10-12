"use client";

import { useCallback, useLayoutEffect, useRef, type RefObject } from "react";
import { useReducedMotion } from "@/hooks/use-exit-presence";
import { SURFACE_EASE, surfaceTransform, type MotionOrigin } from "@/lib/surface-motion";

type SurfaceFrame = { transform: string; opacity: string | number; borderRadius: string };

/** One visual owner per panel. Focus, commands and reader mounting stay immediate. */
export function useSurfaceMotion(ref: RefObject<HTMLDivElement | null>, open: boolean, origin: MotionOrigin | null | undefined, layout: string) {
  const reduced = useReducedMotion();
  const owned = !reduced && typeof HTMLElement !== "undefined" && typeof HTMLElement.prototype.animate === "function";
  const previous = useRef<DOMRect | null>(null);
  const animation = useRef<Animation | null>(null);
  const interrupted = useRef<SurfaceFrame | null>(null);
  const state = useRef<{open: boolean; layout: string} | null>(null);
  const keyboard = useRef(false);
  const prepare = useCallback((animate = true) => {
    const panel = ref.current, current = animation.current;
    previous.current = animate && !reduced ? panel?.getBoundingClientRect() ?? null : null;
    interrupted.current = null;
    if (current) {
      if (animate && !reduced && panel && current.playState !== "finished") {
        const style = getComputedStyle(panel);
        interrupted.current = {transform:style.transform || "none", opacity:style.opacity || 1, borderRadius:style.borderRadius};
      }
      // A grip drag must take ownership immediately, before its first move.
      current.cancel(); animation.current = null;
    }
  }, [ref, reduced]);

  useLayoutEffect(() => {
    function key(event: KeyboardEvent) {
      if (!event.isComposing) keyboard.current = true;
    }
    function pointer() { keyboard.current = false; }
    window.addEventListener("keydown", key, true);
    window.addEventListener("pointerdown", pointer, true);
    return () => { window.removeEventListener("keydown", key, true); window.removeEventListener("pointerdown", pointer, true); };
  }, []);

  useLayoutEffect(() => {
    const panel = ref.current;
    if (!panel) return;
    const prior = state.current;
    state.current = {open, layout};
    const fromLayout = previous.current;
    previous.current = null;
    const resumed = interrupted.current;
    interrupted.current = null;
    if (!owned) return;
    const changing = !prior || prior.open !== open || prior.layout !== layout || fromLayout || resumed;
    if (!changing) return;

    const source = origin?.element;
    const sourceBounds = source?.isConnected ? source.getBoundingClientRect() : null;
    const sourceRect = sourceBounds && sourceBounds.width > 0 && sourceBounds.height > 0 && sourceBounds.right > 0 && sourceBounds.bottom > 0 && sourceBounds.left < window.innerWidth && sourceBounds.top < window.innerHeight ? sourceBounds : null;
    const target = panel.getBoundingClientRect();
    const spatial = origin !== null && !keyboard.current;
    const radius = getComputedStyle(panel).borderRadius;
    const sourceRadius = origin?.kind === "capsule" ? "999px" : "14px";
    const geometry = spatial && surfaceTransform(!open ? sourceRect || target : fromLayout || sourceRect || target, target);
    const hasGeometry = !!geometry && !!(!open ? sourceRect : fromLayout || sourceRect);
    const compactMove = !spatial ? "none" : panel.classList.contains("motion-drawer") ? (window.innerWidth < 640 ? "translateY(16px)" : "translateX(16px)") : "scale(.98)";
    const rest: SurfaceFrame = {transform:"none", opacity:1, borderRadius:radius};
    const away: SurfaceFrame = {transform:hasGeometry && geometry ? geometry : compactMove, opacity:open && hasGeometry ? .4 : 0, borderRadius:hasGeometry ? sourceRadius : radius};
    const frames = open ? [resumed || away, rest] : [resumed || rest, away];
    const current = panel.animate(frames, {duration:open ? (hasGeometry ? 260 : 200) : 150, easing:SURFACE_EASE, fill:"none"});
    animation.current = current;
    return () => {
      if (animation.current === current && current.playState !== "finished") {
        const style = getComputedStyle(panel);
        interrupted.current = {transform:style.transform || "none", opacity:style.opacity || 1, borderRadius:style.borderRadius};
      }
      current.cancel();
      if (animation.current === current) animation.current = null;
    };
  }, [open, origin, layout, owned, ref]);

  return { prepare, reduced, owned };
}
