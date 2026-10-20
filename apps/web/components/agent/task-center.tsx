"use client";

import { useResource } from "@/hooks/use-resource";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  cancelAgentTask,
  createAgentTask,
  getAgentTasks,
  getAgentTaskTypes,
  resolveAgentTask,
  type AgentTask,
  type AgentTaskStatus,
  type AgentTaskType,
} from "@/lib/api";
import { dateTimeTextBJ } from "@/lib/format";
import {ConditionList, ExplanationMap, FactTimeline, NarrativeText} from "@/components/ui/explanation-map";
import {TradingAgentsResearchPanel} from "@/components/detail/tradingagents-research-panel";

/**
 * AI 控制台 · 任务中心（docs/summary/ai-evolution.md P0）。
 *
 * 首批只暴露 L0 只读/生成类任务；每个任务可展开看**步骤轨迹**（可追溯三件套
 * 第一件：做过什么、依据什么、花了多久、是否经 LLM 增强）。
 *
 * 三态纪律：`types === undefined` 骨架 / `null` 显式错误态 / 有值渲染；
 * 状态徽标只有终态与异常态上色，running 用中性色（避免每秒闪色）。
 */

const STATUS_META: Record<AgentTaskStatus, { label: string; cls: string }> = {
  queued: { label: "排队中", cls: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400" },
  running: { label: "执行中", cls: "bg-sky-500/10 text-sky-700 dark:text-sky-300" },
  succeeded: { label: "成功", cls: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" },
  failed: { label: "失败", cls: "bg-red-500/10 text-red-700 dark:text-red-300" },
  canceled: { label: "已取消", cls: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400" },
  needs_confirm: { label: "待确认", cls: "bg-amber-500/10 text-amber-800 dark:text-amber-300" },
};

/**
 * 服务侧登记类条目的标签（P1-14 议程留痕合一 / P1-36 告警升级待办）。
 * 它们**不在** task-types 里——不是可创建的任务类型（没有执行体），只作为
 * 待办/留痕出现在同一时间线。标签在此兜底，避免列表里显示成裸 type 名。
 */
const REGISTRY_LABELS: Record<string, string> = {
  agenda: "每日进化议程（自动执行）",
  mutation: "系统变更留痕",
  escalation: "告警升级待办",
};

function taskLabel(type: string, types: AgentTaskType[] | undefined | null): string {
  return types?.find((x) => x.type === type)?.label
    ?? (type === "company_evidence" ? "采集公司依据与判读" : type === "tradingagents_research" ? "TradingAgents完整研究" : REGISTRY_LABELS[type] ?? type);
}

function taskStatusLabel(task: AgentTask): string {
  return task.outcome?.label ?? (task.type === "company_evidence" && task.status === "succeeded"
    ? "处理完成" : STATUS_META[task.status].label);
}

const COMPANY_RESULT_LABELS: Record<string, string> = {
  idle: "尚未开始", queued: "排队中", collecting: "采集公司资料中", generating: "生成经营解释中",
  ready: "经营解释已保存", insufficient: "依据不足", source_failed: "来源采集失败",
  model_failed: "模型调用失败", invalid_response: "模型回答未通过校验", budget_blocked: "预算阻止调用",
  canceled: "已取消", interrupted: "处理中断", disabled: "自动采集已停用",
  historical_excluded: "历史时点资料不可用",
};

function CompanyTaskResult({task}: {task: AgentTask}) {
  const result = task.result_ref?.kind === "company_evidence" ? task.result_ref : null;
  const batchLabels: Record<string, string> = {
    processed: "本批处理已结束", no_candidates: "本轮没有当前候选",
    no_due_candidates: "当前候选均未到处理时点", disabled: "自动采集已停用",
    failed: "本批处理失败", canceled: "本批已取消", interrupted: "本批处理中断",
  };
  const seen = new Set<string>();
  const rows = (Array.isArray(result?.symbols) ? result.symbols : []).filter(row => {
    if (!row || typeof row.symbol !== "string" || !/^\d{6}$/.test(row.symbol) || seen.has(row.symbol)) return false;
    seen.add(row.symbol);
    return true;
  }).slice(0, 8);
  const calls = typeof result?.model_calls === "number" && Number.isInteger(result.model_calls) && result.model_calls >= 0
    ? `${result.model_calls} 次` : "未记录";
  return <section aria-label="公司资料任务处理结果" className="mb-3 space-y-1.5 rounded-md border border-zinc-200 px-2 py-1.5 text-[11px] text-zinc-700 dark:border-zinc-800 dark:text-zinc-300">
    <h4 className="font-medium">公司资料任务处理结果</h4>
    <p>{result ? batchLabels[result.state ?? ""] ?? "本批结果状态未确认" : "尚未保存本批逐股处理结果"}。</p>
    <p>模型调用：{calls}。</p>
    {rows.length > 0 ? <ul aria-label="逐股处理状态" className="space-y-1">{rows.map(row => <li key={row.symbol}>
      <span className="font-mono tabular-nums">{row.symbol}</span> · {typeof row.state === "string" ? COMPANY_RESULT_LABELS[row.state] ?? "状态未确认" : "状态未确认"}
      {row.cached === true && <span> · 复用已存结果</span>}
    </li>)}</ul> : result && result.state === "processed" ? <p>未保存逐股处理结果，不能确认本批个股判读状态。</p> : null}
    <p className="text-zinc-600 dark:text-zinc-400">任务处理完成不代表所有个股判读通过。经营解释仅供当前研究，未验证实际效果，不改变排序或入选资格。</p>
  </section>;
}

/** params 键的中文标签（登记类条目的正文靠它才读得懂；未知键回退原样显示）。 */
const PARAM_LABEL: Record<string, string> = {
  summary: "摘要",
  reason: "判读理由",
  symbol: "标的",
  rule: "规则",
  trigger_value: "触发值",
  event_id: "告警事件",
  source: "来源",
  kind: "类型",
  result: "结果",
  trade_date: "交易日",
  agenda_date: "议程日期",
};

// 2026-09-12 方案 A：agent 域时间戳已统一存**北京 naive**（原 UTC naive 已迁移），
// 但 naive 串仍不能直接 `new Date(...)`（按运行环境时区解释，海外/CI 错位）。
// 用 `dateTimeTextBJ`（naive 补 +08:00、固定 Asia/Shanghai 输出），格式保持 `MM-DD HH:mm` 不变。
const timeText = dateTimeTextBJ;

export function TaskCenter() {
  const [taskReading, setTaskReading] = useState(false);
  const taskRead = useRef({ generation: 0, reading: false });
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const readTasks = useCallback(async () => {
    const generation = ++taskRead.current.generation;
    taskRead.current.reading = true;
    setTaskReading(true);
    try {
      return await getAgentTasks();
    } finally {
      if (generation === taskRead.current.generation) {
        taskRead.current.reading = false;
        if (mounted.current) setTaskReading(false);
      }
    }
  }, []);
  // 三态：undefined=尚未拉到（骨架）/ null=拉过且失败（错误态）/ 有值=渲染
  const typesResource = useResource(getAgentTaskTypes, { marketHours: false });
  const tasksResource = useResource(readTasks, { marketHours: false });
  const types = typesResource.data ?? (typesResource.error ? null : undefined);
  const tasks = tasksResource.data ?? (tasksResource.error ? null : undefined);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState<string | null>(null);
  const [paramsText, setParamsText] = useState("");
  const [resolving, setResolving] = useState(false);
  const resolveLock = useRef(false);
  const [resolveReceipts, setResolveReceipts] = useState<Record<string, { outcome: "done" | "dismissed"; task: AgentTask }>>({});

  // 有任务在跑时按 3s 轮询（无 running 则停），避免无谓请求
  const hasRunning = useMemo(
    () => (tasks ?? []).some((t) => t.status === "queued" || t.status === "running"),
    [tasks],
  );
  // 只有一个任务读取 owner。结算后按需校准，避免 interval 改变导致额外立即请求。
  // 首次失败与后续失败都继续重试；静态入口不随任务进度重复读取。
  const { data: taskData, error: taskError, pending: taskPending, refresh: refreshTasks } = tasksResource;
  useEffect(() => {
    if (taskReading || taskPending || (taskData !== undefined && !taskError && !hasRunning)) return;
    const timer = setTimeout(() => {
      if (!taskRead.current.reading && document.visibilityState !== "hidden") refreshTasks();
    }, 3_000);
    return () => clearTimeout(timer);
  }, [taskData, taskError, taskPending, taskReading, hasRunning, refreshTasks]);

  function refresh() {
    setError(null);
    typesResource.refresh();
    refreshTasks();
  }

  async function submit(type: string) {
    setCreating(type);
    setError(null);
    let params: Record<string, unknown> = {};
    if (paramsText.trim()) {
      try {
        params = JSON.parse(paramsText) as Record<string, unknown>;
      } catch {
        setError("参数必须是合法 JSON 对象（如 {\"trade_date\": \"20260908\"}）");
        setCreating(null);
        return;
      }
    }
    try {
      const t = await createAgentTask(type, params);
      setSelected(t.id);
      setParamsText("");
      refreshTasks();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(null);
    }
  }

  async function cancel(id: string) {
    try {
      await cancelAgentTask(id);
      setError(null);
      refreshTasks();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  /** 处置待办（P1-36）：done=已处置 / dismissed=判定无需处理 */
  async function resolve(id: string, outcome: "done" | "dismissed") {
    const task = (tasks ?? []).find(t => t.id === id);
    if (resolveLock.current || taskRead.current.reading || taskPending || taskError != null || resolveReceipts[id] || !task || task.read_only || task.status !== "needs_confirm") return;
    resolveLock.current = true;
    setResolving(true);
    try {
      const response = await resolveAgentTask(id, outcome);
      if (!mounted.current) return;
      // POST 回执与后续持久读取分开；旧待办不能在校准慢/失败时再次处置。
      setResolveReceipts(prev => ({ ...prev, [id]: { outcome, task: response } }));
      setError(null);
      refreshTasks();
    } catch (e) {
      if (!mounted.current) return;
      setError(e instanceof Error ? e.message : String(e));
      // 网络失败无法证明处置未落地，先重新读取再提供操作资格。
      refreshTasks();
    } finally {
      resolveLock.current = false;
      if (mounted.current) setResolving(false);
    }
  }

  const detail = (tasks ?? []).find((t) => t.id === selected) ?? null;

  // 登记类条目（escalation 待办 / mutation 留痕）的正文就在 params 里——不渲染
  // 等于「待办没有内容」。只取标量：议程条目的 params.items 是数组，JSON 平铺
  // 会把面板淹掉（它的内容已在步骤轨迹里）。
  const paramRows = Object.entries(detail?.params ?? {}).filter(
    ([, v]) => typeof v === "string" || typeof v === "number",
  );
  const companySymbols = detail?.type === "company_evidence" && Array.isArray(detail.params.symbols)
    ? [...new Set(detail.params.symbols.filter((v): v is string => typeof v === "string" && /^\d{6}$/.test(v)))].slice(0, 8) : [];
  const invalidCompanySymbols = detail?.type === "company_evidence" && Array.isArray(detail.params.symbols)
    && (detail.params.symbols.some(v => typeof v !== "string" || !/^\d{6}$/.test(v))
      || detail.params.symbols.length > 8);
  const needsDisposition = detail !== null && !detail.read_only && detail.status === "needs_confirm";
  const resolveReceipt = detail ? resolveReceipts[detail.id] : undefined;
  const actionable = needsDisposition && !taskReading && !taskPending && taskError == null && !resolving && !resolveReceipt;
  const receiptCalibrated = resolveReceipt && detail && !taskReading && !taskPending && taskError == null && detail.status !== "needs_confirm" && detail.status === resolveReceipt.task.status;
  const traceSize = Math.max(1, Math.ceil((detail?.steps.length ?? 0) / 6));
  const traceGroups = detail ? Array.from({length:Math.ceil(detail.steps.length / traceSize)},(_,index)=>detail.steps.slice(index*traceSize,(index+1)*traceSize)) : [];

  return (
    <div className="task-center-layout h-full min-h-0">
      <div className="task-center-columns flex h-full min-h-0 gap-4">
      {/* 左：任务列表 */}
      <div className="task-center-list flex min-h-0 min-w-0 shrink-0 flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="text-xs text-zinc-600 dark:text-zinc-400">任务列表</span>
          <button data-action="quiet"
            onClick={refresh}
          >
            刷新
          </button>
        </div>
        {taskError != null && tasks != null && (
          <p role="alert" className="text-[11px] text-amber-700 dark:text-amber-300">
            任务刷新失败。上次读取结果，当前任务状态未确认；将自动重试。
          </p>
        )}
        <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
          {tasks === undefined ? (
            <div className="space-y-1.5">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-12 w-full animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-800" />
              ))}
            </div>
          ) : tasks === null ? (
            <p className="rounded-lg border border-dashed border-zinc-200 p-3 text-[11px] text-zinc-600 dark:text-zinc-400 dark:border-zinc-700">
              任务列表加载失败（后端未启动或接口异常），将自动重试。
            </p>
          ) : tasks.length === 0 ? (
            <p className="rounded-lg border border-dashed border-zinc-200 p-3 text-[11px] text-zinc-600 dark:text-zinc-400 dark:border-zinc-700">
              暂无任务。选择新建区的任务类型创建（首批为 L0 只读/生成类）。
            </p>
          ) : (
            tasks.map((t) => (
              <button
                key={t.id}
                onClick={() => setSelected(t.id)}
                className={`w-full rounded-lg border px-2.5 py-2 text-left transition-colors ${
                  selected === t.id
                    ? "border-zinc-400 bg-zinc-50 dark:border-zinc-500 dark:bg-zinc-800"
                    : "border-zinc-200 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-800/60"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-xs font-medium text-zinc-800 dark:text-zinc-100">
                    {taskLabel(t.type, types)}
                  </span>
                  <span className={`shrink-0 rounded px-1 py-0.5 text-[10px] ${t.outcome ? STATUS_META.needs_confirm.cls : STATUS_META[t.status].cls}`}>
                    {taskStatusLabel(t)}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-zinc-600 dark:text-zinc-400">
                  {t.read_only ? (
                    <span className="rounded bg-zinc-500/10 px-1 text-zinc-600 dark:text-zinc-400" title="系统自动执行的留痕条目：只读，不可取消">
                      只读留痕
                    </span>
                  ) : (
                    <span className="font-mono">{t.risk_level}</span>
                  )}
                  <span>·</span>
                  <span>{timeText(t.created_at)}</span>
                  {t.steps.length > 0 && <span>· {t.steps.length} 步</span>}
                </div>
              </button>
            ))
          )}
        </div>
      </div>

      {/* 右：新建 + 详情 */}
      <div className="task-center-detail flex min-h-0 min-w-0 flex-1 flex-col gap-4">
        {/* 新建任务 */}
        <section className="ui-card shrink-0 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-xs font-medium text-zinc-700 dark:text-zinc-200">新建任务</h3>
            <span className="text-[10px] text-zinc-600 dark:text-zinc-400">首批仅 L0 只读/生成类；写类任务在参数配置模块（P1）开放</span>
          </div>
          {typesResource.error != null && types != null && <p role="alert" className="mb-2 text-[11px] text-amber-700 dark:text-amber-300">任务入口刷新失败，下方为上次读取结果。请点击“刷新”重试。</p>}
          {types === undefined ? (
            <div role="status" aria-label="正在读取任务入口" className="h-8 w-full animate-pulse rounded bg-zinc-100 dark:bg-zinc-800" />
          ) : types === null ? (
            <p className="text-[11px] text-zinc-600 dark:text-zinc-400">任务入口读取失败，暂时无法创建。请点击任务列表的“刷新”重试。</p>
          ) : types.length === 0 ? (
            <p className="text-[11px] text-zinc-600 dark:text-zinc-400">暂无可创建的任务。</p>
          ) : (
            <div className="flex flex-wrap items-center gap-1.5">
              {types.map((t) => (
                <button data-action="command"
                  key={t.type}
                  disabled={creating !== null}
                  onClick={() => void submit(t.type)}
                  title={t.desc}
                >
                  {creating === t.type ? "提交中…" : t.label}
                  <span className="ml-1 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{t.risk}</span>
                </button>
              ))}
            </div>
          )}
          {Boolean(types?.length) && <label className="mt-3 block text-[11px] text-zinc-600 dark:text-zinc-400">可选任务参数（JSON）
          <input
            value={paramsText}
            onChange={(e) => setParamsText(e.target.value)}
            placeholder='可选参数（JSON），如 {"trade_date": "20260908"}；留空用默认'
            className="mt-2 w-full rounded-md border border-zinc-200 bg-transparent px-2 py-1 font-mono text-[11px] text-zinc-700 outline-none placeholder:text-zinc-400 dark:border-zinc-700 dark:text-zinc-200"
          />
          </label>}
        </section>

        {/* 任务详情 */}
        <section className="ui-card min-h-0 flex-1 overflow-y-auto rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          {error && (
            <p className="mb-2 rounded-md bg-red-500/5 px-2 py-1.5 text-[11px] text-red-700 dark:text-red-300">{error}</p>
          )}
          {detail === null ? (
            <p className="text-[11px] text-zinc-600 dark:text-zinc-400">选择任务列表中的条目查看执行轨迹；或先创建一个任务。</p>
          ) : (
            <>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-zinc-900 dark:text-zinc-50">
                    {taskLabel(detail.type, types)}
                  </span>
                  <span className={`rounded px-1.5 py-0.5 text-[10px] ${detail.outcome ? STATUS_META.needs_confirm.cls : STATUS_META[detail.status].cls}`}>
                    {taskStatusLabel(detail)}
                  </span>
                  {detail.read_only && (
                    <span className="rounded bg-zinc-500/10 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">
                      自动执行 · 只读
                    </span>
                  )}
                </div>
                {!detail.read_only && (detail.status === "running" || detail.status === "queued") && (
                  <button data-action="danger"
                    disabled={Boolean(detail.cancel_requested_at)}
                    onClick={() => void cancel(detail.id)}
                    className="disabled:cursor-not-allowed"
                  >
                    {detail.cancel_requested_at ? "取消中…" : "取消任务"}
                  </button>
                )}
                {needsDisposition && (
                  <div className="flex shrink-0 items-center gap-1.5">
                    <button data-action="command"
                      disabled={!actionable}
                      onClick={() => void resolve(detail.id, "done")}
                    >
                      已处置
                    </button>
                    <button data-action="quiet"
                      disabled={!actionable}
                      onClick={() => void resolve(detail.id, "dismissed")}
                    >
                      忽略
                    </button>
                  </div>
                )}
              </div>

              <dl className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-zinc-600 dark:text-zinc-400">
                <div className="flex gap-1">
                  <dt>{detail.read_only ? "议程日期" : "任务 ID"}</dt>
                  {/* 只读条目 ID 是 `agenda:2026-09-10`，截断成 "agenda:2026-" 没信息量 */}
                  <dd className="truncate font-mono text-zinc-700 dark:text-zinc-200">
                    {detail.read_only
                      ? String(detail.params?.agenda_date ?? detail.id)
                      : detail.id.slice(0, 12)}
                  </dd>
                </div>
                <div className="flex gap-1">
                  <dt>触发方</dt>
                  <dd className="text-zinc-700 dark:text-zinc-200">
                    {detail.read_only ? "进化议程（定时自动）" : detail.created_by}
                  </dd>
                </div>
                <div className="flex gap-1">
                  <dt>开始</dt>
                  <dd>{timeText(detail.started_at)}</dd>
                </div>
                <div className="flex gap-1">
                  <dt>结束</dt>
                  <dd>{timeText(detail.finished_at)}</dd>
                </div>
              </dl>

              {resolving && <p role="status" className="mb-2 text-[11px] text-zinc-600 dark:text-zinc-400">处置提交中，暂不可重复处置。</p>}
              {resolveReceipt && <p role="status" className="mb-2 rounded-md bg-sky-500/5 px-2 py-1.5 text-[11px] text-sky-800 dark:text-sky-300">
                处置请求已提交（{resolveReceipt.outcome === "done" ? "已处置" : "忽略"}）。返回任务状态：{STATUS_META[resolveReceipt.task.status]?.label ?? "未知"}
                {resolveReceipt.task.finished_at ? ` · ${timeText(resolveReceipt.task.finished_at)}` : ""}。
                {receiptCalibrated ? "持久状态已校准。" : "持久状态待校准，请刷新或等待读取恢复。"}
                该回执不表示业务动作已执行或消息已送达。
              </p>}
              {actionable && (
                <p className="mb-2 rounded-md bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-800 dark:text-amber-300">
                  待人工处置：AI 判读把该告警升级为「需处理」。系统不会自动执行任何动作——
                  已处理点「已处置」，判定无需处理点「忽略」，结论留痕在审计里。
                </p>
              )}
              {detail.outcome?.note && <NarrativeText text={detail.outcome.note} label="已记录任务范围" identity={detail.id}/>}
              {paramRows.length > 0 && (
                <dl className="mb-3 rounded-lg border border-zinc-100 px-2 py-1.5 text-[11px] dark:border-zinc-800">
                  {paramRows.map(([k, v]) => (
                    <div key={k} className="flex gap-2 py-0.5">
                      <dt className="w-[72px] shrink-0 text-zinc-600 dark:text-zinc-400">{PARAM_LABEL[k] ?? k}</dt>
                      <dd className="min-w-0 flex-1 break-words text-zinc-700 dark:text-zinc-200">
                        <NarrativeText text={String(v)} label={PARAM_LABEL[k] ?? k} identity={`${detail.id}:${k}`}/>
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
              {companySymbols.length > 0 && <dl className="mb-3 rounded-lg border border-zinc-100 px-2 py-1.5 text-[11px] dark:border-zinc-800">
                <div className="flex gap-2 py-0.5"><dt className="w-[72px] shrink-0 text-zinc-600 dark:text-zinc-400">任务标的</dt>
                  <dd className="min-w-0 flex-1 break-words font-mono tabular-nums">{companySymbols.join("、")}</dd></div>
              </dl>}
              {invalidCompanySymbols && <p className="mb-2 text-[11px] text-amber-700 dark:text-amber-300">部分标的参数无效或超出展示上限，仅展示前8个合法证券代码。</p>}
              {detail.error && (
                <p className="mb-2 rounded-md bg-red-500/5 px-2 py-1.5 text-[11px] text-red-700 dark:text-red-300">
                  失败：{detail.error.message}（{detail.error.code}）
                </p>
              )}
              {detail.type === "company_evidence" ? <CompanyTaskResult task={detail}/> : detail.result_ref && (
                <p className="mb-2 rounded-md bg-emerald-500/5 px-2 py-1.5 text-[11px] text-emerald-700 dark:text-emerald-300">
                  产物：{detail.result_ref.kind}{detail.result_ref.id ? ` · ${detail.result_ref.id}` : ""}
                </p>
              )}
              {detail.type === "tradingagents_research" && typeof detail.params.symbol === "string" && /^\d{6}$/.test(detail.params.symbol) && <div>
                <p className="text-[11px] text-zinc-600 dark:text-zinc-400">以下读取本股最近完整研究，不表示所选任务已生成报告或通过效果验证。</p>
                <TradingAgentsResearchPanel key={`${detail.id}:${detail.status}`} symbol={detail.params.symbol} defaultOpen allowStart={false}/>
              </div>}

              <FactTimeline label="任务时点" items={[{label:"创建",value:detail.created_at ? timeText(detail.created_at) : "未记录",kind:"time"},{label:"开始",value:detail.started_at ? timeText(detail.started_at) : "未记录",kind:"time"},{label:"结束",value:detail.finished_at ? timeText(detail.finished_at) : "未记录",kind:"time"}]}/>
              {detail.steps.length === 0 ? (
                <p className="text-[11px] text-zinc-600 dark:text-zinc-400">{detail.type === "company_evidence" ? "本任务未记录执行步骤；请结合批次结果与错误查看实际处理状态。" : "暂无步骤（任务刚创建，等待执行）。"}</p>
              ) : (
                <ExplanationMap label="执行轨迹" identity={detail.id} steps={traceGroups.map(group=>({id:`step-${group[0].index}`,label:`${group[0].index}. ${group[0].name}${group.length > 1 ? `等 ${group.length} 步` : ""}`,icon:"check",content:<div className="space-y-3">{group.map(step=><section key={step.index}><h4 className="text-xs font-medium">{step.index}. {step.name}</h4><ConditionList label="步骤记录" items={[`步骤状态：${typeof step.ok === "boolean" ? step.ok ? "通过" : "未通过" : "未记录"}；耗时：${step.duration_ms ?? "未记录"}ms。`,...(step.llm ? [`模型：${step.llm.model || "rules"}${step.llm.enhanced ? " · LLM 增强" : ""}`] : [])]}/>{step.input_summary && <NarrativeText text={step.input_summary} label="输入原记录"/>}{step.output_summary && <NarrativeText text={step.output_summary} label="输出原记录"/>}</section>)}</div>}))}/>
              )}
            </>
          )}
        </section>
      </div>
      </div>
    </div>
  );
}
