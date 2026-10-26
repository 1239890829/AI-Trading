"use client";

import {useId, useState, type KeyboardEvent, type ReactNode} from "react";
import type {TradingAgentsResearchReport, TradingAgentsRoleName, TradingAgentsRoleOutput} from "@/lib/api/agent";
import {originalRating, researchLead, researchPassages, researchSentences, researchTable} from "@/lib/research-presentation";
import styles from "./tradingagents-role-reader.module.css";

const GROUPS = ["四位分析", "多空辩论", "交易规划", "风险复核"];
const ROLES: {name: TradingAgentsRoleName; label: string; group: number; path: string}[] = [
  {name:"Market Analyst",label:"行情",group:0,path:"M3 3v18h18M6 15l4-5 4 3 6-8"},
  {name:"Sentiment Analyst",label:"情绪",group:0,path:"M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M8 9h.01M16 9h.01M7 14q5 5 10 0"},
  {name:"News Analyst",label:"消息",group:0,path:"M5 3h14v18H5zM8 7h8M8 11h8M8 15h4M8 18h8"},
  {name:"Fundamentals Analyst",label:"公司",group:0,path:"M4 21V7l8-4 8 4v14M8 10v2M12 10v2M16 10v2M8 15v2M16 15v2M10 21v-4h4v4"},
  {name:"Bull Researcher",label:"多方",group:1,path:"M3 17l6-6 4 3 8-10M14 4h7v7"},
  {name:"Bear Researcher",label:"空方",group:1,path:"M3 7l6 6 4-3 8 10M14 20h7v-7"},
  {name:"Research Manager",label:"研究统筹",group:1,path:"M12 3v5M5 20v-5h14v5M12 8v12M9 8h6v5H9zM2 20h6M9 20h6M16 20h6"},
  {name:"Trader",label:"交易研究",group:2,path:"M3 8h17l-4-4M21 16H4l4 4M6 5v6M18 13v6"},
  {name:"Aggressive Analyst",label:"积极检验",group:3,path:"M13 2 4 14h7l-1 8 10-13h-7z"},
  {name:"Conservative Analyst",label:"保守检验",group:3,path:"m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6zM8 12l3 3 5-6"},
  {name:"Neutral Analyst",label:"中性检验",group:3,path:"M12 3v18M7 21h10M3 7h18M6 7l-4 8h8L6 7M18 7l-4 8h8l-4-8"},
  {name:"Portfolio Manager",label:"最终研究",group:3,path:"M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 17l3 3 5-6"},
];
const LABELS: Record<string,string> = {recommendation:"研究倾向",action:"原研究动作",rating:"原评级",time_horizon:"观察周期",overall_band:"情绪原判断",confidence:"原答置信描述",entry_price:"原研究参考价",position_sizing:"原研究仓位描述",stop_loss:"原研究失效条件"};

function shortSource(role: TradingAgentsRoleOutput): string {
  const raw = ["executive_summary","rationale","narrative","reasoning","content","investment_thesis"]
    .map(key=>role.fields[key]).find(value=>typeof value==="string" && value.trim());
  if (typeof raw!=="string") return "原答未提供可独立摘录的短句，请核对本角色原文。";
  return researchLead(raw, 220);
}
function valueText(value: unknown): string | null {
  if (typeof value==="string" && value.trim() && value.length<=100) return value;
  if (typeof value==="number" && Number.isFinite(value)) return String(value);
  return null;
}

const FIELD_LABELS: Record<string,string> = {...LABELS,executive_summary:"结论",investment_thesis:"研究论据",rationale:"判断依据",narrative:"分析原文",reasoning:"推理依据",content:"分析原文",strategic_actions:"后续核验",price_target:"原研究目标价",overall_score:"原情绪分值",tool_calls:"工具提案"};
function OriginalValue({value}: {value: unknown}) {
  if (value === null || value === undefined) return <span>原答未提供</span>;
  if (Array.isArray(value)) return value.length ? <ul>{value.map((item,index)=><li key={index}><OriginalValue value={item}/></li>)}</ul> : <span>原答无条目</span>;
  if (typeof value === "object") return <dl>{Object.entries(value).map(([key,item])=><div key={key}><dt>{FIELD_LABELS[key] ?? key}</dt><dd><OriginalValue value={item}/></dd></div>)}</dl>;
  return <p>{String(value)}</p>;
}

function indexKeyboard(event: KeyboardEvent<HTMLButtonElement>, index: number, count: number, select: (index: number)=>void) {
  if(event.nativeEvent.isComposing || event.keyCode===229 || event.altKey || event.ctrlKey || event.metaKey || !["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;
  event.preventDefault();event.stopPropagation();
  const next=event.key==="Home"?0:event.key==="End"?count-1:(index+(event.key==="ArrowRight"?1:-1)+count)%count;
  select(next);event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus({preventScroll:true});
}

function SourceSentenceFocus({text}: {text: string}) {
  const id=useId();
  const [selected,setSelected]=useState(0);
  const [whole,setWhole]=useState(false);
  const sentences=researchSentences(text);
  if(sentences.length<2)return <><p className={styles.sourceNote}>句界无法安全细分，保留本段完整条件。</p><p className={styles.passageText}>{text}</p></>;
  return <section className={styles.sentenceFocus} aria-label="长段原句定位">
    <div className={styles.passageHeader}><strong>原文定位 · 节选</strong><span>第 {selected+1} / {sentences.length} 句</span></div>
    <p className={styles.sourceNote}>同时保留相邻原句；不能将节选当作独立结论。</p>
    <div className={styles.passageIndex} aria-label="选择原文句子">{sentences.map((sentence,index)=><button key={index} type="button" aria-label={`定位原文第${index+1}句`} title={sentence.trim()} aria-pressed={selected===index} aria-controls={`${id}-focus`} onClick={()=>setSelected(index)} onKeyDown={event=>indexKeyboard(event,index,sentences.length,setSelected)}>{index+1}</button>)}</div>
    <dl id={`${id}-focus`} className={styles.sentenceContext}>{[selected-1,selected,selected+1].filter(index=>index>=0 && index<sentences.length).map(index=><div key={index} data-focused={index===selected || undefined}><dt>{index===selected?"定位原句":index<selected?"上句原文":"下句原文"}</dt><dd>{sentences[index].trim()}</dd></div>)}</dl>
    <button type="button" className={styles.wholePassage} aria-expanded={whole} aria-controls={`${id}-whole`} onClick={()=>setWhole(value=>!value)}>{whole?"收起本段整文":"核对本段完整原文"}</button>
    {whole && <p id={`${id}-whole`} className={styles.passageText} aria-label="本段完整原文">{text}</p>}
  </section>;
}

function SourceTableFocus({text, table}: {text: string; table: NonNullable<ReturnType<typeof researchTable>>}) {
  const id=useId();
  const [selected,setSelected]=useState(0);
  const [whole,setWhole]=useState(false);
  const row=table.rows[selected] ?? table.rows[0];
  return <section className={styles.tableFocus} aria-label="原文表格定位">
    <div className={styles.passageHeader}><strong>原表逐行核对</strong><span>{selected+1} / {table.rows.length} 行</span></div>
    <label htmlFor={`${id}-row`}>{table.headers[0]}</label><select id={`${id}-row`} value={selected} onChange={event=>setSelected(Number(event.target.value))}>{table.rows.map((item,index)=><option value={index} key={index}>{item[0] || `原表第${index+1}行`}</option>)}</select>
    <dl className={styles.tableValues}>{table.headers.slice(1).map((header,index)=><div key={index} data-last={index===table.headers.length-2 || undefined}><dt>{header}</dt><dd>{row[index+1]}</dd></div>)}</dl>
    <p className={styles.sourceNote}>保留原表数值与限制；单位和口径以完整原报告为准。</p>
    <button type="button" className={styles.wholePassage} aria-expanded={whole} aria-controls={`${id}-whole`} onClick={()=>setWhole(value=>!value)}>{whole?"收起本段整文":"核对本段完整原文"}</button>
    {whole && <p id={`${id}-whole`} className={styles.passageText} aria-label="本段完整原文">{text}</p>}
  </section>;
}

function SourcePassageFocus({text}: {text: string}) {
  const table=researchTable(text);
  return table?<SourceTableFocus text={text} table={table}/>:<SourceSentenceFocus text={text}/>;
}

/** A source index and one whole paragraph; it never labels extracted text as verified evidence. */
export function ResearchPassageReader({raw, label}: {raw: string; label: string}) {
  const id=useId();
  const passages=researchPassages(raw);
  const [selected,setSelected]=useState(0);
  const current=passages[selected] ?? passages[0];
  if (!current) return <p>原答没有可读取正文。</p>;
  return <div className={styles.passages} aria-label={`${label}原文索引`}>
    <div className={styles.passageHeader}><strong>逐条核对原文</strong><span>{current.index+1} / {passages.length} 段 · 保留整段条件</span></div>
    <div className={styles.passageIndex} aria-label="选择原文段落">{passages.map(item=><button key={item.index} type="button" aria-label={`原文第${item.index+1}段：${item.title}`} aria-pressed={current.index===item.index} aria-controls={`${id}-passage`} title={item.title} onClick={()=>setSelected(item.index)} onKeyDown={event=>indexKeyboard(event,item.index,passages.length,setSelected)}>{item.index+1}</button>)}</div>
    <div id={`${id}-passage`}>{current.text.length>500?<SourcePassageFocus key={current.index} text={current.text}/>:<p className={styles.passageText}>{current.text}</p>}</div>
    <div className={styles.passageActions}><button type="button" disabled={current.index===0} onClick={()=>setSelected(current.index-1)}>上一段</button><span>原答观点，尚待核验</span><button type="button" disabled={current.index===passages.length-1} onClick={()=>setSelected(current.index+1)}>下一段</button></div>
  </div>;
}

function roleBody(output: TradingAgentsRoleOutput): string {
  const body=["investment_thesis","content","narrative","rationale","reasoning","strategic_actions"]
    .flatMap(key=>typeof output.fields[key]==="string" && (output.fields[key] as string).trim() ? [output.fields[key] as string] : []).join("\n\n");
  return body || (typeof output.fields.executive_summary==="string" ? output.fields.executive_summary : "");
}

/** Explore the twelve saved voices. No synthetic verdict, score or model call. */
export function TradingAgentsRoleReader({report, originals}: {report: TradingAgentsResearchReport; originals: ReactNode}) {
  const id=useId();
  const [selected,setSelected]=useState<TradingAgentsRoleName>("Portfolio Manager");
  const [raw,setRaw]=useState(false);
  const [showReports,setShowReports]=useState(false);
  const [evidence,setEvidence]=useState(false);
  const [compare,setCompare]=useState(false);
  const available=ROLES.filter(role=>report.roles?.some(output=>output.role===role.name));
  if (!available.length) return <>{originals}</>;
  const current=available.find(role=>role.name===selected) ?? available.at(-1)!;
  const output=report.roles!.find(role=>role.role===current.name)!;
  const body=roleBody(output);
  const passageCount=researchPassages(body).length;
  const peers=available.filter(role=>role.group===current.group);
  const counterpart=available.find(role=>role.name===(current.name==="Bull Researcher"?"Bear Researcher":current.name==="Bear Researcher"?"Bull Researcher":current.name==="Aggressive Analyst"?"Conservative Analyst":current.name==="Conservative Analyst"?"Aggressive Analyst":"Bull Researcher"));
  const peerOutput=counterpart && report.roles!.find(role=>role.role===counterpart.name);
  const rating=originalRating(report.reports.final_trade_decision);
  const facts=Object.entries(LABELS).flatMap(([key,label])=>{
    const value=valueText(output.fields[key]);return value===null?[]:[{key,label,value}];
  });
  function select(name: TradingAgentsRoleName) {setSelected(name);setRaw(false);setEvidence(false);setCompare(false);}
  function keyboard(event: KeyboardEvent<HTMLButtonElement>,index:number) {
    if (event.nativeEvent.isComposing || event.keyCode===229 || event.altKey || event.ctrlKey || event.metaKey || !["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    event.preventDefault();event.stopPropagation();
    const next=event.key==="Home"?0:event.key==="End"?peers.length-1:(index+(event.key==="ArrowRight"?1:-1)+peers.length)%peers.length;
    select(peers[next].name);event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus({preventScroll:true});
  }
  return <section className={styles.reader} aria-label="十二角色研究">
    <div className={styles.heading}><div><strong>{rating?.label ?? "完整研究已保存"}</strong><span>{available.length} 个角色原答 · 七份报告 · 待核</span></div>
      <button type="button" aria-pressed={showReports} onClick={()=>setShowReports(value=>!value)}>{showReports?"返回角色":"七份报告"}</button></div>
    {showReports?originals:<>
      <div className={styles.groups} aria-label="研究阶段">{GROUPS.map((label,index)=><button key={label} type="button" aria-pressed={current.group===index} onClick={()=>{const first=available.find(role=>role.group===index);if(first)select(first.name);}} disabled={!available.some(role=>role.group===index)}>{label}<small>{available.filter(role=>role.group===index).length}</small></button>)}</div>
      <div className={styles.roles} role="tablist" aria-label="研究角色">{peers.map((role,index)=><button key={role.name} type="button" role="tab" id={`${id}-${role.name.replaceAll(" ","-")}`} aria-controls={`${id}-content`} aria-selected={current.name===role.name} tabIndex={current.name===role.name?0:-1} onClick={()=>select(role.name)} onKeyDown={event=>keyboard(event,index)} title={role.name}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={role.path}/></svg><span>{role.label}</span>
      </button>)}</div>
      <section className={styles.content} role="tabpanel" id={`${id}-content`} aria-labelledby={`${id}-${current.name.replaceAll(" ","-")}`} tabIndex={0}>
        <div className={styles.identity}><h4>{current.label}</h4><span>原答摘录</span></div>
        <div className={styles.lenses} aria-label="阅读方式"><button type="button" aria-pressed={!evidence} onClick={()=>setEvidence(false)}>结论速读</button><button type="button" disabled={!passageCount} aria-pressed={evidence} onClick={()=>setEvidence(true)}>原文论据 <small>{passageCount}</small></button>{peerOutput && <button type="button" aria-pressed={compare} onClick={()=>setCompare(value=>!value)}>{compare?"结束角色对照":`对照${counterpart.label}`}</button>}</div>
        {evidence ? <ResearchPassageReader key={`${current.name}:evidence`} raw={body} label={current.label}/> : <p className={styles.lead}>{shortSource(output)}</p>}
        {facts.length>0 && <dl className={styles.facts}>{facts.map(fact=><div key={fact.key}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>}
        {compare && peerOutput && <section aria-label={`${current.label}与${counterpart.label}角色对照`} className={styles.comparison}><div><strong>{current.label}</strong><p>{shortSource(output)}</p></div><div><strong>{counterpart.label}</strong><p>{shortSource(peerOutput)}</p><button type="button" onClick={()=>{select(counterpart.name);setEvidence(true);}}>转看{counterpart.label}论据</button></div></section>}
        <div className={styles.tools}><span>研究意见不等于账户指令</span><button type="button" aria-expanded={raw} aria-controls={`${id}-original`} onClick={()=>setRaw(value=>!value)}>{raw?"收起原答":`核对${current.label}原答`}</button></div>
        {raw && <section id={`${id}-original`} aria-label={`${current.label}角色原答`} className={styles.original}><dl>{Object.entries(output.fields).map(([key,value])=><div key={key}><dt>{FIELD_LABELS[key] ?? key}</dt><dd><OriginalValue value={value}/></dd></div>)}</dl><small>调用 {output.call_id} · 原答 {output.output_sha256}</small></section>}
      </section>
    </>}
  </section>;
}
