"use client";

import { SelectionRail } from "@/components/ui/selection-rail";
import { useMemo, useRef, useState } from "react";
import { useExitPresence } from "@/hooks/use-exit-presence";
import { useResource } from "@/hooks/use-polling-fetch";
import { getOpportunities, type OpportunityView } from "@/lib/api/picks";
import { bjDate, dateTimeTextBJ } from "@/lib/format";
import {readerExcerpt, readerCautions} from "@/lib/reader-copy";
import {ReaderBrief, ResearchDisclosure} from "@/components/ui/reader-brief";
import { ConditionList, FactCards, FactTimeline } from "@/components/ui/explanation-map";
import { ReadingRow, readingRowClasses } from "@/components/ui/reading-row";

const STATES: Record<string, string> = {
  observing: "观察", priority: "重点观察", waiting: "等待", triggered: "条件触发",
  unknown: "待核", weakened: "转弱", stale: "待更新", expired: "已到期",
};
const GATES: Record<string, string> = {
  not_concentrated: "题材未达集中条件", missing_catalog: "缺官方成分容器",
  mined_with_candidates: "有联动候选", mined_empty_unavailable: "缺行情，无法判定",
  mined_empty_no_eligible: "本轮无符合条件的成分",
};
const SCENARIOS: Record<string, string> = {
  intraday_opportunity: "题材联动", buy_point: "盘中触发", leader_research: "研究观察",
};
const ROUTES: Record<string, string> = {
  public_event: "公共事件", company_event: "公司独立事件", trend: "趋势强势",
};

function OpportunityCardReading({card, date}: {card: OpportunityView["cards"][number]; date: string}) {
  return <div aria-label={`${card.name || card.symbol}机会说明`} className="space-y-3">{card.hypotheses.map(h => <section key={h.opportunity_id} className="border-b border-zinc-200 py-2 dark:border-zinc-700">
    <h5 className="font-medium">{h.source_theme || SCENARIOS[h.scenario] || "观察记录"} · {STATES[h.state] || "待核实"}</h5>
    <ReaderBrief label="观察理由简述" facts={[{label:"为什么关注",value:readerExcerpt(h.reasons[0]) || "尚未保存理由"},{label:"还要等什么",value:readerExcerpt(h.execution_blocker) || "当前条件尚未确认"}]} cautions={[...readerCautions(h.reasons), ...(h.unknowns.length ? [`还有 ${h.unknowns.length} 项待确认`] : []), "状态触发不代表可以买入"]}/>
    <ResearchDisclosure label="这条观察的完整依据" identity={`${date}:${h.opportunity_id}:${h.decision_version}`}>
      <p>{STATES[h.state] || h.state} · {h.decision_version || `研究观察 ${h.observation_id || "未知"}`}</p>
      {!!h.routes?.length && <p>观察路径：{h.routes.map(route => ROUTES[route] || route).join(" / ")}</p>}
      <ConditionList label="已记录依据" items={h.reasons}/><ConditionList label="还缺什么" items={h.unknowns} tone="warning"/>
      <p>执行限制：{h.execution_blocker || "未记录，不能推定可执行"}</p><p>参考价 {h.reference.price ?? "缺失"}，不是成交价。</p>
      <FactTimeline label="观察时点" items={[{label:"首次观察",value:h.first_seen,kind:"time"},{label:"本版来源时间",value:h.as_of,kind:"time"}]}/>
      <FactCards label="记录身份" items={[{label:"来源",value:h.source},{label:"版本",value:h.decision_version || "未记录"}]}/>
      <pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify({事件引用:h.event_refs || [],知识依据:h.kb_refs || {},反证:h.counterevidence || [],原始证据:h.evidence || {},进入条件:h.entry_conditions || {}},null,2)}</pre>
    </ResearchDisclosure>
  </section>)}</div>;
}

/** One read-only consumer for versioned decisions, without new rankings or actions. */
export function OpportunityEvidencePanel({date: requestedDate, onDateChange, active = true}: {date?: string; onDateChange?: (date: string) => void; active?: boolean} = {}) {
  const [localDate, setDate] = useState(() => bjDate(new Date().toISOString()));
  const date = requestedDate?.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3") ?? localDate;
  const [inspectionMode, setInspectionMode] = useState<"follow" | "pin" | "compare">("follow");
  const inspectOrigin = useRef<HTMLButtonElement | null>(null);
  const dateInput = useRef<HTMLInputElement | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [pinned, setPinned] = useState<{date: string; card: OpportunityView["cards"][number]} | null>(null);
  const [compared, setCompared] = useState<{date: string; card: OpportunityView["cards"][number]} | null>(null);
  const [limit, setLimit] = useState(10);
  const result = useResource(() => getOpportunities(date), { key: date, intervalMs: 60_000, enabled: active });
  const data = result.data;
  const selected = data?.cards.find(card => card.symbol === selectedSymbol);
  const inspected = useMemo(() => inspectionMode === "follow" ? selected ? {date, card: selected} : null : pinned, [inspectionMode, selected, date, pinned]);
  const inspectionPresence = useExitPresence(active ? inspected : null);
  function inspect(card: OpportunityView["cards"][number]) {
    setSelectedSymbol(card.symbol);
    if (inspectionMode === "pin") setPinned({date, card});
    if (inspectionMode === "compare") {
      if (!pinned) setPinned({date, card}); else setCompared({date, card});
    }
  }
  return <section className="ui-card evidence-sheet border border-zinc-200 p-4 text-xs text-zinc-700 dark:border-zinc-800 dark:text-zinc-300">
    <div className="evidence-heading flex flex-wrap items-center justify-between gap-2">
      <h4 className="text-sm font-semibold">机会依据与等待条件</h4>
      <label className="flex items-center gap-2">决定日期
        <input ref={dateInput} aria-label="机会决定日期" type="date" value={date} onChange={e => { if (e.target.value) { if (onDateChange) onDateChange(e.target.value); else setDate(e.target.value); setLimit(10); } }}
          className="evidence-date rounded border border-zinc-300 bg-transparent px-2 dark:border-zinc-600 focus-visible:outline-2 focus-visible:outline-sky-500" />
      </label>
    </div>
    <div className="evidence-toolbar mt-3 flex flex-wrap items-center gap-2" ><SelectionRail activeKey={inspectionMode} label="证据联动模式">{([["follow", "跟随"], ["pin", "固定版本"], ["compare", "对比"]] as const).map(([mode, label]) => <button key={mode} aria-pressed={inspectionMode === mode} onClick={() => { if (mode !== "follow" && selected) setPinned({date, card: selected}); setCompared(null); setInspectionMode(mode); }} className="evidence-mode">{label}</button>)}</SelectionRail><span className="evidence-help">选择“查看依据”核对；固定后保留当前版本。</span></div>
    <div className="evidence-workarea" data-inspecting={inspected ? "true" : "false"}><div className="evidence-records" tabIndex={0} role="region" aria-label="机会依据记录">
    {!!result.error && <p role="alert" className="mt-2 text-amber-800 dark:text-amber-300">机会依据读取失败。{data ? "以下保留上次读取结果，当前状态待重试核实。" : "请稍后重试，未读到决定不代表没有机会。"}<button onClick={result.refresh}>重试读取</button></p>}
    {!data && !result.error && <p role="status" className="py-3">正在读取已有决定…</p>}
    {data && <>
      <p className="mt-2">{data.trade_date} · {data.cards.length} 只 · {data.disclaimer}</p>
      <p className="mt-1 text-zinc-600 dark:text-zinc-400">{data.coverage}</p>
      {data.cards.length === 0 && <p className="py-3">{data.state === "collected_empty" ? "本轮已记录，候选为空。" : data.state === "unavailable" ? "已有运行记录，但来源未就绪，不能据此判断没有机会。" : "该日期尚无已归档决定，不能据此判断没有机会。"}</p>}
      <div className="mt-3 divide-y divide-zinc-200 dark:divide-zinc-800">
        {data.cards.slice(0, limit).map(card => <ReadingRow key={card.symbol} className="py-2">
          <p className="font-medium">{card.name} {card.symbol} · {card.hypotheses.length} 条独立假设</p>
          <button type="button" data-reading-primary onClick={event => { inspectOrigin.current = event.currentTarget; inspect(card); }} className={`${readingRowClasses.primary} min-h-11 py-2`}>查看 {card.symbol} 依据</button>
          <p className="text-zinc-600 dark:text-zinc-400">{card.hypotheses.map(h => `${h.source_theme || SCENARIOS[h.scenario] || h.scenario} · ${STATES[h.state] ?? h.state}`).join(" / ")}</p>
        </ReadingRow>)}
      </div>
      {data.cards.length > limit && <button onClick={() => setLimit(n => n + 10)} className="mt-2 min-h-11 rounded border border-zinc-300 px-3 dark:border-zinc-600 focus-visible:outline-2 focus-visible:outline-sky-500">再显示 10 只</button>}
      {data.runs.map(run => <div key={run.run_id} className="mt-2 leading-relaxed">
        <p>{run.scenario} · 来源状态 {run.data_state} · {dateTimeTextBJ(run.as_of, {seconds:true})}</p>
        {run.gate_counts && Object.entries(run.gate_counts).map(([key, count]) => <span key={key} className="mr-3">{GATES[key] ?? key} {count} 个题材</span>)}
      </div>)}
    </>}
    </div>
    {inspectionPresence.value && <aside aria-label="当前证据侧栏" data-motion-state={active && inspected ? "open" : "closed"} aria-hidden={!active || !inspected || undefined} inert={!active || !inspected} className="motion-float evidence-inspection grid gap-3 rounded-lg border border-zinc-300 p-3 dark:border-zinc-600" tabIndex={0}>{[inspectionPresence.value, ...(inspectionMode === "compare" && compared ? [compared] : [])].map((item, index) => <div key={index}><h5 className="font-semibold">{item.card.name} {item.card.symbol} · {item.date}{inspectionMode !== "follow" && index === 0 ? "（固定快照）" : ""}</h5><OpportunityCardReading card={item.card} date={item.date} /></div>)}{inspectionMode === "compare" && !compared && <p>请选择第二个对象／日期／版本。不同 scope 不合并收益。</p>}<button onClick={() => { setSelectedSymbol(null); setPinned(null); setCompared(null); const origin = inspectOrigin.current; if (origin?.isConnected) origin.focus(); else dateInput.current?.focus(); }}>关闭侧栏</button></aside>}
    </div>
  </section>;
}
