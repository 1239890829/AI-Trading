"use client";

import { useState } from "react";
import { useResource } from "@/hooks/use-polling-fetch";
import { getLeaderResearch } from "@/lib/api/picks";
import { dateTimeTextBJ, pctColor, pctText } from "@/lib/format";
import {readerExcerpt, readerCautions} from "@/lib/reader-copy";
import {ReaderBrief, ResearchDisclosure} from "@/components/ui/reader-brief";
import {StockLink} from "@/components/stock-link";
import { ConditionList, FactCards, FactTimeline } from "@/components/ui/explanation-map";

const ROUTES: Record<string, string> = { public_event: "公共事件", company_event: "公司事件", trend: "相对强势" };
const STATES: Record<string, string> = { priority: "重点观察", observing: "观察", weakened: "转弱" };
const HEALTH: Record<string, string> = {
  ready: "采集中", closing_census: "收盘对照已记录", outside_session: "休市时段",
  calendar_unavailable_or_closed: "非交易日或日历不可用", stale: "行情过期",
  unavailable: "行情不可用", degraded: "行情降级，暂停采集", error: "采集异常", not_started: "采集尚未启动",
};

/** A research consumer within the existing follow ledger, without order controls. */
export function LeaderResearchPanel({date: requestedDate, onDateChange}: {date?: string; onDateChange?: (date: string | undefined) => void} = {}) {
  const [localDate, setDate] = useState("");
  const date = requestedDate?.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3") ?? localDate;
  const [limit, setLimit] = useState(10);
  const result = useResource(() => getLeaderResearch(date || undefined), { key: date, intervalMs: 60_000 });
  const error = result.error ? String(result.error) : undefined;
  const data = result.data;

  return <div className="ui-card hunting-research mt-4 border border-zinc-200 p-4 text-xs text-zinc-700 dark:border-zinc-800 dark:text-zinc-300">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h4 className="font-semibold">强势候选持续研究</h4>
      <label className="flex items-center gap-2">查看日期
        <input aria-label="研究日期" type="date" value={date} onChange={e => { if (onDateChange) onDateChange(e.target.value || undefined); else setDate(e.target.value); setLimit(10); }}
          className="rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-600 focus-visible:outline-2 focus-visible:outline-sky-500" />
      </label>
    </div>
    <div className="hunting-research-records" tabIndex={0} role="region" aria-label="强势候选研究记录">
    {error && <p role="alert" className="mt-2 text-amber-800 dark:text-amber-300">研究记录读取失败：{error}{data ? "。以下保留上次读取结果，当前状态待重试核实。" : "。未读到记录不代表没有机会。"}</p>}
    {!data && !error && <p role="status" className="py-3">正在读取研究记录…</p>}
    {data && <>
      <p className="mt-2">{data.trade_date} · {data.cards.length} 只 · 当前采集：{HEALTH[data.collector.state] ?? "状态未知"}</p>
      {data.collector.checked_at && <p className="mt-1">最近检查 {dateTimeTextBJ(data.collector.checked_at, {seconds:true})}</p>}
      {data.collector.reason && <p className="mt-1 text-amber-800 dark:text-amber-300">{data.collector.reason}</p>}
      <p className="mt-1 text-zinc-600 dark:text-zinc-400">{data.disclaimer}</p>
      {data.cards.length === 0 && <p className="py-3">{data.state === "collected_empty" ? "本日有收盘对照，尚无盘中候选记录。" : "尚无该日期的研究观察；未采集不代表没有机会。"}</p>}
      <div aria-label="逐股研究资料" className="divide-y divide-zinc-200 dark:divide-zinc-700">{data.cards.slice(0,limit).map(card => <section key={card.symbol} className="py-3">
        <h5><StockLink symbol={card.symbol} preferModal>{card.name} {card.symbol}</StockLink><span className={`ml-2 tabular-nums ${pctColor(card.pct)}`}>{pctText(card.pct)}（观察时）</span></h5>
        <ReaderBrief label={`${card.symbol}研究简述`} facts={[{label:"观察状态",value:`${STATES[card.state] || "待核实"}${card.stale ? " · 数据待更新" : ""}${card.expired ? " · 跟踪期已结束" : ""}`},{label:"为什么关注",value:readerExcerpt(card.reasons[0]) || "尚未保存理由"},{label:"还要等什么",value:readerExcerpt(card.entry_state) || "当前参与条件尚未确认"}]} cautions={[...readerCautions(card.reasons), ...(card.unknowns.length ? [`还有 ${card.unknowns.length} 项待确认`] : []), ...(card.invalidation ? ["有失效条件，见依据"] : [])]}/>
        <ResearchDisclosure label="研究完整依据" identity={`${data.trade_date}:${data.version}:${card.symbol}`}>
          <p>观察路径：{card.routes.map(r => ROUTES[r] || r).join(" / ")}</p>
          <FactTimeline label="原观察时点" items={[{label:"首次观察",value:card.first_seen,kind:"time"},{label:"行情时点",value:card.source_as_of,kind:"time"}]}/><FactCards label="观察来源" items={[{label:"来源",value:card.source}]}/>
          <ConditionList label="入池依据" items={card.reasons}/><p>参与条件：{card.entry_state}</p><ConditionList label="待核实" items={card.unknowns} tone="warning"/>
          {card.event_refs.map((ref,i) => <section key={i}><h5>{ref.title}</h5><p>{ref.source} · {ref.link_kind === "theme" ? "仅题材成员关联，未证明个股受益" : ref.link_kind === "symbol" ? "个股关联记录" : "关联类型待核"}</p><p>{ref.basis}</p><FactTimeline label="事件时点" items={[{label:"发布",value:ref.published_at,kind:"time"},{label:"可用时间",value:ref.available_at,kind:"time"}]}/></section>)}
          <p>下一观察：{card.next_check}</p><p>失效条件：{card.invalidation || "未记录"}</p>
          <div>{Object.entries(card.outcomes).map(([h,o]) => <p key={h}>{h.toUpperCase()}：{o.state === "observed" ? pctText(o.reference_change_pct) : o.state === "missing" ? "缺收盘数据" : o.state === "unknown" ? "日历缺失" : "待观察"}</p>)}</div>
        </ResearchDisclosure>
      </section>)}</div>
      {data.cards.length > limit && <button className="mt-2 rounded border border-zinc-300 px-3 py-1 dark:border-zinc-600 focus-visible:outline-2 focus-visible:outline-sky-500 active:opacity-70" onClick={() => setLimit(n => n + 10)}>再显示 10 只（共 {data.cards.length} 只）</button>}
      <section className="mt-3" aria-label="收盘漏选与转弱复核">
        <h5 className="font-medium">收盘漏选与转弱复核</h5>
        {data.review.state !== "observed" ? <p className="mt-2">缺少当日收盘对照，不能判断漏选。</p> : <div className="mt-2 space-y-1">
          <p>收盘来源覆盖 {data.review.universe_count} 只主板股票；当日强势 {data.review.strong_count} 只；5 日趋势对照 {data.review.trend_audit_count ?? "缺少完整窗口"}。</p>
          <p>未被研究观察：{data.review.missed.map(r => `${r.name} ${r.symbol}`).join("、") || "该对照口径下未发现"}</p>
          <p>观察后当日收跌：{data.review.cooled_symbols.join("、") || "该对照口径下未发现"}</p>
          <p>复盘将这些差异形成待核行动项；不自动修改选股权重。</p>
        </div>}
      </section>
      <p className="mt-2 text-zinc-600 dark:text-zinc-400">{data.outcome_basis}</p>
    </>}
    </div>
  </div>;
}
