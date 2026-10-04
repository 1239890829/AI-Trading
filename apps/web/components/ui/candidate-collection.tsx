"use client";

import { Children, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useReducedMotion } from "@/hooks/use-exit-presence";

/** User-driven visual reordering keeps every child mounted and never changes the source pool. */
export function CandidateCollection({children}: {children:ReactNode}) {
  const [view,setView]=useState<"cards"|"list">("cards");
  const root=useRef<HTMLDivElement>(null);
  const before=useRef(new Map<string,DOMRect>());
  const animations=useRef<Animation[]>([]);
  const reduced=useReducedMotion();
  function choose(next:"cards"|"list") {
    if(next===view)return;
    animations.current.forEach(animation=>animation.cancel());animations.current=[];
    before.current.clear();
    if(!reduced) root.current?.querySelectorAll<HTMLElement>('[data-collection-item]').forEach(item=>before.current.set(item.dataset.collectionItem!,item.getBoundingClientRect()));
    setView(next);
  }
  useLayoutEffect(()=>{
    if(!reduced) root.current?.querySelectorAll<HTMLElement>('[data-collection-item]').forEach(item=>{
      const old=before.current.get(item.dataset.collectionItem!);const now=item.getBoundingClientRect();
      if(!old || !old.width || !old.height || !now.width || !now.height || typeof item.animate!=="function")return;
      animations.current.push(item.animate([{transform:`translate(${old.left-now.left}px,${old.top-now.top}px) scale(${old.width/now.width},${old.height/now.height})`},{transform:"none"}],{duration:240,easing:"cubic-bezier(.22,1,.36,1)"}));
    });
    before.current.clear();
    return ()=>{animations.current.forEach(animation=>animation.cancel());animations.current=[];};
  },[view,reduced]);
  return <div className="candidate-collection">
    <div className="collection-toolbar"><span>按形成依据逐项核对</span><div role="group" aria-label="候选呈现方式"><button type="button" aria-pressed={view==="cards"} onClick={()=>choose("cards")}>卡片</button><button type="button" aria-pressed={view==="list"} onClick={()=>choose("list")}>列表</button></div></div>
    <div ref={root} className="collection-items" data-view={view}>{Children.toArray(children).map(child=><div key={typeof child==="object" && child && "key" in child ? child.key : String(child)} data-collection-item={String(typeof child==="object" && child && "key" in child ? child.key : child)} style={{transformOrigin:"top left"}}>{child}</div>)}</div>
  </div>;
}
