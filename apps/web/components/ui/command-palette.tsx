"use client";

import { ControlHint } from "@/components/ui/control-hint";
import Link from "next/link";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useExitPresence } from "@/hooks/use-exit-presence";
import { ModalShell } from "@/components/ui/modal-shell";
import { TASK_LINKS } from "@/lib/task-navigation";
import { MARKET_LENSES, MAINTENANCE_TOOLS, RESEARCH_TOOLS } from "@/lib/workspace-tools";

export const NAV_COMMANDS = [
  ...TASK_LINKS.map(item => ({label:item.label, detail:"主要任务", href:item.href})),
  ...MARKET_LENSES.map(item => ({label:item.label, detail:item.description, href:item.href})),
  ...RESEARCH_TOOLS.map(item => ({label:item.label, detail:item.description, href:`/agent?area=research&tab=${item.key}`})),
  ...MAINTENANCE_TOOLS.map(item => ({label:item.label, detail:item.description, href:`/agent?area=maintenance&tab=${item.key}`})),
  {label:"持仓与模拟", detail:"明确账户范围后查看记录和回执", href:"/workbench?mode=positions"},
];

function Match({text, query}: {text:string; query:string}) {
  const start = query ? text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase()) : -1;
  if (start < 0) return <>{text}</>;
  return <>{text.slice(0,start)}<mark>{text.slice(start,start+query.length)}</mark>{text.slice(start+query.length)}</>;
}

/** Navigation only: selecting a command cannot submit a business operation. */
export function CommandPalette() {
  const [open,setOpen] = useState(false);
  const [query,setQuery] = useState("");
  const [index,setIndex] = useState(0);
  const listId = useId();
  const list = useRef<HTMLDivElement>(null);
  const results = useMemo(() => NAV_COMMANDS.filter(item => `${item.label} ${item.detail}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())), [query]);
  const presence = useExitPresence(open ? true : null);
  const selected = Math.min(index,Math.max(0,results.length-1));
  function close() { setOpen(false); }
  function show() { setQuery(""); setIndex(0); setOpen(true); }
  useEffect(() => {
    function key(event:KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLocaleLowerCase()==="k" && !event.isComposing && !event.defaultPrevented) {
        if (document.querySelector('[aria-modal="true"]')) return;
        event.preventDefault(); setQuery(""); setIndex(0); setOpen(true);
      }
    }
    window.addEventListener("keydown",key);
    return () => window.removeEventListener("keydown",key);
  },[]);
  useEffect(() => { list.current?.querySelector('[data-selected="true"]')?.scrollIntoView?.({block:"nearest"}); },[selected,query]);
  return <>
    <ControlHint content="查找页面与工具 · Ctrl/⌘ K" inactive={open}><button type="button" className="command-launch" aria-label="打开命令面板" aria-haspopup="dialog" onClick={show}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="4"/><path d="m8 9 3 3-3 3m5 0h3"/></svg><kbd>⌘ K</kbd></button></ControlHint>
    {presence.value && <ModalShell open={presence.active} onClose={close} label="查找页面与工具" size="md" header={<div><p className="workspace-kicker">快速到达</p><h2 className="text-lg font-semibold">查找页面与工具</h2></div>} footer="↑ ↓ 选择 · Enter 打开 · Esc 关闭。证券搜索在顶栏，业务操作仍需原权限与确认。">
      <div className="command-palette">
        <input data-overlay-autofocus="primary" aria-label="查找页面与工具" role="combobox" aria-expanded="true" aria-controls={listId} aria-autocomplete="list" aria-activedescendant={results.length ? `${listId}-${selected}` : undefined} placeholder="输入任务、工具或关键词" value={query} onChange={event => {setQuery(event.target.value);setIndex(0);}} onKeyDown={event => {
          if (event.nativeEvent.isComposing || event.keyCode===229) return;
          if (event.key==="ArrowDown" || event.key==="ArrowUp") {event.preventDefault();setIndex(value => results.length ? (Math.min(value,results.length-1)+(event.key==="ArrowDown"?1:-1)+results.length)%results.length : 0);}
          if (event.key==="Enter" && results.length) {event.preventDefault();list.current?.querySelector<HTMLAnchorElement>(`[data-command-index="${selected}"]`)?.click();}
        }}/>
        <div id={listId} ref={list} role="listbox" aria-label="导航结果" className="command-results">
          {results.map((item,i) => <Link role="option" aria-selected={i===selected} tabIndex={-1} id={`${listId}-${i}`} data-command-index={i} data-selected={i===selected} key={`${item.href}:${item.label}`} href={item.href} onClick={close} onPointerMove={() => setIndex(i)}><span className="command-result-index" aria-hidden="true">{String(i+1).padStart(2,"0")}</span><span><strong><Match text={item.label} query={query.trim()}/></strong><small><Match text={item.detail} query={query.trim()}/></small></span><span aria-hidden="true">↗</span></Link>)}
          {!results.length && <p role="status">没有匹配项。可以换一个关键词。</p>}
        </div>
      </div>
    </ModalShell>}
  </>;
}
