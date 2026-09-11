"use client";

import { usePollingFetch } from "@/hooks/use-polling-fetch";
import { useCallback, useEffect, useState } from "react";

import {
  applyAgentParamChange,
  createAgentParamChange,
  getAgentParamChanges,
  getAgentParamSurvival,
  getAgentParams,
  getAgentRollbackReasons,
  rollbackAgentParamChange,
  type AgentParamChange,
  type AgentParamInfo,
  type AgentParamSurvival,
  type AgentRollbackReason,
} from "@/lib/api";

/**
 * AI 控制台 · 参数配置（方案 P1-B：闭环"复盘 → 改进项 → 参数变更单 → 生效 → 回滚"）。
 *
 * 纪律：所有生效动作必须人工点确认（建议态设计）；值域非法 422 直接显示；
 * 白名单之外不可改；每次应用/回滚都有审计留痕。
 */

function fmtTime(iso: string | null): string {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function pretty(v: unknown): string {
  if (v === null || v === undefined || v === "") return "--";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

const STATUS_META: Record<string, { label: string; cls: string }> = {
  draft: { label: "待确认", cls: "bg-amber-500/10 text-amber-800 dark:text-amber-300" },
  applied: { label: "已生效", cls: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" },
  rolled_back: { label: "已回滚", cls: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400" },
};

export function ParamsTab() {
  // 三态：undefined=尚未拉到（骨架）/ null=加载失败 / 有值=渲染
  const [params, setParams] = useState<AgentParamInfo[] | undefined | null>(undefined);
  const [changes, setChanges] = useState<AgentParamChange[] | undefined | null>(undefined);
  const [survival, setSurvival] = useState<AgentParamSurvival | undefined | null>(undefined);
  const [reasons, setReasons] = useState<AgentRollbackReason[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [draftText, setDraftText] = useState("");
  const [busy, setBusy] = useState(false);
  // 回滚归因（P1-15）：先选原因再回滚。只记"回滚了"不记"为什么"，
  // 存活率就只是个数字——归因是它的下钻维度，不是可选的装饰。
  const [rollbackFor, setRollbackFor] = useState<number | null>(null);
  const [reasonCode, setReasonCode] = useState("manual");
  const [reasonNote, setReasonNote] = useState("");

  const load = useCallback(async () => {
    try {
      const [p, c, s, rs] = await Promise.all([
        getAgentParams(),
        getAgentParamChanges(),
        getAgentParamSurvival(),
        getAgentRollbackReasons(),
      ]);
      setParams(p);
      setChanges(c);
      setSurvival(s);
      setReasons(rs);
      setError(null);
    } catch (e) {
      setParams(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  // 挂载即拉取（P1-27 收编 usePollingFetch）：setState 落在 promise 回调里，
  // 不再触发 react-hooks/set-state-in-effect。load 依赖为 []（无参数），语义不变。
  usePollingFetch(load, null);

  async function propose() {
    if (!editingKey) return;
    setBusy(true);
    setError(null);
    try {
      await createAgentParamChange(editingKey, draftText);
      setEditingKey(null);
      setDraftText("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e)); // 422：值域非法，显式展示
    } finally {
      setBusy(false);
    }
  }

  async function act(changeId: number, fn: (id: number) => Promise<AgentParamChange>) {
    setBusy(true);
    setError(null);
    try {
      await fn(changeId);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doRollback(id: number) {
    setBusy(true);
    setError(null);
    try {
      await rollbackAgentParamChange(id, { reason_code: reasonCode, note: reasonNote });
      setRollbackFor(null);
      setReasonNote("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    // 滚动归属：本页三段（概览 / 参数清单 / 变更历史）都是「按内容高展示」的信息区块，
    // 没有一个适合独占弹性区——实测三段全 shrink-0 时内容 717px > 容器 508px，
    // 而根 overflow-y 为 visible、FadeSwap 父为 hidden ⇒ 多出的 209px 被直接裁掉，
    // 表现为「向下滚不动、下半截看不见」（2026-09-10 用户报）。
    // 故按 FadeSwap 契约（各 tab 根 h-full min-h-0 + **自管滚动**）让根整体滚动，
    // 子区块一律 shrink-0 按内容高排布，不再互相挤压（变更历史原为 flex-1 被压到 26px）。
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto">
      {error && (
        <p className="shrink-0 rounded-md bg-red-500/5 px-2 py-1.5 text-[11px] text-red-700 dark:text-red-300">{error}</p>
      )}

      {/* 变更存活率（P1-15）：数字 + **样本是否够用**一起展示。
          只给一个"存活率 100%"会把"只有 1 条变更"读成"策略很稳"。 */}
      <section className="shrink-0 rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
          <span className="font-medium text-zinc-700 dark:text-zinc-200">变更存活率</span>
          {survival === undefined ? (
            <span className="text-zinc-600 dark:text-zinc-400">加载中…</span>
          ) : survival === null ? (
            <span className="text-zinc-600 dark:text-zinc-400">加载失败</span>
          ) : (
            <>
              <span className="text-zinc-600 dark:text-zinc-300">
                已裁决 <b>{survival.decided}</b> 条 · 回滚 <b>{survival.rolled_back}</b> 条 ·{" "}
                存活率{" "}
                <b>{survival.survival_rate === null ? "--" : `${Math.round(survival.survival_rate * 100)}%`}</b>
              </span>
              {survival.superseded > 0 && (
                <span className="text-zinc-600 dark:text-zinc-400" title="状态仍是「已生效」，但值已被后来的变更覆盖——只按 status 统计会把这类算成存活">
                  其中 {survival.superseded} 条已被后续变更覆盖
                </span>
              )}
              {survival.insufficient && (
                <span className="rounded bg-amber-500/10 px-1 py-0.5 text-[10px] text-amber-800 dark:text-amber-300">
                  样本不足，暂不可用于判断
                </span>
              )}
              {Object.keys(survival.rollback_reasons).length > 0 && (
                <span className="text-zinc-600 dark:text-zinc-400">
                  回滚归因：
                  {Object.entries(survival.rollback_reasons)
                    .map(([code, n]) => `${survival.reason_labels[code] ?? code}×${n}`)
                    .join(" · ")}
                </span>
              )}
            </>
          )}
        </div>
      </section>

      {/* 参数清单 */}
      <section className="shrink-0 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-xs font-medium text-zinc-700 dark:text-zinc-200">参数白名单（生效值：运行时覆盖 &gt; 静态配置）</h3>
          <span className="text-[10px] text-zinc-600 dark:text-zinc-400">改参数免重启 · 全程审计留痕</span>
        </div>
        {params === undefined ? (
          <div className="h-10 w-full animate-pulse rounded bg-zinc-100 dark:bg-zinc-800" />
        ) : params === null ? (
          <p className="text-[11px] text-zinc-600 dark:text-zinc-400">参数加载失败。</p>
        ) : (
          <div className="space-y-2">
            {params.map((p) => (
              <div key={p.key} className="rounded-lg border border-zinc-100 p-2 dark:border-zinc-800">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-medium text-zinc-800 dark:text-zinc-100">{p.label}</span>
                  <span className="flex shrink-0 items-center gap-1">
                    {p.range && (
                      <span className="rounded bg-zinc-500/10 px-1 py-0.5 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">
                        {p.range.min}–{p.range.max}
                      </span>
                    )}
                    <span className="rounded bg-zinc-500/10 px-1 py-0.5 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{p.risk}</span>
                  </span>
                </div>
                <p className="mt-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">{p.desc}</p>
                <p className="mt-1 break-all rounded bg-zinc-50 px-1.5 py-1 font-mono text-[10px] text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
                  {pretty(p.current) || "（默认）"}
                </p>
                {editingKey === p.key ? (
                  <div className="mt-1.5 space-y-1">
                    <textarea
                      value={draftText}
                      onChange={(e) => setDraftText(e.target.value)}
                      rows={p.kind === "json" ? 3 : 1}
                      placeholder={
                        p.kind === "json"
                          ? '新值 JSON，如 {"发酵": {"echelon": 0.04}}'
                          : `新值（数值${p.range ? `，${p.range.min}–${p.range.max}` : ""}）`
                      }
                      className="w-full rounded-md border border-zinc-200 bg-transparent px-2 py-1 font-mono text-[11px] outline-none dark:border-zinc-700"
                    />
                    <div className="flex gap-1.5">
                      <button
                        disabled={busy}
                        onClick={() => void propose()}
                        className="rounded-md bg-zinc-900 px-2 py-0.5 text-[11px] text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
                      >
                        提交变更单
                      </button>
                      <button
                        onClick={() => setEditingKey(null)}
                        className="rounded-md px-2 py-0.5 text-[11px] text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                      >
                        取消
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    onClick={() => {
                      setEditingKey(p.key);
                      setDraftText(p.current && p.current !== "--" ? String(p.current) : "");
                    }}
                    className="mt-1.5 rounded-md border border-zinc-300 px-2 py-0.5 text-[11px] text-zinc-700 hover:bg-zinc-100 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-800"
                  >
                    发起变更
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 变更单历史：整页已由根容器滚动，这里按内容高排布（原 flex-1 会在
          前两段撑高时被压到只剩边框高度——实测 26px vs 内容 154px）。 */}
      <section className="shrink-0 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
        <h3 className="mb-2 text-xs font-medium text-zinc-700 dark:text-zinc-200">变更单历史（应用/回滚均留审计）</h3>
        {changes === undefined ? (
          <div className="h-10 w-full animate-pulse rounded bg-zinc-100 dark:bg-zinc-800" />
        ) : changes === null ? (
          <p className="text-[11px] text-zinc-600 dark:text-zinc-400">变更单加载失败。</p>
        ) : changes.length === 0 ? (
          <p className="text-[11px] text-zinc-600 dark:text-zinc-400">暂无变更单。</p>
        ) : (
          <div className="space-y-1.5">
            {changes.map((c) => {
              const meta = STATUS_META[c.status] ?? STATUS_META.draft;
              return (
                <div key={c.id} className="rounded-lg border border-zinc-100 px-2 py-1.5 dark:border-zinc-800">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] text-zinc-600 dark:text-zinc-300">
                      #{c.id} {c.key} <span className="text-zinc-600 dark:text-zinc-400">· 来源 {c.source_type}{c.source_id ? `/${c.source_id}` : ""} · {fmtTime(c.created_at)}</span>
                    </span>
                    <span className={`shrink-0 rounded px-1 py-0.5 text-[10px] ${meta.cls}`}>{meta.label}</span>
                  </div>
                  {c.evidence && Object.keys(c.evidence).length > 0 && (
                    <p className="mt-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">依据：{pretty(c.evidence)}</p>
                  )}
                  {c.rollback_reason && (
                    <p className="mt-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">
                      回滚归因：{survival?.reason_labels?.[c.rollback_reason.code] ?? c.rollback_reason.code}
                      {c.rollback_reason.note ? `（${c.rollback_reason.note}）` : ""}
                    </p>
                  )}
                  {c.status === "draft" && (
                    <div className="mt-1 flex gap-1.5">
                      <button
                        disabled={busy}
                        onClick={() => void act(c.id, applyAgentParamChange)}
                        className="rounded-md border border-emerald-500/40 px-2 py-0.5 text-[11px] text-emerald-700 hover:bg-emerald-500/10 disabled:opacity-50 dark:text-emerald-300"
                      >
                        确认生效
                      </button>
                    </div>
                  )}
                  {c.status === "applied" && rollbackFor !== c.id && (
                    <button
                      disabled={busy}
                      onClick={() => {
                        setRollbackFor(c.id);
                        setReasonCode("manual");
                        setReasonNote("");
                      }}
                      className="mt-1 rounded-md border border-zinc-300 px-2 py-0.5 text-[11px] text-zinc-600 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    >
                      回滚
                    </button>
                  )}
                  {c.status === "applied" && rollbackFor === c.id && (
                    <div className="mt-1 space-y-1 rounded-md border border-amber-500/30 bg-amber-500/5 p-1.5">
                      <p className="text-[10px] text-amber-800 dark:text-amber-300">
                        回滚前先归因（用于存活率下钻：是实验测到劣化，还是当初判断有误）
                      </p>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <select
                          value={reasonCode}
                          onChange={(e) => setReasonCode(e.target.value)}
                          className="rounded border border-zinc-300 bg-transparent px-1.5 py-0.5 text-[11px] dark:border-zinc-600"
                        >
                          {reasons.map((r) => (
                            <option key={r.code} value={r.code}>
                              {r.label}
                            </option>
                          ))}
                        </select>
                        <input
                          value={reasonNote}
                          onChange={(e) => setReasonNote(e.target.value)}
                          placeholder="备注（可选）"
                          className="min-w-[140px] flex-1 rounded border border-zinc-300 bg-transparent px-1.5 py-0.5 text-[11px] dark:border-zinc-600"
                        />
                        <button
                          disabled={busy}
                          onClick={() => void doRollback(c.id)}
                          className="rounded-md border border-red-500/40 px-2 py-0.5 text-[11px] text-red-700 hover:bg-red-500/10 disabled:opacity-50 dark:text-red-300"
                        >
                          确认回滚
                        </button>
                        <button
                          onClick={() => setRollbackFor(null)}
                          className="rounded-md px-2 py-0.5 text-[11px] text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                        >
                          取消
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
