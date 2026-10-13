"use client";

import { useEffect, useState } from "react";
import {
  getMinuteDecisions,
  getMinuteSignals,
  type MinuteDecisionsPayload,
  type MinuteSignalsPayload,
} from "@/lib/api";
import { bjHHMM, bjMonthDay, fmt, pctColor, pctText } from "@/lib/format";
import { ConditionList, ExplanationMap, FactTimeline, NarrativeText } from "@/components/ui/explanation-map";

/**
 * 做 T 决策（个股详情右列「做T」标签，P1-24 前端消费）。
 *
 * 只渲染后端给的结构化字段，**不做任何推断**：
 * - `signals: []` = 引擎已跑但当前无越过阈值的信号（确无），不是"加载中"；
 * - `degraded` 非空 = 输入缺失导致的降级（缺昨日量/缺波动率…），如实展示，
 *   绝不假装满配；`outcome: null` = 窗口未走完的待结算，不是"没结果"；
 * - 文案遵循 AGENTS 红线 3：只有**偏向 + 依据 + 失效条件**，不输出确定性结论。
 *
 * 触发即记录是引擎的幂等副作用（前缀稳定 ⇒ 同 bar 重算同结果），因此本面板
 * 每次挂载拉一次即可，不需要轮询——避免把分钟级接口变成高频请求。
 */

const OUTCOME: Record<string, { text: string; cls: string }> = {
  correct: { text: "方向正确", cls: "text-up-ink dark:text-up" },
  wrong: { text: "方向错误", cls: "text-down-ink dark:text-down" },
  invalid: { text: "无有效价差", cls: "text-zinc-600 dark:text-zinc-400" },
  expired: { text: "数据不足·未判定", cls: "text-amber-800 dark:text-amber-300" },
};

// bjHHMM / bjMonthDay 已收口到 lib/format（2026-09-11 冗余清理）

/** 低吸偏向 = 看涨意图（A 股惯例红色）；高抛偏向 = 看跌意图（绿色）。 */
function biasCls(bias: string): string {
  return bias === "低吸偏向" ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down";
}

type ReadState<T> = { data: T | null; error: string | null; pending: boolean };

function TriggerConditions({items}: {items: MinuteSignalsPayload["signals"][number]["triggered"]}) {
  return items.length ? <div className="space-y-3">{items.map((hit, index) => <section key={`${index}:${hit.key}`}>
    <h5 className="text-xs font-medium">{hit.name}</h5>
    <ConditionList label={`${hit.name}原始条件`} items={[
      hit.evidence || "触发说明未记录。",
      `规则 ${hit.key} · 触发值 ${hit.trigger_value} · 阈值 ${hit.threshold}`,
      `权重 ${hit.weight} · 方向 ${hit.direction}`,
    ]} />
  </section>)}</div> : <p className="text-amber-800 dark:text-amber-300">触发条件未记录，不能补推触发原因。</p>;
}

export function MinuteDecisionPanel({
  symbol,
  className,
}: {
  symbol: string;
  className?: string;
}) {
  const [signalsRead, setSignalsRead] = useState<ReadState<MinuteSignalsPayload>>({ data: null, error: null, pending: true });
  const [decisionsRead, setDecisionsRead] = useState<ReadState<MinuteDecisionsPayload>>({ data: null, error: null, pending: true });
  const [nonce, setNonce] = useState(0);
  const [prevSymbol, setPrevSymbol] = useState(symbol);
  if (symbol !== prevSymbol) {
    setPrevSymbol(symbol);
    setSignalsRead({ data: null, error: null, pending: true });
    setDecisionsRead({ data: null, error: null, pending: true });
  }

  useEffect(() => {
    let alive = true;
    // 两个只读展示来源独立返回；保留挂载/手动刷新频率，不添加轮询。
    void getMinuteSignals(symbol)
      .then(data => alive && setSignalsRead({ data, error: null, pending: false }))
      .catch((e: unknown) => {
        if (alive) setSignalsRead(prev => ({ ...prev, error: e instanceof Error ? e.message : "分钟信号不可用", pending: false }));
      });
    void getMinuteDecisions(symbol, 30)
      .then(data => alive && setDecisionsRead({ data, error: null, pending: false }))
      .catch((e: unknown) => {
        if (alive) setDecisionsRead(prev => ({ ...prev, error: e instanceof Error ? e.message : "决策记录不可用", pending: false }));
      });
    return () => {
      alive = false;
    };
  }, [symbol, nonce]);

  const sig = signalsRead.data;
  const decisions = decisionsRead.data?.items ?? [];
  const note = decisionsRead.data?.note;
  const openCount = decisionsRead.data?.open_count ?? 0;
  const loading = signalsRead.pending || decisionsRead.pending;
  const latest = sig?.signals.length ? sig.signals[sig.signals.length - 1] : null;
  const degraded = sig?.degraded ?? [];

  return (
    <div className={`flex min-h-0 flex-col ${className ?? ""}`}>
      <div className="flex shrink-0 items-center gap-2 border-b border-zinc-100 px-2 py-1.5 text-xs dark:border-zinc-800/60">
        <span className="text-zinc-600 dark:text-zinc-400">分钟级做 T 信号</span>
        {sig && (
          <span className="text-[10px] text-zinc-600 dark:text-zinc-400">
            观测 {sig.observed} 次 · 本次新记录 {sig.recorded} 条
          </span>
        )}
        <button data-action="secondary"
          onClick={() => {
            setSignalsRead(prev => ({ ...prev, pending: true }));
            setDecisionsRead(prev => ({ ...prev, pending: true }));
            setNonce((n) => n + 1);
          }}
          className="ml-auto"
          aria-label="刷新做 T 信号"
        >
          {loading ? "刷新中…" : "刷新"}
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2 text-xs">
        {signalsRead.error && (
          <div role="alert" className="mb-2 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-800 dark:text-amber-300">
            信号读取失败（{signalsRead.error}）。{sig ? "保留上次信号、依据与时间，当前状态待核实。" : "尚未读到信号，不表示当前确无；可刷新重试。"}
          </div>
        )}
        {signalsRead.pending && !sig && <p className="py-2 text-[11px] text-zinc-600 dark:text-zinc-400">分钟信号加载中…</p>}

        {/* ---- 当前信号 ---- */}
        {sig &&
          (latest ? (
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{bjHHMM(latest.ts)}</span>
                <span className={`font-medium ${biasCls(latest.bias)}`}>{latest.bias}</span>
                <span className="font-mono tabular-nums text-zinc-600 dark:text-zinc-400">
                  score {fmt(latest.score, 2)}
                </span>
                <span className="text-[10px] text-zinc-600 dark:text-zinc-400">
                  置信 {latest.confidence} · 参考价 {fmt(latest.signal_price)}
                </span>
              </div>
              <ConditionList label="失效条件" tone="warning" items={[latest.invalidate_condition || "失效条件未记录，不能据此判断信号仍然有效。"]} />
              {!latest.triggered.length && <p className="text-amber-800 dark:text-amber-300">触发条件未记录，不能补推触发原因。</p>}
              <ExplanationMap label="分钟信号依据" identity={`${symbol}:${latest.ts}`} steps={[
                {id:"trigger",label:"触发依据",icon:"check",summary:`${latest.triggered.length} 项原始条件`,content:<TriggerConditions items={latest.triggered} />},
                {id:"time",label:"信号时点",icon:"time",summary:bjHHMM(latest.ts),content:<FactTimeline label="信号原始记录" items={[{label:"触发时间",value:latest.ts},{label:"信号参考价",value:fmt(latest.signal_price)},{label:"原始分值",value:String(latest.score)},{label:"置信标记",value:latest.confidence}]} />},
                {id:"basis",label:"记录口径",icon:"source",summary:"原始输入与说明",content:<>{latest.basis && <NarrativeText label="信号口径原说明" text={latest.basis} />}<p className="text-[11px] text-zinc-600 dark:text-zinc-400">置信标记是引擎输出，不是正确率或盈利概率。以下仅展示本次返回的输入口径。</p>{Object.keys(sig.basis).length ? <pre className="whitespace-pre-wrap [overflow-wrap:anywhere] text-[11px]" aria-label="信号输入口径原始值">{JSON.stringify(sig.basis, null, 2)}</pre> : <p>输入口径未返回，不补填数据。</p>}</>},
              ]} />
            </div>
          ) : (
            <p className="py-4 text-center text-[11px] text-zinc-600 dark:text-zinc-400">
              {signalsRead.error ? "上次读取无越过阈值的信号，当前状态待核实" : "当前无越过阈值的做 T 信号（引擎已在运行）"}
            </p>
          ))}

        {/* ---- 降级如实标注 ---- */}
        {degraded.length > 0 && (
          <ConditionList label={`${degraded.length} 项输入缺失导致降级（如实标注，未用别家口径顶替）`} items={degraded} tone="warning" />
        )}

        {/* ---- 决策记录 ---- */}
        <div className="mt-3 border-t border-zinc-100 pt-2 dark:border-zinc-800/60">
          <div className="mb-1 flex items-center gap-2 text-[10px] text-zinc-600 dark:text-zinc-400">
            <span>决策记录（近 {decisions.length} 条）</span>
            {openCount > 0 && <span>待结算 {openCount}</span>}
          </div>
          {decisionsRead.error && <p role="alert" className="mb-2 text-[11px] text-amber-800 dark:text-amber-300">决策记录读取失败（{decisionsRead.error}）。{decisionsRead.data ? "保留上次记录、结算状态与时间，当前状态待核实。" : "尚未读到记录，不表示没有记录；可刷新重试。"}</p>}
          {decisionsRead.pending && !decisionsRead.data && <p className="py-2 text-[11px] text-zinc-600 dark:text-zinc-400">决策记录加载中…</p>}
          {decisionsRead.data && decisions.length === 0 && !decisionsRead.error ? (
            <p className="py-2 text-[11px] text-zinc-600 dark:text-zinc-400">
              暂无记录。该股进入盘中跟踪台账后，收盘会统一扫描并记录当日信号。
            </p>
          ) : decisions.length > 0 ? (
            <ul className="space-y-1">
              {decisions.map((d) => {
                const o = d.outcome ? OUTCOME[d.outcome] : null;
                const att = d.error_attribution;
                return (
                  <li
                    key={d.decision_id}
                    className="rounded border border-zinc-100 px-2 py-1 dark:border-zinc-800/60"
                  >
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="font-mono text-[10px] text-zinc-600 dark:text-zinc-400">
                        {bjMonthDay(d.trigger_ts)} {bjHHMM(d.trigger_ts)}
                      </span>
                      <span className={`text-[11px] font-medium ${biasCls(d.bias)}`}>
                        {d.bias.replace("偏向", "")}
                      </span>
                      <span className="text-[10px] text-zinc-600 dark:text-zinc-400">
                        同向最优价差 {d.optimal_spread_pct == null ? "--" : pctText(d.optimal_spread_pct)}
                      </span>
                      <span className={`ml-auto text-[10px] ${o ? o.cls : "text-zinc-600 dark:text-zinc-400"}`}>
                        {o ? o.text : "待结算"}
                      </span>
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-zinc-600 dark:text-zinc-400">
                      <span>信号价 {fmt(d.signal_price)}</span>
                      {d.executed && d.executed_price != null && (
                        <span>
                          已执行@{fmt(d.executed_price)}
                          {d.realized_spread_pct != null && (
                            <span className={pctColor(d.realized_spread_pct)}>
                              {" "}
                              {pctText(d.realized_spread_pct)}
                            </span>
                          )}
                        </span>
                      )}
                    </div>
                    <ConditionList label="记录失效条件" tone="warning" items={[d.invalidate_condition || "该记录失效条件未记录。"]} />
                    <ExplanationMap label="决策条件核对" identity={d.decision_id} steps={[
                      {id:"conditions",label:"原触发条件",icon:"check",summary:`${d.triggered.length} 项`,content:<TriggerConditions items={d.triggered} />},
                      {id:"receipt",label:"原始回执",icon:"time",summary:d.executed ? "执行标记已记录" : "未标记执行",content:<FactTimeline label="决策记录口径" items={[{label:"决定标识",value:d.decision_id},{label:"交易日",value:d.trade_date},{label:"触发原时点",value:d.trigger_ts},{label:"原始分值",value:String(d.score)},{label:"置信标记",value:d.confidence},{label:"最佳价",value:d.best_price == null ? "未记录" : fmt(d.best_price)},{label:"最差价",value:d.worst_price == null ? "未记录" : fmt(d.worst_price)}]} />},
                    ]} />
                    {att?.counter_evidence && (
                      <div className="mt-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">
                        {att.pivotal && att.primary_name ? `主因「${att.primary_name}」：` : ""}
                        <NarrativeText label="记录反证原文" text={att.counter_evidence} />
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : null}
          {note && <div className="mt-1.5"><NarrativeText label="决策记录原说明" text={note} /></div>}
        </div>
      </div>

      <div className="shrink-0 border-t border-zinc-100 px-3 py-1 text-[10px] text-zinc-600 dark:text-zinc-400 dark:border-zinc-800/60">
        偏向 + 依据 + 失效条件，不构成买卖建议
      </div>
    </div>
  );
}
