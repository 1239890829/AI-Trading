"use client";

import Link from "next/link";
import {useEffect, useId, useLayoutEffect, useRef, useState, type FormEvent} from "react";
import {StockLink} from "@/components/stock-link";
import {SELECTION_SCORE_LABELS, SelectionMeasurements, SelectionScores, readSelectionMetrics} from "@/components/detail/selection-decision-explorer";
import {ConditionList, FactCards, NarrativeText} from "@/components/ui/explanation-map";
import {ResearchDisclosure} from "@/components/ui/reader-brief";
import {IncrementalSentinel} from "@/components/ui/incremental-sentinel";
import {useIncremental} from "@/hooks/use-incremental";
import {useResource} from "@/hooks/use-resource";
import {dateTimeTextBJ, fmt} from "@/lib/format";
import {getStrategyMethodRun, getStrategyMethodRuns, isReferenceResearchMethod, type ReferenceResearchRowState, type SavedMethodKey, type SavedMethodRunDetail, type SavedMethodRunIdentity, type SavedMethodRunIndex, type SavedStrategyRunItem, type SavedStrategyRun} from "@/lib/api/picks";
import {identityLocation, methodRunDay, methodRunIdentity, methodRunKey, methodRunMatches, methodRunUrl, readMethodRunLocation, type MethodRowFilter, type MethodRunLocation} from "./method-run-location";

const ROW_LABELS: Record<MethodRowFilter, string> = {selected:"原入选",rejected:"拒绝存档",candidate:"候选",unknown:"待核",all:"全部存档行",matched:"符合条件",not_matched:"未符合"};
const DECISION_LABELS: Record<string,string> = {passed:"通过",ranked:"已排序",rejected:"未过",blocked:"阻断",unknown:"待核",included:"纳入候选",excluded:"排除",matched:"符合条件",not_matched:"未符合"};
const STAGE_LABELS: Record<string,string> = {source:"来源预筛",identity:"证券身份",candidate:"候选容量",quote:"报价核对",board:"交易板块",eligibility:"参与预筛",deep:"深评容量",score:"评分",selection:"最终选择",research:"研究",hard_gate:"硬门",rank:"排序"};
const SOURCE_LABELS: Record<string,string> = {event:"事件点名",event_theme:"题材成员",theme_linkage:"题材联动",limit_up:"涨停池",hot:"人气榜",carryover:"昨日组合"};
const UNSCORED_REASONS = new Set(["outside_a_share_universe","invalid_security_identity","candidate_capacity","deep_capacity","quote_missing","quote_identity_mismatch","risk_name_excluded","account_board_excluded","open_sealed_source_excluded"]);

export function ReferenceOutcomeSummary({outcomes}: {outcomes: SavedStrategyRun["outcomes"]}) {
  const [horizon, setHorizon] = useState("1");
  if (!outcomes) return <p className="text-[11px] text-zinc-600 dark:text-zinc-400">此旧批未保存参考价表现；不将缺项当作零胜率。</p>;
  const row = outcomes.horizons?.[horizon];
  const consistent = row && [row.signals,row.matured,row.immature,row.missing,row.wins].every(n=>Number.isSafeInteger(n) && n>=0)
    && row.matured+row.immature+row.missing===row.signals && row.wins<=row.matured
    && (row.matured===0 ? row.positive_rate===null && row.mean_return_pct===null && row.stress_mean_return_pct===null
      : Number.isFinite(row.positive_rate) && Math.abs(row.positive_rate!-row.wins/row.matured)<1e-9
        && Number.isFinite(row.mean_return_pct) && Number.isFinite(row.stress_mean_return_pct));
  const pct = (value: number | null) => value===null ? "未测得" : `${value.toFixed(2)}%`;
  return <section aria-label="战法参考价表现" className="space-y-2 border-b border-zinc-200 pb-3 text-xs dark:border-zinc-700">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-medium">历史参考价表现</h4><div role="group" aria-label="参考价观察周期" className="flex gap-1">{["1","3","5"].map(day=><button key={day} type="button" data-action="quiet" aria-pressed={horizon===day} onClick={()=>setHorizon(day)}>D{day}</button>)}</div></div>
    {consistent ? <><div className="flex flex-wrap items-center gap-2"><span>上涨 {row.wins} / 可测 {row.matured}</span><strong className="tabular-nums">{row.positive_rate===null?"未测得":`${(row.positive_rate*100).toFixed(1)}%`}</strong>
      {row.positive_rate!==null && <meter className="min-w-16 flex-1 accent-red-500" min={0} max={1} value={row.positive_rate} aria-label={`D${horizon}参考价上涨比例`}/>}</div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 sm:grid-cols-4"><div><dt className="text-zinc-600 dark:text-zinc-400">平均变化</dt><dd className="tabular-nums">{pct(row.mean_return_pct)}</dd></div><div><dt className="text-zinc-600 dark:text-zinc-400">{outcomes.cost_bps}bps压力后</dt><dd className="tabular-nums">{pct(row.stress_mean_return_pct)}</dd></div><div><dt className="text-zinc-600 dark:text-zinc-400">未成熟</dt><dd className="tabular-nums">{row.immature}</dd></div><div><dt className="text-zinc-600 dark:text-zinc-400">已成熟缺价</dt><dd className="tabular-nums">{row.missing}</dd></div></dl>
      <p className="text-[11px] text-zinc-600 dark:text-zinc-400">原信号 {row.signals}；{outcomes.window_start}–{outcomes.signal_through}，结果截至{outcomes.outcome_through}。复权收盘参考，不是成交胜率。</p></>
      : <p role="alert" className="text-amber-800 dark:text-amber-300">保存统计的分母未能核对，不显示比例或均值。</p>}
  </section>;
}
const GAP_LABELS: Record<string,string> = {sub_scores:"原六维分项",dimension_evidence:"原分项证据状态",scoring_trace:"原合成贡献",vetoes:"原否决项",min_pick_score:"原入选分线",score_shortfall:"原距分线差额",source_clock:"原来源时间"};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const stringList = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && !!item.trim()) : [];
const countText = (value: unknown) => finite(value) && value >= 0 ? value.toLocaleString("zh-CN") : "未记录";
const errorText = (value: unknown) => value instanceof Error ? value.message : "读取异常";
const emptyLocation = (method: string) => readMethodRunLocation(typeof window === "undefined" ? "" : window.location.search,method);
const candidateRow = (row: SavedStrategyRunItem) => !!row.item_id?.startsWith("candidate:") && typeof row.reason_code === "string";
const scoreText = (row: SavedStrategyRunItem) => finite(row.score) ? `原分 ${row.score}` : !candidateRow(row) ? "未保存分数"
  : UNSCORED_REASONS.has(row.reason_code!) ? "未评分" : row.reason_code==="scoring_unavailable" ? "成功评分未保存" : "评分状态未记录";

function sourceText(row: SavedStrategyRunItem) {
  const saved = Array.isArray(row.evidence?.candidate_sources) ? row.evidence.candidate_sources : [];
  const codes = [...new Set([row.evidence?.source,...saved.map(source=>source && typeof source==="object" && "from" in source ? source.from : null)]
    .filter((value):value is string=>typeof value==="string" && !!value.trim()))];
  return {label:codes.length?codes.map(code=>SOURCE_LABELS[code] || "来源待核").join(" · "):"未记录",raw:codes.join(" · ")};
}

function CandidateAuditSummary({audit}: {audit: SavedStrategyRun["candidate_audit"]}) {
  const knownCount = (value: unknown):value is number => typeof value==="number" && Number.isSafeInteger(value) && value>=0;
  const validCounts = audit && knownCount(audit.input_total) && knownCount(audit.saved_total) && knownCount(audit.omitted_total)
    && audit.input_total===audit.saved_total+audit.omitted_total;
  const state = audit?.state ?? "missing";
  const label = state==="missing" ? "未保存" : state==="partial" ? "部分保存"
    : validCounts && audit.complete===true && audit.omitted_total===0 ? "完整保存" : "保存状态待核";
  const sources = Object.entries(audit?.source_states ?? {}).map(([key,value])=>`${key==="events"?"事件来源":key==="hot"?"热榜来源":"其他来源"} ${value==="available"?"可用":value==="unavailable"?"不可用":"待核"}`);
  return <section aria-label="原候选来源留痕" className="space-y-1 text-[11px] text-zinc-600 dark:text-zinc-400">
    <p>原候选留痕：{label}{state!=="missing" && validCounts && <span className="tabular-nums"> · 记录 {countText(audit.saved_total)} / 原输入 {countText(audit.input_total)} · 未保存 {countText(audit.omitted_total)}</span>}</p>
    <p>{sources.length?sources.join(" · "):"原来源状态未记录"}；未出现不等于低分。</p>
    {state==="missing" ? <p>{audit?.reason || "原版本未保存候选来源、预筛与容量依据，不能按当前规则补写。"}</p>
      : (!validCounts || state==="saved" && label!=="完整保存") ? <p>原留痕计数或完整状态待核，不按已读行补算。</p>
      : state==="partial" && <p>{audit?.reason || "未保存行的具体原因未知；只展示原有留痕。"}</p>}
  </section>;
}

function CandidateTrace({row}: {row: SavedStrategyRunItem}) {
  const positions = [["原输入位置",row.evidence?.input_position],["原候选位置",row.evidence?.candidate_position],["原深评位置",row.evidence?.deep_position]]
    .flatMap(([label,value])=>finite(value)&&Number.isSafeInteger(value)&&value>0?[`${label} ${value}`]:[]);
  return <section aria-label={`${row.name || row.symbol}的原候选留痕`} className="mt-2 space-y-1 border-t border-zinc-200 pt-2 text-[11px] text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
    <p>{positions.length?positions.join(" · "):"原位置未记录，不补排位。"}</p>
    <p>只读原候选阶段；未保存的评分与原因不补0、不重算。</p>
  </section>;
}

function researchPageMatches(value: SavedMethodRunDetail, state: ReferenceResearchRowState, offset = 0) {
  const coverage = value.coverage, items = value.items;
  const total = coverage?.total, batchTotal = coverage?.batch_total;
  if (value.row_state !== state || !Array.isArray(items) || coverage.offset !== offset
    || !finite(total) || !Number.isInteger(total) || total < 0
    || !finite(batchTotal) || !Number.isInteger(batchTotal) || batchTotal < total
    || state === "all" && total !== batchTotal || coverage.returned !== items.length
    || !Number.isInteger(coverage.limit) || coverage.limit <= 0 || coverage.limit > 200 || items.length > coverage.limit
    || offset + items.length > total
    || items.some(row => !["matched", "not_matched", "unknown"].includes(row.state) || state !== "all" && row.state !== state)) return false;
  return offset + items.length < total
    ? items.length > 0 && coverage.next_offset === offset + items.length && coverage.possibly_truncated === true
    : coverage.next_offset === null && coverage.possibly_truncated === false;
}

function RowStateFilters({research, value, onChange}: {research: boolean; value: MethodRowFilter; onChange: (filter: MethodRowFilter) => void}) {
  const filters: MethodRowFilter[] = research ? ["all", "matched", "not_matched", "unknown"] : ["selected", "rejected", "candidate", "unknown", "all"];
  return <div className="flex flex-wrap gap-2 text-xs" role="group" aria-label="运行结果筛选">{filters.map(filter => <button key={filter} type="button" data-action="secondary" aria-pressed={value === filter} onClick={() => onChange(filter)}>{ROW_LABELS[filter]}</button>)}</div>;
}

function RunFilters({value, onApply}: {value: MethodRunLocation; onApply: (next: MethodRunLocation) => void}) {
  const [date,setDate] = useState(value.date);
  const [source,setSource] = useState(value.sourceVersion);
  const [strategy,setStrategy] = useState(value.strategyVersion);
  const [missing,setMissing] = useState(value.missingSource);
  function apply(event: FormEvent) {
    event.preventDefault();
    onApply({...value,date,sourceVersion:missing ? "" : source.trim(),missingSource:missing,strategyVersion:strategy.trim(),runId:"",contentVersion:"",row:""});
  }
  const inputClass = "min-h-8 w-full rounded border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-600";
  return <form onSubmit={apply} onKeyDown={event=>{if(event.key==="Enter" && (event.nativeEvent.isComposing || event.keyCode===229))event.preventDefault();}} aria-label="筛选保存运行" className="space-y-2">
    <div className="grid min-w-0 grid-cols-2 items-end gap-2 xl:grid-cols-[minmax(8rem,.8fr)_minmax(0,1fr)_minmax(0,1fr)_auto]">
      <label className="min-w-0 space-y-1 text-[11px] text-zinc-600 dark:text-zinc-400"><span>原运行日期</span><input aria-label="原运行日期" type="date" className={inputClass} value={date} onChange={event=>setDate(event.target.value)}/></label>
      <label className="min-w-0 space-y-1 text-[11px] text-zinc-600 dark:text-zinc-400"><span>原方法版本</span><input aria-label="原方法版本" className={inputClass} value={strategy} placeholder="全部已存版本" onChange={event=>setStrategy(event.target.value)}/></label>
      <label className="min-w-0 space-y-1 text-[11px] text-zinc-600 dark:text-zinc-400"><span>原来源版本</span><input aria-label="原来源版本" className={inputClass} value={source} disabled={missing} placeholder={missing?"未记录来源版本":"全部已存版本"} onChange={event=>setSource(event.target.value)}/></label>
      <button type="submit" data-action="secondary" className="min-h-8">筛选批次</button>
    </div>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-zinc-600 dark:text-zinc-400"><label className="inline-flex min-h-8 items-center gap-1.5"><input type="checkbox" checked={missing} onChange={event=>setMissing(event.target.checked)}/>只看来源版本未记录</label><button type="button" data-action="quiet" onClick={()=>onApply({...value,date:"",sourceVersion:"",strategyVersion:"",missingSource:false,runId:"",contentVersion:"",row:""})}>查看最近存档</button></div>
  </form>;
}

function RunDiagnostics({row, identity}: {row: SavedStrategyRunItem; identity: string}) {
  const diagnostics = row.diagnostics;
  const basis = Object.entries(row.basis ?? {}).filter((entry): entry is [string,string]=>typeof entry[1] === "string" && !!entry[1].trim());
  const trace = diagnostics?.scoring_trace;
  const metrics=readSelectionMetrics(row.evidence?.selection_metrics);
  const originalGateFacts=[
    {label:"原题材",value:row.source_theme}, {label:"原排名",value:finite(row.rank)?String(row.rank):null},
    {label:"原容量",value:finite(row.evidence?.effective_limit)?String(row.evidence.effective_limit):null},
    {label:"原容量内",value:typeof row.evidence?.within_display_capacity==="boolean"?row.evidence.within_display_capacity?"是":"否":null},
    {label:"原硬门",value:typeof row.evidence?.gate_decision==="string"?DECISION_LABELS[row.evidence.gate_decision] || row.evidence.gate_decision:null},
    {label:"原题材关联",value:typeof row.evidence?.linkage_level==="string"?row.evidence.linkage_level:null},
  ].flatMap(item=>item.value?[{label:item.label,value:item.value}]:[]);
  const traceFields = [["加权合成",trace?.weighted_total],["否决后",trace?.post_veto_score],["风险扣分",trace?.risk_penalty],["原最终分",trace?.final_score]] as const;
  const scores = diagnostics?.sub_scores ? SELECTION_SCORE_LABELS.map(([key,label])=>({key,label:label==="基本"?"基本面":label,maximum:100 as const,
    value:finite(diagnostics.sub_scores?.[key])?diagnostics.sub_scores[key]:null,
    state:typeof diagnostics.dimension_evidence?.[key]?.state==="string"?diagnostics.dimension_evidence[key].state:"unknown",
    basis:typeof diagnostics.dimension_evidence?.[key]?.reason==="string"?diagnostics.dimension_evidence[key].reason:typeof row.basis?.[key]==="string"?row.basis[key]:"原记录未保存此项依据。"})) : [];
  const vetoes = stringList(diagnostics?.vetoes);
  return <section aria-label={`${row.name || row.symbol}的原评分诊断`} className="mt-2 space-y-3 border-t border-zinc-200 pt-3 dark:border-zinc-700">
    <FactCards label="本行原分线与差额" items={[
      {label:"原总分",value:finite(row.score)?`${row.score}分`:"未记录"},
      {label:"原入选分线",value:finite(diagnostics?.min_pick_score)?`${diagnostics.min_pick_score}分`:"未记录"},
      {label:"原记录距分线差额",value:finite(diagnostics?.score_shortfall)?`${diagnostics.score_shortfall}分`:"未记录，不补算"},
    ]}/>
    {scores.length ? <SelectionScores key={identity} compact scores={scores} label="原运行六维评分" title="原运行六维" note="原规则分值，不是上涨概率；缺项不补0"/> : <p className="text-xs text-zinc-600 dark:text-zinc-400">原存档未保存六维分项，不套用当前分值。</p>}
    {metrics && <SelectionMeasurements value={metrics} compact/>}
    <FactCards label="原排名与阶段" items={originalGateFacts}/>
    {vetoes.length > 0 && <ConditionList label="原否决项" tone="warning" items={vetoes}/>}
    {Array.isArray(diagnostics?.vetoes) && diagnostics.vetoes.length > vetoes.length && <p className="text-xs text-zinc-600 dark:text-zinc-400">部分原否决记录结构未识别，没有改写成通过。</p>}
    {stringList(diagnostics?.missing).length > 0 && <ConditionList label="原评分缺项" tone="warning" items={stringList(diagnostics?.missing).map(key=>`${GAP_LABELS[key] || key}未记录`)}/>}
    {diagnostics?.selection_reason && <p className="text-xs text-zinc-600 dark:text-zinc-400">原选择原因码：{diagnostics.selection_reason}</p>}
    {!diagnostics || diagnostics.state === "missing" ? <p className="text-xs text-zinc-600 dark:text-zinc-400">逐项诊断未保存；有总分不等于依据完整。</p> : diagnostics.state === "partial" && <p className="text-xs text-amber-700 dark:text-amber-300">原诊断只保存了部分字段；未记录不解释为正常。</p>}
    {(trace || basis.length > 0 || row.snapshot_id) && <ResearchDisclosure label="原合成贡献与依据" identity={identity}>
      {trace && <><FactCards label="原评分合成" items={traceFields.flatMap(([label,value])=>finite(value)?[{label,value:`${fmt(value,2)}分`}]:[])}/>
        <FactCards label="原六维权重与贡献" items={SELECTION_SCORE_LABELS.flatMap(([key,label])=>{
          const dimension=trace.dimensions?.[key];
          return dimension && (finite(dimension.weight) || finite(dimension.contribution)) ? [{label,value:`权重 ${finite(dimension.weight)?`${fmt(dimension.weight*100,2)}%`:"未记录"} · 原贡献 ${finite(dimension.contribution)?`${fmt(dimension.contribution,2)}分`:"未记录"}`}] : [];
        })}/><p className="text-[11px] text-zinc-600 dark:text-zinc-400">原算法版本：{trace.algorithm_version || "未记录"}；只读原合成记录，不按当前权重重算。</p></>}
      {basis.map(([key,value])=><div key={key} className="mt-2"><h5 className="text-xs font-medium">{SELECTION_SCORE_LABELS.find(([id])=>id===key)?.[1] || key}</h5><NarrativeText text={value} label={`${key}原评分依据`} identity={`${identity}:${key}`} compact/></div>)}
      {row.snapshot_id && <p className="mt-2 break-all text-[10px] text-zinc-600 dark:text-zinc-400">原快照 {row.snapshot_id}</p>}
    </ResearchDisclosure>}
  </section>;
}

function ResearchConditions({row}: {row: SavedStrategyRunItem}) {
  const labels: Record<string,string> = {input_valid:"输入有效性",previous_dif_lte_dea:"前日DIF≤DEA",dif_above_dea:"当日DIF>DEA",close_above_ma5:"收盘>MA5",ma5_above_ma10:"MA5>MA10",ma10_above_ma20:"MA10>MA20",ma5_cross_ma10_or_ma20:"MA5上穿MA10或MA20",volume_above_previous5:"成交量>前5日均量",
    rsi14_crosses_above_30:"RSI14上穿30",close_above_previous:"收盘上升",ma20_not_declining:"MA20不下降",close_at_or_above_ma20:"守住MA20",distance_to_ma10_or_ma20_lte_2pct:"距支撑≤2%",volume_below_previous5_08:"量比<0.8",
    close_breaks_previous20_high:"突破前20日高点",turnover_above_previous5_15:"成交额放大≥1.5倍",prior_l1_12_to_30_sessions:"12–30日前有L1",volume_above_previous60_3:"量/前60日均量≥3",lower_wick_above_twice_body:"非零实体，下影≥2倍",close_in_upper_half:"收在区间上半部",
    first_body_above_5pct:"先有≥5%大阳",three_small_bearish_bodies:"随后三根小阴",three_volume_contracts:"三日均量收缩",three_lows_hold_first_open:"守住阳线开盘",confirmed_double_bottom:"已确认双底",close_above_ma5_10_20:"站上三条均线",distance_to_low20_lte_15pct:"距20日低点≤15%",volume_above_previous5_15:"量比≥1.5",
    return60_lte_minus20pct:"60日跌幅≥20%",positive_body_above_3pct:"阳线实体≥3%",volume_above_previous20_2:"量/前20日均量≥2",first_in_previous20:"20日首次信号",box20_amplitude_lte_15pct:"箱体振幅≤15%",close_in_lower20pct:"处箱体下20%",positive_body:"当日阳线"};
  const metricLabels: Record<string,string> = {close:"复权收盘",volume:"成交量（来源单位）",dif:"DIF",dea:"DEA",previous_dif:"前日DIF",previous_dea:"前日DEA",volume_ma5_previous:"前5日均量",volume_ratio5:"相对前5日量比",ma5:"MA5",ma10:"MA10",ma20:"MA20",previous_ma5:"前日MA5",previous_ma10:"前日MA10",previous_ma20:"前日MA20",
    previous_rsi14:"前日RSI14",rsi14:"RSI14",support_distance:"支撑距离（比例）",turnover_ratio5:"成交额/前5均额",previous_high20:"前20日复权高点",volume_ratio60:"量/前60日均量",lower_wick:"复权下影长度",candle_body:"复权实体长度",distance_to_low20:"距20日低点（比例）",return60:"60日变化（比例）",volume_ratio20:"量/前20日均量",box_top:"复权箱顶",box_bottom:"复权箱底",box_amplitude:"箱体振幅（比例）",box_position:"箱体位置（比例）"};
  const text = (value: unknown) => finite(value) ? fmt(value, 4) : typeof value === "string" && value.trim() ? value : "未提供";
  const conditions = Array.isArray(row.conditions) ? row.conditions : [];
  return <section className="mt-2 space-y-2" aria-label={`${row.name || row.symbol}研究条件`}>
    {conditions.length ? <ul className="flex flex-wrap gap-1.5">{conditions.map((condition,index) => <li key={`${condition.key}:${index}`} className="rounded border border-zinc-200 px-2 py-1 text-[11px] dark:border-zinc-700">
      <span className="font-medium">{condition.label || labels[condition.key] || condition.key}</span><span className="ml-1">{condition.met === true ? "符合" : condition.met === false ? "未符合" : "未知"}</span>
      {(condition.value!==null || condition.reference!==null) && <span className="block text-zinc-600 dark:text-zinc-400">原值 {text(condition.value)} · 对照 {text(condition.reference)}</span>}
    </li>)}</ul> : <p className="text-xs text-zinc-600 dark:text-zinc-400">逐项条件未记录，不补成通过。</p>}
    {row.metrics && <ResearchDisclosure label="本股指标原值" identity={row.item_id || row.symbol}><FactCards label="保存的研究指标" items={Object.entries(row.metrics).map(([label,value]) => ({label:metricLabels[label] || label,value:finite(value) ? String(value) : text(value)}))}/></ResearchDisclosure>}
  </section>;
}

function RunRows({run, identity, location, onLocation}: {run: SavedMethodRunDetail; identity: SavedMethodRunIdentity; location: MethodRunLocation; onLocation: (next: MethodRunLocation)=>void}) {
  const research = isReferenceResearchMethod(identity.method);
  const prefix=useId();
  const [pages,setPages]=useState<SavedMethodRunDetail[]>([]);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const inFlight=useRef(false), alive=useRef(true), controller=useRef<AbortController|null>(null);
  const rowButtons=useRef(new Map<string,HTMLButtonElement>());
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;controller.current?.abort();};},[]);
  const allPages=[run,...pages];
  const items=allPages.flatMap(page=>(page.items ?? []).map((row,index)=>({row,id:row.item_id || `${page.coverage.offset ?? 0}:${index}`})));
  const matching=research ? items : items.filter(({row})=>location.filter==="all" || row.state===location.filter);
  const incremental=useIncremental(matching,{pageSize:20,step:20,resetKey:location.filter});
  const latest=allPages[allPages.length-1], nextOffset=latest.coverage.next_offset;
  const hasRemote=typeof nextOffset==="number" && Number.isInteger(nextOffset) && nextOffset>=(latest.coverage.offset ?? 0)+(latest.items?.length ?? 0) && nextOffset>(latest.coverage.offset ?? 0);
  const coverage=allPages.reduce((state,page)=>({read:state.read+(page.items?.length ?? 0),complete:state.complete && page.state==="saved",
    contiguous:state.contiguous && page.coverage.offset===state.read && page.coverage.total===run.coverage.total
      && page.coverage.returned===(page.items?.length ?? 0)}),{read:0,complete:true,contiguous:true});
  const allRowsRead=coverage.contiguous && finite(run.coverage.total) && run.coverage.total>=0 && coverage.read===run.coverage.total
    && nextOffset===null && latest.coverage.possibly_truncated===false && latest.possibly_truncated!==true;
  const savedZero=location.filter==="selected" && allPages.every(page=>page.counts?.selected===0) && coverage.complete;
  const exactLocation=identityLocation(identity,location);
  async function readNext() {
    if (!hasRemote || inFlight.current) return;
    inFlight.current=true;setLoading(true);setError(null);controller.current=new AbortController();
    try {
      const result=await getStrategyMethodRun(identity,{offset:nextOffset!,limit:200,signal:controller.current.signal,
        ...(research ? {rowState:run.row_state!} : {})});
      if (!alive.current) return;
      if (!methodRunMatches(result,identity,nextOffset!)) throw new Error("后续页的原批次身份不符，未追加其他记录");
      if (research && (!researchPageMatches(result,run.row_state!,nextOffset!) || result.coverage.batch_total !== run.coverage.batch_total
        || result.coverage.total !== run.coverage.total || Object.keys({...run.counts,...result.counts}).some(key=>run.counts?.[key]!==result.counts?.[key]))) throw new Error("后续页的状态或分页范围不符，未追加其他状态");
      setPages(previous=>[...previous,result]);
    } catch (problem) {if (alive.current) setError(errorText(problem));}
    finally {inFlight.current=false;if(alive.current)setLoading(false);}
  }
  const readNextRef=useRef(readNext);
  // A visible continuation must already listen; its first scroll can precede passive effects.
  useLayoutEffect(()=>{readNextRef.current=readNext;});
  useLayoutEffect(()=>{
    if (!hasRemote || incremental.hasMore || loading || error) return;
    const sentinel=incremental.sentinelRef.current;
    if(!sentinel) return;
    let root:HTMLElement|null=sentinel.parentElement;
    while(root && root.scrollHeight<=root.clientHeight+1) root=root.parentElement;
    const target:HTMLElement|Window=root ?? window;
    const check=()=>{const rect=sentinel.getBoundingClientRect();if(rect.top<=(window.innerHeight || 800)+300 && rect.bottom>=-300)void readNextRef.current();};
    target.addEventListener("scroll",check,{passive:true});
    return()=>target.removeEventListener("scroll",check);
  },[hasRemote,incremental.hasMore,incremental.sentinelRef,matching.length,location.filter,location.row,loading,error]);
  const counts=research ? [["原样本",run.counts?.universe],["指标可评价",run.counts?.eligible],["符合条件",run.counts?.matched],["未符合",run.counts?.not_matched],["未知",run.counts?.unknown]] : identity.method==="daily_picks" ? [["候选母集",run.counts?.candidates],["评分成功",run.counts?.scored],["原入选",run.counts?.selected],["评分拒选",run.counts?.rejected_saved]]
    : [["候选行",run.counts?.candidate_rows],["排序行",run.counts?.rank_rows],["硬门行",run.counts?.hard_gate_rows],["原入选",run.counts?.selected]];
  const researchCountMismatch = research && (finite(run.counts?.universe) && run.counts.universe !== run.coverage.batch_total
    || run.row_state !== "all" && finite(run.counts?.[run.row_state!]) && run.counts![run.row_state!] !== run.coverage.total);
  function toggleRow(id: string) {
    onLocation({...exactLocation,row:location.row===id?"":id});
  }
  return <section aria-label="原保存运行" className="space-y-3">
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-zinc-200 pb-2 dark:border-zinc-700"><h3 className="text-sm font-medium">原保存运行 <span className="ml-1 text-xs font-normal text-zinc-600 dark:text-zinc-400">{identity.trade_date}</span></h3><span className="text-[11px] text-zinc-600 dark:text-zinc-400">{run.run_at?dateTimeTextBJ(run.run_at):"运行时刻未记录"} · {finite(run.elapsed_ms)?`${run.elapsed_ms}ms`:"耗时未记录"}</span></div>
    {run.state==="partial" && <p role="status" className="text-xs text-amber-700 dark:text-amber-300">部分存档不可读取：{run.reason || "原始内容不完整"}；未知计数不按0处理。</p>}
    {run.version_state==="legacy" && <p className="text-xs text-zinc-600 dark:text-zinc-400">旧档没有完整方法或来源版本，只按原内容指纹绑定；不补写现行版本。</p>}
    {run.version_state==="mismatch" && <p role="status" className="text-xs text-amber-700 dark:text-amber-300">原入选版本与所保存内容不一致；可读原记录，不作为已核入选版本。</p>}
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 border-b border-zinc-200 pb-3 text-xs sm:grid-cols-4 dark:border-zinc-700">{counts.map(([label,value])=><div key={String(label)}><dt className="text-zinc-600 dark:text-zinc-400">{label}</dt><dd className="mt-0.5 font-medium tabular-nums">{countText(value)}</dd></div>)}</dl>
    {!research && (identity.method==="daily_picks" || run.candidate_audit) && <CandidateAuditSummary audit={run.candidate_audit}/>}
    {research && <ReferenceOutcomeSummary outcomes={run.outcomes}/>}
    <p className="text-[11px] text-zinc-600 dark:text-zinc-400">{research ? "仅手动价格研究：指标可评价不是风控资格；匹配不构成入选、通知、模拟或收益证据。" : identity.method==="daily_picks"?"评分拒选仅计原评分后的拒选存档；预筛与未评分留痕另列，不代表执行通过。":"各阶段按行计数，可能包含同股/同题材多行，不是递减漏斗。"} 运行、健康窗口与离线核验分别读取。</p>
    {research && <p className="text-[11px] text-zinc-600 dark:text-zinc-400">来源已完成日 {run.source_completed_date || "未记录"}；源首次发布时间 {run.source_as_of ? dateTimeTextBJ(run.source_as_of) : "未知"}；筛选界限 {run.source_cutoff_at ? dateTimeTextBJ(run.source_cutoff_at) : "未记录"}（不是发布时间）。</p>}
    {research && <p className="text-[11px] text-zinc-600 dark:text-zinc-400">比较采用保存原值；条件小数显示4位，完整精度见本股指标原值。</p>}
    {researchCountMismatch && <p role="status" className="text-xs text-amber-700 dark:text-amber-300">原分母与实际保存行覆盖不一致，分别保留；未用当前可见行改写原计数。</p>}
    {run.data_state && <p className="text-[11px] text-zinc-600 dark:text-zinc-400">运行输入：{run.data_state}（原保存状态）；存档存在不等于输入完整。</p>}
    {!allRowsRead && <p role="status" className="text-xs text-amber-700 dark:text-amber-300">{research ? "当前仅已读部分，不以可见行替换完整样本、未符合或未知分母。" : run.counts?.selected==null?"原入选总数未知；不能以当前行数冒充全集。":"原存档投影可能截断，不按当前行数替换原计数。"}</p>}
    {(finite(run.criteria?.min_pick_score)||finite(run.criteria?.max_picks)) && <p className="text-[11px] text-zinc-600 dark:text-zinc-400">原入选条件：{finite(run.criteria?.min_pick_score)?`分线 ${run.criteria.min_pick_score}`:"分线未记录"} · {finite(run.criteria?.max_picks)?`容量 ${run.criteria.max_picks}`:"容量未记录"}；执行资格另行复核。</p>}
    {!research && <RowStateFilters research={false} value={location.filter} onChange={filter=>onLocation({...exactLocation,filter,row:""})}/>}
    {matching.length===0 ? <p className="py-2 text-xs text-zinc-600 dark:text-zinc-400">{research && allRowsRead && coverage.complete && run.coverage.total===0 ? `原研究批次已保存：${ROW_LABELS[run.row_state!]} 0 条原行。` : savedZero?"原批次已保存：0只入选。":"已读部分没有此类保存行"+(hasRemote?"，可继续下拉读取原批次":"")+(research?"；不据此推断当时零匹配或未运行。":"；不据此推断当时零入选或未运行。")}</p> : <ul className="divide-y divide-zinc-200 dark:divide-zinc-700">{incremental.shown.map(({row,id})=><li key={id} className="py-2 text-xs">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">{/^\d{6}$/.test(row.symbol)?<StockLink symbol={row.symbol} className="font-medium">{row.name || row.symbol}</StockLink>:<span className="font-medium">{row.name || row.symbol || "证券未记录"}</span>}<span className="font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{row.symbol}</span><span className="text-[11px] text-zinc-600 dark:text-zinc-400">{ROW_LABELS[row.state] || row.state}</span>{row.stage && <span className="text-[11px] text-zinc-600 dark:text-zinc-400">{STAGE_LABELS[row.stage] || "原阶段待核"}{row.decision?`：${DECISION_LABELS[row.decision] || `未识别（${row.decision}）`}`:candidateRow(row)?"":"：未记录"}</span>}{!research && <span className="ml-auto tabular-nums">{scoreText(row)}</span>}</div>
      {candidateRow(row) && <p className="mt-1 break-words text-[11px] text-zinc-600 dark:text-zinc-400" title={`原保存来源：${sourceText(row).raw || "未记录"}`}>原来源：{sourceText(row).label}</p>}
      <p className="mt-1 break-words text-zinc-600 dark:text-zinc-400">{row.reason || (research ? row.state === "matched" ? "本版价格条件符合，逐项原值见下方；不是买点或入选。" : row.state === "not_matched" ? "未满足全部价格条件，逐项原值见下方。" : "研究输入未知，不当作未符合或零值。" : Object.keys(row.basis ?? {}).length?"原评分依据已保存，可展开核对；简短理由未记录。":"原存档没有简短理由，不能据此推定通过。")}</p>
      {research && <ResearchConditions row={row}/>}
      {!research && <><button ref={element=>{if(element)rowButtons.current.set(id,element);else rowButtons.current.delete(id);}} type="button" data-action="quiet" className="mt-1 min-h-8 text-[11px]" aria-expanded={location.row===id} aria-controls={`${prefix}-${encodeURIComponent(id)}`} onClick={()=>toggleRow(id)}>{location.row===id?"收起":"核对"}{row.name || row.symbol}{candidateRow(row)?"原候选留痕":"原评分与缺项"}</button>
      {location.row===id && <div id={`${prefix}-${encodeURIComponent(id)}`} onKeyDown={event=>{if(event.key==="Escape" && !event.nativeEvent.isComposing){event.preventDefault();event.stopPropagation();onLocation({...exactLocation,row:""});rowButtons.current.get(id)?.focus({preventScroll:true});}}}>{candidateRow(row)?<CandidateTrace row={row}/>:<RunDiagnostics row={row} identity={`${methodRunKey(identity)}:${id}`}/>}</div>}</>}
    </li>)}</ul>}
    {location.row && !items.some(item=>item.id===location.row) && <p role="status" className="text-xs text-zinc-600 dark:text-zinc-400">链接指定的原行尚未读到；继续读取原批次，不按同股代码换行。</p>}
    <IncrementalSentinel sentinelRef={incremental.sentinelRef} visible={Math.min(incremental.visible,matching.length)} total={matching.length} onLoadMore={incremental.loadMore}/>
    <p className="text-[11px] text-zinc-600 dark:text-zinc-400">{research ? `本状态已读 ${countText(items.length)} / ${countText(run.coverage.total)} 条；全批 ${countText(run.coverage.batch_total)} 条原行。` : `本批已读 ${items.length} / ${countText(run.coverage.total)} 条原行；筛选当前已读部分。`}{run.coverage.history_semantics}</p>
    {error && <p role="alert" className="text-xs text-rose-700 dark:text-rose-300">后续页读取失败：{error}。已读行保留，未换成最新批次。</p>}
    {hasRemote && <button type="button" data-action="secondary" disabled={loading} onClick={()=>void readNext()}>{loading?"读取原批次后续行…":error?"重试原批次后续行":"继续读取原批次"}</button>}
    <div className="flex flex-wrap gap-3 border-t border-zinc-200 pt-2 text-xs dark:border-zinc-700"><Link href={`/agent?area=research&tab=review&date=${encodeURIComponent(identity.trade_date)}`}>同日复盘</Link>{!research && <Link href={identity.method==="daily_picks"?"/hunting?view=discover&sec=daily":"/hunting?view=discover&sec=candidates"}>另看当前名单（非本次历史）</Link>}</div>
    <p className="text-[10px] text-zinc-600 dark:text-zinc-400">个股链接打开当前行情详情；{research ? "本页条件与指标原值继续绑定保存研究日，不产生当前入选、委托或成交。" : "本页原分、缺项和理由继续绑定上述历史批次。原入选不等于委托或成交。"}</p>
    <ResearchDisclosure label="原批次身份与范围" identity={methodRunKey(identity)}><dl className="space-y-1 break-all text-[11px]"><div><dt>原运行ID</dt><dd>{identity.run_id}</dd></div><div><dt>原来源版本</dt><dd>{identity.source_version ?? "未记录，不虚构版本"}</dd></div><div><dt>原方法版本</dt><dd>{run.strategy_version || "未记录"}</dd></div><div><dt>原内容指纹</dt><dd>{identity.content_version}</dd></div>{research && <><div><dt>本批规则</dt><dd>{run.definition || "未记录"}</dd></div><div><dt>规则指纹</dt><dd>{run.definition_digest || "未记录"}</dd></div><div><dt>本系统v1明确口径</dt><dd>{run.definition_supplement || "未记录"}</dd></div>{run.data_limitations && <div><dt>研究数据限制</dt><dd><ConditionList label="研究数据限制" tone="warning" items={run.data_limitations}/></dd></div>}</>}</dl></ResearchDisclosure>
  </section>;
}

export function MethodRunReader({method, initialIdentity, initialDate, compact = false}: {method: string; initialIdentity?: SavedMethodRunIdentity; initialDate?: string; compact?: boolean}) {
  const seed = () => {const value = emptyLocation(method); if (initialDate !== undefined) value.date = initialDate; return initialIdentity?.method === method ? identityLocation(initialIdentity, value) : value;};
  const [location,setLocation]=useState(seed);
  const [scope,setScope]=useState(seed);
  useEffect(()=>{const sync=()=>{const next=emptyLocation(method);setLocation(next);setScope(next);};window.addEventListener("popstate",sync);return()=>window.removeEventListener("popstate",sync);},[method]);
  const supported=(method==="daily_picks" || method==="intraday_watch" || isReferenceResearchMethod(method)) && (!initialIdentity || initialIdentity.method === method);
  const research=isReferenceResearchMethod(method);
  const rowState:ReferenceResearchRowState=research && ["matched","not_matched","unknown"].includes(location.filter) ? location.filter as ReferenceResearchRowState : "all";
  const day=methodRunDay(location.date);
  const scopeDay=methodRunDay(scope.date);
  const invalidDate=!!location.date && !day;
  const explicit=!!(location.runId || location.contentVersion || location.row);
  const query={method:method as SavedMethodKey,trade_date:scopeDay || undefined,source_version:scope.sourceVersion || undefined,source_version_missing:scope.missingSource || undefined,strategy_version:scope.strategyVersion || undefined,limit:30};
  const index=useResource(signal=>getStrategyMethodRuns(query,signal),
    {key:JSON.stringify([method,scopeDay,scope.sourceVersion,scope.missingSource,scope.strategyVersion]),enabled:supported && (!scope.date || !!scopeDay),marketHours:false,abortable:true});
  const [moreIndex,setMoreIndex]=useState<{base:SavedMethodRunIndex|undefined;pages:SavedMethodRunIndex[];loading:boolean;error:string|null}>({base:undefined,pages:[],loading:false,error:null});
  if(moreIndex.base!==index.data)setMoreIndex({base:index.data,pages:[],loading:false,error:null});
  const indexBusy=useRef<SavedMethodRunIndex|null>(null), indexControllers=useRef(new Set<AbortController>());
  useEffect(()=>{const controllers=indexControllers.current;return()=>{for(const controller of controllers)controller.abort();};},[]);
  const indexMatches=index.data?.method===method;
  const indexPages=indexMatches?[index.data!,...(moreIndex.base===index.data?moreIndex.pages:[])]:[];
  const runs=[...new Map(indexPages.flatMap(page=>page.items).map(item=>[methodRunKey(item),item])).values()].filter(item=>item.method===method && methodRunDay(item.trade_date) && (!scopeDay || methodRunDay(item.trade_date)===scopeDay)
    && (!scope.sourceVersion || item.source_version===scope.sourceVersion) && (!scope.missingSource || item.source_version===null)
    && (!scope.strategyVersion || item.strategy_version===scope.strategyVersion));
  const lastIndex=indexPages.at(-1), nextIndexOffset=lastIndex?.coverage.next_offset;
  const moreIndexAvailable=typeof nextIndexOffset==="number" && Number.isInteger(nextIndexOffset) && nextIndexOffset>(lastIndex?.coverage.offset ?? 0);
  async function readMoreIndex() {
    const base=index.data;
    if(!base || !moreIndexAvailable || indexBusy.current===base)return;
    indexBusy.current=base;setMoreIndex(previous=>({...previous,loading:true,error:null}));
    const controller=new AbortController();indexControllers.current.add(controller);
    try {
      const result=await getStrategyMethodRuns({...query,offset:nextIndexOffset!},controller.signal);
      if(result.method!==method || result.coverage.offset!==nextIndexOffset)throw new Error("后续索引的方法或扫描位置不符，未合入其他范围");
      setMoreIndex(previous=>previous.base===base?{...previous,pages:[...previous.pages,result],loading:false}:previous);
    }catch(problem){if(!controller.signal.aborted)setMoreIndex(previous=>previous.base===base?{...previous,loading:false,error:errorText(problem)}:previous);}
    finally{indexControllers.current.delete(controller);if(indexBusy.current===base)indexBusy.current=null;}
  }
  const indexMarker=useRef<HTMLDivElement|null>(null), readMoreIndexRef=useRef(readMoreIndex);
  useLayoutEffect(()=>{readMoreIndexRef.current=readMoreIndex;});
  useLayoutEffect(()=>{
    if(!moreIndexAvailable || moreIndex.loading || moreIndex.error)return;
    const marker=indexMarker.current;if(!marker)return;
    let root:HTMLElement|null=marker.parentElement;
    while(root && root.scrollHeight<=root.clientHeight+1)root=root.parentElement;
    const target:HTMLElement|Window=root ?? window;
    const check=()=>{const rect=marker.getBoundingClientRect();if(rect.top<=(window.innerHeight || 800)+300 && rect.bottom>=-300)void readMoreIndexRef.current();};
    target.addEventListener("scroll",check,{passive:true});return()=>target.removeEventListener("scroll",check);
  },[moreIndexAvailable,moreIndex.loading,moreIndex.error]);
  const identity=explicit ? methodRunIdentity(method as SavedMethodKey,location) : runs[0] ?? null;
  if(supported && identity && !explicit)setLocation(identityLocation(identity,location));
  const key=identity?methodRunKey(identity):"none";
  const detail=useResource(async signal=>{
    const result=await getStrategyMethodRun(identity!,{signal,...(research ? {rowState} : {})});
    if(!methodRunMatches(result,identity!))throw new Error("返回的日期、来源版本或内容指纹不符，未使用其他批次");
    if(research && !researchPageMatches(result,rowState))throw new Error("返回的状态或分页范围不符，未使用其他状态");
    return result;
  },{key:research?JSON.stringify([key,rowState]):key,enabled:supported && !!identity && !invalidDate,marketHours:false,abortable:true});
  useEffect(()=>{
    const original=methodRunIdentity(method as SavedMethodKey,location);
    if(original) {
      const url=methodRunUrl(window.location.pathname,window.location.search,method,identityLocation(original,location));
      if(url!==window.location.pathname+window.location.search)window.history.replaceState({},"",url);
    }
  },[location,method]);
  function navigate(next: MethodRunLocation, changeScope=false) {
    window.history.pushState({},"",methodRunUrl(window.location.pathname,window.location.search,method,next));
    setLocation(next);
    if(changeScope)setScope(next);
  }
  if(!supported)return <section aria-label="保存运行" className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-700"><h3 className="font-medium">保存运行</h3><p className="mt-1 text-zinc-600 dark:text-zinc-400">尚无本版可读取的保存运行；不按页面读取时间制造运行记录。</p><p className="mt-1 text-zinc-600 dark:text-zinc-400">观察或已否决的方法不会因打开目录而启动。</p></section>;
  const chosenInList=identity && runs.some(run=>methodRunKey(run)===key);
  return <section aria-label="选股历史运行" className="space-y-3">
    {compact ? <details><summary className="cursor-pointer text-xs">批次与版本筛选</summary><div className="mt-2"><RunFilters key={JSON.stringify([scope.date,scope.sourceVersion,scope.missingSource,scope.strategyVersion])} value={scope} onApply={next=>navigate(next,true)}/></div></details>
      : <RunFilters key={JSON.stringify([scope.date,scope.sourceVersion,scope.missingSource,scope.strategyVersion])} value={scope} onApply={next=>navigate(next,true)}/>}
    {invalidDate && <p role="alert" className="text-xs text-rose-700 dark:text-rose-300">原日期无效；修正日期后读取，不回落最新运行。</p>}
    <label className="block space-y-1 text-[11px] text-zinc-600 dark:text-zinc-400"><span>选择原运行批次</span><select aria-label="选择原运行批次" className="min-h-9 w-full min-w-0 rounded border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-600" value={identity?key:""} disabled={!runs.length && !identity} onChange={event=>{const chosen=runs.find(run=>methodRunKey(run)===event.target.value);if(chosen)navigate(identityLocation(chosen,{...location,row:""}));}}>
      {!identity && <option value="">{index.pending?"读取保存批次…":"没有匹配的可读批次"}</option>}{identity && !chosenInList && <option value={key}>{identity.trade_date} · 链接指定的原批次 {identity.run_id}</option>}{runs.map(run=><option key={methodRunKey(run)} value={methodRunKey(run)}>{run.trade_date} · {run.run_at?dateTimeTextBJ(run.run_at,{seconds:true}):"时刻未记录"} · {run.run_id}</option>)}</select></label>
    {index.pending && !index.data && <p role="status" className="text-xs text-zinc-600 dark:text-zinc-400">读取保存运行索引…</p>}
    {(index.error != null || index.data && !indexMatches) && <p role="alert" className="text-xs text-rose-700 dark:text-rose-300">运行索引读取失败：{index.error?errorText(index.error):"返回方法不符"}；未改读其他方法。<button type="button" data-action="quiet" onClick={index.refresh}>重试索引</button></p>}
    {index.data && <p className="text-[10px] text-zinc-600 dark:text-zinc-400">已读索引 {runs.length} 批{moreIndexAvailable?"，仍有未扫描范围；可下拉继续读取或输入日期/原版本缩小范围":""}。{index.data.coverage.history_semantics}</p>}
    <div ref={indexMarker} className="h-px"/>
    {moreIndex.error && <p role="alert" className="text-xs text-rose-700 dark:text-rose-300">较早批次索引读取失败：{moreIndex.error}；已读批次保留。</p>}
    {moreIndexAvailable && <button type="button" data-action="quiet" disabled={moreIndex.loading} onClick={()=>void readMoreIndex()}>{moreIndex.loading?"读取较早批次…":moreIndex.error?"重试较早批次":"继续读取更早批次"}</button>}
    {explicit && !identity && <p role="alert" className="text-xs text-rose-700 dark:text-rose-300">原批次链接缺少有效日期、内容指纹或来源版本状态；没有换成最新运行。可重选保存批次。</p>}
    {research && identity && !invalidDate && <RowStateFilters research value={rowState} onChange={filter=>navigate({...identityLocation(identity,location),filter,row:""})}/>}
    {!explicit && !identity && !index.pending && !index.error && !invalidDate && <p className="text-xs text-zinc-600 dark:text-zinc-400">{moreIndexAvailable?"本次扫描没有匹配批次，仍有未读范围；可继续读取更早批次。":isReferenceResearchMethod(method)?"所查扫描范围没有可读取的研究批次；不证明源日未运行或零匹配。":"所查扫描范围没有可读取的保存批次；不证明当日未运行或零入选。"}</p>}
    {detail.pending && !detail.data && <p role="status" className="text-xs text-zinc-600 dark:text-zinc-400">读取指定原批次…</p>}
    {detail.error != null && <p role="alert" className="text-xs text-rose-700 dark:text-rose-300">指定原批次读取失败：{errorText(detail.error)}。{detail.data?"保留已读原批次，当前重读未确认。":"未回落最新运行或当前名单。"}<button type="button" data-action="quiet" onClick={detail.refresh}>重试原批次</button></p>}
    {detail.data && identity && (detail.data.state==="saved" || detail.data.state==="partial") && <RunRows key={research?`${key}:${rowState}`:key} run={detail.data} identity={identity} location={location} onLocation={navigate}/>}
    {detail.data && !["saved","partial"].includes(detail.data.state) && <p role="status" className="text-xs text-zinc-600 dark:text-zinc-400">{detail.data.reason || "原批次内容不可读取，不代表没有运行。"}</p>}
  </section>;
}
