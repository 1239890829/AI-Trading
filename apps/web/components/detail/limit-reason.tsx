"use client";
import {ConditionList, ExplanationMap, FactTimeline, NarrativeText, useExplanationDetail} from "@/components/ui/explanation-map";
import {ReadingButton} from "@/components/ui/reading-row";
import {useState} from "react";
import {ModalShell} from "@/components/ui/modal-shell";
import {useExitPresence} from "@/hooks/use-exit-presence";
import {StockAttributionPanel} from "./attribution-evidence";
import styles from "./attribution-evidence.module.css";

/** Only an explicit short parallel source format supplies branches; no causal chain is inferred. */
function parallelSourceLabels(text: string): string[] {
  if (/[\r\n。！？!?]/.test(text)) return [];
  const labels = text.split(/[+＋]/).map(value => value.trim());
  if (labels.length < 2 || labels.length > 6 || labels.some(value => !value || value.length > 40 || /^[\d\s.,%％=<>≤≥]+$/.test(value))) return [];
  return labels;
}

/** 原文、来源与交易日一起展示；概念成分不能填补缺失归因。 */
export function LimitReason({reason, source, date, symbol, compact = false, observedAt, missingText = "数据源未提供涨停原因"}: {
  reason?: string | null; source?: string | null; date?: string | null; symbol?: string; compact?: boolean; observedAt?: string; missingText?: string;
}) {
  const detail = useExplanationDetail();
  const text = typeof reason === "string" && reason.trim() ? reason : null;
  const identity = text ? `${symbol || ""}:${date || ""}:${source || ""}:${text}` : null;
  const [openedReason, setOpenedReason] = useState<{identity: string; symbol?: string} | null>(null);
  const presence = useExitPresence(!detail && compact && openedReason?.identity === identity ? openedReason : null);
  if (openedReason !== null && (detail || !compact || openedReason.identity !== identity)) setOpenedReason(null);
  const label = source === "ths" ? "同花顺" : source === "eastmoney" ? "东方财富" : source || "来源未记录";
  const unknown = <div className={styles.reasonUnknown} role="note"><svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="12" cy="12" r="9"/><path d="M9.5 8a2.5 2.5 0 1 1 3.3 3.5c-.8.3-.8 1-.8 1.5M12 16h.01"/></svg><p>{missingText}<small>归因未知，不能用行业或概念标签补填。</small></p></div>;
  // The surrounding node already supplies the overview and reading entry.
  if (detail) return <section className={styles.reasonOverview} aria-label="数据源归因原文">
    {text ? <blockquote className={styles.sourceCopy}>{text}</blockquote> : unknown}
    <FactTimeline label="归因来源与交易日" items={[{label: "所属交易日", value: date || "未记录", kind: "time"}, {label: "归因来源", value: label, kind: "fact"}]}/>
    <ConditionList label="归因边界" tone="warning" items={["数据源归因，非已核实的涨停因果。", "交易日标签不代替新闻发布时点。", ...(text ? ["完整保留来源原句；“+”等分隔不代表已核实的产业传导或涨停因果。"] : [])]}/>
  </section>;
  const parallelLabels = text ? parallelSourceLabels(text) : [];
  const body = text ? <section className={styles.reasonOverview} aria-label="涨停原因资料图">
    <p className={styles.reasonMeta}>{label} · 数据源归因 · 因果待核</p>
    {parallelLabels.length > 0 && <ExplanationMap label="涨停原因资料关系" identity={identity ?? undefined}
      center={{id:"attribution",label:symbol ? `${symbol} · 涨停原因` : "涨停原因",icon:"relation",value:"来源并列标签，未核实实际受益"}}
      links={parallelLabels.map((_,index)=>({from:"attribution",to:`label-${index}`,label:"原文并列标签 · 待核",kind:"unverified"}))}
      steps={parallelLabels.map((value,index)=>({id:`label-${index}`,label:value,icon:"relation",content:<section aria-label={`原文并列项 ${index+1}`}><blockquote className={styles.sourceCopy}>{value}</blockquote><p className={styles.boundary}>没有建立标签之间的产业传导或涨停因果。</p></section>}))}/>}
    {text.length <= 180 ? <section aria-label="数据源归因原文"><h4 className="text-xs font-medium">来源原句</h4><blockquote className={styles.sourceCopy}>{text}</blockquote></section> : <NarrativeText text={text} label="数据源归因原文" identity={identity ?? undefined}/>}
    <FactTimeline label="交易日记录" items={[{label:"所属交易日",value:date || "未记录",kind:"time"}, ...(observedAt ? [{label:"此前来源观察时点",value:observedAt,kind:"time" as const}] : [])]}/>
    <p className={styles.boundary}>仅按数据源原文呈现。缺少对应消息时，不补推当日催化；交易日不代替新闻发布时间。</p>
  </section> : unknown;
  // Invalidated data retires the active view, while the captured inert shell exits.
  const modal = presence.value && <ModalShell open={presence.active} onClose={() => setOpenedReason(null)} label="涨停原因原文" size="md" zIndex={70} header={<h2 className="text-sm font-medium">涨停原因原文</h2>} bodyTabIndex={0} bodyClassName="data-scroll min-w-0 overflow-y-auto px-5 py-4 text-xs">{presence.active && <>{body}{presence.value.symbol && <StockAttributionPanel symbol={presence.value.symbol} title="涨停归因 · 证据核对" poolDate={date || undefined} defaultExpanded defaultSourceExpanded bounded={false} />}</>}</ModalShell>;
  return compact ? <div className="min-w-0 text-[11px]" onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
    {text && identity ? <ReadingButton className={`${styles.reasonEntry} whitespace-normal py-1 text-zinc-700 dark:text-zinc-300`} onClick={() => setOpenedReason({identity, symbol})}><span><span className="block line-clamp-2 [overflow-wrap:anywhere]">原因摘要：{text}</span><span className="mt-1 block text-[10px]">涨停原因 · 查看原文</span></span><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m9 6 6 6-6 6"/></svg></ReadingButton> : body}
    {modal}
  </div> : <>{body}{modal}</>;
}
