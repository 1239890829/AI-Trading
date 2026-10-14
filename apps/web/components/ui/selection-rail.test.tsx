import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SelectionRail } from "./selection-rail";

const frames = new Map<number, FrameRequestCallback>();
let nextFrame = 0;
function flushFrame() {
 const ready = [...frames.values()]; frames.clear();
 act(() => { for (const callback of ready) callback(0); });
}
beforeEach(() => {
 vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { const id = ++nextFrame; frames.set(id, callback); return id; });
 vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
});
afterEach(() => { cleanup(); frames.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function sample(active: string | null) {
 return <SelectionRail activeKey={active ?? ""} label="视角"><button aria-pressed={active === "a"}>短</button><button aria-pressed={active === "b"}>较长的名称</button></SelectionRail>;
}
it("tracks unequal labels across rapid reversals without changing focus or selecting commands", () => {
 vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
  const x=this.tagName==="BUTTON" ? this.textContent==="短" ? 110 : 180 : 100;
  const width=this.tagName==="BUTTON" ? this.textContent==="短" ? 64 : 104 : 220;
  return {x,y:0,left:x,right:x+width,top:0,bottom:44,width,height:44,toJSON:()=>({})};
 });
 const {container,rerender}=render(sample("a"));
 flushFrame();
 const line=container.querySelector<HTMLElement>(".selection-line")!;
 const group=screen.getByRole("group");
 expect(line.style.transform).toBe("translateX(18px) scaleX(48)");
 const second=screen.getByRole("button",{name:"较长的名称"});second.focus();
 fireEvent.click(second,{detail:1});rerender(sample("b"));flushFrame();
 expect(group.dataset.motionImmediate).toBe("false");
 expect(line.style.transform).toBe("translateX(88px) scaleX(88)");
 expect(document.activeElement).toBe(second);
 rerender(sample("a"));flushFrame();expect(line.style.transform).toBe("translateX(18px) scaleX(48)");
 fireEvent.keyDown(second,{key:"Tab"});expect(group.dataset.motionImmediate).toBe("true");
 rerender(sample(null));flushFrame();expect(line.style.opacity).toBe("0");
 expect(document.activeElement).toBe(second);
});
it("recomputes live geometry on resize and cleans every observer when unmounted", () => {
 const disconnect=vi.fn(),observe=vi.fn();
 vi.stubGlobal("ResizeObserver",class {observe=observe;disconnect=disconnect;});
 let width=80;
 vi.spyOn(HTMLElement.prototype,"getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
  return {x:0,y:0,left:0,right:width,top:0,bottom:44,width:this.tagName==="BUTTON"?width:200,height:44,toJSON:()=>({})};
 });
 const {container,unmount,rerender}=render(sample("b"));
 flushFrame();
 const line=container.querySelector<HTMLElement>(".selection-line")!;
 expect(observe).toHaveBeenCalledTimes(3);
 rerender(sample("a"));rerender(sample("b"));
 expect(observe).toHaveBeenCalledTimes(3);expect(disconnect).not.toHaveBeenCalled();
 width=120;fireEvent(window,new Event("resize"));flushFrame();
 expect(line.style.transform).toBe("translateX(8px) scaleX(104)");
 expect(screen.getByRole("group").dataset.motionImmediate).toBe("true");
 unmount();expect(disconnect).toHaveBeenCalledTimes(1);
});

it("batches geometry reads before marker writes and drops obsolete or unmounted work", () => {
 const order: string[] = [];
 const geometry = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
  order.push("read");
  return new DOMRect(this.tagName === "BUTTON" ? 120 : 100, 0, this.textContent === "短" ? 64 : 104, 44);
 });
 const {container, rerender, unmount} = render(<>{sample("a")}{sample("a")}</>);
 expect(geometry).not.toHaveBeenCalled();
 for (const line of container.querySelectorAll<HTMLElement>(".selection-line")) {
  Object.defineProperty(line.style, "transform", {configurable:true, set: () => { order.push("write"); }});
 }
 rerender(<>{sample("b")}{sample("b")}</>);
 expect(frames.size).toBe(1);
 flushFrame();
 expect(order).toEqual(["read", "read", "read", "read", "write", "write"]);
 rerender(<>{sample("a")}{sample("a")}</>);
 unmount();
 expect(frames.size).toBe(0);
 geometry.mockClear(); flushFrame(); expect(geometry).not.toHaveBeenCalled();
});

it("consumes a resize with no selection before the next pointer transition", () => {
 let notify: ResizeObserverCallback = () => {};
 vi.stubGlobal("ResizeObserver", class {
  constructor(callback: ResizeObserverCallback) { notify = callback; }
  observe() {} disconnect() {}
 });
 vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0,0,80,44));
 const {rerender, container} = render(sample(null)); flushFrame();
 act(() => { notify([], {} as ResizeObserver); }); flushFrame();
 fireEvent.click(screen.getByRole("button", {name:"短"}), {detail:1});
 rerender(sample("a")); flushFrame();
 expect(screen.getByRole("group").dataset.motionImmediate).toBe("false");
 expect(container.querySelector<HTMLElement>(".selection-line")!.style.opacity).toBe("1");
});
it("preserves another pending rail across StrictMode cleanup and one owner's unmount", () => {
 vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0,0,80,44));
 const one = render(<StrictMode>{sample("a")}</StrictMode>);
 const two = render(<StrictMode>{sample("b")}</StrictMode>);
 expect(frames.size).toBe(1);
 one.unmount();
 expect(frames.size).toBe(1);
 flushFrame();
 expect(two.container.querySelector<HTMLElement>(".selection-line")!.style.opacity).toBe("1");
 two.unmount(); expect(frames.size).toBe(0);
});

it("does no marker work for button-only chart controls and cleans work when toggled", () => {
 const observe=vi.fn(), disconnect=vi.fn();
 vi.stubGlobal("ResizeObserver", class { observe=observe; disconnect=disconnect; });
 const geometry=vi.spyOn(HTMLElement.prototype,"getBoundingClientRect").mockReturnValue(new DOMRect(0,0,80,44));
 const sampleMode=(indicator:boolean) => <SelectionRail indicator={indicator} activeKey="a" label="图表视角"><button aria-pressed="true">K线</button><button>分时</button></SelectionRail>;
 const {container,rerender}=render(sampleMode(false));
 expect(screen.getByRole("button",{name:"K线"}).getAttribute("aria-pressed")).toBe("true");
 expect(container.querySelector(".selection-line")).toBeNull();
 expect(observe).not.toHaveBeenCalled(); expect(frames.size).toBe(0); expect(geometry).not.toHaveBeenCalled();
 rerender(sampleMode(true)); expect(frames.size).toBe(1);
 rerender(sampleMode(false)); expect(frames.size).toBe(0); flushFrame();
 expect(geometry).not.toHaveBeenCalled(); expect(disconnect).toHaveBeenCalledOnce();
});
