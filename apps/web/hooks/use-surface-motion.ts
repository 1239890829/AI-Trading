"use client";

import { useCallback, useLayoutEffect, useRef, type RefObject } from "react";
import { useReducedMotion } from "@/hooks/use-exit-presence";
import { SURFACE_EASE, surfaceTransform, type MotionOrigin } from "@/lib/surface-motion";

/** Animate geometry only. State, focus and business mounting remain immediate. */
export function useSurfaceMotion(ref: RefObject<HTMLDivElement | null>, open: boolean, origin: MotionOrigin | null | undefined, layout: string) {
  const reduced = useReducedMotion();
  const previous = useRef<DOMRect | null>(null);
  const animation = useRef<Animation | null>(null);
  const started = useRef(false);
  const keyboard = useRef(false);
  const prepare = useCallback((animate = true) => {
    previous.current = animate && !reduced ? ref.current?.getBoundingClientRect() ?? null : null;
    animation.current?.cancel();
  }, [ref, reduced]);

  useLayoutEffect(() => {
    function key(event: KeyboardEvent) {
      if (event.isComposing) return;
      keyboard.current = true;
      animation.current?.cancel();
      ref.current?.setAttribute("data-motion-keyboard", "true");
    }
    function pointer() {
      keyboard.current = false;
      ref.current?.removeAttribute("data-motion-keyboard");
    }
    window.addEventListener("keydown", key, true);
    window.addEventListener("pointerdown", pointer, true);
    return () => { window.removeEventListener("keydown", key, true); window.removeEventListener("pointerdown", pointer, true); };
  }, [ref]);

  useLayoutEffect(() => {
    const panel = ref.current;
    if (!panel) return;
    const wasStarted = started.current;
    started.current = true;
    if (reduced || keyboard.current || typeof panel.animate !== "function") {
      previous.current = null;
      return;
    }
    const source = origin?.element;
    const sourceBounds = source?.isConnected ? source.getBoundingClientRect() : null;
    const sourceRect = sourceBounds && sourceBounds.right > 0 && sourceBounds.bottom > 0 && sourceBounds.left < window.innerWidth && sourceBounds.top < window.innerHeight ? sourceBounds : null;
    const target = panel.getBoundingClientRect();
    const from = previous.current ?? (!wasStarted && open ? sourceRect : null);
    previous.current = null;
    const closing = !open && sourceRect;
    const transform = surfaceTransform(closing || from || target, target);
    if (transform && (closing || from)) {
      const radius = getComputedStyle(panel).borderRadius;
      const sourceRadius = origin?.kind === "capsule" ? "999px" : "14px";
      animation.current = panel.animate(
        closing ? [{ transform: "none", opacity: 1, borderRadius: radius }, { transform, opacity: 0, borderRadius: sourceRadius }] : [{ transform, opacity: .4, borderRadius: sourceRadius }, { transform: "none", opacity: 1, borderRadius: radius }],
        { duration: closing ? 150 : 260, easing: SURFACE_EASE, fill: "none" },
      );
    }
    return () => {
      animation.current?.cancel();
      animation.current = null;
      // StrictMode replays setup after cleanup: restore the entry state as well.
      started.current = wasStarted;
    };
  }, [open, origin, layout, reduced, ref]);

  return { prepare, reduced };
}
