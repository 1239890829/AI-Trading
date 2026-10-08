"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { patchWorkspaceUrl } from "@/lib/task-navigation";

type HuntingView = "discover" | "evidence" | "tracking" | "research" | "review";
type Task = { id: string; label: string; view: HuntingView; section?: string; external?: boolean };
const GROUPS: { id: string; label: string; tasks: Task[] }[] = [
  { id: "discover", label: "发现候选", tasks: [
    { id: "candidates", label: "盘中候选", view: "discover", section: "candidates" },
    { id: "daily", label: "每日精选", view: "discover", section: "daily" },
    { id: "opportunity", label: "题材参与", view: "discover", section: "opportunity" },
    { id: "evidence", label: "机会依据", view: "evidence" },
  ] },
  { id: "tracking", label: "持续观察", tasks: [
    { id: "tracking", label: "跟踪记录", view: "tracking" },
    { id: "postmarket", label: "接力与潜伏", view: "discover", section: "postmarket" },
    { id: "brief", label: "盘中节拍", view: "discover", section: "brief" },
  ] },
  { id: "review", label: "验证复盘", tasks: [
    { id: "review", label: "当日对照", view: "review", section: "review" },
    { id: "research", label: "龙头研究", view: "research" },
    { id: "crossday", label: "跨日复盘", view: "review", external: true },
  ] },
];

/** Project existing consumers into a small task rack; route keys remain compatible. */
export function HuntingTaskRail({ view, section, search }: { view: HuntingView; section: string | null; search: string }) {
  const selected = view === "discover" ? ["watcher", "reminders"].includes(section ?? "") ? "brief" : section ?? "candidates" : view;
  const currentGroup = GROUPS.find(group => group.tasks.some(task => task.id === selected))?.id ?? "discover";
  const [groupId, setGroupId] = useState(currentGroup);
  const [previousGroup, setPreviousGroup] = useState(currentGroup);
  if (previousGroup !== currentGroup) { setPreviousGroup(currentGroup); setGroupId(currentGroup); }
  const group = GROUPS.find(item => item.id === groupId) ?? GROUPS[0];
  const panelId = useId();
  return <div className="hunting-task-rack">
    <div className="hunting-task-groups" aria-label="选股任务分组">
      {GROUPS.map(item => <button key={item.id} type="button" aria-expanded={groupId === item.id} aria-controls={panelId}
        onClick={() => setGroupId(item.id)} className="hunting-task-group">
        <span>{item.label}</span><svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="m7 10 5 5 5-5" /></svg>
      </button>)}
    </div>
    <nav id={panelId} className="hunting-task-links" aria-label={`${group.label}功能`}>
      {group.tasks.map(task => <Link key={task.id} scroll={false} aria-current={!task.external && selected === task.id ? "page" : undefined}
        href={task.external ? patchWorkspaceUrl("/agent", search, {area: "research", tab: "review", view: null, panel: null, sec: null, from: `/hunting${search ? `?${search}` : ""}`})
          : patchWorkspaceUrl("/hunting", search, {view: task.view, sec: task.section ?? null, panel: null, review: null})}>
        {task.label}{task.external && <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M7 17 17 7M7 7h10v10" /></svg>}
      </Link>)}
    </nav>
  </div>;
}
