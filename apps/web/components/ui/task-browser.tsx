"use client";

import Link from "next/link";
import { useState } from "react";
import { useExitPresence } from "@/hooks/use-exit-presence";
import { ModalShell } from "@/components/ui/modal-shell";
import { MARKET_LENSES, MAINTENANCE_TOOLS, RESEARCH_TOOLS } from "@/lib/workspace-tools";
import { motionOrigin, type MotionOrigin } from "@/lib/surface-motion";

const GROUPS = [
  {key:"market",label:"市场与结构",note:"从环境到题材，再核事件与交易足迹",items:MARKET_LENSES.map(item=>({label:item.label,note:item.description,href:item.href}))},
  {key:"research",label:"证据与复盘",note:"核对当时判断、失败样本与方法适用性",items:[{label:"日度复盘",note:"当时判断与后续结果",href:"/agent?area=research&tab=review"},...RESEARCH_TOOLS.map(item=>({label:item.label,note:item.description,href:`/agent?area=research&tab=${item.key}`}))]},
  {key:"maintenance",label:"系统维护",note:"查看运行结果，在原权限内处置",items:MAINTENANCE_TOOLS.map(item=>({label:item.label,note:item.description,href:`/agent?area=maintenance&tab=${item.key}`}))},
];

export function TaskBrowser() {
  const [open,setOpen]=useState(false);
  const [group,setGroup]=useState("market");
  const [origin,setOrigin]=useState<MotionOrigin|null>(null);
  const current=GROUPS.find(item=>item.key===group)!;
  const presence = useExitPresence(open ? true : null);
  return <>
    <button type="button" className="task-browser-trigger header-action" aria-label="浏览工作区" aria-haspopup="dialog" aria-expanded={open} onClick={event=>{setOrigin(motionOrigin(event,"capsule"));setOpen(true);}}><span className="morph-menu" data-open={open} aria-hidden="true"><i/><i/></span></button>
    {presence.value && <ModalShell open={presence.active} label="浏览工作区" onClose={()=>setOpen(false)} size="md" motionOrigin={origin} header={<div><p className="workspace-kicker">所有入口，共用上下文</p><h2 className="text-lg font-semibold">浏览工作区</h2></div>} bodyClassName="overflow-y-auto p-0" footer="入口不会授予额外权限。选择工具后，回执与业务状态仍由原服务维护。">
      <div className="task-browser">
        <div className="browser-groups" role="group" aria-label="工作区分组">{GROUPS.map(item=><button key={item.key} type="button" aria-pressed={group===item.key} onClick={()=>setGroup(item.key)} onPointerEnter={event=>{if(event.pointerType==="mouse")setGroup(item.key);}}><strong>{item.label}</strong><span>{item.note}</span></button>)}</div>
        <div className="browser-links" aria-label={current.label}>{current.items.map((item,index)=><Link href={item.href} key={item.href} onClick={()=>setOpen(false)}><span className="browser-number" aria-hidden="true">{String(index+1).padStart(2,"0")}</span><span><strong>{item.label}</strong><small>{item.note}</small></span><span aria-hidden="true">↗</span></Link>)}</div>
      </div>
    </ModalShell>}
  </>;
}
