import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SelectionRail } from "./selection-rail";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
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
 const line=container.querySelector<HTMLElement>(".selection-line")!;
 const group=screen.getByRole("group");
 expect(line.style.transform).toBe("translateX(18px) scaleX(48)");
 const second=screen.getByRole("button",{name:"较长的名称"});second.focus();
 fireEvent.click(second,{detail:1});rerender(sample("b"));
 expect(group.dataset.motionImmediate).toBe("false");
 expect(line.style.transform).toBe("translateX(88px) scaleX(88)");
 expect(document.activeElement).toBe(second);
 rerender(sample("a"));expect(line.style.transform).toBe("translateX(18px) scaleX(48)");
 fireEvent.keyDown(second,{key:"Tab"});expect(group.dataset.motionImmediate).toBe("true");
 rerender(sample(null));expect(line.style.opacity).toBe("0");
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
 const line=container.querySelector<HTMLElement>(".selection-line")!;
 expect(observe).toHaveBeenCalledTimes(3);
 rerender(sample("a"));rerender(sample("b"));
 expect(observe).toHaveBeenCalledTimes(3);expect(disconnect).not.toHaveBeenCalled();
 width=120;fireEvent(window,new Event("resize"));
 expect(line.style.transform).toBe("translateX(8px) scaleX(104)");
 expect(screen.getByRole("group").dataset.motionImmediate).toBe("true");
 unmount();expect(disconnect).toHaveBeenCalledTimes(1);
});
