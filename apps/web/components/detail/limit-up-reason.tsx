"use client";

import {getLimitUpPoolSnapshot} from "@/lib/api";
import {bjDate, timeTextBJ} from "@/lib/format";
import {useResource} from "@/hooks/use-polling-fetch";
import type {Quote} from "@/types/market";
import {LimitReason} from "./limit-reason";
import {useState} from "react";
import {ModalShell} from "@/components/ui/modal-shell";
import {useExitPresence} from "@/hooks/use-exit-presence";
import {StockAttributionPanel} from "./attribution-evidence";

/** 工作台、独立个股页和各模块证券弹窗共用；不阻塞图表，不替换行情日期。 */
export function LimitUpReasonPanel({symbol, quote, active = true}: {symbol: string; quote: Quote | null | undefined; active?: boolean}) {
  const quoteDate = quote?.symbol === symbol && quote.data_timestamp ? bjDate(quote.data_timestamp) : null;
  const resource = useResource(getLimitUpPoolSnapshot, {key: `${symbol}:${quoteDate ?? "unknown"}`, enabled: active && !!quoteDate, intervalMs: 60_000});
  const row = resource.data?.pool.find(item => item.symbol === symbol);
  // 池内身份只证明所查日曾进入涨停池；不据 high 推断炸板，也不把未收录当作未触板。
  const eligible = !!quoteDate && resource.data?.trade_date === quoteDate && row?.trade_date === quoteDate && !resource.error;
  const currentReason = eligible && typeof row?.reason === "string" && row.reason.trim() ? row.reason : null;
  const observations = resource.data?.trade_date === quoteDate ? resource.data?.reason_observations?.[symbol] || [] : [];
  const previous = observations.filter(item => item.symbol === symbol && item.trade_date === quoteDate && item.source && item.reason?.trim() && Number.isFinite(Date.parse(item.observed_at)))
    .sort((a,b) => Date.parse(b.observed_at) - Date.parse(a.observed_at))[0];
  const prior = !currentReason && !!previous;
  const original = currentReason || previous?.reason || null;
  const reasonSource = prior ? previous?.source : row?.source;
  const readable = !!quoteDate && !!original;
  const firstSeal = eligible && /^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$/.test(row?.first_seal_time || "") ? `${quoteDate}T${row!.first_seal_time}+08:00` : undefined;
  const identity = `${symbol}:${quoteDate ?? "unknown"}:${original ?? "missing"}`;
  const [openedFor, setOpenedFor] = useState<{identity: string; symbol: string; name: string} | null>(null);
  const presence = useExitPresence(active && readable && openedFor?.identity === identity ? openedFor : null);
  if (openedFor !== null && (!active || !readable || openedFor.identity !== identity)) setOpenedFor(null);
  const source = reasonSource === "ths" ? "同花顺" : reasonSource === "eastmoney" ? "东方财富" : reasonSource || "来源未记录";
  return <>{(eligible && row || prior) && <div className="limit-up-reason-panel min-w-0 shrink-0 py-1 text-xs">
    <div className="flex min-h-8 items-center justify-between gap-3"><div className="min-w-0 flex-1"><span className="block font-medium">涨停原因</span><p className="line-clamp-2 [overflow-wrap:anywhere]">{original || "数据源未提供涨停原因"}</p>{prior && <p role="note">{resource.error ? "当前池读取失败，保留此前来源原句。" : "当前来源未提供原因，以下为此前来源原句。"}</p>}<span className="block text-[10px] text-zinc-600 dark:text-zinc-400">{quoteDate} · {source} · {prior ? `此前来源观察 ${timeTextBJ(previous.observed_at)}，不证明当前在板` : "数据源归因，非已核实的涨停因果"}</span></div>{original && <button type="button" data-action="secondary" className="shrink-0 whitespace-nowrap" onClick={() => setOpenedFor({identity, symbol, name:quote?.name || symbol})}>查看全文</button>}</div>
  </div>}
    {presence.value && <ModalShell open={presence.active} onClose={() => setOpenedFor(null)} label={`涨停原因 ${presence.value.symbol}`} size="md" zIndex={60} header={<h2 className="text-sm font-medium">涨停原因 · {presence.value.name}</h2>} bodyTabIndex={0} bodyClassName="data-scroll min-w-0 overflow-y-auto px-5 py-4 text-xs [overflow-wrap:anywhere]">{presence.active && <><h3 className="font-medium">数据源归因标签</h3><LimitReason symbol={presence.value.symbol} reason={original} source={reasonSource} date={quoteDate} observedAt={prior ? previous?.observed_at : undefined} /><StockAttributionPanel symbol={presence.value.symbol} title="涨停归因 · 证据核对" poolDate={quoteDate || undefined} responseAsOf={firstSeal} defaultExpanded defaultSourceExpanded bounded={false} /></>}</ModalShell>}
  </>;
}
