import { StrictMode, useEffect, useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { CandidateCollection } from "./candidate-collection";
afterEach(cleanup);
it("同一候选切换列表保留对象状态和挂载身份", () => {
 const mount = vi.fn();
 function Candidate() {
  const [count,setCount]=useState(0);
  useEffect(()=>{mount();},[]);
  return <button onClick={()=>setCount(count+1)}>核对 {count}</button>;
 }
 render(<CandidateCollection><Candidate key="600127"/></CandidateCollection>);
 fireEvent.click(screen.getByRole("button",{name:"核对 0"}));
 fireEvent.click(screen.getByRole("button",{name:"列表"}));
 expect(screen.getByRole("button",{name:"核对 1"})).toBeTruthy();
 fireEvent.click(screen.getByRole("button",{name:"卡片"}));
 expect(mount).toHaveBeenCalledTimes(1);
 expect(screen.getByRole("button",{name:"核对 1"})).toBeTruthy();
});

it("a live pointer reversal and resize leave a usable final layout without replacing candidates", () => {
 const rect = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
  const list = this.closest('[data-view]')?.getAttribute('data-view') === 'list';
  return {x:0,y:0,left:0,top:0,right:list?600:280,bottom:180,width:list?600:280,height:180,toJSON:()=>({})} as DOMRect;
 });
 try {
  const {container,unmount} = render(<CandidateCollection><button key="one">查看依据</button><button key="two">查看失效条件</button></CandidateCollection>);
  const candidate = screen.getByRole("button",{name:"查看依据"});
  fireEvent.click(screen.getByRole("button",{name:"列表"}),{detail:1});
  fireEvent.click(screen.getByRole("button",{name:"卡片"}),{detail:1});
  fireEvent(window,new Event("resize"));
  expect(container.querySelector('[data-view="cards"]')).toBeTruthy();
  expect(screen.getByRole("button",{name:"查看依据"})).toBe(candidate);
  for(const item of container.querySelectorAll<HTMLElement>('[data-collection-item]')) {
   expect(item.style.transform).toBe("");
   expect(item.style.width).toBe("");
  }
  unmount();
  fireEvent(window,new Event("resize"));
 } finally {rect.mockRestore();}
});


it("a live reduced-motion change restores the layout and unsubscribes on unmount", () => {
 let reduced = false;
 const listeners = new Set<() => void>();
 const media = {get matches(){return reduced;},media:"(prefers-reduced-motion: reduce)",addEventListener:(_type:string,listener:()=>void)=>listeners.add(listener),removeEventListener:(_type:string,listener:()=>void)=>listeners.delete(listener)};
 vi.stubGlobal("matchMedia",()=>media);
 const rect = vi.spyOn(HTMLElement.prototype,"getBoundingClientRect").mockReturnValue({x:0,y:0,left:0,top:0,right:280,bottom:180,width:280,height:180,toJSON:()=>({})} as DOMRect);
 try {
  const {container,unmount} = render(<StrictMode><CandidateCollection><button key="one">核对事件</button></CandidateCollection></StrictMode>);
  fireEvent.click(screen.getByRole("button",{name:"列表"}),{detail:1});
  act(()=>{reduced=true;listeners.forEach(listener=>listener());});
  expect(container.querySelector('[data-view="list"]')).toBeTruthy();
  for(const item of container.querySelectorAll<HTMLElement>('[data-collection-item]')) expect(item.style.transform).toBe("");
  fireEvent.click(screen.getByRole("button",{name:"卡片"}),{detail:1});
  expect(container.querySelector('[data-view="cards"]')).toBeTruthy();
  for(const item of container.querySelectorAll<HTMLElement>('[data-collection-item]')) expect(item.style.transform).toBe("");
  unmount(); expect(listeners.size).toBe(0);
 } finally {rect.mockRestore();vi.unstubAllGlobals();}
});
