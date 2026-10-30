import {getJson} from "./internal";

export interface CompanyRelationIdentity {
  symbol: string;
  event_id: number;
  version_id: number;
  observation_id: number;
}

export type CompanyRelationChoice = "supported" | "contradicted" | "insufficient" | "unrelated";
export interface CompanyRelationItem {
  fact_id: string;
  type: "choice" | "refusal";
  choice?: CompanyRelationChoice;
  fact: {id: string; kind: "business" | "product" | "customer" | "order" | "risk";
    quote: string; document_title: string; url: string; published_at: string; period: string; page: number; sha256: string};
}

export interface CompanyRelationView extends CompanyRelationIdentity {
  state: "not_requested" | "ready" | "stale" | "historical_excluded" | "unavailable" | "invalid_receipt"
    | "reserved" | "started" | "interrupted" | "accounting_pending" | "not_started";
  protocol: "company-relation-decisions-v1";
  model: "gpt-6-luna";
  verification: "unvalidated_model_research";
  human_gold_count: 0;
  eligibility_changed: false;
  research_only: true;
  source_ready?: boolean;
  configured?: boolean;
  can_request?: boolean;
  reason_code?: string | null;
  task_id?: string | null;
  result: null | {items: CompanyRelationItem[]; coverage: {total: number; answered: number; refused: number};
    generated_at: string; valid_until: string; source_as_of: string; input_sha256: string; usage_id: string;
    elapsed_seconds: number; actual_cost_usd: null};
}

const STATES = new Set(["not_requested", "ready", "stale", "historical_excluded", "unavailable", "invalid_receipt",
  "reserved", "started", "interrupted", "accounting_pending", "not_started"]);
const CHOICES = new Set(["supported", "contradicted", "insufficient", "unrelated"]);
const KINDS = new Set(["business", "product", "customer", "order", "risk"]);
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown, max: number): value is string => typeof value === "string" && !!value.trim() && value.length <= max;
const clock = (value: unknown): value is string => typeof value === "string"
  && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  && Number.isFinite(Date.parse(value));
const hash = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);

export function companyRelationKey(value: CompanyRelationIdentity): string {
  return `${value.symbol}:${value.event_id}:${value.version_id}:${value.observation_id}`;
}

export function validCompanyRelationIdentity(value: CompanyRelationIdentity): boolean {
  return /^[0-9]{6}$/.test(value.symbol) && [value.event_id, value.version_id, value.observation_id]
    .every(id => Number.isSafeInteger(id) && id > 0);
}

function officialDocument(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "static.cninfo.com.cn" && !url.port
      && !url.username && !url.password && !url.search && !url.hash
      && /^\/finalpage\/\d{4}-\d{2}-\d{2}\/\d+\.(?:PDF|pdf)$/.test(url.pathname);
  } catch {return false;}
}

export function validateCompanyRelationView(raw: unknown, identity: CompanyRelationIdentity): CompanyRelationView {
  if (!object(raw) || raw.symbol !== identity.symbol || raw.event_id !== identity.event_id
    || raw.version_id !== identity.version_id || raw.observation_id !== identity.observation_id
    || raw.protocol !== "company-relation-decisions-v1" || raw.model !== "gpt-6-luna"
    || raw.verification !== "unvalidated_model_research" || raw.human_gold_count !== 0
    || raw.eligibility_changed !== false || raw.research_only !== true
    || raw.business_effect_verified !== false || raw.probabilities_are_accuracy !== false
    || typeof raw.state !== "string" || !STATES.has(raw.state)
    || (raw.can_request !== undefined && typeof raw.can_request !== "boolean")
    || (raw.source_ready !== undefined && typeof raw.source_ready !== "boolean")
    || (raw.configured !== undefined && typeof raw.configured !== "boolean")
    || (raw.can_request === true && (raw.state !== "not_requested" || raw.source_ready !== true || raw.configured !== true))
    || (raw.task_id != null && (typeof raw.task_id !== "string" || !/^[a-f0-9]{32}$/.test(raw.task_id)))) {
    throw new Error("关联核验的证券、版本或研究身份不匹配");
  }
  if (raw.state !== "ready") {
    if (raw.result !== null) throw new Error("未完成核验不能携带有效结论");
    return raw as unknown as CompanyRelationView;
  }
  const result = raw.result;
  if (!object(result) || !Array.isArray(result.items) || result.items.length < 1 || result.items.length > 12
    || !object(result.coverage) || !clock(result.generated_at) || !clock(result.valid_until)
    || !clock(result.source_as_of) || Date.parse(result.source_as_of) > Date.parse(result.generated_at)
    || Date.parse(result.generated_at) > Date.now() || Date.parse(result.valid_until) <= Date.now()
    || !hash(result.input_sha256) || typeof result.usage_id !== "string" || !/^[a-f0-9]{32}$/.test(result.usage_id)
    || typeof result.elapsed_seconds !== "number" || !Number.isFinite(result.elapsed_seconds) || result.elapsed_seconds < 0
    || result.actual_cost_usd !== null) throw new Error("关联核验的有效期或结果结构未通过核对");
  const ids = new Set<string>();
  for (const item of result.items) {
    if (!object(item) || !text(item.fact_id, 90) || ids.has(item.fact_id) || !object(item.fact)
      || item.fact.id !== item.fact_id || typeof item.fact.kind !== "string" || !KINDS.has(item.fact.kind)
      || !text(item.fact.quote, 1000) || !text(item.fact.document_title, 300) || !officialDocument(item.fact.url)
      || !clock(item.fact.published_at) || Date.parse(item.fact.published_at) > Date.parse(result.source_as_of)
      || !text(item.fact.period, 300) || !hash(item.fact.sha256)
      || !Number.isSafeInteger(item.fact.page) || Number(item.fact.page) < 1
      || (item.type !== "refusal" && (item.type !== "choice" || typeof item.choice !== "string" || !CHOICES.has(item.choice)))
      || (item.type === "refusal" && item.choice !== undefined)) throw new Error("关联核验的原文引用不完整");
    ids.add(item.fact_id);
  }
  const answered = result.items.filter(item => item.type === "choice").length;
  if (result.coverage.total !== result.items.length || result.coverage.answered !== answered
    || result.coverage.refused !== result.items.length - answered) throw new Error("关联核验覆盖数不匹配");
  return raw as unknown as CompanyRelationView;
}

export async function getCompanyRelationResearch(identity: CompanyRelationIdentity, signal?: AbortSignal): Promise<CompanyRelationView> {
  if (!validCompanyRelationIdentity(identity)) throw new Error("关联核验需要准确的A股事件身份");
  const query = new URLSearchParams({event_id: String(identity.event_id), version_id: String(identity.version_id),
    observation_id: String(identity.observation_id)});
  const value = (await getJson<unknown>(`/api/agent/research/company-relations/${identity.symbol}?${query}`, undefined, {signal})).data;
  return validateCompanyRelationView(value, identity);
}
