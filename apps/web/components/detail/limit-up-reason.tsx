"use client";

import {getLimitUpPoolSnapshot, type LimitPoolSnapshot} from "@/lib/api";
import {bjDate, timeTextBJ, sourceLabel, dateTimeTextBJ} from "@/lib/format";
import {useResource} from "@/hooks/use-polling-fetch";
import type {Quote, LimitUpRecord} from "@/types/market";
import type {Resource} from "@/hooks/use-resource";
import {limitQualification} from "@/lib/limit-qualification";
import {readerExcerpt} from "@/lib/reader-copy";
import {limitReasonView} from "@/lib/limit-reason-view";
import {LimitReason} from "./limit-reason";
import {useState} from "react";
import {ModalShell} from "@/components/ui/modal-shell";
import {useExitPresence} from "@/hooks/use-exit-presence";
import {StockAttributionPanel} from "./attribution-evidence";

/** 工作台、独立个股页和各模块证券弹窗共用；不阻塞图表，不替换行情日期。 */
export function LimitUpReasonPanel({symbol, quote, active = true, standalone = false, sharedResource}: {symbol: string; quote: Quote | null | undefined; active?: boolean; standalone?: boolean; sharedResource?: Resource<LimitPoolSnapshot<LimitUpRecord>>}) {
  const quoteDate = quote?.symbol === symbol && quote.data_timestamp ? bjDate(quote.data_timestamp) : null;
  const localResource = useResource(getLimitUpPoolSnapshot, {key: `${symbol}:${quoteDate ?? "unknown"}`, enabled: !sharedResource && active && !!quoteDate, intervalMs: 60_000});
  const resource = sharedResource || localResource;
  const qualification = limitQualification(resource.data, symbol, quoteDate);
  const row = qualification.row;
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
  if (standalone) {
    const savedReason = row?.reason?.trim() || qualification.previous?.reason || null;
    const sourceView = savedReason ? limitReasonView(savedReason) : null;
    const failed = !!resource.error || !!resource.data?.coverage && Object.values(resource.data.coverage).some(side => side.state !== "success");
    if (!active) return null;
    return <section className="limit-up-reason-panel min-w-0 space-y-3 py-1 text-xs" aria-label="涨停原因与依据">
      {failed && <div className="flex flex-wrap items-center gap-2"><p role="alert" className="text-amber-800 dark:text-amber-300">原因资料读取不完整{qualification.eligible ? " · 保留同日记录，当前状态待核" : "，资格尚待确认"}</p><button type="button" data-action="secondary" disabled={resource.pending} onClick={resource.refresh}>重新读取涨停原因</button></div>}
      {!qualification.eligible ? <p className="text-zinc-600 dark:text-zinc-400">{resource.pending ? "正在核对该交易日的涨停与炸板记录…" : "该交易日尚无可确认的涨停或炸板记录。"}</p> : <>
        <p className="text-zinc-600 dark:text-zinc-400">{sourceLabel(row?.source || qualification.previous?.source)} · {quoteDate}{!qualification.row ? " · 此前触板" : qualification.kind === "limit_break" ? " · 炸板记录" : ""}</p>
        {sourceView && <section className="space-y-2" aria-label="核心原因"><h3 className="font-medium">来源提到的原因</h3><p className="leading-relaxed [overflow-wrap:anywhere]">{readerExcerpt(sourceView.core)}</p></section>}
        <StockAttributionPanel key={`${symbol}:${quoteDate}`} symbol={symbol} poolDate={quoteDate || undefined} responseAsOf={firstSeal} active={active} title="涨停归因 · 已保存解读" inlinePrimary sourceReason={savedReason} sourceCore={sourceView?.core} sourceAudit={savedReason && <>
          <p className="text-zinc-600 dark:text-zinc-400">{qualification.kind === "limit_break" ? "炸板池记录" : qualification.row ? "涨停池记录" : "此前触板记录"} · 不证明当前仍封板。记录接收 {dateTimeTextBJ(row?.received_at || qualification.previous?.received_at, {seconds: true})}（非新闻发布时点）</p>
          <LimitReason symbol={symbol} reason={savedReason} source={row?.source || qualification.previous?.source} date={quoteDate} observedAt={!row ? qualification.previous?.observed_at : undefined} full/>
        </>}/>
        {!savedReason && <p className="text-zinc-600 dark:text-zinc-400">池内未提供原因原句；已有解读也不等于已核实因果。</p>}
      </>}
    </section>;
  }
  return <>{(eligible && row || prior) && <div className="limit-up-reason-panel min-w-0 shrink-0 py-1 text-xs">
    <div className="flex min-h-8 items-center justify-between gap-3"><div className="min-w-0 flex-1">{!standalone && <span className="block font-medium">涨停原因</span>}<p title={original || undefined} className="truncate">{original || "数据源未提供涨停原因"}</p>{prior && <p role="note" title={resource.error ? "当前池读取失败，保留此前来源原句。" : "当前来源未提供原因，以下为此前来源原句。"}>{standalone ? "此前来源 · 待核" : resource.error ? "当前池读取失败，保留此前来源原句。" : "当前来源未提供原因，以下为此前来源原句。"}</p>}<span title={`${quoteDate} · ${source} · ${prior ? `此前来源观察 ${timeTextBJ(previous.observed_at)}，不证明当前在板` : "数据源归因，非已核实的涨停因果"}`} className="block text-[10px] text-zinc-600 dark:text-zinc-400">{standalone ? `${source} · ${prior ? "此前观察" : "来源归因，待核"}` : <>{quoteDate} · {source} · {prior ? `此前来源观察 ${timeTextBJ(previous.observed_at)}，不证明当前在板` : "数据源归因，非已核实的涨停因果"}</>}</span></div>{original && <button type="button" data-action="secondary" className="shrink-0 whitespace-nowrap" onClick={() => setOpenedFor({identity, symbol, name:quote?.name || symbol})}>查看全文</button>}</div>
  </div>}
    {presence.value && <ModalShell presentation="drawer" open={presence.active} onClose={() => setOpenedFor(null)} label={`涨停原因 ${presence.value.symbol}`} size="md" zIndex={60} header={<h2 className="text-sm font-medium">涨停原因 · {presence.value.name}</h2>} bodyTabIndex={0} bodyClassName="data-scroll min-w-0 overflow-y-auto px-5 py-4 text-xs [overflow-wrap:anywhere]">{presence.active && <><h3 className="font-medium">数据源归因标签</h3><LimitReason symbol={presence.value.symbol} reason={original} source={reasonSource} date={quoteDate} observedAt={prior ? previous?.observed_at : undefined} full/><StockAttributionPanel symbol={presence.value.symbol} title="涨停归因 · 证据核对" poolDate={quoteDate || undefined} responseAsOf={firstSeal} defaultExpanded defaultSourceExpanded bounded={false} /></>}</ModalShell>}
  </>;
}
