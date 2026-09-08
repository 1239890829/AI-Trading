"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  cancelAgentTask,
  createAgentTask,
  getAgentTasks,
  getAgentTaskTypes,
  type AgentTask,
  type AgentTaskStatus,
  type AgentTaskType,
} from "@/lib/api";

/**
 * AI 控制台 · 任务中心（docs/ai-agent-console-plan.md P0）。
 *
 * 首批只暴露 L0 只读/生成类任务；每个任务可展开看**步骤轨迹**（可追溯三件套
 * 第一件：做过什么、依据什么、花了多久、是否经 LLM 增强）。
 *
 * 三态纪律：`types === undefined` 骨架 / `null` 显式空态 / 有值渲染；
 * 状态徽标只有终态与异常态上色，running 用中性色（避免每秒闪色）。
 */

const STATUS_META: Record<AgentTaskStatus, { label: string; cls: string }> = {
  queued: { label: "排队中", cls: "bg-zinc-500/10 text-zinc-500" },
  running: { label: "执行中", cls: "bg-sky-500/10 text-sky-600 dark:text-sky-300" },
  succeeded: { label: "成功", cls: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-300" },
  failed: { label: "失败", cls: "bg-red-500/10 text-red-600 dark:text-red-300" },
  canceled: { label: "已取消", cls: "bg-zinc-500/10 text-zinc-400" },
  needs_confirm: { label: "待确认", cls: "bg-amber-500/10 text-amber-600 dark:text-amber-300" },
};

function timeText(iso: string | null | undefined): string {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function TaskCenter() {
  // 三态：undefined=尚未拉到（骨架）/ null=拉过且失败（错误态）/ 有值=渲染
  const [types, setTypes] = useState<AgentTaskType[] | undefined | null>(undefined);
  const [tasks, setTasks] = useState<AgentTask[] | undefined | null>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState<string | null>(null);
  const [paramsText, setParamsText] = useState("");

  const load = useCallback(async () => {
    try {
      const [t, list] = await Promise.all([getAgentTaskTypes(), getAgentTasks()]);
      setTypes(t);
      setTasks(list);
      setError(null);
    } catch (e) {
      setTasks(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 有任务在跑时按 3s 轮询（无 running 则停），避免无谓请求
  const hasRunning = useMemo(
    () => (tasks ?? []).some((t) => t.status === "queued" || t.status === "running"),
    [tasks],
  );
  useEffect(() => {
    if (!hasRunning) return;
    const id = setInterval(() => void load(), 3000);
    return () => clearInterval(id);
  }, [hasRunning, load]);

  async function submit(type: string) {
    setCreating(type);
    setError(null);
    let params: Record<string, unknown> = {};
    if (paramsText.trim()) {
      try {
        params = JSON.parse(paramsText) as Record<string, unknown>;
      } catch {
        setError("参数必须是合法 JSON 对象（如 {\"trade_date\": \"20260908\"}）");
        setCreating(null);
        return;
      }
    }
    try {
      const t = await createAgentTask(type, params);
      setSelected(t.id);
      setParamsText("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(null);
    }
  }

  async function cancel(id: string) {
    try {
      await cancelAgentTask(id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const detail = (tasks ?? []).find((t) => t.id === selected) ?? null;

  return (
    <div className="flex h-full min-h-0 gap-3">
      {/* 左：任务列表 */}
      <div className="flex w-[300px] shrink-0 flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="text-xs text-zinc-500">任务列表</span>
          <button
            onClick={() => void load()}
            className="rounded px-1.5 py-0.5 text-[11px] text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            刷新
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
          {tasks === undefined ? (
            <div className="space-y-1.5">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-12 w-full animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-800" />
              ))}
            </div>
          ) : tasks === null ? (
            <p className="rounded-lg border border-dashed border-zinc-200 p-3 text-[11px] text-zinc-400 dark:border-zinc-700">
              任务列表加载失败（后端未启动或接口异常）。
            </p>
          ) : tasks.length === 0 ? (
            <p className="rounded-lg border border-dashed border-zinc-200 p-3 text-[11px] text-zinc-400 dark:border-zinc-700">
              暂无任务。选择右侧任务类型创建（首批为 L0 只读/生成类）。
            </p>
          ) : (
            tasks.map((t) => (
              <button
                key={t.id}
                onClick={() => setSelected(t.id)}
                className={`w-full rounded-lg border px-2.5 py-2 text-left transition-colors ${
                  selected === t.id
                    ? "border-zinc-400 bg-zinc-50 dark:border-zinc-500 dark:bg-zinc-800"
                    : "border-zinc-200 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-800/60"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-xs font-medium text-zinc-800 dark:text-zinc-100">
                    {types?.find((x) => x.type === t.type)?.label ?? t.type}
                  </span>
                  <span className={`shrink-0 rounded px-1 py-0.5 text-[10px] ${STATUS_META[t.status].cls}`}>
                    {STATUS_META[t.status].label}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-zinc-400">
                  <span className="font-mono">{t.risk_level}</span>
                  <span>·</span>
                  <span>{timeText(t.created_at)}</span>
                  {t.steps.length > 0 && <span>· {t.steps.length} 步</span>}
                </div>
              </button>
            ))
          )}
        </div>
      </div>

      {/* 右：新建 + 详情 */}
      <div className="flex min-h-0 flex-1 flex-col gap-3">
        {/* 新建任务 */}
        <section className="shrink-0 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs font-medium text-zinc-700 dark:text-zinc-200">新建任务</h3>
            <span className="text-[10px] text-zinc-400">首批仅 L0 只读/生成类；写类任务在参数配置模块（P1）开放</span>
          </div>
          {types === undefined ? (
            <div className="h-8 w-full animate-pulse rounded bg-zinc-100 dark:bg-zinc-800" />
          ) : types === null ? (
            <p className="text-[11px] text-zinc-400">任务类型加载失败，无法创建任务。</p>
          ) : (
            <div className="flex flex-wrap items-center gap-1.5">
              {types.map((t) => (
                <button
                  key={t.type}
                  disabled={creating !== null}
                  onClick={() => void submit(t.type)}
                  title={t.desc}
                  className="rounded-md border border-zinc-300 px-2 py-1 text-[11px] text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-800"
                >
                  {creating === t.type ? "提交中…" : t.label}
                  <span className="ml-1 font-mono text-[10px] text-zinc-400">{t.risk}</span>
                </button>
              ))}
            </div>
          )}
          <input
            value={paramsText}
            onChange={(e) => setParamsText(e.target.value)}
            placeholder='可选参数（JSON），如 {"trade_date": "20260908"}；留空用默认'
            className="mt-2 w-full rounded-md border border-zinc-200 bg-transparent px-2 py-1 font-mono text-[11px] text-zinc-700 outline-none placeholder:text-zinc-400 dark:border-zinc-700 dark:text-zinc-200"
          />
        </section>

        {/* 任务详情 */}
        <section className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          {error && (
            <p className="mb-2 rounded-md bg-red-500/5 px-2 py-1.5 text-[11px] text-red-500 dark:text-red-300">{error}</p>
          )}
          {detail === null ? (
            <p className="text-[11px] text-zinc-400">选择左侧任务查看执行轨迹；或先创建一个任务。</p>
          ) : (
            <>
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-zinc-900 dark:text-zinc-50">
                    {types?.find((x) => x.type === detail.type)?.label ?? detail.type}
                  </span>
                  <span className={`rounded px-1.5 py-0.5 text-[10px] ${STATUS_META[detail.status].cls}`}>
                    {STATUS_META[detail.status].label}
                  </span>
                </div>
                {(detail.status === "running" || detail.status === "queued") && (
                  <button
                    onClick={() => void cancel(detail.id)}
                    className="rounded-md border border-zinc-300 px-2 py-0.5 text-[11px] text-zinc-600 hover:bg-zinc-100 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  >
                    取消任务
                  </button>
                )}
              </div>

              <dl className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-zinc-500">
                <div className="flex gap-1">
                  <dt>任务 ID</dt>
                  <dd className="truncate font-mono text-zinc-700 dark:text-zinc-200">{detail.id.slice(0, 12)}</dd>
                </div>
                <div className="flex gap-1">
                  <dt>触发方</dt>
                  <dd className="text-zinc-700 dark:text-zinc-200">{detail.created_by}</dd>
                </div>
                <div className="flex gap-1">
                  <dt>开始</dt>
                  <dd>{timeText(detail.started_at)}</dd>
                </div>
                <div className="flex gap-1">
                  <dt>结束</dt>
                  <dd>{timeText(detail.finished_at)}</dd>
                </div>
              </dl>

              {detail.error && (
                <p className="mb-2 rounded-md bg-red-500/5 px-2 py-1.5 text-[11px] text-red-500 dark:text-red-300">
                  失败：{detail.error.message}（{detail.error.code}）
                </p>
              )}
              {detail.result_ref && (
                <p className="mb-2 rounded-md bg-emerald-500/5 px-2 py-1.5 text-[11px] text-emerald-600 dark:text-emerald-300">
                  产物：{detail.result_ref.kind} · {detail.result_ref.id}
                </p>
              )}

              <h4 className="mb-1.5 text-[11px] font-medium text-zinc-600 dark:text-zinc-300">执行轨迹</h4>
              {detail.steps.length === 0 ? (
                <p className="text-[11px] text-zinc-400">暂无步骤（任务刚创建，等待执行）。</p>
              ) : (
                <ol className="space-y-1.5">
                  {detail.steps.map((s) => (
                    <li key={s.index} className="rounded-lg border border-zinc-100 px-2 py-1.5 dark:border-zinc-800">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[11px] font-medium text-zinc-800 dark:text-zinc-100">
                          {s.index}. {s.name}
                        </span>
                        <span className="shrink-0 font-mono text-[10px] text-zinc-400">
                          {s.ok ? "✓" : "✗"} {s.duration_ms}ms
                        </span>
                      </div>
                      {s.input_summary && (
                        <p className="mt-0.5 text-[10px] text-zinc-400">输入：{s.input_summary}</p>
                      )}
                      {s.output_summary && (
                        <p className="text-[10px] text-zinc-500 dark:text-zinc-300">输出：{s.output_summary}</p>
                      )}
                      {s.llm && (
                        <p className="mt-0.5 text-[10px] text-zinc-400">
                          模型：{s.llm.model || "rules"}
                          {s.llm.enhanced ? " · LLM 增强" : ""}
                        </p>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
