"use client";

import { useRef, useState } from "react";
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
  return <div className="operations-layout">
    <section className="operations-status" aria-labelledby="watcher-heading">
      <h2 id="watcher-heading">节拍持久状态</h2>
      <p>自动调度取决于服务和各项开关。机器关闭或服务未启动时不会继续生产。</p>
      {watcher.error ? <div role="alert" className="status-feedback"><p>状态读取失败，不能据此判断调度正常。</p><button className="command-action" onClick={watcher.refresh}>重新读取</button></div> : watcher.pending ? <p role="status" className="status-feedback">正在读取持久状态…</p> : <pre>{JSON.stringify(watcher.data, null, 2)}</pre>}
    </section>
    <section className="operations-desk" aria-labelledby="production-heading">
      <header className="operations-intro">
        <h2 id="production-heading">后台生产与维护</h2>
        <p>查看生产结果，按需补做任务。每次执行前核对影响，完成后查看回执。</p>
      </header>
      <p className="operations-note">命令沿用后端写权限、预算与安全边界。进入此区域不授予额外权限。</p>
        <div className="command-list">
          {COMMANDS.map((command) => <div key={command.label} className="command-row">
              <div className="command-copy"><strong>{command.label}</strong><p>{command.note}</p></div>
            <button aria-label={command.label} disabled={busy !== null} onClick={() => void execute(command)} className="command-action">{busy === command.label ? "等待回执…" : "执行任务"}<span aria-hidden="true" className="ml-2">↗</span></button>
          </div>)}
        </div>
        {receipt && <details open className="command-receipt"><summary>本次命令回执</summary><pre role="status">{receipt}</pre></details>}
    </section>

  </div>;
}
