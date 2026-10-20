"use client";

import {memo, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode} from "react";
import {useResource} from "@/hooks/use-resource";
import {useExitPresence} from "@/hooks/use-exit-presence";
import {getEventAttribution, getStockAttribution, type AttributionEvidenceItem, type AttributionSourceContext, type CompanyImpact, type CompanyStatus, type EventInterpretationRef, type StockAttributionPayload} from "@/lib/api";
import {bjDate, eventTimeText, sourceLabel, timeTextBJ} from "@/lib/format";
import {Skeleton} from "@/components/ui/loading";
import styles from "./attribution-evidence.module.css";
import {ConditionList, FactTimeline} from "@/components/ui/explanation-map";
import {limitReasonView, matchSourceReason} from "@/lib/limit-reason-view";
import {ReaderBrief, ResearchDisclosure} from "@/components/ui/reader-brief";
import {readerExcerpt, readerCautions} from "@/lib/reader-copy";
import {ModalShell} from "@/components/ui/modal-shell";

/** A primary clue must bind a saved source and its exact interpretation. */
function boundPrimaryItem(data: StockAttributionPayload): AttributionEvidenceItem | null {
  const primary = data.primary_reason;
  if (!primary?.event_id || !primary.version_id || !primary.observation_id || !primary.excerpt?.quote) return null;
  return [...data.items, ...(data.later_items || [])].find(item => item.event_id === primary.event_id && item.interpretation_ref.event_id === primary.event_id
    && item.interpretation_ref.version_id === primary.version_id && item.interpretation_ref.observation_id === primary.observation_id
    && !!item.source_context && item.source_context.observation_id === primary.observation_id
    && typeof item.source_context[primary.excerpt!.field] === "string"
    && item.source_context[primary.excerpt!.field]!.includes(primary.excerpt!.quote)) ?? null;
}

/** Saved auxiliary prose needs the same exact source and earlier input journal as the primary view. */
function boundAuxiliary(item: AttributionEvidenceItem) {
  const aux = item.llm_aux;
  const source = aux && item.source_context?.[aux.evidence.field];
  return aux && aux.event_id === item.event_id && Number.isInteger(aux.input_version_id) && aux.input_version_id! > 0
    && Number.isInteger(item.interpretation_ref.version_id) && aux.input_version_id! < item.interpretation_ref.version_id!
    && aux.observation_id === item.interpretation_ref.observation_id && aux.observation_id === item.source_context?.observation_id
    && !!aux.evidence.quote && typeof source === "string" && source.includes(aux.evidence.quote) ? aux : null;
}

/** Cross-stock or stale-version explanations cannot travel with a response. */
function boundStockReasoning(item: AttributionEvidenceItem, symbol?: string | null) {
  const value = item.stock_reasoning;
  if (!value || !symbol || value.symbol !== symbol || value.event_id !== item.event_id
    || !Number.isInteger(value.version_id) || !value.version_id || value.version_id !== item.interpretation_ref.version_id
    || !Number.isInteger(value.observation_id) || !value.observation_id || value.observation_id !== item.interpretation_ref.observation_id
    || value.observation_id !== item.source_context?.observation_id
    || value.method !== "saved_evidence_projection" || value.eligibility_changed !== false
    || value.business_effect_verified !== false) return null;
  if (!["source_related", "theme_only", "insufficient", "inactive"].includes(value.status)
    || !["company_context", "not_applicable"].includes(value.ai_scope)
    || !Array.isArray(value.basis) || !value.basis.every(text => typeof text === "string")
    || [value.explanation, value.ai_scope_reason, value.missing].some(text => typeof text !== "string")
    || (value.transmission !== null && typeof value.transmission !== "string")) return null;
  if (value.ai_scope === "company_context" && (value.status !== "source_related"
    || item.state !== "active" || !item.is_active || item.interpretation_ref.state !== "active")) return null;
  const excerpt = value.source_excerpt;
  if (excerpt && (!["title", "summary"].includes(excerpt.field) || typeof excerpt.quote !== "string"
    || !excerpt.quote || typeof item.source_context?.[excerpt.field] !== "string"
    || !item.source_context[excerpt.field]!.includes(excerpt.quote))) return null;
  return value;
}

function StockReasoningEvidence({item, symbol, now}: {item: AttributionEvidenceItem; symbol?: string | null; now: number}) {
  const value = boundStockReasoning(item, symbol);
  const hasCompanyImpact = boundCompanyImpact(item, symbol, now) !== null;
  if (!symbol) return null;
  if (!value) return <p className="py-1 text-zinc-600 dark:text-zinc-400">个股关系证据尚未对应完整，不将消息AI观点当成本股判断。</p>;
  return <section aria-label="本股关联与判读依据" className="space-y-2 py-2">
    <ReaderBrief label={hasCompanyImpact ? "原消息关系核对" : "本股关系核对"} facts={[
      {label: "为什么相关", value: hasCompanyImpact ? value.status === "theme_only" ? "原消息按题材成员关联；补充公司原文依据见下文。" : value.status === "source_related" ? "原消息记录了来源与本股的关联；补充经营解释见下文。" : "原消息的逐股关联依据不完整；补充公司经营证据另列如下。" : value.explanation},
      {label: "关联依据", value: value.basis.join("；") || "未保存可核对的逐股关联依据。"},
      {label: hasCompanyImpact ? "原消息传导线索" : "传导线索", value: value.transmission || (hasCompanyImpact ? "原消息记录未单独保存逐股传导；新增经营解释见下文。" : "尚不能从现有材料建立公司经营传导链。")},
      {label: hasCompanyImpact ? "原消息AI适用范围" : "AI适用范围", value: value.ai_scope_reason},
      {label: hasCompanyImpact ? "原消息证据范围" : "还缺什么", value: hasCompanyImpact ? "本段只核对原消息的关联与AI适用范围；补充公司经营资料、传导假设及缺口见下文。" : value.missing},
    ]}/>
    {value.source_excerpt && <blockquote aria-label="关联核对原句" className={styles.sourceCopy}>{value.source_excerpt.quote}</blockquote>}
    <p className="text-zinc-600 dark:text-zinc-400">关系核对来自已保存证据，不是新生成的AI结论；不改变入选、评分或通知资格。</p>
  </section>;
}

function companySourceUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "static.cninfo.com.cn"
      && !url.username && !url.password && !url.port ? url.href : null;
  } catch {return null;}
}

function companyClock(value: string | null | undefined): number | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const result = clock(value);
  if (result === null) return null;
  // Date.parse normalizes impossible dates such as February 30; do not accept that repair.
  const local = new Date(`${value.slice(0, 19)}Z`);
  return Number.isFinite(local.getTime()) && local.toISOString().slice(0, 19) === value.slice(0, 19) ? result : null;
}

/** Observe the real clock outside rendering; wake only at relevant source/expiry boundaries. */
function useCompanyEvidenceClock(boundaries: string): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const times = boundaries.split("|").map(companyClock).filter((value): value is number => value !== null);
    function update() {
      const current = Date.now();
      setNow(current);
      const next = times.filter(value => value > current).sort((a, b) => a - b)[0];
      if (next !== undefined) timer = setTimeout(update, Math.min(next - current, 2147483647));
    }
    update();
    return () => clearTimeout(timer);
  }, [boundaries]);
  return now;
}

function checkedCompanyFacts(value: CompanyImpact["facts"] | undefined, knownAt: number): CompanyImpact["facts"] | null {
  if (!Array.isArray(value) || value.length > 12) return null;
  const text = (input: unknown, max: number) => typeof input === "string" && !!input.trim() && input.length <= max;
  const ids = new Set<string>();
  for (const fact of value) {
    if (!fact || !text(fact.id, 128) || fact.id.startsWith("event:") || ids.has(fact.id)
      || !text(fact.kind, 80) || !text(fact.quote, 4000) || !text(fact.document_title, 512) || !text(fact.period, 120)
      || !text(fact.url, 2048) || !companySourceUrl(fact.url) || typeof fact.sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(fact.sha256)
      || !Number.isInteger(fact.page) || fact.page <= 0 || fact.page > 10000) return null;
    const published = companyClock(fact.published_at);
    if (published === null || published > knownAt) return null;
    ids.add(fact.id);
  }
  return value;
}

function CompanySourceFacts({facts}: {facts: CompanyImpact["facts"]}) {
  const labels: Record<string, string> = {business: "主营业务", main_business: "主营业务", product: "产品", products: "产品", customer: "客户", customers: "客户", order: "订单", orders: "订单", risk: "限制与反证", limitation: "限制与反证"};
  return <section aria-label="公司原文事实"><h5 className="font-medium">公司原文事实</h5><ol className="space-y-3">{facts.map(fact => <li key={fact.id} className="min-w-0 space-y-1">
    <p>{labels[fact.kind] || "公司原文"} · 依据 {fact.id}</p>
    <blockquote className={styles.sourceCopy}>{fact.quote}</blockquote>
    <p className="text-zinc-600 dark:text-zinc-400">{fact.document_title} · 第 {fact.page} 页 · 报告期：{fact.period} · 披露日期：{bjDate(fact.published_at)}</p>
    <a href={companySourceUrl(fact.url)!} target="_blank" rel="noopener noreferrer" data-action="quiet" className={`quiet-action ${styles.action}`} aria-label={`公司依据 ${fact.id} 原文`}>查看披露原文</a>
  </li>)}</ol></section>;
}

/** Reject stale or foreign receipts before any model prose or source link is displayed. */
function boundCompanyImpact(item: AttributionEvidenceItem, symbol: string | null | undefined, now: number) {
  const value = item.company_impact;
  const text = (input: unknown, max = 4000): input is string => typeof input === "string" && !!input.trim() && input.length <= max;
  const hash = (input: unknown) => typeof input === "string" && /^[a-f0-9]{64}$/i.test(input);
  if (!value || !symbol || value.symbol !== symbol || value.event_id !== item.event_id
    || item.interpretation_ref.event_id !== item.event_id || item.state !== "active" || !item.is_active
    || item.interpretation_ref.state !== "active" || !Number.isInteger(value.version_id) || value.version_id <= 0
    || value.version_id !== item.interpretation_ref.version_id || !Number.isInteger(value.observation_id) || value.observation_id <= 0
    || value.observation_id !== item.interpretation_ref.observation_id || value.observation_id !== item.source_context?.observation_id
    || !["author_source_reviewed_not_gold", "automated_evidence_bound_not_gold"].includes(value.verification)
    || (value.verification === "automated_evidence_bound_not_gold" && value.generation_method !== "model_selection_and_deterministic_compilation")
    || value.eligibility_changed !== false
    || value.current_context_only !== true || value.business_effect_verified !== false
    || !hash(value.input_sha256) || !text(value.company_name, 120)
    || !value.model || !text(value.model.provider, 120) || !text(value.model.model, 160)) return null;
  const generated = companyClock(value.generated_at), validUntil = companyClock(value.valid_until), captured = companyClock(value.facts_captured_at);
  if (generated === null || validUntil === null || captured === null || captured > generated
    || generated > now || validUntil <= now || validUntil <= generated) return null;
  const sourceClocks = [item.timing.received_at, item.timing.available_at, item.timing.interpretation_effective_at,
    item.interpretation_ref.available_at, item.timing.source_published_at];
  if (sourceClocks.some(value => value !== null && (clock(value) === null || clock(value)! > generated))) return null;
  const checked = checkedCompanyFacts(value.facts, captured);
  if (!checked?.length) return null;
  const facts = new Map(checked.map(fact => [fact.id, fact]));
  const analysis = value.analysis;
  if (!analysis || !["conditional", "mixed", "insufficient", "no_supported_link"].includes(analysis.status)
    || !text(analysis.summary) || !Array.isArray(analysis.chain) || analysis.chain.length > 6
    || !Array.isArray(analysis.counterarguments) || analysis.counterarguments.length > 12 || !analysis.counterarguments.every(entry => text(entry))
    || !Array.isArray(analysis.missing) || analysis.missing.length > 12 || !analysis.missing.every(entry => text(entry))) return null;
  let companyReferenced = false, eventReferenced = false;
  for (const step of analysis.chain) {
    if (!step || !text(step.text) || !Array.isArray(step.evidence_ids) || !step.evidence_ids.length || step.evidence_ids.length > 14) return null;
    for (const id of step.evidence_ids) {
      if (facts.has(id)) companyReferenced = true;
      else if ((id === "event:title" || id === "event:summary") && text(item.source_context?.[id === "event:title" ? "title" : "summary"])) eventReferenced = true;
      else return null;
    }
  }
  if ((analysis.chain.length || ["conditional", "mixed"].includes(analysis.status)) && (!companyReferenced || !eventReferenced)) return null;
  return value;
}

function CompanyImpactEvidence({item, symbol, now}: {item: AttributionEvidenceItem; symbol?: string | null; now: number}) {
  const deadline = companyClock(item.company_impact?.valid_until);
  const value = boundCompanyImpact(item, symbol, now);
  if (!value) {
    const prior = item.company_impact;
    const expired = !!prior && prior.symbol === symbol && prior.event_id === item.event_id
      && prior.version_id === item.interpretation_ref.version_id && prior.observation_id === item.interpretation_ref.observation_id
      && deadline !== null && deadline <= now;
    return prior && symbol ? <p role="note" className="py-1 text-zinc-600 dark:text-zinc-400">{expired ? `已保存的本股经营判读已于${timeLabel(prior.valid_until)}过期，暂不作为当前解释；` : "本股经营判读的身份、来源或有效期未通过核对，暂不展示；"}这不代表公司没有相关业务。</p> : null;
  }
  const status = {conditional: "有条件影响", mixed: "双向影响", insufficient: "证据不够", no_supported_link: "尚无材料支持直接传导"}[value.analysis.status];
  const reference = (id: string) => id === "event:title" ? "消息标题" : id === "event:summary" ? "消息摘要" : `公司依据 ${id}`;
  return <section aria-label="公司经营依据与AI传导" className="space-y-3 py-2">
    <header><h4 className="font-medium">{value.company_name} · 公司经营判读</h4><p>{status} · 仅用于当前研究</p><p className="text-zinc-600 dark:text-zinc-400">{value.verification === "automated_evidence_bound_not_gold" ? "自动引用约束研究解释 · 未经作者逐案源审" : "AI生成、作者核对并校正的研究解释"}</p></header>
    <section aria-label="AI经营传导假设"><h5 className="font-medium">AI经营传导假设</h5><p className={styles.sourceCopy}>{value.analysis.summary}</p>
      {value.analysis.chain.length ? <ol className="space-y-2">{value.analysis.chain.map((step, index) => <li key={index}><p className={styles.sourceCopy}>{index + 1}. {step.text}</p><p className="text-zinc-600 dark:text-zinc-400">引用依据：{step.evidence_ids.map(reference).join("；")}</p></li>)}</ol> : <p>现有材料不足以建立公司经营传导链，未补写。</p>}
    </section>
    <CompanySourceFacts facts={value.facts}/>
    <ConditionList label="反例与成立条件" items={value.analysis.counterarguments.length ? value.analysis.counterarguments : ["本次判读未列出反例与成立条件，不等于影响没有条件。"]} tone="warning"/>
    <ConditionList label="经营证据缺失" items={value.analysis.missing.length ? value.analysis.missing : ["本次材料未列出额外缺项，不代表资料完整。"]} tone="warning"/>
    <p className="text-zinc-600 dark:text-zinc-400">公司原文与AI推演分别展示。{value.verification === "automated_evidence_bound_not_gold" ? "模型选择引用后由代码编译条件机制，只完成自动引用约束，未经作者逐案源审或独立效果验证。" : "这是AI生成、作者核对并校正的研究解释，尚无独立效果验证。"}不回填原入选依据，不改变排序、评分、入选或通知资格。</p>
    <p className="text-zinc-600 dark:text-zinc-400">资料采集：{timeLabel(value.facts_captured_at)} · 判读生成：{timeLabel(value.generated_at)} · 有效至：{timeLabel(value.valid_until)} · 模型：{value.model.provider} / {value.model.model}</p>
  </section>;
}

const COMPANY_STATES: Record<CompanyStatus["state"], string> = {
  idle: "等待采集条件", queued: "已排队", collecting: "正在采集公司原文", generating: "正在生成研究解释",
  ready: "本次研究结果已保存", insufficient: "证据不够", source_failed: "公司原文采集失败", model_failed: "模型判读失败",
  invalid_response: "模型结果未通过引用核对", budget_blocked: "模型预算已阻断", canceled: "任务已取消",
  interrupted: "任务中断，等待恢复", disabled: "常规采集未启用", historical_excluded: "所查时点无可用资料",
};

function boundCompanyStatus(value: CompanyStatus | undefined, symbol: string | null | undefined, now: number) {
  if (!value || !symbol || value.symbol !== symbol || !Object.hasOwn(COMPANY_STATES, value.state)
    || typeof value.reason !== "string" || value.reason.length > 1000 || !Array.isArray(value.missing) || value.missing.length > 12
    || !value.missing.every(text => typeof text === "string" && !!text.trim() && text.length <= 500)
    || !Number.isSafeInteger(value.documents_count) || value.documents_count < 0
    || (value.task_id != null && (typeof value.task_id !== "string" || value.task_id.length > 160))
    || (value.source_status !== undefined && (typeof value.source_status !== "string" || value.source_status.length > 80))) return null;
  const updated = companyClock(value.updated_at), retry = companyClock(value.next_retry_at);
  if (updated === null || updated > now || (value.next_retry_at != null && (retry === null || retry < updated))) return null;
  return value;
}

function CompanyStatusEvidence({value, symbol, now, showFacts, responseAsOf, compact = false}: {
  value?: CompanyStatus; symbol?: string | null; now: number; showFacts: boolean; responseAsOf?: string; compact?: boolean;
}) {
  const current = boundCompanyStatus(value, symbol, now);
  if (!current) return value && symbol ? <p role="note" className="py-1 text-zinc-600 dark:text-zinc-400">公司资料状态的证券或时刻未通过核对，暂不展示；原有消息资料保留。</p> : null;
  const updated = companyClock(current.updated_at)!;
  const cutoff = companyClock(responseAsOf);
  const historicalUnknown = !!responseAsOf && (cutoff === null || updated > cutoff);
  const facts = !historicalUnknown && current.documents_count > 0 ? checkedCompanyFacts(current.facts, updated) : null;
  const busy = ["queued", "collecting", "generating"].includes(current.state);
  return <section aria-label={compact ? "公司资料与判读状态摘要" : "公司资料与判读状态"} className="min-w-0 space-y-2 py-2">
    <h4 className="font-medium">公司资料与判读</h4>
    <p role={busy ? "status" : "note"}>{COMPANY_STATES[current.state]}{current.reason ? `：${current.reason}` : ""}</p>
    {!compact && <>
      <ConditionList label="本轮具体缺项" items={current.missing.length ? current.missing : ["本轮未列出额外缺项，不代表资料完整或经营效果已验证。"]} tone="warning"/>
      <p className="text-zinc-600 dark:text-zinc-400">已保存 {current.documents_count} 份公司原文 · 状态记录于 {timeLabel(current.updated_at)}{current.next_retry_at ? ` · 下次可重试时点：${timeLabel(current.next_retry_at)}` : ""}</p>
      {showFacts && facts && facts.length > 0 && <section aria-label="已保存公司原文" className="space-y-2">
        <p className="text-zinc-600 dark:text-zinc-400">以下为已保存的公司资料；尚不能据此补出本条事件的经营传导。</p>
        <CompanySourceFacts facts={facts}/>
      </section>}
      {showFacts && historicalUnknown && !!current.facts?.length && <p className="text-zinc-600 dark:text-zinc-400">公司资料的可得时刻无法对应所查历史时点，本次不展示，不回填当时依据。</p>}
      {showFacts && !historicalUnknown && !!current.facts?.length && !facts && <p className="text-zinc-600 dark:text-zinc-400">公司原文未通过来源或时间核对，暂不展示；这不代表公司没有相关业务。</p>}
      <p className="text-zinc-600 dark:text-zinc-400">刷新只读取已保存资料，不启动模型。任务恢复和维护见任务中心；公司原文不等于本条事件的影响已验证。</p>
    </>}
  </section>;
}

function clock(value: string | null | undefined): number | null {
  if (!value || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const result = Date.parse(value);
  return Number.isFinite(result) ? result : null;
}

function sourceUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : null;} catch {return null;}
}

function timeLabel(value: string | null | undefined): string {
  return clock(value) !== null ? `${bjDate(value!)} ${timeTextBJ(value!)}` : eventTimeText(value) || "未记录";
}

/** Stay inside known local readers; never move the page or document viewport. */
function readingViewport(element: HTMLElement | null): HTMLElement | null {
  let ancestor = element?.parentElement;
  while (ancestor && ancestor !== document.body && ancestor !== document.documentElement) {
    if (ancestor.hasAttribute("data-attribution-reader")) return ancestor;
    if (!ancestor.matches(".task-scroll, .workspace-content") && ancestor.matches(".data-scroll, .overflow-y-auto, .overflow-auto, .stock-context-brief, .stock-workspace, .task-panel-body")) {
      const style = getComputedStyle(ancestor);
      if (/^(auto|scroll)$/.test(style.overflowY || style.overflow)) return ancestor;
    }
    if (ancestor.matches('[role="dialog"]')) return null;
    ancestor = ancestor.parentElement;
  }
  return null;
}

function SourceExcerpt({context, label}: {context: AttributionSourceContext | null; label: string}) {
  const href = sourceUrl(context?.url);
  return <div className="min-w-0 space-y-1">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="min-w-0 font-medium [overflow-wrap:anywhere]">{label}</h4>{href && <a data-action="quiet" href={href} target="_blank" rel="noopener noreferrer" className={`quiet-action shrink-0 ${styles.action}`} aria-label={`${label}原文`}>查看原文</a>}</div>
    {context ? <blockquote className={styles.sourceCopy}><p>{context.title}</p>{context.summary && <p aria-label={label}>{context.summary}</p>}</blockquote> : <p className="text-zinc-600 dark:text-zinc-400">原始内容未记录，不能核对完整语境。</p>}
    {context && !context.summary && <p className="text-zinc-600 dark:text-zinc-400">仅保存标题；正文条件尚未核对。</p>}
    {!href && <p className="text-zinc-600 dark:text-zinc-400">原文链接未记录。</p>}
  </div>;
}

/** The same explanation vocabulary serves source events, stock details and selection drawers. */
export function AttributionEvidence({item, symbol, selectedAt, recordedRef, selectionContext = false, defaultSourceExpanded = false, complete = false}: {
  item: AttributionEvidenceItem; symbol?: string | null; selectedAt?: string | null; recordedRef?: EventInterpretationRef; selectionContext?: boolean; defaultSourceExpanded?: boolean; complete?: boolean;
}) {
  const companyNow = useCompanyEvidenceClock([item.company_impact?.generated_at, item.company_impact?.valid_until].filter(Boolean).join("|"));
  const available = clock(item.timing.available_at);
  const effective = clock(item.timing.interpretation_effective_at);
  const selected = clock(selectedAt);
  const late = available !== null && selected !== null && available > selected;
  const lateInterpretation = effective !== null && selected !== null && effective > selected;
  const revised = !!recordedRef?.version_id && recordedRef.version_id !== item.interpretation_ref.version_id;
  const version = item.interpretation_ref.version_id;
  const publisher = item.source.original_publisher;
  const relation = item.stock_relation;
  const observation = item.source_context?.observation_id;
  const hasSourceIdentity = typeof observation === "number" && Number.isInteger(observation) && observation > 0;
  const sourceBound = typeof version === "number" && Number.isInteger(version) && version > 0 && hasSourceIdentity && observation === item.interpretation_ref.observation_id;
  const excerptLabel = !item.source_context ? "来源内容未记录" : !hasSourceIdentity ? "旧卡片摘录 · 来源观察身份未记录" : sourceBound ? "解释版本绑定的来源内容" : "来源观察内容 · 解释绑定待核";
  const stageUnknown = !["done", "proposed", "rumor"].some(value => value === item.certainty);
  const savedAux = boundAuxiliary(item);
  const stockReasoning = boundStockReasoning(item, symbol);
  const aiLabel = symbol && stockReasoning?.ai_scope !== "company_context" ? "消息AI观点（非个股结论）" : "AI解读（待核）";
  const qualifiers = item.qualifiers.map(value => `${value.label}${value.matched_terms.length ? `（${value.matched_terms.join("、")}）` : ""}`);
  const relationLabel = symbol ? "个股关联" : "题材关联";
  const sourceSummary = item.source_context?.summary;
  const preview = (text: string) => text.length <= 160 ? text : `${text.slice(0,160)}…`;

  if (!complete) return <section className={styles.evidence} aria-label={`事件归因 ${item.event_id}`}>
    <ReaderBrief label="消息与个股说明" headline={item.title} facts={[
      {label: "怎么相关", value: relation.status === "theme_only" ? "只关联到题材，尚不能确认这只股票受益" : relation.status === "direct_symbol" ? "消息直接提到这家公司，影响仍需核实" : relation.status === "source_association" ? "来源记录了公司关联，影响仍需核实" : "还没有足够的公司关联依据"},
      {label: "消息进展", value: !item.is_active || item.interpretation_ref.state === "withdrawn" ? "已失效或撤回，不作为当前依据" : item.certainty === "proposed" ? "仍是计划，尚未落地" : item.certainty === "rumor" ? "传闻，尚未证实" : item.certainty === "done" ? "来源称已发生，未独立核实" : "进展还不清楚"},
      {label: savedAux ? aiLabel : "内容摘录", value: readerExcerpt(savedAux?.reason || sourceSummary || item.source_context?.title) || "来源正文未保存"},
    ]} cautions={[...readerCautions([sourceSummary || "", relation.basis, ...qualifiers, ...item.limitations]), ...(savedAux ? ["AI解读未独立验证"] : []), ...(!sourceBound ? ["来源与解释尚未对应完整"] : []), ...(late || lateInterpretation ? ["这条解释晚于入选，仅供事后复盘"] : []), ...(revised ? ["解释已变更，不替换原入选依据"] : []), ...(selectedAt && (selected === null || available === null || effective === null) ? ["时间记录不全，无法确认入选时是否已知"] : [])]}/>
    <StockReasoningEvidence item={item} symbol={symbol} now={companyNow}/>
    <CompanyImpactEvidence item={item} symbol={symbol} now={companyNow}/>
    <ResearchDisclosure identity={`${item.event_id}:${version}:${observation}:${item.state}:${JSON.stringify(item)}`}>
      <AttributionEvidence item={item} symbol={symbol} selectedAt={selectedAt} recordedRef={recordedRef} selectionContext={selectionContext} defaultSourceExpanded complete/>
    </ResearchDisclosure>
  </section>;

  return <article className={styles.evidence} aria-label={`事件归因 ${item.event_id}`}>
    <header className={styles.evidenceHeader}><h3>{item.title}</h3>
      {!!item.dimensions.length && <ul className={styles.dimensions} aria-label="来源线索维度">{item.dimensions.map(value => <li key={value.key}>{value.label}</li>)}</ul>}
    </header>
    <p className={styles.boundary}>来源收录不等于消息属实；关联与方向仍须核对。</p>
    <StockReasoningEvidence item={item} symbol={symbol} now={companyNow}/>
    <CompanyImpactEvidence item={item} symbol={symbol} now={companyNow}/>
    {item.timing_relation && <p role="note" className="text-zinc-600 dark:text-zinc-400">{item.timing_relation.label}。时点关系不证明涨停因果。</p>}
    {stageUnknown && <p role="note" className="text-amber-800 dark:text-amber-300">事件阶段尚未核实，不能按已落地事件解读。</p>}
    {(late || lateInterpretation || revised) && <p role="note" className="text-amber-800 dark:text-amber-300">{late ? "消息可用时间晚于首次加入，仅供后续复盘。" : lateInterpretation ? "当前解释形成晚于首次加入，仅供后续复盘。" : selectionContext ? "当前解释与选股记录引用的原版本不同。" : "当前解释与列表入口引用的版本不同。"}{selectionContext || selectedAt ? "不回填当时入选依据。" : "列表快照保留原记录。"}</p>}
    {selectedAt && (selected === null || available === null || effective === null) && <p className="text-zinc-600 dark:text-zinc-400">时点证据不完整，无法确认这条解释在首次加入时是否可用。</p>}
    {defaultSourceExpanded ? <section className={styles.nodeContent} aria-label="完整来源核对"><p>{item.fact_support.note}</p><SourceExcerpt context={item.source_context} label={excerptLabel}/></section>
      : sourceSummary && <section className={styles.nodeContent} aria-label="来源要点摘录"><h4>{sourceBound ? "绑定来源要点 · 原文摘录" : "来源要点 · 绑定待核"}</h4><p className={styles.sourceCopy}>{preview(sourceSummary)}</p></section>}
    {!complete && (relation.basis || relation.chain) && <section className={styles.nodeContent} aria-label="当前关联依据摘要"><h4>已记录依据 · 待核</h4>{relation.basis && <p className={styles.sourceCopy}>{preview(relation.basis)}</p>}{relation.chain && relation.chain !== relation.basis && <p className={styles.sourceCopy}>{preview(relation.chain)}</p>}</section>}
    {<div className={styles.nodeContent} aria-label="完整依据核对">
      <section><h4>来源与解释引用</h4><p>{item.fact_support.label} · {item.market_response.status_label}</p><p>事件 #{item.event_id} · 解释 {version ? `#${version}` : "未知"} · 来源观察 {observation ? `#${observation}` : "未知"}</p>
        <dl className={styles.sourceFacts}><div><dt>原始发布者</dt><dd>{publisher || "未记录"}</dd></div><div><dt>采集平台</dt><dd>{item.source.collector ? sourceLabel(item.source.collector) : "未记录"}</dd></div><div><dt>来源等级</dt><dd>{item.source.source_tier_label}</dd></div><div><dt>来源绑定</dt><dd>{sourceBound ? "本解释引用的来源" : "待核，未补推"}</dd></div></dl>
        {item.pending_context && <SourceExcerpt context={item.pending_context} label="来源已修订，待复核内容" />}
        {item.withdrawal_context && <SourceExcerpt context={{...item.withdrawal_context, change_kind: "withdrawal"}} label="撤回依据" />}
      </section>
      <section aria-label="完整关联依据"><h4>{relationLabel} · {relation.label}</h4><p>关联与已记录方向不证明消息属实或个股受益。</p>
        {relation.basis && <p className={styles.sourceCopy}>{relation.basis}</p>}{relation.chain && relation.chain !== relation.basis && <p className={styles.sourceCopy}>{relation.chain}</p>}
        {!relation.basis && !relation.chain && <p>关联依据与传导线索未记录。</p>}
        <p>方向原标记：{relation.direction === null ? "未知" : String(relation.direction)} · 匹配口径：{relation.matched_by || "未记录"}</p>
        {relation.matched_themes.length > 0 && <ConditionList label="已记录关联题材" items={relation.matched_themes}/>}
      </section>
      <section><h4>市场响应 · 待核</h4><p className={styles.sourceCopy}>{item.market_response.basis}</p><p>市场响应不证明消息属实或涨停因果。</p><ConditionList label="其他解释与待核线索" items={item.alternatives} />{item.review_note && <p>{item.review_note}</p>}</section>
    </div>}
    {savedAux && <section className={styles.nodeContent} aria-label="已保存辅助判读"><h4>{savedAux.state === "no_direction" ? "辅助判读：无明确方向" : "辅助方向假设"}</h4><p>尚未验证，不影响选股资格。</p>{symbol && stockReasoning?.ai_scope !== "company_context" && <p>该AI回执只用于消息研究，当前不能支持本股判断。</p>}<p className={styles.sourceCopy}>{savedAux.reason}</p>{savedAux.chain && <p className={styles.sourceCopy}>{savedAux.chain}</p>}<blockquote className={styles.sourceCopy}>{savedAux.evidence.quote}</blockquote><p>输入解释 {savedAux.input_version_id ? `#${savedAux.input_version_id}` : "未知"} · 来源观察 {savedAux.observation_id ? `#${savedAux.observation_id}` : "未知"}</p></section>}
    <FactTimeline label="来源与解释时点" items={[
      {label: "来源发布", value: item.timing.source_time_known ? timeLabel(item.timing.source_published_at) : "未知，未用收录时间补填", kind: "time"},
      {label: "系统收到", value: timeLabel(item.timing.received_at), kind: "time"},
      {label: "消息可用", value: timeLabel(item.timing.available_at), kind: "time"},
      {label: "解释生效", value: timeLabel(item.timing.interpretation_effective_at), kind: "time"},
    ]}/>
    <div className={styles.conditionArea}><ConditionList label="关键条件" items={qualifiers.length ? qualifiers : ["未记录关键条件，不等于事件没有条件。"]} tone="warning" /><ConditionList label="解读边界" items={item.limitations.length ? item.limitations : ["解读边界未记录，仍需核对完整语境。"]} tone="warning" /></div>
  </article>;
}

/** One local reading position; list navigation reuses the already loaded evidence. */
function AttributionItems({items, laterItems = [], responseContext = false, symbol, selectedAt, recordedRefs, selectionContext, defaultSourceExpanded}: {
  items: AttributionEvidenceItem[]; laterItems?: AttributionEvidenceItem[]; responseContext?: boolean; selectedAt?: string | null; recordedRefs?: EventInterpretationRef[];
  symbol?: string | null; selectionContext: boolean; defaultSourceExpanded: boolean;
}) {
  const [selection, setSelection] = useState<{id: number | null; removed: boolean}>({id: null, removed: false});
  const selectedItem = [...items, ...laterItems].find(item => item.event_id === selection.id);
  const entryButtons = useRef(new Map<number, HTMLButtonElement>());
  const backButton = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const returnFocus = useRef<number | null>(null);
  const listScroll = useRef(0);
  const view = useRef<HTMLDivElement>(null);
  const selectedEventId = selectedItem?.event_id ?? null;

  // Reset a removed selection before commit, so a later refresh cannot reopen it silently.
  if (selection.id !== null && !selectedItem) setSelection({id: null, removed: true});

  useLayoutEffect(() => {
    const viewport = readingViewport(view.current);
    if (selectedEventId !== null) {
      if (viewport && backButton.current) viewport.scrollTop = Math.max(0, viewport.scrollTop + backButton.current.getBoundingClientRect().top - viewport.getBoundingClientRect().top - 8);
      backButton.current?.focus({preventScroll: true});
    } else if (returnFocus.current !== null) {
      if (viewport) viewport.scrollTop = listScroll.current;
      const target = entryButtons.current.get(returnFocus.current) || entryButtons.current.values().next().value || list.current;
      target?.focus({preventScroll: true});
      returnFocus.current = null;
    }
  }, [selectedEventId]);

  return <div ref={view} className="min-w-0">
    {selection.removed && <p role="status" className="py-2 leading-relaxed text-zinc-600 dark:text-zinc-400">所选消息已不在本次资料中，已返回列表。</p>}
    {selectedItem ? <div className="anim-fade-in min-w-0">
      <button ref={backButton} type="button" data-action="quiet" className={`quiet-action ${styles.action}`} onClick={() => setSelection({id: null, removed: false})}><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="mr-1"><path d="m14 6-6 6 6 6" /></svg>返回消息列表</button>
      <AttributionEvidence item={selectedItem} symbol={symbol} selectedAt={selectedAt} recordedRef={recordedRefs?.find(ref => ref.event_id === selectedItem.event_id)} selectionContext={selectionContext} defaultSourceExpanded={defaultSourceExpanded} />
    </div> : <ul ref={list} tabIndex={-1} aria-label="归因消息列表" className="min-w-0 divide-y divide-zinc-200 dark:divide-zinc-700/60">
      {(responseContext ? [{label: "响应时点前已有记录", rows: items}, {label: "晚到或时点未明资料", rows: laterItems}] : [{label: null, rows: items}]).map(group => <li key={group.label ?? "all"} className="list-none min-w-0">
        {group.label && <h4 className="py-2 font-medium">{group.label} · {group.rows.length} 条</h4>}
        <ul className="min-w-0 divide-y divide-zinc-200 dark:divide-zinc-700/60">
        {group.rows.map(item => <li key={item.event_id} className="min-w-0"><button ref={button => {if (button) entryButtons.current.set(item.event_id, button); else entryButtons.current.delete(item.event_id);}} type="button" data-action="quiet" className={`quiet-action ${styles.entry}`} aria-label={`查看归因消息：${item.title}`} onClick={() => {
          returnFocus.current = item.event_id;
          listScroll.current = readingViewport(view.current)?.scrollTop || 0;
          setSelection({id: item.event_id, removed: false});
        }}><span className="min-w-0"><span className={styles.title}>{item.title}</span><span className={styles.meta}><span>{item.state_label}</span><span>{item.stock_relation.label}</span>{item.timing_relation && <span>{item.timing_relation.label}</span>}</span></span><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m9 6 6 6-6 6" /></svg></button></li>)}
        </ul>
      </li>)}
    </ul>}
  </div>;
}

/** Toolbar reads only on open; visible reason fallbacks read once, with no polling. */
export const StockAttributionPanel = memo(function StockAttributionPanel({symbol, eventId, active = true, title = "驱动线索", defaultExpanded = false, bounded = true, selectedAt, recordedRefs, selectionContext = false, defaultSourceExpanded = false, poolDate, responseAsOf, primarySummary = false, inlinePrimary = false, sourceReason, sourceCore, sourceAudit}: {
  symbol?: string | null; eventId?: number | null; active?: boolean; title?: string; defaultExpanded?: boolean; bounded?: boolean;
  selectedAt?: string | null; recordedRefs?: EventInterpretationRef[];
  selectionContext?: boolean; defaultSourceExpanded?: boolean;
  poolDate?: string; responseAsOf?: string; primarySummary?: boolean; inlinePrimary?: boolean; sourceReason?: string | null;
  /** Inline cause readers keep their already-visible source core and full audit in one reading flow. */
  sourceCore?: string; sourceAudit?: ReactNode;
}) {
  const [open, setOpen] = useState(defaultExpanded);
  const [readingCycle, setReadingCycle] = useState(0);
  const id = useId();
  const identity = `${symbol || ""}:${eventId || ""}:${poolDate || ""}:${responseAsOf || ""}`;
  const presence = useExitPresence(active && open ? identity : null);
  const [openedIdentity, setOpenedIdentity] = useState(identity);
  if (openedIdentity !== identity) {setOpenedIdentity(identity); if (primarySummary) setOpen(false);}
  if (primarySummary && !active && open) setOpen(false);
  const options = poolDate || responseAsOf ? {poolDate, responseAsOf} : undefined;
  const resource = useResource(() => eventId ? options ? getEventAttribution(eventId, symbol, options) : getEventAttribution(eventId, symbol) : options ? getStockAttribution(symbol!, options) : getStockAttribution(symbol!), {key: identity, enabled: active && (open || primarySummary || inlinePrimary) && (!!eventId || /^\d{6}$/.test(symbol || "")), intervalMs: null, marketHours: false});
  const data = resource.data;
  const companyNow = useCompanyEvidenceClock([data?.company_status?.updated_at, ...[...(data?.items || []), ...(data?.later_items || []), ...(data?.company_items || [])]
    .flatMap(item => [item.company_impact?.generated_at, item.company_impact?.valid_until])].filter(Boolean).join("|"));
  const windowEventIds = new Set([...(data?.items || []), ...(data?.later_items || [])].map(item => item.event_id));
  const companyItems = (data?.company_items || []).slice(0, 4).filter(item => {
    if (windowEventIds.has(item.event_id) || boundCompanyImpact(item, symbol, companyNow) === null) return false;
    windowEventIds.add(item.event_id);
    return true;
  });
  const companyEventIds = new Set(companyItems.map(item => item.event_id));
  const readItems = data ? [...new Map([...data.items, ...(data.later_items || []), ...companyItems].map(item =>
    [`${item.event_id}:${item.interpretation_ref.version_id}:${item.interpretation_ref.observation_id}`, item])).values()] : [];
  const savedCompanyAI = readItems.filter(item => boundCompanyImpact(item, symbol, companyNow) !== null).length;
  const savedAI = readItems.filter(item => boundAuxiliary(item) !== null || boundCompanyImpact(item, symbol, companyNow) !== null).length;
  const coverage = data && <p className="py-1 leading-relaxed text-zinc-600 dark:text-zinc-400" aria-label="AI解读读取范围">已读取 {readItems.length} 条消息，其中 {savedAI} 条有已保存AI解读（未独立验证）{savedCompanyAI > 0 ? `，含 ${savedCompanyAI} 条本股经营判读` : ""}。其余条目只展示来源；刷新仅重读结果，不会启动判读。</p>;
  const primary = data?.primary_reason;
  const boundItem = data ? boundPrimaryItem(data) : null;
  const sourceMatch = boundItem ? matchSourceReason(boundItem, sourceReason) : null;
  const primaryItem = inlinePrimary && boundItem && (!["direct_symbol", "source_association"].includes(boundItem.stock_relation.status) || !sourceMatch) ? null : boundItem;
  const otherItems = primaryItem ? data!.items.filter(item => item.event_id !== primaryItem.event_id) : data?.items || [];
  const otherLater = primaryItem ? (data!.later_items || []).filter(item => item.event_id !== primaryItem.event_id) : data?.later_items || [];
  const companyLabel = companyItems.every(item => item.company_impact?.verification === "author_source_reviewed_not_gold")
    ? "已源审经营补充（原消息窗口外）" : companyItems.every(item => item.company_impact?.verification === "automated_evidence_bound_not_gold")
      ? "自动引用约束经营补充（原消息窗口外）" : "经营研究补充（原消息窗口外）";
  const statusAsOf = responseAsOf || data?.response_context?.response_as_of || undefined;
  const companyStatusReading = <CompanyStatusEvidence value={data?.company_status} symbol={symbol} now={companyNow} showFacts={savedCompanyAI === 0} responseAsOf={statusAsOf}/>;
  const companyReading = companyItems.length > 0 && <section aria-label={companyLabel} className="min-w-0 space-y-2 py-2">
    <h4 className="font-medium">{companyLabel} · {companyItems.length} 条</h4>
    <p className="text-zinc-600 dark:text-zinc-400">当前研究补充，不替换最新消息、市场触发线索或历史入选依据。</p>
    <AttributionItems key={`${identity}:${readingCycle}:company`} items={companyItems} symbol={symbol} selectedAt={selectedAt} recordedRefs={recordedRefs} selectionContext={selectionContext} defaultSourceExpanded={defaultSourceExpanded}/>
  </section>;
  if (inlinePrimary) {
    const boundAux = primaryItem ? boundAuxiliary(primaryItem) : null;
    const otherSavedAI = readItems.filter(item => (item.event_id !== primaryItem?.event_id
      || item.interpretation_ref.version_id !== primaryItem?.interpretation_ref.version_id
      || item.interpretation_ref.observation_id !== primaryItem?.interpretation_ref.observation_id) && (boundAuxiliary(item) !== null || boundCompanyImpact(item, symbol, companyNow) !== null));
    const otherWindowAI = otherSavedAI.filter(item => !companyEventIds.has(item.event_id));
    const sourceText = [sourceReason, primaryItem?.source_context?.title, primaryItem?.source_context?.summary].filter(Boolean).join("\n");
    const sourceConditions = limitReasonView(sourceText).conditions;
    const conditionKey = (value: string) => value.replace(/\s+/g, "").replace(/[。；;！？!?]+$/, "");
    const fullConditions = sourceConditions.map(conditionKey).join("\n");
    // A complete source sentence carries its own qualifiers. Keep a label only
    // when the saved qualifier has no corresponding visible source wording.
    const qualifiers = [...(primaryItem ? primary?.qualifiers || [] : []), ...(primaryItem?.qualifiers || [])].filter(value =>
      value.matched_terms.length ? !value.matched_terms.every(term => fullConditions.includes(conditionKey(term))) : !fullConditions.includes(conditionKey(value.label))
    ).map(value => `${value.label}${value.matched_terms.length ? `（${value.matched_terms.join("、")}）` : ""}`);
    const seen = new Set(sourceCore ? [conditionKey(sourceCore)] : []);
    const conditions = [...sourceConditions, ...qualifiers, ...(primaryItem?.limitations || [])].filter(value => {
      const key = conditionKey(value);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    const auditIdentity = `${identity}:${sourceReason || ""}:${primaryItem?.interpretation_ref.version_id || ""}:${primaryItem?.interpretation_ref.observation_id || ""}`;
    return <section aria-label={title} className="min-w-0 space-y-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-2"><h3 className="font-medium">已保存解读{boundAux?.state === "no_direction" ? " · 无明确方向" : ""}</h3>{!!resource.error && <button type="button" data-action="secondary" disabled={resource.pending} onClick={resource.refresh}>重试解读</button>}</div>
      {resource.error ? <p role="alert" className="text-amber-800 dark:text-amber-300">解读读取失败{data ? " · 保留已有内容" : "，暂不能核对"}</p> : resource.pending && !data && <p role="status" className="text-zinc-600 dark:text-zinc-400">读取已保存解读…</p>}
      {coverage}
      {companyStatusReading}
      {data && !primaryItem && <p className="text-zinc-600 dark:text-zinc-400">暂无可核对的直接来源线索，不用题材标签补填原因。</p>}
      {primaryItem && <>
        <ReaderBrief label="原因与依据关系" facts={[
          {label: boundAux ? "AI解读（待核）" : "消息摘录", value: readerExcerpt(boundAux?.reason || primary?.excerpt?.quote)},
          {label: "与公司", value: primaryItem.stock_relation.status === "direct_symbol" ? "消息直接提到公司，尚不能证明涨停由此引起" : "来源提及公司关联，实际影响待核实"},
          {label: "进展", value: !primaryItem.is_active || primaryItem.interpretation_ref.state === "withdrawn" ? "已失效或撤回" : primaryItem.certainty === "proposed" ? "尚属计划" : primaryItem.certainty === "rumor" ? "传闻待证实" : primaryItem.certainty === "done" ? "来源称已发生" : "尚不清楚"},
        ]} cautions={[...readerCautions(conditions), ...(conditions.length ? ["完整限制条件见依据"] : []), ...(primaryItem.timing_relation && primaryItem.timing_relation.status !== "known_before_response" ? ["消息时间未能对应首封时点"] : []), ...(boundAux ? ["AI解读仅作参考，未独立验证"] : [])]}/>
      </>}
      {primaryItem && <StockReasoningEvidence item={primaryItem} symbol={symbol} now={companyNow}/>}
      {primaryItem && <CompanyImpactEvidence item={primaryItem} symbol={symbol} now={companyNow}/>}
      {!primaryItem && conditions.length > 0 && <ReaderBrief label="来源原因限制" cautions={readerCautions(conditions)}/>}
      {companyReading}
      {otherWindowAI.length > 0 && <details><summary className="w-fit cursor-pointer text-zinc-600 dark:text-zinc-400">其他已保存AI解读 · {otherWindowAI.length} 条</summary><div className="space-y-3 py-2">{otherWindowAI.map(item => <AttributionEvidence key={`${item.event_id}:${item.interpretation_ref.version_id}:${item.interpretation_ref.observation_id}`} item={item} symbol={symbol} selectedAt={selectedAt} selectionContext={selectionContext} complete />)}</div></details>}
      <p className="text-zinc-600 dark:text-zinc-400">来源解释，尚不能确认是涨停的真正原因。</p>
      {(sourceAudit || primaryItem) && <ResearchDisclosure identity={auditIdentity} label="来源原文与完整核对"><div className="min-w-0 space-y-3 py-2"><ConditionList label="必要条件" items={conditions} tone="warning"/>
        {sourceAudit}
        {primaryItem && <>
          <p className="text-zinc-600 dark:text-zinc-400">{primary?.label} · 当前保存解释，不回填首封或入选依据。</p>
          <p className="text-zinc-600 dark:text-zinc-400">与来源原因交叉：{sourceMatch}，仅作展示关联核对。</p>
          {boundAux && <p className="text-zinc-600 dark:text-zinc-400">已保存辅助判读 · {boundAux.state === "no_direction" ? "无明确方向" : "待验证假设"}，尚未验证，不改变选股资格。</p>}
          {primaryItem.timing_relation && <p className="text-zinc-600 dark:text-zinc-400">{primaryItem.timing_relation.label}，不回填首封时的原因。</p>}
          <FactTimeline label="关键来源时点" items={[{label: "消息发布", value: primaryItem.timing.source_time_known ? timeLabel(primaryItem.timing.source_published_at) : "未知", kind: "time"}, {label: "解释生效", value: timeLabel(primaryItem.timing.interpretation_effective_at), kind: "time"}]}/>
          <AttributionEvidence item={primaryItem} symbol={symbol} selectedAt={selectedAt} selectionContext={selectionContext} defaultSourceExpanded complete/>
        </>}
      </div></ResearchDisclosure>}
    </section>;
  }
  const primaryReading = primary && <div className="min-w-0 space-y-3">
    <p className="text-zinc-600 dark:text-zinc-400">{primary.label} · {primary.reason}。来源线索不是已核实因果，不回填入选与首封依据。</p>
    {!!primary.event_id && !primaryItem && <p role="alert" className="text-amber-800 dark:text-amber-300">主线索来源绑定待核，未自动选择其他资料。</p>}
    {primaryItem && <AttributionEvidence item={primaryItem} symbol={symbol} selectedAt={selectedAt} recordedRef={recordedRefs?.find(ref => ref.event_id === primaryItem.event_id)} selectionContext={selectionContext} defaultSourceExpanded />}
    {(otherItems.length > 0 || otherLater.length > 0) && <details><summary className="cursor-pointer text-zinc-600 dark:text-zinc-400">其他关联资料 · {otherItems.length + otherLater.length} 条</summary><AttributionItems items={otherItems} laterItems={otherLater} responseContext={!!data?.response_context} symbol={symbol} selectedAt={selectedAt} recordedRefs={recordedRefs} selectionContext={selectionContext} defaultSourceExpanded={defaultSourceExpanded}/></details>}
  </div>;
  if (primarySummary) return <section aria-label={title} className="min-w-0 py-1 text-xs">
    <div className="flex min-w-0 items-center gap-2">
      {resource.error ? <p role="alert" className="min-w-0 text-amber-800 dark:text-amber-300">来源线索读取失败{data ? " · 保留结果" : ""}</p>
        : resource.pending && !data ? <p role="status" className="text-zinc-600 dark:text-zinc-400">来源线索读取中…</p>
          : <span className="min-w-0 flex-1 truncate" title={primary ? `${primary.label}：${primary.reason}；不证明因果` : "主线索未确认"}><span className="mr-1 text-zinc-600 dark:text-zinc-400">{primary?.label || "主线索未确认"}</span>{primaryItem ? primary?.excerpt?.quote : primary?.reason || "已保存资料尚不能建立主线索"}</span>}
      {!!resource.error && <button type="button" data-action="secondary" disabled={resource.pending} onClick={resource.refresh}>重读</button>}
      {data && <button type="button" data-action="secondary" className="shrink-0" aria-label="查看主线索完整依据" aria-haspopup="dialog" onClick={() => setOpen(true)}>核对全文</button>}
    </div>
    <CompanyStatusEvidence value={data?.company_status} symbol={symbol} now={companyNow} showFacts={false} responseAsOf={statusAsOf} compact/>
    {companyItems.length > 0 && <p className="py-1 text-zinc-600 dark:text-zinc-400">{companyLabel} · {companyItems.length} 条，可在“核对全文”中读取；不替换主线索。</p>}
    {presence.value === identity && <ModalShell presentation="drawer" open={presence.active} onClose={() => setOpen(false)} label={title} size="md" zIndex={60} header={<h2 className="text-sm font-medium">{title}</h2>} bodyTabIndex={0} bodyClassName="data-scroll overflow-y-auto px-4 py-3 text-xs [overflow-wrap:anywhere]">
      {presence.active && <><button type="button" data-action="secondary" disabled={resource.pending} onClick={resource.refresh}>刷新依据</button>{coverage}{companyStatusReading}{data?.response_context && <p className="py-2 text-zinc-600 dark:text-zinc-400">响应时点 {timeLabel(data.response_context.response_as_of)} · 当前解释，非历史还原。</p>}{primaryReading || <><p className="py-2">主线索未记录，不能选第一条资料冒充原因。</p>{data && (otherItems.length > 0 || otherLater.length > 0) && <details><summary className="cursor-pointer">其他关联资料 · {otherItems.length + otherLater.length} 条</summary><AttributionItems items={otherItems} laterItems={otherLater} responseContext={!!data.response_context} symbol={symbol} selectedAt={selectedAt} recordedRefs={recordedRefs} selectionContext={selectionContext} defaultSourceExpanded /></details>}</>}{companyReading}<p className="py-2 text-zinc-600 dark:text-zinc-400">来源解释供核对，不构成买卖建议。</p></>}
    </ModalShell>}
  </section>;
  return <section className="min-w-0 py-1 text-xs" aria-label={title}>
    <div className="flex min-h-8 flex-wrap items-center justify-between gap-2"><button type="button" data-action="quiet" className={`quiet-action min-w-0 text-left ${styles.action} ${styles.panelToggle}`} aria-expanded={open} aria-controls={id} onClick={() => {if (!open) setReadingCycle(value => value + 1); setOpen(value => !value);}}><span>{title}</span><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={`ml-1 shrink-0 transition-transform duration-150 motion-reduce:transition-none ${open ? "rotate-180" : ""}`}><path d="m6 9 6 6 6-6" /></svg></button>{open && <button type="button" data-action="quiet" className={`quiet-action shrink-0 ${styles.action}`} disabled={resource.pending} onClick={resource.refresh}>{resource.pending && data ? "更新中" : "刷新依据"}</button>}</div>
    <div className="motion-disclosure" data-open={open} aria-hidden={!open || undefined} inert={!open}><div className="motion-disclosure-body">
    {presence.value === identity && <div data-attribution-reader={bounded || undefined} id={id} className={`min-w-0 ${bounded ? "data-scroll max-h-72 overflow-y-auto pr-1" : ""}`}>
      <p className="py-1 leading-relaxed text-zinc-600 dark:text-zinc-400">已采集消息可逐条查看；不改写首次入选记录。不构成买卖建议。</p>
      {coverage}
      {companyStatusReading}
      {!!recordedRefs?.length && <details className="py-1 text-zinc-600 dark:text-zinc-400"><summary className="w-fit cursor-pointer">{selectionContext ? "选股记录事件引用" : "列表入口事件引用"}</summary><ul className="mt-1 space-y-1 [overflow-wrap:anywhere]">{recordedRefs.map((ref, index) => <li key={`${ref.event_id}:${ref.version_id}:${index}`}>事件 #{ref.event_id} · {ref.version_id ? `原解释 #${ref.version_id}` : "原版本未知"} · {timeLabel(ref.available_at)}</li>)}</ul></details>}
      {resource.pending && !data && <div role="status" aria-label="归因依据加载中" className="space-y-2 py-3"><Skeleton className="h-3 w-3/4" /><Skeleton className="h-3 w-full" /><span className="sr-only">归因依据加载中</span></div>}
      {resource.error != null && <p role="alert" className="py-2 leading-relaxed text-amber-800 dark:text-amber-300">{data ? "依据刷新失败，保留上次读取内容。" : "归因依据读取失败，尚不能确认是否有相关消息。"}可使用上方“刷新依据”重试。</p>}
      {data && readItems.length === 0 && <p className="py-3 leading-relaxed text-zinc-600 dark:text-zinc-400">已采集资料中暂未找到关联事件。覆盖不等于全市场，不能据此认定没有驱动消息。</p>}
      {data?.response_context && <p className="py-1 leading-relaxed text-zinc-600 dark:text-zinc-400">所查响应时点：{timeLabel(data.response_context.response_as_of)}。按已保存的来源时点分组；当前解释，非历史还原。晚到或时点未知资料不回填驱动与入选依据。</p>}
      {primaryReading || (data && <AttributionItems laterItems={data.later_items} responseContext={!!data.response_context} key={`${identity}:${readingCycle}`} items={data.items} symbol={symbol} selectedAt={selectedAt} recordedRefs={recordedRefs} selectionContext={selectionContext} defaultSourceExpanded={defaultSourceExpanded} />)}
      {companyReading}
    </div>}
    </div></div>
  </section>;
});
