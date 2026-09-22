/**
 * AI 大脑：进化议程、参数变更、实验记录、知识库、策略健康度/登记册
 *
 * `lib/api.ts` 的内部切片（IMP-005，2026-09-15 从 2751 行单文件按业务域拆出）。
 * **只搬位置、不重写**：声明正文与拆分前逐字符相同；对外仍由 `lib/api.ts` 统一转发，
 * 因此引用方（`@/lib/api`）**零改动**。
 */

import { getJson, getJsonArray, sendJson } from "./internal";

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

export interface AgentTask {
  id: string;
  type: string;
  status: AgentTaskStatus;
  params: Record<string, unknown>;
  steps: AgentTaskStep[];
  result_ref: { kind: string; id: string } | null;
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

export async function createAgentTask(type: string, params: Record<string, unknown> = {}): Promise<AgentTask> {
  return (await sendJson<AgentTask>("/api/agent/tasks", "POST", { type, params })).data;
}

export async function cancelAgentTask(id: string): Promise<AgentTask> {
  return (await sendJson<AgentTask>(`/api/agent/tasks/${id}/cancel`, "POST")).data;
}

export async function resolveAgentTask(
  id: string,
  outcome: "done" | "dismissed",
  note = "",
): Promise<AgentTask> {
  return (await sendJson<AgentTask>(`/api/agent/tasks/${id}/resolve`, "POST", { outcome, note })).data;
}

export interface AgentBubble {
  id: number;
  event_id: number;
  verdict: string;
  reason: string;
  /** 股票名称（判读合并，缺名称的判读不会进入气泡） */
  name?: string | null;
  /** llm=AI 判读 / rules=确定性去重 / llm_fallback=LLM 不可用按规则提醒（界面须标注） */
  model: string;
  acked: boolean;
  symbol: string;
  trigger_value: number | null;
  threshold: number | null;
  created_at: string | null;
}

export async function getAgentBubbles(limit = 5): Promise<AgentBubble[]> {
  return getJsonArray<AgentBubble>(`/api/agent/triage/pending?limit=${limit}`);
}

export async function ackAgentTriage(id: number): Promise<boolean> {
  return (await sendJson<{ ok: boolean }>(`/api/agent/triage/${id}/ack`, "POST")).data.ok;
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
