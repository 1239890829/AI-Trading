import { StrictMode, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ModalShell } from "./modal-shell";
import { motionOrigin, type MotionOrigin } from "@/lib/surface-motion";
import { useExitPresence } from "@/hooks/use-exit-presence";

let reduce = false;
const listeners = new Set<() => void>();
const animations: { cancel: ReturnType<typeof vi.fn> }[] = [];
const animate = vi.fn(() => { const a = { cancel: vi.fn() }; animations.push(a); return a as unknown as Animation; });
beforeEach(() => {
  reduce = false; listeners.clear(); animations.length = 0;
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce") ? reduce : query.includes("max-width"), addEventListener: (_: string, fn: () => void) => listeners.add(fn), removeEventListener: (_: string, fn: () => void) => listeners.delete(fn) }));
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) { return this.getAttribute("role") === "dialog" ? new DOMRect(10, 10, 600, 600) : new DOMRect(100, 100, 180, 44); });
  vi.stubGlobal("PointerEvent", MouseEvent);
  Object.defineProperty(HTMLElement.prototype, "animate", {value: animate, configurable: true});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); animate.mockClear(); delete (HTMLElement.prototype as Partial<HTMLElement>).animate; });

function Host() {
  const [open, setOpen] = useState(false);
  const [origin, setOrigin] = useState<MotionOrigin | null>(null);
  const presence = useExitPresence(open ? true : null);
  return <><button onClick={event => { setOrigin(motionOrigin(event)); setOpen(true); }}>打开工具</button>{presence.value && <ModalShell label="工具" presentation="drawer" motionOrigin={origin} open={presence.active} onClose={() => setOpen(false)} header="工具标题">{presence.active && <input aria-label="草稿" />}正文</ModalShell>}</>;
}
function pointer(el: HTMLElement, type: string, y: number, time: number, id = 1) {
  const e = new MouseEvent(type, { bubbles:true, clientY:y, button:0 });
  Object.defineProperties(e, { pointerId:{value:id}, isPrimary:{value:id === 1}, pointerType:{value:"touch"}, timeStamp:{value:time} });
  fireEvent(el, e);
}

describe("actual surface lifecycle", () => {
  it("StrictMode effect replay leaves a live entry animation rather than a canceled one", () => {
    render(<StrictMode><Host /></StrictMode>);
    fireEvent.click(screen.getByText("打开工具"), {detail:1});
    expect(animations.length).toBeGreaterThan(0);
    expect(animations.at(-1)!.cancel).not.toHaveBeenCalled();
  });

  it("opens at the pointer source, but Escape closes immediately without a reverse animation", () => {
    render(<Host />);
    fireEvent.click(screen.getByText("打开工具"), {detail:1});
    expect(animate).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, {key:"Escape"});
    expect(animate).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("草稿")).toBeNull();
    expect(animations[0].cancel).toHaveBeenCalled();
  });
  it("keyboard entry and resize keep the input immediate and mounted", () => {
    render(<Host />);
    fireEvent.click(screen.getByText("打开工具"), {detail:0});
    const input = screen.getByLabelText("草稿") as HTMLInputElement;
    fireEvent.change(input, {target:{value:"保留"}});
    fireEvent.click(screen.getByRole("button", {name:"收起至半屏"}), {detail:0});
    expect(screen.getByRole("dialog").dataset.mobileSnap).toBe("compact");
    expect(screen.getByLabelText("草稿")).toBe(input);
    expect(input.value).toBe("保留");
    expect(animate).not.toHaveBeenCalled();
  });
  it("switching to reduced motion cancels a running surface and unmount also cleans up", () => {
    const view = render(<Host />);
    fireEvent.click(screen.getByText("打开工具"), {detail:1});
    act(() => { reduce = true; [...listeners].forEach(fn => fn()); });
    expect(animations[0].cancel).toHaveBeenCalled();
    expect(animate).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(listeners.size).toBe(0);
  });
  it("a completed drag does not trigger the synthetic click and reverse its snap", () => {
    render(<Host />); fireEvent.click(screen.getByText("打开工具"), {detail:0});
    const handle = screen.getByRole("button", {name:"收起至半屏"});
    pointer(handle,"pointerdown",100,100); pointer(handle,"pointermove",210,300); pointer(handle,"pointerup",210,400);
    expect(screen.getByRole("dialog").dataset.mobileSnap).toBe("compact");
    fireEvent.click(handle,{detail:1});
    expect(screen.getByRole("dialog").dataset.mobileSnap).toBe("compact");
  });
  it("secondary pointers and canceled gestures cannot resize or close a drawer", () => {
    render(<Host />); fireEvent.click(screen.getByText("打开工具"), {detail:0});
    const handle = screen.getByRole("button", {name:"收起至半屏"});
    pointer(handle,"pointerdown",100,100); pointer(handle,"pointermove",240,200,2);
    pointer(handle,"pointercancel",240,300);
    expect(screen.getByRole("dialog").dataset.mobileSnap).toBe("full");
    expect(screen.getByRole("dialog").style.transform).toBe("");
    expect(screen.getByLabelText("草稿")).toBeTruthy();
  });
});
