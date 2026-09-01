"use client";

import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/panel";
import {
  getReviewEffectiveness,
  getReviewReport,
  getReviewReports,
  type ReviewReportDetail,
  type ReviewReportSummary,
} from "@/lib/api";

/**
 * 复盘 tab（评审 A2，2026-09-01）：方法论闭环的报告与有效性数据此前只在
 * 后端 6 个端点产出，前端无查看入口（每日精选页的复盘区只展示 picks 归因，
 * 不覆盖这套"报告→改进项→采纳统计"闭环）。
 *
 * 口径说明：复盘报告由后端每日盘后生成（/review/run），记录当日操作评估、
 * 数据缺口与改进项；effectiveness 统计各类别改进项的采纳/回退率——
 * 采纳率是方法论自我校准的度量。
 */

const PRIORITY_CLS: Record<string, string> = {
  P0: "text-red-400",
  P1: "text-amber-400",
  P2: "text-zinc-400",
};

const CATEGORY_LABEL: Record<string, string> = {
  data: "数据",
  process: "流程",
  strategy: "策略",
  risk: "风控",
  other: "其他",
};

/** gaps 元素可能是字符串，也可能是结构化对象（{field,reason,impact,severity}）——统一成可读文本。 */
function gapText(g: unknown): string {
  if (typeof g === "string") return g;
  if (g && typeof g === "object") {
    const o = g as Record<string, unknown>;
    const parts = [
      o.field ? String(o.field) : null,
      o.reason ? String(o.reason) : null,
      o.impact ? `影响 ${o.impact}` : null,
    ].filter(Boolean);
    return parts.join("：") || JSON.stringify(o);
  }
  return String(g);
}

function ReportDetail({ report }: { report: ReviewReportDetail }) {
  return (
    <div className="space-y-3 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-400">
        <span className="font-mono">{report.review_id}</span>
        <span>方法论 {report.methodology_version}</span>
        <span>
          模型 {report.model.actual ?? "--"}
          {report.model.degraded && <span className="text-amber-400">（降级：{report.model.reason ?? "未知"}）</span>}
        </span>
      </div>

      {report.dimensions.map((d) => (
        <div key={d.key} className="rounded-lg border border-zinc-200 px-3 py-2 dark:border-zinc-800">
          <div className="text-xs font-medium text-zinc-200 dark:text-zinc-100">
            {d.title}
            <span className="ml-2 font-normal text-zinc-400">{d.status}</span>
          </div>
          {d.judgements.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-zinc-400">
              {d.judgements.map((j, i) => (
                <li key={i}>{j}</li>
              ))}
            </ul>
          )}
          {d.gaps.length > 0 && (
            <div className="mt-1 text-xs text-amber-400">
              缺口：
              <ul className="list-disc pl-4">
                {d.gaps.map((g, i) => (
                  <li key={i}>{gapText(g)}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ))}

      {report.action_items.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-medium text-zinc-200 dark:text-zinc-100">改进项</div>
          <div className="space-y-1.5">
            {report.action_items.map((a) => (
              <div key={a.id} className="rounded-lg border border-zinc-200 px-3 py-2 dark:border-zinc-800">
                <div className="flex items-baseline gap-2 text-xs">
                  <span className={`font-mono font-semibold ${PRIORITY_CLS[a.priority] ?? "text-zinc-400"}`}>{a.priority}</span>
                  <span className="text-zinc-200 dark:text-zinc-100">{a.title}</span>
                  <span className="text-zinc-400">[{CATEGORY_LABEL[a.category] ?? a.category}]</span>
                </div>
                <div className="mt-0.5 text-[11px] text-zinc-400">{a.expected_impact}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function ReviewTab() {
  const [reports, setReports] = useState<ReviewReportSummary[] | null>(null);
  const [effect, setEffect] = useState<Awaited<ReturnType<typeof getReviewEffectiveness>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openDate, setOpenDate] = useState<string | null>(null);
  const [detail, setDetail] = useState<ReviewReportDetail | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([getReviewReports(), getReviewEffectiveness().catch(() => null)])
      .then(async ([r, e]) => {
        if (!alive) return;
        setReports(r);
        setEffect(e);
        // 默认展开最新一份报告并立即拉详情（否则一直停在"加载中"直到手点）
        if (r.length > 0) {
          setOpenDate(r[0].trade_date);
          setDetail(await getReviewReport(r[0].trade_date).catch(() => null));
        }
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, []);

  const toggle = useCallback(
    (date: string) => {
      const next = openDate === date ? null : date;
      setOpenDate(next);
      setDetail(null);
      if (next) {
        getReviewReport(next)
          .then((d) => setDetail(d))
          .catch(() => setDetail(null));
      }
    },
    [openDate],
  );

  if (error) {
    return (
      <Panel title="复盘报告">
        <p className="px-4 py-8 text-center text-sm text-amber-500">复盘报告加载失败：{error}</p>
      </Panel>
    );
  }
  if (reports === null) {
    return (
      <Panel title="复盘报告">
        <p className="px-4 py-8 text-center text-sm text-zinc-400">加载中…</p>
      </Panel>
    );
  }

  return (
    <div className="grid min-h-0 flex-1 grid-rows-[auto_1fr] gap-3 overflow-auto lg:grid-cols-2 lg:grid-rows-1">
      <Panel title="复盘报告（盘后自动生成）" className="min-h-[240px]">
        {reports.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-zinc-400">
            暂无复盘报告。后端每日盘后自动生成（POST /api/review/run）。
          </p>
        ) : (
          <div className="divide-y divide-zinc-100 dark:divide-zinc-800/60">
            {reports.map((r) => (
              <div key={r.review_id}>
                <button
                  onClick={() => toggle(r.trade_date)}
                  className="w-full px-4 py-2.5 text-left transition-colors hover:bg-zinc-100/60 dark:hover:bg-zinc-800/40"
                >
                  <div className="flex items-baseline gap-2 text-sm">
                    <span className="font-mono text-xs text-zinc-400">{r.trade_date}</span>
                    <span className="text-zinc-200 dark:text-zinc-100">{r.summary}</span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-zinc-400">
                    缺口 {r.gap_count} · 行动项 {r.action_item_count}
                    {r.model_degraded && <span className="ml-2 text-amber-400">模型降级</span>}
                  </div>
                </button>
                {openDate === r.trade_date && (detail ? <ReportDetail report={detail} /> : <p className="px-4 py-3 text-xs text-zinc-400">详情加载中…</p>)}
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="改进项有效性（方法论自校准）" className="min-h-[240px]">
        {effect === null ? (
          <p className="px-4 py-8 text-center text-sm text-zinc-400">有效性统计加载失败或暂无数据</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-200 text-xs text-zinc-400 dark:border-zinc-800">
                <th className="px-4 py-2 text-left font-normal">类别</th>
                <th className="px-2 py-2 text-right font-normal">总数</th>
                <th className="px-2 py-2 text-right font-normal">已采纳</th>
                <th className="px-2 py-2 text-right font-normal">回退</th>
                <th className="px-2 py-2 text-right font-normal">驳回</th>
                <th className="px-4 py-2 text-right font-normal">采纳率</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(effect.by_category).map(([cat, v]) => (
                <tr key={cat} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                  <td className="px-4 py-2">{CATEGORY_LABEL[cat] ?? cat}</td>
                  <td className="px-2 py-2 text-right font-mono">{v.total}</td>
                  <td className="px-2 py-2 text-right font-mono text-up">{v.confirmed}</td>
                  <td className="px-2 py-2 text-right font-mono text-down">{v.reverted}</td>
                  <td className="px-2 py-2 text-right font-mono text-zinc-400">{v.rejected}</td>
                  <td className="px-4 py-2 text-right font-mono">{v.adoption_rate != null ? `${Math.round(v.adoption_rate * 100)}%` : "--"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
