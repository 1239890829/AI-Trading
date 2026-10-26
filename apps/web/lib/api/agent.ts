/**
 * AI 大脑：进化议程、参数变更、实验记录、知识库、策略健康度/登记册
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import { getJson, getJsonArray, sendJson } from "./internal";
import type {SavedMethodRunIdentity} from "./picks";

export type AgentTaskStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "canceled"
  | "needs_confirm";

export interface AgentTaskStep {
  index: number;
  name: string;
  input_summary: string;
  output_summary: string;
  duration_ms: number;
  ok: boolean;
  llm?: { model: string; prompt_hash: string; enhanced: boolean };
}

export interface AgentOutcome { label: string; note: string }

export type AgentCompanyEvidenceState =
  | "idle" | "queued" | "collecting" | "generating" | "ready" | "insufficient"
  | "source_failed" | "model_failed" | "invalid_response" | "budget_blocked"
  | "canceled" | "interrupted" | "disabled" | "historical_excluded";

export interface AgentCompanyEvidenceResult {
  symbol: string;
  state: AgentCompanyEvidenceState;
  cached?: boolean;
  result_key?: string | null;
}

export interface AgentTaskResult {
  kind: string;
  /** 公司任务返回批次和逐股状态，不生成单个产物 id。 */
  id?: string;
  state?: string;
  symbols?: AgentCompanyEvidenceResult[];
  /** 缺失表示未记录，不能当成零调用。 */
  model_calls?: number | null;
  runs?: SavedMethodRunIdentity[];
  reused?: boolean;
}

export interface AgentTask {
  outcome?: AgentOutcome | null;
  id: string;
  type: string;
  status: AgentTaskStatus;
  params: Record<string, unknown>;
  steps: AgentTaskStep[];
  result_ref: AgentTaskResult | null;
  error: { code: string; message: string; retryable: boolean } | null;
  risk_level: "L0" | "L1" | "L2";
  created_by: string;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested_at?: string | null;
  /** 只读留痕条目（议程自动执行，P1-14 留痕合一）：不可取消、不可重跑 */
  read_only?: boolean;
}

export interface AgentTaskType {
  type: string;
  label: string;
  risk: "L0" | "L1" | "L2";
  desc: string;
}

export async function getAgentTaskTypes(): Promise<AgentTaskType[]> {
  return getJsonArray<AgentTaskType>("/api/agent/task-types");
}

export async function getAgentTasks(type?: string): Promise<AgentTask[]> {
  const q = type ? `?type=${encodeURIComponent(type)}` : "";
  return getJsonArray<AgentTask>(`/api/agent/tasks${q}`);
}

export async function getAgentTask(id: string): Promise<AgentTask> {
  if (!/^[0-9a-f]{32}$/.test(id)) throw new Error("读取任务需要准确任务编号");
  const value = (await getJson<AgentTask>(`/api/agent/tasks/${id}`)).data;
  if (!value || value.id !== id) throw new Error("返回任务编号不符");
  return value;
}

export async function createAgentTask(type: string, params: Record<string, unknown> = {}): Promise<AgentTask> {
  return (await sendJson<AgentTask>("/api/agent/tasks", "POST", { type, params })).data;
}

export async function cancelAgentTask(id: string): Promise<AgentTask> {
  return (await sendJson<AgentTask>(`/api/agent/tasks/${id}/cancel`, "POST")).data;
}

/** Server-bound checkpoint only; runtime, grant and model fields are never supplied by the client. */
export async function recoverTradingAgentsResearch(sourceTaskId: string, runId: string): Promise<AgentTask> {
  if (![sourceTaskId, runId].every(value => typeof value === "string" && /^[0-9a-f]{32}$/.test(value))) throw new Error("恢复研究需要准确的原任务与运行编号");
  return (await sendJson<AgentTask>(`/api/agent/tasks/${sourceTaskId}/recover`, "POST", {run_id: runId})).data;
}

export type TradingAgentsReportKey = "market_report" | "sentiment_report" | "news_report" | "fundamentals_report"
  | "investment_plan" | "trader_investment_plan" | "final_trade_decision";

export const TRADING_AGENTS_ROLES = ["Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst",
  "Bull Researcher", "Bear Researcher", "Research Manager", "Trader", "Aggressive Analyst", "Conservative Analyst",
  "Neutral Analyst", "Portfolio Manager"] as const;
export type TradingAgentsRoleName = typeof TRADING_AGENTS_ROLES[number];
export interface TradingAgentsRoleOutput {
  role: TradingAgentsRoleName;
  call_id: number;
  output_sha256: string;
  fields: Record<string, unknown>;
}

export interface TradingAgentsRecoveryLineage {
  version: 1;
  source_task_id: string; source_run_id: string; recovery_task_id: string;
  source_method_sha256: string; recovery_method_sha256: string;
  reused_original_outputs: number; new_call_from: number; new_call_to: number;
  inherited_unknown_usage_count: number;
}

export interface TradingAgentsResearchReport {
  version: number; state: "completed"; symbol: string; run_id: string;
  generated_at: string; as_of: string; generated_today: boolean;
  upstream_sha: string; input_sha256: string; method_sha256: string;
  verification: "unverified_full_graph_research"; production_eligible: false;
  review_state: "unreviewed" | "finite_failed_requires_review";
  review_summary: {finite_failures: number; peer_review_required: number; source_verified: false; independent_human_gold: false};
  reports: Record<TradingAgentsReportKey, string>;
  roles?: TradingAgentsRoleOutput[];
  role_projection_state?: "available" | "unavailable";
  recovery_lineage?: TradingAgentsRecoveryLineage;
}

export interface TradingAgentsResearchView {
  startup_conditions_not_checked?: boolean;
  symbol: string; enabled: boolean; executable: boolean;
  blockers: {code: string; message: string}[];
  required_model_slots: number | null;
  budget: {date: string; limit: number | null; used: number; remaining: number | null; scope: string} | null;
  admission: {state: string; method_sha256: string | null; valid_until?: string};
  verification: "unverified_full_graph_research"; production_eligible: false;
  latest: TradingAgentsResearchReport | null;
  task: {id: string; status: AgentTaskStatus; cancel_requested: boolean; failed: boolean;
    created_at: string; finished_at: string | null; error: {code: string; message: string} | null;
    result_run_id?: string | null; reused_identical_selection?: boolean} | null;
  task_read_unavailable?: boolean;
  recovery?: {
    state: "not_checked" | "available" | "blocked" | "none" | "unknown";
    source_task_id: string | null;
    run_id: string | null;
    blockers: {code: string; message: string}[];
  };
}

/** Ordinary paired read. Opening this reader never creates a research task. */
export async function getTradingAgentsResearch(symbol: string, options: {savedOnly?: boolean; checkRecovery?: boolean} = {}): Promise<TradingAgentsResearchView> {
  if (!/^\d{6}$/.test(symbol)) throw new Error("完整研究需要六位证券代码");
  const params = new URLSearchParams();
  if (options.savedOnly && !options.checkRecovery) params.set("saved_only", "true");
  const data = (await getJson<TradingAgentsResearchView>(`/api/agent/research/tradingagents/${symbol}${options.checkRecovery ? "/recovery" : ""}${params.size ? `?${params}` : ""}`)).data;
  const keys: TradingAgentsReportKey[] = ["market_report", "sentiment_report", "news_report", "fundamentals_report", "investment_plan", "trader_investment_plan", "final_trade_decision"];
  const lineage = data?.latest?.recovery_lineage;
  const lineageKeys = ["version", "source_task_id", "source_run_id", "recovery_task_id", "source_method_sha256", "recovery_method_sha256",
    "reused_original_outputs", "new_call_from", "new_call_to", "inherited_unknown_usage_count"];
  const invalidLineage = lineage !== undefined && (!lineage || typeof lineage !== "object" || Array.isArray(lineage)
    || Object.keys(lineage).length !== lineageKeys.length || Object.keys(lineage).some(key => !lineageKeys.includes(key))
    || lineage.version !== 1
    || ![lineage.source_task_id, lineage.source_run_id, lineage.recovery_task_id].every(value => typeof value === "string" && /^[0-9a-f]{32}$/.test(value))
    || ![lineage.source_method_sha256, lineage.recovery_method_sha256].every(value => typeof value === "string" && /^[0-9a-f]{64}$/.test(value))
    || lineage.recovery_method_sha256 !== data.latest!.method_sha256
    || ![lineage.reused_original_outputs, lineage.inherited_unknown_usage_count].every(value => Number.isSafeInteger(value) && value >= 0 && value <= 10000)
    || !Number.isSafeInteger(lineage.new_call_from) || lineage.new_call_from < 1
    || !Number.isSafeInteger(lineage.new_call_to) || lineage.new_call_to < lineage.new_call_from);
  const recovery = data?.recovery;
  const invalidRecovery = recovery !== undefined && (!recovery || typeof recovery !== "object" || Array.isArray(recovery)
    || !["not_checked", "available", "blocked", "none", "unknown"].includes(recovery.state)
    || ![recovery.source_task_id, recovery.run_id].every(value => value === null || typeof value === "string" && /^[0-9a-f]{32}$/.test(value))
    || !Array.isArray(recovery.blockers) || recovery.blockers.some(row => !row || typeof row.code !== "string" || typeof row.message !== "string")
    || recovery.state === "available" && (!recovery.source_task_id || !recovery.run_id || recovery.blockers.length > 0)
    || recovery.state === "available" && !options.checkRecovery);
  if (!data || data.symbol !== symbol || typeof data.enabled !== "boolean" || typeof data.executable !== "boolean"
    || !Array.isArray(data.blockers) || data.blockers.some(row => !row || typeof row.code !== "string" || typeof row.message !== "string")
    || data.executable && (!data.enabled || data.blockers.length > 0)
    || !data.admission || typeof data.admission.state !== "string"
    || data.latest === undefined || data.task === undefined
    || data.verification !== "unverified_full_graph_research" || data.production_eligible !== false || invalidLineage || invalidRecovery
    || data.task?.result_run_id !== undefined && data.task.result_run_id !== null
      && (data.task.status !== "succeeded" || typeof data.task.result_run_id !== "string" || !/^[0-9a-f]{32}$/.test(data.task.result_run_id))
    || data.task?.reused_identical_selection !== undefined && (typeof data.task.reused_identical_selection !== "boolean"
      || data.task.reused_identical_selection && (data.task.status !== "succeeded" || !data.task.result_run_id))
    || data.latest && (data.latest.symbol !== symbol || data.latest.verification !== data.verification
      || data.latest.production_eligible !== false || !data.latest.reports
      || !["unreviewed", "finite_failed_requires_review"].includes(data.latest.review_state)
      || !data.latest.review_summary || data.latest.review_summary.source_verified !== false
      || data.latest.review_summary.independent_human_gold !== false
      || !Number.isSafeInteger(data.latest.review_summary.finite_failures) || data.latest.review_summary.finite_failures < 0
      || !Number.isSafeInteger(data.latest.review_summary.peer_review_required) || data.latest.review_summary.peer_review_required < 0
      || (data.latest.review_state === "unreviewed") !== (data.latest.review_summary.finite_failures === 0)
      || keys.some(key => typeof data.latest!.reports[key] !== "string" || !data.latest!.reports[key].trim()))) {
    throw new Error("完整研究响应身份或报告不完整，未展示");
  }
  const roles = data.latest?.roles;
  if (roles !== undefined && (!Array.isArray(roles) || roles.length !== TRADING_AGENTS_ROLES.length
      || new Set(roles.map(row => row?.role)).size !== TRADING_AGENTS_ROLES.length
      || roles.some(row => !row || !TRADING_AGENTS_ROLES.includes(row.role)
        || !Number.isSafeInteger(row.call_id) || row.call_id < 1
        || !/^[0-9a-f]{64}$/.test(row.output_sha256)
        || !row.fields || typeof row.fields !== "object" || Array.isArray(row.fields)))) {
    return {...data, latest: {...data.latest!, roles: undefined, role_projection_state: "unavailable"}};
  }
  return data;
}

export async function resolveAgentTask(
  id: string,
  outcome: "done" | "dismissed",
  note = "",
): Promise<AgentTask> {
  return (await sendJson<AgentTask>(`/api/agent/tasks/${id}/resolve`, "POST", { outcome, note })).data;
}

export interface AgentParamInfo {
  key: string;
  label: string;
  desc: string;
  risk: string;
  /** 参数形态：json（结构化，走各自 provider 通道）/ int / float（标量，走运行时覆盖层） */
  kind?: "json" | "int" | "float";
  /** 标量参数的值域（json 参数为 null） */
  range?: { min: number | null; max: number | null } | null;
  current: string;
  default: string;
}

export interface AgentParamChange {
  outcome?: AgentOutcome | null;
  rollback_receipt?: { runtime_value_restored: boolean; skipped_reason: string | null; runtime_refreshed: boolean | null } | null;
  id: number;
  key: string;
  before: unknown;
  after: unknown;
  source_type: string;
  source_id: string;
  evidence: Record<string, unknown> | null;
  status: "draft" | "shadow" | "shadow_rejected" | "applied" | "rolled_back";
  manual_apply_allowed?: boolean;
  apply_block_reason?: string | null;
  created_at: string | null;
  applied_at: string | null;
  rolled_back_at: string | null;
  /** 回滚归因（P1-15）：只记"回滚了"不记"为什么" ⇒ 存活率无法下钻 */
  rollback_reason: { code: string; note: string } | null;
  task_id: string | null;
}

export interface AgentParamSurvival {
  total_changes: number;
  applied: number;
  rolled_back: number;
  draft: number;
  shadow: number;
  /** 已裁决 = applied + rolled_back（存活率的分母） */
  decided: number;
  /** applied / decided；无已裁决变更时为 null（不编造 0） */
  survival_rate: number | null;
  /** applied 且当前值仍等于其 after（未被后续变更覆盖） */
  still_effective: number;
  /** applied 但已被后来者覆盖 —— 与 rolled_back 不同，状态上看不出来 */
  superseded: number;
  rollback_reasons: Record<string, number>;
  reason_labels: Record<string, string>;
  by_key: Record<string, { applied: number; rolled_back: number; draft: number; shadow: number }>;
  /** 样本不足（< 3 条已裁决）：数字照给，但明确标注不可用于判断 */
  insufficient: boolean;
  note: string;
}

export interface AgentRollbackReason {
  code: string;
  label: string;
}

export async function getAgentParams(): Promise<AgentParamInfo[]> {
  return getJsonArray<AgentParamInfo>("/api/agent/params");
}

export async function getAgentParamChanges(key?: string): Promise<AgentParamChange[]> {
  const q = key ? `?key=${encodeURIComponent(key)}` : "";
  return getJsonArray<AgentParamChange>(`/api/agent/params/changes${q}`);
}

export async function getAgentParamSurvival(): Promise<AgentParamSurvival> {
  return (await getJson<AgentParamSurvival>("/api/agent/params/survival")).data;
}

export async function getAgentRollbackReasons(): Promise<AgentRollbackReason[]> {
  return getJsonArray<AgentRollbackReason>("/api/agent/params/rollback-reasons");
}

export async function createAgentParamChange(
  key: string,
  after: string,
  opts: { source_type?: string; source_id?: string; evidence?: Record<string, unknown> } = {},
): Promise<AgentParamChange> {
  return (await sendJson<AgentParamChange>("/api/agent/params/change", "POST", {
    key, after, ...opts,
  })).data;
}

export async function applyAgentParamChange(changeId: number): Promise<AgentParamChange> {
  return (await sendJson<AgentParamChange>(`/api/agent/params/changes/${changeId}/apply`, "POST")).data;
}

export async function rollbackAgentParamChange(
  changeId: number,
  reason: { reason_code: string; note?: string } = { reason_code: "manual" },
): Promise<AgentParamChange> {
  return (await sendJson<AgentParamChange>(
    `/api/agent/params/changes/${changeId}/rollback`, "POST", reason,
  )).data;
}

export interface AgentAgendaItem {
  outcome?: AgentOutcome;
  execution_scope?: "shadow_only";
  runtime_applied?: boolean;
  review_required?: boolean;
  class: "A" | "B" | "C";
  finding: string;
  evidence: Record<string, unknown>;
  action: string;
  expected_effect: string;
  verification: string;
  priority: number;
  param?: { key: string; after: unknown };
  summary?: string;
  status: "pending" | "proposed" | "executed" | "deferred" | "rejected" | "failed";
  result: string;
}

export interface AgentAgenda {
  id: number;
  date: string;
  status: "generating" | "ready" | "executed" | "failed" | "skipped";
  inputs: Record<string, unknown>;
  items: AgentAgendaItem[];
  budget: Record<string, unknown>;
  error: { code: string; message: string } | null;
  created_at: string | null;
  finished_at: string | null;
}

export async function getAgentAgenda(date?: string): Promise<AgentAgenda | null> {
  const q = date ? `?date=${encodeURIComponent(date)}` : "";
  return (await getJson<AgentAgenda | null>(`/api/agent/agenda${q}`)).data;
}

export async function getAgentAgendas(limit = 14): Promise<AgentAgenda[]> {
  return getJsonArray<AgentAgenda>(`/api/agent/agendas?limit=${limit}`);
}

export async function runAgentAgenda(): Promise<AgentAgenda> {
  return (await sendJson<AgentAgenda>("/api/agent/agenda/run", "POST", {}, 180_000)).data;
}

export interface AgentExperiment {
  outcome?: AgentOutcome;
  id: number;
  change_id: number;
  param_key: string;
  hypothesis: string;
  baseline: { status?: string; win_rate?: number | null; mean_excess?: number | null; taken_at?: string };
  verification_date: string | null;
  status: "running" | "concluded" | "rolled_back" | "concluded_insufficient";
  result: { conclusion: string; win_rate_delta?: number; current?: Record<string, unknown>; rollback?: unknown } | null;
  extensions: number;
  created_at: string | null;
  concluded_at: string | null;
}

export async function getAgentExperiments(): Promise<AgentExperiment[]> {
  return getJsonArray<AgentExperiment>("/api/agent/experiments");
}

export type KbTier = "canonical" | "current" | "history" | "timeline";

export interface KbFileMeta {
  path: string;
  name: string;
  dir: string;
  /** 分层标识：决定面板默认展开还是折进「历史与日志」 */
  tier: KbTier;
  size: number;
  /** 文件内包含的 KB-ID（[[KB-XXX]] 关联跳转索引） */
  kb_ids: string[];
}

export async function getAgentKbTree(): Promise<{ root: string; files: KbFileMeta[] }> {
  return (await getJson<{ root: string; files: KbFileMeta[] }>("/api/agent/kb/tree")).data;
}

export async function getAgentKbFile(path: string): Promise<{ path: string; content: string }> {
  const q = new URLSearchParams({ path });
  return (await getJson<{ path: string; content: string }>(`/api/agent/kb/file?${q.toString()}`)).data;
}
