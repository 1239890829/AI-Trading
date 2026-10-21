"use client";

import {useId, useState, type KeyboardEvent, type ReactNode} from "react";
import type {CompanyImpact, SelectionMetrics} from "@/lib/api";
import {dateTimeTextBJ} from "@/lib/format";
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
  const item = value.items.find(item => item.key === selected) ?? value.items[0];
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
      <strong>{item.label} · {item.state === "ready" ? metricValue(item.value, item.unit) : "原测量待核"}</strong><p>{item.basis || "原记录未保存该项具体依据。"}</p>
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
    {summary.measurements?.items.length ? <SelectionMeasurements value={summary.measurements}/> : <SelectionScores scores={scores} onSelect={setScoreKey}/>}
    <section aria-label="入选与事件核对" className={styles.eventSection}>
      <div className={styles.heading}><h3>事件与这只股票</h3><span>{summary.historical ? "原提醒时点" : "当前补充资料"} · {originalClock}</span></div>
      {events.length ? <div role="tablist" aria-label="关联事件选择" className={styles.events}>{events.map((item, index) => <button key={item.id} type="button" role="tab" id={`${uid}-event-${index}`} aria-controls={`${uid}-detail`} aria-selected={event?.id === item.id} tabIndex={event?.id === item.id ? 0 : -1} className={styles.event} data-active={event?.id === item.id} onMouseEnter={() => setEventKey(item.id)} onFocus={() => setEventKey(item.id)} onClick={() => setEventKey(item.id)} onKeyDown={key => chooseEvent(key, index)}>
        <strong>{item.title}</strong><span>{item.relation}</span><small>{item.availableAt}</small>
      </button>)}</div> : <p className={styles.empty}>未找到符合所查时点与版本的可核对事件；没有用其他消息填补。</p>}
      <div className={styles.columns} data-event-id={event?.id} id={`${uid}-detail`} role={event ? "tabpanel" : undefined} aria-labelledby={event ? `${uid}-event-${events.indexOf(event)}` : undefined}>
        <EvidenceColumn title="公司联系" icon="fact" active={score?.key === "fundamental"}>
          {facts.length ? <><p className={styles.state}>传导引用了 {facts.length} 条公司原句</p><div className={styles.sourceText} tabIndex={0} aria-label="当前事件公司原句">{facts.map(fact => <blockquote key={fact.id}><p>{fact.quote}</p><cite>{fact.document_title} · 第 {fact.page} 页</cite><a href={fact.url} target="_blank" rel="noopener noreferrer" data-action="quiet">查看披露原文</a></blockquote>)}</div></> : <><p className={styles.state}>{event?.status || "公司关系待核"}</p><div className={styles.sourceText}>{fallback}</div></>}
        </EvidenceColumn>
        <EvidenceColumn title="可能影响" icon="impact" active={score?.key === "news"}>
          <p className={styles.state}>{company ? {conditional: "有条件推演", mixed: "双向影响待核", insufficient: "证据不够", no_supported_link: "暂无直接传导支持"}[company.analysis.status] : "尚不能建立具体经营传导"}</p>
          <div className={styles.sourceText} tabIndex={0} aria-label="当前事件影响解释">{company ? <><p>{company.analysis.summary}</p><p className={styles.note}>这是研究假设，尚未验证经营效果。</p></> : <p>缺少同股、同版本的事件和公司依据，不补写客户、订单或利润结论。</p>}</div>
        </EvidenceColumn>
        <EvidenceColumn title="反证与缺项" icon="risk">
          <p className={styles.state}>{company ? `${company.analysis.missing.length} 项缺口 · ${company.analysis.counterarguments.length} 条反证` : "证据边界未闭合"}</p>
          <div className={styles.sourceText} tabIndex={0} aria-label="当前事件反证与缺项"><ul>{company ? [...company.analysis.counterarguments, ...company.analysis.missing].map((value, index) => <li key={index}>{value}</li>) : <li>{event?.gap || "尚无可读取的本股经营证据；不代表公司没有相关业务。"}</li>}</ul></div>
        </EvidenceColumn>
      </div>
      {event?.temporalNote && <p className={styles.note}>{event.temporalNote}</p>}
      {event?.source && <section className={styles.eventSource} aria-label="当前事件原句"><strong>事件原句</strong><p className={styles.sourceText} tabIndex={0}>{event.source.quote}</p>{event.source.url && <a href={event.source.url} target="_blank" rel="noopener noreferrer" data-action="quiet">查看消息来源</a>}</section>}
    </section>
    <section aria-label="原选择核对" className={styles.original}>
      <div className={styles.heading}><h3>{summary.reasonRecorded ? "原选择记录" : "原理由未单列"}</h3><button type="button" aria-expanded={showOriginal} onClick={() => setShowOriginal(value => !value)}>{showOriginal ? "收起原选择" : "核对原选择"}</button></div>
      <p className={styles.note}>原版本：{summary.version || "未记录"}。完整理由与全部条件可在本区核对。</p>
      {showOriginal && <div className={styles.originalText}><p>{summary.reason}</p><p>{summary.condition}</p>{summary.supports?.map((support, index) => <p key={index}><strong>{support.label}：</strong>{support.value}</p>)}<p>原版本：{summary.version || "未记录"}。公司补充资料不改写原选择。</p></div>}
    </section>
    {children}
  </section>;
}
