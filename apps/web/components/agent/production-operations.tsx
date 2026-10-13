"use client";

import { useRef, useState } from "react";
import { useResource } from "@/hooks/use-polling-fetch";
import {ConditionList, ExplanationMap, FactTimeline, NarrativeText} from "@/components/ui/explanation-map";
import {dateTimeTextBJ} from "@/lib/format";
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
  return <div className="operations-workspace">
    <p className="panel-reader-hint">左右滑动查看其他阅读区</p>
    <div className="operations-layout" role="region" aria-label="运行状态与生产命令阅读区" tabIndex={0}>
    <section className="operations-status" aria-labelledby="watcher-heading">
      <h2 id="watcher-heading">节拍持久状态</h2>
      <p>自动调度取决于服务和各项开关。机器关闭或服务未启动时不会继续生产。</p>
      {watcher.error ? <div role="alert" className="status-feedback"><p>状态读取失败，不能据此判断调度正常。</p><button data-action="secondary" className="command-action" onClick={watcher.refresh}>重新读取</button></div> : watcher.pending ? <p role="status" className="status-feedback">正在读取持久状态…</p> : watcher.data ? <ExplanationMap label="节拍记录" identity="watcher-state" steps={[
        {id:"state",label:"运行与开关",icon:"check",content:<><ConditionList label="后端已记录状态" items={[
          `运行标记：${typeof watcher.data.active === "boolean" ? watcher.data.active ? "运行中" : "未运行" : "未记录"}`,
          `启用开关：${typeof watcher.data.enabled === "boolean" ? watcher.data.enabled ? "已启用" : "未启用" : "未记录"}`,
          `简报记录：${typeof watcher.data.brief_exists === "boolean" ? watcher.data.brief_exists ? "存在" : "不存在" : "未记录"}`,
          `节拍计数：${watcher.data.beat_count ?? "未记录"}`,
        ]}/>{watcher.data.note && <NarrativeText text={watcher.data.note} label="运行说明"/>}<FactTimeline label="运行时点" items={[{label:"开始时间",value:watcher.data.started_at ? dateTimeTextBJ(watcher.data.started_at) : "未记录"}]}/></>},
        {id:"trackers",label:"方向跟踪",icon:"response",content:watcher.data.trackers?.length ? <div className="space-y-3">{watcher.data.trackers.map((tracker,index)=><section key={`${index}:${tracker.direction}`}><h4 className="text-xs font-medium">{tracker.direction}</h4><ConditionList label="跟踪记录" items={[`采样 ${tracker.beats ?? "未记录"} 次；缺失 ${tracker.missing_beats ?? "未记录"} 次。`,`峰值涨幅：${tracker.peak_pct == null ? "未知" : `${tracker.peak_pct}%`}`,...(tracker.falsify_triggers ?? []).map(trigger=>`${trigger.key}：${trigger.detail}`)]}/><p className="text-xs text-[var(--ui-muted)]">确认标记：{String(tracker.confirmed)}；证伪标记：{String(tracker.falsified)}。标记不代表收益证明。</p></section>)}</div> : <p>没有已记录的方向跟踪；不能据此判断市场没有机会。</p>},
        {id:"raw",label:"原始 JSON",icon:"source",content:<pre className="data-scroll whitespace-pre-wrap [overflow-wrap:anywhere]" aria-label="节拍原始 JSON">{JSON.stringify(watcher.data,null,2)}</pre>},
      ]}/> : <p>状态未记录，不能判断调度是否正常。</p>}
    </section>
    <section className="operations-desk" aria-labelledby="production-heading">
      <header className="operations-intro">
        <h2 id="production-heading">后台生产与维护</h2>
        <p>查看生产结果，按需补做任务。每次执行前核对影响，完成后查看回执。</p>
      </header>
      <div className="operations-command-scroll">
      <p className="operations-note">命令沿用后端写权限、预算与安全边界。进入此区域不授予额外权限。</p>
        <div className="command-list">
          {COMMANDS.map((command) => <div key={command.label} className="command-row">
              <div className="command-copy"><strong>{command.label}</strong><p>{command.note}</p></div>
            <button data-action="command" aria-label={command.label} aria-busy={busy === command.label} title={busy !== null ? "已有命令等待回执，请先核对结果" : command.note} disabled={busy !== null} onClick={() => void execute(command)} className="command-action">{busy === command.label ? "等待回执…" : "执行任务"}<span aria-hidden="true" className="command-glyph"><svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor"><path d="m5 3 7 5-7 5z" /></svg></span></button>
          </div>)}
        </div>
        {receipt && <section className="command-receipt" aria-label="本次命令回执"><h3 className="text-xs font-medium">本次命令回执</h3><div role="status"><NarrativeText text={receipt} label="原始命令回执"/></div></section>}
      </div>
    </section>

    </div>
  </div>;
}
