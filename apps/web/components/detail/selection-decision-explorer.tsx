"use client";

import {useId, useState, type KeyboardEvent, type ReactNode} from "react";
import type {CompanyImpact, SelectionMetrics} from "@/lib/api";
import {dateTimeTextBJ} from "@/lib/format";
import {researchExcerpt} from "@/lib/research-presentation";
import styles from "./selection-decision-explorer.module.css";

export interface SelectionScore {
  key: string; label: string; value: number | null; maximum: 100;
  basis: string; state: string;
}
/** Existing picks engine's six 0-100 sub-scores; never probabilities. */
export const SELECTION_SCORE_LABELS = [["sentiment", "情绪"], ["news", "消息"], ["tech", "技术"], ["fundamental", "基本"], ["capital", "资金"], ["echelon", "梯队"]] as const;
export interface SelectionSummary {
  reason: string; condition: string; supports?: {label: string; value: string}[];
  scores?: SelectionScore[]; reasonRecorded?: boolean; historical?: boolean; version?: string | null;
  measurements?: SelectionMetrics | null;
}

/** Both live cards and archived notifications accept only this producer's known units. */
export function readSelectionMetrics(raw: unknown): SelectionMetrics | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const value = raw as Partial<SelectionMetrics>;
  if (value.schema_version !== 1 || value.producer !== "linkage_candidates" || !Array.isArray(value.items) || value.items.length > 5) return null;
  const units = {change_pct: "%", amount: "元", theme_limit_ups: "家", theme_share: "%", limit_up_gap_pct: "%"};
  const seen = new Set<string>();
  const clock = (value: unknown) => typeof value === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)) ? value : null;
  const items: SelectionMetrics["items"] = [];
  for (const item of value.items) {
    if (!item || !Object.hasOwn(units, item.key) || item.unit !== units[item.key] || seen.has(item.key)
      || typeof item.label !== "string" || !item.label.trim() || item.label.length > 80
      || typeof item.basis !== "string" || item.basis.length > 4000 || !["ready", "missing", "unknown"].includes(item.state)
      || !Array.isArray(item.thresholds) || item.thresholds.length > 8
      || item.thresholds.some(threshold => !threshold || typeof threshold.key !== "string" || typeof threshold.label !== "string"
        || !Number.isFinite(threshold.value) || ![">=", "<"].includes(threshold.operator))) return null;
    seen.add(item.key);
    const number = typeof item.value === "number" && Number.isFinite(item.value) ? item.value : null;
    const valid = number !== null && (item.key === "change_pct" || number >= 0)
      && (item.key !== "theme_limit_ups" || Number.isInteger(number)) && (item.key !== "theme_share" || number <= 100);
    items.push({...item, value: valid ? number : null, state: valid ? item.state : "unknown",
      source: typeof item.source === "string" ? item.source.slice(0, 120) : null,
      as_of: clock(item.as_of), received_at: clock(item.received_at)});
  }
  return {schema_version: 1, producer: "linkage_candidates", snapshot_version: typeof value.snapshot_version === "string" ? value.snapshot_version : null, items};
}

function metricValue(value: number | null, unit: string) {
  return value === null ? "未记录" : `${new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 2}).format(value)}${unit}`;
}

export function SelectionMeasurements({value, compact = false}: {value: SelectionMetrics; compact?: boolean}) {
  const [selected, setSelected] = useState(value.items[0]?.key);
  const [thresholdKey, setThresholdKey] = useState("");
  const item = value.items.find(item => item.key === selected) ?? value.items[0];
  const threshold = item?.thresholds.find(value => value.key === thresholdKey) ?? item?.thresholds[0];
  return <section aria-label={compact ? "盘中关键选择项概览" : "盘中关键选择项"} className={`${styles.scoreSection} ${compact ? styles.compact : ""}`}>
    <div className={styles.heading}><h3>盘中关键选择项</h3><span>{compact ? "原测量，非AI评分" : "真实量纲与原门槛，不是六维评分或AI分"}</span></div>
    <div className={styles.scores}>{value.items.map(metric => {
      const measured = metric.state === "ready" && metric.value !== null;
      const min = Math.min(0, metric.value ?? 0, ...metric.thresholds.map(threshold => threshold.value));
      const max = Math.max(0, metric.value ?? 0, ...metric.thresholds.map(threshold => threshold.value));
      const position = (number: number) => `${(number - min) / (max - min) * 100}%`;
      return <button key={metric.key} type="button" className={styles.score} data-active={item?.key === metric.key} aria-pressed={item?.key === metric.key}
        aria-label={`核对${metric.label}原测量`} onMouseEnter={() => setSelected(metric.key)} onFocus={() => setSelected(metric.key)} onClick={() => setSelected(metric.key)}>
        <span className={styles.scoreTop}><strong>{metric.label}</strong><span>{measured ? metricValue(metric.value, metric.unit) : "测量待核"}</span></span>
        <span className={styles.measureTrack} {...(measured && max > min ? {role: "meter", "aria-label": `${metric.label}原测量刻度`, "aria-valuemin": min, "aria-valuemax": max, "aria-valuenow": metric.value!, "aria-valuetext": metricValue(metric.value, metric.unit)} : {})}>
          {measured && max > min && <span className={styles.measureFill} style={{width: position(metric.value!)}}/>}
          {max > min && metric.thresholds.map(threshold => <span key={threshold.key} className={styles.threshold} style={{left: position(threshold.value)}} aria-hidden="true"/>)}</span>
        <span className={styles.metricThresholds}>{metric.thresholds.map(threshold => `${threshold.label} ${threshold.operator === ">=" ? "≥" : "<"}${metricValue(threshold.value, metric.unit)}`).join(" · ") || "原记录未设数值门槛"}</span>
      </button>;
    })}</div>
    {item && <section aria-label="当前原测量依据" className={styles.scoreEvidence} aria-live="polite" tabIndex={0}>
      <strong>{item.label} · {item.state === "ready" ? metricValue(item.value, item.unit) : "原测量待核"}</strong><p><span className={styles.note}>摘录：</span>{researchExcerpt(item.basis || "原记录未保存该项具体依据。")}</p>
      {item.basis.trim() && <details key={JSON.stringify([value.snapshot_version, item.key, item.basis])} className={styles.metricBasis}>
        <summary>核对{item.label}完整依据</summary>
        <div role="region" aria-label={`${item.label}完整原测量依据`} tabIndex={0}>{item.basis}</div>
      </details>}
      {!compact && item.thresholds.length > 0 && <><div className={styles.thresholdChoices} role="group" aria-label="选择原门槛对照">{item.thresholds.map(value => <button type="button" key={value.key} aria-pressed={threshold?.key === value.key} onClick={() => setThresholdKey(value.key)}>{value.label}</button>)}</div>
        {threshold && <div className={styles.thresholdComparison} aria-label="当前原门槛对照"><span>原测量 <strong>{metricValue(item.value, item.unit)}</strong></span><span>{threshold.operator === ">=" ? "≥" : "<"}</span><span>{threshold.label} <strong>{metricValue(threshold.value, item.unit)}</strong></span><output>{item.state !== "ready" || item.value === null ? "待核，暂不比较" : (threshold.operator === ">=" ? item.value >= threshold.value : item.value < threshold.value) ? "数值满足" : "数值未满足"}</output></div>}</>}
      {compact && <p>{item.thresholds.map(threshold => `${threshold.label} ${threshold.operator === ">=" ? "≥" : "<"}${metricValue(threshold.value, item.unit)}`).join(" · ") || "原记录未设数值门槛"}</p>}
      {!compact && <p>来源：{item.source || "未记录"} · 源时刻：{item.as_of ? dateTimeTextBJ(item.as_of, {seconds: true}) : "未提供"} · 接收时刻：{item.received_at ? dateTimeTextBJ(item.received_at, {seconds: true}) : "未提供"}</p>}
    </section>}
    <p className={styles.note}>{compact ? "各项量程不同；悬停或聚焦核对原依据，完整来源见入选解读。" : `刻度使用本项原值与门槛，量程不同，不可当作跨项强弱评分。快照版本：${value.snapshot_version || "未记录"}；快照版本不代替源时刻。`}</p>
  </section>;
}

export function SelectionScores({scores, compact = false, onSelect}: {scores: SelectionScore[]; compact?: boolean; onSelect?: (key: string) => void}) {
  const [selected, setSelected] = useState(scores[0]?.key ?? "");
  const score = scores.find(value => value.key === selected) ?? scores[0];
  const choose = (key: string) => {setSelected(key); onSelect?.(key);};
  return <section aria-label={compact ? "入选关键评分概览" : "关键分项评分"} className={`${styles.scoreSection} ${compact ? styles.compact : ""}`}>
    <div className={styles.heading}><h3>关键分项</h3><span>原规则分值，不是上涨概率</span></div>
    {scores.length ? <>
      <div className={styles.scores}>{scores.map(item => {
        const valid = typeof item.value === "number" && Number.isFinite(item.value) && item.value >= 0 && item.value <= item.maximum;
        const missing = ["missing", "error"].includes(item.state);
        const measured = valid && ["success", "valid_empty", "partial"].includes(item.state);
        return <button key={item.key} type="button" className={styles.score} data-active={score?.key === item.key} data-state={item.state} aria-pressed={score?.key === item.key}
          aria-label={`核对${item.label}评分依据`} onMouseEnter={() => choose(item.key)} onFocus={() => choose(item.key)} onClick={() => choose(item.key)}>
          <span className={styles.scoreTop}><strong>{item.label}</strong><span>{missing ? "依据缺项" : valid ? `${item.value} / ${item.maximum}` : "未记录"}{item.state === "partial" ? " · 部分" : item.state === "valid_empty" ? " · 有效空集" : !measured && !missing ? " · 待核" : ""}</span></span>
          <span className={styles.track} {...(measured ? {role: "meter", "aria-label": `${item.label}原规则分值`, "aria-valuemin": 0, "aria-valuemax": item.maximum, "aria-valuenow": item.value!} : {"aria-label": `${item.label}无可展示的实测分值`})}>{measured && <span style={{width: `${item.value! / item.maximum * 100}%`}}/>}</span>
        </button>;
      })}</div>
      {score && <section aria-label="当前评分依据" className={styles.scoreEvidence} aria-live="polite" tabIndex={0}><strong>{score.label} · 原规则依据</strong><p>{score.basis || "原记录未保存本分项依据，不能仅凭数字判定可靠。"}</p>{["missing", "error"].includes(score.state) && <p>记录中的 {score.value ?? "未知"} 分为计算占位，未作为实测分值填充。</p>}{score.state === "valid_empty" && <p>有效空集：原来源已成功读取，没有匹配条目；不是读取失败或缺失。</p>}{!["success", "valid_empty", "partial", "missing", "error"].includes(score.state) && <p>该原记录未保存读取状态或状态无效，数字不证明证据完整。</p>}</section>}
    </> : <p className={styles.empty}>原记录未存分项评分，不补算或套用当前分值。</p>}
  </section>;
}
export interface SelectionDecisionEvent {
  id: string; title: string; relation: string; availableAt: string;
  source: {title: string; quote: string; url: string | null} | null;
  company: CompanyImpact | null; status: string; gap: string; temporalNote: string;
}
function Icon({kind}: {kind: "fact" | "impact" | "risk"}) {
  return <svg aria-hidden="true" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{kind === "fact" ? <><path d="M5 3h10l4 4v14H5zM14 3v5h5M8 12h8M8 16h6"/></> : kind === "impact" ? <><circle cx="5" cy="12" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="m7 11 10-4M7 13l10 4"/></> : <><path d="m12 3 10 17H2zM12 9v5M12 17h.01"/></>}</svg>;
}
function EvidenceColumn({title, icon, active, children}: {title: string; icon: "fact" | "impact" | "risk"; active?: boolean; children: ReactNode}) {
  return <section className={styles.column} aria-label={title} data-highlight={active || undefined}><h4><Icon kind={icon}/>{title}</h4>{children}</section>;
}

const FACT_KINDS: Record<string, string> = {business: "主营", product: "产品", customer: "客户", order: "订单", capacity: "产能", financial: "财务"};

function CompanyEvidenceWorkspace({event, facts, fallback, scoreKey}: {event: SelectionDecisionEvent | undefined; facts: CompanyImpact["facts"]; fallback: ReactNode; scoreKey: string | undefined}) {
  const company = event?.company;
  const [factKey, setFactKey] = useState(facts[0]?.id ?? "");
  const [wholeQuote, setWholeQuote] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const [riskView, setRiskView] = useState<"compare" | "counter" | "missing">("compare");
  const [riskIndex, setRiskIndex] = useState(0);
  const fact = facts.find(item => item.id === factKey) ?? facts[0];
  const quoteExcerpt = fact ? researchExcerpt(fact.quote, 150) : "";
  const step = company?.analysis.chain[stepIndex] ?? company?.analysis.chain[0];
  const riskItems = riskView === "counter" ? company?.analysis.counterarguments ?? [] : company?.analysis.missing ?? [];
  function chooseStep(index: number) {
    setStepIndex(index);
    setWholeQuote(false);
    const cited = company?.analysis.chain[index]?.evidence_ids.find(id => facts.some(item => item.id === id));
    if (cited) setFactKey(cited);
  }
  return <div className={styles.columns}>
    <EvidenceColumn title="公司联系" icon="fact" active={scoreKey === "fundamental"}>
      {fact ? <>
        <div role="group" aria-label="选择公司依据原句" className={styles.evidenceLocator}>{facts.map((item, index) => <button key={item.id} type="button" aria-pressed={fact.id === item.id} onClick={() => {setFactKey(item.id); setWholeQuote(false);}} title={item.document_title}><span>{FACT_KINDS[item.kind] ?? "披露"}</span><small>{index + 1}</small></button>)}</div>
        <div className={styles.sourceText} tabIndex={0} aria-label="当前事件公司原句" data-evidence-id={fact.id}><blockquote><p>{wholeQuote ? fact.quote : quoteExcerpt === "这段原答较长，请在原文中核对完整表述。" ? "披露原句较长，可核对整段。" : quoteExcerpt}</p><cite>{fact.document_title} · 第 {fact.page} 页</cite>{quoteExcerpt !== fact.quote.trim() && <button type="button" className={styles.quoteToggle} aria-expanded={wholeQuote} onClick={() => setWholeQuote(value => !value)}>{wholeQuote ? "返回原句摘录" : "核对整段原句"}</button>}<a href={fact.url} target="_blank" rel="noopener noreferrer" data-action="quiet">查看披露原文</a></blockquote></div>
        <p className={styles.note}>传导引用了 {facts.length} 条公司原句；点击依据可逐条核对。</p>
      </> : <><p className={styles.state}>{event?.status || "公司关系待核"}</p><div className={styles.sourceText}>{fallback}</div></>}
    </EvidenceColumn>
    <EvidenceColumn title="可能影响" icon="impact" active={scoreKey === "news"}>
      <p className={styles.state}>{company ? {conditional: "有条件推演", mixed: "双向影响待核", insufficient: "证据不够", no_supported_link: "暂无直接传导支持"}[company.analysis.status] : "尚不能建立具体经营传导"}</p>
      <div className={styles.sourceText} tabIndex={0} aria-label="当前事件影响解释">{company ? <p>{researchExcerpt(company.analysis.summary)}</p> : <p>缺少同股、同版本的事件和公司依据，不补写客户、订单或利润结论。</p>}</div>
      {!!company?.analysis.chain.length && <><div className={styles.chainChoices} role="group" aria-label="传导核对项">{company.analysis.chain.map((item, index) => <button type="button" key={index} aria-pressed={stepIndex === index} aria-label={`核对传导第${index + 1}条`} onClick={() => chooseStep(index)}><span>{index + 1}</span><small>{item.evidence_ids.length} 条引用</small></button>)}</div>
        {step && <div className={styles.chainDetail} aria-label="当前传导条件"><p>{researchExcerpt(step.text)}</p><div role="group" aria-label="定位传导引用">{step.evidence_ids.map(id => {
          const citation = facts.find(item => item.id === id);
          return citation ? <button type="button" key={id} aria-pressed={citation.id === fact?.id} onClick={() => {setFactKey(id); setWholeQuote(false);}}>定位{FACT_KINDS[citation.kind] ?? "公司"}原句</button> : <span key={id}>{id.startsWith("event:") ? "引用事件原句" : "引用原句未提供"}</span>;
        })}</div></div>}</>}
      {company && <p className={styles.note}>经营效果未验证。</p>}
    </EvidenceColumn>
    <EvidenceColumn title="反证与缺项" icon="risk">
      {company ? <><div role="group" aria-label="反证缺项核对方式" className={styles.riskModes}>{([['compare', '并排对照'], ['counter', `反证 ${company.analysis.counterarguments.length}`], ['missing', `缺项 ${company.analysis.missing.length}`]] as const).map(([key, label]) => <button type="button" key={key} aria-pressed={riskView === key} onClick={() => {setRiskView(key); setRiskIndex(0);}}>{label}</button>)}</div>
        <div aria-label="当前事件反证与缺项" className={styles.riskContent}>
          {riskView === "compare" ? <div className={styles.riskCompare}><div><strong>不能据此推出</strong><p>{researchExcerpt(company.analysis.counterarguments[0] ?? "原记录未单列反证，不代表不存在风险。")}</p></div><div><strong>还缺哪条证据</strong><p>{researchExcerpt(company.analysis.missing[0] ?? "原记录未单列缺项，不代表证据已完整。")}</p></div></div> : <><div className={styles.riskNumbers} role="group" aria-label={riskView === "counter" ? "选择反证" : "选择缺项"}>{riskItems.map((_, index) => <button type="button" key={index} aria-pressed={riskIndex === index} onClick={() => setRiskIndex(index)}>{index + 1}</button>)}</div><p>{researchExcerpt(riskItems[riskIndex] ?? "原记录未提供该项。")}</p></>}
        </div></> : <div className={styles.sourceText} tabIndex={0} aria-label="当前事件反证与缺项"><p>{event?.gap || "尚无可读取的本股经营证据；不代表公司没有相关业务。"}</p></div>}
    </EvidenceColumn>
  </div>;
}

/** A read-only explorer of saved scores and exact event/company bindings. */
export function SelectionDecisionExplorer({summary, events, fallback, originalClock, children}: {
  summary: SelectionSummary; events: SelectionDecisionEvent[]; fallback: ReactNode; originalClock: string; children?: ReactNode;
}) {
  const uid = useId();
  const [scoreKey, setScoreKey] = useState(summary.scores?.[0]?.key ?? "");
  const scores = summary.scores ?? [];
  const score = scores.find(value => value.key === scoreKey) ?? scores[0];
  const [eventKey, setEventKey] = useState(events[0]?.id ?? "");
  const event = events.find(value => value.id === eventKey) ?? events[0];
  const company = event?.company;
  const cited = new Set(company?.analysis.chain.flatMap(step => step.evidence_ids) ?? []);
  const facts = company?.facts.filter(fact => cited.has(fact.id)) ?? [];
  const [showOriginal, setShowOriginal] = useState(false);
  const [originalView, setOriginalView] = useState<"reason" | "condition">("reason");
  function chooseEvent(key: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (key.nativeEvent.isComposing || key.keyCode === 229 || !["ArrowRight", "ArrowLeft", "Home", "End"].includes(key.key)) return;
    key.preventDefault(); key.stopPropagation();
    const next = key.key === "Home" ? 0 : key.key === "End" ? events.length - 1 : (index + (key.key === "ArrowRight" ? 1 : -1) + events.length) % events.length;
    setEventKey(events[next].id);
    const rail = key.currentTarget.parentElement;
    const button = rail?.querySelectorAll<HTMLButtonElement>("button")[next];
    button?.focus({preventScroll: true});
    if (rail && button) {
      const viewport = rail.getBoundingClientRect(), target = button.getBoundingClientRect();
      const delta = target.left < viewport.left ? target.left - viewport.left : target.right > viewport.right ? target.right - viewport.right : 0;
      // Move this horizontal rail only; do not scroll the enclosing stock/notification drawer.
      rail.scrollLeft = Math.max(0, Math.min(rail.scrollWidth - rail.clientWidth, rail.scrollLeft + delta));
    }
  }
  return <section aria-label="入选决策核对台" className={styles.explorer}>
    {(summary.measurements?.items.length ? <SelectionMeasurements value={summary.measurements}/> : <SelectionScores scores={scores} onSelect={setScoreKey}/>)}
    <section aria-label="入选与事件核对" className={styles.eventSection}>
      <div className={styles.heading}><h3>事件与这只股票</h3><span>{summary.historical ? "原提醒时点" : "当前补充资料"} · {originalClock}</span></div>
      {events.length ? <div role="tablist" aria-label="关联事件选择" className={styles.events}>{events.map((item, index) => <button key={item.id} type="button" role="tab" id={`${uid}-event-${index}`} aria-controls={`${uid}-detail`} aria-selected={event?.id === item.id} tabIndex={event?.id === item.id ? 0 : -1} className={styles.event} data-active={event?.id === item.id} onMouseEnter={() => setEventKey(item.id)} onFocus={() => setEventKey(item.id)} onClick={() => setEventKey(item.id)} onKeyDown={key => chooseEvent(key, index)}>
        <strong>{item.title}</strong><span>{item.relation}</span><small>{item.availableAt}</small>
      </button>)}</div> : <p className={styles.empty}>未找到符合所查时点与版本的可核对事件；没有用其他消息填补。</p>}
      <div data-event-id={event?.id} id={`${uid}-detail`} role={event ? "tabpanel" : undefined} aria-labelledby={event ? `${uid}-event-${events.indexOf(event)}` : undefined}>
        <CompanyEvidenceWorkspace key={event?.id ?? "no-event"} event={event} facts={facts} fallback={fallback} scoreKey={score?.key}/>
      </div>
      {event?.temporalNote && <p className={styles.note}>{event.temporalNote}</p>}
      {event?.source && <section className={styles.eventSource} aria-label="当前事件原句"><strong>事件原句</strong><p className={styles.sourceText} tabIndex={0}>{event.source.quote}</p>{event.source.url && <a href={event.source.url} target="_blank" rel="noopener noreferrer" data-action="quiet">查看消息来源</a>}</section>}
    </section>
    <section aria-label="原选择核对" className={styles.original}>
      <div className={styles.heading}><h3>{summary.reasonRecorded ? "原选择记录" : "原理由未单列"}</h3><button type="button" aria-expanded={showOriginal} onClick={() => setShowOriginal(value => !value)}>{showOriginal ? "收起原选择" : "核对原选择"}</button></div>
      <div className={styles.originalChoices} role="group" aria-label="原选择核对视角"><button type="button" aria-pressed={originalView === "reason"} onClick={() => setOriginalView("reason")}>为何入选</button><button type="button" aria-pressed={originalView === "condition"} onClick={() => setOriginalView("condition")}>失效条件</button></div>
      <p aria-label="当前原选择摘录" className={styles.originalExcerpt}>{researchExcerpt(originalView === "reason" ? summary.reason : summary.condition)}</p>
      <p className={styles.note}>原版本：{summary.version || "未记录"}。完整理由与全部条件可在本区核对。</p>
      {showOriginal && <div className={styles.originalText}><p>{summary.reason}</p><p>{summary.condition}</p>{summary.supports?.map((support, index) => <p key={index}><strong>{support.label}：</strong>{support.value}</p>)}<p>原版本：{summary.version || "未记录"}。公司补充资料不改写原选择。</p></div>}
    </section>
    {children}
  </section>;
}
