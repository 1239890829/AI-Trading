import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SpatialSurface } from "./spatial-surface";

let reduced = false;
const listeners = new Set<() => void>();
let frames = new Map<number, FrameRequestCallback>();
let next = 1;
beforeEach(() => {
  reduced = false; frames = new Map(); listeners.clear(); next = 1;
  vi.stubGlobal("matchMedia", () => ({ matches: reduced, addEventListener: (_: string, cb: () => void) => listeners.add(cb), removeEventListener: (_: string, cb: () => void) => listeners.delete(cb) }));
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => { const id = next++; frames.set(id, fn); return id; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 400, 800));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function move(node: Element, pointerType = "mouse") {
  const event = new MouseEvent("pointermove", { bubbles: true, clientX: 360, clientY: 100 });
  Object.defineProperties(event, { isPrimary: { value: true }, pointerType: { value: pointerType } });
  fireEvent(node, event);
}
function touch(node: Element, type: string, x = 360, y = 100, primary = true) {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y });
  Object.defineProperties(event, { isPrimary: { value: primary }, pointerType: { value: "touch" }, pointerId: { value: primary ? 1 : 2 } });
  fireEvent(node, event);
  return event;
}
function step(count: number) {
  act(() => { for (let i = 1; i <= count; i++) { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn(i * 16.67)); } });
}
it("reserves a stable hit box while the inner plane tilts and stops scheduling at rest", () => {
  const { container } = render(<SpatialSurface><button>打开</button></SpatialSurface>);
  const slot = container.firstElementChild as HTMLElement;
  const face = slot.firstElementChild as HTMLElement;
  move(slot); step(120);
  expect(slot.style.transform).toBe("");
  expect(face.style.transform).toContain("rotateX(");
  expect(face.style.transform).not.toContain("rotateX(0.000deg) rotateY(0.000deg)");
  expect(frames.size).toBe(0);
  fireEvent.keyDown(window, { key: "Tab" });
  expect(face.style.transform).toBe("");
});
it("touch movement without a press stays still, and reduced motion or unmount cancels an active spring", () => {
  const view = render(<SpatialSurface>内容</SpatialSurface>);
  const slot = view.container.firstElementChild as HTMLElement;
  move(slot, "touch"); expect(frames.size).toBe(0);
  move(slot); step(1); expect(frames.size).toBe(1);
  act(() => { reduced = true; listeners.forEach(fn => fn()); });
  expect(frames.size).toBe(0);
  expect((slot.firstElementChild as HTMLElement).style.transform).toBe("");
  act(() => { reduced = false; listeners.forEach(fn => fn()); });
  move(slot); expect(frames.size).toBe(1);
  view.unmount(); expect(frames.size).toBe(0);
});

it("dragging a surface does not activate its button, while a fresh click and keyboard activation do", () => {
  const open = vi.fn();
  const view = render(<SpatialSurface><button onClick={open}>打开</button></SpatialSurface>);
  const button = view.getByRole("button");
  fireEvent(button, new MouseEvent("pointerdown", { bubbles: true, clientX: 20, clientY: 20 }));
  move(button);
  fireEvent.pointerUp(button);
  fireEvent.click(button, { detail: 1 });
  expect(open).not.toHaveBeenCalled();
  fireEvent.click(button, { detail: 0 });
  expect(open).toHaveBeenCalledTimes(1);
  fireEvent(button, new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
  fireEvent.pointerUp(button);
  fireEvent.click(button, { detail: 1 });
  expect(open).toHaveBeenCalledTimes(2);
});

it("lights a stationary touch but yields to native scrolling without activating the card", () => {
  const open = vi.fn();
  const { container } = render(<SpatialSurface onClick={open}>内容</SpatialSurface>);
  const slot = container.firstElementChild as HTMLElement;
  const face = slot.firstElementChild as HTMLElement;
  touch(slot, "pointerdown"); step(3);
  expect(face.getAttribute("data-spatial-active")).toBe("true");
  expect(face.style.transform).toContain("rotateX(");
  const scroll = touch(slot, "pointermove", 360, 130);
  expect(scroll.defaultPrevented).toBe(false);
  expect(face.style.transform).toBe("");
  expect(face.hasAttribute("data-spatial-active")).toBe(false);
  expect(frames.size).toBe(0);
  touch(slot, "pointerup", 360, 130);
  fireEvent.click(slot, { detail: 1 });
  expect(open).not.toHaveBeenCalled();
  touch(slot, "pointerdown"); step(3); touch(slot, "pointerup");
  expect(face.hasAttribute("data-spatial-active")).toBe(false);
  step(120);
  expect(face.style.transform).toBe("");
  expect(frames.size).toBe(0);
  fireEvent.click(slot, { detail: 1 });
  expect(open).toHaveBeenCalledTimes(1);
});

it("ignores a second finger and nested controls, and removes touch motion on cancellation", () => {
  const open = vi.fn();
  const { container, getByRole } = render(<SpatialSurface><button onClick={open}>打开</button></SpatialSurface>);
  const slot = container.firstElementChild as HTMLElement;
  const face = slot.firstElementChild as HTMLElement;
  touch(slot, "pointerdown"); step(2);
  touch(slot, "pointerup", 100, 700, false);
  expect(face.hasAttribute("data-spatial-active")).toBe(true);
  touch(slot, "pointercancel", 100, 700, false);
  expect(face.hasAttribute("data-spatial-active")).toBe(true);
  touch(slot, "pointercancel");
  expect(frames.size).toBe(0);
  expect(face.style.transform).toBe("");
  touch(slot, "pointerdown"); step(2);
  fireEvent.blur(window);
  touch(slot, "pointermove");
  expect(frames.size).toBe(0);
  expect(face.hasAttribute("data-spatial-active")).toBe(false);
  act(() => { reduced = true; listeners.forEach(fn => fn()); });
  touch(slot, "pointerdown"); step(2);
  expect(frames.size).toBe(0);
  expect(face.hasAttribute("data-spatial-active")).toBe(false);
  act(() => { reduced = false; listeners.forEach(fn => fn()); });
  const button = getByRole("button");
  touch(button, "pointerdown"); touch(button, "pointerup");
  expect(frames.size).toBe(0);
  fireEvent.click(button, { detail: 1 });
  expect(open).toHaveBeenCalledTimes(1);
});
