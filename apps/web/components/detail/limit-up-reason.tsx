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
  // 池内身份只证明所查日曾进入涨停池；不据 high 推断炸板，也不把未收录当作未触板。
  const eligible = !!quoteDate && resource.data?.trade_date === quoteDate && row?.trade_date === quoteDate && !resource.error;
  const original = typeof row?.reason === "string" && row.reason.trim() ? row.reason : null;
  const identity = `${symbol}:${quoteDate ?? "unknown"}`;
  const [openedFor, setOpenedFor] = useState<string | null>(null);
  const readable = eligible && !!original;
  const presence = useExitPresence(readable && openedFor === identity ? identity : null);
  if (openedFor !== null && (!readable || openedFor !== identity)) setOpenedFor(null);
  if (!eligible || !row) return null;
  const source = row.source === "ths" ? "同花顺" : row.source === "eastmoney" ? "东方财富" : row.source || "来源未记录";
  return <div className="limit-up-reason-panel min-w-0 shrink-0 py-1 text-xs">
    <div className="flex min-h-8 items-center justify-between gap-3"><div className="min-w-0 flex-1"><span className="block font-medium">涨停原因</span><p className="line-clamp-2 [overflow-wrap:anywhere]">{original || "数据源未提供涨停原因"}</p><span className="block text-[10px] text-zinc-600 dark:text-zinc-400">{row.trade_date} · {source} · 数据源归因，非已核实的涨停因果</span></div>{original && <button type="button" data-action="secondary" className="shrink-0 whitespace-nowrap" onClick={() => setOpenedFor(identity)}>查看全文</button>}</div>
    {presence.value === identity && <ModalShell open={presence.active} onClose={() => setOpenedFor(null)} label={`涨停原因 ${symbol}`} size="md" zIndex={60} header={<h2 className="text-sm font-medium">涨停原因 · {quote?.name || symbol}</h2>} bodyTabIndex={0} bodyClassName="data-scroll min-w-0 overflow-y-auto px-5 py-4 text-xs [overflow-wrap:anywhere]">{presence.active && <LimitReason reason={original} source={row.source} date={row.trade_date} />}</ModalShell>}
  </div>;
}
