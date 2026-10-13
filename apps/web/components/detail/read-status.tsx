"use client";

import {timeTextBJ} from "@/lib/format";

export interface DetailReadState {
  pending: boolean;
  loaded: boolean;
  error: string | null;
  readAt: string | null;
}

export function DetailReadStatus({label, state, onRetry}: {label: string; state: DetailReadState; onRetry?: () => void}) {
  return <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 px-3 py-1.5 text-xs [overflow-wrap:anywhere]">
    {state.error ? <p role="alert" className="min-w-0 text-amber-800 dark:text-amber-300">{label}读取失败。{state.loaded ? "保留上次结果，仅供参考；本次状态未确认。" : "尚不能确认是否有数据。"}</p>
      : state.pending && <p role="status" className="text-zinc-600 dark:text-zinc-400">{label}{state.loaded ? "校准中…" : "读取中…"}</p>}
    {state.readAt && <span className="text-zinc-600 dark:text-zinc-400">最近成功读取 {timeTextBJ(state.readAt)}（非源时刻）</span>}
    {state.error && onRetry && <button type="button" data-action="secondary" disabled={state.pending} onClick={onRetry}>重新读取{label}</button>}
  </div>;
}
