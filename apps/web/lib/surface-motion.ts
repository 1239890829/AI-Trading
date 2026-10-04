import type { MouseEvent } from "react";

export type MotionOrigin = { element: HTMLElement; kind: "card" | "capsule" };

/** Keyboard activation deliberately has no spatial travel. */
export function motionOrigin(event: MouseEvent<HTMLElement>, kind: MotionOrigin["kind"] = "card"): MotionOrigin | null {
  return event.detail > 0 ? { element: event.currentTarget, kind } : null;
}

export const SURFACE_EASE = "cubic-bezier(.22,1,.36,1)";

export function surfaceTransform(from: DOMRect, to: DOMRect): string | null {
  if (!from.width || !from.height || !to.width || !to.height) return null;
  return `translate(${from.left - to.left}px, ${from.top - to.top}px) scale(${from.width / to.width}, ${from.height / to.height})`;
}

/** Beyond an anchor, travel keeps increasing with decreasing resistance. */
export function drawerTravel(delta: number, compact: boolean): number {
  if ((!compact && delta < 0) || (compact && delta > 0)) {
    return Math.sign(delta) * 38 * (1 - Math.exp(-Math.abs(delta) / 100));
  }
  return Math.max(-180, Math.min(180, delta));
}

export function drawerSnap(compact: boolean, delta: number, velocity: number): boolean {
  if (velocity < -.35) return false;
  if (velocity > .35) return true;
  if (delta < -45) return false;
  if (delta > 45) return true;
  return compact;
}
