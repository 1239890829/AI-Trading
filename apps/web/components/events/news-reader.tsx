"use client";

import { useEffect, useRef, useState } from "react";
import { getEventHistory, getImpactEvents, type ImpactEvent } from "@/lib/api";
import { useResource } from "@/hooks/use-resource";
import { useIncremental } from "@/hooks/use-incremental";
import { IncrementalSentinel } from "@/components/ui/incremental-sentinel";
import { NewsModal, type NewsModalItem } from "@/components/news-modal";
import { eventNewsItem, eventJudgeView } from "@/lib/event-view";
import { dateTimeTextBJ } from "@/lib/format";
import { StockLink } from "@/components/stock-link";
import { ReaderBrief } from "@/components/ui/reader-brief";

type Filter = "all" | "important" | "selected" | "merger";
const FILTERS: {key: Filter; label: string}[] = [{key:"all",label:"全部快讯"},{key:"important",label:"重要"},{key:"selected",label:"关联入选"},{key:"merger",label:"并购重组"}];
const PHASE: Record<string,string> = {done:"已报告",proposed:"拟议",rumor:"传闻",unknown:"阶段未核"};
const CHANGE: Record<string,string> = {initial:"首次记录",corroboration:"新增来源",revision:"原文修订",variant:"不同表述"};

function History({id}: {id:number}) {
  const history = useResource(()=>getEventHistory(id), {key:`event-history:${id}`, marketHours:false});
  if (history.error) return <p role="alert" className="text-xs text-amber-700 dark:text-amber-300">连续记录读取失败。<button type="button" className="quiet-action" onClick={history.refresh}>重试</button></p>;
  if (!history.data) return <p role="status" className="text-xs text-zinc-500">正在读取原始观察…</p>;
  const observations=history.data.observations ?? [];
  return <div className="space-y-2" aria-label="连续报道与原文变更">
    <p className="text-[11px] text-zinc-500">{observations.length}次原始观察 · {history.data.interpretations?.length ?? 0}版解释；新增来源不等于独立证实。</p>
    {observations.length === 0 && <p className="text-xs text-zinc-500">旧记录未保存观察版本，无法还原连续过程。</p>}
    {observations.map(o=><article key={o.id} className="border-t border-zinc-200 py-2 dark:border-zinc-700">
      <div className="flex flex-wrap gap-x-3 text-[11px] text-zinc-500"><strong>{CHANGE[o.change_kind] ?? "观察"}</strong><span>{o.source}</span><span>发布 {o.source_published_at ? dateTimeTextBJ(o.source_published_at) : "未提供"}</span><span>首次可用 {dateTimeTextBJ(o.available_at)}</span></div>
      <p className="mt-1 text-xs">{o.title}</p>
      {o.summary && <p className="mt-1 line-clamp-3 text-xs text-zinc-600 dark:text-zinc-400">{o.summary}</p>}
      {o.url && /^https?:\/\//.test(o.url) && <a className="quiet-action text-xs" href={o.url} target="_blank" rel="noreferrer">核对该版原文</a>}
    </article>)}
  </div>;
}

/** Shared news-reading consumer. One existing scroll owner, saved facts only. */
export function NewsReader({active=true, compact=false, refreshToken=0}: {active?:boolean;compact?:boolean;refreshToken?:number}) {
  const [filter,setFilter]=useState<Filter>("all");
  const [query,setQuery]=useState("");
  const [expanded,setExpanded]=useState<number|null>(null);
  const [article,setArticle]=useState<NewsModalItem|null>(null);
  const events=useResource(()=>getImpactEvents(true,200,"time",true), {key:"news-reading",intervalMs:60_000,marketHours:false,enabled:active});
  const prior=useRef({active,refreshToken});
  const refresh=events.refresh;
  useEffect(()=>{const p=prior.current;prior.current={active,refreshToken};if(active && p.active && p.refreshToken!==refreshToken)refresh();},[active,refreshToken,refresh]);
  const all=events.data?.items;
  const rows=(all ?? []).filter(e=>(filter!=="important" || e.impact_level==="L1")
    && (filter!=="selected" || !!e.selection_links?.length)
    && (filter!=="merger" || /并购|重组|收购|控制权|股权转让|吸收合并/.test(e.title+" "+(e.summary??"")))
    && (!query.trim() || (e.title+" "+(e.summary??"")+" "+e.source).toLowerCase().includes(query.trim().toLowerCase())));
  const incremental=useIncremental(rows,{resetKey:`${filter}/${query}`});
  const context=events.data?.selectionContext;
  const dates=[...new Set((context?.items ?? []).map(x=>x.trade_date))].sort();
  function row(e:ImpactEvent) {
    const judge=eventJudgeView(e);
    const links=e.selection_links ?? [];
    return <li key={e.id} className="border-b border-zinc-200 py-3 dark:border-zinc-800">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-zinc-600 dark:text-zinc-400"><time>{e.published_at ? dateTimeTextBJ(e.published_at) : "发布时刻未提供"}</time><span>{e.source}</span>{e.impact_level==="L1" && <strong className="text-amber-700 dark:text-amber-300">重要</strong>}<span>{e.fact_kind==="opinion" ? "观点" : e.fact_kind==="rumor" ? "传闻" : "报道事实"}</span><span>{PHASE[e.certainty] ?? "阶段未核"}</span>{!e.is_active && <span>超出机会时效</span>}</div>
      <button type="button" className="mt-1 block w-full text-left text-sm font-medium leading-relaxed hover:underline" onClick={()=>setArticle(eventNewsItem(e))}>{e.title}</button>
      {e.summary && e.summary!==e.title && <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">{e.summary}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]"><span className={judge.cls} title={judge.title}>{judge.text}</span>
        {links.map(l=><StockLink key={`${l.scope}:${l.trade_date}:${l.symbol}`} symbol={l.symbol}><span className="underline decoration-dotted underline-offset-4">{l.name} · {l.scope==="daily"?"每日":"盘中"} {l.trade_date} · {l.relation==="original_evidence"?(l.reference_state==="current_version"?"原版引用":l.reference_state==="different_version"?"同事件不同版本":"引用版本未核"):"同股关联"}</span></StockLink>)}
        <button type="button" className="quiet-action ml-auto" aria-expanded={expanded===e.id} aria-controls={`news-history-${e.id}`} onClick={()=>setExpanded(expanded===e.id?null:e.id)}>{expanded===e.id?"收起连续记录":"连续记录与依据"}</button>
      </div>
      {expanded===e.id && <section id={`news-history-${e.id}`} className="mt-3 rounded-lg bg-zinc-100/60 p-3 dark:bg-zinc-800/40"><History id={e.id}/>{!!links.length && <p className="mt-2 text-[11px] text-zinc-500">同股关联不等于入选原因；名单引用只证明该版名单保存过这条依据，不把现有修订倒填首次判断。</p>}</section>}
    </li>;
  }
  return <section className="flex h-full min-h-0 flex-col" aria-label="新闻阅读">
    <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
      {FILTERS.map(f=><button key={f.key} type="button" className={`ui-filter px-2 py-1 text-xs ${filter===f.key?"ui-filter-selected":""}`} aria-pressed={filter===f.key} onClick={()=>setFilter(f.key)}>{f.label}</button>)}
      {!compact && <input type="search" aria-label="查找快讯" placeholder="查标题、正文或来源" value={query} onChange={e=>setQuery(e.target.value)} className="ml-auto min-w-0 rounded border border-zinc-300 bg-transparent px-2 py-1 text-xs dark:border-zinc-700"/>}
    </div>
    <div className="shrink-0 px-3 py-1 text-[11px] text-zinc-600 dark:text-zinc-400" role="status">{all ? `${rows.length} / ${all.length}条已读取` : "正在读取快讯…"} · {events.data?.coverage?.possibly_truncated?"最近200条范围，非全量":"当前已存范围"}{filter==="selected" && ` · 名单日期 ${dates.join(" / ") || "未取得"}`}</div>
    {(context?.state==="error" || context?.state==="partial") && <p role="alert" className="px-3 text-xs text-amber-700 dark:text-amber-300">{context?.state==="error"?"关联入选读取失败。":context?.missing.join("；")}不能将空关联解释为没有影响。</p>}
    {!!events.data?.officialSources?.length && <details className="shrink-0 px-3 py-1 text-[11px] text-zinc-600 dark:text-zinc-400"><summary className="cursor-pointer">来源覆盖与时点</summary>{events.data.officialSources.map(s=><p key={s.source}>{s.source} · {!s.enabled?"已停用":s.state==="ready"?"最近读取成功":s.state==="degraded"?"来源读取失败":"尚未观察"} · 最新发布 {s.latest_published_at?dateTimeTextBJ(s.latest_published_at):"未取得"}；仅此RSS窗口，不是完整历史。{s.last_error&&` ${s.last_error}`}</p>)}</details>}
    <div className="min-h-0 flex-1 overflow-y-auto px-3">
      {Boolean(events.error) && <p role="alert" className="py-2 text-xs text-amber-700 dark:text-amber-300">新闻读取失败。{all?"保留上次内容，当前状态未核。":""}<button type="button" className="quiet-action" onClick={events.refresh}>重试</button></p>}
      {!all && !events.error && <div aria-label="正在读取新闻" className="space-y-3 py-3">{[0,1,2].map(x=><div key={x} className="h-14 animate-pulse rounded bg-zinc-100 dark:bg-zinc-800"/>)}</div>}
      {all && !rows.length && !events.error && <ReaderBrief label="当前快讯空态" headline="该范围暂无匹配快讯" cautions={[filter==="selected"?"当前已保存名单中没有已绑定的同股事件；不代表公司没有相关事实。":"可切换筛选或清除搜索；缺失与来源失败不当作实测零值。"]}/>}
      <ul>{incremental.shown.map(row)}</ul>
      <IncrementalSentinel sentinelRef={incremental.sentinelRef} visible={incremental.visible} total={rows.length} unit="条快讯" testId="news-reading-sentinel"/>
    </div><NewsModal item={article} onClose={()=>setArticle(null)}/>
  </section>;
}
