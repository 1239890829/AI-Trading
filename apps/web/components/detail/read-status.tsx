"use client";

import {useState, type ReactNode} from "react";
import {dateTimeTextBJ, dateTimesInTextBJ} from "@/lib/format";
import {ModalShell} from "@/components/ui/modal-shell";
import {useExitPresence} from "@/hooks/use-exit-presence";

export interface DetailReadState {
  pending: boolean;
  loaded: boolean;
  error: string | null;
  readAt: string | null;
}

export function DetailReadStatus({label, state, onRetry, diagnostics, status, identity = label, active = true}: {
  label: string; state: DetailReadState; onRetry?: () => void; diagnostics?: ReactNode;
  status?: {label: string; warning?: boolean; quality?: string} | null;
  identity?: string; active?: boolean;
}) {
  const [openedFor, setOpenedFor] = useState<string | null>(null);
  const presence = useExitPresence(active && openedFor === identity ? identity : null);
  if (openedFor !== null && (!active || openedFor !== identity)) setOpenedFor(null);
  const showDiagnostics = !!(diagnostics || state.readAt || state.error);
  return <><div className="flex min-h-7 min-w-0 shrink-0 flex-wrap items-center gap-x-2 gap-y-1 px-3 py-1 text-xs [overflow-wrap:anywhere]">
    {state.error ? <p role="alert" className="min-w-0 text-amber-800 dark:text-amber-300">{label}读取失败{state.loaded ? " · 保留结果" : ""}</p>
      : status ? <p role="status" data-quality={status.quality} className={status.warning ? "text-amber-800 dark:text-amber-300" : "text-zinc-600 dark:text-zinc-400"}>{status.label}</p>
      : state.pending && <p role="status" className="text-zinc-600 dark:text-zinc-400">{label}{state.loaded ? "更新中…" : "读取中…"}</p>}
    {state.error && onRetry && <button type="button" data-action="secondary" disabled={state.pending} onClick={onRetry}>重新读取{label}</button>}
    {showDiagnostics && <button type="button" data-action="quiet" aria-label={`${label}诊断`} aria-haspopup="dialog" className="ml-auto" onClick={() => setOpenedFor(identity)}>诊断</button>}
  </div>
    {presence.value === identity && <ModalShell presentation="drawer" open={presence.active} onClose={() => setOpenedFor(null)} label={`${label}诊断`} size="md" zIndex={60} header={<h2 className="text-sm font-medium">{label} · 数据诊断</h2>} bodyTabIndex={0} bodyClassName="data-scroll overflow-y-auto px-4 py-3 text-xs leading-relaxed [overflow-wrap:anywhere]">
      {presence.active && <div className="space-y-3">
        {state.error && <p role="alert">{dateTimesInTextBJ(state.error)}。{state.loaded ? "保留上次结果，仅供参考；本次状态未确认。" : "尚不能确认是否有数据。"}</p>}
        {state.readAt && <p>最近成功读取 {dateTimeTextBJ(state.readAt, {seconds: true})}（非源时刻）</p>}
        {diagnostics}
      </div>}
    </ModalShell>}
  </>;
}
