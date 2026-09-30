"use client";

import { useState } from "react";
import { useResource } from "@/hooks/use-polling-fetch";
import { getOpportunities } from "@/lib/api/picks";
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
export function OpportunityEvidencePanel() {
  const [date, setDate] = useState(() => bjDate(new Date().toISOString()));
  const [limit, setLimit] = useState(10);
  const result = useResource(() => getOpportunities(date), { key: date, intervalMs: 60_000 });
  const data = result.error ? undefined : result.data;
  return <section className="mt-3 border-t border-zinc-200 pt-3 text-xs text-zinc-700 dark:border-zinc-800 dark:text-zinc-300">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h4 className="text-sm font-semibold">机会依据与等待条件</h4>
      <label className="flex items-center gap-2">决定日期
        <input aria-label="机会决定日期" type="date" value={date} onChange={e => { if (e.target.value) { setDate(e.target.value); setLimit(10); } }}
          className="min-h-11 rounded border border-zinc-300 bg-transparent px-2 dark:border-zinc-600 focus-visible:outline-2 focus-visible:outline-sky-500" />
      </label>
    </div>
    {!!result.error && <p role="alert" className="mt-2 text-amber-800 dark:text-amber-300">机会依据读取失败，请稍后重试。</p>}
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
          {card.hypotheses.map(h => <div key={h.opportunity_id} className="max-w-prose space-y-1 py-2 leading-relaxed">
            <p className="font-medium">{STATES[h.state] ?? h.state} · {h.source_theme || SCENARIOS[h.scenario] || h.scenario}</p>
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
