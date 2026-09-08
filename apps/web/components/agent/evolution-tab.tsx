"use client";

import { useCallback, useEffect, useState } from "react";

import {
  getAgentAgenda,
  getAgentAgendas,
  getAgentExperiments,
  runAgentAgenda,
  type AgentAgenda,
  type AgentExperiment,
} from "@/lib/api";

/**
 * AI 控制台 · 进化 tab（AI 大脑 v2，docs/evolution-brain-plan.md）。
 *
 * 展示今日进化议程：LLM 的发现/依据/动作/执行状态与结果——
 * **没有"待确认"态**（后置守护模型：议程项要么已执行、要么写明为什么没执行）。
 * 手动「立即进化」按钮是 LLM 不可用时的降级兜底（正常由 15:45 自动跑）。
 */

const CLASS_META: Record<string, { label: string; cls: string }> = {
  A: { label: "参数", cls: "bg-sky-500/10 text-sky-600 dark:text-sky-300" },
  B: { label: "文档", cls: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-300" },
  C: { label: "代码", cls: "bg-violet-500/10 text-violet-600 dark:text-violet-300" },
};

const STATUS_META: Record<string, { label: string; cls: string }> = {
  executed: { label: "已执行", cls: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-300" },
  deferred: { label: "暂缓", cls: "bg-amber-500/10 text-amber-600 dark:text-amber-300" },
  rejected: { label: "已拒绝", cls: "bg-red-500/10 text-red-600 dark:text-red-300" },
  failed: { label: "失败", cls: "bg-red-500/10 text-red-600 dark:text-red-300" },
  pending: { label: "未执行", cls: "bg-zinc-500/10 text-zinc-500" },
};

const AGENDA_STATUS: Record<string, string> = {
  generating: "生成中", ready: "已生成待执行", executed: "已执行",
  failed: "失败", skipped: "已跳过",
};

function timeText(iso: string | null): string {
  if (!iso) return "--";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "--" : d.toLocaleTimeString("zh-CN", { hour12: false });
}

export function EvolutionTab() {
  const [agenda, setAgenda] = useState<AgentAgenda | null | undefined>(undefined);
  const [history, setHistory] = useState<AgentAgenda[] | undefined>(undefined);
  const [experiments, setExperiments] = useState<AgentExperiment[] | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [a, list, exps] = await Promise.all([
        getAgentAgenda(), getAgentAgendas(), getAgentExperiments(),
      ]);
      setAgenda(a);
      setHistory(list);
      setExperiments(exps);
      setError(null);
    } catch (e) {
      setAgenda(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function runNow() {
    setBusy(true);
    setError(null);
    try {
      await runAgentAgenda();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const autonomyNote =
    agenda?.status === "skipped"
      ? agenda.error?.message ?? "自主执行未运行"
      : null;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <section className="shrink-0 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-xs font-medium text-zinc-700 dark:text-zinc-200">
              今日进化议程 {agenda ? `· ${agenda.date}` : ""}
            </h3>
            <p className="mt-0.5 text-[10px] text-zinc-400">
              LLM 汇总复盘改进项 / 信号健康 / 告警判读 → 自动执行可落地的改进；手动按钮仅作降级兜底
            </p>
          </div>
          <div className="flex items-center gap-2">
            {agenda && (
              <span className={`rounded px-1.5 py-0.5 text-[10px] ${
                agenda.status === "executed"
                  ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-300"
                  : agenda.status === "skipped"
                    ? "bg-amber-500/10 text-amber-600 dark:text-amber-300"
                    : "bg-zinc-500/10 text-zinc-500"
              }`}>
                {AGENDA_STATUS[agenda.status] ?? agenda.status}
              </span>
            )}
            <button
              disabled={busy}
              onClick={() => void runNow()}
              className="rounded-md border border-zinc-300 px-2 py-0.5 text-[11px] text-zinc-700 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              {busy ? "运行中…" : "立即进化（降级兜底）"}
            </button>
          </div>
        </div>
        {autonomyNote && (
          <p className="mt-1.5 rounded-md bg-amber-500/5 px-2 py-1 text-[11px] text-amber-600 dark:text-amber-300">
            {autonomyNote}
          </p>
        )}
        {error && (
          <p className="mt-1.5 rounded-md bg-red-500/5 px-2 py-1 text-[11px] text-red-500 dark:text-red-300">{error}</p>
        )}
      </section>

      {/* 议程项 */}
      <section className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
        {agenda === undefined ? (
          <div className="space-y-2">
            {[0, 1].map((i) => (
              <div key={i} className="h-16 w-full animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-800" />
            ))}
          </div>
        ) : agenda === null ? (
          <p className="text-[11px] text-zinc-400">
            今日议程尚未生成（每交易日 15:45 自动跑，或点上方按钮手动触发）。
          </p>
        ) : (agenda.items ?? []).length === 0 ? (
          <p className="text-[11px] text-zinc-400">
            今日议程为空——LLM 判断今日无值得立即执行的改进（宁缺毋滥）。
          </p>
        ) : (
          <div className="space-y-2">
            {(agenda.items ?? []).map((it, idx) => {
              const cm = CLASS_META[it.class] ?? { label: it.class, cls: "bg-zinc-500/10 text-zinc-500" };
              const sm = STATUS_META[it.status] ?? STATUS_META.pending;
              return (
                <div key={idx} className="rounded-lg border border-zinc-100 p-2.5 dark:border-zinc-800">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${cm.cls}`}>
                      {cm.label}类
                    </span>
                    <span className="text-[11px] font-medium text-zinc-800 dark:text-zinc-100">
                      {it.finding}
                    </span>
                    <span className={`ml-auto rounded px-1.5 py-0.5 text-[10px] ${sm.cls}`}>{sm.label}</span>
                  </div>
                  {it.evidence && Object.keys(it.evidence).length > 0 && (
                    <p className="mt-1 text-[10px] text-zinc-400">
                      依据：{JSON.stringify(it.evidence)}
                    </p>
                  )}
                  {it.action && (
                    <p className="mt-0.5 text-[11px] text-zinc-600 dark:text-zinc-300">动作：{it.action}</p>
                  )}
                  {it.expected_effect && (
                    <p className="mt-0.5 text-[10px] text-zinc-400">预期：{it.expected_effect} · 验证：{it.verification || "—"}</p>
                  )}
                  {it.result && (
                    <p className={`mt-1 rounded px-1.5 py-1 text-[10px] ${
                      it.status === "executed"
                        ? "bg-emerald-500/5 text-emerald-600 dark:text-emerald-300"
                        : it.status === "failed" || it.status === "rejected"
                          ? "bg-red-500/5 text-red-500 dark:text-red-300"
                          : "bg-amber-500/5 text-amber-600 dark:text-amber-300"
                    }`}>
                      {it.result}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* 实验记录本：A 类自动变更的后置验证（劣化自动回滚） */}
      <section className="shrink-0 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
        <h3 className="mb-1.5 text-xs font-medium text-zinc-700 dark:text-zinc-200">
          实验记录本 <span className="text-[10px] text-zinc-400">· 30 日后置验证，劣化自动回滚</span>
        </h3>
        {experiments === undefined ? (
          <div className="h-6 w-full animate-pulse rounded bg-zinc-100 dark:bg-zinc-800" />
        ) : experiments.length === 0 ? (
          <p className="text-[11px] text-zinc-400">暂无进行中的实验（A 类参数自动生效时会自动挂账）。</p>
        ) : (
          <div className="space-y-1">
            {experiments.slice(0, 5).map((e) => {
              const meta =
                e.status === "rolled_back"
                  ? { label: "已自动回滚", cls: "bg-red-500/10 text-red-600 dark:text-red-300" }
                  : e.status === "concluded"
                    ? { label: "验证通过", cls: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-300" }
                    : e.status === "concluded_insufficient"
                      ? { label: "样本不足", cls: "bg-amber-500/10 text-amber-600 dark:text-amber-300" }
                      : { label: `验证中（${e.verification_date?.slice(5, 10) ?? "--"} 到期）`, cls: "bg-sky-500/10 text-sky-600 dark:text-sky-300" };
              return (
                <div key={e.id} className="flex items-center justify-between gap-2 text-[11px]">
                  <span className="truncate text-zinc-600 dark:text-zinc-300">
                    #{e.change_id} {e.param_key} · {e.hypothesis.slice(0, 40) || "—"}
                  </span>
                  <span className={`shrink-0 rounded px-1 py-0.5 text-[10px] ${meta.cls}`}>{meta.label}</span>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* 历史 */}
      <section className="shrink-0 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
        <h3 className="mb-1.5 text-xs font-medium text-zinc-700 dark:text-zinc-200">历史议程</h3>
        {history === undefined ? (
          <div className="h-6 w-full animate-pulse rounded bg-zinc-100 dark:bg-zinc-800" />
        ) : history.length === 0 ? (
          <p className="text-[11px] text-zinc-400">暂无历史。</p>
        ) : (
          <div className="space-y-0.5">
            {history.slice(0, 7).map((a) => (
              <div key={a.id} className="flex items-center justify-between text-[11px] text-zinc-500">
                <span>{a.date}</span>
                <span>{AGENDA_STATUS[a.status] ?? a.status} · {(a.items ?? []).length} 项 · {timeText(a.finished_at)}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
