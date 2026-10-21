"use client";

import {useEffect, useRef, useState} from "react";
import {useResource} from "@/hooks/use-resource";
import {createAgentTask, getTradingAgentsResearch, type AgentTask, type TradingAgentsResearchView, type TradingAgentsReportKey} from "@/lib/api/agent";
import {dateTimeTextBJ} from "@/lib/format";

const REPORTS: [TradingAgentsReportKey, string][] = [
  ["market_report", "行情分析"], ["sentiment_report", "新闻与情绪分析"], ["news_report", "新闻分析"],
  ["fundamentals_report", "基本面分析"], ["investment_plan", "研究辩论结论"],
  ["trader_investment_plan", "交易员研究方案"], ["final_trade_decision", "风险辩论与最终研究结论"],
];
const STATUS: Record<string, string> = {queued: "排队中", running: "执行中", succeeded: "任务已结束", failed: "研究失败", canceled: "已取消", needs_confirm: "待确认"};

/** Existing stock drawer and task center share the same read-only report consumer. */
export function TradingAgentsResearchPanel({symbol, active = true, defaultOpen = false, allowStart = true}: {
  symbol: string; active?: boolean; defaultOpen?: boolean; allowStart?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (!active || !/^\d{6}$/.test(symbol)) return null;
  return <section aria-label="TradingAgents完整研究" className="mt-3 min-w-0 border-t border-zinc-200 pt-2 text-xs dark:border-zinc-800">
    <button type="button" data-action="quiet" aria-expanded={open} onClick={() => setOpen(value => !value)}>TradingAgents 完整研究{open ? " · 收起" : " · 查看"}</button>
    {open && <ResearchReader key={symbol} symbol={symbol} allowStart={allowStart}/>}
  </section>;
}

function ResearchReader({symbol, allowStart}: {symbol: string; allowStart: boolean}) {
  const resource = useResource(() => getTradingAgentsResearch(symbol), {key: symbol, marketHours: false});
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
  const uncertain = error !== null && view === error.snapshot;
  const {pending, error: readError, refresh} = resource;
  useEffect(() => {
    if (pending || readError || !activeTask && !receiptPending) return;
    const timer = setTimeout(() => { if (document.visibilityState !== "hidden") refresh(); }, 3_000);
    return () => clearTimeout(timer);
  }, [pending, readError, refresh, activeTask, receiptPending, task?.status]);

  async function start() {
    if (busy.current || !view?.executable || resource.pending || resource.error || activeTask || receiptPending || uncertain || view.task_read_unavailable) return;
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
  const methodChanged = report && view?.admission.method_sha256 && report.method_sha256 !== view.admission.method_sha256;
  return <div className="min-w-0 space-y-2 py-2">
    <p className="text-zinc-600 dark:text-zinc-400">独立的完整多角色研究，区别于已有消息解读和公司依据。结果待核，不构成买卖建议。</p>
    {resource.error != null && <p role="alert" className="text-amber-800 dark:text-amber-300">完整研究读取失败。{report ? "保留本股上次读取报告，当前状态未确认。" : "尚不能确认是否有已保存报告。"}</p>}
    {!view && resource.pending && <p role="status">正在读取 {symbol} 的完整研究…</p>}
    {view && <>
      <p>{resource.error ? "启动条件读取失败，当前资格未确认" : resource.pending ? "正在校准启动条件，暂不能启动" : `${view.enabled ? "研究功能已配置" : "当前未启用完整研究"} · ${view.executable ? "启动条件当前满足，仍需显式提交" : "当前不能启动"}`}。</p>
      {view.blockers.length > 0 && <details><summary className="cursor-pointer">查看启动阻断原因（{view.blockers.length}项）</summary><ul className="list-disc space-y-1 pl-4">{view.blockers.map((item, index) => <li key={`${item.code}:${index}`}>{item.message}</li>)}</ul></details>}
      <p>完整图所需模型余量：{view.required_model_slots}次；{view.budget ? `预算快照剩余 ${view.budget.remaining}/${view.budget.limit} 次（${view.budget.date}）。` : "共享预算未能核实。"}</p>
      {view.task_read_unavailable && <p role="alert">任务状态读取失败，不能确认是否仍有研究在运行。</p>}
      {task && <p role="status">本股最近任务{resource.error ? "（上次读取）" : ""}：{task.cancel_requested ? "取消请求已记录，等待当前步骤结束" : STATUS[task.status] ?? "状态未知"}{task.error ? ` · ${task.error.message}（${task.error.code}）` : ""}。{task.status === "succeeded" && !report ? "尚无可读取的完整七报告，不能据任务状态当作研究通过。" : ""}</p>}
      {receiptPending && <p role="status">任务已提交，持久状态待校准；不会重复提交。</p>}
      {!report && <p>本股尚无可读取的完整七报告。</p>}
      {report && <div>
        <p className="font-medium">{symbol} · 已保存完整七报告 · 待核</p>
        {report.review_state === "finite_failed_requires_review" && <p role="alert" className="text-amber-800 dark:text-amber-300">自动来源或引用检查有{report.review_summary.finite_failures}项未通过，原报告保留待复核，不能视为已验证结论。</p>}
        <p>同伴释义待审：{report.review_summary.peer_review_required}项。尚无独立人工验收，不代表投资正确率。</p>
        <p>生成：{dateTimeTextBJ(report.generated_at)} · 材料截点：{dateTimeTextBJ(report.as_of)}。来源披露日期见各原报告。</p>
        {!report.generated_today && <p className="text-amber-800 dark:text-amber-300">历史生成报告，不代表今天已重新判读。</p>}
        {methodChanged && <p className="text-amber-800 dark:text-amber-300">报告方法与当前执行器不同，仅供历史阅读。</p>}
        <details><summary className="cursor-pointer">报告版本与核验身份</summary><p className="break-all">报告方法：{report.method_sha256}<br/>上游：{report.upstream_sha}<br/>输入：{report.input_sha256}<br/>核验身份：完整原图未验证研究；原答与结论仍待复核。</p></details>
        <div className="space-y-2">{REPORTS.map(([key, label]) => <details key={`${report.run_id}:${key}`} className="rounded border border-zinc-200 p-2 dark:border-zinc-800"><summary className="cursor-pointer font-medium">{label}</summary><div className="mt-2 whitespace-pre-wrap [overflow-wrap:anywhere]">{report.reports[key]}</div></details>)}</div>
      </div>}
    </>}
    {error && <p role="alert" className="text-amber-800 dark:text-amber-300">{error.message}。请先核对任务状态；提交失败不表示已执行研究。</p>}
    <div className="flex flex-wrap gap-2">
      <button type="button" data-action="secondary" disabled={resource.pending || submitting} onClick={resource.refresh}>重新读取研究</button>
      {allowStart && <button type="button" data-action="command" disabled={!view?.executable || resource.pending || resource.error != null || submitting || activeTask || receiptPending || uncertain || view?.task_read_unavailable} onClick={() => void start()}>{submitting ? "正在提交…" : "显式启动完整研究"}</button>}
      {allowStart && <a href="/agent?area=maintenance&tab=tasks" data-action="quiet">任务中心查看或取消</a>}
    </div>
  </div>;
}
