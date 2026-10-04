"use client";

import { useRef, useState } from "react";
import { useResource } from "@/hooks/use-polling-fetch";
import { getOpportunities, type OpportunityView } from "@/lib/api/picks";
import { bjDate } from "@/lib/format";

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

/** One read-only consumer for versioned decisions, without new rankings or actions. */
export function OpportunityEvidencePanel({date: requestedDate, onDateChange}: {date?: string; onDateChange?: (date: string) => void} = {}) {
  const [localDate, setDate] = useState(() => bjDate(new Date().toISOString()));
  const date = requestedDate?.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3") ?? localDate;
  const [inspectionMode, setInspectionMode] = useState<"follow" | "pin" | "compare">("follow");
  const inspectOrigin = useRef<HTMLButtonElement | null>(null);
  const dateInput = useRef<HTMLInputElement | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [pinned, setPinned] = useState<{date: string; card: OpportunityView["cards"][number]} | null>(null);
  const [compared, setCompared] = useState<{date: string; card: OpportunityView["cards"][number]} | null>(null);
  const [limit, setLimit] = useState(10);
  const result = useResource(() => getOpportunities(date), { key: date, intervalMs: 60_000 });
  const data = result.error ? undefined : result.data;
  const selected = data?.cards.find(card => card.symbol === selectedSymbol);
  const inspected = inspectionMode === "follow" ? selected && {date, card: selected} : pinned;
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
    <div className="evidence-toolbar mt-3 flex flex-wrap items-center gap-2" aria-label="证据联动模式">{([["follow", "跟随"], ["pin", "固定版本"], ["compare", "对比"]] as const).map(([mode, label]) => <button key={mode} aria-pressed={inspectionMode === mode} onClick={() => { if (mode !== "follow" && selected) setPinned({date, card: selected}); setCompared(null); setInspectionMode(mode); }} className="evidence-mode">{label}</button>)}<span className="evidence-help">选择“查看依据”核对；固定后保留当前版本。</span></div>
    {inspected && <aside aria-label="当前证据侧栏" className="mt-3 grid gap-3 rounded-lg border border-zinc-300 p-3 lg:grid-cols-2 dark:border-zinc-600">{[inspected, ...(inspectionMode === "compare" && compared ? [compared] : [])].map((item, index) => <div key={index}><h5 className="font-semibold">{item.card.name} {item.card.symbol} · {item.date}{inspectionMode !== "follow" && index === 0 ? "（固定快照）" : ""}</h5>{item.card.hypotheses.map(h => <div key={h.opportunity_id} className="mt-2 space-y-1 break-words"><p><span className="opportunity-state" data-state={h.state}>{STATES[h.state] ?? h.state}</span> · {h.decision_version ?? `研究观察 ${h.observation_id ?? "身份待核"}`}</p><p>来源 {h.source} · {h.as_of}</p><p>依据 {h.reasons.join("；") || "未记录"}</p><p>等待 {h.unknowns.join("；") || "须核动作前条件"}</p><p>{h.execution_blocker}</p><p>参考 {h.reference.price ?? "缺失"}，非成交。</p></div>)}</div>)}{inspectionMode === "compare" && !compared && <p>请选择第二个对象／日期／版本。不同 scope 不合并收益。</p>}<button onClick={() => { setSelectedSymbol(null); setPinned(null); setCompared(null); const origin = inspectOrigin.current; if (origin?.isConnected && !origin.closest("details:not([open])")) origin.focus(); else dateInput.current?.focus(); }}>关闭侧栏</button></aside>}
    {!!result.error && <p role="alert" className="mt-2 text-amber-800 dark:text-amber-300">机会依据读取失败，请稍后重试。<button onClick={result.refresh}>重试读取</button></p>}
    {!data && !result.error && <p role="status" className="py-3">正在读取已有决定…</p>}
    {data && <>
      <p className="mt-2">{data.trade_date} · {data.cards.length} 只 · {data.disclaimer}</p>
      <p className="mt-1 text-zinc-600 dark:text-zinc-400">{data.coverage}</p>
      {data.cards.length === 0 && <p className="py-3">{data.state === "collected_empty" ? "本轮已记录，候选为空。" : data.state === "unavailable" ? "已有运行记录，但来源未就绪，不能据此判断没有机会。" : "该日期尚无已归档决定，不能据此判断没有机会。"}</p>}
      <div className="mt-3 divide-y divide-zinc-200 dark:divide-zinc-800">
        {data.cards.slice(0, limit).map(card => <details key={card.symbol} className="py-2">
          <summary className="min-h-11 cursor-pointer py-2 font-medium focus-visible:outline-2 focus-visible:outline-sky-500">
            {card.name} {card.symbol} · {card.hypotheses.length} 条独立假设
          </summary>
          <button onClick={event => { inspectOrigin.current = event.currentTarget; inspect(card); }} className="min-h-11 rounded border border-zinc-300 px-3 dark:border-zinc-600">查看 {card.symbol} 依据</button>
          {card.hypotheses.map(h => <div key={h.opportunity_id} className="max-w-prose space-y-1 py-2 leading-relaxed">
            <p className="font-medium"><span className="opportunity-state" data-state={h.state}>{STATES[h.state] ?? h.state}</span> · {h.source_theme || SCENARIOS[h.scenario] || h.scenario}</p>
            {!!h.routes?.length && <p>观察路径：{h.routes.map(route => ROUTES[route] ?? route).join(" / ")}</p>}
            <p>依据：{h.reasons.join("；") || "尚无已记录依据"}</p>
            <p>还缺什么：{h.unknowns.join("；") || "请核对原始证据及动作前条件"}</p>
            <p className="text-amber-800 dark:text-amber-300">{h.execution_blocker}</p>
            <p>首次观察 {h.first_seen}；本版来源时间 {h.as_of}</p>
            <p className="break-all text-zinc-600 dark:text-zinc-400">{h.source} · {h.decision_version ?? `研究观察 ${h.observation_id ?? "身份待核"}`}</p>
            <p>参考价 {h.reference.price ?? "缺失"}，不是成交价。</p>
            <details>
              <summary className="min-h-11 cursor-pointer py-2 focus-visible:outline-2 focus-visible:outline-sky-500">来源与反证记录</summary>
              <pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify({
                事件引用: h.event_refs ?? [], 知识依据: h.kb_refs ?? {}, 反证: h.counterevidence ?? [],
                原始证据: h.evidence ?? {}, 进入条件: h.entry_conditions ?? {},
              }, null, 2)}</pre>
            </details>
          </div>)}
        </details>)}
      </div>
      {data.cards.length > limit && <button onClick={() => setLimit(n => n + 10)} className="mt-2 min-h-11 rounded border border-zinc-300 px-3 dark:border-zinc-600 focus-visible:outline-2 focus-visible:outline-sky-500">再显示 10 只</button>}
      {data.runs.map(run => <div key={run.run_id} className="mt-2 leading-relaxed">
        <p>{run.scenario} · 来源状态 {run.data_state} · {run.as_of}</p>
        {run.gate_counts && Object.entries(run.gate_counts).map(([key, count]) => <span key={key} className="mr-3">{GATES[key] ?? key} {count} 个题材</span>)}
      </div>)}
    </>}
  </section>;
}
