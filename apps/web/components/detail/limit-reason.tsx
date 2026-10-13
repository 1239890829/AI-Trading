"use client";
import {NarrativeText} from "@/components/ui/explanation-map";
import {ReadingButton} from "@/components/ui/reading-row";
import {useState} from "react";
import {ModalShell} from "@/components/ui/modal-shell";
import {useExitPresence} from "@/hooks/use-exit-presence";
import {StockAttributionPanel} from "./attribution-evidence";

/** 原文、来源与交易日一起展示；概念成分不能填补缺失归因。 */
export function LimitReason({reason, source, date, symbol, compact = false, missingText = "数据源未提供涨停原因"}: {
  reason?: string | null; source?: string | null; date?: string | null; symbol?: string; compact?: boolean; missingText?: string;
}) {
  const text = typeof reason === "string" && reason.trim() ? reason : null;
  const identity = text ? `${symbol || ""}:${date || ""}:${source || ""}:${text}` : null;
  const [openedReason, setOpenedReason] = useState<{identity: string; symbol?: string} | null>(null);
  const presence = useExitPresence(compact && openedReason?.identity === identity ? openedReason : null);
  if (openedReason !== null && (!compact || openedReason.identity !== identity)) setOpenedReason(null);
  const label = source === "ths" ? "同花顺" : source === "eastmoney" ? "东方财富" : source || "来源未记录";
  const body = text ? <div className="min-w-0 space-y-1 whitespace-normal py-1">
    <NarrativeText text={text} label="数据源归因原文" identity={identity ?? undefined}/>
    <p className="text-[10px] text-zinc-600 dark:text-zinc-400">{date || "交易日未记录"} · {label} · 数据源归因，非已核实的涨停因果</p>
  </div> : <span className="whitespace-normal text-zinc-600 dark:text-zinc-400">{missingText}</span>;
  // Invalidated data retires the active view, while the captured inert shell exits.
  const modal = presence.value && <ModalShell open={presence.active} onClose={() => setOpenedReason(null)} label="涨停原因原文" size="md" zIndex={70} header={<h2 className="text-sm font-medium">涨停原因原文</h2>} bodyTabIndex={0} bodyClassName="data-scroll min-w-0 overflow-y-auto px-5 py-4 text-xs">{presence.active && <>{body}{presence.value.symbol && <StockAttributionPanel symbol={presence.value.symbol} title="涨停归因 · 证据核对" defaultExpanded defaultSourceExpanded bounded={false} />}</>}</ModalShell>;
  return compact ? <div className="min-w-0 text-[11px]" onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
    {text && identity ? <ReadingButton className="block w-full space-y-1 whitespace-normal py-1 text-left text-zinc-700 dark:text-zinc-300" onClick={() => setOpenedReason({identity, symbol})}><span className="line-clamp-2 [overflow-wrap:anywhere]">原因摘要：{text}</span><span className="block text-[10px]">涨停原因 · 查看原文</span></ReadingButton> : body}
    {modal}
  </div> : <>{body}{modal}</>;
}
