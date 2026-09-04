"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { KlineChartPro } from "@/components/kline-chart-pro";
import { analyze } from "@/lib/technical-analysis";
import type { Kline } from "@/types/market";

/** 历史回放（Phase 6 收官）：K 线逐 bar 推进动画，技术评估随切片实时重算。
 * 回放模式不画持仓成本线——当前成本对历史时点是未来数据（防未来函数）。 */

const SPEEDS: [string, number][] = [["1x", 600], ["2x", 300], ["4x", 150]];

interface Props {
  bars: Kline[];
  fills?: { date: string; side: string; price: number; quantity: number }[];
  onExit: () => void;
}

export function ReplayChart({ bars, fills = [], onExit }: Props) {
  // 回放起点：默认从最近 90 根前开始（起点选择 60/90/120）
  const [startOffset, setStartOffset] = useState(90);
  const startIdx = Math.max(30, bars.length - startOffset);
  const [idx, setIdx] = useState(startIdx);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(300);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 起点档位/bars 数量变化 → 渲染期重置到新起点（adjust-state 模式；
  // prevResetKey 初始 null 保证挂载时同样执行一次）
  const [prevResetKey, setPrevResetKey] = useState<string | null>(null);
  const resetKey = `${startOffset}:${bars.length}`;
  if (resetKey !== prevResetKey) {
    setPrevResetKey(resetKey);
    setIdx(Math.max(30, bars.length - startOffset));
    setPlaying(false);
  }

  // 播放推进（到末尾自动停止）
  useEffect(() => {
    if (!playing) return;
    timerRef.current = setInterval(() => {
      setIdx((p) => {
        if (p >= bars.length - 1) {
          setPlaying(false);
          return p;
        }
        return p + 1;
      });
    }, speed);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [playing, speed, bars.length]);

  const visible = useMemo(() => bars.slice(0, idx + 1), [bars, idx]);
  const tech = useMemo(
    () =>
      analyze(
        visible.map((b) => ({
          ts: b.ts, open: b.open ?? 0, high: b.high ?? 0,
          low: b.low ?? 0, close: b.close ?? 0, volume: b.volume, change_pct: b.change_pct,
        })),
      ),
    [visible],
  );
  const cur = visible[visible.length - 1];
  const curDate = cur?.ts.slice(0, 10) ?? "--";

  const btn = "rounded border border-zinc-300 px-2 py-0.5 text-xs text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100 disabled:opacity-40";

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col gap-1.5">
      {/* 回放控制条 */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 rounded-lg border border-sky-500/40 bg-sky-500/5 px-3 py-1.5 text-xs">
        <span className="font-medium text-sky-400">回放中</span>
        <button onClick={() => setPlaying((p) => !p)} disabled={idx >= bars.length - 1} className={btn}>
          {playing ? "⏸ 暂停" : "▶ 播放"}
        </button>
        <button onClick={() => { setPlaying(false); setIdx((p) => Math.max(startIdx, p - 1)); }} disabled={idx <= startIdx} className={btn}>
          ◀ 单步
        </button>
        <button onClick={() => { setPlaying(false); setIdx(startIdx); }} className={btn}>↺ 重置</button>
        <span className="text-zinc-400">速度</span>
        {SPEEDS.map(([label, ms]) => (
          <button key={label} onClick={() => setSpeed(ms)} className={`rounded px-1.5 py-0.5 ${speed === ms ? "bg-sky-500/20 font-medium text-sky-400" : "text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100"}`}>
            {label}
          </button>
        ))}
        <span className="text-zinc-400">起点</span>
        <select
          value={startOffset}
          onChange={(e) => setStartOffset(Number(e.target.value))}
          className="rounded border border-zinc-300 bg-transparent px-1 py-0.5 dark:border-zinc-700"
        >
          <option value={120}>120 根前</option>
          <option value={90}>90 根前</option>
          <option value={60}>60 根前</option>
        </select>
        <span className="font-mono tabular-nums text-zinc-600 dark:text-zinc-300">
          {curDate} <span className="text-zinc-400 dark:text-zinc-500">（{idx - startIdx + 1}/{bars.length - startIdx}）</span>
        </span>
        {tech && (
          <span className={`rounded px-1.5 py-0.5 text-[11px] ${tech.bias === "bull" ? "bg-up/15 text-up" : tech.bias === "bear" ? "bg-down/15 text-down" : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-300"}`}>
            {tech.bias === "bull" ? "偏多" : tech.bias === "bear" ? "偏空" : "中性"}（{tech.bullCount}多/{tech.bearCount}空）
          </span>
        )}
        <span className="ml-auto text-[10px] text-zinc-500">技术评估随回放切片实时重算 · 不画当前成本线（防未来函数）</span>
        <button onClick={onExit} className="rounded border border-zinc-300 px-2 py-0.5 text-xs text-zinc-500 hover:text-zinc-900 dark:border-zinc-700 dark:hover:text-zinc-100">
          退出回放 ✕
        </button>
      </div>

      {/* 图表：传切片 bars——B/S 点由 KlineChartPro 内部按可见 bars 过滤，天然随回放出现。
          followLatest：回放逐 bar 前进时重聚焦最近 20 根（跟随进度；详情页不重置用户视口） */}
      <div className="min-h-0 flex-1">
        <KlineChartPro bars={visible} tradeMarks={fills} followLatest className="h-full" />
      </div>
    </div>
  );
}
