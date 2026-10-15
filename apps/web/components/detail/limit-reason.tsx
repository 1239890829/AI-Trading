"use client";
import {useState} from "react";
import {useExplanationDetail} from "@/components/ui/explanation-map";
import {ReadingButton} from "@/components/ui/reading-row";
import {ReaderBrief, ResearchDisclosure} from "@/components/ui/reader-brief";
import {readerExcerpt, readerCautions} from "@/lib/reader-copy";
import {ModalShell} from "@/components/ui/modal-shell";
import {useExitPresence} from "@/hooks/use-exit-presence";
import {StockAttributionPanel} from "./attribution-evidence";
import styles from "./attribution-evidence.module.css";

/** The source reason is a clue, never a causal diagram inferred from '+' labels. */
export function LimitReason({reason, source, date, symbol, compact = false, full = false, observedAt, missingText = "数据源未提供涨停原因"}: {
  reason?: string | null; source?: string | null; date?: string | null; symbol?: string; compact?: boolean; full?: boolean; observedAt?: string; missingText?: string;
}) {
  const detail = useExplanationDetail();
  const text = reason?.trim() || null;
  const identity = `${symbol || ""}:${date || ""}:${source || ""}:${text || ""}`;
  const [opened, setOpened] = useState<string | null>(null);
  if (opened !== null && (opened !== identity || !text || detail || !compact)) setOpened(null);
  const presence = useExitPresence(!detail && compact && opened === identity ? identity : null);
  const label = source === "ths" ? "同花顺" : source === "eastmoney" ? "东方财富" : source || "来源未记录";
  const original = <section aria-label="数据源归因原文" className="space-y-2"><blockquote className={styles.sourceCopy}>{text}</blockquote><p>{label} · <time>{date || "交易日未记录"}</time></p>{observedAt && <p>此前观察 {observedAt}</p>}<p>数据源归因，非已核实的涨停因果。</p><p>交易日不代替新闻发布时间。</p></section>;
  const unknown = <div className={styles.reasonUnknown} role="note"><p>{missingText}<small>归因未知，不能用行业或概念标签补填。</small></p></div>;
  if ((detail || full) && text) return original;
  const body = <section aria-label="涨停原因说明" className="min-w-0">
    <ReaderBrief label="来源原因简述" headline={readerExcerpt(text)} facts={[{label:"消息来源",value:`${label} · ${date || "交易日未记录"}`}]} cautions={[...readerCautions([text || ""]), "来源解释，尚不能确认是涨停的真正原因"]}/>
    <ResearchDisclosure identity={identity} label="完整原因与来源记录">{original}</ResearchDisclosure>
  </section>;
  const modal = presence.value && <ModalShell open={presence.active && presence.value === identity} onClose={() => setOpened(null)} label="涨停原因原文" size="md" zIndex={70} header={<h2 className="text-sm font-medium">涨停原因与依据</h2>} bodyTabIndex={0} bodyClassName="data-scroll min-w-0 overflow-y-auto px-5 py-4 text-xs">{presence.active && presence.value === identity && <>{original}{symbol && <StockAttributionPanel symbol={symbol} title="相关消息" poolDate={date || undefined} defaultExpanded bounded={false}/>}</>}</ModalShell>;
  return compact ? <div className="min-w-0 text-[11px]" onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
    {text ? <ReadingButton className={`${styles.reasonEntry} py-1 text-zinc-700 dark:text-zinc-300`} onClick={() => setOpened(identity)}><span className="min-w-0"><span className="block line-clamp-2 [overflow-wrap:anywhere]">原因摘录：{readerExcerpt(text)}</span><span className="block text-[10px]">涨停原因 · 查看依据</span></span><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m9 6 6 6-6 6"/></svg></ReadingButton> : unknown}
    {modal}
  </div> : text ? body : unknown;
}
