"use client";

import { useEffect, useState } from "react";
import {
  getMinuteDecisions,
  getMinuteSignals,
  type MinuteDecisionItem,
  type MinuteSignalsPayload,
} from "@/lib/api";
import { bjHHMM, bjMonthDay, fmt, pctColor, pctText } from "@/lib/format";

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

export function MinuteDecisionPanel({
  symbol,
  className,
}: {
  symbol: string;
  className?: string;
}) {
  const [sig, setSig] = useState<MinuteSignalsPayload | null>(null);
  const [decisions, setDecisions] = useState<MinuteDecisionItem[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [openCount, setOpenCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    // loading 初值 true（挂载即加载）；非首次由刷新按钮在事件处理里置 true。
    // 刻意不在 effect 体内同步 setState——那会触发级联渲染（eslint set-state-in-effect）。
    // 信号与决策库独立容错：一个源挂了不应让整块空白（各自给出三态）
    void Promise.allSettled([getMinuteSignals(symbol), getMinuteDecisions(symbol, 30)])
      .then(([s, d]) => {
        if (!alive) return;
        if (s.status === "fulfilled") {
          setSig(s.value);
          setError(null);
        } else {
          setSig(null);
          setError((s.reason as Error)?.message ?? "分钟信号不可用");
        }
        if (d.status === "fulfilled") {
          setDecisions(d.value.items);
          setNote(d.value.note);
          setOpenCount(d.value.open_count);
        } else {
          setDecisions([]);
          setNote(null);
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [symbol, nonce]);

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
        <button
          onClick={() => {
            setLoading(true);
            setNonce((n) => n + 1);
          }}
          className="ml-auto rounded border border-zinc-200 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:text-zinc-400 transition-colors hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100"
          aria-label="刷新做 T 信号"
        >
          {loading ? "刷新中…" : "刷新"}
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2 text-xs">
        {error && (
          <div className="mb-2 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-800 dark:text-amber-300">
            {error}
          </div>
        )}

        {/* ---- 当前信号 ---- */}
        {sig &&
          (latest ? (
            <div className="space-y-1.5">
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{bjHHMM(latest.ts)}</span>
                <span className={`font-medium ${biasCls(latest.bias)}`}>{latest.bias}</span>
                <span className="font-mono tabular-nums text-zinc-600 dark:text-zinc-400">
                  score {fmt(latest.score, 2)}
                </span>
                <span className="text-[10px] text-zinc-600 dark:text-zinc-400">
                  置信 {latest.confidence} · 参考价 {fmt(latest.signal_price)}
                </span>
              </div>
              <dl className="space-y-0.5 rounded bg-zinc-50 px-2 py-1.5 dark:bg-zinc-800/40">
                <dt className="text-[10px] text-zinc-600 dark:text-zinc-400">触发依据</dt>
                {latest.triggered.map((h) => (
                  <dd key={`${h.key}-${h.evidence}`} className="text-[11px] text-zinc-600 dark:text-zinc-300">
                    <span className="font-medium">{h.name}</span>
                    <span className="ml-1 text-zinc-600 dark:text-zinc-400">{h.evidence}</span>
                  </dd>
                ))}
                {latest.invalidate_condition && (
                  <>
                    <dt className="pt-1 text-[10px] text-zinc-600 dark:text-zinc-400">失效条件</dt>
                    <dd className="text-[11px] text-zinc-600 dark:text-zinc-300">
                      {latest.invalidate_condition}
                    </dd>
                  </>
                )}
              </dl>
            </div>
          ) : (
            <p className="py-4 text-center text-[11px] text-zinc-600 dark:text-zinc-400">
              当前无越过阈值的做 T 信号（引擎已在运行）
            </p>
          ))}

        {/* ---- 降级如实标注 ---- */}
        {degraded.length > 0 && (
          <details className="mt-2 rounded border border-zinc-200 px-2 py-1 dark:border-zinc-800">
            <summary className="cursor-pointer text-[10px] text-zinc-600 dark:text-zinc-400">
              {degraded.length} 项输入缺失导致降级（如实标注，未用别家口径顶替）
            </summary>
            <ul className="mt-1 space-y-0.5">
              {degraded.map((d) => (
                <li key={d} className="text-[10px] text-zinc-600 dark:text-zinc-400">
                  {d}
                </li>
              ))}
            </ul>
          </details>
        )}

        {/* ---- 决策记录 ---- */}
        <div className="mt-3 border-t border-zinc-100 pt-2 dark:border-zinc-800/60">
          <div className="mb-1 flex items-center gap-2 text-[10px] text-zinc-600 dark:text-zinc-400">
            <span>决策记录（近 {decisions.length} 条）</span>
            {openCount > 0 && <span>待结算 {openCount}</span>}
          </div>
          {decisions.length === 0 ? (
            <p className="py-2 text-[11px] text-zinc-600 dark:text-zinc-400">
              暂无记录。该股进入盘中跟踪台账后，收盘会统一扫描并记录当日信号。
            </p>
          ) : (
            <ul className="space-y-1">
              {decisions.map((d) => {
                const o = d.outcome ? OUTCOME[d.outcome] : null;
                const att = d.error_attribution;
                return (
                  <li
                    key={d.decision_id}
                    className="rounded border border-zinc-100 px-2 py-1 dark:border-zinc-800/60"
                  >
                    <div className="flex items-center gap-2">
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
                    <div className="mt-0.5 flex items-center gap-2 text-[10px] text-zinc-600 dark:text-zinc-400">
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
                    {att?.counter_evidence && (
                      <div className="mt-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">
                        {att.pivotal && att.primary_name ? `主因「${att.primary_name}」：` : ""}
                        {att.counter_evidence}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          {note && <p className="mt-1.5 text-[10px] leading-relaxed text-zinc-600 dark:text-zinc-400">{note}</p>}
        </div>
      </div>

      <div className="shrink-0 border-t border-zinc-100 px-3 py-1 text-[10px] text-zinc-600 dark:text-zinc-400 dark:border-zinc-800/60">
        偏向 + 依据 + 失效条件，不构成买卖建议
      </div>
    </div>
  );
}
