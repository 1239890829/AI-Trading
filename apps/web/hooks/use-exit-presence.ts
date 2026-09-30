"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";
// Paired with --dur-fast. This is visual retention only, never a command delay.
const EXIT_MS = 160;
function reducedSnapshot() { return typeof window.matchMedia === "function" && window.matchMedia(QUERY).matches; }
function subscribeReduced(listener: () => void) {
  if (typeof window.matchMedia !== "function") return () => {};
  const query = window.matchMedia(QUERY);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}
export function useReducedMotion() {
  return useSyncExternalStore(subscribeReduced, reducedSnapshot, () => true);
}

/** Keep a closed, inert reading surface briefly; caller state/focus/IO close immediately. */
export function useExitPresence<T>(value: T | null) {
  const reduced = useReducedMotion();
  const active = value !== null;
  const [shown, setShown] = useState(value);
  const [wasActive, setWasActive] = useState(active);
  const [exiting, setExiting] = useState(false);
  if (active && value !== shown) setShown(value);
  if (active !== wasActive) {
    setWasActive(active);
    setExiting(!active);
  }
  useEffect(() => {
    if (active || !exiting) return;
    const timer = setTimeout(() => { setShown(null); setExiting(false); }, reduced ? 0 : EXIT_MS);
    return () => clearTimeout(timer);
  }, [active, exiting, reduced]);
  return { value: active ? value : exiting && !reduced ? shown : null, active };
}
