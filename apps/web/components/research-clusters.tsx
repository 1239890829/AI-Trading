"use client";

import {useId,useState} from "react";
import type {ResearchClustersPayload,ResearchThemeCluster} from "@/types/research-clusters";
import {useIncremental} from "@/hooks/use-incremental";
import {IncrementalSentinel} from "@/components/ui/incremental-sentinel";
import {StockLink} from "@/components/stock-link";
import {dateTimeTextBJ,pctColor,pctText} from "@/lib/format";
import styles from "./research-clusters.module.css";

function label(c:ResearchThemeCluster) {return c.support_scope === "industry_structure" ? c.state === "supported" ? "产业结构有据" : "结构候选" : c.state === "supported" ? "交易叙事有据" : "叙事候选";}
const MISSING:Record<string,string>={event_to_company_driver_not_verified:"事件到公司主营、产品或订单的传导尚未核实",full_theme_universe_not_observed:"仅观察涨停样本，缺完整成员及未上涨样本",common_constituent_and_market_effect_not_removed:"尚未剔除共同成分、大盘和行业影响",current_common_driver_not_verified:"当日共同催化尚未核实",shared_market_driver_not_verified:"共同市场驱动尚未核实",formal_catalog_status_unknown:"正式目录当前不可核对",formal_catalog_unavailable:"正式目录暂不可用",no_exact_formal_catalog_identity:"原标签没有精确同名的正式目录身份",source_quote_clock_unavailable:"来源未提供可核实的报价时刻",non_ths_reason_observation_excluded:"其他来源的归因未混入本次同花顺样本",source_trade_date_mismatch:"日期不符的来源记录已排除",unusable_source_quality:"质量不足的来源记录已排除",conflicting_duplicate_symbol_excluded:"同股冲突记录已排除",invalid_source_observation:"部分来源记录结构不完整",invalid_source_reason:"部分归因正文不可用",invalid_source_symbol:"部分证券身份未核",historical_cluster_not_recorded:"当日未保存研究簇",snapshot_corrupt_or_unreadable:"已存研究簇损坏或不可读取",snapshot_pointer_missing:"已存研究簇索引缺失",snapshot_pointer_target_missing:"已存研究簇原件缺失",snapshot_version_missing:"指定研究簇版本未保存"};
MISSING.source_trade_date_unknown="来源交易日未提供，无法核对样本日期";
MISSING.source_quality_unknown="来源质量未记录，未作为已核样本";
MISSING.formal_catalog_name_ambiguous="来源正式目录存在同名条目，身份不唯一";
function missingText(value:string){return MISSING[value] ?? (/^[a-z_]+$/.test(value)?"另有来源缺项，尚未核实":value);}
export function ResearchClusterBadges({data,symbol,theme}:{data?:ResearchClustersPayload;symbol?:string;theme?:string|null}) {
  const rows=data?.clusters.filter(c=>symbol ? c.stocks.some(s=>s.symbol === symbol) : !!theme && c.branches.some(b=>b.raw_name === theme)) ?? [];
  if (!rows.length) return null;
  const structure=rows.find(c=>c.type === "industry_chain"),narrative=rows.find(c=>c.type === "market_narrative");
  const compact=[structure,narrative].filter((c):c is ResearchThemeCluster=>!!c);
  const remainder=rows.filter(c=>!compact.some(shown=>shown.id === c.id));
  return <span className={styles.badges} aria-label="研究簇关联">{compact.map(c=><span key={c.id} className={`${styles.badge} ${c.state==="candidate"?styles.candidate:""}`} title={`${label(c)}；${c.driver}；${data?.reference_mode === "current" ? "当前研究参考，非原入选依据" : `已存观察 ${data?.trade_date}`}；不替换原正式题材。`}>{c.name} · {c.driver_state === "candidate" ? "当前驱动候选" : label(c)}</span>)}{remainder.length>0&&<span className={styles.badge} title={remainder.map(c=>`${c.name}：${label(c)}，${c.driver_state==="candidate"?"当前驱动候选":"共同驱动有据"}`).join("；")} aria-label={`另${remainder.length}项研究关联：${remainder.map(c=>c.name).join("、")}`}>+{remainder.length}关联</span>}</span>;
}
interface Props {data?:ResearchClustersPayload;pending?:boolean;error?:unknown;onRetry?:()=>void;
  selectedId?:string|null;onSelect?:(id:string|null)=>void;selectionSymbols?:readonly string[];historical?:boolean;}
/** Calm, inline evidence reader. All cards receive one owner's payload. */
export function ResearchClustersPanel({data,pending=false,error,onRetry,selectedId,onSelect,selectionSymbols,historical=false}:Props) {
  const scope=`${data?.trade_date}:${data?.version}:${data?.reference_mode}`;
  const [local,setLocal]=useState<{scope:string;id:string|null}>({scope,id:null});
  const [view,setView]=useState({scope,query:"",singles:false});
  const controls=view.scope === scope ? view : {scope,query:"",singles:false};
  const query=controls.query.trim().toLocaleLowerCase();
  const allRows=data?.clusters??[];
  const singleCount=allRows.filter(c=>c.state === "candidate" && c.unique_stock_count<=1).length;
  // This is presentation compression, never evidence admission or a strength rank.
  const visibleRows=allRows.filter(c=>query
    ? `${c.name} ${c.branches.map(b=>`${b.raw_name} ${b.official_identity?.code??""}`).join(" ")}`.toLocaleLowerCase().includes(query)
    : controls.singles || c.state === "supported" || c.unique_stock_count>1);
  const chosen=selectedId !== undefined ? selectedId : local.scope === scope ? local.id : null;
  const selected=data?.clusters.find(c=>c.id === chosen);
  const body=useId();
  const choose=(id:string|null)=>{if(onSelect)onSelect(id);else setLocal({scope,id});};
  const lists=useIncremental(visibleRows,{pageSize:6,step:12,resetKey:JSON.stringify([scope,query,controls.singles])});
  return <section className={styles.panel} aria-label="研究主题簇">
    <header className={styles.head}><h3>研究簇 · {historical ? "原记录" : data?.reference_mode === "saved" ? "已存观察" : "当前参考"}</h3>{onRetry&&<button type="button" className="quiet-action" disabled={pending} onClick={onRetry}>{pending?"读取中…":"重读研究簇"}</button>}</header>
    {pending&&!data?<p className={styles.empty} role="status">正在核对共同驱动与原始标签…</p>:null}
    {!!error&&<p role="alert" className={styles.warning}>{historical?"已存研究簇未通过日期或证据核对，未用于该报告。":`研究簇读取失败。${data?"保留上次观察，当前关系待核。":"不能据此判断没有主题。"}`}</p>}
    {data?.state === "not_recorded" ? !error&&<p role="status" className={styles.empty}>当时未记录研究簇；原题材、原指标保留，不用今天的关系补写。</p>
      : data?.state === "unavailable" ? <p role="status" className={styles.warning}>共同驱动证据暂不可用，原始概念仍可独立浏览。</p>
      : data&&<>
      <p className={styles.note}>{data.trade_date} · {data.observed_at?`观察 ${dateTimeTextBJ(data.observed_at)}（北京）` : "观察时点未提供"} · 涨停来源 {data.coverage.source_count}条，去重 {data.coverage.unique_stock_count}只；非全市场，报价源钟未核。</p>
      {!data.clusters.length?<p role="status" className={styles.empty}>本次样本没有可列出的研究簇；不等于市场没有共同主题。</p>:<>
      <div className={styles.tools}><label className={styles.search}><span className="sr-only">搜索全部原始题材标签</span><input aria-label="搜索全部原始题材标签" placeholder="搜索原标签（含单股线索）" value={controls.query} onChange={event=>{setView({...controls,scope,query:event.currentTarget.value});choose(null);}}/></label>
        {singleCount>0&&<button type="button" className="quiet-action" aria-pressed={controls.singles} onClick={()=>{setView({...controls,scope,singles:!controls.singles});choose(null);}}>单股线索（{singleCount}）</button>}
        {query&&<button type="button" className="quiet-action" onClick={()=>{setView({...controls,scope,query:""});choose(null);}}>清除搜索</button>}
      </div>
      <p className={styles.note}>研究观察顺序，非强度排名；多只观察不代表共同驱动已证实。</p>
      <div className={styles.choices} role="group" aria-label="研究簇切换"><button type="button" aria-pressed={!selected} onClick={()=>choose(null)}>{onSelect?"全部原名单":"全部主题"}</button>{lists.shown.map(c=>{
        const matched=selectionSymbols?new Set(c.stocks.filter(s=>selectionSymbols.includes(s.symbol)).map(s=>s.symbol)).size:undefined;
        return <button key={c.id} type="button" className={c.state==="candidate"?styles.candidate:""} aria-pressed={selected?.id === c.id} aria-controls={selected?.id === c.id?body:undefined} onClick={()=>choose(c.id)}>{c.name}<span className={styles.status}>{label(c)}{c.support_scope==="industry_structure"&&c.driver_state==="candidate"?" · 当前驱动候选":""}{matched !== undefined?` · 名单${matched}只`:` · 样本${c.unique_stock_count}只`}</span></button>;
      })}</div>
      {!visibleRows.length&&<p className={styles.empty} role="status">{query?"没有匹配的原始标签，可清除搜索。":"单股候选已收在“单股线索”中；未据此合并为共同主题。"}</p>}
      {lists.hasMore&&<IncrementalSentinel sentinelRef={lists.sentinelRef} visible={lists.visible} total={visibleRows.length} unit="项观察" onLoadMore={lists.loadMore}/>}
      {selected?<ClusterDetail key={`${scope}:${selected.id}`} cluster={selected} id={body}/>:<p className={styles.note}>点选核对分支、公司与依据。产业关联不等于同因上涨，簇不改变入选资格。</p>}
      </>}
      {data.missing.length>0&&<details className={styles.evidence}><summary>本次缺项 · {data.missing.length}项</summary><ul>{data.missing.map((v,i)=><li key={i} title={v}>{missingText(v)}</li>)}</ul></details>}
    </>}
  </section>;
}
function ClusterDetail({cluster:c,id}:{cluster:ResearchThemeCluster;id:string}) {
  const [branchIndex,setBranchIndex]=useState<number|null>(null);
  const branch=branchIndex === null ? null : c.branches[branchIndex];
  const observations=branch ? c.stocks.filter(stock=>branch.symbols.includes(stock.symbol)) : c.stocks;
  const stocks=useIncremental(observations,{pageSize:8,step:16,resetKey:JSON.stringify([c.id,branchIndex])});
  return <div id={id} className={styles.detail}>
    <div className={styles.fact}><strong>{c.name}</strong><span>{label(c)}</span><span>{c.driver_state === "supported"?"共同驱动有据":"共同驱动仍为候选"}</span></div>
    <p>{c.driver}</p>
    <div className={styles.branches} role="group" aria-label="保留的原始分支"><button type="button" className={styles.branch} aria-pressed={branchIndex===null} onClick={()=>setBranchIndex(null)}>全部分支</button>{c.branches.map((b,i)=><button type="button" className={styles.branch} aria-pressed={branchIndex===i} onClick={()=>setBranchIndex(i)} key={`${b.raw_name}:${i}`}><strong>{b.raw_name}</strong>{b.official_identity?<code>{b.official_identity.code}</code>:null}<small>{b.official_identity?`来源正式${b.official_identity.kind==="industry"?"行业":"概念"}`:b.catalog_mapping_state==="ambiguous"?"原始标签 · 正式目录同名不唯一":b.catalog_mapping_state==="unavailable"?"原始标签 · 正式目录未核":"来源原始题材标签"} · {b.relation==="partial"?"仅部分关联":b.relation==="candidate"?"关系候选":"关联分支"}</small></button>)}</div>
    <h4>{branch?`${branch.raw_name} · 观察股 ${observations.length}/${c.unique_stock_count}只`:`已观察公司 · 去重 ${c.unique_stock_count}只`}</h4>
    <ul className={styles.stocks}>{stocks.shown.map(s=><li key={s.symbol}><StockLink symbol={s.symbol}>{s.name||s.symbol}<small> {s.symbol}</small></StockLink><span className={pctColor(s.change_pct)}>{pctText(s.change_pct)}</span></li>)}</ul>
    {stocks.hasMore&&<IncrementalSentinel sentinelRef={stocks.sentinelRef} visible={stocks.visible} total={observations.length} unit="只" onLoadMore={stocks.loadMore}/>}
    <details className={styles.evidence}><summary>证据 {c.evidence.length} · 缺项 {c.missing.length} · 反证 {c.counter_evidence.length}</summary>
      <ul>{c.evidence.map(e=><li key={e.id}>{e.url?<a href={e.url} target="_blank" rel="noreferrer">{e.source}</a>:<strong>{e.source}</strong>}<span className={styles.meta}> · {e.published_at?`发布 ${/^\d{4}-\d{2}-\d{2}$/.test(e.published_at)?`${e.published_at}（仅日期）`:dateTimeTextBJ(e.published_at)}`:`接收 ${dateTimeTextBJ(e.observed_at)}（北京）`}</span><blockquote>{e.excerpt}</blockquote></li>)}</ul>
      {!!c.missing.length&&<><h4>还缺什么</h4><ul>{c.missing.map((v,i)=><li key={i} title={v}>{missingText(v)}</li>)}</ul></>}
      {!!c.counter_evidence.length&&<><h4>反证与其他解释</h4><ul>{c.counter_evidence.map((v,i)=><li key={i}>{v}</li>)}</ul></>}
      <h4>原始题材与理由</h4><ul>{c.stocks.map(s=><li key={s.symbol}>{s.name||s.symbol} · {s.raw_tags.join("、")||"未记录标签"}<blockquote>{s.reason||"未记录原理由"}</blockquote></li>)}</ul>
    </details>
    <p className={styles.note}>只作研究关联；未合并原代码、成分、板块涨幅或资金。涨停样本不能用于推算胜率。</p>
  </div>;
}
