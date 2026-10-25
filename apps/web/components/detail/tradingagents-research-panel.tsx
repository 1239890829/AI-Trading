"use client";

import {useEffect, useRef, useState} from "react";
import {useResource} from "@/hooks/use-resource";
import {createAgentTask, getTradingAgentsResearch, type AgentTask, type TradingAgentsResearchView} from "@/lib/api/agent";
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
  const canStart = allowStart && access.device?.maintenance === true;
  const [startupChecked, setStartupChecked] = useState(false);
  const resource = useResource(() => getTradingAgentsResearch(symbol, {savedOnly: !startupChecked}), {key: `${symbol}:${startupChecked ? "conditions" : "saved"}`, marketHours: false, intervalMs: null});
  const view = resource.data;
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState<AgentTask | null>(null);
  const [error, setError] = useState<{message: string; snapshot: TradingAgentsResearchView | undefined} | null>(null);
  const busy = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const task = view?.task;
  const receiptPending = submitted !== null && task?.id !== submitted.id;
  const activeTask = task?.status === "queued" || task?.status === "running";
  const canceling = activeTask && task.cancel_requested;
  const uncertain = error !== null && view === error.snapshot;
  const {pending, error: readError, refresh} = resource;
  useEffect(() => {
    if (pending || readError || !activeTask && !receiptPending) return;
    const timer = setTimeout(() => { if (document.visibilityState !== "hidden") refresh(); }, 3_000);
    return () => clearTimeout(timer);
  }, [pending, readError, refresh, activeTask, receiptPending, task?.status]);

  async function start() {
    if (!canStart || !startupChecked || busy.current || !view?.executable || resource.pending || resource.error || activeTask || receiptPending || uncertain || view.task_read_unavailable) return;
    busy.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const result = await createAgentTask("tradingagents_research", {symbol});
      if (!alive.current) return;
      if (result.type !== "tradingagents_research" || result.params.symbol !== symbol) throw new Error("任务回执证券不匹配，请到任务中心核对");
      setSubmitted(result);
    } catch (failure) {
      if (alive.current) setError({message: failure instanceof Error ? failure.message : "研究提交未确认", snapshot: view});
    } finally {
      busy.current = false;
      if (alive.current) { setSubmitting(false); resource.refresh(); }
    }
  }

  const report = view?.latest;
  const lineage = report?.recovery_lineage;
  const selectedResearch = view?.admission.state === "research_only_authorized";
  const methodChanged = report && view?.admission.method_sha256 && report.method_sha256 !== view.admission.method_sha256;
  const runtimeConditions = view && <>
    <p>{resource.error ? "启动条件读取失败，当前资格未确认" : resource.pending ? "正在读取保存结果" : view.startup_conditions_not_checked ? "只读已保存结果；启动条件尚未检查" : selectedResearch ? "已授权入选后完整研究；按需提交，不代表选股或投资效果已通过验证" : `${view.enabled ? "研究功能已配置" : "当前未启用完整研究"} · ${view.executable ? "启动条件当前满足，需主动提交" : "当前不能启动"}`}。</p>
    {view.blockers.length > 0 && <details><summary className="cursor-pointer">查看启动阻断原因（{view.blockers.length}项）</summary><ul className="list-disc space-y-1 pl-4">{view.blockers.map((item, index) => <li key={`${item.code}:${index}`}>{item.message}</li>)}</ul></details>}
    {selectedResearch && <p>只研究真正入选股；复用同次入选的已有任务和报告，行情刷新不会重复生成。</p>}
  </>;
  const reportTitle = resource.error ? "读取失败" : resource.pending ? "读取中" : view?.task_read_unavailable ? "运行状态未确认"
    : canceling ? "取消处理中" : task ? STATUS[task.status] ?? "任务状态未知"
    : report ? "已有保存报告" : "暂无可读完整报告";
  return <div className="min-w-0 space-y-2 py-2">
    {!compact && <p className="text-zinc-600 dark:text-zinc-400">独立的完整多角色研究，区别于已有消息解读和公司依据。结果待核，不构成买卖建议。</p>}
    {compact && <p role="status" className="font-medium">{symbol} · {reportTitle}{report ? ` · ${!report.generated_today || methodChanged ? "历史报告" : "已保存报告"} · 待核` : ""}</p>}
    {resource.error != null && <p role="alert" className="text-amber-800 dark:text-amber-300">完整研究读取失败。{report ? "保留本股上次读取报告，当前状态未确认。" : "尚不能确认是否有已保存报告。"}</p>}
    {!view && resource.pending && <p role="status">正在读取 {symbol} 的完整研究…</p>}
    {view && <>
      {!compact && <details><summary className="cursor-pointer">运行条件</summary>{runtimeConditions}</details>}
      {view.task_read_unavailable && <p role="alert">任务状态读取失败，不能确认是否仍有研究在运行。</p>}
      {task && (!compact || task.error || canceling || task.status === "succeeded" && !report) && <p role="status">本股最近任务{resource.error ? "（上次读取）" : ""}：{canceling ? "取消请求已记录，等待当前步骤结束" : STATUS[task.status] ?? "状态未知"}{task.error ? ` · ${task.error.message}（${task.error.code}）` : ""}。{task.status === "succeeded" && !report ? "尚无可读取的完整七报告，不能据任务状态当作研究通过。" : ""}</p>}
      {receiptPending && <p role="status">任务已提交，持久状态待校准；不会重复提交。</p>}
      {!report && <p>暂无可核验的完整七报告；不代表从未研究。</p>}
      {report && <div>
        {!compact && <p className="font-medium">{symbol} · 已保存完整七报告 · 待核</p>}
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
    {error && <p role="alert" className="text-amber-800 dark:text-amber-300">{error.message}。请先核对任务状态；提交失败不表示已执行研究。</p>}
    <div className="flex flex-wrap gap-2">
      <button type="button" data-action="secondary" disabled={resource.pending || submitting} onClick={resource.refresh}>重新读取研究</button>
      {canStart && !startupChecked && <button type="button" data-action="quiet" disabled={resource.pending} onClick={()=>setStartupChecked(true)}>检查启动条件</button>}
      {canStart && <button type="button" data-action="command" disabled={!startupChecked || !view?.executable || resource.pending || resource.error != null || submitting || activeTask || receiptPending || uncertain || view?.task_read_unavailable} onClick={() => void start()}>{submitting ? "正在提交…" : "开始完整研究"}</button>}
      {canStart && <a href="/agent?area=maintenance&tab=tasks" data-action="quiet">任务中心查看或取消</a>}
    </div>
    {allowStart && !canStart && <p className="text-zinc-600 dark:text-zinc-400">已有报告可直接读取。生成新研究需要维护授权。<button type="button" data-action="quiet" className="ml-2" onClick={access.open}>设备与权限</button></p>}
  </div>;
}
