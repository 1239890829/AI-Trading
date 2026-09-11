"use client";

import { StockLink } from "@/components/stock-link";
import { pctColor, pctText } from "@/lib/format";
import type { PickReviewRow } from "@/lib/api";

/**
 * 猎场「每日精选」侧的复盘区块（2026-09-08 猎场批次③从 /picks/page.tsx 迁出）：
 * 梯队角色胜率 / 走坏原因分布 / 逐日复盘 / 历史组合。内容与原页一致。
 * 元结论是周末权重微调建议的输入，权重变更需人工确认（组合纪律）。
 */

export const REASON_LABELS: Record<string, string> = {
  event_expired: "事件失效",
  board_receding: "板块退潮",
  market_drag: "大盘拖累",
  data_issue: "数据源问题",
  news_gap: "消息卡顿",
  logic_failed: "入选逻辑失效",
  gone_well: "走势健康",
  entry_bad: "买点不对",
  sentiment_misread: "情绪误判",
  missed: "踏空未介入",
};

export type RolePerformance = { role: string; count: number; win_rate: number; avg_excess: number };

/** 梯队角色胜率表：回答「能不能按题材抓妖」的直接证据；样本随交易日积累。 */
export function RolePerformanceTable({ rows }: { rows: RolePerformance[] }) {
  return (
    <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
      <div className="mb-1 font-medium">
        梯队角色胜率（回答「能不能按题材抓妖」的直接证据；样本随交易日积累）
      </div>
      <table className="w-full">
        <thead>
          <tr className="text-zinc-600 dark:text-zinc-400">
            <th className="text-left font-normal">角色</th>
            <th className="text-right font-normal">样本</th>
            <th className="text-right font-normal">胜率</th>
            <th className="text-right font-normal">平均超额</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.role} className="border-t border-zinc-100 dark:border-zinc-800/60">
              <td className="py-1">{r.role}</td>
              <td className="text-right font-mono tabular-nums">{r.count}</td>
              <td className={`text-right font-mono tabular-nums ${r.win_rate >= 50 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down"}`}>
                {r.win_rate}%
              </td>
              <td className={`text-right font-mono tabular-nums ${pctColor(r.avg_excess)}`}>
                {pctText(r.avg_excess)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 复盘元结论：走坏原因分布 chips。 */
export function ReasonDistribution({ dist }: { dist: Record<string, number> }) {
  return (
    <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
      <div className="mb-1 font-medium">复盘元结论：走坏原因分布（周末权重微调建议的输入；权重变更需人工确认）</div>
      <div className="flex flex-wrap gap-2">
        {Object.entries(dist).map(([k, v]) => (
          <span key={k} className="rounded bg-zinc-100 px-1.5 py-0.5 dark:bg-zinc-800">
            {REASON_LABELS[k] ?? k}: {v}
          </span>
        ))}
      </div>
    </div>
  );
}

/** 当日复盘逐只归因：对在哪、错在哪。 */
export function DailyReviews({ reviews }: { reviews: PickReviewRow[] }) {
  return (
    <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
      <div className="mb-1 font-medium">
        当日复盘（逐只归因：对在哪、错在哪）
        <span className="ml-1.5 font-normal text-zinc-600 dark:text-zinc-400">
          走坏 {reviews.filter((r) => r.verdict === "bad").length} / 共 {reviews.length}
        </span>
      </div>
      {reviews.map((r) => {
        const label = REASON_LABELS[r.reason_category] ?? r.reason_category;
        const tone =
          r.verdict === "good"
            ? "text-up-ink dark:text-up"
            : r.verdict === "bad"
              ? "text-down-ink dark:text-down"
              : "text-zinc-600 dark:text-zinc-400";
        return (
          <div
            key={r.symbol + r.date}
            className="flex flex-wrap gap-2 border-b border-zinc-100 py-1 last:border-0 dark:border-zinc-800/60"
          >
            <StockLink symbol={r.symbol} className="font-mono text-zinc-600 dark:text-zinc-400">
              {r.symbol}
            </StockLink>
            <span>{r.name ?? ""}</span>
            <span className={`font-mono tabular-nums ${pctColor(r.excess_pct)}`}>
              超额 {pctText(r.excess_pct)}
            </span>
            <span className={tone}>{label}</span>
            <span className="w-full text-zinc-600 dark:text-zinc-400">{r.note}</span>
          </div>
        );
      })}
    </div>
  );
}

/** 历史组合（一致性可回溯）。 */
export function HistoryList({
  history,
}: {
  history: { date: string; symbols: (string | null)[]; score_avg: number }[];
}) {
  return (
    <div className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
      <div className="mb-1 font-medium">历史组合（一致性可回溯）</div>
      {history.map((h) => (
        <div key={h.date} className="flex gap-2 border-b border-zinc-100 py-1 last:border-0 dark:border-zinc-800/60">
          <span className="font-mono text-zinc-600 dark:text-zinc-400">{h.date}</span>
          <span>均分 {h.score_avg}</span>
          <span className="text-zinc-600 dark:text-zinc-400">{h.symbols.join(" · ")}</span>
        </div>
      ))}
    </div>
  );
}
