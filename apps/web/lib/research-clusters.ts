import {ApiError} from "./api/client";
import {officialThemeIdentity} from "./theme-identity";
import type {ResearchClustersPayload} from "@/types/research-clusters";

const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === "string" && !!v.trim();
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(text);
const clock = (v: unknown) => typeof v === "string" && /(?:Z|[+-]\d{2}:?\d{2})$/i.test(v) && Number.isFinite(Date.parse(v));
const integer = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) >= 0;
const symbols = (v: unknown): v is string[] => Array.isArray(v) && v.every(s=>typeof s === "string" && /^\d{6}$/.test(s)) && new Set(v).size === v.length;
const unique = (v: unknown[]) => v.every(x=>object(x)&&text(x.id)) && new Set(v.map(x=>(x as {id:string}).id)).size === v.length;

export function researchDate(date: string): string {
  const value=date.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3");
  const parsed=new Date(`${value}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0,10) !== value) throw new ApiError(422,"研究簇日期无效","invalid_cluster_date");
  return value;
}
export function validateResearchClusters(value: unknown, expected: {date: string; version?: string; historical?: boolean}): ResearchClustersPayload {
  const fail=():never=>{throw new ApiError(502,"研究簇日期、版本或证据结构不符","bad_research_clusters");};
  if (!object(value) || value.scope !== "research_theme_clusters" || value.trade_date !== researchDate(expected.date)
    || !text(value.methodology_version) || !(value.version === null || typeof value.version === "string" && /^[a-f0-9]{64}$/.test(value.version))
    || !(value.observed_at === null || clock(value.observed_at)) || !["current","saved","not_recorded"].includes(String(value.reference_mode))
    || !["ready","partial","unavailable","not_recorded"].includes(String(value.state)) || !strings(value.missing)
    || !Array.isArray(value.clusters) || !object(value.coverage)
    || value.coverage.source_scope !== "limit_up_sample" || value.coverage.complete_universe !== false
    || ![value.coverage.source_count,value.coverage.tagged_count,value.coverage.unique_stock_count].every(integer)
    || expected.version && value.version !== expected.version && value.state !== "not_recorded"
    || expected.historical && value.reference_mode === "current") return fail();
  if (value.state === "not_recorded" && (value.reference_mode !== "not_recorded" || value.clusters.length || value.version !== null || value.observed_at !== null)) return fail();
  if (value.clusters.length && (!clock(value.observed_at) || value.version === null || !unique(value.clusters as {id:string}[]))) return fail();
  for (const c of value.clusters) {
    if (!object(c) || !text(c.id) || !text(c.name) || !["industry_chain","market_narrative"].includes(String(c.type))
      || !["supported","candidate"].includes(String(c.state)) || !["industry_structure","market_narrative"].includes(String(c.support_scope))
      || !["candidate","supported"].includes(String(c.driver_state)) || !text(c.driver) || c.change_pct !== null
      || !Array.isArray(c.branches) || !Array.isArray(c.stocks) || !Array.isArray(c.evidence)
      || !strings(c.missing) || !strings(c.counter_evidence) || !integer(c.unique_stock_count)) return fail();
    const stockSymbols=c.stocks.map((s: unknown)=>object(s)?s.symbol:null);
    if (!symbols(stockSymbols) || stockSymbols.length !== c.unique_stock_count || !unique(c.evidence as {id:string}[])) return fail();
    for (const s of c.stocks) if (!object(s) || !(s.name === null || typeof s.name === "string") || !strings(s.raw_tags) || typeof s.reason !== "string" || !text(s.source)
      || !(s.change_pct === null || typeof s.change_pct === "number" && Number.isFinite(s.change_pct))) return fail();
    for (const e of c.evidence) if (!object(e) || !text(e.id) || !text(e.source) || !clock(e.observed_at) || !text(e.excerpt) || !text(e.evidence_type)
      || !(e.published_at === null || clock(e.published_at) || typeof e.published_at === "string" && /^\d{4}-\d{2}-\d{2}$/.test(e.published_at)) || !(e.url === null || typeof e.url === "string" && /^https?:\/\//.test(e.url))) return fail();
    const evidenceIds=new Set(c.evidence.map((e:{id:string})=>e.id));
    for (const b of c.branches) if (!object(b) || !text(b.raw_name) || !["whole","partial","candidate"].includes(String(b.relation)) || !symbols(b.symbols)
      || b.symbols.some(s=>!stockSymbols.includes(s)) || !strings(b.evidence_ids) || b.evidence_ids.some(id=>!evidenceIds.has(id))
      || b.catalog_mapping_state !== undefined && !["exact","ambiguous","unmapped","unavailable"].includes(String(b.catalog_mapping_state))
      || b.missing !== undefined && !strings(b.missing)
      || !(b.official_identity === null || officialThemeIdentity(b.raw_name,b.official_identity,"exact"))) return fail();
  }
  return value as unknown as ResearchClustersPayload;
}

/** Explicit absence is different from a newly computed reference for an old report. */
export function unrecordedResearchClusters(date: string): ResearchClustersPayload {
  return {scope:"research_theme_clusters",methodology_version:"not_recorded",version:null,observed_at:null,trade_date:researchDate(date),reference_mode:"not_recorded",state:"not_recorded",clusters:[],coverage:{source_scope:"limit_up_sample",source_count:0,tagged_count:0,unique_stock_count:0,complete_universe:false},missing:["原记录未保存研究簇版本"]};
}

export function clusterSelection<T extends {symbol:string}>(rows: readonly T[], data: ResearchClustersPayload | undefined, id: string | null): readonly T[] {
  const selected=id ? data?.clusters.find(cluster=>cluster.id === id) : undefined;
  if (!selected) return rows;
  const included=new Set(selected.stocks.map(stock=>stock.symbol));
  return rows.filter(row=>included.has(row.symbol));
}
