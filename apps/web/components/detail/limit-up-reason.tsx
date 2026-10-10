"use client";

import {getLimitUpPoolSnapshot} from "@/lib/api";
import {bjDate} from "@/lib/format";
import {useResource} from "@/hooks/use-polling-fetch";
import type {Quote} from "@/types/market";
import {LimitReason} from "./limit-reason";
import {useState} from "react";
import {ModalShell} from "@/components/ui/modal-shell";
import {useExitPresence} from "@/hooks/use-exit-presence";

/** 工作台、独立个股页和各模块证券弹窗共用；不阻塞图表，不替换行情日期。 */
export function LimitUpReasonPanel({symbol, quote}: {symbol: string; quote: Quote | null | undefined}) {
  const quoteDate = quote?.symbol === symbol && quote.data_timestamp ? bjDate(quote.data_timestamp) : null;
  const resource = useResource(getLimitUpPoolSnapshot, {key: `${symbol}:${quoteDate ?? "unknown"}`, enabled: !!quoteDate, intervalMs: 60_000});
  const row = resource.data?.pool.find(item => item.symbol === symbol);
  const identity = `${symbol}:${quoteDate ?? "unknown"}`;
  const [openedFor, setOpenedFor] = useState<string | null>(null);
  const presence = useExitPresence(openedFor === identity ? identity : null);
  if (openedFor !== null && openedFor !== identity) setOpenedFor(null);
  const current = !!row && !!row.reason?.trim() && resource.data?.trade_date === quoteDate && !resource.error;
  const error = resource.error instanceof Error ? resource.error.message : "数据源读取失败";
  let content;
  if (!quoteDate) content = <p>行情源日期尚未确认，暂不把其他日期的涨停原因用于当前行情。</p>;
  else if (resource.error) content = <p className="text-amber-800 dark:text-amber-300">涨停归因读取失败：{error} <button type="button" className="quiet-action" onClick={resource.refresh}>重试读取</button></p>;
  else if (!resource.data) content = <p role="status">正在核对涨停归因…</p>;
  else if (resource.data.trade_date !== quoteDate) content = <p>最新涨停池为 {resource.data.trade_date}，行情源日期为 {quoteDate}；日期不同，暂不展示为本次行情原因。</p>;
  else if (!row) content = <p>{quoteDate} 涨停池未收录该股；不能据此认定没有上涨催化。</p>;
  else content = <LimitReason reason={row.reason} source={row.source} date={row.trade_date} />;
  const source = row?.source === "ths" ? "同花顺" : row?.source === "eastmoney" ? "东方财富" : row?.source || "来源未记录";
  return <div className="limit-up-reason-panel min-w-0 shrink-0 py-1 text-xs">
    <div className="flex min-h-8 items-center justify-between gap-3"><div className="min-w-0 flex-1"><span className="block font-medium">涨停原因</span>{current && <p className="line-clamp-2 [overflow-wrap:anywhere]">{row?.reason}</p>}<span className="block text-[10px] text-zinc-600 dark:text-zinc-400">{current ? `${row!.trade_date} · ${source} · 数据源归因，非已核实的涨停因果` : !quoteDate ? "行情源日期待确认" : resource.error ? "归因源读取失败" : !resource.data ? "正在核对归因" : resource.data.trade_date !== quoteDate ? "归因与行情日期不同" : !row ? "所查池未收录该股" : "数据源未提供涨停原因"}</span></div><button type="button" data-action="secondary" className="shrink-0 whitespace-nowrap" onClick={() => setOpenedFor(identity)}>{current ? "查看全文" : "展开核对"}</button></div>
    {presence.value === identity && <ModalShell open={presence.active} onClose={() => setOpenedFor(null)} label={`涨停原因 ${symbol}`} size="md" zIndex={60} header={<h2 className="text-sm font-medium">涨停原因 · {quote?.name || symbol}</h2>} bodyTabIndex={0} bodyClassName="data-scroll min-w-0 overflow-y-auto px-5 py-4 text-xs [overflow-wrap:anywhere]">{presence.active && content}</ModalShell>}
  </div>;
}
