"use client";

import {useId, useState, type KeyboardEvent} from "react";
import type {TradingAgentsResearchReport, TradingAgentsReportKey} from "@/lib/api/agent";
import {originalRating, researchExcerpt, researchField} from "@/lib/research-presentation";
import styles from "./tradingagents-report-explorer.module.css";

const REPORTS: {key: TradingAgentsReportKey; label: string; group: string}[] = [
  {key: "market_report", label: "行情", group: "分析"}, {key: "sentiment_report", label: "情绪", group: "分析"},
  {key: "news_report", label: "新闻", group: "分析"}, {key: "fundamentals_report", label: "公司", group: "分析"},
  {key: "investment_plan", label: "多空研究", group: "交叉审视"}, {key: "trader_investment_plan", label: "交易研究", group: "交叉审视"},
  {key: "final_trade_decision", label: "最终判断", group: "最终"},
];

/** Seven saved reports become a comparison workspace; no model or new verdict. */
export function TradingAgentsReportExplorer({report}: {report: TradingAgentsResearchReport}) {
  const id = useId();
  const [selected, setSelected] = useState<TradingAgentsReportKey>("final_trade_decision");
  const [compare, setCompare] = useState(false);
  const [peer, setPeer] = useState<TradingAgentsReportKey>("fundamentals_report");
  const [raw, setRaw] = useState(false);
  const current = REPORTS.find(item => item.key === selected)!;
  const compareKey = peer === selected ? (selected === "fundamentals_report" ? "final_trade_decision" : "fundamentals_report") : peer;
  const rating = originalRating(report.reports.final_trade_decision);
  const excerpt = (key: TradingAgentsReportKey) => researchExcerpt(researchField(report.reports[key], "Executive Summary") ?? report.reports[key]);
  function choose(index: number) {setSelected(REPORTS[index].key); setRaw(false);}
  function keyboard(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.nativeEvent.isComposing || event.keyCode === 229 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    const next = event.key === "Home" ? 0 : event.key === "End" ? REPORTS.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + REPORTS.length) % REPORTS.length;
    choose(next);
    const rail = event.currentTarget.parentElement;
    const button = rail?.querySelectorAll<HTMLButtonElement>("button")[next];
    button?.focus({preventScroll: true});
    if (rail && button) {
      const viewport = rail.getBoundingClientRect(), target = button.getBoundingClientRect();
      const delta = target.left < viewport.left ? target.left - viewport.left : target.right > viewport.right ? target.right - viewport.right : 0;
      rail.scrollLeft = Math.max(0, Math.min(rail.scrollWidth - rail.clientWidth, rail.scrollLeft + delta));
    }
  }
  return <section className={styles.reader} aria-label="完整研究速览">
    <div className={styles.summary}>
      <div><strong>{rating?.label ?? "研究原答已保存"}</strong><span>{rating ? `原评级 ${rating.original}` : "没有新增评级"}</span></div>
      <div className={styles.quality}><span>{REPORTS.filter(item => report.reports[item.key]?.trim()).length} / 7 份已保存</span><span>来源待核 · 非买卖指令</span></div>
    </div>
    <div role="tablist" aria-label="研究视角" className={styles.rail}>{REPORTS.map((item, index) => <button key={item.key} type="button" role="tab" id={`${id}-${item.key}`} aria-controls={`${id}-report`} aria-selected={selected === item.key} tabIndex={selected === item.key ? 0 : -1} onClick={() => choose(index)} onKeyDown={event => keyboard(event, index)}>
      <span className={styles.dot} aria-hidden="true"/><span>{item.label}</span><small>{item.group}</small>
    </button>)}</div>
    <div className={styles.toolbar}><span>原答摘录 · {current.label}</span><button type="button" aria-pressed={compare} onClick={() => {setCompare(value => !value); setRaw(false);}}>{compare ? "结束对照" : "并排对照"}</button></div>
    <div className={styles.reading} data-compare={compare || undefined}>
      <section role="tabpanel" id={`${id}-report`} aria-labelledby={`${id}-${selected}`} className={styles.excerpt} tabIndex={0}>
        <h4>{current.label}</h4><p>{excerpt(selected)}</p>
      </section>
      {compare && <section className={styles.excerpt} aria-label="对照报告摘录">
        <label htmlFor={`${id}-peer`}>对照哪份原答</label><select id={`${id}-peer`} value={compareKey} onChange={event => setPeer(event.target.value as TradingAgentsReportKey)}>{REPORTS.filter(item => item.key !== selected).map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select>
        <p>{excerpt(compareKey)}</p>
      </section>}
    </div>
    <div className={styles.boundary}><span>研究倾向</span><span>经营效果未验证</span><span>账户条件未核实</span></div>
    <button type="button" className={styles.rawButton} aria-expanded={raw} aria-controls={`${id}-raw`} onClick={() => setRaw(value => !value)}>{raw ? "返回速览" : `核对${current.label}完整原文`}</button>
    {raw && <section id={`${id}-raw`} className={styles.original} aria-label={`${current.label}完整原文`} tabIndex={0}><p>原始报告，含引用标记；未独立验收。</p><pre>{report.reports[selected]}</pre></section>}
  </section>;
}
