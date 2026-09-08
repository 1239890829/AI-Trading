"use client";

import { useCallback, useEffect, useState } from "react";

import {
  applyAgentParamChange,
  createAgentParamChange,
  getAgentParamChanges,
  getAgentParams,
  rollbackAgentParamChange,
  type AgentParamChange,
  type AgentParamInfo,
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
  draft: { label: "待确认", cls: "bg-amber-500/10 text-amber-600 dark:text-amber-300" },
  applied: { label: "已生效", cls: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-300" },
  rolled_back: { label: "已回滚", cls: "bg-zinc-500/10 text-zinc-400" },
};

export function ParamsTab() {
  // 三态：undefined=尚未拉到（骨架）/ null=加载失败 / 有值=渲染
  const [params, setParams] = useState<AgentParamInfo[] | undefined | null>(undefined);
  const [changes, setChanges] = useState<AgentParamChange[] | undefined | null>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [draftText, setDraftText] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, c] = await Promise.all([getAgentParams(), getAgentParamChanges()]);
      setParams(p);
      setChanges(c);
      setError(null);
    } catch (e) {
      setParams(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

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

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      {error && (
        <p className="shrink-0 rounded-md bg-red-500/5 px-2 py-1.5 text-[11px] text-red-500 dark:text-red-300">{error}</p>
      )}

      {/* 参数清单 */}
      <section className="shrink-0 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-xs font-medium text-zinc-700 dark:text-zinc-200">参数白名单（生效值：运行时覆盖 &gt; 静态配置）</h3>
          <span className="text-[10px] text-zinc-400">改参数免重启 · 全程审计留痕</span>
        </div>
        {params === undefined ? (
          <div className="h-10 w-full animate-pulse rounded bg-zinc-100 dark:bg-zinc-800" />
        ) : params === null ? (
          <p className="text-[11px] text-zinc-400">参数加载失败。</p>
        ) : (
          <div className="space-y-2">
            {params.map((p) => (
              <div key={p.key} className="rounded-lg border border-zinc-100 p-2 dark:border-zinc-800">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-medium text-zinc-800 dark:text-zinc-100">{p.label}</span>
                  <span className="rounded bg-zinc-500/10 px-1 py-0.5 font-mono text-[10px] text-zinc-500">{p.risk}</span>
                </div>
                <p className="mt-0.5 text-[10px] text-zinc-400">{p.desc}</p>
                <p className="mt-1 break-all rounded bg-zinc-50 px-1.5 py-1 font-mono text-[10px] text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
                  {pretty(p.current) || "（默认）"}
                </p>
                {editingKey === p.key ? (
                  <div className="mt-1.5 space-y-1">
                    <textarea
                      value={draftText}
                      onChange={(e) => setDraftText(e.target.value)}
                      rows={3}
                      placeholder='新值 JSON，如 {"发酵": {"echelon": 0.04}}'
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
                        className="rounded-md px-2 py-0.5 text-[11px] text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
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

      {/* 变更单历史 */}
      <section className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
        <h3 className="mb-2 text-xs font-medium text-zinc-700 dark:text-zinc-200">变更单历史（应用/回滚均留审计）</h3>
        {changes === undefined ? (
          <div className="h-10 w-full animate-pulse rounded bg-zinc-100 dark:bg-zinc-800" />
        ) : changes === null ? (
          <p className="text-[11px] text-zinc-400">变更单加载失败。</p>
        ) : changes.length === 0 ? (
          <p className="text-[11px] text-zinc-400">暂无变更单。</p>
        ) : (
          <div className="space-y-1.5">
            {changes.map((c) => {
              const meta = STATUS_META[c.status] ?? STATUS_META.draft;
              return (
                <div key={c.id} className="rounded-lg border border-zinc-100 px-2 py-1.5 dark:border-zinc-800">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] text-zinc-600 dark:text-zinc-300">
                      #{c.id} {c.key} <span className="text-zinc-400">· 来源 {c.source_type}{c.source_id ? `/${c.source_id}` : ""} · {fmtTime(c.created_at)}</span>
                    </span>
                    <span className={`shrink-0 rounded px-1 py-0.5 text-[10px] ${meta.cls}`}>{meta.label}</span>
                  </div>
                  {c.evidence && Object.keys(c.evidence).length > 0 && (
                    <p className="mt-0.5 text-[10px] text-zinc-400">依据：{pretty(c.evidence)}</p>
                  )}
                  {c.status === "draft" && (
                    <div className="mt-1 flex gap-1.5">
                      <button
                        disabled={busy}
                        onClick={() => void act(c.id, applyAgentParamChange)}
                        className="rounded-md border border-emerald-500/40 px-2 py-0.5 text-[11px] text-emerald-600 hover:bg-emerald-500/10 disabled:opacity-50 dark:text-emerald-300"
                      >
                        确认生效
                      </button>
                    </div>
                  )}
                  {c.status === "applied" && (
                    <button
                      disabled={busy}
                      onClick={() => void act(c.id, rollbackAgentParamChange)}
                      className="mt-1 rounded-md border border-zinc-300 px-2 py-0.5 text-[11px] text-zinc-600 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    >
                      回滚
                    </button>
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
