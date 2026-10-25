"use client";

import Link from "next/link";
import {useEffect, useId, useRef, useState, type FormEvent} from "react";
import {StockLink} from "@/components/stock-link";
import {SELECTION_SCORE_LABELS, SelectionMeasurements, SelectionScores, readSelectionMetrics} from "@/components/detail/selection-decision-explorer";
import {ConditionList, FactCards, NarrativeText} from "@/components/ui/explanation-map";
import {ResearchDisclosure} from "@/components/ui/reader-brief";
import {IncrementalSentinel} from "@/components/ui/incremental-sentinel";
import {useIncremental} from "@/hooks/use-incremental";
import {useResource} from "@/hooks/use-resource";
import {dateTimeTextBJ, fmt} from "@/lib/format";
import {getStrategyMethodRun, getStrategyMethodRuns, type SavedMethodKey, type SavedMethodRunDetail, type SavedMethodRunIdentity, type SavedMethodRunIndex, type SavedStrategyRunItem} from "@/lib/api/picks";
import {identityLocation, methodRunDay, methodRunIdentity, methodRunKey, methodRunMatches, methodRunUrl, readMethodRunLocation, type MethodRowFilter, type MethodRunLocation} from "./method-run-location";

const ROW_LABELS: Record<MethodRowFilter, string> = {selected:"原入选",rejected:"拒绝存档",candidate:"候选",unknown:"待核",all:"全部存档行"};
const DECISION_LABELS: Record<string,string> = {passed:"通过",ranked:"已排序",rejected:"未过",blocked:"阻断",unknown:"待核",included:"纳入候选",excluded:"排除"};
const GAP_LABELS: Record<string,string> = {sub_scores:"原六维分项",dimension_evidence:"原分项证据状态",scoring_trace:"原合成贡献",vetoes:"原否决项",min_pick_score:"原入选分线",score_shortfall:"原距分线差额",source_clock:"原来源时间"};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const stringList = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && !!item.trim()) : [];
const countText = (value: unknown) => finite(value) && value >= 0 ? value.toLocaleString("zh-CN") : "未记录";
const errorText = (value: unknown) => value instanceof Error ? value.message : "读取异常";
const emptyLocation = (method: string) => readMethodRunLocation(typeof window === "undefined" ? "" : window.location.search,method);

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

function RunRows({run, identity, location, onLocation}: {run: SavedMethodRunDetail; identity: SavedMethodRunIdentity; location: MethodRunLocation; onLocation: (next: MethodRunLocation)=>void}) {
  const prefix=useId();
  const [pages,setPages]=useState<SavedMethodRunDetail[]>([]);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const inFlight=useRef(false), alive=useRef(true), controller=useRef<AbortController|null>(null);
  const rowButtons=useRef(new Map<string,HTMLButtonElement>());
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;controller.current?.abort();};},[]);
  const allPages=[run,...pages];
  const items=allPages.flatMap(page=>(page.items ?? []).map((row,index)=>({row,id:row.item_id || `${page.coverage.offset ?? 0}:${index}`})));
  const matching=items.filter(({row})=>location.filter==="all" || row.state===location.filter);
  const incremental=useIncremental(matching,{pageSize:20,step:20,resetKey:location.filter});
  const latest=allPages[allPages.length-1], nextOffset=latest.coverage.next_offset;
  const hasRemote=typeof nextOffset==="number" && Number.isInteger(nextOffset) && nextOffset>=(latest.coverage.offset ?? 0)+(latest.items?.length ?? 0) && nextOffset>(latest.coverage.offset ?? 0);
  const coverage=allPages.reduce((state,page)=>({read:state.read+(page.items?.length ?? 0),complete:state.complete && page.state==="saved"
    && page.coverage.offset===state.read && page.coverage.total===run.coverage.total && page.coverage.returned===(page.items?.length ?? 0)}),{read:0,complete:true});
  const savedZero=location.filter==="selected" && allPages.every(page=>page.counts?.selected===0) && coverage.complete
    && finite(run.coverage.total) && run.coverage.total>=0 && coverage.read===run.coverage.total
    && nextOffset===null && latest.coverage.possibly_truncated===false && latest.possibly_truncated!==true;
  const exactLocation=identityLocation(identity,location);
  async function readNext() {
    if (!hasRemote || inFlight.current) return;
    inFlight.current=true;setLoading(true);setError(null);controller.current=new AbortController();
    try {
      const result=await getStrategyMethodRun(identity,{offset:nextOffset!,limit:200,signal:controller.current.signal});
      if (!alive.current) return;
      if (!methodRunMatches(result,identity,nextOffset!)) throw new Error("后续页的原批次身份不符，未追加其他记录");
      setPages(previous=>[...previous,result]);
    } catch (problem) {if (alive.current) setError(errorText(problem));}
    finally {inFlight.current=false;if(alive.current)setLoading(false);}
  }
  const readNextRef=useRef(readNext);
  useEffect(()=>{readNextRef.current=readNext;});
  useEffect(()=>{
    if (!hasRemote || incremental.hasMore || loading || error) return;
    const sentinel=incremental.sentinelRef.current;
    if(!sentinel) return;
    let root:HTMLElement|null=sentinel.parentElement;
    while(root && root.scrollHeight<=root.clientHeight+1) root=root.parentElement;
    const target:HTMLElement|Window=root ?? window;
    const check=()=>{const rect=sentinel.getBoundingClientRect();if(rect.top<=(window.innerHeight || 800)+300 && rect.bottom>=-300)void readNextRef.current();};
    target.addEventListener("scroll",check,{passive:true});
    return()=>target.removeEventListener("scroll",check);
  },[hasRemote,incremental.hasMore,incremental.sentinelRef,loading,error]);
  const counts=identity.method==="daily_picks" ? [["候选母集",run.counts?.candidates],["评分成功",run.counts?.scored],["原入选",run.counts?.selected],["拒绝存档",run.counts?.rejected_saved]]
    : [["候选行",run.counts?.candidate_rows],["排序行",run.counts?.rank_rows],["硬门行",run.counts?.hard_gate_rows],["原入选",run.counts?.selected]];
  function toggleRow(id: string) {
    onLocation({...exactLocation,row:location.row===id?"":id});
  }
  return <section aria-label="原保存运行" className="space-y-3">
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-zinc-200 pb-2 dark:border-zinc-700"><h3 className="text-sm font-medium">原保存运行 <span className="ml-1 text-xs font-normal text-zinc-600 dark:text-zinc-400">{identity.trade_date}</span></h3><span className="text-[11px] text-zinc-600 dark:text-zinc-400">{run.run_at?dateTimeTextBJ(run.run_at):"运行时刻未记录"} · {finite(run.elapsed_ms)?`${run.elapsed_ms}ms`:"耗时未记录"}</span></div>
    {run.state==="partial" && <p role="status" className="text-xs text-amber-700 dark:text-amber-300">部分存档不可读取：{run.reason || "原始内容不完整"}；未知计数不按0处理。</p>}
    {run.version_state==="legacy" && <p className="text-xs text-zinc-600 dark:text-zinc-400">旧档没有完整方法或来源版本，只按原内容指纹绑定；不补写现行版本。</p>}
    {run.version_state==="mismatch" && <p role="status" className="text-xs text-amber-700 dark:text-amber-300">原入选版本与所保存内容不一致；可读原记录，不作为已核入选版本。</p>}
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 border-b border-zinc-200 pb-3 text-xs sm:grid-cols-4 dark:border-zinc-700">{counts.map(([label,value])=><div key={String(label)}><dt className="text-zinc-600 dark:text-zinc-400">{label}</dt><dd className="mt-0.5 font-medium tabular-nums">{countText(value)}</dd></div>)}</dl>
    <p className="text-[11px] text-zinc-600 dark:text-zinc-400">{identity.method==="daily_picks"?"入选与拒绝均为原保存结果，不代表执行通过。":"各阶段按行计数，可能包含同股/同题材多行，不是递减漏斗。"} 运行、健康窗口与离线核验分别读取。</p>
    {run.data_state && <p className="text-[11px] text-zinc-600 dark:text-zinc-400">运行输入：{run.data_state}（原保存状态）；存档存在不等于输入完整。</p>}
    {run.possibly_truncated && <p role="status" className="text-xs text-amber-700 dark:text-amber-300">{run.counts?.selected==null?"原入选总数未知；不能以当前行数冒充全集。":"原存档投影可能截断，不按当前行数替换原计数。"}</p>}
    {(finite(run.criteria?.min_pick_score)||finite(run.criteria?.max_picks)) && <p className="text-[11px] text-zinc-600 dark:text-zinc-400">原入选条件：{finite(run.criteria?.min_pick_score)?`分线 ${run.criteria.min_pick_score}`:"分线未记录"} · {finite(run.criteria?.max_picks)?`容量 ${run.criteria.max_picks}`:"容量未记录"}；执行资格另行复核。</p>}
    <div className="flex flex-wrap gap-2 text-xs" aria-label="运行结果筛选">{(Object.keys(ROW_LABELS) as MethodRowFilter[]).map(filter=><button key={filter} type="button" data-action="secondary" aria-pressed={location.filter===filter} onClick={()=>onLocation({...exactLocation,filter,row:""})}>{ROW_LABELS[filter]}</button>)}</div>
    {matching.length===0 ? <p className="py-2 text-xs text-zinc-600 dark:text-zinc-400">{savedZero?"原批次已保存：0只入选。":"已读部分没有此类保存行"+(hasRemote?"，可继续下拉读取原批次":"")+"；不据此推断当时零入选或未运行。"}</p> : <ul className="divide-y divide-zinc-200 dark:divide-zinc-700">{incremental.shown.map(({row,id})=><li key={id} className="py-2 text-xs">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">{/^\d{6}$/.test(row.symbol)?<StockLink symbol={row.symbol} className="font-medium">{row.name || row.symbol}</StockLink>:<span className="font-medium">{row.name || row.symbol || "证券未记录"}</span>}<span className="font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{row.symbol}</span><span className="text-[11px] text-zinc-600 dark:text-zinc-400">{ROW_LABELS[row.state] || row.state}</span>{row.stage && <span className="text-[11px] text-zinc-600 dark:text-zinc-400">{row.stage==="hard_gate"?"硬门":row.stage==="rank"?"排序":row.stage}：{row.decision?DECISION_LABELS[row.decision] || `未识别（${row.decision}）`:"未记录"}</span>}<span className="ml-auto tabular-nums">{finite(row.score)?`原分 ${row.score}`:"未保存分数"}</span></div>
      <p className="mt-1 break-words text-zinc-600 dark:text-zinc-400">{row.reason || (Object.keys(row.basis ?? {}).length?"原评分依据已保存，可展开核对；简短理由未记录。":"原存档没有简短理由，不能据此推定通过。")}</p>
      <button ref={element=>{if(element)rowButtons.current.set(id,element);else rowButtons.current.delete(id);}} type="button" data-action="quiet" className="mt-1 min-h-8 text-[11px]" aria-expanded={location.row===id} aria-controls={`${prefix}-${encodeURIComponent(id)}`} onClick={()=>toggleRow(id)}>{location.row===id?"收起":"核对"}{row.name || row.symbol}原评分与缺项</button>
      {location.row===id && <div id={`${prefix}-${encodeURIComponent(id)}`} onKeyDown={event=>{if(event.key==="Escape" && !event.nativeEvent.isComposing){event.preventDefault();event.stopPropagation();onLocation({...exactLocation,row:""});rowButtons.current.get(id)?.focus({preventScroll:true});}}}><RunDiagnostics row={row} identity={`${methodRunKey(identity)}:${id}`}/></div>}
    </li>)}</ul>}
    {location.row && !items.some(item=>item.id===location.row) && <p role="status" className="text-xs text-zinc-600 dark:text-zinc-400">链接指定的原行尚未读到；继续读取原批次，不按同股代码换行。</p>}
    <IncrementalSentinel sentinelRef={incremental.sentinelRef} visible={Math.min(incremental.visible,matching.length)} total={matching.length} onLoadMore={incremental.loadMore}/>
    <p className="text-[11px] text-zinc-600 dark:text-zinc-400">本批已读 {items.length} / {countText(run.coverage.total)} 条原行；筛选当前已读部分。{run.coverage.history_semantics}</p>
    {error && <p role="alert" className="text-xs text-rose-700 dark:text-rose-300">后续页读取失败：{error}。已读行保留，未换成最新批次。</p>}
    {hasRemote && <button type="button" data-action="secondary" disabled={loading} onClick={()=>void readNext()}>{loading?"读取原批次后续行…":error?"重试原批次后续行":"继续读取原批次"}</button>}
    <div className="flex flex-wrap gap-3 border-t border-zinc-200 pt-2 text-xs dark:border-zinc-700"><Link href={`/agent?area=research&tab=review&date=${encodeURIComponent(identity.trade_date)}`}>同日复盘</Link><Link href={identity.method==="daily_picks"?"/hunting?view=discover&sec=daily":"/hunting?view=discover&sec=candidates"}>另看当前名单（非本次历史）</Link></div>
    <p className="text-[10px] text-zinc-600 dark:text-zinc-400">个股链接打开当前行情详情；本页原分、缺项和理由继续绑定上述历史批次。原入选不等于委托或成交。</p>
    <ResearchDisclosure label="原批次身份与范围" identity={methodRunKey(identity)}><dl className="space-y-1 break-all text-[11px]"><div><dt>原运行ID</dt><dd>{identity.run_id}</dd></div><div><dt>原来源版本</dt><dd>{identity.source_version ?? "未记录，不虚构版本"}</dd></div><div><dt>原方法版本</dt><dd>{run.strategy_version || "未记录"}</dd></div><div><dt>原内容指纹</dt><dd>{identity.content_version}</dd></div></dl></ResearchDisclosure>
  </section>;
}

export function MethodRunReader({method}: {method: string}) {
  const [location,setLocation]=useState(()=>emptyLocation(method));
  const [scope,setScope]=useState(()=>emptyLocation(method));
  useEffect(()=>{const sync=()=>{const next=emptyLocation(method);setLocation(next);setScope(next);};window.addEventListener("popstate",sync);return()=>window.removeEventListener("popstate",sync);},[method]);
  const supported=method==="daily_picks" || method==="intraday_watch";
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
  useEffect(()=>{readMoreIndexRef.current=readMoreIndex;});
  useEffect(()=>{
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
    const result=await getStrategyMethodRun(identity!,{signal});
    if(!methodRunMatches(result,identity!))throw new Error("返回的日期、来源版本或内容指纹不符，未使用其他批次");
    return result;
  },{key,enabled:supported && !!identity && !invalidDate,marketHours:false,abortable:true});
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
    <RunFilters key={JSON.stringify([scope.date,scope.sourceVersion,scope.missingSource,scope.strategyVersion])} value={scope} onApply={next=>navigate(next,true)}/>
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
    {!explicit && !identity && !index.pending && !index.error && !invalidDate && <p className="text-xs text-zinc-600 dark:text-zinc-400">{moreIndexAvailable?"本次扫描没有匹配批次，仍有未读范围；可继续读取更早批次。":"所查扫描范围没有可读取的保存批次；不证明当日未运行或零入选。"}</p>}
    {detail.pending && !detail.data && <p role="status" className="text-xs text-zinc-600 dark:text-zinc-400">读取指定原批次…</p>}
    {detail.error != null && <p role="alert" className="text-xs text-rose-700 dark:text-rose-300">指定原批次读取失败：{errorText(detail.error)}。{detail.data?"保留已读原批次，当前重读未确认。":"未回落最新运行或当前名单。"}<button type="button" data-action="quiet" onClick={detail.refresh}>重试原批次</button></p>}
    {detail.data && identity && (detail.data.state==="saved" || detail.data.state==="partial") && <RunRows key={key} run={detail.data} identity={identity} location={location} onLocation={navigate}/>}
    {detail.data && !["saved","partial"].includes(detail.data.state) && <p role="status" className="text-xs text-zinc-600 dark:text-zinc-400">{detail.data.reason || "原批次内容不可读取，不代表没有运行。"}</p>}
  </section>;
}
