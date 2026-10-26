"use client";

import {useEffect, useMemo, useRef, useState} from "react";
import {useDeviceAccess} from "@/components/access/device-access";
import {useResource} from "@/hooks/use-resource";
import {createAgentTask, getAgentTask, type AgentTask} from "@/lib/api/agent";
import {ApiError} from "@/lib/api/client";
import {REFERENCE_RESEARCH_METHODS, type SavedMethodRunIdentity} from "@/lib/api/picks";
import {methodRunDay} from "@/components/agent/method-run-location";

const LABELS: Record<string, string> = {queued:"排队中",running:"计算中",succeeded:"研究存档已保存",failed:"研究运行失败",canceled:"已取消",needs_confirm:"待核对"};
function completedRun(task: AgentTask | null, method: string, date: string | undefined, version: string) {
  const result = task?.result_ref;
  if (task?.status !== "succeeded" || result?.kind !== "reference_strategies" || !Array.isArray(result.runs)
    || result.runs.length !== REFERENCE_RESEARCH_METHODS.length || typeof result.reused !== "boolean") return null;
  if (new Set(result.runs.map(row => row.method)).size !== REFERENCE_RESEARCH_METHODS.length
    || result.runs.some(row => !(REFERENCE_RESEARCH_METHODS as readonly string[]).includes(row.method)
      || !methodRunDay(row.trade_date) || !row.run_id || !/^[0-9a-f]{64}$/.test(row.content_version)
      || typeof row.source_version !== "string" || !row.source_version || !row.strategy_version || row.state !== "saved"
      || date !== undefined && methodRunDay(row.trade_date) !== methodRunDay(date)
      || row.run_id !== result.runs![0].run_id || row.trade_date !== result.runs![0].trade_date
      || row.source_version !== result.runs![0].source_version || row.content_version !== result.runs![0].content_version)) return null;
  const identity = result.runs.find(row => row.method === method);
  return identity?.strategy_version === version ? {identity, reused: result.reused} : null;
}

/** Maintenance runs one shared deterministic scan. Ordinary readers never request task state. */
export function ReferenceMethodResearch({method, version, date, onComplete}: {method: string; version: string; date?: string; onComplete: (identity: SavedMethodRunIdentity) => void}) {
  const access = useDeviceAccess();
  const allowed = access.device?.state === "active" && access.device.maintenance === true;
  const [sourceDate, setSourceDate] = useState(date ?? "");
  const [submittedDate, setSubmittedDate] = useState<string | undefined>();
  const selectedDate = sourceDate || undefined;
  const invalidDate = selectedDate !== undefined && !methodRunDay(selectedDate);
  const [receipt, setReceipt] = useState<AgentTask | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<{message: string; unknown: boolean} | null>(null);
  const busy = useRef(false), alive = useRef(true), delivered = useRef<string | null>(null);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const resource = useResource(async () => {
    const task = await getAgentTask(receipt!.id);
    if (task.type !== "reference_strategies") throw new Error("返回研究任务类型不符");
    return task;
  }, {key:receipt?.id, enabled:allowed && !!receipt, marketHours:false});
  const task = resource.data ?? receipt;
  const {pending:taskPending, error:taskError, refresh:refreshTask} = resource;
  const running = !!task && ["queued", "running"].includes(task.status);
  const completion = useMemo(() => completedRun(task, method, submittedDate, version), [task, method, submittedDate, version]);
  const unconfirmed = !!task && (task.status === "succeeded" && !completion || !["queued", "running", "succeeded", "failed", "canceled"].includes(task.status));
  useEffect(() => {
    if (task && completion && delivered.current !== task.id) {delivered.current = task.id; onComplete(completion.identity);}
  }, [completion, task, onComplete]);
  useEffect(() => {
    if (!allowed || !receipt || taskPending || !running && !taskError) return;
    const timer = setTimeout(() => {if (document.visibilityState !== "hidden") refreshTask();}, 3_000);
    return () => clearTimeout(timer);
  }, [allowed, receipt, taskPending, taskError, refreshTask, running]);
  async function start() {
    if (!allowed || busy.current || invalidDate || running || unconfirmed || resource.pending || resource.error || failure?.unknown) return;
    busy.current = true; setSubmitting(true); setFailure(null);
    try {
      const task = await createAgentTask("reference_strategies", selectedDate ? {trade_date:selectedDate} : {});
      if (!alive.current) return;
      if (!task || task.type !== "reference_strategies" || !/^[0-9a-f]{32}$/.test(task.id)
        || selectedDate !== undefined && task.params.trade_date !== selectedDate) throw new Error("研究任务回执身份未确认");
      setReceipt(task); setSubmittedDate(selectedDate);
    } catch (error) {
      if (alive.current) setFailure({message:error instanceof Error ? error.message : "提交未确认",
        unknown:!(error instanceof ApiError && [400,401,403,404,409,422].includes(error.status))});
    } finally {busy.current = false; if (alive.current) setSubmitting(false);}
  }
  return <section className="space-y-2 border-t border-zinc-200 pt-3 text-xs dark:border-zinc-700" aria-label="手动战法研究运行">
    <p className="text-zinc-600 dark:text-zinc-400">一次扫描11项研究规则与D1/D3/D5参考价表现，无模型调用；不改变正式入选、通知或模拟资格。</p>
    <p className="text-[11px] text-zinc-600 dark:text-zinc-400">{selectedDate ? `请求源日 ${selectedDate}；服务端仅接受来源已完成日。` : "未指定日期时读取来源最新已完成日，不以界面今日代替。"}</p>
    {allowed && <div className="flex flex-wrap items-end gap-2"><label className="min-w-0 text-[11px] text-zinc-600 dark:text-zinc-400">研究源日（空为最新已完成日）<input type="date" aria-label="研究源日" value={sourceDate} disabled={submitting || running || resource.pending || failure?.unknown} onChange={event=>setSourceDate(event.target.value)} className="mt-1 block min-h-8 w-full rounded border border-zinc-300 bg-transparent px-2 dark:border-zinc-600"/></label>
      <button type="button" className="quiet-action" disabled={!sourceDate || submitting || running || resource.pending || failure?.unknown} onClick={()=>setSourceDate("")}>使用最新已完成日</button></div>}
    {invalidDate && <p role="alert">日期无效，未提交运行。</p>}
    {task && <p role="status">{resource.error ? "任务读取失败，上次状态：" : ""}{LABELS[task.status] || "任务状态未知"}{completion ? ` · ${completion.identity.trade_date} · ${completion.reused ? "复用同版存档，未重扫" : "新保存批次"}` : ""}。</p>}
    {task?.error && <p role="alert">{task.error.message}；未改写为零匹配。</p>}
    {task?.status === "succeeded" && !completion && <p role="alert">结果身份或覆盖未确认，未替换当前保存记录。</p>}
    {failure && <p role="alert">{failure.message}。{failure.unknown ? "提交结果未确认，已停止重复提交；请到任务中心核对。" : "本次提交被拒绝。"}</p>}
    {allowed ? <div className="flex flex-wrap gap-2"><button type="button" className="quiet-action" disabled={invalidDate || submitting || running || unconfirmed || resource.pending || !!resource.error || failure?.unknown} onClick={() => void start()}>{submitting ? "正在提交研究…" : "运行研究筛选"}</button>
      {receipt && <button type="button" className="quiet-action" disabled={resource.pending || submitting} onClick={resource.refresh}>核对研究任务</button>}
      <a className="quiet-action" href="/agent?area=maintenance&tab=tasks">任务中心</a></div>
      : <p className="text-zinc-600 dark:text-zinc-400">普通设备可读保存结果；运行需要维护授权。</p>}
  </section>;
}
