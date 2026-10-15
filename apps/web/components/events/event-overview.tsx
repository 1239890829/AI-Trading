"use client";

import type {EventSummary} from "@/lib/api";
import {eventDirectionView, eventInterpretationText, eventJudgeView, eventJudgementExplanation} from "@/lib/event-view";
import {eventTimeText, sourceLabel} from "@/lib/format";
import {ReaderBrief, ResearchDisclosure} from "@/components/ui/reader-brief";
import {readerExcerpt, readerCautions} from "@/lib/reader-copy";
import {StockLink} from "@/components/stock-link";
import {inspectionClick, useInspection} from "@/components/inspection/inspection-context";
import {themesUrl} from "@/lib/routing";

/** Object-by-object readout. A shared theme never supplies a stock's direction. */
export function EventOverview({event, symbol}: {event: EventSummary; symbol?: string | null}) {
  const {open: inspect} = useInspection();
  const ref = event.interpretation_ref;
  const identity = `${event.id}:${JSON.stringify(event)}`;
  const inactive = !event.is_active || ref?.state === "withdrawn";
  const stage = inactive ? "已失效或撤回" : ref?.state === "pending" ? "解释正在等待复核" : event.certainty === "proposed" ? "仍是计划，尚未落地" : event.certainty === "rumor" ? "传闻，尚未证实" : event.certainty === "done" ? "来源称已发生，未独立核实" : "进展还不清楚";
  return <section className="min-w-0 space-y-3 pb-3 text-xs" aria-label="事件关系概览">
    <ReaderBrief label="消息要点摘录" headline={readerExcerpt(event.summary || event.title)} facts={[
      {label:"消息进展",value:stage},
      {label:"来源时间",value:`${sourceLabel(event.source)} · ${event.published_at ? eventTimeText(event.published_at) : "发布时间未记录"}`},
    ]} cautions={[...readerCautions([event.summary || ""]), ...(inactive ? ["原记录仅供复盘，不作为当前依据"] : [])]}/>
    {event.directions.length ? <section aria-label="关联对象与依据" className="divide-y divide-zinc-200 dark:divide-zinc-700/60">{event.directions.map((row,index) => {
      const direction = eventDirectionView(row,event,symbol);
      const target = row.target_type === "symbol" ? <StockLink symbol={row.target} preferModal>{row.target}</StockLink> : row.target_type === "theme" ? <a href={themesUrl(row.target)} onClick={inspectionClick(inspect,{kind:"themes",focus:row.target})}>{row.target}</a> : row.target;
      return <ReaderBrief key={`${row.target}:${index}`} label={`关联 ${row.target}`} facts={[
        {label:row.target_type === "symbol" ? "涉及个股" : "涉及方向",value:target},
        {label:"已有线索",value:inactive ? "原记录已失效" : row.matched_by === "llm_aux" ? "只有AI猜测，尚未验证" : direction.text},
        {label:"怎么相关",value:readerExcerpt(row.basis || row.chain) || "还没有保存关联理由"},
      ]}/>;
    })}</section> : <p className="text-zinc-600 dark:text-zinc-400">没有已记录的题材或个股关联。不能从标题自动推导受益股。</p>}
    <ResearchDisclosure identity={identity} label="完整判读与来源记录">
      <p>{eventInterpretationText(event)}</p><p>{eventJudgeView(event).title}</p><p>{eventJudgementExplanation(event)}</p>
      {event.summary && <blockquote className="whitespace-pre-wrap leading-relaxed" aria-label="列表来源摘要">{event.summary}</blockquote>}
      {event.directions.map((row,index) => <section key={index} aria-label={`原始关联 ${row.target}`}><h4>{row.target}</h4><p>{eventDirectionView(row,event,symbol).title}</p><p>方向 {row.direction} · 强度 {row.strength} · 匹配 {row.matched_by || "未记录"}</p>{row.basis && <blockquote className="whitespace-pre-wrap">{row.basis}</blockquote>}{row.chain && <blockquote className="whitespace-pre-wrap">{row.chain}</blockquote>}</section>)}
      <p>解释可用时间：{ref?.available_at ? eventTimeText(ref.available_at) : "未记录"}</p><p>判读记录时间：{event.judged_at ? eventTimeText(event.judged_at) : "未记录"}</p>{event.llm_judged_at && <p>辅助尝试时间：{eventTimeText(event.llm_judged_at)}</p>}
      <p>来源级别 {event.source_tier}/5。方向和关联记录不证明消息属实或公司受益。</p>
      {event.url ? <a href={event.url} target="_blank" rel="noopener noreferrer" data-action="quiet">查看消息原文</a> : <p>原文链接缺失。</p>}
    </ResearchDisclosure>
  </section>;
}
