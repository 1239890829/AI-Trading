"use client";

import {useState} from "react";
import type {EventSummary} from "@/lib/api";
import {eventDirectionView, eventInterpretationText, eventJudgeView, eventJudgementExplanation} from "@/lib/event-view";
import {eventTimeText, sourceLabel} from "@/lib/format";
import {ConditionList, ExplanationMap, FactCards, FactTimeline} from "@/components/ui/explanation-map";

/** Exact source excerpt for the overview; original records stay available without rewriting. */
function excerpt(text: string): string {
  if (text.length <= 140) return text;
  const end = text.slice(0, 140).search(/[。！？]/);
  return end >= 0 ? text.slice(0,end+1) : `${text.slice(0,140)}…`;
}

/** Content decides the branches. No placeholder target or causal claim is generated. */
export function EventOverview({event, symbol}: {event: EventSummary; symbol?: string | null}) {
  const ref = event.interpretation_ref;
  const judge = eventJudgeView(event);
  const identity = `${event.id}:${ref?.version_id ?? "unknown"}:${ref?.observation_id ?? "unknown"}:${ref?.state ?? "unknown"}:${symbol || "global"}:${JSON.stringify(event.directions)}`;
  const [recordView,setRecordView] = useState<string|null>(null);
  if (recordView !== null && recordView !== identity) setRecordView(null);
  const recordsOpen = recordView === identity;
  const targetSteps = event.directions.map((row,index) => {
    const direction = eventDirectionView(row,event,symbol);
    return {id:`target-${index}`, label:`${row.target_type === "symbol" ? "个股" : row.target_type === "theme" ? "题材" : row.target_type} · ${row.target}`, icon:"relation" as const,
      summary:<><strong>{direction.text}</strong>{row.basis && <span>记录依据：{excerpt(row.basis)}</span>}{row.chain && row.chain !== row.basis && <span>传导线索：{excerpt(row.chain)}</span>}{!row.basis && !row.chain && <span>依据未记录，不能据此推断受益。</span>}</>,
      content:<div className="space-y-3"><p>{direction.title}</p><FactCards label="原始方向记录" items={[{label:"方向",value:String(row.direction)},{label:"强度标记",value:String(row.strength)},{label:"匹配方式",value:row.matched_by || "未记录"}]}/>{row.basis && <blockquote className="whitespace-pre-wrap" aria-label="原记录依据（待核）">{row.basis}</blockquote>}{row.chain && <blockquote className="whitespace-pre-wrap" aria-label="原传导线索（待核）">{row.chain}</blockquote>}</div>};
  });
  return <section className="min-w-0 space-y-4 pb-3 text-xs" aria-label="事件关系概览">
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-600 dark:text-zinc-400"><span>{sourceLabel(event.source)} · 来源级别 {event.source_tier}/5</span><span>{judge.text}</span><span>{event.certainty === "proposed" ? "尚属提议/预案" : event.certainty === "rumor" ? "传闻，待核" : event.certainty === "done" ? "来源记录为已发生，未独立核实" : "阶段未知"}</span></div>
    {event.summary && <section className="min-w-0 space-y-1" aria-label="消息要点摘录"><h4 className="font-medium">消息要点 · 原文摘录</h4><p className="max-w-[72ch] whitespace-pre-wrap leading-7 text-zinc-700 dark:text-zinc-300">{excerpt(event.summary)}</p></section>}
    {targetSteps.length ? <ExplanationMap label="关联对象与原记录依据" identity={identity} center={{label:"这条消息",value:event.title}} links={targetSteps.map(step=>({from:"center",to:step.id,label:"关联记录 · 受益待核",kind:"unverified"}))} steps={targetSteps}/> : <p className="text-zinc-600 dark:text-zinc-400">没有已记录的题材或个股关联。不能从标题自动推导受益股。</p>}
    <FactTimeline label="已记录时点" items={[
      {label:"来源发布时间",value:event.published_at ? eventTimeText(event.published_at) : "未记录，未用系统时间补填",kind:"time"},
      {label:"解释可用时间",value:ref?.available_at ? eventTimeText(ref.available_at) : "未记录",kind:"time"},
      ...(event.judged_at ? [{label:"判读记录时间",value:eventTimeText(event.judged_at),kind:"time" as const}] : []),
      ...(event.llm_judged_at ? [{label:"辅助尝试时间",value:eventTimeText(event.llm_judged_at),kind:"time" as const}] : []),
    ]}/>
    <p className="text-[11px] text-zinc-600 dark:text-zinc-400">列表没有提供已核实的涨停因果。关联对象的方向与实际受益需分别核查。</p>
    {(!event.is_active || ref?.state === "withdrawn" || ref?.state === "pending") && <ConditionList label="当前限制" tone="warning" items={[...(!event.is_active ? ["事件已非活跃，原记录仅供核对。"] : []),...(ref?.state === "withdrawn" ? ["解释已撤回，不能当作有效依据。"] : []),...(ref?.state === "pending" ? ["解释待复核，不能当作已核实依据。"] : [])]}/>}
    <section className="min-w-0 text-zinc-600 dark:text-zinc-400"><button type="button" data-action="quiet" aria-expanded={recordsOpen} onClick={()=>setRecordView(recordsOpen ? null : identity)}>核对完整判读与来源记录</button><div className="motion-disclosure" data-open={recordsOpen} aria-hidden={!recordsOpen || undefined} inert={!recordsOpen}><div className="motion-disclosure-body"><div className="mt-2 space-y-3"><p>{eventInterpretationText(event)}</p><blockquote className="whitespace-pre-wrap leading-relaxed">{eventJudgementExplanation(event)}</blockquote>{event.summary && <blockquote className="whitespace-pre-wrap leading-relaxed" aria-label="列表来源摘要">{event.summary}</blockquote>}{event.url ? <a href={event.url} target="_blank" rel="noopener noreferrer" data-action="quiet">查看消息原文</a> : <p>原文链接缺失。</p>}</div></div></div></section>
  </section>;
}
