"use client";

import { useEffect, useState } from "react";

import { getJevOverview, type JevDecisionTrace, type JevOverview, type JevTraceAnswer } from "@/lib/api";

const STATE_LABEL: Record<string, string> = {
  ready: "可用",
  disabled: "已关闭",
  not_configured: "未配置",
};

const STATUS_CLASS: Record<string, string> = {
  ok: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  skipped: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  failed: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
};

function pct(value: number | undefined): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}

function answerText(answer: JevTraceAnswer): string {
  if (answer.choice) {
    const conf = typeof answer.confidence === "number" ? ` · conf ${pct(answer.confidence)}` : "";
    return `${answer.choice}${conf}`;
  }
  if (typeof answer.noul === "number") return `yes ${pct(answer.noul)}`;
  if (typeof answer.probability === "number") return `p ${pct(answer.probability)}`;
  if (typeof answer.score === "number") return `score ${answer.score.toFixed(3)}`;
  return answer.type ?? "结构化结果";
}

function TraceRow({ trace }: { trace: JevDecisionTrace }) {
  return (
    <li className="rounded-lg border border-zinc-200 px-3 py-2 dark:border-zinc-800">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11px] text-zinc-800 dark:text-zinc-200">{trace.purpose}</span>
          <span className={`rounded px-1.5 py-0.5 text-[10px] ${STATUS_CLASS[trace.status] ?? "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400"}`}>
            {trace.status}
          </span>
        </div>
        <span className="text-[10px] text-zinc-500">
          {trace.model ?? "—"} · {trace.latency_ms.toFixed(0)}ms
        </span>
      </div>

      {Object.keys(trace.answers).length > 0 ? (
        <div className="mt-1.5 flex flex-col gap-1">
          {Object.entries(trace.answers).map(([qid, answer]) => (
            <div key={qid} className="flex flex-wrap items-baseline gap-2 text-[11px]">
              <span className="font-mono text-zinc-500">{qid}</span>
              <span className="text-zinc-800 dark:text-zinc-200">{answerText(answer)}</span>
              {answer.probabilities && (
                <span className="text-[10px] text-zinc-500">
                  {Object.entries(answer.probabilities)
                    .map(([label, value]) => `${label} ${pct(value)}`)
                    .join(" · ")}
                </span>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-1 text-[10px] text-zinc-500">
          本次没有 JEV 结构化输出{trace.reason ? `：${trace.reason}` : ""}
        </p>
      )}
      <p className="mt-1 text-[10px] text-zinc-500">{new Date(trace.at_utc).toLocaleString()}</p>
    </li>
  );
}

export function JevTab() {
  const [data, setData] = useState<JevOverview | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const result = await getJevOverview(60);
        if (!alive) return;
        setData(result);
        setFailed(null);
      } catch (exc) {
        if (alive) setFailed(exc instanceof Error ? exc.message : "加载失败");
      }
    })();
    return () => {
      alive = false;
    };
  }, [nonce]);

  const status = data?.status;
  const usage = data?.historical_usage;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">JEV 运行观察</h2>
          <p className="mt-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">
            只展示真实 JEV 结构化判断与运行状态，不展示输入正文，也不伪造“思维链”。
          </p>
        </div>
        <button
          type="button"
          onClick={() => setNonce((n) => n + 1)}
          className="rounded border border-zinc-200 px-2 py-1 text-[10px] hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          刷新
        </button>
      </div>

      {failed && (
        <p className="rounded border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
          JEV 状态加载失败：{failed}
        </p>
      )}

      {!failed && !data && <p className="text-[11px] text-zinc-500">加载 JEV 状态…</p>}

      {data && (
        <>
          <section className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
              <p className="text-[10px] text-zinc-500">状态</p>
              <p className="mt-1 text-sm font-medium text-zinc-900 dark:text-zinc-100">
                {STATE_LABEL[status?.state ?? ""] ?? status?.state ?? "—"}
              </p>
              <p className="mt-0.5 font-mono text-[10px] text-zinc-500">{status?.model ?? "—"}</p>
            </div>
            <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
              <p className="text-[10px] text-zinc-500">历史调用</p>
              <p className="mt-1 text-sm font-medium text-zinc-900 dark:text-zinc-100">{usage?.calls ?? 0}</p>
              <p className="mt-0.5 text-[10px] text-zinc-500">
                ok {usage?.ok ?? 0} · failed {usage?.failed ?? 0} · skipped {usage?.skipped ?? 0}
              </p>
            </div>
            <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
              <p className="text-[10px] text-zinc-500">平均延迟</p>
              <p className="mt-1 text-sm font-medium text-zinc-900 dark:text-zinc-100">
                {(usage?.avg_latency_ms ?? 0).toFixed(0)} ms
              </p>
              <p className="mt-0.5 text-[10px] text-zinc-500">历史 metadata receipt</p>
            </div>
            <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
              <p className="text-[10px] text-zinc-500">本进程 Decision Trace</p>
              <p className="mt-1 text-sm font-medium text-zinc-900 dark:text-zinc-100">
                {data.recent_decisions.length}
              </p>
              <p className="mt-0.5 text-[10px] text-zinc-500">重启可丢失 · 不落正文</p>
            </div>
          </section>

          <section className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
            <h3 className="text-[11px] font-medium text-zinc-900 dark:text-zinc-100">当前模式</h3>
            <div className="mt-2 grid gap-1 sm:grid-cols-2 xl:grid-cols-4">
              {Object.entries(status?.modes ?? {}).map(([name, mode]) => (
                <div key={name} className="flex items-center justify-between gap-2 rounded bg-zinc-50 px-2 py-1 dark:bg-zinc-900">
                  <span className="font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{name}</span>
                  <span className="text-[10px] font-medium text-zinc-800 dark:text-zinc-200">{mode}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
            <h3 className="text-[11px] font-medium text-zinc-900 dark:text-zinc-100">JEV 不可用时怎么走</h3>
            <ul className="mt-2 grid gap-1 sm:grid-cols-2">
              {Object.entries(data.fallbacks).map(([name, rule]) => (
                <li key={name} className="rounded bg-zinc-50 px-2 py-1.5 dark:bg-zinc-900">
                  <span className="font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{name}</span>
                  <p className="mt-0.5 text-[10px] leading-relaxed text-zinc-700 dark:text-zinc-300">{rule}</p>
                </li>
              ))}
            </ul>
          </section>

          <section className="min-h-0">
            <div className="mb-2 flex items-baseline justify-between gap-2">
              <h3 className="text-[11px] font-medium text-zinc-900 dark:text-zinc-100">最近 JEV Decision Trace</h3>
              <span className="text-[10px] text-zinc-500">runtime only</span>
            </div>
            {data.recent_decisions.length === 0 ? (
              <p className="rounded-lg border border-dashed border-zinc-300 px-3 py-6 text-center text-[11px] text-zinc-500 dark:border-zinc-700">
                本进程还没有 JEV 调用。JEV 未参与时这里保持为空，不用占位结果冒充判断。
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {data.recent_decisions.map((trace, idx) => (
                  <TraceRow key={`${trace.at_utc}-${trace.purpose}-${idx}`} trace={trace} />
                ))}
              </ul>
            )}
          </section>

          <p className="pb-2 text-[10px] leading-relaxed text-zinc-500">
            {data.privacy}。Decision Trace 只回答“JEV 实际返回了什么”，不代表下游一定采纳，
            也不代表准确率、交易胜率或 JEV 已进入猎场生产排序。
          </p>
        </>
      )}
    </div>
  );
}
