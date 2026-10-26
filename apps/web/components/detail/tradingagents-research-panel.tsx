"use client";

import {useEffect, useRef, useState} from "react";
import {useResource} from "@/hooks/use-resource";
import {cancelAgentTask, createAgentTask, getTradingAgentsResearch, recoverTradingAgentsResearch, type AgentTask} from "@/lib/api/agent";
import {ApiError} from "@/lib/api/client";
import {dateTimeTextBJ} from "@/lib/format";
import {TradingAgentsReportExplorer} from "./tradingagents-report-explorer";
import {useDeviceAccess} from "@/components/access/device-access";

const STATUS: Record<string, string> = {queued: "排队中", running: "执行中", succeeded: "任务已结束", failed: "研究失败", canceled: "已取消", needs_confirm: "待确认"};

/** Existing stock drawer and task center share the same read-only report consumer. */
export function TradingAgentsResearchPanel({symbol, active = true, defaultOpen = false, compact = false, allowStart = !compact}: {
  symbol: string; active?: boolean; defaultOpen?: boolean; allowStart?: boolean; compact?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen || compact);
  const [readSymbol, setReadSymbol] = useState(defaultOpen || compact ? symbol : null);
  if (!active || !/^\d{6}$/.test(symbol)) return null;
  return <section aria-label="TradingAgents完整研究" className="mt-3 min-w-0 border-t border-zinc-200 pt-2 text-xs dark:border-zinc-800">
    <button type="button" data-action="quiet" aria-expanded={open} onClick={() => {
      if (!open) setReadSymbol(symbol);
      setOpen(value => !value);
    }}>{compact ? "TradingAgents 研究记录" : "TradingAgents 完整研究"}{open ? " · 收起" : " · 查看"}</button>
    {compact && !open && <p className="mt-1 text-zinc-600 dark:text-zinc-400">{readSymbol !== symbol ? "未读取 · " : ""}展开只查已有结果，不会运行模型。</p>}
    {open && <ResearchReader key={symbol} symbol={symbol} allowStart={allowStart} compact={compact}/>}
  </section>;
}

function ResearchReader({symbol, allowStart, compact}: {symbol: string; allowStart: boolean; compact: boolean}) {
  const access = useDeviceAccess();
  const canStart = allowStart && access.device?.state === "active" && access.device.maintenance === true;
  const [startupChecked, setStartupChecked] = useState(false);
  const [recoveryChecked, setRecoveryChecked] = useState(false);
  const [priorPermission, setPriorPermission] = useState(canStart);
  if (priorPermission !== canStart) {
    setPriorPermission(canStart);
    if (!canStart) {setStartupChecked(false); setRecoveryChecked(false);}
  }
  const readConditions = canStart && startupChecked;
  const readRecovery = canStart && recoveryChecked;
  const alive = useRef(true);
  const readGeneration = useRef(0);
  const manualRead = useRef<{generation: number; run: string | null} | null>(null);
  const [readNotice, setReadNotice] = useState<string | null>(null);
  const resource = useResource(async () => {
    const generation = ++readGeneration.current;
    setReadNotice(null);
    const next = await getTradingAgentsResearch(symbol, {savedOnly: !readConditions, ...(readRecovery ? {checkRecovery: true} : {})});
    if (alive.current && generation === readGeneration.current && manualRead.current?.generation === generation) {
      const prior = manualRead.current.run;
      setReadNotice(next.latest ? next.latest.run_id === prior ? "已重新读取，仍是同一保存版本；没有生成新报告。" : "已读到另一份保存报告；本次读取没有调用模型。" : "已重新读取，暂无可读报告；没有启动生成。" );
      manualRead.current = null;
    }
    return next;
  }, {key: `${symbol}:${readConditions ? "conditions" : "saved"}:${readRecovery ? "recovery" : "ordinary"}`, marketHours: false, intervalMs: null});
  const view = resource.data;
  const [submitting, setSubmitting] = useState<"start" | "recover" | "cancel" | null>(null);
  const [submitted, setSubmitted] = useState<AgentTask | null>(null);
  const [cancelReceipt, setCancelReceipt] = useState<string | null>(null);
  const [error, setError] = useState<{message: string; uncertain: boolean} | null>(null);
  const busy = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const task = view?.task;
  const receiptPending = submitted !== null && task?.id !== submitted.id;
  const activeTask = task?.status === "queued" || task?.status === "running";
  const canceling = activeTask && (task.cancel_requested || cancelReceipt === task.id);
  const uncertain = error?.uncertain === true;
  const recovery = view?.recovery;
  const unavailable = resource.pending || resource.error != null || view?.task_read_unavailable || !!submitting || receiptPending || uncertain;
  const {pending, error: readError, refresh} = resource;
  useEffect(() => {
    if (pending || readError || !activeTask && !receiptPending) return;
    const timer = setTimeout(() => { if (document.visibilityState !== "hidden") refresh(); }, 3_000);
    return () => clearTimeout(timer);
  }, [pending, readError, refresh, activeTask, receiptPending, task?.status]);

  async function submit(operation: "start" | "recover" | "cancel") {
    if (!canStart || busy.current || unavailable || !view) return;
    if (operation === "start" && (!readConditions || !view.executable || activeTask)) return;
    if (operation === "recover" && (!readRecovery || recovery?.state !== "available" || !recovery.source_task_id || !recovery.run_id || activeTask)) return;
    if (operation === "cancel" && (!task || !activeTask || canceling)) return;
    busy.current = true;
    setSubmitting(operation);
    setError(null);
    try {
      const result = operation === "start" ? await createAgentTask("tradingagents_research", {symbol})
        : operation === "recover" ? await recoverTradingAgentsResearch(recovery!.source_task_id!, recovery!.run_id!)
        : await cancelAgentTask(task!.id);
      if (!alive.current) return;
      if (!result || result.type !== "tradingagents_research" || !result.params || result.params.symbol !== symbol) throw new Error("任务回执证券不匹配，请到任务中心核对");
      if (operation === "recover") {
        const binding = result.params.native_recovery;
        if (!binding || typeof binding !== "object" || Array.isArray(binding)
          || (binding as Record<string, unknown>).source_task_id !== recovery!.source_task_id
          || (binding as Record<string, unknown>).run_id !== recovery!.run_id) throw new Error("恢复回执原任务或运行不匹配，请到任务中心核对");
      }
      if (operation === "cancel") {
        if (result.id !== task!.id) throw new Error("取消回执任务不匹配，请到任务中心核对");
        setCancelReceipt(result.id);
      } else setSubmitted(result);
    } catch (failure) {
      if (alive.current) setError({message: failure instanceof Error ? failure.message : "研究提交未确认",
        uncertain: !(failure instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(failure.status))});
    } finally {
      busy.current = false;
      if (alive.current) { setSubmitting(null); resource.refresh(); }
    }
  }

  const report = view?.latest;
  const boundReport = !!report && !!task?.result_run_id && task.result_run_id === report.run_id;
  const lineage = report?.recovery_lineage;
  const selectedResearch = view?.admission.state === "research_only_authorized";
  const methodChanged = report && view?.admission.method_sha256 && report.method_sha256 !== view.admission.method_sha256;
  const runtimeConditions = view && <>
    <p>{resource.error ? "启动条件读取失败，当前资格未确认" : resource.pending ? "正在读取保存结果" : view.startup_conditions_not_checked ? "只读已保存结果；启动条件尚未检查" : selectedResearch ? "已授权入选后完整研究；按需提交，不代表选股或投资效果已通过验证" : `${view.enabled ? "研究功能已配置" : "当前未启用完整研究"} · ${view.executable ? "启动条件当前满足，需主动提交" : "当前不能启动"}`}。</p>
    {!readConditions && view.blockers.length > 0 && <details><summary className="cursor-pointer">查看启动阻断原因（{view.blockers.length}项）</summary><ul className="list-disc space-y-1 pl-4">{view.blockers.map((item, index) => <li key={`${item.code}:${index}`}>{item.message}</li>)}</ul></details>}
    {selectedResearch && <p>只研究真正入选股；复用同次入选的已有任务和报告，行情刷新不会重复生成。</p>}
  </>;
  const reportTitle = resource.error ? "读取失败" : resource.pending ? "读取中" : view?.task_read_unavailable ? "运行状态未确认"
    : canceling ? "取消处理中" : task ? STATUS[task.status] ?? "任务状态未知"
    : report ? "已有保存报告" : "暂无可读完整报告";
  return <div className="min-w-0 space-y-2 py-2">
    <div className="flex flex-wrap items-center gap-2" aria-label="研究读取与生成操作">
      <button type="button" data-action="secondary" disabled={resource.pending || !!submitting} onClick={() => {
        manualRead.current = {generation: readGeneration.current + 1, run: report?.run_id ?? null};
        setReadNotice(null); resource.refresh();
      }}>重新读取研究</button>
      {canStart && (!startupChecked || view?.startup_conditions_not_checked) && <button type="button" data-action="quiet" disabled={resource.pending} onClick={()=>{setReadNotice(null); if (readConditions && !readRecovery) resource.refresh(); else {setRecoveryChecked(false); setStartupChecked(true);}}}>检查启动条件</button>}
      {canStart && <button type="button" data-action="command" disabled={!readConditions || !view?.executable || unavailable || activeTask} onClick={() => void submit("start")}>{submitting === "start" ? "正在提交…" : "开始完整研究"}</button>}
      {canStart && recovery && <button type="button" data-action="quiet" disabled={resource.pending || !!submitting} onClick={() => {if (readRecovery) resource.refresh(); else setRecoveryChecked(true);}}>检查原任务恢复</button>}
      {canStart && readRecovery && recovery?.state === "available" && <button type="button" data-action="command" disabled={unavailable || activeTask} onClick={() => void submit("recover")}>{submitting === "recover" ? "正在提交恢复…" : "恢复未完成步骤"}</button>}
      {canStart && activeTask && <button type="button" data-action="quiet" disabled={unavailable || canceling} onClick={() => void submit("cancel")}>{submitting === "cancel" ? "正在请求取消…" : canceling ? "取消处理中" : "取消本次研究"}</button>}
      {canStart && <a href="/agent?area=maintenance&tab=tasks" data-action="quiet">任务中心查看或取消</a>}
    </div>
    {readNotice && !resource.pending && !resource.error && <p role="status" aria-label="研究重读结果">{readNotice}</p>}
    {allowStart && !canStart && <p className="text-zinc-600 dark:text-zinc-400">已有报告可直接读取。生成新研究需要维护授权。<button type="button" data-action="quiet" className="ml-2" onClick={access.open}>设备与权限</button></p>}
    {readConditions && view && !resource.pending && !resource.error && !view.executable && <section aria-label="新研究启动阻断" className="text-amber-800 dark:text-amber-300">
      <strong>暂不能生成新研究</strong><ul className="list-disc space-y-1 pl-4">{view.blockers.map((item, index) => <li key={`${item.code}:${index}`}>{item.message}</li>)}</ul>
      <p>旧报告仍可读；重新读取不会解除这些阻断。</p>
    </section>}
    {!compact && <p className="text-zinc-600 dark:text-zinc-400">独立的完整多角色研究，区别于已有消息解读和公司依据。结果待核，不构成买卖建议。</p>}
    {compact && <p role="status" className="font-medium">{symbol} · {reportTitle}{report ? ` · ${!report.generated_today || methodChanged ? "历史报告" : "已保存报告"} · 待核` : ""}</p>}
    {resource.error != null && <p role="alert" className="text-amber-800 dark:text-amber-300">完整研究读取失败。{report ? "保留本股上次读取报告，当前状态未确认。" : "尚不能确认是否有已保存报告。"}</p>}
    {!view && resource.pending && <p role="status">正在读取 {symbol} 的完整研究…</p>}
    {view && <>
      {!compact && <details><summary className="cursor-pointer">运行条件</summary>{runtimeConditions}</details>}
      {view.task_read_unavailable && <p role="alert">任务状态读取失败，不能确认是否仍有研究在运行。</p>}
      {task && (!compact || task.error || canceling || task.status === "succeeded" && !report) && <p role="status">本股最近任务{resource.error ? "（上次读取）" : ""}：{canceling ? "取消请求已记录，等待当前步骤结束" : STATUS[task.status] ?? "状态未知"}{task.error ? ` · ${task.error.message}（${task.error.code}）` : ""}。{task.status === "succeeded" && !report ? "尚无可读取的完整七报告，不能据任务状态当作研究通过。" : ""}</p>}
      {receiptPending && <p role="status">任务已提交，持久状态待校准；不会重复提交。</p>}
      {task?.reused_identical_selection && boundReport && <p role="status" aria-label="研究复用结果">本次入选已完成研究，已复用原保存报告；没有重复生成。原生成时间与方法身份保留。</p>}
      {!report && <p>暂无可核验的完整七报告；不代表从未研究。</p>}
      {report && <div>
        {!compact && <p className="font-medium">{symbol} · 已保存完整七报告 · 待核</p>}
        {task && <p aria-label="报告与任务关联" className="text-zinc-600 dark:text-zinc-400">{boundReport ? "本次任务的保存报告。" : "上次已保存报告，不是本次任务结果。"}</p>}
        {report.review_state === "finite_failed_requires_review" && <p role="alert" className="text-amber-800 dark:text-amber-300">自动来源或引用检查有{report.review_summary.finite_failures}项未通过，原报告保留待复核，不能视为已验证结论。</p>}
        {!compact && <p>同伴释义待审：{report.review_summary.peer_review_required}项。尚无独立人工验收，不代表投资正确率。</p>}
        <p>生成：{dateTimeTextBJ(report.generated_at)} · 材料截点：{dateTimeTextBJ(report.as_of)}。来源披露日期见各原报告。</p>
        {lineage && <p aria-label="原答复用与接续">{lineage.reused_original_outputs > 0 ? `已复用${lineage.reused_original_outputs}份原答，仅接续未完成步骤` : "从未完成步骤接续，未复用已保存原答"}；材料截点保持不变。</p>}
        {lineage && lineage.inherited_unknown_usage_count > 0 && <p role="note" aria-label="研究用量缺口" className="text-amber-800 dark:text-amber-300">仍有{lineage.inherited_unknown_usage_count}次历史用量未知，总用量与费用不完整。</p>}
        {!report.generated_today && <p className="text-amber-800 dark:text-amber-300">历史生成报告，不代表今天已重新判读。</p>}
        {methodChanged && <p className="text-amber-800 dark:text-amber-300">报告方法与当前执行器不同，仅供历史阅读。</p>}
        {report.role_projection_state === "unavailable" && <p role="status">角色原答暂不可读，七份保存报告仍可核对。</p>}
        <TradingAgentsReportExplorer key={report.run_id} report={report}/>
        <details><summary className="cursor-pointer">报告版本与核验身份</summary><p className="break-all">报告方法：{report.method_sha256}<br/>上游：{report.upstream_sha}<br/>输入：{report.input_sha256}<br/>核验身份：完整原图未验证研究；原答与结论仍待复核。</p>{lineage && <p aria-label="恢复任务与方法来源" className="break-all">原任务：{lineage.source_task_id}<br/>原运行：{lineage.source_run_id}<br/>接续任务：{lineage.recovery_task_id}<br/>原方法：{lineage.source_method_sha256}<br/>接续方法：{lineage.recovery_method_sha256}<br/>新增调用编号：{lineage.new_call_from}–{lineage.new_call_to}</p>}{compact && <p>同伴释义待审：{report.review_summary.peer_review_required}项。尚无独立人工验收，不代表投资正确率。</p>}</details>

      </div>}
      {compact && <details><summary className="cursor-pointer">运行条件与承接状态</summary><div className="mt-2 space-y-2">{runtimeConditions}<p>此处只读已保存结果；查看或重读都不会发起模型调用。</p></div></details>}
    </>}
    {error && <p role="alert" className="text-amber-800 dark:text-amber-300">{error.message}。{uncertain ? "提交结果未确认，已停止重复操作；请先到任务中心核对。" : "本次操作被拒绝，可重读条件后再试。"}</p>}
    {canStart && recovery && recovery.state !== "not_checked" && <div className="text-zinc-600 dark:text-zinc-400" aria-label="原任务恢复条件">
      <p>{resource.error ? "恢复条件重读失败，当前资格未确认。" : resource.pending ? "正在核对恢复条件，暂不可提交。" : recovery.state === "available" ? "原任务已核对：仅接续未完成步骤，已保存原答不重新生成。" : recovery.state === "none" ? "未发现可恢复的原任务。" : recovery.state === "unknown" ? "恢复状态未确认，不能提交恢复。" : "原任务暂不能恢复。"}</p>
      {recovery.blockers.length > 0 && <details><summary className="cursor-pointer">查看恢复阻断（{recovery.blockers.length}项）</summary><ul className="list-disc space-y-1 pl-4">{recovery.blockers.map((blocker, index) => <li key={`${blocker.code}:${index}`}>{blocker.message}</li>)}</ul></details>}
    </div>}
  </div>;
}
