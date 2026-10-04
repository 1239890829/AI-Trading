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
it("touch never starts tilt, and reduced motion or unmount cancels an active spring", () => {
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
  fireEvent.pointerDown(button);
  fireEvent.pointerUp(button);
  fireEvent.click(button, { detail: 1 });
  expect(open).toHaveBeenCalledTimes(2);
});
