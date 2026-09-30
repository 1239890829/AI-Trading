"use client";

import { useRef, useState } from "react";
import { Panel } from "@/components/panel";
import { useResource } from "@/hooks/use-polling-fetch";
import {
  generatePicks, generatePickReview, generateMorningBrief, runWatcherBeat, runIntradayReview,
  getWatcherState,
} from "@/lib/api";

const COMMANDS = [
  { label: "重新生成组合", run: generatePicks, note: "重算并覆盖当前组合。" },
  { label: "生成精选复盘", run: generatePickReview, note: "对已有组合生成归因记录。" },
  { label: "重新生成简报", run: generateMorningBrief, note: "覆盖今日简报，并清空其原提醒列表，可能调用模型。" },
  { label: "执行一次节拍", run: runWatcherBeat, note: "可能产生真实通知，沿原通知授权与去重规则执行。" },
  { label: "运行盘后对照", run: runIntradayReview, note: "回填当日简报对照与提醒结果。" },
] as const;

/** Existing backend commands, now separated from result reading. No effect invokes a write. */
export function ProductionOperations() {
  const watcher = useResource(getWatcherState, { intervalMs: 30_000, marketHours: false });
  const lock = useRef(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);
  async function execute(command: typeof COMMANDS[number]) {
    if (lock.current || !window.confirm(`${command.label}：${command.note} 确认执行？`)) return;
    lock.current = true;
    setBusy(command.label);
    setReceipt(null);
    try {
      const result = await command.run();
      setReceipt(`${command.label}已返回。原始回执：${JSON.stringify(result)}`);
      watcher.refresh();
    } catch (error) {
      setReceipt(`${command.label}未确认完成：${error instanceof Error ? error.message : "读取失败"}。先查询持久结果，避免对未知结果直接重复执行。`);
    } finally {
      lock.current = false;
      setBusy(null);
    }
  }
  return <div className="task-scroll min-h-0 flex-1 space-y-4 overflow-auto">
    <Panel title="后台生产与维护兜底">
      <div className="space-y-3 p-4 text-sm">
        <p>普通页面只读取结果；这些命令沿用后端写权限、预算与安全边界。进入此区域不授予额外权限。</p>
        <p className="text-zinc-600 dark:text-zinc-400">自动调度是否运行取决于服务和各项开关。机器关闭或服务未启动时不会继续生产。</p>
        <div className="divide-y divide-zinc-200 dark:divide-zinc-800">
          {COMMANDS.map(command => <div key={command.label} className="flex flex-wrap items-center justify-between gap-3 py-3">
            <p>{command.note}</p><button disabled={busy !== null} onClick={() => void execute(command)} className="min-h-11 rounded-md border border-zinc-300 px-3 disabled:opacity-50 dark:border-zinc-600">{busy === command.label ? "等待后端回执…" : command.label}</button>
          </div>)}
        </div>
        {receipt && <details open><summary>本次命令回执</summary><pre role="status" className="max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">{receipt}</pre></details>}
      </div>
    </Panel>
    <Panel title="节拍持久状态">
      <div className="p-4 text-xs">
        {watcher.error ? <p role="alert">状态读取失败，不能据此判断调度正常。<button onClick={watcher.refresh}>重试</button></p> : watcher.pending ? <p role="status">读取中…</p> : <pre className="whitespace-pre-wrap break-all">{JSON.stringify(watcher.data, null, 2)}</pre>}
      </div>
    </Panel>
  </div>;
}
