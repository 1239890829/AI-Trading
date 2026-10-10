"use client";
import {useState} from "react";
import {ModalShell} from "@/components/ui/modal-shell";
import {useExitPresence} from "@/hooks/use-exit-presence";

/** 原文、来源与交易日一起展示；概念成分不能填补缺失归因。 */
export function LimitReason({reason, source, date, compact = false, missingText = "数据源未提供涨停原因"}: {
  reason?: string | null; source?: string | null; date?: string | null; compact?: boolean; missingText?: string;
}) {
  const text = typeof reason === "string" && reason.trim() ? reason : null;
  const [openedReason, setOpenedReason] = useState<string | null>(null);
  const presence = useExitPresence(openedReason === text ? text : null);
  if (openedReason !== null && openedReason !== text) setOpenedReason(null);
  if (!text) return <span className="whitespace-normal text-zinc-600 dark:text-zinc-400">{missingText}</span>;
  const label = source === "ths" ? "同花顺" : source === "eastmoney" ? "东方财富" : source || "来源未记录";
  const body = <div className="min-w-0 space-y-1 whitespace-normal py-1">
    <p className="whitespace-pre-line [overflow-wrap:anywhere]">{text}</p>
    <p className="text-[10px] text-zinc-600 dark:text-zinc-400">{date || "交易日未记录"} · {label} · 数据源归因，非已核实的涨停因果</p>
  </div>;
  return compact ? <div className="min-w-0 text-[11px]" onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
    <button type="button" className="block w-full space-y-1 whitespace-normal py-1 text-left text-zinc-700 dark:text-zinc-300" onClick={() => setOpenedReason(text)}><span className="line-clamp-2 [overflow-wrap:anywhere]">原因摘要：{text}</span><span className="block text-[10px]">涨停原因 · 查看原文</span></button>
    {presence.value && <ModalShell open={presence.active} onClose={() => setOpenedReason(null)} label="涨停原因原文" size="md" zIndex={70} header={<h2 className="text-sm font-medium">涨停原因原文</h2>} bodyTabIndex={0} bodyClassName="data-scroll min-w-0 overflow-y-auto px-5 py-4 text-xs">{presence.active && body}</ModalShell>}
  </div> : body;
}
