"use client";

import { StockLink } from "@/components/stock-link";
import { pctColor, pctText, winRateColor } from "@/lib/format";
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

/** 同版可信价格窗口的角色观察；不能证明实际成交或策略胜率。 */
export function RolePerformanceTable({ rows }: { rows: RolePerformance[] }) {
  return (
    <div className="ui-card rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
      <div className="mb-1 font-medium">
        梯队角色价格观察（同版窗口；不是成交胜率）
      </div>
      <table className="w-full">
        <thead>
          <tr className="text-zinc-600 dark:text-zinc-400">
            <th className="text-left font-normal">角色</th>
            <th className="text-right font-normal">样本</th>
            <th className="text-right font-normal">走好观察占比</th>
            <th className="text-right font-normal">平均超额</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.role} className="border-t border-zinc-100 dark:border-zinc-800/60">
              <td className="py-1">{r.role}</td>
              <td className="text-right font-mono tabular-nums">{r.count}</td>
              <td className={`text-right font-mono tabular-nums ${winRateColor(r.win_rate, "pct")}`}>
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
    <div className="ui-card rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
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
    <div className="ui-card rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
      <div className="mb-1 font-medium">
        精选复盘（按原记录日期与观察窗口）
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
            <span>{r.name ?? ""}</span><span className="text-zinc-600 dark:text-zinc-400">{r.date}</span>
            <span className={`font-mono tabular-nums ${pctColor(r.excess_pct)}`}>
              {r.excess_pct == null ? "收益观察未知" : `观察超额 ${pctText(r.excess_pct)}`}
            </span>
            <span className={tone}>{label}</span>
            <span className="w-full text-zinc-600 dark:text-zinc-400">{r.note}</span>
            <span className="w-full break-words text-zinc-600 dark:text-zinc-400" title={r.review_context?.selection_version ?? "历史版本未绑定"}>
              {r.statistics_eligible === true ? "同版可信价格观察" : r.review_context?.selection_version ? "未进入可信统计：窗口、源数据或生成版本不满足" : "历史口径未绑定，不进入可信统计"}
              {r.review_context?.window_start ? ` · ${r.review_context.window_start} → ${r.review_context.window_end ?? "终点未知"}` : ""}
            </span>
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
    <div className="ui-card rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
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
