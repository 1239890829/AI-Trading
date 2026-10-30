import type {SavedSelectionSummary, SavedStrategyRun, SelectionEvaluation} from "@/lib/api/picks";
import type {MethodRowFilter} from "./method-run-location";

export const EVALUATION_LABELS: Record<SelectionEvaluation, string> = {
  selected: "原入选", score_rejected: "已评分落选", not_evaluated: "容量未评估",
  input_unavailable: "输入或评分未完成", excluded: "预筛排除", unknown: "原过程待核",
};
const states = Object.keys(EVALUATION_LABELS) as SelectionEvaluation[];
const count = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

export function SelectionRunSummary({summary, context, audit, value, onChange}: {
  summary: SavedSelectionSummary; context: SavedStrategyRun["strategy_context"];
  audit?: SavedStrategyRun["candidate_audit"];
  value: MethodRowFilter; onChange: (value: MethodRowFilter) => void;
}) {
  const scopeValid = summary.scope === "recorded_inputs_not_all_market" && count(summary.input_total)
    && count(summary.saved_total) && count(summary.omitted_total) && summary.saved_total + summary.omitted_total === summary.input_total;
  const complete = scopeValid && summary.state === "saved" && summary.omitted_total === 0;
  const distribution = complete ? summary.counts : summary.saved_counts;
  const distributionValid = scopeValid && states.every(state => count(distribution?.[state]))
    && states.reduce((sum, state) => sum + distribution[state]!, 0) === summary.saved_total;
  return <section aria-label="原输入去向" className="space-y-2 border-b border-zinc-200 pb-3 dark:border-zinc-700">
    <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
      <h4 className="font-medium">原输入去向 <span className="tabular-nums">{scopeValid ? summary.input_total!.toLocaleString("zh-CN") : "未核实"}</span></h4>
      <button type="button" data-action="quiet" data-ui-choice aria-pressed={value === "all"} onClick={() => onChange("all")}>全部原行</button>
    </div>
    {!complete && <p role="status" className="text-[11px] text-amber-800 dark:text-amber-300">{scopeValid ? summary.omitted_total! > 0 ? `仅保存 ${summary.saved_total} / ${summary.input_total}；缺失 ${summary.omitted_total}，下列只统计已保存部分。` : "原行已保存，分类一致性待核；下列是已存行的核对结果。" : "原输入计数未核实，不展示分布数字。"}</p>}
    <div role="group" aria-label="按原输入去向筛选" className="grid min-w-0 grid-cols-2 gap-1.5 sm:grid-cols-3">
      {states.map(state => <button key={state} type="button" data-action="secondary" data-ui-choice aria-pressed={value === state} aria-label={`${EVALUATION_LABELS[state]} ${distributionValid ? distribution[state]!.toLocaleString("zh-CN") : "—"}`} onClick={() => onChange(state)}
        className="flex min-w-0 items-center justify-between gap-2 px-2 py-2 text-left text-[11px]">
        <span className="min-w-0 break-words">{EVALUATION_LABELS[state]}</span><strong className="shrink-0 text-sm tabular-nums">{distributionValid ? distribution[state]!.toLocaleString("zh-CN") : "—"}</strong>
      </button>)}
    </div>
    <p className="text-[11px] text-zinc-600 dark:text-zinc-400">仅本批记录来源；未评估不等于低分，原入选不等于模拟成交。</p>
    {audit?.source_states && <p className="text-[11px] text-zinc-600 dark:text-zinc-400">{Object.entries(audit.source_states).map(([key,state])=>`${key==="events"?"事件来源":key==="hot"?"热榜来源":"其他来源"} ${state==="available"?"可用":state==="unavailable"?"不可用":"待核"}`).join(" · ")}</p>}
    {(!distributionValid || summary.state!=="saved") && <p role="status" className="text-[11px] text-amber-800 dark:text-amber-300">{summary.reason || "原分布未能核对；不按当前可见行补算。"}</p>}
    {context && <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[11px] text-zinc-600 dark:text-zinc-400">
      <span>原盘面：{context.market_phase || "未记录"}</span>
      <span title={context.style_basis || "原依据未记录"}>排行风格：{context.style || "未记录"}</span>
      <span>{context.routed === true ? "原权重路由已用" : context.routed === false ? "原权重路由未用" : "路由状态未记录"} · 执行资格另核</span>
    </div>}
  </section>;
}
