"use client";

import { useEffect, useRef, type RefObject } from "react";

type Layer = { root: HTMLElement; z: number; order: number; previous: HTMLElement | SVGElement | null; close: () => void };
const layers: Layer[] = [];
const originalInert = new Map<HTMLElement, boolean>();
let order = 0;

function topLayer() { return [...layers].sort((a, b) => a.z - b.z || a.order - b.order).at(-1); }
function visible(el: HTMLElement) {
  for (let node: HTMLElement | null = el; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (node.hidden || style.display === "none" || style.visibility === "hidden") return false;
    if (node instanceof HTMLDetailsElement && !node.open && !node.querySelector("summary")?.contains(el)) return false;
  }
  return true;
}
function controls(root: HTMLElement) {
  return [...root.querySelectorAll<HTMLElement>('button, a[href], input, select, textarea, summary, [tabindex]')]
    .filter(el => el.tabIndex >= 0 && !el.matches(':disabled') && !el.closest('[inert], [hidden]') && visible(el));
}
function focusFirst(layer: Layer) {
  const targets = controls(layer.root);
  (targets.find(el => el.hasAttribute("data-overlay-autofocus")) ?? targets[0] ?? layer.root).focus();
}
function isolateTop() {
  for (const [node, inert] of originalInert) node.inert = inert;
  originalInert.clear();
  let branch: HTMLElement | undefined = topLayer()?.root;
  // Isolate siblings on the full ancestor path, including inline drawers and nested portals.
  while (branch?.parentElement) {
    for (const sibling of branch.parentElement.children) {
      if (sibling !== branch && sibling instanceof HTMLElement && !sibling.matches("script, style")) {
        originalInert.set(sibling, sibling.inert);
        sibling.inert = true;
      }
    }
    if (branch.parentElement === document.body) break;
    branch = branch.parentElement;
  }
}

/** Shared modal/drawer focus, top-only Escape, background isolation and return focus. */
export function useOverlayFocus(ref: RefObject<HTMLElement | null>, onClose: () => void, enabled = true, zIndex = 50) {
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; }, [onClose]);
  useEffect(() => {
    if (!enabled || !ref.current) return;
    const layer: Layer = { root: ref.current, z: zIndex, order: ++order,
      previous: document.activeElement instanceof HTMLElement || document.activeElement instanceof SVGElement ? document.activeElement : null,
      close: () => closeRef.current() };
    layers.push(layer);
    isolateTop();
    if (topLayer() === layer) focusFirst(layer);
    function key(event: KeyboardEvent) {
      if (topLayer() !== layer || event.isComposing) return;
      if (event.key === "Escape") {
        event.preventDefault(); event.stopImmediatePropagation(); layer.close();
      } else if (event.key === "Tab") {
        const targets = controls(layer.root);
        const first = targets[0] ?? layer.root;
        const last = targets.at(-1) ?? layer.root;
        if (!layer.root.contains(document.activeElement) || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
          event.preventDefault(); (event.shiftKey ? last : first).focus();
        }
      }
    }
    function focus(event: FocusEvent) {
      if (topLayer() === layer && event.target instanceof Node && !layer.root.contains(event.target)) focusFirst(layer);
    }
    window.addEventListener("keydown", key, true);
    document.addEventListener("focusin", focus);
    return () => {
      const wasTop = topLayer() === layer;
      window.removeEventListener("keydown", key, true);
      document.removeEventListener("focusin", focus);
      layers.splice(layers.indexOf(layer), 1);
      isolateTop();
      if (wasTop) {
        const top = topLayer();
        const previous = layer.previous;
        if (previous?.isConnected && !previous.closest('[inert]') && (!top || top.root.contains(previous))) previous.focus();
        else if (top) focusFirst(top);
      }
    };
  }, [enabled, ref, zIndex]);
}
